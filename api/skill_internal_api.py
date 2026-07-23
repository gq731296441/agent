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


# ── VM 迁移 ──


@router.post("/migrate-vm", response_model=ApiResponse)
async def migrate_to_vm_repo(x_internal_token: str | None = Header(default=None)):
    """首次部署/手动触发：将项目 skill-repo/ 非内置 skill 迁移到 VM .skill-repo/。"""
    _verify_internal_token(x_internal_token)
    from deep_agent_server.skills.vm_repo import migrate_non_builtin_skills

    result = await migrate_non_builtin_skills()
    return ApiResponse.success(data=result, msg="迁移完成")


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
