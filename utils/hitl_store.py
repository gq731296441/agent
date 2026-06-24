"""HITL 人工审批状态持久化（基于 LangGraph AsyncPostgresStore）。"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from config import get_logger
from config.config import app_config

from deep_agent_server.store.namespaces import HITL_PENDING

logger = get_logger(__name__)

HITL_NAMESPACE = HITL_PENDING


def _is_expired(value: Dict[str, Any]) -> bool:
    expires_at = value.get("expires_at")
    if not expires_at:
        return False
    try:
        exp = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= exp
    except (TypeError, ValueError):
        return False


async def save_interrupt(
    store,
    *,
    request_id: str,
    thread_id: str,
    session_id: str,
    user_id: str,
    actions: List[Dict[str, Any]],
    num_pending: int,
    runtime: str = "deep",
    run_id: str = None,
    conversation_id: str = None,
    step_index: int = None,
) -> None:
    if store is None:
        logger.warning("[HITL] store 未配置，无法持久化 interrupt")
        return

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=app_config.HITL_PENDING_TTL_MINUTES)
    payload = {
        "thread_id": thread_id,
        "session_id": session_id,
        "user_id": user_id,
        "actions": actions,
        "num_pending": num_pending,
        "runtime": runtime,
        "run_id": run_id,
        "conversation_id": conversation_id,
        "step_index": step_index,
        "request_id": request_id,
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    await store.aput(HITL_NAMESPACE, request_id, payload)
    logger.info(f"[HITL] 已保存 interrupt request_id={request_id}, pending={num_pending}")


async def get_interrupt(
    store,
    request_id: str,
    *,
    user_id: str,
    session_id: str,
) -> Optional[Dict[str, Any]]:
    """读取 pending interrupt（不删除）。"""
    if store is None or not request_id:
        return None

    item = await store.aget(HITL_NAMESPACE, request_id)
    if not item:
        return None

    value = item.value if hasattr(item, "value") else item
    if not isinstance(value, dict):
        return None

    if value.get("user_id") != user_id or value.get("session_id") != session_id:
        return None
    return value


async def pop_interrupt(
    store,
    request_id: str,
    *,
    user_id: str,
    session_id: str,
) -> Optional[Dict[str, Any]]:
    if store is None or not request_id:
        return None

    value = await get_interrupt(
        store, request_id, user_id=user_id, session_id=session_id
    )
    if not value:
        return None

    if _is_expired(value):
        try:
            await store.adelete(HITL_NAMESPACE, request_id)
        except Exception as e:
            logger.warning(f"[HITL] 删除过期 interrupt 失败: {e}")
        return None

    try:
        await store.adelete(HITL_NAMESPACE, request_id)
    except Exception as e:
        logger.warning(f"[HITL] 删除 interrupt 记录失败: {e}")

    return value


def build_hitl_decisions(user_decisions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """将前端 decisions 转为 LangGraph resume 格式。"""
    from common.enums import DecisionType

    decisions = []
    for dec in user_decisions:
        dec_type = dec["type"]
        if dec_type in (DecisionType.APPROVE, DecisionType.APPROVE.value):
            decisions.append({"type": dec_type})
        elif dec_type in (DecisionType.REJECT, DecisionType.REJECT.value):
            decisions.append({
                "type": dec_type,
                "message": "用户拒绝该工具的调用，无需再次发起调用请求",
            })
        elif dec_type in (DecisionType.EDIT, DecisionType.EDIT.value):
            decisions.append({
                "type": dec_type,
                "edited_action": dec["edited_action"],
            })
    return decisions


def build_hitl_history_decisions(
    actions: List[Dict[str, Any]],
    user_decisions: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    将用户决策写入 chat_history 的 decisions 数组。

    一次 interrupt 可含多个待审批 action；decisions 与 actions 按 index 一一对齐，
    不拆成多条 history 记录。
    """
    from common.enums import DecisionType

    records: List[Dict[str, Any]] = []
    for i, dec in enumerate(user_decisions):
        action = actions[i] if i < len(actions) else {}
        dec_type = dec.get("type", "approve")
        entry: Dict[str, Any] = {
            "index": i,
            "type": dec_type,
            "tool_name": action.get("tool_name") or action.get("name") or "unknown",
            "tool_call_id": action.get("tool_call_id"),
        }
        if dec_type in (DecisionType.EDIT, DecisionType.EDIT.value):
            edited = dec.get("edited_action")
            if edited:
                entry["edited_action"] = edited
        records.append(entry)
    return records
