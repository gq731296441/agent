"""文件解析状态变更通知（推送到 WebSocket）。

模仿 sandbox/status_notify.py 的 Callback 模式：
  1. main.py 在 lifespan 中调用 set_file_parse_notifier(manager.broadcast_file_parse_status)
  2. upload_service 中调用 notify_file_parse_status(user_id, payload)
  3. WebSocket 推送 { type: "file_parse_status", content: {...} }
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

# payload = { "path": "...", "status": "parsing|completed|failed", ... }
FileParseNotifier = Callable[[str, dict[str, Any]], Awaitable[None]]

_notifier: Optional[FileParseNotifier] = None


def set_file_parse_notifier(fn: Optional[FileParseNotifier]) -> None:
    """由 main.py lifespan 设置/清除 WebSocket 推送回调。"""
    global _notifier
    _notifier = fn


async def notify_file_parse_status(user_id: str, payload: dict[str, Any]) -> None:
    """向该用户所有 WebSocket 连接推送文件解析状态。

    payload 字段：
        path       - 前端虚拟路径 (workspace/{sid}/uploads/filename)
        status     - "parsing" | "completed" | "failed"
        filename   - 文件名
        format     - "text" | "office" | "image" | "wps"
        layer      - 1-4
        chars      - 解析后字符数 (completed 时有)
        truncated  - 是否截断 (completed 时有)
        error      - 错误信息 (failed 时有)
    """
    if not user_id or not _notifier:
        return
    try:
        await _notifier(user_id, payload)
    except Exception:
        pass
