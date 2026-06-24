"""Deep 工具调用写入 measure.tool_call_logs。"""

from deep_agent_server.events.models import DomainEvent, ToolCallCompleted
from db import storage


async def tool_logs_processor(event: DomainEvent) -> None:
    if not isinstance(event, ToolCallCompleted):
        return
    if not event.session_id or not event.user_id:
        return

    payload = event.payload or {}
    tool_call_id = payload.get("tool_call_id")
    tool_name = payload.get("tool_name")
    if not tool_call_id or not tool_name:
        return

    tool_result = payload.get("tool_result") or payload.get("summary") or ""
    if not isinstance(tool_result, str):
        tool_result = str(tool_result)

    status = payload.get("status") or "success"
    if payload.get("is_error"):
        status = "fail"

    await storage.insert_tool_call_log(
        session_id=event.session_id,
        user_id=event.user_id,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        tool_args=payload.get("args") or {},
        tool_result=tool_result,
        status=status,
        run_id=event.run_id,
        conversation_id=event.conversation_id,
        duration_ms=payload.get("duration_ms"),
    )
