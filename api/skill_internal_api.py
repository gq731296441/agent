"""Skill 市场管理 API（仅内部/sync）。"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from common.schemas import ApiResponse
from config.config import app_config
from db import deep_storage
from deep_agent_server.skills.catalog_sync import sync_catalog_from_repo

router = APIRouter(prefix="/internal/skills", tags=["internal-skills"])


def _verify_internal_token(x_internal_token: str | None) -> None:
    expected = (app_config.SKILL_INTERNAL_TOKEN or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="SKILL_INTERNAL_TOKEN 未配置")
    if (x_internal_token or "").strip() != expected:
        raise HTTPException(status_code=401, detail="无效的内部 Token")


class SkillStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(draft|published|archived)$")


@router.post("/sync", response_model=ApiResponse)
async def sync_skills(x_internal_token: str | None = Header(default=None)):
    _verify_internal_token(x_internal_token)
    result = await sync_catalog_from_repo()
    return ApiResponse.success(data=result, msg="同步完成")


@router.put("/catalog/{skill_id}/status", response_model=ApiResponse)
async def set_catalog_status(
    skill_id: str,
    body: SkillStatusUpdate,
    x_internal_token: str | None = Header(default=None),
):
    _verify_internal_token(x_internal_token)
    ok = await deep_storage.set_skill_catalog_status(skill_id, body.status)
    if not ok:
        return ApiResponse.error(msg="Skill 不存在", code=404)
    return ApiResponse.success(msg="状态已更新")


# ── 可信导入（P1e）──


class ImportBody(BaseModel):
    package_id: str
    version: str
    source_path: str = Field(..., description="服务器本地 ZIP 或目录路径")
    policy: str = Field(default="trusted_import", pattern="^(trusted_import|emergency_import)$")
    reason: str = ""
    visibility: str = "public"
    origin: str = "official"
    operator: str = "system"


@router.post("/import", response_model=ApiResponse)
async def import_trusted_skill(
    body: ImportBody,
    x_internal_token: str | None = Header(default=None),
):
    """管理员/内部可信导入：可跳过扫描与人审，不可跳过结构校验。"""
    _verify_internal_token(x_internal_token)
    from pathlib import Path

    from deep_agent_server.skills.marketplace.publish_service import (
        PublishError,
        get_publish_service,
    )

    src = Path(body.source_path)
    try:
        result = await get_publish_service().import_trusted(
            source=src,
            package_id=body.package_id.strip(),
            version=body.version.strip(),
            operator=body.operator or "system",
            policy=body.policy,
            reason=body.reason,
            visibility=body.visibility,
            origin=body.origin,
        )
        return ApiResponse.success(data=result, msg="导入完成")
    except PublishError as e:
        return ApiResponse.error(msg=e.message, code=e.code)


# ── VM 迁移（已废弃：保留接口但提示改用 import）──


@router.post("/migrate-vm", response_model=ApiResponse)
async def migrate_to_vm_repo(x_internal_token: str | None = Header(default=None)):
    _verify_internal_token(x_internal_token)
    return ApiResponse.error(
        msg="已废弃：请使用 POST /internal/skills/import 与显式 package_id+version 清单",
        code=410,
    )


# ── 管理员初始化 ──


class BootstrapAdminBody(BaseModel):
    user_id: str


@router.post("/admin/bootstrap", response_model=ApiResponse)
async def bootstrap_admin(
    body: BootstrapAdminBody,
    x_internal_token: str | None = Header(default=None),
):
    """初始化首位超级管理员（仅当 admin_users 表为空时有效）。"""
    _verify_internal_token(x_internal_token)
    existing = await deep_storage.list_admins()
    if existing:
        return ApiResponse.error(msg="管理员已存在，请通过 /admin/admins 接口添加", code=400)
    await deep_storage.add_admin(body.user_id, created_by="system", role="super_admin")
    return ApiResponse.success(msg=f"首位超级管理员已添加: {body.user_id}")
