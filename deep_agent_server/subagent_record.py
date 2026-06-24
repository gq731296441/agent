"""Subagent run 入库辅助（task 工具输入摘要等）。"""

import json
from typing import Any

from deep_agent_server.artifact import truncate_summary


def summarize_subagent_input(args: Any, *, max_chars: int = 500) -> str:
    """从 task 工具 args 提取输入摘要（不存 artifact）。"""
    if args is None:
        return ""
    if isinstance(args, str):
        return truncate_summary(args, max_chars=max_chars)
    if not isinstance(args, dict):
        return truncate_summary(str(args), max_chars=max_chars)
    if not args:
        return ""

    for key in ("description", "task", "prompt", "input"):
        val = args.get(key)
        if val:
            text = str(val).strip()
            if text:
                sub = args.get("subagent_type")
                prefix = f"[{sub}] " if sub else ""
                return truncate_summary(prefix + text, max_chars=max_chars)

    sub = args.get("subagent_type")
    prefix = f"[{sub}] " if sub else ""
    return truncate_summary(
        prefix + json.dumps(args, ensure_ascii=False, default=str),
        max_chars=max_chars,
    )


def task_subagent_name(args: Any) -> str:
    """子 agent 显示名：优先 subagent_type。"""
    if isinstance(args, dict):
        name = args.get("subagent_type") or args.get("name")
        if name:
            return str(name)[:64]
    return "task"
