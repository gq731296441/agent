"""审计日志处理器。

订阅 EventBus，将 skill_loaded / hitl_requested / sandbox 等事件写入 audit_log 表。
"""

from deep_agent_server.events.models import DomainEvent, HitlRequested, SandboxPaused, SandboxResumed, SkillLoaded
from db import deep_storage


async def audit_processor(event: DomainEvent) -> None:
    if not event.user_id:
        return
    if isinstance(event, SkillLoaded):
        await deep_storage.insert_audit_log(
            event.user_id,
            "skill_loaded",
            session_id=event.session_id,
            run_id=event.run_id,
            target=event.payload.get("skill_name"),
            detail=event.payload,
        )
    elif isinstance(event, HitlRequested):
        await deep_storage.insert_audit_log(
            event.user_id,
            "hitl_requested",
            session_id=event.session_id,
            run_id=event.run_id,
            detail={"action_count": len(event.payload.get("actions") or [])},
        )
    elif isinstance(event, SandboxPaused):
        await deep_storage.insert_audit_log(
            event.user_id, "sandbox_paused", target=event.payload.get("container_id")
        )
    elif isinstance(event, SandboxResumed):
        await deep_storage.insert_audit_log(
            event.user_id, "sandbox_resumed", target=event.payload.get("container_id")
        )
