"""User Memory prompt 注入块。"""

from typing import Any, Dict, List


def build_memory_prompt_block(memories: List[Dict[str, Any]]) -> str:
    if not memories:
        return ""
    lines = ["## 用户长期记忆（请遵循）"]
    for mem in memories:
        text = (mem.get("text") or "").strip()
        if text:
            lines.append(f"- {text}")
    if len(lines) <= 1:
        return ""
    return "\n".join(lines)
