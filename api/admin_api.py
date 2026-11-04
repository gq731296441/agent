"""Skill 管理员 API：审批队列管理、管理员 CRUD、隔离。"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from common.schemas import ApiResponse, ListApiResponse, PaginatedApiResponse
from db import deep_storage
from deep_agent_server.skills.admin_service import (
    AdminServiceError,
    approve_publish,
    assert_admin,
    list_pending_reviews,
    reject_action,
)
from deep_agent_server.skills.marketplace.publish_service import PublishError, get_publish_service
from deep_agent_server.skills.marketplace.review import approve_version, reject_version
from deep_agent_server.skills.marketplace import storage as ms

router = APIRouter(prefix="/admin", tags=["admin"])


class ReviewAction(BaseModel):
    action: str = Field(..., pattern="^(approve|reject)$")
    reviewer_user_id: str = Field(..., description="管理员 user_id")
    comment: str = ""


class AddAdminBody(BaseModel):
    user_id: str
    role: str = "admin"


class QuarantineBody(BaseModel):
    reviewer_user_id: str
    comment: str = ""


# ── 管理员身份 ──


@router.get("/check")
async def check_admin(user_id: str = Query(...)):
    is_admin = await deep_storage.is_admin(user_id)
    return ApiResponse.success(data={"is_admin": is_admin})


# ── 新版：按 version_id 人审 ──


@router.get("/skill-reviews", response_model=PaginatedApiResponse)
async def list_skill_reviews(
    user_id: str = Query(..., description="管理员 user_id"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    try:
        await assert_admin(user_id)
    except AdminServiceError as e:
        raise HTTPException(status_code=e.code, detail=str(e)) from e

    items, total = await ms.list_pending_version_reviews(page=page, page_size=page_size)
    for it in items:
        sid = it.get("submission_id")
        if sid:
            it["findings"] = await ms.list_findings(str(sid))
    return PaginatedApiResponse.success(items, total, page, page_size)


@router.post("/skill-versions/{version_id}/approve", response_model=ApiResponse)
async def approve_skill_version(version_id: str, body: ReviewAction):
    try:
        result = await approve_version(version_id, body.reviewer_user_id, body.comment)
        return ApiResponse.success(data=result, msg="审批通过并已发布")
    except (AdminServiceError, PublishError) as e:
        code = getattr(e, "code", 400)
        raise HTTPException(status_code=code, detail=str(e)) from e


@router.post("/skill-versions/{version_id}/reject", response_model=ApiResponse)
async def reject_skill_version(version_id: str, body: ReviewAction):
    try:
        result = await reject_version(version_id, body.reviewer_user_id, body.comment)
        return ApiResponse.success(data=result, msg="已拒绝")
    except (AdminServiceError, PublishError) as e:
        code = getattr(e, "code", 400)
        raise HTTPException(status_code=code, detail=str(e)) from e


@router.post("/skill-versions/{version_id}/quarantine", response_model=ApiResponse)
async def quarantine_skill_version(version_id: str, body: QuarantineBody):
    try:
        result = await get_publish_service().quarantine(version_id, body.reviewer_user_id)
        return ApiResponse.success(data=result, msg="已隔离")
    except (AdminServiceError, PublishError) as e:
        code = getattr(e, "code", 400)
        raise HTTPException(status_code=code, detail=str(e)) from e


# ── 旧审批队列（兼容 pending_skill_actions）──


@router.get("/pending-actions", response_model=PaginatedApiResponse)
async def list_pending(
    user_id: str = Query(..., description="管理员 user_id"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str = Query("pending", description="pending|approved|rejected"),
):
    try:
        await assert_admin(user_id)
    except AdminServiceError as e:
        raise HTTPException(status_code=e.code, detail=str(e)) from e

    items, total = await list_pending_reviews(page=page, page_size=page_size, status=status)
    return PaginatedApiResponse.success(items, total, page, page_size)


@router.post("/approve/{action_id}", response_model=ApiResponse)
async def approve(action_id: str, body: ReviewAction):
    try:
        result = await approve_publish(action_id, body.reviewer_user_id)
        return ApiResponse.success(data=result, msg="审批通过")
    except AdminServiceError as e:
        raise HTTPException(status_code=e.code, detail=str(e)) from e


@router.post("/reject/{action_id}", response_model=ApiResponse)
async def reject(action_id: str, body: ReviewAction):
    try:
        result = await reject_action(action_id, body.reviewer_user_id, body.comment)
        return ApiResponse.success(data=result, msg="已拒绝")
    except AdminServiceError as e:
        raise HTTPException(status_code=e.code, detail=str(e)) from e


# ── 管理员管理 ──


@router.get("/admins", response_model=ListApiResponse)
async def list_admins(user_id: str = Query(...)):
    try:
        await assert_admin(user_id)
    except AdminServiceError as e:
        raise HTTPException(status_code=e.code, detail=str(e)) from e

    admins = await deep_storage.list_admins()
    return ListApiResponse.success(data=admins)


@router.post("/admins", response_model=ApiResponse)
async def add_admin(body: AddAdminBody, admin_user_id: str = Query(..., description="操作者")):
    try:
        await assert_admin(admin_user_id)
    except AdminServiceError as e:
        raise HTTPException(status_code=e.code, detail=str(e)) from e

    await deep_storage.add_admin(body.user_id, created_by=admin_user_id, role=body.role)
    return ApiResponse.success(msg="管理员已添加")


@router.delete("/admins/{target_user_id}", response_model=ApiResponse)
async def remove_admin(target_user_id: str, admin_user_id: str = Query(...)):
    try:
        await assert_admin(admin_user_id)
    except AdminServiceError as e:
        raise HTTPException(status_code=e.code, detail=str(e)) from e

    ok = await deep_storage.remove_admin(target_user_id)
    if not ok:
        return ApiResponse.error(msg="管理员不存在", code=404)
    return ApiResponse.success(msg="管理员已移除")
