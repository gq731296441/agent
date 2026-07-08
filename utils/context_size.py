"""会话上下文大小查询工具。

从 LangGraph 编译图获取当前会话的完整消息状态，
使用项目统一 TokenCounter 准确计算 token 数量，
返回上下文窗口使用量及百分比。

注意：不能直接使用 checkpointer.aget_tuple()，因为 DeepAgentState
的 messages 字段使用了 DeltaChannel（增量存储），raw checkpoint 中
仅存储 sentinel 值，必须通过 compiled.aget_state() 重建完整状态。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from config import get_logger

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.graph.state import CompiledStateGraph

logger = get_logger(__name__)

# 常见模型的 max_input_tokens（tokenizer.json 未命中时使用）
_MODEL_MAX_TOKENS: dict[str, int] = {
    "nebulacoder-cot-v8.0": 262144,
    "nebulacoder-v8.0": 262144,
}

# 模型 max_input_tokens 的默认回退值
_DEFAULT_MAX_INPUT_TOKENS = 131072

# 预警阈值
WARNING_THRESHOLD = 0.70   # 70% 开始预警
CRITICAL_THRESHOLD = 0.2  # 85% 危险（自动摘要触发点）


@dataclass
class ContextSizeResult:
    """上下文大小查询结果。"""

    token_count: int
    """当前会话消息 + 系统提示词的 token 估算数。"""

    max_input_tokens: int
    """模型最大输入 token 数。"""

    percentage: float
    """上下文窗口使用百分比（0.0-100.0+）。"""

    message_count: int
    """会话中的消息条数。"""

    warning_level: str
    """预警级别: 'normal' | 'warning' | 'critical'。"""

    model_name: str = ""
    """使用的模型名称（如果已知）。"""

    details: dict = field(default_factory=dict)
    """额外详情（如 tool_messages, ai_messages, system_prompt_tokens 等）。"""


async def get_session_context_size(
    compiled: "CompiledStateGraph",
    user_id: str,
    session_id: str,
    *,
    max_input_tokens: Optional[int] = None,
    model_name: str = "",
    system_prompt_text: str = "",
) -> ContextSizeResult:
    """获取指定会话的上下文大小及百分比。

    通过 compiled.aget_state() 获取完整状态（包含 DeltaChannel 重建），
    使用项目 TokenCounter 准确计算 token 数。

    Args:
        compiled: LangGraph 编译后的 StateGraph。
        user_id: 用户 ID。
        session_id: 会话 ID。
        max_input_tokens: 手动指定最大输入 token 数；
            为 None 时从 model_name 推断或使用默认值。
        model_name: 模型名称，用于推断 max_input_tokens。
        system_prompt_text: 系统提示词文本。每次 LLM 调用都会携带，
            应计入上下文总用量。为空则不额外计数。

    Returns:
        ContextSizeResult 包含 token 数、百分比、预警级别等信息。
    """
    from utils.token_counter import token_counter as tk_counter

    thread_id = f"{user_id}#{session_id}"
    config = {"configurable": {"thread_id": thread_id}}

    # ① 通过编译图获取完整状态（重建 DeltaChannel）
    try:
        state = await compiled.aget_state(config)
    except Exception as e:
        logger.error(f"[ContextSize] 获取 state 失败: {e}")
        raise ValueError(f"无法获取会话状态: session_id={session_id}") from e

    if state is None:
        return ContextSizeResult(
            token_count=0,
            max_input_tokens=max_input_tokens or _DEFAULT_MAX_INPUT_TOKENS,
            percentage=0.0,
            message_count=0,
            warning_level="normal",
            model_name=model_name,
        )

    raw_messages = list(state.values.get("messages") or [])

    # ② 检查是否有 SummarizationMiddleware 的压缩事件
    # deepagents 不直接修改 messages，而是通过 _summarization_event
    # 动态构造有效消息：[summary_message] + messages[cutoff_index:]
    summarization_event = state.values.get("_summarization_event")
    has_summarization = bool(summarization_event and summarization_event.get("cutoff_index"))

    if has_summarization:
        try:
            summary_msg = summarization_event["summary_message"]
            cutoff_idx = int(summarization_event["cutoff_index"])
            effective_messages = [summary_msg] + raw_messages[cutoff_idx:]
        except (KeyError, TypeError, IndexError):
            effective_messages = raw_messages
            has_summarization = False
    else:
        effective_messages = raw_messages

    # ③ 统计消息数（raw 和 effective）
    raw_message_count = len(raw_messages)
    effective_message_count = len(effective_messages)

    # ④ 计数：有效消息 tokens + 系统提示词 tokens
    token_count = 0
    raw_token_count = 0
    system_prompt_tokens = 0

    if effective_messages:
        token_count = tk_counter.count_messages_tokens(effective_messages)
    if raw_messages:
        raw_token_count = tk_counter.count_messages_tokens(raw_messages)

    if system_prompt_text:
        from langchain_core.messages import SystemMessage
        system_prompt_tokens = tk_counter.count_messages_tokens(
            [SystemMessage(content=system_prompt_text)]
        )
        token_count += system_prompt_tokens
        raw_token_count += system_prompt_tokens

    # ⑤ 确定 max_input_tokens
    if max_input_tokens is None:
        max_input_tokens = _resolve_max_input_tokens(model_name)

    # ⑥ 计算百分比和预警级别（基于有效消息）
    if max_input_tokens > 0:
        percentage = round(token_count / max_input_tokens * 100, 1)
    else:
        percentage = 0.0

    if percentage >= CRITICAL_THRESHOLD * 100:
        warning_level = "critical"
    elif percentage >= WARNING_THRESHOLD * 100:
        warning_level = "warning"
    else:
        warning_level = "normal"

    # ⑦ 额外详情
    details: dict = {
        "system_prompt_tokens": system_prompt_tokens,
        "conversation_tokens": token_count - system_prompt_tokens,
        "effective_messages": effective_message_count,
        "has_summarization": has_summarization,
    }
    if has_summarization:
        details["raw_token_count"] = raw_token_count
        details["raw_messages"] = raw_message_count
        details["compressed_saved"] = raw_token_count - token_count

    if raw_messages:
        from langchain_core.messages import AIMessage, ToolMessage
        tool_msgs = sum(1 for m in raw_messages if isinstance(m, ToolMessage))
        ai_msgs = sum(1 for m in raw_messages if isinstance(m, AIMessage))
        details["tool_messages"] = tool_msgs
        details["ai_messages"] = ai_msgs

    logger.info(
        f"[ContextSize] session={session_id} "
        f"tokens={token_count}/{max_input_tokens} "
        f"(effective={effective_message_count}, raw={raw_message_count}"
        f"{', compressed=' + str(details.get('compressed_saved', 0)) if has_summarization else ''}) "
        f"({percentage}%) level={warning_level}"
    )

    return ContextSizeResult(
        token_count=token_count,
        max_input_tokens=max_input_tokens,
        percentage=percentage,
        message_count=effective_message_count,
        warning_level=warning_level,
        model_name=model_name,
        details=details,
    )


def _resolve_max_input_tokens(model_name: str) -> int:
    """根据模型名称解析 max_input_tokens。"""
    if not model_name:
        return _DEFAULT_MAX_INPUT_TOKENS

    # 精确匹配
    if model_name in _MODEL_MAX_TOKENS:
        return _MODEL_MAX_TOKENS[model_name]

    # 前缀匹配
    for prefix, value in sorted(
        _MODEL_MAX_TOKENS.items(), key=lambda x: -len(x[0])
    ):
        if model_name.startswith(prefix):
            return value

    return _DEFAULT_MAX_INPUT_TOKENS


# ---- 工具函数（供内部代码直接调用，无需编译图）----

def count_messages_tokens_sync(messages: list) -> int:
    """同步版本：对消息列表进行 token 计数。

    适用于已有消息列表、无需从 Checkpointer 读取的场景。
    """
    from utils.token_counter import token_counter as tk_counter
    return tk_counter.count_messages_tokens(messages)


def get_context_percentage(
    token_count: int,
    max_input_tokens: int,
) -> float:
    """计算上下文使用百分比。"""
    if max_input_tokens <= 0:
        return 0.0
    return round(token_count / max_input_tokens * 100, 1)
