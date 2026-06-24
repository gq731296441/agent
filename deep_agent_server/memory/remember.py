"""显式「记住」检测与文本抽取。"""

import re
from typing import Optional, Tuple

from config.config import app_config


def detect_remember_intent(text: str) -> bool:
    if not app_config.USER_MEMORY_REMEMBER_ENABLED:
        return False
    raw = (text or "").strip()
    if not raw:
        return False
    pattern = app_config.USER_MEMORY_REMEMBER_PATTERNS
    try:
        return bool(re.search(pattern, raw, re.IGNORECASE))
    except re.error:
        return bool(re.search(r"记住|/remember", raw, re.IGNORECASE))


def extract_memory_text(text: str) -> str:
    """从用户消息抽取要记忆的正文。"""
    raw = (text or "").strip()
    if not raw:
        return ""

    if raw.lower().startswith("/remember"):
        return raw.split(None, 1)[1].strip() if len(raw.split(None, 1)) > 1 else ""

    patterns = [
        r"^(?:请)?记住[：:,，]?\s*",
        r"^请记住[：:,，]?\s*",
        r"^保存到记忆[：:,，]?\s*",
        r"^帮我记一下[：:,，]?\s*",
    ]
    for pat in patterns:
        cleaned = re.sub(pat, "", raw, count=1, flags=re.IGNORECASE).strip()
        if cleaned != raw:
            return cleaned
    return raw


def parse_remember_message(text: str) -> Optional[Tuple[str, str]]:
    """若命中 remember 意图，返回 (memory_text, source)。"""
    if not detect_remember_intent(text):
        return None
    memory_text = extract_memory_text(text)
    if not memory_text:
        return None
    return memory_text, "explicit"
