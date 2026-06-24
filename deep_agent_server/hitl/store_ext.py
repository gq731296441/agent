"""Deep HITL store 扩展 — preview / risk_level。

在基础 hitl_store.save_interrupt 之上补充 Deep 模式特有的
preview 文本与 risk_level 字段，runtime 标记为 "deep"。
"""

from typing import Any, Dict, List, Optional

from utils.hitl_store import save_interrupt as _base_save_interrupt


def assess_risk_level(action: Dict[str, Any]) -> str:
    """根据工具名与参数评估风险等级。"""
    name = (action.get("tool_name") or action.get("name") or "").lower()
    args = action.get("args") or action.get("arguments") or {}
    args_str = str(args).lower()
    if name == "execute" or "rm -rf" in args_str:
        return "high"
    if name in ("write_file", "edit_file") or name.startswith("mcp_"):
        return "medium"
    return "low"


def build_action_preview(action: Dict[str, Any]) -> str:
    """生成 HITL 预览文本。"""
    name = action.get("tool_name") or action.get("name") or "unknown"
    args = action.get("args") or action.get("arguments") or {}
    preview = f"{name}({args})"
    return preview[:500]


async def save_deep_interrupt(
    store,
    *,
    request_id: str,
    thread_id: str,
    session_id: str,
    user_id: str,
    actions: List[Dict[str, Any]],
    num_pending: int,
    run_id: str = None,
    conversation_id: str = None,
    step_index: int = None,
) -> None:
    """保存 Deep interrupt，附带 preview 与 risk_level。"""
    enriched = []
    for action in actions:
        item = dict(action)
        item.setdefault("preview", build_action_preview(action))
        item.setdefault("risk_level", assess_risk_level(action))
        enriched.append(item)

    await _base_save_interrupt(
        store,
        request_id=request_id,
        thread_id=thread_id,
        session_id=session_id,
        user_id=user_id,
        actions=enriched,
        num_pending=num_pending,
        runtime="deep",
        run_id=run_id,
        conversation_id=conversation_id,
        step_index=step_index,
    )
