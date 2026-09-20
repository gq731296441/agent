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
    """手动 stop 沙箱（保留容器供 docker start 复用）。

    失败必须以 4xx/5xx 返回：前端只看 HTTP 状态码（Agenta.vue 的 catch 分支），
    `200 + status:false` 会被当成成功并弹出"沙箱已停止"。
    成功路径保持 200（前端的 axios 拦截器会把 201/204 主动抛成异常）。
    """
    try:
        from db import deep_storage

        row = await deep_storage.get_sandbox_session(user_id, session_id)
        has_container = bool((row or {}).get("container_id"))

        released = await release_session_sandbox(user_id, session_id)

        # release 返回 False 有两种含义："本来就没有沙箱"（正常）或"停止失败"。
        # status 已是 stopped 时 release 会返回 True，故此处必然是真实失败。
        if has_container and not released:
            raise HTTPException(status_code=500, detail="停止沙箱失败，请稍后重试")

        return {"status": True, "released": released}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/stop-all")
async def sandbox_stop_all(user_id: str = Query(..., description="用户 ID")):
    """停止用户全部 session 沙箱。"""
    try:
        stopped = await stop_user_sandboxes(user_id)
        if not stopped:
            raise HTTPException(
                status_code=500, detail="停止全部沙箱时部分失败，请稍后重试"
            )
        return {"status": True}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
