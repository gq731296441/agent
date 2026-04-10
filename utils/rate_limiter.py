"""全局异步速率限制器

基于滑动窗口 + 最小间隔算法，控制 LLM API 调用速率，主动预防 429 Too Many Requests。

设计原则：
- 滑动窗口限制每分钟请求总数（max_rpm），避免密集突发
- 最小间隔限制两次连续请求之间的时间（min_interval_seconds），平滑请求分布
- 全局单例（llm_rate_limiter），所有 Agent 实例共享同一限制器
- 纯异步实现，等待时不阻塞事件循环

使用方式:
    from utils.rate_limiter import llm_rate_limiter
    waited = await llm_rate_limiter.acquire()  # 获取一次调用许可
"""

import asyncio
import time
from collections import deque

from config import get_logger

logger = get_logger(__name__)


def is_rate_limit_error(exc: Exception) -> bool:
    """判断异常是否为速率限制错误（429 Too Many Requests）。

    递归检查异常链（__cause__）以处理被 LangChain/LangGraph 包装的异常。

    Args:
        exc: 待检查的异常

    Returns:
        True 如果异常（或其 cause 链中的任一异常）是速率限制错误
    """
    # 优先进行精确的类型检查
    try:
        import openai
        if isinstance(exc, openai.RateLimitError):
            return True
    except ImportError:
        pass

    # 递归检查异常链
    cause = getattr(exc, "__cause__", None)
    if cause is not None and cause is not exc:
        if is_rate_limit_error(cause):
            return True

    # 字符串兜底（处理被二次包装且丢失类型信息的情况）
    err_str = str(exc).lower()
    return "429" in str(exc) or "rate limit" in err_str or "too many requests" in err_str


class LLMRateLimiter:
    """基于滑动窗口 + 最小间隔的异步速率限制器。

    特性：
    - 协程安全（asyncio.Lock）
    - 60 秒滑动窗口精确控制 RPM
    - 最小间隔防止连续突发调用
    - 等待期间释放锁，不阻塞其他协程

    Args:
        max_rpm: 每分钟最大请求数（滑动窗口内）
        min_interval_seconds: 两次请求之间的最小间隔（秒）
    """

    def __init__(self, max_rpm: int = 10, min_interval_seconds: float = 2.0):
        self.max_rpm = max_rpm
        self.min_interval = min_interval_seconds
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> float:
        """获取一次 API 调用许可。

        如果当前速率超限，会异步等待直到有配额可用。
        等待期间释放锁，允许其他协程并发检查。

        Returns:
            实际等待的总时长（秒），0.0 表示无需等待
        """
        total_waited = 0.0

        while True:
            async with self._lock:
                now = time.monotonic()

                # 清理滑动窗口外的过期记录（60 秒窗口）
                window_start = now - 60.0
                while self._timestamps and self._timestamps[0] < window_start:
                    self._timestamps.popleft()

                wait = 0.0

                # 检查最小间隔
                if self._timestamps:
                    gap = now - self._timestamps[-1]
                    if gap < self.min_interval:
                        wait = max(wait, self.min_interval - gap)

                # 检查 RPM 上限
                if len(self._timestamps) >= self.max_rpm:
                    # 需要等到最早的请求滑出 60 秒窗口
                    rpm_wait = self._timestamps[0] + 60.0 - now
                    if rpm_wait > 0:
                        wait = max(wait, rpm_wait)

                if wait <= 0:
                    # 有配额，记录时间戳并放行
                    self._timestamps.append(now)
                    return total_waited

            # 锁已释放 — 异步等待，然后重新竞争
            total_waited += wait
            logger.debug(
                f"🚦 [RateLimiter] 速率限制等待 {wait:.1f}s "
                f"(窗口内已有 {len(self._timestamps)}/{self.max_rpm} 个请求)"
            )
            await asyncio.sleep(wait)

    @property
    def current_rpm(self) -> int:
        """当前滑动窗口内的请求数（近似值，不加锁）。"""
        now = time.monotonic()
        window_start = now - 60.0
        return sum(1 for t in self._timestamps if t > window_start)

    def __repr__(self) -> str:
        return (
            f"LLMRateLimiter(max_rpm={self.max_rpm}, "
            f"min_interval={self.min_interval}s, "
            f"current_rpm≈{self.current_rpm})"
        )


# ============================================================
# 全局单例 — 所有 Agent 实例共享同一限制器
# 默认配置：每分钟最多 15 次请求，两次请求间至少间隔 2 秒
# 可通过环境变量或 config 覆盖
# ============================================================
llm_rate_limiter = LLMRateLimiter(max_rpm=15, min_interval_seconds=2.0)
