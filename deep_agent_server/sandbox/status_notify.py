"""沙箱状态变更通知（推送到 WebSocket，避免 HTTP 轮询）。"""

from __future__ import annotations

from typing import Awaitable, Callable, Optional

StatusNotifier = Callable[[str], Awaitable[None]]

_notifier: Optional[StatusNotifier] = None


def set_sandbox_status_notifier(fn: Optional[StatusNotifier]) -> None:
    global _notifier
    _notifier = fn


async def notify_sandbox_status_changed(user_id: str) -> None:
    if not user_id or not _notifier:
        return
    try:
        await _notifier(user_id)
    except Exception:
        pass
