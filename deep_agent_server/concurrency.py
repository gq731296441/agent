"""Deep Agent 并发控制。

双层限流：全局 DEEP_GLOBAL_MAX_CONCURRENT + 每用户 DEEP_USER_MAX_CONCURRENT。
用户侧超限立即拒绝（不等待），同会话续跑不额外占名额。
"""

from __future__ import annotations

import asyncio
from typing import Dict, Set

from config.config import app_config


class UserRunLimitError(Exception):
    """用户并发执行已达上限。"""

    code = "USER_RUN_LIMIT"

    def __init__(self, user_id: str, limit: int):
        self.user_id = user_id
        self.limit = limit
        super().__init__(
            f"用户并发执行已达上限（最多 {limit} 个会话同时执行）"
        )


# 进程级全局并发上限
_global_sem = asyncio.Semaphore(app_config.DEEP_GLOBAL_MAX_CONCURRENT)
# 每用户当前占用的 session 集合
_user_active_sessions: Dict[str, Set[str]] = {}
_user_lock = asyncio.Lock()


def get_user_active_session_ids(user_id: str) -> Set[str]:
    return set(_user_active_sessions.get(user_id) or set())


class DeepConcurrencyGuard:
    """全局 + 每用户并发守卫，作为 async context manager 使用。"""

    def __init__(self, user_id: str, session_id: str | None = None):
        self.user_id = user_id
        self.session_id = session_id or ""
        self._global_acquired = False
        self._user_slot_held = False

    async def acquire(self) -> None:
        limit = int(app_config.DEEP_USER_MAX_CONCURRENT or 2)
        async with _user_lock:
            active = _user_active_sessions.setdefault(self.user_id, set())
            already = bool(self.session_id) and self.session_id in active
            if already:
                # 同会话已占用名额（续跑）：不重复占用户/全局槽
                return
            if len(active) >= limit:
                raise UserRunLimitError(self.user_id, limit)
            if self.session_id:
                active.add(self.session_id)
                self._user_slot_held = True

        await _global_sem.acquire()
        self._global_acquired = True

    def release(self) -> None:
        if self._user_slot_held:
            active = _user_active_sessions.get(self.user_id)
            if active is not None and self.session_id:
                active.discard(self.session_id)
                if not active:
                    _user_active_sessions.pop(self.user_id, None)
            self._user_slot_held = False
        if self._global_acquired:
            _global_sem.release()
            self._global_acquired = False

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.release()
