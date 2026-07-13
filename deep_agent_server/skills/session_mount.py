"""Session 级 skill 挂载编排：按勾选 bind mount，不再 copytree。

供沙箱 execute 与 manifest 追踪使用；文件工具 read_file 直读用户级 {user}/skills/。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config import get_logger
from deep_agent_server.skills.selector import (
    SkillSelector,
    effective_selected_skill_ids,
    resolve_selected_pool_dirs,
)
from deep_agent_server.workspace import (
    ensure_session_workspace_permissions,
    get_sandbox_dir,
    get_session_skills_mount_dir,
    sync_skills_to_sandbox_bind_paths,
)

logger = get_logger(__name__)

MANIFEST_FILENAME = ".mount_manifest.json"
LEGACY_MANIFEST_FILENAME = ".sync_manifest.json"

_mount_locks: Dict[str, object] = {}


def compute_selection_key(
    candidate_dirs: List[str],
    selected_skill_ids: Optional[List[str]],
) -> str:
    """与 agent cache 中 selection 语义对齐的 mount 指纹。"""
    pool = resolve_selected_pool_dirs(candidate_dirs, selected_skill_ids)
    if selected_skill_ids is None:
        token = ["*"]
    else:
        token = sorted(str(s).strip() for s in selected_skill_ids if str(s).strip())
    return SkillSelector.match_hash(token) + ":" + SkillSelector.match_hash(sorted(pool))


def resolve_mounted_skill_ids(
    candidate_dirs: List[str],
    selected_skill_ids: Optional[List[str]],
) -> List[str]:
    """解析应 bind mount 的 skill 目录名（物理 id）。"""
    effective = effective_selected_skill_ids(candidate_dirs, selected_skill_ids)
    if effective is None:
        return sorted(
            Path(d).name for d in candidate_dirs if Path(d).is_dir()
        )
    return effective


async def resolve_mount_selected_skill_ids(
    user_id: str,
    session_id: str,
    candidate_dirs: List[str],
    selected_skill_ids: Optional[List[str]],
) -> Optional[List[str]]:
    """合并前端 capabilities 与 session 已挂载配置（resume 未传 skills 时沿用 manifest/DB）。"""
    if selected_skill_ids is not None:
        return effective_selected_skill_ids(candidate_dirs, selected_skill_ids)

    manifest = read_mount_manifest(user_id, session_id)
    if manifest:
        raw = manifest.get("selected_skill_ids")
        if raw is None:
            raw = manifest.get("skill_ids")
        if raw is not None:
            return effective_selected_skill_ids(candidate_dirs, list(raw))

    from db import deep_storage

    row = await deep_storage.get_sandbox_session(user_id, session_id)
    if row:
        raw = row.get("mounted_skill_ids")
        ids: Optional[List[str]] = None
        if isinstance(raw, list):
            ids = [str(x) for x in raw]
        elif isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    ids = [str(x) for x in parsed]
            except json.JSONDecodeError:
                ids = None
        if ids is not None:
            return effective_selected_skill_ids(candidate_dirs, ids)
    return None


def _manifest_path(user_id: str, session_id: str) -> Path:
    return get_sandbox_dir(user_id, session_id) / MANIFEST_FILENAME


def _legacy_manifest_path(user_id: str, session_id: str) -> Path:
    return get_session_skills_mount_dir(user_id, session_id) / MANIFEST_FILENAME


def read_mount_manifest(user_id: str, session_id: str) -> Optional[dict]:
    for path in (_manifest_path(user_id, session_id), _legacy_manifest_path(user_id, session_id)):
        if not path.is_file():
            continue
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return None


def purge_host_sandbox_skills_dir(user_id: str, session_id: str) -> None:
    """移除宿主机 .sandbox/skills/（Docker 嵌套 bind mount 在 Windows 上会生成空挂载点）。"""
    mount_dir = get_session_skills_mount_dir(user_id, session_id)
    if not mount_dir.is_dir():
        return
    legacy_manifest = mount_dir / MANIFEST_FILENAME
    if legacy_manifest.is_file():
        try:
            legacy_manifest.unlink()
        except OSError:
            pass
    shutil.rmtree(mount_dir, ignore_errors=True)
    logger.debug(f"[SkillMount] purged host sandbox skills dir {mount_dir}")


def purge_stale_host_skill_dirs(user_id: str, session_id: str) -> None:
    """向后兼容别名：清理宿主机 .sandbox/skills/ 整棵树。"""
    purge_host_sandbox_skills_dir(user_id, session_id)


def write_mount_manifest(
    user_id: str,
    session_id: str,
    *,
    selection_key: str,
    skill_ids: List[str],
    selected_skill_ids: Optional[List[str]] = None,
) -> None:
    sb = get_sandbox_dir(user_id, session_id)
    sb.mkdir(parents=True, exist_ok=True)
    purge_host_sandbox_skills_dir(user_id, session_id)
    manifest = {
        "selection_key": selection_key,
        "skill_ids": list(skill_ids),
        "selected_skill_ids": None if selected_skill_ids is None else list(selected_skill_ids),
        "mounted_at": datetime.now(timezone.utc).isoformat(),
    }
    _manifest_path(user_id, session_id).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.debug(
        f"[SkillMount] manifest user={user_id} session={session_id} "
        f"skills={skill_ids} key={selection_key[:12]}"
    )


def prepare_mount_placeholder(user_id: str, session_id: str) -> Path:
    """确保 .sandbox 存在；manifest 在 .sandbox/，不在 .sandbox/skills/。"""
    ensure_session_workspace_permissions(user_id, session_id)
    sb = get_sandbox_dir(user_id, session_id)
    sb.mkdir(parents=True, exist_ok=True)
    return sb


@dataclass
class MountResult:
    skipped: bool
    selection_key: str
    mounted_count: int
    recreated: bool = False


async def ensure_session_skill_mount(
    user_id: str,
    session_id: str,
    candidate_dirs: List[str],
    selected_skill_ids: Optional[List[str]] = None,
    *,
    manager=None,
    force_recreate: bool = False,
    agent_id: Optional[str] = None,
) -> MountResult:
    """
    确保 session skill mount 与勾选一致。

    - 勾选变更且 agent/沙箱活跃 → SkillSelectionLockedError
    - 勾选一致且容器 healthy → skip
    - 否则写 manifest + recreate/ensure 容器 bind mount
    """
    from db import deep_storage
    from deep_agent_server.skills.selection_guard import assert_skill_selection_change_allowed
    from deep_agent_server.sandbox import get_sandbox_service

    if selected_skill_ids is not None:
        selected_skill_ids = effective_selected_skill_ids(
            candidate_dirs, selected_skill_ids
        )
    else:
        selected_skill_ids = await resolve_mount_selected_skill_ids(
            user_id, session_id, candidate_dirs, None
        )

    selection_key = compute_selection_key(candidate_dirs, selected_skill_ids)
    skill_ids = resolve_mounted_skill_ids(candidate_dirs, selected_skill_ids)

    await assert_skill_selection_change_allowed(
        user_id,
        session_id,
        selection_key,
        new_skill_ids=skill_ids,
        manager=manager,
    )

    row = await deep_storage.get_sandbox_session(user_id, session_id)
    manifest = read_mount_manifest(user_id, session_id)
    stored_key = (row or {}).get("selection_key") or (manifest or {}).get("selection_key")

    if (
        not force_recreate
        and stored_key == selection_key
        and row
        and row.get("status") == "running"
    ):
        svc = get_sandbox_service()
        if await svc.health_check(user_id, session_id):
            return MountResult(
                skipped=True,
                selection_key=selection_key,
                mounted_count=len(skill_ids),
                recreated=False,
            )

    prepare_mount_placeholder(user_id, session_id)
    write_mount_manifest(
        user_id,
        session_id,
        selection_key=selection_key,
        skill_ids=skill_ids,
        selected_skill_ids=selected_skill_ids,
    )

    skills_synced = sync_skills_to_sandbox_bind_paths(user_id, skill_ids)

    svc = get_sandbox_service()
    recreated = await svc.ensure_with_skills(
        user_id,
        session_id,
        mounted_skill_ids=skill_ids,
        selection_key=selection_key,
        force_recreate=(
            force_recreate
            or skills_synced
            or (stored_key is not None and stored_key != selection_key)
        ),
        agent_id=agent_id,
    )

    return MountResult(
        skipped=False,
        selection_key=selection_key,
        mounted_count=len(skill_ids),
        recreated=recreated,
    )
