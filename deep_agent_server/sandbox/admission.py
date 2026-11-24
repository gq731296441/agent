"""全站沙箱准入队列 — 替代 LRU 抢占。

名额上限 = SANDBOX_MAX_CONCURRENT。新建/启动容器前申请名额；
释放、TTL 清理、停止时归还并唤醒队首。排队超时默认 10 分钟。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, Optional, Set

from config import get_logger
from config.config import app_config

logger = get_logger(__name__)

QueueNotifier = Callable[[str, str, Optional[int]], Awaitable[None]]
# (user_id, session_id, position|None)  position=None 表示排队结束


@dataclass
class _Waiter:
    user_id: str
    session_id: str
    event: asyncio.Event = field(default_factory=asyncio.Event)
    enqueued_at: float = field(default_factory=time.time)
    position: int = 0
    cancelled: bool = False
    timed_out: bool = False


class SandboxAdmissionTimeout(Exception):
    """沙箱排队超时。"""


class SandboxAdmissionQueue:
    def __init__(
        self,
        max_slots: Optional[int] = None,
        timeout_seconds: float = 600.0,
    ):
        self.max_slots = int(max_slots or app_config.SANDBOX_MAX_CONCURRENT or 25)
        self.timeout_seconds = timeout_seconds
        self._held: Set[str] = set()  # session_id
        self._waiters: list[_Waiter] = []
        self._lock = asyncio.Lock()
        self._notifier: Optional[QueueNotifier] = None

    def set_notifier(self, fn: Optional[QueueNotifier]) -> None:
        self._notifier = fn

    def _key(self, session_id: str) -> str:
        return session_id

    async def _notify(self, user_id: str, session_id: str, position: Optional[int]) -> None:
        if not self._notifier:
            return
        try:
            await self._notifier(user_id, session_id, position)
        except Exception as e:
            logger.debug(f"[SandboxQueue] notify 失败: {e}")

    async def _refresh_positions(self) -> None:
        for i, w in enumerate(self._waiters):
            w.position = i + 1
            await self._notify(w.user_id, w.session_id, w.position)

    async def force_hold(self, user_id: str, session_id: str) -> None:
        """会话已有 running 容器时登记名额（不排队，重启后重建 _held 用）。"""
        key = self._key(session_id)
        async with self._lock:
            self._held.add(key)
            self._waiters = [w for w in self._waiters if w.session_id != session_id]

    async def acquire(self, user_id: str, session_id: str) -> None:
        """申请名额。同 session 已持有则直接返回。超限则 FIFO 排队。"""
        key = self._key(session_id)
        waiter: Optional[_Waiter] = None
        async with self._lock:
            if key in self._held:
                return
            if len(self._held) < self.max_slots:
                self._held.add(key)
                await self._notify(user_id, session_id, None)
                return
            # 已在队列中？
            for w in self._waiters:
                if w.session_id == session_id and not w.cancelled:
                    waiter = w
                    break
            else:
                waiter = _Waiter(user_id=user_id, session_id=session_id)
                self._waiters.append(waiter)
            await self._refresh_positions()

        assert waiter is not None
        try:
            await asyncio.wait_for(waiter.event.wait(), timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            waiter.timed_out = True
            async with self._lock:
                self._waiters = [w for w in self._waiters if w is not waiter]
                await self._refresh_positions()
            raise SandboxAdmissionTimeout(
                f"沙箱资源繁忙，排队超时（{int(self.timeout_seconds)} 秒）"
            )

        if waiter.cancelled:
            raise SandboxAdmissionTimeout("沙箱排队已取消")

        async with self._lock:
            if key not in self._held:
                # 被唤醒但名额被别人拿走（理论上不应发生）
                if len(self._held) < self.max_slots:
                    self._held.add(key)
                else:
                    raise SandboxAdmissionTimeout("沙箱名额获取失败，请重试")
            await self._notify(user_id, session_id, None)

    async def release(self, session_id: str, user_id: str = "") -> None:
        """归还名额并唤醒队首。"""
        key = self._key(session_id)
        async with self._lock:
            self._held.discard(key)
            # 取消同 session 仍在排队的 waiter
            self._waiters = [
                w for w in self._waiters
                if not (w.session_id == session_id and not w.event.is_set())
            ]
            await self._wake_next_locked()

    async def _wake_next_locked(self) -> None:
        while self._waiters and len(self._held) < self.max_slots:
            waiter = self._waiters.pop(0)
            if waiter.cancelled or waiter.timed_out:
                continue
            self._held.add(waiter.session_id)
            waiter.event.set()
            await self._notify(waiter.user_id, waiter.session_id, None)
            break
        await self._refresh_positions()

    def held_count(self) -> int:
        return len(self._held)

    def queue_size(self) -> int:
        return len(self._waiters)


sandbox_admission_queue = SandboxAdmissionQueue()


def set_sandbox_queue_notifier(fn: Optional[QueueNotifier]) -> None:
    sandbox_admission_queue.set_notifier(fn)
