"""上下文溢出守卫中间件。

将 API 级别的 BadRequestError（上下文长度超限）转换为
ContextOverflowError，以便 deepagents 内置的 SummarizationMiddleware
的兜底摘要机制能够捕获并执行紧急压缩重试。

背景：
  deepagents 的 SummarizationMiddleware 在 wrap_model_call 中主动
  估算 token 数并触发摘要，但 count_tokens_approximately 对中文文本
  严重低估（chars/4 估算，实际中文每个字约 1-2 tokens，低估 6-8 倍）。
  当主动摘要未触发而 API 返回 400 context length 错误时，
  SummarizationMiddleware 的兜底只捕获 ContextOverflowError，
  而 DeepSeek 等 API 返回的是 openai.BadRequestError，无法被捕获。

本中间件插入在 SummarizationMiddleware 和模型之间（通过
create_deep_agent 的 middleware 参数），负责将上下文溢出的
BadRequestError 转换为 ContextOverflowError 向上抛出，
使 SummarizationMiddleware 的兜底逻辑正常运转。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# 上下文溢出相关的错误关键词
_CONTEXT_OVERFLOW_KEYWORDS = (
    "maximum context length",
    "context_length_exceeded",
    "too many tokens",
    "context window",
    "token limit",
    "reduce the length",
)


def _is_context_overflow_error(error_message: str) -> bool:
    """判断错误消息是否指示上下文溢出。"""
    lowered = error_message.lower()
    return any(kw in lowered for kw in _CONTEXT_OVERFLOW_KEYWORDS)


class ContextOverflowGuardMiddleware(
    AgentMiddleware[Any, Any, Any]
):
    """将 API 上下文溢出错误转换为 ContextOverflowError 的中间件。

    本中间件不修改请求或响应，仅在模型调用抛出特定错误时进行转换。
    它应该放置在内层（靠近模型），外层的 SummarizationMiddleware
    会捕获转换后的 ContextOverflowError 并执行紧急摘要。
    """

    # 使用默认的 AgentState，不添加额外状态字段
    # state_schema 不可设为 None，否则 create_agent 中 get_type_hints(None) 抛出 TypeError

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """同步包装：捕获上下文溢出错误并转换为 ContextOverflowError。"""
        try:
            return handler(request)
        except Exception as e:
            if _is_context_overflow_error(str(e)):
                self._convert_and_raise(e)
            raise

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """异步包装：捕获上下文溢出错误并转换为 ContextOverflowError。"""
        try:
            return await handler(request)
        except Exception as e:
            if _is_context_overflow_error(str(e)):
                self._convert_and_raise(e)
            raise

    @staticmethod
    def _convert_and_raise(original_error: Exception) -> None:
        """将原始异常转换为 ContextOverflowError 并抛出。"""
        from langchain_core.exceptions import ContextOverflowError

        error_msg = str(original_error)
        logger.warning(
            f"[ContextOverflowGuard] 检测到上下文溢出错误，"
            f"转换为 ContextOverflowError 以触发自动摘要压缩。"
            f"原始错误: {error_msg[:300]}"
        )
        raise ContextOverflowError(
            f"Context overflow detected: {error_msg}"
        ) from original_error
