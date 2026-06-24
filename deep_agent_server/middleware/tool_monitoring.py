"""Deep 内置工具监控 — 结果截断与敏感参数脱敏。

用于日志 / 审计场景，对 tool args 中的 api_key 等敏感字段脱敏，
并对过长 tool result 截断（与 adapter 中的 artifact 存储互补）。
"""

from typing import Any, Dict

from config.config import app_config

_SENSITIVE_KEYS = frozenset(
    {"api_key", "token", "password", "secret", "authorization", "auth"}
)


def sanitize_tool_args(args: Dict[str, Any]) -> Dict[str, Any]:
    """脱敏工具参数中的敏感字段。"""
    if not isinstance(args, dict):
        return {}
    out = {}
    for key, val in args.items():
        if str(key).lower() in _SENSITIVE_KEYS:
            out[key] = "***"
        elif isinstance(val, dict):
            out[key] = sanitize_tool_args(val)
        else:
            out[key] = val
    return out


def truncate_tool_result(text: str, max_chars: int = None) -> tuple[str, bool]:
    """截断过长工具结果，返回 (summary, truncated)。"""
    limit = max_chars or app_config.CONTENT_SUMMARY_MAX_CHARS
    raw = text or ""
    if len(raw) <= limit:
        return raw, False
    return raw[:limit] + "…", True
