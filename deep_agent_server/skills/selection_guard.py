"""Skill 勾选变更锁定：agent 运行中或沙箱 running/paused 时禁止变更。"""

import asyncio
from typing import Any, List, Optional

from db import deep_storage
from deep_agent_server.skills.session_mount import read_mount_manifest


class SkillSelectionLockedError(Exception):
    """Skill 勾选变更被锁定。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def to_error_payload(self) -> dict:
        return {
            "code": "SKILL_SELECTION_LOCKED",
            "reason": self.code,
            "msg": self.message,
        }


def _session_agent_active(session_id: str, manager: Any) -> bool:
    """是否存在「其他」正在执行的 Agent Run（不含当前协程自身）。"""
    if not manager or not session_id:
        return False
    active = getattr(manager, "_active_tasks", None)
    if not active:
        return False
    task = active.get(session_id)
    if task is None or task.done():
        return False
    current = asyncio.current_task()
    if current is not None and task is current:
        # 新 ask 在 create_agent 前已 register，此时应允许变更 skill mount
        return False
    return True


async def _effective_selection_key(user_id: str, session_id: str) -> Optional[str]:
    row = await deep_storage.get_sandbox_session(user_id, session_id)
    if row and row.get("selection_key"):
        return str(row["selection_key"])
    manifest = read_mount_manifest(user_id, session_id)
    if manifest:
        return manifest.get("selection_key")
    return None


async def _effective_mounted_skill_ids(
    user_id: str, session_id: str
) -> Optional[List[str]]:
    row = await deep_storage.get_sandbox_session(user_id, session_id)
    if row:
        raw = row.get("mounted_skill_ids")
        if isinstance(raw, list) and raw:
            return [str(x).strip() for x in raw if str(x).strip()]
        if isinstance(raw, str):
            import json

            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list) and parsed:
                    return [str(x).strip() for x in parsed if str(x).strip()]
            except json.JSONDecodeError:
                pass
    manifest = read_mount_manifest(user_id, session_id)
    if manifest:
        raw = manifest.get("skill_ids") or manifest.get("selected_skill_ids")
        if isinstance(raw, list) and raw:
            return [str(x).strip() for x in raw if str(x).strip()]
    return None


async def assert_skill_selection_change_allowed(
    user_id: str,
    session_id: str,
    new_selection_key: str,
    *,
    new_skill_ids: Optional[List[str]] = None,
    manager: Any = None,
) -> None:
    """
    若 selection_key 相对已生效配置发生变化，且 agent/沙箱活跃，则拒绝。

    selection_key 未变、首次配置、或 mounted skill id 集合未变时不抛错。
    """
    old_key = await _effective_selection_key(user_id, session_id)
    if old_key is None or old_key == new_selection_key:
        return

    if new_skill_ids is not None:
        old_ids = await _effective_mounted_skill_ids(user_id, session_id)
        if old_ids is not None and sorted(old_ids) == sorted(new_skill_ids):
            return

    if _session_agent_active(session_id, manager):
        raise SkillSelectionLockedError(
            "AGENT_RUNNING",
            "Agent 正在执行任务，无法变更 Skill 勾选。请先停止当前对话。",
        )

    row = await deep_storage.get_sandbox_session(user_id, session_id)
    if row and row.get("status") in ("running", "paused"):
        raise SkillSelectionLockedError(
            "SANDBOX_ACTIVE",
            "沙箱容器运行中或已暂停，无法变更 Skill 勾选。请等待 Run 结束或释放沙箱。",
        )


async def validate_skill_selection_for_ask(
    user_id: str,
    session_id: str,
    candidate_dirs: list,
    selected_skill_ids: Optional[list],
    *,
    manager: Any = None,
) -> Optional[dict]:
    """ask 前校验；若锁定返回 error payload，否则 None。

    注：全仓库当前无调用者（保留作为兼容入口）。生产路径由
    `ensure_session_skill_mount` 内部的 `assert_skill_selection_change_allowed`
    完成。
    """
    from deep_agent_server.skills.session_mount import compute_selection_key

    try:
        key = compute_selection_key(candidate_dirs, selected_skill_ids)
        await assert_skill_selection_change_allowed(
            user_id, session_id, key, manager=manager
        )
    except SkillSelectionLockedError as exc:
        return exc.to_error_payload()
    return None
