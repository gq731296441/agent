"""工作区配额与文件管理 API。"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from urllib.parse import quote

from config import get_logger
from deep_agent_server.workspace_service import (
    delete_file,
    get_quota_detail,
    list_conversation_workspace_cards,
    list_files,
    list_session_workspace_cards,
    read_file_bytes,
    reconcile_user_workspace,
)
from utils.session_utils import SESSION_ACCESS_DENIED_MSG, validate_session_access

router = APIRouter(prefix="/workspace", tags=["workspace"])


@router.get("/quota")
async def workspace_quota(user_id: str = Query(..., description="用户ID")):
    try:
        return await get_quota_detail(user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/files")
async def workspace_files(
    user_id: str = Query(...),
    session_id: Optional[str] = Query(None),
    scope: str = Query("all", description="workspace|skills|artifacts|uploads|all"),
    sort_by: str = Query("name", description="排序字段: name|size|time"),
    sort_order: str = Query("asc", description="排序方向: asc|desc"),
):
    """
    列出工作区文件。

    注意：列表以磁盘扫描为准（workspace_service.list_files），不读 deep_workspace_files。
    deep_workspace_files 仅作 write/edit 后的索引与 artifact 元数据；若表为空而磁盘有文件属正常，
    可调用 POST /workspace/reconcile 重建索引。
    """
    if session_id and not await validate_session_access(user_id, session_id):
        raise HTTPException(status_code=403, detail=SESSION_ACCESS_DENIED_MSG)
    if scope not in ("workspace", "skills", "artifacts", "uploads", "all"):
        raise HTTPException(status_code=400, detail="invalid scope")
    if sort_by not in ("name", "size", "time"):
        raise HTTPException(status_code=400, detail="sort_by 须为 name / size / time")
    if sort_order not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="sort_order 须为 asc / desc")
    try:
        files = list_files(
            user_id, session_id=session_id, scope=scope,
            sort_by=sort_by, sort_order=sort_order,
        )
        return {"user_id": user_id, "session_id": session_id, "scope": scope, "files": files}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/files/by-conversation")
async def workspace_files_by_conversation(
    user_id: str = Query(...),
    session_id: str = Query(...),
    conversation_id: str = Query(...),
):
    """按 conversation 查询本轮 Agent 写入的工作区文件（含是否仍存在）。"""
    if not await validate_session_access(user_id, session_id):
        raise HTTPException(status_code=403, detail=SESSION_ACCESS_DENIED_MSG)
    try:
        files = await list_conversation_workspace_cards(
            user_id, session_id, conversation_id
        )
        return {
            "user_id": user_id,
            "session_id": session_id,
            "conversation_id": conversation_id,
            "files": files,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/files/by-session")
async def workspace_files_by_session(
    user_id: str = Query(...),
    session_id: str = Query(...),
):
    """按 session 批量返回所有轮次的工作区文件（替代逐轮调 by-conversation）。"""
    if not await validate_session_access(user_id, session_id):
        raise HTTPException(status_code=403, detail=SESSION_ACCESS_DENIED_MSG)
    try:
        grouped = await list_session_workspace_cards(user_id, session_id)
        return {
            "user_id": user_id,
            "session_id": session_id,
            "files_by_conversation": grouped,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


logger = get_logger(__name__)


@router.get("/files/download")
async def workspace_download(
    user_id: str = Query(...),
    path: str = Query(..., description="虚拟路径 workspace/{session_id}/file 或 skills/..."),
):
    try:
        if path.startswith("workspace/"):
            parts = path.strip("/").split("/", 2)
            if len(parts) >= 2 and not await validate_session_access(user_id, parts[1]):
                raise HTTPException(status_code=403, detail=SESSION_ACCESS_DENIED_MSG)
        data, mime = read_file_bytes(user_id, path)
        filename = path.rsplit("/", 1)[-1] if "/" in path else path
        encoded_filename = quote(filename)
        return Response(
            content=data,
            media_type=mime,
            headers={
                "Content-Disposition": f'attachment; filename="{encoded_filename}"; filename*=UTF-8\'\'{encoded_filename}',
            },
        )
    except FileNotFoundError as e:
        logger.warning(f"[Download] 文件不存在 user={user_id} path={path}: {e}")
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        logger.warning(f"[Download] 参数错误 user={user_id} path={path}: {e}")
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/files")
async def workspace_delete_file(
    user_id: str = Query(...),
    path: str = Query(...),
):
    try:
        if path.startswith("workspace/"):
            parts = path.strip("/").split("/", 2)
            if len(parts) >= 2 and not await validate_session_access(user_id, parts[1]):
                raise HTTPException(status_code=403, detail=SESSION_ACCESS_DENIED_MSG)
        await delete_file(user_id, path)
        return {"ok": True, "path": path}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/reconcile")
async def workspace_reconcile(user_id: str = Query(...)):
    try:
        return await reconcile_user_workspace(user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
