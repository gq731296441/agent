"""Deep Agent 并发控制。

双层 Semaphore：全局 DEEP_GLOBAL_MAX_CONCURRENT + 每用户 DEEP_USER_MAX_CONCURRENT，
防止单用户或总体并发过高压垮 LLM / Docker 资源。
"""

import asyncio
from typing import Dict

from config.config import app_config

# 进程级全局并发上限
_global_sem = asyncio.Semaphore(app_config.DEEP_GLOBAL_MAX_CONCURRENT)
_user_sems: Dict[str, asyncio.Semaphore] = {}


def _user_sem(user_id: str) -> asyncio.Semaphore:
    if user_id not in _user_sems:
        _user_sems[user_id] = asyncio.Semaphore(app_config.DEEP_USER_MAX_CONCURRENT)
    return _user_sems[user_id]


class DeepConcurrencyGuard:
    """全局 + 每用户并发守卫，作为 async context manager 使用。"""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self._global_acquired = False
        self._user_acquired = False

    async def acquire(self) -> None:
        await _global_sem.acquire()
        self._global_acquired = True
        await _user_sem(self.user_id).acquire()
        self._user_acquired = True

    def release(self) -> None:
        if self._user_acquired:
            _user_sem(self.user_id).release()
            self._user_acquired = False
        if self._global_acquired:
            _global_sem.release()
            self._global_acquired = False

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.release()
