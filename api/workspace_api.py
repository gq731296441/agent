"""工作区配额与文件管理 API。"""

import asyncio
import hashlib
import shutil
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, HTMLResponse
from pydantic import BaseModel, Field
from urllib.parse import quote

from config import get_logger
from db.html_share_store import (
    create_or_reuse_share_token,
    create_share_token,
    delete_share_token,
    get_share_token,
    list_share_tokens,
    touch_share_token,
    update_share_source,
)
from db import deep_storage
from deep_agent_server.html_inline import inline_html_references
from deep_agent_server.html_share_snapshot import (
    SNAPSHOT_DIR_NAME,
    SNAPSHOT_INDEX_NAME,
    write_share_snapshot,
)
from deep_agent_server.workspace import get_workspace_dir
from deep_agent_server.workspace_service import (
    count_workspace_basename,
    delete_file,
    find_workspace_file_by_sha256,
    get_quota_detail,
    list_conversation_workspace_cards,
    list_files,
    list_files_tree,
    list_session_workspace_cards,
    read_file_bytes,
    reconcile_user_workspace,
    resolve_virtual_path,
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
    agent_id: Optional[str] = Query(None, description="订阅智能体会话上下文（仅 skills/ 前缀生效）"),
):
    try:
        if path.startswith("workspace/"):
            parts = path.strip("/").split("/", 2)
            if len(parts) >= 2 and not await validate_session_access(user_id, parts[1]):
                raise HTTPException(status_code=403, detail=SESSION_ACCESS_DENIED_MSG)
        data, mime = read_file_bytes(user_id, path, agent_id=agent_id)
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


# 分享创建/重建的进程内锁（per-key，防并发竞态）
_html_share_create_locks: dict = {}
_html_share_rebuild_locks: dict = {}


def _lock_for(pool: dict, key: str) -> asyncio.Lock:
    if key not in pool:
        pool[key] = asyncio.Lock()
    return pool[key]


