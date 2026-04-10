import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, ToolMessage

from config import get_logger

logger = get_logger(__name__)

# 项目内 tokenizer.json 相对路径
PROJECT_TIKTOKEN_CACHE_DIR = "tiktoken_cache"
TOKENIZER_JSON_FILE = "tokenizer.json"


class TokenCounter:
    """项目内统一的 token 计数/截断工具（基于 tokenizers + tokenizer.json）。"""

    def __init__(self):
        self._tokenizer = self._init_tokenizer()

    @property
    def tokenizer(self):
        return self._tokenizer

    def count_text_tokens(self, text: str) -> int:
        if not text:
            return 0
        if self._tokenizer is not None:
            return len(self._tokenizer.encode(text).ids)

        # 降级：离线估算
        chinese_chars = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
        other_chars = max(len(text) - chinese_chars, 0)
        estimated_tokens = chinese_chars / 1.5 + other_chars / 4
        return max(1, int(estimated_tokens))

    def truncate_text_by_tokens(self, text: str, max_tokens: int) -> str:
        if not text:
            return ""
        if max_tokens <= 0:
            return ""

        if self._tokenizer is not None:
            encoding = self._tokenizer.encode(text)
            token_ids = encoding.ids
            if len(token_ids) <= max_tokens:
                return text
            return self._tokenizer.decode(token_ids[:max_tokens])

        # 降级：按字符粗略截断
        approx_max_chars = max(50, max_tokens * 4)
        if len(text) <= approx_max_chars:
            return text
        return text[:approx_max_chars]

    def count_messages_tokens(self, messages: Iterable[Any]) -> int:
        total = 0
        overhead_per_message = 4  # 每条消息的固定开销（role/分隔符等）
        for message in messages:
            serialized = self.stringify_message_like(message)
            total += self.count_text_tokens(serialized)
            total += overhead_per_message
        return total

    def stringify_message_like(self, message: Any) -> str:
        if isinstance(message, BaseMessage):
            role_map = {"human": "user", "ai": "assistant", "system": "system"}
            msg_type = getattr(message, "type", message.__class__.__name__)
            role = role_map.get(msg_type, msg_type)
            payload: dict[str, Any] = {
                "role": role,
                "content": self.stringify_content(getattr(message, "content", "")),
            }

            if getattr(message, "name", None):
                payload["name"] = message.name

            if hasattr(message, "tool_calls") and getattr(message, "tool_calls", None):
                payload["tool_calls"] = getattr(message, "tool_calls")

            if isinstance(message, ToolMessage):
                if getattr(message, "tool_call_id", None):
                    payload["tool_call_id"] = message.tool_call_id

            return json.dumps(payload, ensure_ascii=False, default=str)

        if isinstance(message, dict):
            return json.dumps(message, ensure_ascii=False, default=str)

        return str(message)

    def stringify_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text" and "text" in item:
                    parts.append(str(item["text"]))
                elif isinstance(item, (dict, list)):
                    parts.append(json.dumps(item, ensure_ascii=False, default=str))
                else:
                    parts.append(str(item))
            return "\n".join(part for part in parts if part)
        if isinstance(content, dict):
            return json.dumps(content, ensure_ascii=False, default=str)
        return str(content)

    def _init_tokenizer(self):
        """加载本地 tokenizer.json，失败则降级为离线估算。"""
        tokenizer_path = self._find_tokenizer_json_path()

        if tokenizer_path is not None:
            try:
                from tokenizers import Tokenizer

                tokenizer = Tokenizer.from_file(str(tokenizer_path))
                logger.info(
                    f"【TokenCounter】已加载 tokenizer.json: {tokenizer_path} "
                    f"(词表大小: {tokenizer.get_vocab_size()})"
                )
                return tokenizer
            except ImportError:
                logger.warning(
                    "【TokenCounter】tokenizers 库未安装，请执行 pip install tokenizers。改用离线估算模式。"
                )
            except Exception as e:
                logger.warning(f"【TokenCounter】加载 tokenizer.json 失败，改用离线估算模式。错误: {e}")
        else:
            logger.warning("【TokenCounter】未找到 tokenizer.json 文件，改用离线估算模式。")

        return None

    def _find_tokenizer_json_path(self) -> Path | None:
        """按优先级搜索 tokenizer.json 文件。"""
        project_root = Path(__file__).resolve().parents[1]
        search_paths = [
            # 优先：项目内 tiktoken_cache 目录
            project_root / PROJECT_TIKTOKEN_CACHE_DIR / TOKENIZER_JSON_FILE,
            # 项目根目录
            project_root / TOKENIZER_JSON_FILE,
            # 当前工作目录
            Path.cwd() / TOKENIZER_JSON_FILE,
            Path.cwd() / PROJECT_TIKTOKEN_CACHE_DIR / TOKENIZER_JSON_FILE,
        ]

        for candidate in search_paths:
            if candidate.exists() and candidate.is_file():
                logger.info(f"【TokenCounter】命中本地 tokenizer.json: {candidate}")
                return candidate

        logger.warning(
            "【TokenCounter】未找到本地 tokenizer.json 文件，"
            f"已检查: {', '.join(str(p) for p in search_paths)}"
        )
        return None


# 全局单例，供其他模块直接 import 使用
token_counter = TokenCounter()