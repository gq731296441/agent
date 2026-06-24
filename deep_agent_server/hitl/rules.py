"""HITL 危险操作规则。

为待审批工具调用评估 risk_level（low/medium/high），
并生成前端展示用的 action preview。
"""

import re
from typing import Any, Dict, List

_DANGEROUS_PATTERNS = [
    (re.compile(r"rm\s+-rf", re.I), "high"),
    (re.compile(r"sudo\s+", re.I), "high"),
    (re.compile(r"chmod\s+777", re.I), "high"),
    (re.compile(r"curl\s+.*\|\s*sh", re.I), "high"),
    (re.compile(r"mkfs\.", re.I), "high"),
]

_FS_WRITE_TOOLS = {"write_file", "edit_file"}
_EXECUTE_TOOLS = {"execute"}


def assess_risk(tool_name: str, args: dict) -> str:
    """根据工具类型与参数内容评估风险等级。"""
    args = args or {}
    if tool_name in _EXECUTE_TOOLS:
        cmd = str(args.get("command") or args.get("cmd") or "")
        for pat, level in _DANGEROUS_PATTERNS:
            if pat.search(cmd):
                return level
        return "medium"
    if tool_name in _FS_WRITE_TOOLS:
        path = str(args.get("file_path") or args.get("path") or "")
        if ".env" in path or "credentials" in path:
            return "high"
        return "medium"
    if tool_name.startswith("mcp_"):
        return "medium"
    return "low"


def build_action_preview(tool_name: str, args: dict) -> dict:
    args = args or {}
    preview = {
        "command": args.get("command") or args.get("cmd"),
        "file_path": args.get("file_path") or args.get("path"),
        "old_string": (args.get("old_string") or "")[:200] or None,
        "new_string": (args.get("new_string") or "")[:200] or None,
        "mcp_server": args.get("mcp_server"),
    }
    return {k: v for k, v in preview.items() if v is not None}


def enrich_interrupt_actions(interrupt_data: Any) -> List[Dict[str, Any]]:
    """将 LangGraph interrupt 转为扩展 actions 列表。"""
    actions = []
    raw_actions = []
    if isinstance(interrupt_data, list):
        raw_actions = interrupt_data
    elif isinstance(interrupt_data, dict):
        raw_actions = interrupt_data.get("actions") or [interrupt_data]

    for i, item in enumerate(raw_actions):
        if not isinstance(item, dict):
            continue
        tool_name = item.get("name") or item.get("tool_name") or "unknown"
        args = item.get("args") or item.get("arguments") or {}
        risk = assess_risk(tool_name, args)
        actions.append({
            "index": i,
            "tool_name": tool_name,
            "tool_call_id": item.get("id") or item.get("tool_call_id"),
            "tool_category": "builtin" if not tool_name.startswith("mcp_") else "mcp",
            "risk_level": risk,
            "args": args,
            "preview": build_action_preview(tool_name, args),
            "allowed_decisions": ["approve", "edit", "reject"],
        })
    return actions