@router.post("/html-share")
async def create_html_share(data: HtmlShareRequest):
    """创建 HTML 文件的免登录分享链接（带完整资源快照）。

    仅 .html 文件可分享；需校验用户对 session 的访问权限。
    快照（HTML+JS+CSS+图片）复制到 workspace/{sid}/.html-share-assets/{token}/，
    与技能/上传目录生命周期解耦；同一文件唯一 token（四级复用链，
    见 create_or_reuse_share_token）——重复点击/后端自动分享均复用同一 token。
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

    sid = parts[1]

    # 读取 HTML（同时校验文件存在）
    try:
        html_bytes, _ = read_file_bytes(data.user_id, vpath)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="文件不存在")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    sha = hashlib.sha256(html_bytes).hexdigest()
    # 复用链③ 唯一性判定：会话工作区内该 basename 唯一（防多目录同名误并）
    basename_unique = count_workspace_basename(data.user_id, sid, vpath.rsplit("/", 1)[-1]) == 1
    # 订阅智能体会话：skills 资源解析到 .agents/{agent_id}/skills/
    agent_id = await deep_storage.get_session_agent_id(sid)

    async with _lock_for(_html_share_create_locks, f"{data.user_id}:{vpath}"):
        token, reused, reason = await create_or_reuse_share_token(
            user_id=data.user_id,
            session_id=sid,
            file_path=vpath,
            sha256=sha,
            expires_in_hours=data.expires_in_hours,
            basename_unique=basename_unique,
        )

        # ── 资源快照：复制引用到快照目录，改写 HTML 指向快照 ──
        # 复用/新建均重建快照（内容跟随最新创建时刻）
        try:
            await _build_share_snapshot(
                user_id=data.user_id,
                html_text=html_bytes.decode("utf-8", errors="replace"),
                vpath=vpath,
                sid=sid,
                token=token,
                agent_id=agent_id,
            )
        except Exception as e:
            logger.exception(f"[HtmlShare] 快照构建失败 token={token} path={vpath}: {e}")
            raise HTTPException(status_code=500, detail="分享快照构建失败")

        await update_share_source(token, vpath, sha)

    logger.info(
        f"[HtmlShare] 分享创建/复用完成 token={token} path={vpath} reused={reused} reason={reason}"
    )
    return {"ok": True, "token": token, "reused": reused}


async def _build_share_snapshot(
    *,
    user_id: str,
    html_text: str,
    vpath: str,
    sid: str,
    token: str,
    agent_id: Optional[str],
) -> None:
    """构建分享快照：复制图片到快照目录，快照 HTML 引用指向快照。

    实际实现见 deep_agent_server.html_share_snapshot.write_share_snapshot
    （与 Agent 自动分享、访问时懒重建共用同一入口）。
    """
    await write_share_snapshot(
        user_id=user_id,
        html_text=html_text,
        vpath=vpath,
        session_id=sid,
        token=token,
        agent_id=agent_id,
    )


@router.get("/html-share/list")
async def list_html_shares(
    user_id: str = Query(...),
    session_id: Optional[str] = Query(None),
):
    """列出用户的分享 token（分享管理界面用）。

    注意：本路由必须声明在 GET /html-share/{token} 之前（FastAPI 按声明顺序匹配路径，
    /html-share/list 若在后会被 {token} 捕获成 token="list"）。
    """
    records = await list_share_tokens(user_id, session_id)
    out = []
    for r in records:
        if not r.get("valid"):
            continue
        sid = r["session_id"]
        snap_abs, *_ = resolve_virtual_path(
            user_id, f"workspace/{sid}/{SNAPSHOT_DIR_NAME}/{r['token']}/{SNAPSHOT_INDEX_NAME}"
        )
        src_abs = None
        if r.get("file_path"):
            try:
                src_cand, *_ = resolve_virtual_path(user_id, r["file_path"])
                if src_cand.is_file():
                    src_abs = src_cand
            except ValueError:
                pass
        out.append({
            "token": r["token"],
            "path": r.get("file_path") or "",
            "file_key": r.get("file_key") or "",
            "session_id": sid,
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            "expires_at": r["expires_at"].isoformat() if r.get("expires_at") else None,
            "access_count": r.get("access_count") or 0,
            "last_access": r["last_access"].isoformat() if r.get("last_access") else None,
            "snapshot_exists": snap_abs.is_file(),
            "source_exists": src_abs is not None,
        })
    return {"shares": out}


@router.get("/html-share/{token}", response_class=HTMLResponse)
async def view_html_share(token: str):
    """通过分享 token 查看完整快照页面（无需登录）。

    流程：定位源文件（改名后按 basename+sha 迁移）→ 懒重建快照（源内容变化时）→ 渲染快照。
    快照（workspace/{sid}/.html-share-assets/{token}/）与源文件生命周期解耦：
    源文件改名/删除/技能卸载均不影响分享页；编辑源文件后首次访问即更新快照（实时）。
    """
    record = await get_share_token(token)
    if not record:
        raise HTTPException(status_code=404, detail="分享链接不存在或已过期")

    user_id, sid = record["user_id"], record["session_id"]
    snapshot_path = f"workspace/{sid}/{SNAPSHOT_DIR_NAME}/{token}/{SNAPSHOT_INDEX_NAME}"
    snapshot_abs, *_ = resolve_virtual_path(user_id, snapshot_path)
    snapshot_exists = snapshot_abs.is_file()

    agent_id = await deep_storage.get_session_agent_id(sid)

    # ── (a) 定位源文件（shell 内 mv 改名后按 basename+sha 找回并迁移路径）──
    source_vpath = record.get("file_path") or ""
    source_abs = None
    if source_vpath:
        try:
            cand, *_ = resolve_virtual_path(user_id, source_vpath)
            if cand.is_file():
                source_abs = cand
        except ValueError:
            pass
    migrated = False
    last_sha = record.get("last_source_sha256")
    if source_abs is None and last_sha and source_vpath:
        found = find_workspace_file_by_sha256(
            user_id, sid, source_vpath.rsplit("/", 1)[-1], last_sha
        )
        if found:
            source_vpath = found
            migrated = True
            try:
                cand, *_ = resolve_virtual_path(user_id, found)
                if cand.is_file():
                    source_abs = cand
            except ValueError:
                pass

    # ── (b) 懒重建：快照缺失 / 源内容变化 / 路径迁移 时重建 ──
    if source_abs is not None and source_abs.is_file():
        sha = hashlib.sha256(source_abs.read_bytes()).hexdigest()
        needs_rebuild = (not snapshot_exists) or migrated or (last_sha != sha)
        if needs_rebuild:
            async with _lock_for(_html_share_rebuild_locks, token):
                # 锁内复检（并发下另一请求可能已重建）
                try:
                    await _build_share_snapshot(
                        user_id=user_id,
                        html_text=source_abs.read_bytes().decode("utf-8", errors="replace"),
                        vpath=source_vpath,
                        sid=sid,
                        token=token,
                        agent_id=agent_id,
                    )
                    await update_share_source(token, source_vpath, sha)
                    snapshot_exists = True
                except Exception as e:
                    # 重建失败降级：继续渲染现有快照（源并发删除等场景）
                    logger.warning(f"[HtmlShare] 快照重建失败 token={token}: {e}")

    # ── (c) 渲染 ──
    html_virtual_path = snapshot_path if snapshot_exists else source_vpath
    if not html_virtual_path:
        raise HTTPException(status_code=404, detail="原文件已被删除")

    # asset 引用 URL 用相对路径 {token}/asset：相对分享页 URL（/.../html-share/{token}）
    # 解析为 /.../html-share/{token}/asset，自动携带任意代理前缀（/agenta、/cmtool/api/agenta 等）
    def asset_url_fn(vpath: str) -> str:
        return f"{record['token']}/asset?path={quote(vpath)}"

    try:
        html_text = inline_html_references(
            user_id=user_id,
            html_virtual_path=html_virtual_path,
            agent_id=agent_id,
            asset_url_fn=asset_url_fn,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="原文件已被删除")
    except Exception as e:
        logger.exception(f"[HtmlShare] 内联渲染失败 token={token}: {e}")
        raise HTTPException(status_code=500, detail="页面渲染失败")

    # 记录访问
    await touch_share_token(token)

    return HTMLResponse(content=html_text, status_code=200)


@router.delete("/html-share/{token}")
async def delete_html_share(
    token: str,
    user_id: str = Query(...),
):
    """删除分享：token 行 + 快照目录（分享管理界面用；归属不符一律 404 不泄漏）。"""
    record = await get_share_token(token)
    if not record or record["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="分享链接不存在")

    sid = record["session_id"]
    await delete_share_token(token, user_id)
    snap_dir = get_workspace_dir(user_id, sid) / SNAPSHOT_DIR_NAME / token
    if snap_dir.exists():
        shutil.rmtree(snap_dir, ignore_errors=True)
    logger.info(f"[HtmlShare] 已删除分享 token={token} user={user_id}")
    return {"ok": True}


# 分享 asset 可返回的 MIME 白名单
# - 图片类（默认排除 SVG：可执行脚本）
# - module 脚本（type="module" 不内联，分享页以外部文件方式引用）
_SHARE_ASSET_MIMES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/avif",
    "image/bmp",
    "image/x-icon",
    "image/vnd.microsoft.icon",
    "text/javascript",
    "application/javascript",
}

# 分享 asset 单文件大小上限
MAX_SHARE_ASSET_BYTES = 20 * 1024 * 1024


@router.get("/html-share/{token}/asset")
async def html_share_asset(
    token: str,
    path: str = Query(..., description="快照内图片虚拟路径"),
):
    """通过分享 token 加载快照图片（免登录，token 即授权）。

    仅允许读取该 token 自己的快照目录（workspace/{sid}/.html-share-assets/{token}/），
    且仅返回白名单图片 MIME —— 授权面最小。
    """
    record = await get_share_token(token)
    if not record:
        raise HTTPException(status_code=404, detail="分享链接不存在或已过期")

    vpath = (path or "").strip().lstrip("/").replace("\\", "/")
    if not vpath:
        raise HTTPException(status_code=400, detail="path 不能为空")

    # 白名单：仅本 token 的快照目录
    prefix = f"workspace/{record['session_id']}/{SNAPSHOT_DIR_NAME}/{record['token']}/"
    if not vpath.startswith(prefix):
        raise HTTPException(status_code=403, detail="无权访问该资源")
    if ".." in vpath.split("/"):
        raise HTTPException(status_code=400, detail="非法路径")

    try:
        abs_path, *_ = resolve_virtual_path(record["user_id"], vpath)
        if abs_path.stat().st_size > MAX_SHARE_ASSET_BYTES:
            raise HTTPException(status_code=413, detail="资源过大")
        data, mime = read_file_bytes(record["user_id"], vpath)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="资源不存在")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if mime not in _SHARE_ASSET_MIMES:
        raise HTTPException(status_code=403, detail="仅支持分享资源类型")

    filename = vpath.rsplit("/", 1)[-1]
    encoded_filename = quote(filename)
    return Response(
        content=data,
        media_type=mime,
        headers={
            "Content-Disposition": (
                f'inline; filename="{encoded_filename}"; '
                f"filename*=UTF-8''{encoded_filename}"
            ),
            "Cache-Control": "private, max-age=300",
        },
    )
