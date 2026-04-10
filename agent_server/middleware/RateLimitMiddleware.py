"""速率限制与可重试错误恢复中间件 (RateLimitMiddleware)

在 LLM 调用层面提供三重保护：
1. 主动限流：调用前通过全局速率限制器排队，避免触发 429
2. 被动重试（429）：捕获 RateLimitError 后长时间退避重试，等待 API 配额恢复
3. 被动重试（5xx）：捕获服务端错误后短退避重试，处理瞬时故障

设计要点：
- 使用 awrap_model_call 钩子包装模型调用
- 放在中间件链的最后位置（最靠近模型调用），重试时仅重试模型本身，
  不会重复执行上游中间件（NodeHooksMiddleWare 的 system prompt 注入等）的逻辑
- 通过 get_stream_writer 向前端实时通知限流/重试状态

重试策略：
  OpenAI 客户端内置 max_retries=5（指数退避 ~13s）
  ↓ 若内置重试仍失败（持续 429 或 5xx）
  本中间件捕获 → 差异化退避重试（429 长退避 / 5xx 短退避）
  ↓ 若中间件重试也耗尽
  异常向上抛出 → agents.py 的 except 块执行错误分类与恢复
"""

import asyncio
import random
from typing import Callable, Awaitable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langgraph.config import get_stream_writer

from config import get_logger
from utils.rate_limiter import llm_rate_limiter, is_rate_limit_error, LLMRateLimiter

logger = get_logger(__name__)


def _is_server_error(exc: Exception) -> bool:
    """判断异常是否为服务端错误（5xx）。

    递归检查异常链以处理被 LangChain/LangGraph 包装的异常。
    """
    # 检查 status_code 属性
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and 500 <= status < 600:
        return True

    # 检查嵌套的 response 对象
    resp = getattr(exc, "response", None)
    if resp is not None:
        sc = getattr(resp, "status_code", None)
        if isinstance(sc, int) and 500 <= sc < 600:
            return True

    # 字符串兜底
    err_str = str(exc).lower()
    if "internal server error" in err_str:
        return True

    # 递归检查 cause
    cause = getattr(exc, "__cause__", None)
    if cause is not None and cause is not exc:
        return _is_server_error(cause)

    return False


def _is_retryable_error(exc: Exception) -> bool:
    """判断异常是否为中间件层面可重试的错误（429 或 5xx）。"""
    return is_rate_limit_error(exc) or _is_server_error(exc)


class RateLimitMiddleware(AgentMiddleware):
    """LLM 调用速率限制 + 可重试错误自动恢复中间件。

    放在中间件链最后位置，通过 awrap_model_call 包装模型调用：
      ① 调用前 → rate_limiter.acquire() 排队等待配额（主动限流）
      ② 调用模型
      ③ 若 429/5xx → 退避等待 → 重试步骤①②（被动重试）

    与 OpenAI 客户端内置重试的协作：
      OpenAI 内置重试处理短暂的速率波动（快速退避 0.4~6s）
      本中间件处理持续性速率超限（长退避 30~120s，让配额窗口恢复）

    Args:
        rate_limiter: LLMRateLimiter 实例，默认使用全局单例
        max_retries: 可重试错误最大重试次数（不含首次尝试）
        base_wait_seconds: 首次重试的基础等待时间（秒），后续按 1.5x 指数增长
        max_wait_seconds: 单次等待时间上限（秒）
    """

    def __init__(
        self,
        rate_limiter: LLMRateLimiter | None = None,
        max_retries: int = 3,
        base_wait_seconds: float = 30.0,
        max_wait_seconds: float = 120.0,
    ):
        super().__init__()
        self.rate_limiter = rate_limiter or llm_rate_limiter
        self.max_retries = max_retries
        self.base_wait = base_wait_seconds
        self.max_wait = max_wait_seconds

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """包装模型调用，添加速率限制和可重试错误恢复。

        Args:
            request: 模型请求（已经过上游中间件处理，如 system prompt 注入）
            handler: 调用链的下一步（直接连接到模型调用）

        Returns:
            模型响应

        Raises:
            原始异常: 非可重试错误直接抛出
            最后一次异常: 重试次数耗尽后抛出
        """
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                # ① 主动限流：等待速率许可
                waited = await self.rate_limiter.acquire()
                if waited > 0.5:
                    logger.info(
                        f"🚦 [RateLimitMW] 速率限制排队 {waited:.1f}s 后继续"
                    )
                    self._notify_frontend(
                        f"API 调用排队中..."
                    )

                # ② 调用模型（通过剩余中间件链 → 实际 LLM API）
                return await handler(request)

            except Exception as e:
                if not _is_retryable_error(e):
                    raise  # 非可重试错误（如 400），直接抛出给 agents.py 处理

                last_error = e
                error_type = "429" if is_rate_limit_error(e) else "5xx"

                if attempt >= self.max_retries:
                    logger.error(
                        f"🚦 [RateLimitMW] LLM API {error_type} 错误，"
                        f"已重试 {self.max_retries} 次仍失败，放弃重试"
                    )
                    raise

                # ③ 根据错误类型差异化退避
                if is_rate_limit_error(e):
                    # 429: 长退避（30s → 45s → 67s），等待配额窗口恢复
                    wait = min(
                        self.base_wait * (1.5 ** attempt),
                        self.max_wait,
                    )
                else:
                    # 5xx: 短退避（5s → 10s → 20s），处理瞬时故障
                    wait = min(
                        5.0 * (2.0 ** attempt),
                        60.0,
                    )

                jitter = random.uniform(0, wait * 0.15)
                total_wait = wait + jitter

                logger.warning(
                    f"🚦 [RateLimitMW] LLM API {error_type} 错误 "
                    f"(重试 {attempt + 1}/{self.max_retries})，"
                    f"等待 {total_wait:.0f}s 后重试"
                )
                self._notify_frontend(
                    f"正在执行中……"
                    f"({attempt + 1}/{self.max_retries})..."
                )

                await asyncio.sleep(total_wait)

        # 理论上不会到达这里（for 循环最后一轮会 raise）
        raise last_error  # type: ignore[misc]

    def _notify_frontend(self, message: str) -> None:
        """通过 stream_writer 通知前端限流/重试状态。

        使用 LangGraph 的 custom stream mode，前端会收到 STATUS 类型的消息。
        通知失败不影响主流程。
        """
        try:
            writer = get_stream_writer()
            if writer:
                writer(message)
        except Exception:
            pass  # 通知失败不影响主流程