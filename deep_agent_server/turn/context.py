"""Turn Context — 同一 user 提问内复用 run_id / conversation_id / step_index。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from config import get_logger
from deep_agent_server.store.namespaces import TURN_ACTIVE

logger = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def load_turn(store, session_id: str) -> Optional[Dict[str, Any]]:
    if store is None or not session_id:
        return None
    try:
        item = await store.aget(TURN_ACTIVE, session_id)
    except Exception as e:
        logger.warning(f"[TurnContext] load 失败 session={session_id}: {e}")
        return None
    if not item:
        return None
    value = item.value if hasattr(item, "value") else item
    return value if isinstance(value, dict) else None


async def start_turn(store, session_id: str, user_id: str) -> Dict[str, Any]:
    """新 ask：清除旧 turn 并分配新 run_id / conversation_id。"""
    await finish_turn(store, session_id)
    payload = {
        "run_id": str(uuid.uuid4()),
        "conversation_id": str(uuid.uuid4()),
        "step_index": 0,
        "status": "running",
        "started_at": _now_iso(),
        "user_id": user_id,
    }
    if store is not None:
        await store.aput(TURN_ACTIVE, session_id, payload)
    return payload


async def update_turn_step(store, session_id: str, step_index: int) -> None:
    if store is None or not session_id:
        return
    turn = await load_turn(store, session_id)
    if not turn:
        return
    turn["step_index"] = int(step_index)
    try:
        await store.aput(TURN_ACTIVE, session_id, turn)
    except Exception as e:
        logger.warning(f"[TurnContext] update step 失败 session={session_id}: {e}")


async def mark_awaiting_hitl(store, session_id: str, step_index: int) -> None:
    if store is None or not session_id:
        return
    turn = await load_turn(store, session_id)
    if not turn:
        return
    turn["status"] = "awaiting_hitl"
    turn["step_index"] = int(step_index)
    try:
        await store.aput(TURN_ACTIVE, session_id, turn)
    except Exception as e:
        logger.warning(f"[TurnContext] mark awaiting_hitl 失败 session={session_id}: {e}")


async def mark_resuming(store, session_id: str) -> None:
    if store is None or not session_id:
        return
    turn = await load_turn(store, session_id)
    if not turn:
        return
    turn["status"] = "running"
    try:
        await store.aput(TURN_ACTIVE, session_id, turn)
    except Exception as e:
        logger.warning(f"[TurnContext] mark resuming 失败 session={session_id}: {e}")


def resolve_resume_step_index(
    turn: Optional[Dict[str, Any]],
    resume_ctx: Optional[Dict[str, Any]],
) -> int:
    """HITL resume 时取 turn 与 interrupt 快照中的较大 step_index，避免回退。"""
    turn_step = int((turn or {}).get("step_index") or 0)
    ctx_step = int((resume_ctx or {}).get("step_index") or 0)
    return max(turn_step, ctx_step)


async def finish_turn(store, session_id: str) -> None:
    if store is None or not session_id:
        return
    try:
        await store.adelete(TURN_ACTIVE, session_id)
    except Exception as e:
        logger.debug(f"[TurnContext] finish_turn delete 忽略 session={session_id}: {e}")
