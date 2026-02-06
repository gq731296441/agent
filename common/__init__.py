# common/__init__.py
"""
公共模块 - 包含枚举、常量、模式定义等
"""

from common.enums import MessageType, SenderType, EndReason, ActionType
from common.schemas import AgentMessage

__all__ = [
    "MessageType",
    "SenderType", 
    "EndReason",
    "ActionType",
    "AgentMessage",
]
