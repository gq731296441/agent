"""工作区配额与文件管理 API。"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, HTMLResponse
from pydantic import BaseModel, Field
from urllib.parse import quote

from config import get_logger
from db.html_share_store import create_share_token, get_share_token, touch_share_token
from deep_agent_server.html_inline import inline_html_references
from deep_agent_server.workspace_service import (
    delete_file,
    get_quota_detail,
    list_conversation_workspace_cards,
    list_files,
    list_files_tree,
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
    format: str = Query("flat", description="返回格式: flat（平铺列表，默认）| tree（树状结构）"),
):
    """
    列出工作区文件。

    format=flat（默认）：返回扁平文件列表，见 files 字段。
    format=tree：返回树状结构（含文件夹节点），见 tree 字段；每层文件夹在前、文件在后，
                各分组内按 sort_by/sort_order 排序。

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
    tree_mode = format == "tree"
    try:
        if tree_mode:
            tree = list_files_tree(
                user_id, session_id=session_id, scope=scope,
                sort_by=sort_by, sort_order=sort_order,
            )
            return {
                "user_id": user_id,
                "session_id": session_id,
                "scope": scope,
                "format": "tree",
                "tree": tree,
            }
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
    recursive: bool = Query(False, description="递归删除目录（删除文件夹时需传 true）"),
):
    try:
        if path.startswith("workspace/"):
            parts = path.strip("/").split("/", 2)
            if len(parts) >= 2 and not await validate_session_access(user_id, parts[1]):
                raise HTTPException(status_code=403, detail=SESSION_ACCESS_DENIED_MSG)
        await delete_file(user_id, path, recursive=recursive)
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


# ── HTML 分享链接 ─────────────────────────────────────────────


class HtmlShareRequest(BaseModel):
    user_id: str = Field(..., description="用户ID")
    path: str = Field(..., description="HTML 文件虚拟路径 workspace/{session_id}/...")
    expires_in_hours: Optional[int] = Field(None, description="过期小时数，None 表示永不过期")


@router.post("/html-share")
async def create_html_share(data: HtmlShareRequest):
    """创建 HTML 文件的免登录分享链接。

    仅 .html 文件可分享；需校验用户对 session 的访问权限。
    """
    vpath = (data.path or "").strip().lstrip("/").replace("\\", "/")

    # 文件类型校验
    if not vpath.lower().endswith(".html") and not vpath.lower().endswith(".htm"):
        raise HTTPException(status_code=400, detail="仅支持分享 .html / .htm 文件")

    # 校验 session 访问权限
    if vpath.startswith("workspace/"):
        parts = vpath.split("/", 2)
        if len(parts) >= 2:
            if not await validate_session_access(data.user_id, parts[1]):
                raise HTTPException(status_code=403, detail=SESSION_ACCESS_DENIED_MSG)
    else:
        raise HTTPException(status_code=400, detail="仅支持 workspace 路径下的文件")

    # 校验文件存在
    try:
        read_file_bytes(data.user_id, vpath)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="文件不存在")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # 创建 token
    token = await create_share_token(
        user_id=data.user_id,
        session_id=parts[1],
        file_path=vpath,
        expires_in_hours=data.expires_in_hours,
    )
    return {"ok": True, "token": token}


@router.get("/html-share/{token}", response_class=HTMLResponse)
async def view_html_share(token: str):
    """通过分享 token 查看内联后的完整 HTML 页面（无需登录）。"""
    record = await get_share_token(token)
    if not record:
        raise HTTPException(status_code=404, detail="分享链接不存在或已过期")

    # 读取并内联 HTML
    try:
        html_text = inline_html_references(
            user_id=record["user_id"],
            html_virtual_path=record["file_path"],
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="原文件已被删除")
    except Exception as e:
        logger.exception(f"[HtmlShare] 内联渲染失败 token={token}: {e}")
        raise HTTPException(status_code=500, detail="页面渲染失败")

    # 记录访问
    await touch_share_token(token)

    return HTMLResponse(content=html_text, status_code=200)
