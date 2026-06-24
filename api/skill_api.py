"""Skill 市场与用户订阅 API。"""

from typing import List, Optional

from fastapi import APIRouter, Query

from common.schemas import ApiResponse, ListApiResponse, PaginatedApiResponse
from db import deep_storage
from deep_agent_server.skills.installer import (
    SkillInstallError,
    install_skill_for_user,
    uninstall_skill_for_user,
    upgrade_skill_for_user,
)

router = APIRouter(prefix="/skills", tags=["skills"])


def _parse_tags(tags: str) -> Optional[List[str]]:
    raw = (tags or "").strip()
    if not raw:
        return None
    return [t.strip() for t in raw.split(",") if t.strip()]


@router.get("/catalog", response_model=PaginatedApiResponse)
async def list_catalog(
    user_id: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str = Query(""),
    tags: str = Query(""),
):
    tag_list = _parse_tags(tags)
    items, total = await deep_storage.list_skill_catalog(
        page=page,
        page_size=page_size,
        q=q.strip(),
        tags=tag_list,
        user_id=user_id.strip() or None,
        status="published",
    )
    return PaginatedApiResponse.success(items, total, page, page_size)


@router.get("/catalog/tags", response_model=ListApiResponse)
async def list_catalog_tags():
    tags = await deep_storage.list_skill_catalog_tags(status="published")
    return ListApiResponse.success(data=tags)


@router.get("/catalog/{skill_id}", response_model=ApiResponse)
async def get_catalog_item(
    skill_id: str,
    user_id: str = Query(""),
):
    row = await deep_storage.get_skill_catalog(skill_id)
    if not row or row.get("status") != "published":
        return ApiResponse.error(msg="Skill 不存在", code=404)
    data = dict(row)
    if data.get("created_at"):
        data["created_at"] = data["created_at"].isoformat()
    if data.get("updated_at"):
        data["updated_at"] = data["updated_at"].isoformat()
    data["tags"] = list(data.get("tags") or [])
    if user_id.strip():
        inst = await deep_storage.get_installed_skill(user_id.strip(), skill_id)
        data["subscribed"] = inst is not None
        if inst:
            data["installed_version"] = inst.get("version")
    from db.database import db_manager

    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        cnt = await conn.fetchval(
            "SELECT COUNT(*)::int FROM user_installed_skills WHERE market_skill_id = $1",
            skill_id,
        )
    data["subscribe_count"] = int(cnt or 0)
    return ApiResponse.success(data=data)


@router.get("/subscribed", response_model=ListApiResponse)
async def list_subscribed(user_id: str = Query(...)):
    rows = await deep_storage.list_user_subscribed_skills(user_id)
    return ListApiResponse.success(data=rows)


@router.post("/subscribe/{skill_id}", response_model=ApiResponse)
async def subscribe_skill(skill_id: str, user_id: str = Query(...)):
    try:
        data = await install_skill_for_user(user_id, skill_id)
        return ApiResponse.success(data=data, msg="订阅成功")
    except SkillInstallError as e:
        return ApiResponse.error(msg=str(e), code=e.code)


@router.delete("/unsubscribe/{skill_id}", response_model=ApiResponse)
async def unsubscribe_skill(skill_id: str, user_id: str = Query(...)):
    try:
        await uninstall_skill_for_user(user_id, skill_id)
        return ApiResponse.success(msg="已取消订阅")
    except SkillInstallError as e:
        return ApiResponse.error(msg=str(e), code=e.code)


@router.post("/subscribed/{skill_id}/upgrade", response_model=ApiResponse)
async def upgrade_subscribed_skill(skill_id: str, user_id: str = Query(...)):
    try:
        data = await upgrade_skill_for_user(user_id, skill_id)
        msg = "已更新到最新版本" if data.get("upgraded") else data.get("message", "已是最新版本")
        return ApiResponse.success(data=data, msg=msg)
    except SkillInstallError as e:
        return ApiResponse.error(msg=str(e), code=e.code)


# ---------- 兼容旧 Mock 路由 ----------

@router.get("/market", response_model=ListApiResponse)
async def list_market_legacy(user_id: str = Query("")):
    items, _ = await deep_storage.list_skill_catalog(
        page=1, page_size=100, user_id=user_id.strip() or None, status="published"
    )
    legacy = [
        {
            "id": r["skill_id"],
            "name": r["name"],
            "description": r.get("description") or "",
            "version": r.get("version") or "1.0.0",
        }
        for r in items
    ]
    return ListApiResponse.success(data=legacy)


@router.get("/installed", response_model=ListApiResponse)
async def list_installed(user_id: str = Query(...)):
    rows = await deep_storage.list_user_subscribed_skills(user_id)
    merged = []
    for row in rows:
        merged.append({
            "id": row.get("installed_skill_id"),
            "market_skill_id": row.get("skill_id"),
            "skill_name": row.get("skill_name") or row.get("name"),
            "install_dir": row.get("install_dir"),
            "version": row.get("installed_version"),
            "source": "catalog",
            "has_update": row.get("has_update"),
            "latest_version": row.get("latest_version"),
        })
    return ListApiResponse.success(data=merged)


@router.post("/market/install/{skill_id}", response_model=ApiResponse)
async def install_skill_legacy(skill_id: str, user_id: str = Query(...)):
    return await subscribe_skill(skill_id, user_id)


@router.put("/installed/{skill_id}", response_model=ApiResponse)
async def reinstall_skill_legacy(skill_id: str, user_id: str = Query(...)):
    return await upgrade_subscribed_skill(skill_id, user_id)


@router.delete("/installed/{skill_id}", response_model=ApiResponse)
async def uninstall_skill_legacy(skill_id: str, user_id: str = Query(...)):
    return await unsubscribe_skill(skill_id, user_id)
