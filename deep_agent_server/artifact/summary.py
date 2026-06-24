"""Artifact 摘要工具。"""

from config.config import app_config


def truncate_summary(text: str, max_chars: int = None) -> str:
    limit = max_chars or app_config.CONTENT_SUMMARY_MAX_CHARS
    s = text or ""
    if len(s) <= limit:
        return s
    return s[:limit] + "…"
