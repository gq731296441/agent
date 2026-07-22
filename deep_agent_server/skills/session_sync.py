"""Session 级 skill 镜像：将 per-ask 选中的 skill 同步到 workspace/.sandbox/skills/。

供沙箱 execute 与沙箱模式下的 /skills/ 文件路由使用，与 capabilities.skills 一致。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config import get_logger
from deep_agent_server.skills.selector import (
    filter_skill_dirs_by_ids,
    resolve_selected_pool_dirs,
)
from deep_agent_server.workspace import get_session_skills_mirror_dir

logger = get_logger(__name__)

MANIFEST_FILENAME = ".sync_manifest.json"

_sync_locks: Dict[str, asyncio.Lock] = {}
_sync_locks_guard = asyncio.Lock()
_last_sync_key: Dict[Tuple[str, str], str] = {}


async def _get_sync_lock(user_id: str, session_id: str) -> asyncio.Lock:
    key = f"{user_id}:{session_id}"
    async with _sync_locks_guard:
        if key not in _sync_locks:
            _sync_locks[key] = asyncio.Lock()
        return _sync_locks[key]


def compute_selection_key(
    candidate_dirs: List[str],
    selected_skill_ids: Optional[List[str]],
) -> str:
    """与 agent cache 中 selection 语义对齐的 sync 指纹。"""
    pool = resolve_selected_pool_dirs(candidate_dirs, selected_skill_ids)
    if selected_skill_ids is None:
        token = ["*"]
    else:
        token = sorted(str(s).strip() for s in selected_skill_ids if str(s).strip())
    return (
        hashlib.md5(json.dumps(token).encode()).hexdigest()[:12] + ":"
        + hashlib.md5(json.dumps(sorted(pool)).encode()).hexdigest()[:12]
    )


def _manifest_path(mirror_dir: Path) -> Path:
    return mirror_dir / MANIFEST_FILENAME


def _read_manifest(mirror_dir: Path) -> Optional[dict]:
    path = _manifest_path(mirror_dir)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _sync_on_disk(
    mirror_dir: Path,
    source_dirs: List[str],
    selection_key: str,
    selected_skill_ids: Optional[List[str]],
) -> int:
    """清理 mirror 并 copytree 选中 skill；返回成功拷贝的目录数。"""
    mirror_dir.mkdir(parents=True, exist_ok=True)

    for child in mirror_dir.iterdir():
        if child.name == MANIFEST_FILENAME:
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            try:
                child.unlink()
            except OSError:
                pass

    copied = 0
    for src in source_dirs:
        src_path = Path(src)
        if not src_path.is_dir():
            logger.warning(f"[SkillSync] 源目录不存在，跳过: {src}")
            continue
        dest = mirror_dir / src_path.name
        shutil.copytree(src_path, dest, dirs_exist_ok=True)
        copied += 1

    manifest = {
        "selection_key": selection_key,
        "skill_ids": None if selected_skill_ids is None else list(selected_skill_ids),
        "synced_skill_dirs": sorted(Path(d).name for d in source_dirs),
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }
    _manifest_path(mirror_dir).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return copied


@dataclass
class SyncResult:
    skipped: bool
    selection_key: str
    copied_count: int


async def sync_session_skills(
    user_id: str,
    session_id: str,
    candidate_dirs: List[str],
    selected_skill_ids: Optional[List[str]] = None,
    *,
    force: bool = False,
) -> SyncResult:
    """
    将选中的 skill 同步到 workspace/{session_id}/.sandbox/skills/。

    - selected_skill_ids is None：拷贝 candidate_dirs 全部
    - selected_skill_ids == []：只清空 mirror
    - 相同 selection_key 且 manifest 命中时 skip（除非 force=True）
    """
    selection_key = compute_selection_key(candidate_dirs, selected_skill_ids)
    cache_key = (user_id, session_id)
    mirror_dir = get_session_skills_mirror_dir(user_id, session_id)

    if not force:
        if _last_sync_key.get(cache_key) == selection_key:
            manifest = _read_manifest(mirror_dir)
            if manifest and manifest.get("selection_key") == selection_key:
                return SyncResult(skipped=True, selection_key=selection_key, copied_count=0)

    lock = await _get_sync_lock(user_id, session_id)
    async with lock:
        if not force:
            if _last_sync_key.get(cache_key) == selection_key:
                manifest = _read_manifest(mirror_dir)
                if manifest and manifest.get("selection_key") == selection_key:
                    return SyncResult(skipped=True, selection_key=selection_key, copied_count=0)

        if selected_skill_ids is None:
            source_dirs = list(candidate_dirs)
        elif not selected_skill_ids:
            source_dirs = []
        else:
            source_dirs = filter_skill_dirs_by_ids(candidate_dirs, selected_skill_ids)

        copied = await asyncio.to_thread(
            _sync_on_disk,
            mirror_dir,
            source_dirs,
            selection_key,
            selected_skill_ids,
        )
        _last_sync_key[cache_key] = selection_key
        logger.info(
            f"[SkillSync] user={user_id} session={session_id} "
            f"key={selection_key[:12]} copied={copied} skipped=False"
        )
        return SyncResult(skipped=False, selection_key=selection_key, copied_count=copied)


def clear_sync_cache(user_id: str, session_id: str) -> None:
    """测试或 session 删除时清除内存 sync 缓存。"""
    _last_sync_key.pop((user_id, session_id), None)
