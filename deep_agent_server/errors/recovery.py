"""Deep Agent 错误恢复策略。

对 Deep 运行中的异常进行分类（timeout / rate_limit / network 等），
决定是否自动重试及用户可见的错误文案。
"""

from dataclasses import dataclass
from typing import Optional

import asyncio


@dataclass
class DeepErrorClassification:
    """Deep 错误分类结果。"""
    retryable: bool
    category: str
    user_message: str = ""


def classify_deep_error(exc: Exception) -> DeepErrorClassification:
    """将异常映射为可重试/不可重试分类。"""
    msg = str(exc).lower()
    exc_name = type(exc).__name__
    if isinstance(exc, asyncio.TimeoutError) or "timeout" in msg:
        return DeepErrorClassification(True, "timeout", "请求超时，正在重试…")
    if "rate limit" in msg or "429" in msg:
        return DeepErrorClassification(True, "rate_limit", "限流，正在重试…")
    if "context" in msg and "overflow" in msg:
        return DeepErrorClassification(False, "context_overflow", "上下文过长")
    if "connection" in msg or "network" in msg:
        return DeepErrorClassification(True, "network", "网络错误，正在重试…")
    # 认证/鉴权错误（错误的 API Key / Base URL / 模型名）
    if (
        "authentication" in exc_name.lower()
        or "auth" in exc_name.lower()
        or "401" in msg
        or "403" in msg
        or "permission" in msg
        or "unauthorized" in msg
    ):
        return DeepErrorClassification(
            False, "auth_error",
            "模型认证失败，请检查 API Key、Base URL 和模型名称是否正确。"
            "可在「模型设置」中修改后重试。",
        )
    return DeepErrorClassification(False, "unknown", str(exc)[:200])


def should_retry(classification: DeepErrorClassification) -> bool:
    return classification.retryable
