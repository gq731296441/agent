"""Deep 专用上下文压缩辅助（独立于 Light LoggingSummarizationMiddleware）。

Deep Agent 使用 deepagents 内置上下文管理，本模块提供额外的
压缩阈值判断与摘要前缀格式化，供未来扩展或手动 compaction 使用。
"""

from typing import List

from config.config import app_config


def should_compact_messages(messages: List, token_count: int, max_input_tokens: int) -> bool:
    """判断是否应对历史消息进行摘要压缩。"""
    if not messages or max_input_tokens <= 0:
        return False
    threshold = int(max_input_tokens * 0.75)
    return token_count >= threshold


def build_compaction_notice(summary: str) -> str:
    """生成写入 checkpoint 的压缩摘要前缀。"""
    prefix = "[上下文已压缩]"
    body = (summary or "")[: app_config.CONTENT_SUMMARY_MAX_CHARS]
    return f"{prefix}\n{body}"
