from langchain.agents.middleware import AgentState
from langchain_core.messages import RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langchain.agents.middleware import SummarizationMiddleware
from langchain.agents.middleware import LLMToolSelectorMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore
from langgraph.config import get_config
from config import get_logger
from typing import Any, Optional
from utils.token_counter import token_counter

logger = get_logger(__name__)

# ==================== 阈值常量 ====================
# 预留给输出的 token 数（压缩摘要本身的输出上限）
RESERVED_OUTPUT_TOKENS = 5000
# 自动压缩缓冲区：有效窗口 - 此值 = 压缩触发阈值
AUTOCOMPACT_BUFFER_TOKENS = 50000
# 告警阈值缓冲区：低于触发阈值 N tokens 时开始告警
WARNING_BUFFER_TOKENS = 10000
# 错误阈值缓冲区
ERROR_BUFFER_TOKENS = 3000
# 连续失败熔断上限
MAX_CONSECUTIVE_FAILURES = 3


def get_effective_context_window(max_input_tokens: int) -> int:
    """计算有效上下文窗口 = 最大输入 token - 输出预留。"""
    return max(0, max_input_tokens - RESERVED_OUTPUT_TOKENS)


def get_auto_compact_threshold(max_input_tokens: int) -> int:
    """计算自动压缩触发阈值 = 有效窗口 - 缓冲区。"""
    effective = get_effective_context_window(max_input_tokens)
    return max(0, effective - AUTOCOMPACT_BUFFER_TOKENS)


def calculate_token_warning_state(
    token_usage: int,
    max_input_tokens: int,
) -> dict[str, Any]:
    """
    多级告警状态计算。

    返回:
        percent_left: 剩余百分比（0-100）
        is_above_warning: 是否触及告警阈值
        is_above_error: 是否触及错误阈值
        is_above_compact: 是否触及自动压缩阈值
        is_at_blocking: 是否触及阻塞上限（应拒绝继续对话）
    """
    compact_threshold = get_auto_compact_threshold(max_input_tokens)
    effective = get_effective_context_window(max_input_tokens)

    # 剩余百分比
    percent_left = max(0, round(((effective - token_usage) / effective) * 100)) if effective > 0 else 0

    warning_threshold = compact_threshold - WARNING_BUFFER_TOKENS
    error_threshold = compact_threshold - ERROR_BUFFER_TOKENS

    return {
        "percent_left": percent_left,
        "is_above_warning": token_usage >= warning_threshold,
        "is_above_error": token_usage >= error_threshold,
        "is_above_compact": token_usage >= compact_threshold,
        "is_at_blocking": token_usage >= effective,
        "token_usage": token_usage,
        "compact_threshold": compact_threshold,
        "effective_window": effective,
    }


