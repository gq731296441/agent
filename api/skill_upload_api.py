"""Skill 上传/管理 API：用户上传、更新、删除、导出 skill。"""

from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response

from common.schemas import ApiResponse, ListApiResponse
from db import deep_storage
from deep_agent_server.skills.admin_service import submit_skill_for_review
from deep_agent_server.skills.vm_repo import (
    export_skill_as_zip,
    upload_skill_zip,
)

router = APIRouter(prefix="/skills", tags=["skills"])


# ── 我创建的 Skill ──


@router.get("/my-skills", response_model=ListApiResponse)
async def list_my_skills(user_id: str = Query(...)):
    rows = await deep_storage.list_skills_by_creator(user_id)
    return ListApiResponse.success(data=rows)


# ── 上传新 Skill ──


@router.post("/upload", response_model=ApiResponse)
async def upload_skill(
    user_id: str = Query(...),
    file: UploadFile = File(...),
    name: str = Form(...),
    description: str = Form(""),
    version: str = Form("1.0.0"),
    tags: str = Form(""),
    author: str = Form(""),
):
    """上传 Skill ZIP 包。创建后状态为 draft，需管理员审核发布。"""
    if not file.filename or not file.filename.lower().endswith(".zip"):
        return ApiResponse.error(msg="仅支持 ZIP 文件", code=400)

    try:
        zip_data = await file.read()
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

        result = await upload_skill_zip(
            user_id=user_id,
            zip_data=zip_data,
            name=name,
            description=description,
            version=version,
            tags=tag_list,
            author=author or user_id,
        )

        # 自动提交发布审批
        try:
            await submit_skill_for_review(
                skill_id=result["skill_id"],
                action_type="publish",
                requested_by=user_id,
                new_version=version,
            )
        except Exception:
            pass  # 提交审批失败不影响上传

        return ApiResponse.success(data=result, msg="上传成功，已提交审核")
    except ValueError as e:
        return ApiResponse.error(msg=str(e), code=400)
    except Exception as e:
        return ApiResponse.error(msg=f"上传失败: {e}", code=500)


# ── 更新 Skill ──


@router.put("/update/{skill_id}", response_model=ApiResponse)
async def update_skill(
    skill_id: str,
    user_id: str = Query(...),
    file: UploadFile = File(...),
    name: str = Form(""),
    description: str = Form(""),
    version: str = Form(""),
    tags: str = Form(""),
    author: str = Form(""),
):
    """更新已有的 skill（上传新 ZIP 覆盖）。需要管理员审核。"""
    if not file.filename or not file.filename.lower().endswith(".zip"):
        return ApiResponse.error(msg="仅支持 ZIP 文件", code=400)

    catalog = await deep_storage.get_skill_catalog(skill_id)
    if not catalog:
        return ApiResponse.error(msg="Skill 不存在", code=404)

    # 校验所有权
    if catalog.get("creator_user_id") and catalog["creator_user_id"] != user_id:
        is_admin = await deep_storage.is_admin(user_id)
        if not is_admin:
            return ApiResponse.error(msg="无权更新该 Skill", code=403)

    try:
        zip_data = await file.read()
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else ""

        result = await upload_skill_zip(
            user_id=user_id,
            zip_data=zip_data,
            name=name or catalog.get("name", ""),
            description=description or catalog.get("description", ""),
            version=version or catalog.get("version", "1.0.0"),
            tags=tag_list if isinstance(tag_list, list) else list(catalog.get("tags") or []),
            author=author or catalog.get("author", ""),
            existing_skill_id=skill_id,
        )

        # 提交更新审批
        await submit_skill_for_review(
            skill_id=skill_id,
            action_type="update",
            requested_by=user_id,
            old_version=catalog.get("version", ""),
            new_version=result["version"],
        )

        return ApiResponse.success(data=result, msg="更新已提交审核")
    except ValueError as e:
        return ApiResponse.error(msg=str(e), code=400)
    except Exception as e:
        return ApiResponse.error(msg=f"更新失败: {e}", code=500)


# ── 删除 Skill ──


@router.delete("/delete/{skill_id}", response_model=ApiResponse)
async def delete_skill(skill_id: str, user_id: str = Query(...)):
    """删除自己创建的 skill（需管理员审批）。"""
    catalog = await deep_storage.get_skill_catalog(skill_id)
    if not catalog:
        return ApiResponse.error(msg="Skill 不存在", code=404)

    if catalog.get("creator_user_id") and catalog["creator_user_id"] != user_id:
        is_admin = await deep_storage.is_admin(user_id)
        if not is_admin:
            return ApiResponse.error(msg="无权删除该 Skill", code=403)

    try:
        review_id = await submit_skill_for_review(
            skill_id=skill_id,
            action_type="delete",
            requested_by=user_id,
        )
        return ApiResponse.success(
            data={"skill_id": skill_id, "review_id": review_id},
            msg="删除申请已提交审核",
        )
    except Exception as e:
        return ApiResponse.error(msg=str(e), code=400)


# ── 导出 Skill ──


@router.get("/{skill_id}/export")
async def export_skill(skill_id: str, user_id: str = Query("")):
    """下载 skill 为 ZIP 文件。"""
    try:
        zip_bytes, filename = export_skill_as_zip(skill_id)
        encoded = quote(filename)
        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{filename}"; '
                    f"filename*=UTF-8''{encoded}"
                ),
            },
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
