"""AgentMessage → DomainEvent 规范化。

将前端 AgentMessage 映射为内部 DomainEvent，供 audit / metrics 处理器消费。
仅关键生命周期事件（tool_call、interrupt、run_end 等）会被规范化。
"""

from typing import Optional

from common.enums import MessageType
from deep_agent_server.events.models import (
    DomainEvent,
    HitlRequested,
    RunCompleted,
    RunFailed,
    SkillLoaded,
    SubagentCompleted,
    SubagentStarted,
    ToolCallCompleted,
    ToolCallStarted,
)


def normalize_agent_message(msg: dict) -> Optional[DomainEvent]:
    """从 AgentMessage dict 提取 DomainEvent；无关类型返回 None。"""
    if not msg:
        return None
    msg_type = msg.get("type")
    session_id = msg.get("session_id")
    user_id = msg.get("user_id")
    run_id = msg.get("run_id")
    conversation_id = msg.get("conversation_id")
    content = msg.get("content") if isinstance(msg.get("content"), dict) else {}

    base = dict(
        user_id=user_id,
        session_id=session_id,
        run_id=run_id,
        conversation_id=conversation_id,
    )

    if msg_type in (MessageType.TOOL_CALL, MessageType.TOOL_CALL.value):
        return ToolCallStarted(
            **base,
            payload={
                "tool_name": content.get("tool_name"),
                "tool_call_id": content.get("tool_call_id"),
                "args": content.get("args"),
            },
        )

    if msg_type in (
        MessageType.TOOL_RESULT,
        MessageType.TOOL_RESULT.value,
        MessageType.EXECUTE_RESULT,
        MessageType.EXECUTE_RESULT.value,
        MessageType.FS_OPERATION,
        MessageType.FS_OPERATION.value,
    ):
        if content.get("tool_result") is not None or msg_type in (
            MessageType.TOOL_RESULT,
            MessageType.TOOL_RESULT.value,
            MessageType.EXECUTE_RESULT,
            MessageType.EXECUTE_RESULT.value,
        ):
            return ToolCallCompleted(
                **base,
                payload={
                    "tool_name": content.get("tool_name"),
                    "tool_call_id": content.get("tool_call_id"),
                    "truncated": content.get("truncated", False),
                    "tool_result": content.get("tool_result") or content.get("summary") or "",
                    "args": content.get("args") or {},
                    "duration_ms": content.get("duration_ms"),
                    "is_error": content.get("status") == "error",
                },
            )

    if msg_type in (MessageType.INTERRUPT, MessageType.INTERRUPT.value):
        return HitlRequested(**base, payload={"actions": content.get("actions", [])})

    if msg_type in (MessageType.SKILL_LOADED, MessageType.SKILL_LOADED.value):
        return SkillLoaded(**base, payload=content)

    if msg_type in (MessageType.SUBAGENT_START, MessageType.SUBAGENT_START.value):
        return SubagentStarted(
            **base,
            payload={
                "subagent_run_id": content.get("subagent_run_id"),
                "name": content.get("name"),
                "input_summary": content.get("input_summary"),
                "status": content.get("status", "running"),
            },
        )

    if msg_type in (MessageType.SUBAGENT_END, MessageType.SUBAGENT_END.value):
        return SubagentCompleted(
            **base,
            payload={
                "subagent_run_id": content.get("subagent_run_id"),
                "name": content.get("name"),
                "final_report": content.get("final_report"),
                "status": content.get("status", "completed"),
            },
        )

    if msg_type in (MessageType.END, MessageType.END.value):
        reason = msg.get("content")
        if reason in ("error", "ERROR") or str(reason).lower() == "error":
            return RunFailed(**base, payload={"reason": str(reason)})
        return RunCompleted(**base, payload={"reason": str(reason)})

    if msg_type in (MessageType.ERROR, MessageType.ERROR.value):
        return RunFailed(**base, payload={"message": str(msg.get("content", ""))})

    return None


def make_run_started(
    *,
    user_id: str,
    session_id: str,
    run_id: str,
    conversation_id: str,
) -> DomainEvent:
    from deep_agent_server.events.models import RunStarted

    return RunStarted(
        user_id=user_id,
        session_id=session_id,
        run_id=run_id,
        conversation_id=conversation_id,
    )
