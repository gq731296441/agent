"""Detect sustained repetition in one streamed assistant response."""

import re
from dataclasses import dataclass

from utils.extractors import ThinkTagParser


@dataclass(frozen=True)
class RepetitionHit:
    period_chars: int
    repeat_count: int


class RepetitionDetector:
    def __init__(
        self,
        min_fragment_chars: int = 24,
        repeat_threshold: int = 3,
        window_chars: int = 4096,
        max_period_chars: int = 1500,
        check_every_chars: int = 12,
    ):
        self.min_fragment_chars = min_fragment_chars
        self.repeat_threshold = repeat_threshold
        self.window_chars = window_chars
        self.max_period_chars = max_period_chars
        self.check_every_chars = check_every_chars
        self._tail = ""
        self._since_check = 0

    def feed(self, chunk: str) -> RepetitionHit | None:
        if not chunk:
            return None
        self._tail = re.sub(r"\s+", " ", self._tail + chunk)[-self.window_chars:]
        self._since_check += len(chunk)
        if self._since_check < self.check_every_chars:
            return None
        self._since_check = 0

        text = self._tail
        max_period = min(self.max_period_chars, len(text) // self.repeat_threshold)
        for period in range(self.min_fragment_chars, max_period + 1):
            fragment = text[-period:]
            if fragment.strip() and text.endswith(fragment * self.repeat_threshold) and not any(
                period % shorter == 0 and fragment == fragment[:shorter] * (period // shorter)
                for shorter in range(1, self.min_fragment_chars)
            ):
                return RepetitionHit(period, self.repeat_threshold)

        sentences = re.split(r"(?<=[。！？.!?；;])\s*", text)
        if len(sentences) >= self.repeat_threshold + 1:
            recent = sentences[-(self.repeat_threshold + 1):-1]
            if all(sentence == recent[0] for sentence in recent) and len(recent[0]) >= self.min_fragment_chars:
                return RepetitionHit(len(recent[0]), self.repeat_threshold)
        return None


class RepetitionDetected(Exception):
    def __init__(self, hit: RepetitionHit):
        self.hit = hit
        super().__init__(f"assistant text repeated {hit.repeat_count} times (period={hit.period_chars})")


class ModelTextGuard:
    """Keep independent detectors for interleaved agent namespaces."""

    def __init__(self):
        self._detectors: dict[tuple, RepetitionDetector] = {}
        self._parsers: dict[tuple, ThinkTagParser] = {}

    def reset(self, namespace: tuple) -> None:
        self._detectors.pop(namespace, None)
        self._parsers.pop(namespace, None)

    def inspect(self, mode: str, namespace: tuple, chunk) -> None:
        if mode == "tools":
            self.reset(namespace)
            return
        if mode == "updates" and isinstance(chunk, dict) and "model" in chunk:
            self.reset(namespace)
            return
        if mode != "messages":
            return
        try:
            message, metadata = chunk
        except (TypeError, ValueError):
            return
        if metadata.get("langgraph_node") != "model":
            self.reset(namespace)
            return
        if getattr(message, "tool_call_chunks", None) or getattr(message, "tool_calls", None):
            return
        content = getattr(message, "content", None)
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "".join(
                block if isinstance(block, str) else block.get("text", "")
                for block in content
                if isinstance(block, str) or (isinstance(block, dict) and block.get("type") == "text")
            )
        else:
            return
        parser = self._parsers.setdefault(namespace, ThinkTagParser())
        _, text = parser.process_chunk(text)
        hit = self._detectors.setdefault(namespace, RepetitionDetector()).feed(text)
        if hit:
            raise RepetitionDetected(hit)
