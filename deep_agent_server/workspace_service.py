"""工作区文件与配额服务（磁盘为准，DB 索引为辅）。"""

import asyncio
import mimetypes
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from stat import S_ISDIR
from typing import Any, Dict, List, Optional, Tuple

from db import deep_storage
from deep_agent_server.quota_service import (
    get_workspace_limit,
    refresh_workspace_usage,
)
from deep_agent_server.skills.session_mount import MANIFEST_FILENAME
from deep_agent_server.workspace import (
    HTML_SHARE_SNAPSHOT_DIR,
    compute_sha256,
    get_agent_skills_dir,
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

# 远程沙箱（NFS）跨主机时钟偏差容忍（秒）：execute 在 VM 容器写入的文件
# mtime 用 VM 时钟，与本机 time.time() 可能差数分钟；磁盘兜底窗口下界放宽此值。
# NTP 同步后偏差微秒级，此值只需覆盖 NTP 中断短期漂移；
# 不宜过大——跨轮次排除依赖它小于轮次间隔，否则上一轮文件会被误收为本轮新文件
CLOCK_SKEW_TOLERANCE = 120


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
    for root, dirs, files in os.walk(base):
        # 分享快照目录对用户不可见（不展示、不可删，见 html_share_snapshot 模块注释）
        dirs[:] = [d for d in dirs if d != HTML_SHARE_SNAPSHOT_DIR]
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


def _scan_user_usage(user_root: Path) -> Dict[str, Any]:
    """单次遍历用户根目录，产出总占用 / 顶层目录分布 / 各会话大小。

    原实现分别调用 measure_workspace_bytes + dir_size_bytes×4 +
    _session_workspace_sizes，同一棵树要走上 3~5 遍；工作区在 NFS 上，
    每次 stat 都是网络往返，这里合并为一次遍历。
    口径与 dir_size_bytes 保持一致：跳过 HTML 分享快照目录。
    """
    total = 0
    breakdown: Dict[str, int] = {
        "workspace": 0, "skills": 0, "uploads": 0, "artifacts": 0,
    }
    sessions: Dict[str, int] = {}
    if not user_root.is_dir():
        return {"total": 0, "breakdown": breakdown, "sessions": []}

    for root, dirs, files in os.walk(user_root):
        # 分享快照目录不占用户配额（与 dir_size_bytes 同口径）
        dirs[:] = [d for d in dirs if d != HTML_SHARE_SNAPSHOT_DIR]
        try:
            rel_parts = Path(root).relative_to(user_root).parts
        except ValueError:
            continue
        top = rel_parts[0] if rel_parts else ""
        sid = rel_parts[1] if top == "workspace" and len(rel_parts) >= 2 else ""
        if sid:
            sessions.setdefault(sid, 0)

        for name in files:
            try:
                size = (Path(root) / name).stat().st_size
            except OSError:
                continue
            total += size
            if top in breakdown:
                breakdown[top] += size
            if sid:
                sessions[sid] += size

    return {
        "total": total,
        "breakdown": breakdown,
        "sessions": sorted(
            ({"session_id": k, "bytes": v} for k, v in sessions.items()),
            key=lambda x: x["bytes"],
            reverse=True,
        ),
    }


async def get_quota_detail(user_id: str) -> Dict[str, Any]:
    uid = validate_user_id(user_id)
    root = get_user_root(uid)
    await deep_storage.ensure_user_quota(uid)
    # 磁盘扫描放线程池：NFS 上可能耗时数秒，不得阻塞事件循环
    usage = await asyncio.to_thread(_scan_user_usage, root)
    limit = get_workspace_limit()
    used = usage["total"]
    return {
        "user_id": uid,
        "limit_bytes": limit,
        "used_bytes": used,
        "remaining_bytes": max(0, limit - used),
        "breakdown": usage["breakdown"],
        "sessions": usage["sessions"],
    }


def _scan_tree_nodes(
    base: Path,
    prefix: str,
    file_kind: str,
    sort_by: str = "name",
    sort_order: str = "asc",
) -> List[Dict[str, Any]]:
    """递归扫描目录树，返回文件 + 文件夹混合节点列表（树状结构）。

    每层节点先按类型分组（文件夹在前），再按 sort_by 排序。
    性能注意：工作区在 NFS 上，stat 是网络往返——每个条目只 stat 一次，
    目录/文件判定复用 stat 结果，不要再用 Path.is_dir()/stat() 重复探测。
    """
    if not base.is_dir():
        return []
    nodes: List[Dict[str, Any]] = []
    try:
        # 预排序仅按名称（无文件系统调用）：为末级排序提供稳定的并列次序，
        # 与历史实现输出保持一致；不要在预排序里调用 is_dir()/stat()
        entries = sorted(base.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return nodes

    for entry in entries:
        if _is_user_hidden_filename(entry.name):
            continue
        # 分享快照目录对用户不可见（不展示、不可删）
        if entry.name == HTML_SHARE_SNAPSHOT_DIR:
            continue
        try:
            entry_stat = entry.stat()
        except OSError:
            continue
        is_dir = S_ISDIR(entry_stat.st_mode)

        rel = entry.name
        virtual = f"{prefix}/{rel}" if rel else prefix

        if is_dir:
            kind = file_kind
            if file_kind == "workspace" and (
                rel.startswith(".sandbox/") or "/.sandbox/" in f"/{rel}/"
            ):
                kind = "sandbox"
            if kind in ("workspace", "sandbox") and _is_skill_mirror_path(virtual, rel):
                continue
            children = _scan_tree_nodes(entry, virtual, file_kind, sort_by, sort_order)
            # 递归汇总文件夹统计
            total_size = sum(c["size_bytes"] for c in children)
            file_count = sum(1 for c in children if c["type"] == "file")
            folder_count = sum(1 for c in children if c["type"] == "folder")
            # mtime 取最新子节点（含自身目录 mtime）
            child_times = [c["updated_at"] for c in children]
            child_times.append(
                datetime.fromtimestamp(entry_stat.st_mtime, tz=timezone.utc).isoformat()
            )
            latest_time = max(child_times)
            nodes.append({
                "name": entry.name,
                "type": "folder",
                "path": virtual,
                "size_bytes": total_size,
                "updated_at": latest_time,
                "file_count": file_count,
                "folder_count": folder_count,
                "children": children,
                "deletable": kind not in ("skill", "sandbox"),
            })
        else:
            kind = file_kind
            if file_kind == "workspace" and (
                rel.startswith(".sandbox/") or "/.sandbox/" in f"/{rel}/"
            ):
                kind = "sandbox"
            if kind in ("workspace", "sandbox") and _is_skill_mirror_path(virtual, rel):
                continue
            nodes.append({
                "name": entry.name,
                "type": "file",
                "path": virtual,
                "size_bytes": entry_stat.st_size,
                "updated_at": datetime.fromtimestamp(
                    entry_stat.st_mtime, tz=timezone.utc
                ).isoformat(),
                "mime_type": _mime_for_path(entry),
                "file_kind": kind,
                "deletable": kind not in ("skill", "sandbox"),
            })

    # 每层节点排序：文件夹始终在前，再按 sort_by 排序
    reverse = sort_order == "desc"
    if sort_by == "size":
        nodes.sort(
            key=lambda n: (n["type"] != "folder", n["size_bytes"]),
            reverse=reverse,
        )
    elif sort_by == "time":
        nodes.sort(
            key=lambda n: (n["type"] != "folder", n["updated_at"]),
            reverse=reverse,
        )
    else:  # name
        nodes.sort(
            key=lambda n: (n["type"] != "folder", n["name"].lower()),
            reverse=reverse,
        )
    return nodes


def list_files_tree(
    user_id: str,
    *,
    session_id: str = None,
    scope: str = "all",
    sort_by: str = "name",
    sort_order: str = "asc",
) -> List[Dict[str, Any]]:
    """树状结构列出工作区文件（含文件夹节点，递归嵌套 children）。"""
    uid = validate_user_id(user_id)
    root = get_user_root(uid)
    roots: List[Dict[str, Any]] = []

    if scope in ("all", "workspace"):
        if session_id:
            base = get_workspace_dir(uid, session_id)
            prefix = f"workspace/{session_id}"
            children = _scan_tree_nodes(base, prefix, "workspace", sort_by, sort_order)
            total_size = sum(c["size_bytes"] for c in children)
            file_count = sum(1 for c in children if c["type"] == "file")
            folder_count = sum(1 for c in children if c["type"] == "folder")
            # 根节点 updated_at 取最新子节点
            root_time = (
                max(c["updated_at"] for c in children)
                if children
                else datetime.fromtimestamp(0, tz=timezone.utc).isoformat()
            )
            roots.append({
                "name": session_id,
                "type": "folder",
                "path": prefix,
                "size_bytes": total_size,
                "updated_at": root_time,
                "file_count": file_count,
                "folder_count": folder_count,
                "children": children,
                "deletable": False,
            })
        else:
            ws_root = root / "workspace"
            if ws_root.exists():
                try:
                    sess_dirs = sorted(
                        [d for d in ws_root.iterdir() if d.is_dir()],
                        key=lambda d: d.name.lower(),
                    )
                except OSError:
                    sess_dirs = []
                for sess_dir in sess_dirs:
                    sid = sess_dir.name
                    prefix = f"workspace/{sid}"
                    children = _scan_tree_nodes(
                        sess_dir, prefix, "workspace", sort_by, sort_order
                    )
                    total_size = sum(c["size_bytes"] for c in children)
                    file_count = sum(1 for c in children if c["type"] == "file")
                    folder_count = sum(1 for c in children if c["type"] == "folder")
                    root_time = (
                        max(c["updated_at"] for c in children)
                        if children
                        else datetime.fromtimestamp(0, tz=timezone.utc).isoformat()
                    )
                    roots.append({
                        "name": sid,
                        "type": "folder",
                        "path": prefix,
                        "size_bytes": total_size,
                        "updated_at": root_time,
                        "file_count": file_count,
                        "folder_count": folder_count,
                        "children": children,
                    })

    if scope == "skills":
        roots.extend(
            _scan_tree_nodes(get_skills_dir(uid), "skills", "skill", sort_by, sort_order)
        )

    if scope in ("all", "artifacts"):
        if not session_id:
            roots.extend(
                _scan_tree_nodes(
                    get_artifacts_dir(uid), "artifacts", "artifact", sort_by, sort_order
                )
            )

    if scope in ("all", "uploads"):
        if not session_id:
            roots.extend(
                _scan_tree_nodes(
                    get_uploads_dir(uid), "uploads", "upload", sort_by, sort_order
                )
            )

    return roots


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


def resolve_virtual_path(
    user_id: str,
    virtual_path: str,
    *,
    agent_id: Optional[str] = None,
) -> Tuple[Path, str, Optional[str], str]:
    """
    解析虚拟路径 workspace/{sid}/file 或 skills/{skill_dir}/...。
    返回 (absolute_path, rel_path_for_index, session_id, file_kind)

    agent_id: 订阅智能体会话上下文。skills/ 前缀且 agent_id 非空时解析到
              .agents/{agent_id}/skills/（订阅智能体技能目录），否则解析到用户目录。
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
        base = get_agent_skills_dir(agent_id) if agent_id else get_skills_dir(uid)
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


def find_workspace_file_by_sha256(
    user_id: str,
    session_id: str,
    file_name: str,
    sha256: str,
) -> Optional[str]:
    """在会话工作区树内按内容 sha256 定位文件（分享源路径迁移用）。

    先按 basename 快速匹配；未命中时全树 sha 兜底（改名后 basename 变化场景）。
    跳过隐藏目录（.html-share-assets / .sandbox）；多个命中取 mtime 最新。
    返回虚拟路径 workspace/{sid}/...，找不到返回 None。
    """
    uid = validate_user_id(user_id)
    root = get_workspace_dir(uid, session_id)
    if not root.is_dir():
        return None

    def _match(path: Path) -> bool:
        rel = path.relative_to(root).as_posix()
        if rel == HTML_SHARE_SNAPSHOT_DIR or rel.startswith(f"{HTML_SHARE_SNAPSHOT_DIR}/"):
            return False
        if rel.startswith(".sandbox/") or "/.sandbox/" in f"/{rel}/":
            return False
        try:
            return compute_sha256(path) == sha256
        except OSError:
            return False

    best: Optional[Tuple[float, str]] = None

    def _consider(path: Path) -> None:
        nonlocal best
        if not _match(path):
            return
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return
        rel = path.relative_to(root).as_posix()
        if best is None or mtime > best[0]:
            best = (mtime, f"workspace/{session_id}/{rel}")

    # ① 按 basename 快速匹配（常规改名/移动）
    for path in root.rglob(file_name):
        if path.is_file():
            _consider(path)
    # ② 全树 sha 兜底（改名后 basename 变化；源缺失时低频触发）
    if best is None:
        for path in root.rglob("*"):
            if path.is_file():
                _consider(path)
    return best[1] if best else None


def count_workspace_basename(user_id: str, session_id: str, file_name: str) -> int:
    """统计会话工作区内同名文件数量（不含隐藏目录），分享复用③唯一性判定用。"""
    uid = validate_user_id(user_id)
    root = get_workspace_dir(uid, session_id)
    if not root.is_dir():
        return 0
    count = 0
    for path in root.rglob(file_name):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel == HTML_SHARE_SNAPSHOT_DIR or rel.startswith(f"{HTML_SHARE_SNAPSHOT_DIR}/"):
            continue
        if rel.startswith(".sandbox/") or "/.sandbox/" in f"/{rel}/":
            continue
        count += 1
    return count


def read_file_bytes(
    user_id: str,
    virtual_path: str,
    *,
    agent_id: Optional[str] = None,
) -> Tuple[bytes, str]:
    _assert_user_visible_path(virtual_path)
    abs_path, rel_path, session_id, file_kind = resolve_virtual_path(
        user_id, virtual_path, agent_id=agent_id
    )
    # 回退链：agent 目录缺失时回退用户目录（覆盖内置会话拿到 agent id、
    # 订阅会话引用用户自有技能等边缘场景）；仍不存在才报 404
    if not abs_path.is_file() and agent_id and file_kind == "skill":
        abs_path, rel_path, session_id, file_kind = resolve_virtual_path(
            user_id, virtual_path, agent_id=None
        )
    if not abs_path.is_file():
        logger.warning(
            f"[Workspace] read_file_bytes 文件不存在 user={user_id} "
            f"virtual={virtual_path} abs={abs_path} rel={rel_path} "
            f"session={session_id} kind={file_kind} agent_id={agent_id}"
        )
        raise FileNotFoundError(virtual_path)
    logger.debug(
        f"[Workspace] read_file_bytes 下载 user={user_id} "
        f"virtual={virtual_path} abs={abs_path} size={abs_path.stat().st_size}"
    )
    return abs_path.read_bytes(), _mime_for_path(abs_path)


async def delete_file(user_id: str, virtual_path: str, *, recursive: bool = False) -> None:
    """删除工作区文件，或递归删除目录（需传 recursive=true）。

    递归删除目录时会先遍历收集所有子文件，逐一清理 DB 索引，再删除整个目录树。
    """
    _assert_user_visible_path(virtual_path)
    abs_path, rel_path, session_id, file_kind = resolve_virtual_path(user_id, virtual_path)
    vpath = (virtual_path or "").strip().lstrip("/").replace("\\", "/")
    if file_kind == "skill" or vpath.startswith("skills/"):
        raise ValueError("Skill 文件不可在此删除，请前往 Skill 管理卸载")
    if "/.sandbox/skills/" in f"/{vpath}/" or vpath.endswith(".sandbox/skills"):
        raise ValueError("Skill 镜像不可删除，请前往 Skill 管理卸载")
    if f"/{HTML_SHARE_SNAPSHOT_DIR}/" in f"/{vpath}/" or vpath.startswith(HTML_SHARE_SNAPSHOT_DIR):
        raise ValueError("分享快照文件不可在此删除，请前往分享管理操作")
    if not abs_path.exists():
        raise FileNotFoundError(virtual_path)

    if abs_path.is_dir():
        if not recursive:
            raise ValueError("不支持删除目录，请传 recursive=true 进行递归删除")
        # 先清理子文件 DB 索引
        index_prefix = (rel_path or "").rstrip("/") + "/"
        for root, _dirs, files in os.walk(abs_path):
            for name in files:
                fp = Path(root) / name
                try:
                    child_rel = fp.relative_to(get_workspace_dir(user_id, session_id) if session_id else get_user_root(user_id)).as_posix()
                except (OSError, ValueError):
                    continue
                child_index_rel = child_rel
                if file_kind != "workspace":
                    child_index_rel = str(fp.relative_to(abs_path.parent).as_posix())
                await deep_storage.delete_workspace_file_index(
                    user_id, child_index_rel,
                    file_kind=file_kind, session_id=session_id,
                )
        shutil.rmtree(abs_path)
        logger.info(
            f"[Workspace] 已递归删除目录 user={user_id} "
            f"virtual={virtual_path} abs={abs_path}"
        )
    else:
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
    time_window: Optional[Tuple[Optional[float], Optional[float]]] = None,
) -> List[Dict[str, Any]]:
    """扫描会话工作区在时间窗口内写入的文件（end 时 DB/内存均未登记时的兜底）。

    time_window=(start_ts, end_ts)：扫描 mtime ∈ [start, end] 的文件
    （run 生命周期窗口——run 开始到结束，覆盖正常完成/出错/用户 stop 全形态，
    不受固定秒数窗口限制；end 之后写入的文件属于后续 run，排除）。
    未传 time_window 时用 within_seconds 向后兼容（120s）。
    增加 conversation_id 排他过滤：跳过已在 DB 中登记为其他轮次的文件，
    避免上一轮的文件被重复推送。
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

    if time_window is not None:
        start_ts, end_ts = time_window
        end_ts = end_ts or time.time()
        # 远程沙箱（NFS）跨主机时钟偏差：execute 在 VM 写入的 mtime 可能比
        # 本机早数分钟，下界放宽容忍；mtime 偏早不会越过上界，上界保持不动
        start_ts = (start_ts - CLOCK_SKEW_TOLERANCE) if start_ts else 0.0
    else:
        start_ts = time.time() - max(60, within_seconds)
        end_ts = time.time()
    cards: List[Dict[str, Any]] = []
    seen = set()
    for path in root.rglob("*"):
        if len(cards) >= _MAX_CARD_FILES:
            break
        if not path.is_file():
            continue
        try:
            mtime = path.stat().st_mtime
            if mtime < start_ts or mtime > end_ts:
                continue
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        if not rel or rel in seen or rel in stale_paths:
            continue
        # 分享快照目录对用户不可见（rglob 会拾取快照文件，需显式过滤）
        if rel == HTML_SHARE_SNAPSHOT_DIR or rel.startswith(f"{HTML_SHARE_SNAPSHOT_DIR}/"):
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
