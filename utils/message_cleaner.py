"""AIMessage 思考内容清理工具

从 LangGraph checkpoint 持久化的 AIMessage 中移除推理/思考内容，
减少 checkpoint 中的 token 占用，延迟上下文压缩触发。

思考内容已通过 chat_history 表完整持久化（含 REASONING_FINAL 消息类型），
用户可在界面查看，因此 checkpoint 中无需保留。

支持两种推理内容形式：
1. additional_kwargs["reasoning_content"] — 模型原生推理（如 DeepSeek）
2. <think>...</think> 标签包裹在 content 中 — 部分模型的推理格式

使用场景：
    在 after_model 中间件钩子中调用，在 AIMessage 写入 checkpoint 之前清理。
"""

import re
from typing import Optional
from langchain_core.messages import AIMessage

from config import get_logger

logger = get_logger(__name__)

# 预编译正则：匹配 <think>...</think> 及其后的前导换行
# re.DOTALL 使 . 匹配换行符，支持多行思考内容
_THINK_TAG_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def strip_reasoning_from_message(message: AIMessage) -> bool:
    """从 AIMessage 中移除思考/推理内容，就地修改。

    处理两种推理内容形式：
    1. additional_kwargs["reasoning_content"] → 直接删除该键
    2. content 中的 <think>...</think> 标签 → 正则移除标签及内容

    注意：此函数会直接修改传入的 message 对象（就地操作），
    因为 LangGraph 的 after_model 钩子期望修改 state 中的消息。

    Args:
        message: 要清理的 AIMessage 实例

    Returns:
        bool: 是否实际进行了清理（True=有内容被移除）
    """
    cleaned = False

    # ==================== 方式1：清理 additional_kwargs.reasoning_content ====================
    if message.additional_kwargs and "reasoning_content" in message.additional_kwargs:
        reasoning_len = len(message.additional_kwargs["reasoning_content"])
        del message.additional_kwargs["reasoning_content"]
        cleaned = True
        logger.debug(f"【MessageCleaner】已移除 reasoning_content ({reasoning_len} chars)")

    # ==================== 方式2：清理 content 中的 <think>...</think> 标签 ====================
    content = message.content
    if isinstance(content, str) and "<think>" in content:
        cleaned_content = _THINK_TAG_RE.sub("", content)
        if cleaned_content != content:
            removed_len = len(content) - len(cleaned_content)
            message.content = cleaned_content
            cleaned = True
            logger.debug(f"【MessageCleaner】已移除 <think> 标签内容 ({removed_len} chars)")

    return cleaned
