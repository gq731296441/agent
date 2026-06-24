"""沙箱容量与状态 API。"""

from fastapi import APIRouter, HTTPException, Query

from deep_agent_server.sandbox import (
    get_sandbox_user_status,
    release_session_sandbox,
    stop_user_sandboxes,
)

router = APIRouter(prefix="/sandbox", tags=["sandbox"])


@router.get("/status")
async def sandbox_status(user_id: str = Query(..., description="用户 ID")):
    try:
        return await get_sandbox_user_status(user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/stop")
async def sandbox_stop(
    user_id: str = Query(..., description="用户 ID"),
    session_id: str = Query(..., description="会话 ID"),
):
    """手动 stop 沙箱（保留容器供 docker start 复用）。"""
    try:
        released = await release_session_sandbox(user_id, session_id)
        return {"status": True, "released": released}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/stop-all")
async def sandbox_stop_all(user_id: str = Query(..., description="用户 ID")):
    """停止用户全部 session 沙箱。"""
    try:
        await stop_user_sandboxes(user_id)
        return {"status": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
