"""订阅智能体 Catalog / CRUD API。"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel, Field

from api.mcp_api import _resolve_mcp_source_list
from common.schemas import ApiResponse, ListApiResponse
from db import deep_storage
from deep_agent_server.skills.installer import sync_skills_to_agent
from deep_agent_server.workspace import remove_agent_directory

router = APIRouter(prefix="/subscription-agents", tags=["subscription-agents"])


class McpBindingItem(BaseModel):
    id: int
    name: str = ""


class SkillBindingItem(BaseModel):
    id: str
    name: str = ""


class SubscriptionAgentCreate(BaseModel):
    user_id: str
    name: str
    description: str = ""
    system_prompt: str = ""
    visibility: str = "public"
    config: Dict[str, Any] = Field(default_factory=dict)
    mcp_ids: List[int] = Field(
        default_factory=list,
        description="[兼容旧版] MCP ID 列表；优先使用 mcps 字段",
    )
    mcps: List[McpBindingItem] = Field(
        default_factory=list,
        description="能力市场中的 MCP 列表（含 id + name）",
    )
    skill_ids: List[str] = Field(
        default_factory=list,
        description="[兼容旧版] market_skill_id 列表；优先使用 skills 字段",
    )
    skills: List[SkillBindingItem] = Field(
        default_factory=list,
        description="能力市场中的 Skill 列表（含 id + name）",
    )


class SubscriptionAgentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    visibility: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    mcp_ids: Optional[List[int]] = Field(
        default=None,
        description="[兼容旧版] MCP ID 列表；优先使用 mcps 字段",
    )
    mcps: Optional[List[McpBindingItem]] = Field(
        default=None,
        description="能力市场中的 MCP 列表（含 id + name）",
    )
    skill_ids: Optional[List[str]] = Field(
        default=None,
        description="[兼容旧版] market_skill_id 列表；优先使用 skills 字段",
    )
    skills: Optional[List[SkillBindingItem]] = Field(
        default=None,
        description="能力市场中的 Skill 列表（含 id + name）",
    )


async def _resolve_mcp_names(user_id: str, mcp_ids: List[int]) -> Dict[int, str]:
    """从用户的 MCP catalog 中批量解析名称。"""
    if not mcp_ids:
        return {}
    try:
        mcp_list, _ = await _resolve_mcp_source_list(user_id)
        return {int(m["id"]): m["name"] for m in mcp_list if m.get("id") and m.get("name")}
    except Exception:
        return {}


from utils.agent_avatar import avatar_color_from_seed


def _catalog_item(row: dict, viewer_id: str) -> dict:
    agent_id = str(row["id"])
    return {
        "id": agent_id,
        "name": row["name"],
        "description": row.get("description") or "",
        "creator_user_id": row.get("creator_user_id"),
        "visibility": row.get("visibility", "public"),
        "avatar_color": avatar_color_from_seed(agent_id),
        "subscriber_count": int(row.get("subscriber_count") or 0),
        "used_user_count": int(row.get("used_user_count") or 0),
        "is_subscribed": bool(row.get("is_subscribed")),
        "archived": bool(row.get("archived") or row.get("is_deleted")),
        "updated_at": row.get("updated_at").isoformat() if row.get("updated_at") else "",
    }


@router.get("/catalog", response_model=ListApiResponse)
async def catalog(
    user_id: str = Query(...),
    tab: str = Query("all", pattern="^(all|subscribed|mine)$"),
    q: str = Query(""),
):
    rows = await deep_storage.list_subscription_catalog(user_id, tab, q.strip())
    if tab == "mine":
        enriched = []
        for row in rows:
            item = _catalog_item(row, user_id)
            stats = await deep_storage.get_agent_stats(row["id"], user_id)
            if stats:
                item.update({
                    "total_tokens": stats["total_tokens"],
                    "completion_rate": stats["completion_rate"],
                    "user_count": stats["user_count"],
                })
            enriched.append(item)
        return ListApiResponse.success(data=enriched)
    return ListApiResponse.success(data=[_catalog_item(r, user_id) for r in rows])


@router.get("/used", response_model=ListApiResponse)
async def used_agents(user_id: str = Query(...)):
    items = await deep_storage.list_used_subscription_agents(user_id)
    return ListApiResponse.success(data=items)


@router.post("", response_model=ApiResponse)
async def create_agent(body: SubscriptionAgentCreate = Body(...)):
    vis = (body.visibility or "public").lower()
    if vis not in ("public", "private"):
        return ApiResponse.error(msg="visibility 须为 public 或 private", code=400)
    agent_id = await deep_storage.create_user_agent(
        body.user_id,
        body.name,
        body.system_prompt,
        description=body.description,
        visibility=vis,
        config=body.config,
    )
    # MCP 绑定：优先使用 mcps（含名称），兼容旧 mcp_ids
    if body.mcps:
        mcp_ids = [m.id for m in body.mcps]
        mcp_names = {m.id: m.name for m in body.mcps if m.name}
    elif body.mcp_ids:
        mcp_ids = body.mcp_ids
        mcp_names = await _resolve_mcp_names(body.user_id, body.mcp_ids)
    else:
        mcp_ids = []
        mcp_names = {}
    if mcp_ids:
        await deep_storage.set_agent_mcp_bindings(agent_id, mcp_ids, mcp_names=mcp_names)
    # Skill 绑定：优先使用 skills（含名称），兼容旧 skill_ids
    if body.skills:
        skill_id_list = [s.id for s in body.skills]
        skill_name_map = {s.id: s.name for s in body.skills if s.name}
    elif body.skill_ids:
        skill_id_list = body.skill_ids
        skill_name_map = None
    else:
        skill_id_list = []
        skill_name_map = None
    if skill_id_list:
        market_ids = await deep_storage.set_agent_skill_bindings(
            agent_id, body.user_id, skill_id_list, skill_names=skill_name_map
        )
        sync_skills_to_agent(agent_id, market_ids)
    return ApiResponse.success(data={"agent_id": agent_id})


@router.get("/{agent_id}", response_model=ApiResponse)
async def get_agent(agent_id: str, user_id: str = Query(...)):
    agent = await deep_storage.resolve_agent_for_caller(
        agent_id, user_id, include_prompt=True
    )
    if not agent:
        return ApiResponse.error(msg="无权访问或不存在", code=403)

    # 解析 MCP：id + name 合并返回
    mcp_ids = await deep_storage.get_agent_mcp_ids(agent_id)
    mcps = []
    if mcp_ids:
        name_map: Dict[int, str] = {}
        # L1: 创建者的 MCP catalog
        creator_id = agent.get("user_id") or agent.get("creator_user_id") or ""
        if creator_id:
            name_map.update(await _resolve_mcp_names(creator_id, mcp_ids))
        # L2: 查看者的 MCP catalog（兜底未命中的）
        missing_ids = [mid for mid in mcp_ids if mid not in name_map or not name_map[mid]]
        if missing_ids and user_id != creator_id:
            name_map.update(await _resolve_mcp_names(user_id, missing_ids))
        # L3: DB 快照（最终兜底）
        snapshots = await deep_storage.get_agent_mcp_snapshot_names(agent_id)
        for mid, snap_name in snapshots.items():
            if mid in mcp_ids and (mid not in name_map or not name_map[mid]):
                name_map[mid] = snap_name
        for mid in mcp_ids:
            mcps.append({"id": mid, "name": name_map.get(mid, "")})
    agent["mcps"] = mcps

    # 解析 Skill：id + name 合并返回
    skills = []
    try:
        for s in await deep_storage.get_agent_skill_names(agent_id):
            skills.append({"id": s["installed_skill_id"], "name": s["skill_name"]})
    except Exception:
        pass
    agent["skills"] = skills

    return ApiResponse.success(data=agent)


@router.put("/{agent_id}", response_model=ApiResponse)
async def update_agent(
    agent_id: str,
    body: SubscriptionAgentUpdate = Body(...),
    user_id: str = Query(...),
):
    ok = await deep_storage.update_user_agent(
        agent_id,
        user_id,
        name=body.name,
        description=body.description,
        system_prompt=body.system_prompt,
        visibility=body.visibility,
        config=body.config,
    )
    if body.mcps is not None or body.mcp_ids is not None:
        if body.mcps is not None:
            mcp_ids: List[int] = [m.id for m in body.mcps]
            mcp_names = {m.id: m.name for m in body.mcps if m.name}
        else:
            mcp_ids = list(body.mcp_ids or [])
            mcp_names = await _resolve_mcp_names(user_id, mcp_ids)
        await deep_storage.set_agent_mcp_bindings(
            agent_id, mcp_ids, mcp_names=mcp_names
        )
    if body.skills is not None or body.skill_ids is not None:
        if body.skills is not None:
            skill_id_list = [s.id for s in body.skills]
            skill_name_map = {s.id: s.name for s in body.skills if s.name}
        else:
            skill_id_list = list(body.skill_ids or [])
            skill_name_map = None
        market_ids = await deep_storage.set_agent_skill_bindings(
            agent_id, user_id, skill_id_list, skill_names=skill_name_map
        )
        sync_skills_to_agent(agent_id, market_ids)
    if not ok and body.mcp_ids is None and body.mcps is None and body.skill_ids is None and body.skills is None:
        return ApiResponse.error(msg="无更新或 Agent 不存在", code=404)
    # 切换为私有时冻结所有订阅者
    if body.visibility == "private":
        await deep_storage.freeze_agent_subscribers(agent_id, user_id)
    return ApiResponse.success(msg="已更新")


@router.delete("/{agent_id}", response_model=ApiResponse)
async def delete_agent(agent_id: str, user_id: str = Query(...)):
    ok = await deep_storage.hard_delete_user_agent(agent_id, user_id)
    if not ok:
        return ApiResponse.error(msg="智能体不存在", code=404)
    remove_agent_directory(agent_id)
    return ApiResponse.success(msg="已删除")


@router.delete("/{agent_id}/permanent", response_model=ApiResponse)
async def permanently_delete_agent(agent_id: str, user_id: str = Query(...)):
    """向后兼容：行为与直接删除一致，无需先下架。"""
    ok = await deep_storage.hard_delete_user_agent(agent_id, user_id)
    if not ok:
        return ApiResponse.error(msg="智能体不存在", code=404)
    remove_agent_directory(agent_id)
    return ApiResponse.success(msg="已删除")


@router.post("/{agent_id}/subscribe", response_model=ApiResponse)
async def subscribe(agent_id: str, user_id: str = Query(...)):
    agent = await deep_storage.resolve_agent_for_caller(agent_id, user_id)
    if not agent or agent.get("archived"):
        return ApiResponse.error(msg="智能体不存在或已下架", code=404)
    await deep_storage.subscribe_agent(user_id, agent_id)
    return ApiResponse.success(msg="已订阅")


@router.delete("/{agent_id}/subscribe", response_model=ApiResponse)
async def unsubscribe(agent_id: str, user_id: str = Query(...)):
    await deep_storage.unsubscribe_agent(user_id, agent_id)
    return ApiResponse.success(msg="已取消订阅")


@router.get("/{agent_id}/stats", response_model=ApiResponse)
async def agent_stats(agent_id: str, user_id: str = Query(...)):
    stats = await deep_storage.get_agent_stats(agent_id, user_id)
    if not stats:
        return ApiResponse.error(msg="无权访问", code=403)
    return ApiResponse.success(data=stats)


@router.get("/{agent_id}/runs", response_model=ListApiResponse)
async def agent_runs(
    agent_id: str,
    user_id: str = Query(...),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    runs = await deep_storage.list_agent_runs_for_owner(agent_id, user_id, limit, offset)
    if runs is None:
        return ListApiResponse.error(msg="无权访问", code=403)
    return ListApiResponse.success(data=runs)
