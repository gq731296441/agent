"""HITL 审批 action 与实际工具执行 tool_call_id 关联。"""

from typing import Any, Dict, List, Optional, Set


def action_matches_tool(
    action: Dict[str, Any],
    tool_name: str,
    tool_input: dict,
) -> bool:
    """判断 resume 后发出的工具 start 是否对应某条 interrupt action。"""
    if not action:
        return False
    an = action.get("tool_name") or action.get("name") or ""
    if tool_name and an != tool_name:
        return False
    args = action.get("args") or action.get("arguments") or {}
    tin = tool_input or {}
    if tool_name == "execute":
        cmd = args.get("command")
        return bool(cmd and tin.get("command") == cmd)
    if tool_name == "ls":
        p = args.get("path") or args.get("file_path") or ""
        tp = tin.get("path") or tin.get("file_path") or ""
        return p == tp
    if tool_name in ("read_file", "write_file", "edit_file"):
        fp = args.get("file_path") or args.get("path") or ""
        tfp = tin.get("file_path") or tin.get("path") or ""
        return fp == tfp
    if args and tin:
        return args == tin
    return True


def match_hitl_action_index(
    actions: List[Dict[str, Any]],
    tool_name: str,
    tool_input: dict,
    used_indices: Set[int],
    decisions: Optional[List[Dict[str, Any]]] = None,
) -> Optional[int]:
    """
    为 resume 后实际执行的工具匹配 interrupt action 下标。

    优先按 args 精确匹配；否则在同名工具中取第一个未使用的已批准项。
    """
    decisions = decisions or []
    exact: List[int] = []
    for i, action in enumerate(actions):
        if i in used_indices:
            continue
        if i < len(decisions) and decisions[i].get("type") == "reject":
            continue
        if action_matches_tool(action, tool_name, tool_input):
            exact.append(i)
    if len(exact) == 1:
        return exact[0]
    if exact:
        return exact[0]

    for i, action in enumerate(actions):
        if i in used_indices:
            continue
        if i < len(decisions) and decisions[i].get("type") == "reject":
            continue
        an = action.get("tool_name") or action.get("name") or ""
        if an == tool_name:
            return i
    return None


def needs_tool_call_id_backfill(tool_call_id: Optional[str]) -> bool:
    """interrupt 阶段是否为 synthetic hitl id（需 resume 后回填真实 call_*）。"""
    if not tool_call_id:
        return True
    tid = str(tool_call_id)
    if tid.startswith("hitl_"):
        return True
    return False
