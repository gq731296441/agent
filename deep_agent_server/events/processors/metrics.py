"""运行指标处理器。

订阅 EventBus，将 run 生命周期事件写入 agent_run_events 表（不含工具明细）。
"""

from deep_agent_server.events.models import (
    DomainEvent,
    RunCompleted,
    RunFailed,
)
from utils.run_events import insert_agent_run_event


async def metrics_processor(event: DomainEvent) -> None:
    if not event.run_id or not event.session_id or not event.user_id:
        return

    if isinstance(event, RunCompleted):
        reason = str(event.payload.get("reason") or "completed").lower()
        category = "completed"
        if reason in ("stop", "stopped"):
            category = "stopped"
        await insert_agent_run_event(
            run_id=event.run_id,
            session_id=event.session_id,
            user_id=event.user_id,
            conversation_id=event.conversation_id,
            event_type="turn_end",
            category=category,
            message=event.payload.get("reason"),
        )
    elif isinstance(event, RunFailed):
        await insert_agent_run_event(
            run_id=event.run_id,
            session_id=event.session_id,
            user_id=event.user_id,
            conversation_id=event.conversation_id,
            event_type="turn_end",
            category="error",
            message=event.payload.get("message") or event.payload.get("reason"),
        )
