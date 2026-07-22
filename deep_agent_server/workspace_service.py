"""工作区文件与配额服务（磁盘为准，DB 索引为辅）。"""

import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from db import deep_storage
from deep_agent_server.quota_service import (
    get_workspace_limit,
    measure_workspace_bytes,
    refresh_workspace_usage,
)
from deep_agent_server.skills.session_mount import MANIFEST_FILENAME
from deep_agent_server.workspace import (
    compute_sha256,
    dir_size_bytes,
    get_artifacts_dir,
    get_skills_dir,
    get_uploads_dir,
    get_user_root,
    get_workspace_dir,
    safe_join,
    validate_user_id,
)

from config import get_logger

logger = get_logger(__name__)


def _mime_for_path(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _is_skill_mirror_path(path: str, rel_path: str = "") -> bool:
    """会话 workspace 内 skill 挂载镜像路径。"""
    rel = (rel_path or "").replace("\\", "/")
    if rel.startswith(".sandbox/skills/") or rel == ".sandbox/skills":
        return True
    vpath = (path or "").replace("\\", "/")
    return "/.sandbox/skills/" in f"/{vpath}/"


def _is_user_hidden_filename(name: str) -> bool:
    """用户不可见、不可通过工作区 API 访问的内部文件。"""
    return name == MANIFEST_FILENAME or name.startswith("._") or name == ".DS_Store"


def _assert_user_visible_path(virtual_path: str) -> None:
    vpath = (virtual_path or "").strip().lstrip("/").replace("\\", "/")
    name = vpath.rsplit("/", 1)[-1]
    if _is_user_hidden_filename(name):
        raise ValueError("该文件不可访问")


def _scan_tree(base: Path, prefix: str, file_kind: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if not base.exists():
        return items
    for root, _dirs, files in os.walk(base):
        root_path = Path(root)
        for name in files:
            if _is_user_hidden_filename(name):
                continue
            fp = root_path / name
            try:
                rel = fp.relative_to(base).as_posix()
                stat = fp.stat()
            except (OSError, ValueError):
                continue
            virtual = f"{prefix}/{rel}" if rel else prefix
            kind = file_kind
            if file_kind == "workspace" and (
                rel.startswith(".sandbox/") or "/.sandbox/" in f"/{rel}/"
            ):
                kind = "sandbox"
            if kind in ("workspace", "sandbox") and _is_skill_mirror_path(virtual, rel):
                continue
            items.append({
                "path": virtual,
                "rel_path": rel,
                "size_bytes": stat.st_size,
                "updated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "file_kind": kind,
                "mime_type": _mime_for_path(fp),
                "deletable": kind not in ("skill", "sandbox"),
            })
    return items


def _session_workspace_sizes(user_root: Path) -> List[Dict[str, Any]]:
    ws_root = user_root / "workspace"
    if not ws_root.exists():
        return []
    sessions = []
    for child in ws_root.iterdir():
        if child.is_dir():
            sessions.append({
                "session_id": child.name,
                "bytes": dir_size_bytes(child),
            })
    sessions.sort(key=lambda x: x["bytes"], reverse=True)
    return sessions


async def get_quota_detail(user_id: str) -> Dict[str, Any]:
    uid = validate_user_id(user_id)
    root = get_user_root(uid)
    await deep_storage.ensure_user_quota(uid)
    used = measure_workspace_bytes(uid)
    limit = get_workspace_limit()
    workspace_total = dir_size_bytes(root / "workspace")
    return {
        "user_id": uid,
        "limit_bytes": limit,
        "used_bytes": used,
        "remaining_bytes": max(0, limit - used),
        "breakdown": {
            "workspace": workspace_total,
            "skills": dir_size_bytes(root / "skills"),
            "uploads": dir_size_bytes(root / "uploads"),
            "artifacts": dir_size_bytes(root / "artifacts"),
        },
        "sessions": _session_workspace_sizes(root),
    }


def list_files(
    user_id: str,
    *,
    session_id: str = None,
    scope: str = "all",
    sort_by: str = "name",
    sort_order: str = "asc",
) -> List[Dict[str, Any]]:
    uid = validate_user_id(user_id)
    root = get_user_root(uid)
    items: List[Dict[str, Any]] = []

    if scope in ("all", "workspace"):
        if session_id:
            base = get_workspace_dir(uid, session_id)
            prefix = f"workspace/{session_id}"
            items.extend(_scan_tree(base, prefix, "workspace"))
        else:
            ws_root = root / "workspace"
            if ws_root.exists():
                for sess_dir in ws_root.iterdir():
                    if sess_dir.is_dir():
                        sid = sess_dir.name
                        prefix = f"workspace/{sid}"
                        items.extend(_scan_tree(sess_dir, prefix, "workspace"))

    if scope == "skills":
        for item in _scan_tree(get_skills_dir(uid), "skills", "skill"):
            item["deletable"] = False
            items.append(item)

    if scope in ("all", "artifacts"):
        if not session_id:
            items.extend(_scan_tree(get_artifacts_dir(uid), "artifacts", "artifact"))

    if scope in ("all", "uploads"):
        if not session_id:
            items.extend(_scan_tree(get_uploads_dir(uid), "uploads", "upload"))

    # 排序
    reverse = sort_order == "desc"
    if sort_by == "size":
        items.sort(key=lambda x: x["size_bytes"], reverse=reverse)
    elif sort_by == "time":
        items.sort(key=lambda x: x["updated_at"], reverse=reverse)
    else:
        items.sort(key=lambda x: x["path"], reverse=reverse)
    return items


def resolve_virtual_path(user_id: str, virtual_path: str) -> Tuple[Path, str, Optional[str], str]:
    """
    解析虚拟路径 workspace/{sid}/file 或 skills/{skill_dir}/...。
    返回 (absolute_path, rel_path_for_index, session_id, file_kind)
    """
    uid = validate_user_id(user_id)
    vpath = (virtual_path or "").strip().lstrip("/").replace("\\", "/")
    if not vpath:
        raise ValueError("path 不能为空")

    parts = vpath.split("/", 2)
    kind = parts[0]
    if kind == "workspace":
        if len(parts) < 3:
            raise ValueError("workspace 路径格式: workspace/{session_id}/{file}")
        sid, rel = parts[1], parts[2]
        base = get_workspace_dir(uid, sid)
        return safe_join(base, rel), rel, sid, "workspace"
    if kind == "skills":
        rel = "/".join(parts[1:]) if len(parts) > 1 else ""
        if not rel:
            raise ValueError("skills 路径格式: skills/{skill_dir}/...")
        base = get_skills_dir(uid)
        return safe_join(base, rel), rel, None, "skill"
    if kind == "artifacts":
        rel = "/".join(parts[1:]) if len(parts) > 1 else ""
        base = get_artifacts_dir(uid)
        return safe_join(base, rel), f"artifacts/{rel}", None, "artifact"
    if kind == "uploads":
        rel = "/".join(parts[1:]) if len(parts) > 1 else ""
        base = get_uploads_dir(uid)
        return safe_join(base, rel), f"uploads/{rel}", None, "upload"
    raise ValueError(f"不支持的 path 前缀: {kind}")


def read_file_bytes(user_id: str, virtual_path: str) -> Tuple[bytes, str]:
    _assert_user_visible_path(virtual_path)
    abs_path, rel_path, session_id, file_kind = resolve_virtual_path(user_id, virtual_path)
    if not abs_path.is_file():
        logger.warning(
            f"[Workspace] read_file_bytes 文件不存在 user={user_id} "
            f"virtual={virtual_path} abs={abs_path} rel={rel_path} "
            f"session={session_id} kind={file_kind}"
        )
        raise FileNotFoundError(virtual_path)
    logger.debug(
        f"[Workspace] read_file_bytes 下载 user={user_id} "
        f"virtual={virtual_path} abs={abs_path} size={abs_path.stat().st_size}"
    )
    return abs_path.read_bytes(), _mime_for_path(abs_path)


async def delete_file(user_id: str, virtual_path: str) -> None:
    _assert_user_visible_path(virtual_path)
    abs_path, rel_path, session_id, file_kind = resolve_virtual_path(user_id, virtual_path)
    vpath = (virtual_path or "").strip().lstrip("/").replace("\\", "/")
    if file_kind == "skill" or vpath.startswith("skills/"):
        raise ValueError("Skill 文件不可在此删除，请前往 Skill 管理卸载")
    if "/.sandbox/skills/" in f"/{vpath}/" or vpath.endswith(".sandbox/skills"):
        raise ValueError("Skill 镜像不可删除，请前往 Skill 管理卸载")
    if abs_path.is_dir():
        raise ValueError("不支持删除目录，请指定文件")
    if not abs_path.exists():
        raise FileNotFoundError(virtual_path)
    abs_path.unlink()
    index_rel = virtual_path.lstrip("/")
    if file_kind == "workspace":
        index_rel = rel_path
    await deep_storage.delete_workspace_file_index(
        user_id, index_rel, file_kind=file_kind, session_id=session_id
    )
    await refresh_workspace_usage(user_id)


async def reconcile_user_workspace(user_id: str) -> Dict[str, Any]:
    """重扫磁盘并刷新配额缓存。"""
    uid = validate_user_id(user_id)
    used = await refresh_workspace_usage(uid)
    detail = await get_quota_detail(uid)
    detail["reconciled_used_bytes"] = used
    return detail


async def index_skill_tree(user_id: str, skill_dir_name: str) -> None:
    """登记 skill 目录下所有文件到索引。"""
    base = get_skills_dir(user_id) / skill_dir_name
    if not base.exists():
        return
    for root, _dirs, files in os.walk(base):
        for name in files:
            fp = Path(root) / name
            rel = fp.relative_to(get_skills_dir(user_id)).as_posix()
            try:
                sha = compute_sha256(fp)
                size = fp.stat().st_size
            except OSError:
                continue
            await deep_storage.upsert_workspace_file_index(
                user_id,
                None,
                rel,
                size,
                sha,
                file_kind="skill",
                mime_type=_mime_for_path(fp),
                storage_path=str(fp),
            )


async def list_conversation_workspace_cards(
    user_id: str,
    session_id: str,
    conversation_id: str,
) -> List[Dict[str, Any]]:
    """按 conversation 返回文件卡片（含磁盘 exists 状态，供历史恢复）。"""
    from deep_agent_server.conversation_files import (
        _MAX_CARD_FILES,
        build_file_entry,
        is_card_eligible_agent_path,
    )

    uid = validate_user_id(user_id)
    rows = await deep_storage.list_workspace_files_by_conversation(
        uid, session_id, conversation_id
    )
    cards: List[Dict[str, Any]] = []
    seen = set()
    for row in rows:
        if len(cards) >= _MAX_CARD_FILES:
            break
        rel = (row.get("rel_path") or "").replace("\\", "/").lstrip("/")
        if not rel or rel in seen:
            continue
        agent_path = f"/workspace/{rel}"
        if not is_card_eligible_agent_path(agent_path):
            continue
        seen.add(rel)
        entry = build_file_entry(
            user_id=uid,
            session_id=session_id,
            conversation_id=conversation_id,
            agent_path=agent_path,
        )
        if entry:
            cards.append(entry)
    return cards


async def list_session_workspace_cards(
    user_id: str,
    session_id: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """按 session 批量返回 { conversation_id: [file_cards] }，替代逐轮调 by-conversation。"""
    from deep_agent_server.conversation_files import (
        build_file_entry,
        is_card_eligible_agent_path,
    )

    uid = validate_user_id(user_id)
    rows = await deep_storage.list_workspace_files_by_session(uid, session_id)
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    seen: Dict[str, set] = {}

    for row in rows:
        conv_id = str(row.get("conversation_id", ""))
        rel = (row.get("rel_path") or "").replace("\\", "/").lstrip("/")
        if not rel:
            continue
        seen.setdefault(conv_id, set())
        if rel in seen[conv_id]:
            continue
        seen[conv_id].add(rel)
        agent_path = f"/workspace/{rel}"
        if not is_card_eligible_agent_path(agent_path):
            continue
        entry = build_file_entry(
            user_id=uid,
            session_id=session_id,
            conversation_id=conv_id,
            agent_path=agent_path,
        )
        if entry:
            grouped.setdefault(conv_id, []).append(entry)
    return grouped


async def list_recent_session_workspace_cards(
    user_id: str,
    session_id: str,
    conversation_id: str,
    *,
    within_seconds: int = 120,
) -> List[Dict[str, Any]]:
    """扫描会话工作区近期写入的文件（end 时 DB/内存均未登记时的兜底）。

    增加 conversation_id 排他过滤：跳过已在 DB 中登记为其他轮次的文件，
    避免上一轮的文件被重复推送。时间窗口从 900s 缩至 120s。
    """
    import time

    from deep_agent_server.conversation_files import (
        _MAX_CARD_FILES,
        build_file_entry,
        is_card_eligible_agent_path,
    )

    uid = validate_user_id(user_id)
    root = get_workspace_dir(uid, session_id)
    if not root.is_dir():
        return []

    # 查询已属于其他轮次的文件，排除跨轮次污染
    stale_paths: set = set()
    if conversation_id:
        try:
            stale_paths = await deep_storage.list_workspace_rel_paths_except_conversation(
                uid, session_id, conversation_id
            )
        except Exception:
            pass

    cutoff = time.time() - max(60, within_seconds)
    cards: List[Dict[str, Any]] = []
    seen = set()
    for path in root.rglob("*"):
        if len(cards) >= _MAX_CARD_FILES:
            break
        if not path.is_file():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                continue
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        if not rel or rel in seen or rel in stale_paths:
            continue
        agent_path = f"/workspace/{rel}"
        if not is_card_eligible_agent_path(agent_path):
            continue
        seen.add(rel)
        entry = build_file_entry(
            user_id=uid,
            session_id=session_id,
            conversation_id=conversation_id,
            agent_path=agent_path,
        )
        if entry:
            cards.append(entry)
    cards.sort(key=lambda item: item.get("name") or "")
    return cards
