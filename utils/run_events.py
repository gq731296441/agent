"""Agent 运行事件记录（measure.agent_run_events）。"""

from typing import Optional
from uuid import UUID

from db.database import db_manager
from config import get_logger

logger = get_logger(__name__)


async def insert_agent_run_event(
    *,
    run_id: str,
    session_id: str,
    user_id: str,
    event_type: str,
    conversation_id: Optional[str] = None,
    category: Optional[str] = None,
    action: Optional[str] = None,
    message: Optional[str] = None,
) -> None:
    """写入 Agent 运行事件，失败时仅记录 warning。"""
    try:
        pool = await db_manager.get_pool()
        cid = UUID(conversation_id) if conversation_id else None
        sid = UUID(session_id) if isinstance(session_id, str) else session_id
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO measure.agent_run_events
                    (run_id, session_id, user_id, conversation_id, event_type, category, action, message)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                run_id,
                sid,
                user_id,
                cid,
                event_type,
                category,
                action,
                (message or "")[:2000],
            )
    except Exception as e:
        logger.warning(f"[RunEvents] 写入失败 run_id={run_id} event={event_type}: {e}")
