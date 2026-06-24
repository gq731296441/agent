"""MCP 工具名规范化（兼容 OpenAI/DeepSeek 等 API 的 function.name 约束）。"""

import re
from typing import Any, List, Set

# LLM API 要求: ^[a-zA-Z0-9_-]+$
_TOOL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

# 常见中文工具名 -> ASCII 映射（MCP 服务端同步改名时保持一致）
_KNOWN_ALIASES = {
    "单据筛选": "doc_query",
    "单据跟催": "doc_mail",
    "单据处理": "doc_process",
}


def is_valid_llm_tool_name(name: str) -> bool:
    return bool(name and _TOOL_NAME_PATTERN.fullmatch(name))


def sanitize_tool_name(name: str, used: Set[str]) -> str:
    """将工具名转为 LLM API 合法标识符，并保证在 used 集合内唯一。"""
    if is_valid_llm_tool_name(name):
        candidate = name
    elif name in _KNOWN_ALIASES:
        candidate = _KNOWN_ALIASES[name]
    else:
        candidate = re.sub(r"[^a-zA-Z0-9_-]", "_", name or "tool")
        candidate = re.sub(r"_+", "_", candidate).strip("_") or "mcp_tool"
        if not candidate[0].isalpha():
            candidate = f"mcp_{candidate}"

    base = candidate
    suffix = 2
    while candidate in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def normalize_tools_for_llm(tools: List[Any]) -> List[Any]:
    """重命名 LangChain 工具对外 name，内部 MCP 调用仍使用原始 tool.name（闭包）。"""
    used: Set[str] = set()
    normalized: List[Any] = []

    for tool in tools:
        original = tool.name
        # MCP 工具统一 mcp_ 前缀
        if not original.startswith("mcp_"):
            base = sanitize_tool_name(f"mcp_{original}", used)
        else:
            base = sanitize_tool_name(original, used)
        safe = base
        if safe == original:
            normalized.append(tool)
            continue

        desc = tool.description or ""
        if original not in desc:
            desc = f"{desc}\n[原工具名: {original}]".strip()

        try:
            normalized.append(tool.model_copy(update={"name": safe, "description": desc}))
        except Exception:
            tool.name = safe
            if hasattr(tool, "description"):
                tool.description = desc
            normalized.append(tool)

    return normalized
