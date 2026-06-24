"""HITL pending 过期扫描 — 超时自动 terminate run。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from config import get_logger
from config.config import app_config
from db import deep_storage, storage
from deep_agent_server.store.namespaces import HITL_PENDING
from deep_agent_server.turn.context import finish_turn
from utils.hitl_store import HITL_NAMESPACE
from utils.run_events import insert_agent_run_event

logger = get_logger(__name__)

_ws_send: Optional[Callable[[dict, str], Awaitable[None]]] = None
_sweeper_task: Optional[asyncio.Task] = None


def set_hitl_ws_notifier(fn: Callable[[dict, str], Awaitable[None]]) -> None:
    global _ws_send
    _ws_send = fn


def _parse_iso(value: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


async def expire_hitl_pending(store, request_id: str, value: dict) -> None:
    """单条 pending 过期处理。"""
    session_id = value.get("session_id") or ""
    user_id = value.get("user_id") or ""
    run_id = value.get("run_id") or ""
    conversation_id = value.get("conversation_id")

    try:
        await store.adelete(HITL_NAMESPACE, request_id)
    except Exception as e:
        logger.warning(f"[HITLSweeper] 删除 pending 失败 request_id={request_id}: {e}")

    if session_id and request_id:
        await storage.resolve_interrupt_in_history(
            session_id,
            request_id,
            decisions=[],
            hitl_status="expired",
        )

    if run_id:
        await deep_storage.update_agent_run(
            run_id,
            "expired",
            error="hitl_timeout",
        )
        await insert_agent_run_event(
            run_id=run_id,
            session_id=session_id,
            user_id=user_id,
            conversation_id=conversation_id,
            event_type="hitl_expired",
            message=str(request_id or "")[:2000],
        )
        await insert_agent_run_event(
            run_id=run_id,
            session_id=session_id,
            user_id=user_id,
            conversation_id=conversation_id,
            event_type="turn_end",
            category="expired",
        )

    if session_id:
        await finish_turn(store, session_id)

    if _ws_send and session_id:
        await _ws_send(
            {
                "type": "hitl_expired",
                "session_id": session_id,
                "content": {
                    "request_id": request_id,
                    "run_id": run_id,
                    "reason": "timeout",
                },
            },
            session_id,
        )

    logger.info(f"[HITLSweeper] 已过期 request_id={request_id} session={session_id}")


async def sweep_expired_hitl(store) -> int:
    if store is None:
        return 0
    now = datetime.now(timezone.utc)
    expired_count = 0
    try:
        items = await store.asearch(HITL_PENDING, limit=200)
    except Exception as e:
        logger.warning(f"[HITLSweeper] asearch 失败: {e}")
        return 0

    for item in items or []:
        value = item.value if hasattr(item, "value") else item
        if not isinstance(value, dict):
            continue
        request_id = getattr(item, "key", None) or value.get("request_id")
        if not request_id:
            continue
        expires_at = _parse_iso(value.get("expires_at") or "")
        if not expires_at or now < expires_at:
            continue
        await expire_hitl_pending(store, str(request_id), value)
        expired_count += 1
    return expired_count


async def _sweeper_loop(store) -> None:
    interval = max(5, int(app_config.HITL_SWEEPER_INTERVAL_SECONDS or 60))
    while True:
        try:
            count = await sweep_expired_hitl(store)
            if count:
                logger.info(f"[HITLSweeper] 本轮过期 {count} 条")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[HITLSweeper] 扫描异常: {e}")
        await asyncio.sleep(interval)


async def start_hitl_sweeper(store) -> None:
    global _sweeper_task
    if not app_config.HITL_SWEEPER_INTERVAL_SECONDS:
        logger.info("[HITLSweeper] 已禁用 (HITL_SWEEPER_INTERVAL_SECONDS=0)")
        return
    if _sweeper_task and not _sweeper_task.done():
        return
    _sweeper_task = asyncio.create_task(_sweeper_loop(store))
    logger.info("[HITLSweeper] 已启动")


async def stop_hitl_sweeper() -> None:
    global _sweeper_task
    if _sweeper_task and not _sweeper_task.done():
        _sweeper_task.cancel()
        try:
            await _sweeper_task
        except asyncio.CancelledError:
            pass
    _sweeper_task = None
