"""内部 Event Bus 领域事件。

所有 DomainEvent 共享 event_id / timestamp / user_id / session_id / run_id 等字段，
具体子类通过 event_type 区分（run_started、tool_call_started 等）。
"""

from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def _now():
    return datetime.now(timezone.utc)


class DomainEvent(BaseModel):
    """EventBus 基础事件模型。"""
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: str
    timestamp: datetime = Field(default_factory=_now)
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    run_id: Optional[str] = None
    conversation_id: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class RunStarted(DomainEvent):
    event_type: Literal["run_started"] = "run_started"


class RunCompleted(DomainEvent):
    event_type: Literal["run_completed"] = "run_completed"


class RunFailed(DomainEvent):
    event_type: Literal["run_failed"] = "run_failed"


class ToolCallStarted(DomainEvent):
    event_type: Literal["tool_call_started"] = "tool_call_started"


class ToolCallCompleted(DomainEvent):
    event_type: Literal["tool_call_completed"] = "tool_call_completed"


class HitlRequested(DomainEvent):
    event_type: Literal["hitl_requested"] = "hitl_requested"


class SkillLoaded(DomainEvent):
    event_type: Literal["skill_loaded"] = "skill_loaded"


class SubagentStarted(DomainEvent):
    event_type: Literal["subagent_started"] = "subagent_started"


class SubagentCompleted(DomainEvent):
    event_type: Literal["subagent_completed"] = "subagent_completed"


class SandboxPaused(DomainEvent):
    event_type: Literal["sandbox_paused"] = "sandbox_paused"


class SandboxResumed(DomainEvent):
    event_type: Literal["sandbox_resumed"] = "sandbox_resumed"
