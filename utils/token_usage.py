"""LLM Token 用量累积（LangChain callback）。

除 API 返回的真实 usage 总量外，还在每次 LLM 调用开始时按消息类型统计
prompt 构成（本地 tokenizer 估算）：
- system_tokens: 系统提示词（含技能指令、安全规则、工具描述等）
- user_tokens:   用户输入（含注入的记忆、HTML 分享链接说明等）
- ai_tokens:     助手历史（含 tool_calls —— write_file/edit_file 的大
                 content 参数会以工具调用参数形式进入此分类）
- tool_tokens:   工具结果（read_file 大文件输出、execute 输出等）

用于定位上下文消耗来源（如工具结果累积、长工具参数导致的上下文膨胀）。
估算值用于构成分析，总量以 API 返回的 usage 为准。
"""

from typing import Any, Dict

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from utils.token_counter import TokenCounter


class TokenUsageAccumulator(AsyncCallbackHandler):
    """按消息类型分解的 LLM Token 用量累积器。"""

    BREAKDOWN_KEYS = ("system_tokens", "user_tokens", "ai_tokens", "tool_tokens")

    def __init__(self):
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.model_name = ""
        self._counter = TokenCounter()
        self.prompt_breakdown: Dict[str, int] = {k: 0 for k in self.BREAKDOWN_KEYS}
        # 每次 LLM 调用的明细：真实 usage + 消息数 + 当次构成（用于定位膨胀点）
        self.call_details: list = []
        self._call_index = 0
        self._call_breakdown: Dict[str, int] = {k: 0 for k in self.BREAKDOWN_KEYS}
        self._call_messages_count = 0

    async def on_chat_model_start(self, serialized: Any, messages: Any, **kwargs: Any) -> None:
        """每次 LLM 调用开始前：按消息类型统计 prompt 构成（本地估算）。

        messages 为 List[List[BaseMessage]]（支持多 prompt），逐条计数。
        """
        try:
            for msg_list in messages or []:
                for msg in msg_list:
                    tokens = self._count_message(msg)
                    category = self._category_of(msg)
                    self.prompt_breakdown[category] += tokens
                    self._call_breakdown[category] += tokens
                    self._call_messages_count += 1
        except Exception:
            pass

    def _category_of(self, message: Any) -> str:
        if isinstance(message, SystemMessage):
            return "system_tokens"
        if isinstance(message, ToolMessage):
            return "tool_tokens"
        if isinstance(message, HumanMessage):
            return "user_tokens"
        if isinstance(message, AIMessage):
            return "ai_tokens"
        return "system_tokens"

    def _count_message(self, message: Any) -> int:
        try:
            return self._counter.count_messages_tokens([message])
        except Exception:
            return 0

    async def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        usage = None
        if hasattr(response, "llm_output") and isinstance(response.llm_output, dict):
            usage = response.llm_output.get("token_usage") or response.llm_output.get("usage")
        if not usage and getattr(response, "generations", None):
            for gen_list in response.generations:
                for gen in gen_list:
                    msg = getattr(gen, "message", None)
                    meta = getattr(msg, "usage_metadata", None) if msg else None
                    if isinstance(meta, dict):
                        usage = meta
                        break
                if usage:
                    break
        pt = ct = tt = 0
        if isinstance(usage, dict):
            pt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            ct = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
            tt = int(usage.get("total_tokens") or 0) or (pt + ct)
            self.prompt_tokens += pt
            self.completion_tokens += ct
            self.total_tokens += tt
        self._call_index += 1
        self.call_details.append(
            {
                "call": self._call_index,
                "prompt_tokens": pt,
                "completion_tokens": ct,
                "total_tokens": tt,
                "messages_count": self._call_messages_count,
                "breakdown": dict(self._call_breakdown),
            }
        )
        self._call_breakdown = {k: 0 for k in self.BREAKDOWN_KEYS}
        self._call_messages_count = 0

    def as_dict(self) -> Dict[str, Any]:
        total = self.total_tokens or (self.prompt_tokens + self.completion_tokens)
        est_total = sum(self.prompt_breakdown.values())
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": total,
            "prompt_breakdown": {
                **self.prompt_breakdown,
                # 本地估算的消息总 token（不含 tools 定义等），与 api_total 对比可
                # 判断估算偏差；两者差值主要来自 API 侧的工具 schema / 额外开销
                "estimated_total": est_total,
                "api_total": self.prompt_tokens,
            },
            "call_count": self._call_index,
            "call_details": self.call_details,
        }


def attach_token_callback(config: dict) -> tuple[dict, TokenUsageAccumulator]:
    acc = TokenUsageAccumulator()
    cfg = dict(config)
    callbacks = list(cfg.get("callbacks") or [])
    callbacks.append(acc)
    cfg["callbacks"] = callbacks
    return cfg, acc
