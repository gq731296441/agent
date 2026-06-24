"""Deep Agent HITL 中断解析。

LangGraph deepagents 在 interrupt_on 命中时通过 updates['__interrupt__']
或 StateSnapshot.interrupts 传递 action_requests，本模块解析并构造
前端 INTERRUPT 消息 content（含 risk_level / preview / hitl_status）。
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from deep_agent_server.hitl.rules import assess_risk, build_action_preview


def stable_hitl_tool_call_id(tool_name: str, args: Any, index: int) -> str:
    """LangGraph action_requests 缺 id 时生成稳定 tool_call_id（按 index+args 哈希）。"""
    try:
        payload = json.dumps(
            {"tool_name": tool_name, "args": args or {}, "index": index},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
    except (TypeError, ValueError):
        payload = f"{tool_name}:{index}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"hitl_{digest}"


def ensure_action_tool_call_ids(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """为无 id 的 action 补全 tool_call_id。"""
    for i, item in enumerate(actions):
        if not item.get("tool_call_id"):
            tool_name = item.get("tool_name") or item.get("name") or "unknown"
            args = item.get("args") or item.get("arguments") or {}
            item["tool_call_id"] = stable_hitl_tool_call_id(tool_name, args, i)
    return actions


def parse_hitl_actions_from_interrupts(interrupt_info: Any) -> List[Dict[str, Any]]:
    """从 updates['__interrupt__'] 或 StateSnapshot.interrupts 解析待审批 actions。"""
    if not interrupt_info:
        return []

    items = interrupt_info if isinstance(interrupt_info, (list, tuple)) else [interrupt_info]
    all_actions: List[Dict[str, Any]] = []

    for interrupt in items:
        if hasattr(interrupt, "value"):
            interrupt_value = interrupt.value
        else:
            interrupt_value = interrupt

        if not isinstance(interrupt_value, dict):
            continue

        action_requests = interrupt_value.get("action_requests") or []
        review_configs = interrupt_value.get("review_configs") or []

        for req in action_requests:
            tool_name = req.get("name") or req.get("tool_name") or "unknown"
            args = req.get("args") or req.get("arguments") or {}
            description = req.get("description")
            allowed_decisions: List[str] = []

            for cfg in review_configs:
                if cfg.get("action_name") == tool_name:
                    allowed_decisions = cfg.get("allowed_decisions") or []
                    break

            all_actions.append({
                "tool_name": tool_name,
                "args": args,
                "description": description,
                "allowed_decisions": allowed_decisions or ["approve", "edit", "reject"],
                "tool_call_id": req.get("id") or req.get("tool_call_id"),
            })

    return ensure_action_tool_call_ids(all_actions)


def finalize_actions_for_frontend(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """为前端 HITL 卡片补充 risk / preview 等字段。"""
    actions = ensure_action_tool_call_ids(list(actions))
    result = []
    for i, item in enumerate(actions):
        tool_name = item.get("tool_name") or item.get("name") or "unknown"
        args = item.get("args") or {}
        result.append({
            "index": i,
            "tool_name": tool_name,
            "tool_call_id": item.get("tool_call_id"),
            "tool_category": "builtin" if not str(tool_name).startswith("mcp_") else "mcp",
            "risk_level": assess_risk(tool_name, args),
            "args": args,
            "preview": build_action_preview(tool_name, args),
            "allowed_decisions": item.get("allowed_decisions") or ["approve", "edit", "reject"],
            "description": item.get("description"),
        })
    return result


def build_interrupt_content(
    actions: List[Dict[str, Any]],
    *,
    run_id: str,
    conversation_id: str,
) -> Dict[str, Any]:
    """构造 INTERRUPT 消息 content。"""
    finalized = finalize_actions_for_frontend(actions)
    return {
        "request_id": str(uuid.uuid4()),
        "agent_mode": "deep",
        "runtime": "deep",
        "run_id": run_id,
        "conversation_id": conversation_id,
        "total_count": len(finalized),
        "actions": finalized,
        "hitl_status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
