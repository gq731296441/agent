"""LLM Token 用量累积（LangChain callback）。"""

from typing import Any, Dict, Optional

from langchain_core.callbacks import AsyncCallbackHandler


class TokenUsageAccumulator(AsyncCallbackHandler):
    def __init__(self):
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.model_name = ""

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
        if not isinstance(usage, dict):
            return
        self.prompt_tokens += int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        self.total_tokens += int(
            usage.get("total_tokens")
            or (self.prompt_tokens + self.completion_tokens)
            or 0
        )

    def as_dict(self) -> Dict[str, int]:
        total = self.total_tokens or (self.prompt_tokens + self.completion_tokens)
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": total,
        }


def attach_token_callback(config: dict) -> tuple[dict, TokenUsageAccumulator]:
    acc = TokenUsageAccumulator()
    cfg = dict(config)
    callbacks = list(cfg.get("callbacks") or [])
    callbacks.append(acc)
    cfg["callbacks"] = callbacks
    return cfg, acc
