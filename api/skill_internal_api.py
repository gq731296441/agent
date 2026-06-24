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