class LoggingSummarizationMiddleware(SummarizationMiddleware):
    """带日志、熔断器和多级告警的增强总结中间件。

    增强能力（相对于原始 SummarizationMiddleware）：
    1. 压缩前后的日志通知
    2. 连续失败熔断器：连续 N 次压缩失败后停止重试，避免浪费 API 调用
    3. 多级告警状态：通过 stream_writer 通知前端上下文剩余情况
    4. 动态阈值计算：可基于模型 max_input_tokens 自动计算触发阈值
    """

    def __init__(
        self,
        model: str | BaseChatModel,
        max_input_tokens: int | None = None,
        **kwargs: Any,
    ):
        """
        Args:
            model: 用于生成摘要的模型
            max_input_tokens: 主模型的最大输入 token 数。
                提供后将自动计算动态触发阈值（覆盖 trigger 参数）。
                不提供则使用 kwargs 中的 trigger。
            **kwargs: 传递给 SummarizationMiddleware 的其他参数
        """
        self._token_counter = token_counter
        kwargs.setdefault("token_counter", self._token_counter.count_messages_tokens)

        # 动态阈值：如果提供了 max_input_tokens，自动计算 trigger
        self._max_input_tokens = max_input_tokens
        if max_input_tokens is not None and "trigger" not in kwargs:
            dynamic_threshold = get_auto_compact_threshold(max_input_tokens)
            kwargs["trigger"] = ("tokens", dynamic_threshold)
            logger.info(
                f"【SummarizationMiddleware】动态阈值已启用: "
                f"max_input={max_input_tokens}, effective={get_effective_context_window(max_input_tokens)}, "
                f"compact_trigger={dynamic_threshold}"
            )

        super().__init__(model=model, **kwargs)

        # ===== Store 持久化 key =====
        # 熔断器状态通过 runtime.store 持久化，key 格式: ("compaction_state",) / thread_id
        self._STORE_NAMESPACE = ("compaction_state",)

    # ==================== Store 读写辅助 ====================

    def _get_thread_id(self, runtime: Runtime) -> Optional[str]:
        """从当前运行上下文中提取 thread_id 作为 store key。
        
        优先从 langgraph.config.get_config() 获取（ContextVar 注入的运行时配置），
        这是 LangGraph 在 middleware 中获取 configurable 的标准方式。
        Runtime dataclass 本身不包含 config 字段。
        """
        # 方式1：langgraph.config.get_config()（推荐，最可靠）
        try:
            config = get_config()
            thread_id = config.get("configurable", {}).get("thread_id")
            if thread_id:
                return thread_id
        except RuntimeError:
            # 不在 runnable 上下文中（如单元测试），静默忽略
            pass

        # 方式2：从 runtime.context 尝试获取（如果 context 是 dict 或有 thread_id 属性）
        context = getattr(runtime, "context", None)
        if isinstance(context, dict):
            thread_id = context.get("thread_id")
            if thread_id:
                return thread_id
        elif context is not None:
            thread_id = getattr(context, "thread_id", None)
            if thread_id:
                return thread_id

        logger.debug("【SummarizationMiddleware】无法获取 thread_id，熔断器状态将使用默认值")
        return None

    async def _load_circuit_state(self, runtime: Runtime) -> dict:
        """从 store 异步加载熔断器状态，不存在则返回默认值。"""
        store: Optional[BaseStore] = getattr(runtime, "store", None)
        thread_id = self._get_thread_id(runtime)
        if store is None or thread_id is None:
            return {"consecutive_failures": 0, "total_compactions": 0}
        try:
            item = await store.aget(self._STORE_NAMESPACE, thread_id)
            if item is not None:
                return item.value
        except Exception as e:
            logger.debug(f"【SummarizationMiddleware】加载熔断器状态失败: {e}")
        return {"consecutive_failures": 0, "total_compactions": 0}

    async def _save_circuit_state(
        self, runtime: Runtime, consecutive_failures: int, total_compactions: int
    ) -> None:
        """将熔断器状态异步写入 store。"""
        store: Optional[BaseStore] = getattr(runtime, "store", None)
        thread_id = self._get_thread_id(runtime)
        if store is None or thread_id is None:
            return
        try:
            await store.aput(
                self._STORE_NAMESPACE,
                thread_id,
                {"consecutive_failures": consecutive_failures, "total_compactions": total_compactions},
            )
        except Exception as e:
            logger.debug(f"【SummarizationMiddleware】保存熔断器状态失败: {e}")

    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """模型调用前处理消息，增强：熔断器检查 + 多级告警 + 压缩日志。"""
        messages = state["messages"]
        self._ensure_message_ids(messages)

        total_tokens = self.token_counter(messages)

        # ===== 多级告警（如有 max_input_tokens 配置）=====
        # if self._max_input_tokens:
        #     warning_state = calculate_token_warning_state(total_tokens, self._max_input_tokens)
        #     self._emit_warning_state(warning_state, runtime)

        # ===== 从 store 加载熔断器状态 =====
        circuit_state = await self._load_circuit_state(runtime)
        consecutive_failures = circuit_state["consecutive_failures"]
        total_compactions = circuit_state["total_compactions"]

        # ===== 熔断器检查 =====
        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            logger.warning(
                f"【SummarizationMiddleware】🔌 熔断器已触发: "
                f"连续 {consecutive_failures} 次压缩失败，跳过本次压缩。"
                f"当前 token 数: {total_tokens}"
            )
            return None

        if not self._should_summarize(messages, total_tokens):
            return None

        cutoff_index = self._determine_cutoff_index(messages)
        if cutoff_index <= 0:
            return None

        # ===== 压缩执行 =====
        logger.info(f"【SummarizationMiddleware】消息过长（{total_tokens} tokens），正在压缩...")
        writer = getattr(runtime, "stream_writer", None)
        if writer:
            writer("正在总结历史上下文...")

        messages_to_summarize, preserved_messages = self._partition_messages(messages, cutoff_index)

        try:
            summary = await self._acreate_summary(messages_to_summarize)

            # 检查压缩结果是否有效（错误摘要以 "Error" 开头）
            if summary.startswith("Error generating summary"):
                raise RuntimeError(summary)

            new_messages = self._build_new_messages(summary)

            after_tokens = self.token_counter(new_messages + preserved_messages)
            logger.info(
                f"【SummarizationMiddleware】✅ 压缩完成: "
                f"{total_tokens} -> {after_tokens} tokens "
                f"(释放 {total_tokens - after_tokens} tokens, "
                f"保留 {len(preserved_messages)} 条消息)"
            )

            # 压缩成功：重置熔断器计数并持久化
            await self._save_circuit_state(runtime, 0, total_compactions + 1)

            return {
                "messages": [
                    RemoveMessage(id=REMOVE_ALL_MESSAGES),
                    *new_messages,
                    *preserved_messages,
                ]
            }

        except Exception as e:
            consecutive_failures += 1
            # 失败时递增并持久化
            await self._save_circuit_state(runtime, consecutive_failures, total_compactions)
            logger.error(
                f"【SummarizationMiddleware】❌ 压缩失败 "
                f"(连续第 {consecutive_failures}/{MAX_CONSECUTIVE_FAILURES} 次): {e}"
            )
            # 失败不阻断主流程，返回 None 让原始消息继续传递
            return None

    def _emit_warning_state(self, warning_state: dict, runtime: Runtime) -> None:
        """通过 stream_writer 向前端推送告警状态。"""
        try:
            writer = getattr(runtime, "stream_writer", None)
            if writer is None:
                return
            

            if warning_state["is_at_blocking"]:
                writer({
                    "type": "context_warning",
                    "level": "blocking",
                    "percent_left": warning_state["percent_left"],
                    "message": f"上下文已满（{warning_state['token_usage']}/{warning_state['effective_window']} tokens），请开启新会话",
                })
                logger.warning(
                    f"【SummarizationMiddleware】⛔ 上下文阻塞: "
                    f"{warning_state['token_usage']}/{warning_state['effective_window']} tokens"
                )
            elif warning_state["is_above_error"]:
                writer({
                    "type": "context_warning",
                    "level": "error",
                    "percent_left": warning_state["percent_left"],
                    "message": f"上下文即将耗尽（剩余 {warning_state['percent_left']}%）",
                })
            elif warning_state["is_above_warning"]:
                writer({
                    "type": "context_warning",
                    "level": "warning",
                    "percent_left": warning_state["percent_left"],
                    "message": f"🟡 上下文使用较多（剩余 {warning_state['percent_left']}%）",
                })
        except Exception as e:
            # 告警推送失败不应影响主流程
            logger.debug(f"【SummarizationMiddleware】告警推送异常（可忽略）: {e}")

    async def reset_circuit_breaker(self, runtime: Runtime) -> None:
        """手动重置熔断器（可用于 /compact 手动压缩后调用）。
        
        Args:
            runtime: LangGraph Runtime，用于访问 store 和 thread_id
        """
        circuit_state = await self._load_circuit_state(runtime)
        if circuit_state["consecutive_failures"] > 0:
            logger.info(
                f"【SummarizationMiddleware】🔄 熔断器已重置 "
                f"(之前连续失败 {circuit_state['consecutive_failures']} 次)"
            )
            await self._save_circuit_state(runtime, 0, circuit_state["total_compactions"])

    async def get_stats(self, runtime: Runtime) -> dict[str, Any]:
        """返回指定 thread 的运行时统计信息。
        
        Args:
            runtime: LangGraph Runtime，用于访问 store 和 thread_id
        """
        circuit_state = await self._load_circuit_state(runtime)
        return {
            "total_compactions": circuit_state["total_compactions"],
            "consecutive_failures": circuit_state["consecutive_failures"],
            "circuit_breaker_tripped": circuit_state["consecutive_failures"] >= MAX_CONSECUTIVE_FAILURES,
        }