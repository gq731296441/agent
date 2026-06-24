"""User Agent 模板 CRUD API。"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel, Field

from common.schemas import ApiResponse, ListApiResponse
from db import deep_storage

router = APIRouter(prefix="/user_agents", tags=["user_agents"])


class UserAgentCreate(BaseModel):
    user_id: str
    name: str = "My Agent"
    description: str = ""
    system_prompt: str = ""
    model_name: str = "nebulacoder-v8"
    config: Dict[str, Any] = Field(default_factory=dict)
    mcp_ids: List[int] = Field(default_factory=list)


class UserAgentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    model_name: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    mcp_ids: Optional[List[int]] = None


@router.post("/", response_model=ApiResponse)
async def create_user_agent(body: UserAgentCreate = Body(...)):
    agent_id = await deep_storage.create_user_agent(
        body.user_id,
        body.name,
        body.system_prompt,
        body.model_name,
        body.config,
        body.description,
    )
    if body.mcp_ids:
        await deep_storage.set_agent_mcp_bindings(agent_id, body.mcp_ids)
    return ApiResponse.success(data={"agent_id": agent_id})


@router.get("/", response_model=ListApiResponse)
async def list_user_agents(user_id: str = Query(...)):
    agents = await deep_storage.list_user_agents(user_id)
    return ListApiResponse.success(data=agents)


@router.get("/{agent_id}", response_model=ApiResponse)
async def get_user_agent(agent_id: str, user_id: str = Query(...)):
    agent = await deep_storage.get_user_agent(agent_id, user_id)
    if not agent:
        return ApiResponse.error(msg="Agent 不存在", code=404)
    agent["mcp_ids"] = await deep_storage.get_agent_mcp_ids(agent_id)
    return ApiResponse.success(data=agent)


@router.put("/{agent_id}", response_model=ApiResponse)
async def update_user_agent(agent_id: str, body: UserAgentUpdate = Body(...), user_id: str = Query(...)):
    ok = await deep_storage.update_user_agent(
        agent_id,
        user_id,
        name=body.name,
        description=body.description,
        system_prompt=body.system_prompt,
        model_name=body.model_name,
        config=body.config,
    )
    if body.mcp_ids is not None:
        await deep_storage.set_agent_mcp_bindings(agent_id, body.mcp_ids)
    if not ok and body.mcp_ids is None:
        return ApiResponse.error(msg="无更新或 Agent 不存在", code=404)
    return ApiResponse.success(msg="已更新")


@router.delete("/{agent_id}", response_model=ApiResponse)
async def delete_user_agent(agent_id: str, user_id: str = Query(...)):
    ok = await deep_storage.hard_delete_user_agent(agent_id, user_id)
    if not ok:
        return ApiResponse.error(msg="Agent 不存在", code=404)
    return ApiResponse.success(msg="已删除")
