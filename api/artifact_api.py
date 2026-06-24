"""Artifact 下载 API。"""

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse
from deep_agent_server.artifact import read_artifact_content

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.get("/{ref_id}")
async def get_artifact(
    ref_id: str,
    request: Request,
    user_id: str = Query(..., description="用户ID"),
    session_id: str = Query(None, description="会话ID（Store fallback 可选）"),
):
    store = getattr(request.app.state, "kv_store", None)
    content = await read_artifact_content(
        ref_id, user_id, store=store, session_id=session_id
    )
    if content is None:
        return PlainTextResponse("Not found", status_code=404)
    return PlainTextResponse(content, media_type="text/plain; charset=utf-8")
