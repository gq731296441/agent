"""Agent 运行状态 API。"""

from fastapi import APIRouter, Query, Request
from common.schemas import ApiResponse
from db import deep_storage

router = APIRouter(prefix="/agent", tags=["agent-runs"])


@router.get("/runs/active", response_model=ApiResponse)
async def list_active_runs(
    request: Request,
    userId: str = Query(..., description="用户ID"),
):
    """返回当前用户仍在执行的会话（来自 AgentManager._active_tasks）。"""
    manager = getattr(request.app.state, "manager_agents", None)
    if manager is None or not hasattr(manager, "list_active_runs_for_user"):
        return ApiResponse.success(data=[])
    items = manager.list_active_runs_for_user(userId)
    return ApiResponse.success(data=items)


@router.get("/runs/{run_id}", response_model=ApiResponse)
async def get_agent_run_status(
    run_id: str,
    user_id: str = Query(..., description="用户ID"),
):
    run = await deep_storage.get_agent_run(run_id, user_id)
    if not run:
        return ApiResponse.error(msg="运行记录不存在", code=404)
    return ApiResponse.success(data=run)
