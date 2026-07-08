# common/schemas/message_schemas.py
"""
消息相关 Pydantic 模型定义
"""

from typing import Any, Dict, Optional, Union
from pydantic import BaseModel, Field

from common.enums.message_enums import MessageType, SenderType


class AgentMessage(BaseModel):
    """
    Agent 消息的统一数据结构。
    
    用于 Agent 流输出、WebSocket 推送、数据库存储等场景。
    """
    type: MessageType = Field(..., description="消息类型")
    sender: SenderType = Field(..., description="发送者类型")
    session_id: str = Field(..., description="会话 ID")
    content: Any = Field(..., description="消息内容，可以是字符串或字典")
    save: bool = Field(default=True, description="是否需要保存到数据库")
    conversation_id: Optional[str] = Field(default=None, description="会话轮次ID，同一轮用户提问及所有回复共享")
    group_id: int = Field(default=0, description="分组ID，同一处理阶段（模型推理/工具执行）的消息共享")
    step_index: int = Field(default=0, description="步骤序号，在一轮会话中的全局顺序")
    
    class Config:
        use_enum_values = True  # 序列化时使用枚举值而非枚举对象
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于 JSON 序列化或 WebSocket 发送）"""
        return self.model_dump()
    
    def to_frontend_dict(self) -> Dict[str, Any]:
        """转换为前端消息字典（不包含 save 字段）"""
        data = self.model_dump()
        data.pop("save", None)
        return data

    @classmethod
    def chat(
        cls,
        session_id: str,
        content: Any,
        sender: SenderType = SenderType.ASSISTANT,
        save: bool = True
    ) -> "AgentMessage":
        """创建聊天消息的便捷方法"""
        return cls(
            type=MessageType.CHAT,
            sender=sender,
            session_id=session_id,
            content=content,
            save=save
        )
    
    @classmethod
    def tool_call(
        cls,
        session_id: str,
        content: Dict[str, Any],
        save: bool = True
    ) -> "AgentMessage":
        """创建工具调用消息的便捷方法"""
        return cls(
            type=MessageType.TOOL_CALL,
            sender=SenderType.SYSTEM,
            session_id=session_id,
            content=content,
            save=save
        )
    
    @classmethod
    def tool_result(
        cls,
        session_id: str,
        content: Dict[str, Any],
        save: bool = True
    ) -> "AgentMessage":
        """创建工具结果消息的便捷方法"""
        return cls(
            type=MessageType.TOOL_RESULT,
            sender=SenderType.SYSTEM,
            session_id=session_id,
            content=content,
            save=save
        )
    
    @classmethod
    def status(
        cls,
        session_id: str,
        content: str,
        save: bool = True
    ) -> "AgentMessage":
        """创建状态消息的便捷方法"""
        return cls(
            type=MessageType.STATUS,
            sender=SenderType.SYSTEM,
            session_id=session_id,
            content=content,
            save=save
        )
    
    @classmethod
    def error(
        cls,
        session_id: str,
        content: str,
        save: bool = True
    ) -> "AgentMessage":
        """创建错误消息的便捷方法"""
        return cls(
            type=MessageType.ERROR,
            sender=SenderType.SYSTEM,
            session_id=session_id,
            content=content,
            save=save
        )
    
    @classmethod
    def end(
        cls,
        session_id: str,
        reason: str,
        save: bool = True
    ) -> "AgentMessage":
        """创建结束消息的便捷方法"""
        return cls(
            type=MessageType.END,
            sender=SenderType.SYSTEM,
            session_id=session_id,
            content=reason,
            save=save
        )

    @classmethod
    def run_metrics(
        cls,
        session_id: str,
        content: Dict[str, Any],
    ) -> "AgentMessage":
        """Deep run 结束指标（不存 chat_history）。"""
        return cls(
            type=MessageType.RUN_METRICS,
            sender=SenderType.SYSTEM,
            session_id=session_id,
            content=content,
            save=False,
        )
    
    @classmethod
    def interrupt(
        cls,
        session_id: str,
        content: Dict[str, Any],
        save: bool = True
    ) -> "AgentMessage":
        """创建中断消息的便捷方法"""
        return cls(
            type=MessageType.INTERRUPT,
            sender=SenderType.SYSTEM,
            session_id=session_id,
            content=content,
            save=save
        )
    
    @classmethod
    def llm_text_final(
        cls,
        session_id: str,
        content: Dict[str, Any],
        save: bool = True
    ) -> "AgentMessage":
        """创建 LLM 完整文本消息的便捷方法（仅存储）"""
        return cls(
            type=MessageType.LLM_TEXT_FINAL,
            sender=SenderType.ASSISTANT,
            session_id=session_id,
            content=content,
            save=save
        )
    
    @classmethod
    def session_allocated(
        cls,
        session_id: str,
        content: Dict[str, Any]
    ) -> "AgentMessage":
        """创建会话分配消息的便捷方法"""
        return cls(
            type=MessageType.SESSION_ALLOCATED,
            sender=SenderType.SYSTEM,
            session_id=session_id,
            content=content,
            save=False
        )

    @classmethod
    def session_title_updated(
        cls,
        session_id: str,
        content: Dict[str, Any]
    ) -> "AgentMessage":
        """创建会话标题更新消息（延迟生成后推送，不存库）。"""
        return cls(
            type=MessageType.SESSION_TITLE_UPDATED,
            sender=SenderType.SYSTEM,
            session_id=session_id,
            content=content,
            save=False
        )

    @classmethod
    def deep_message(
        cls,
        msg_type: MessageType,
        session_id: str,
        content: Dict[str, Any],
        *,
        save: bool = True,
    ) -> "AgentMessage":
        """Deep Agent 语义化消息工厂。"""
        return cls(
            type=msg_type,
            sender=SenderType.SYSTEM,
            session_id=session_id,
            content=content,
            save=save,
        )

    @classmethod
    def todo_update(cls, session_id: str, content: Dict[str, Any], save: bool = True) -> "AgentMessage":
        return cls.deep_message(MessageType.TODO_UPDATE, session_id, content, save=save)

    @classmethod
    def fs_operation(cls, session_id: str, content: Dict[str, Any], save: bool = True) -> "AgentMessage":
        return cls.deep_message(MessageType.FS_OPERATION, session_id, content, save=save)

    @classmethod
    def execute_op(cls, session_id: str, content: Dict[str, Any], save: bool = True) -> "AgentMessage":
        return cls.deep_message(MessageType.EXECUTE, session_id, content, save=save)

    @classmethod
    def execute_result(cls, session_id: str, content: Dict[str, Any], save: bool = True) -> "AgentMessage":
        return cls.deep_message(MessageType.EXECUTE_RESULT, session_id, content, save=save)

    @classmethod
    def subagent_start(cls, session_id: str, content: Dict[str, Any], save: bool = True) -> "AgentMessage":
        return cls.deep_message(MessageType.SUBAGENT_START, session_id, content, save=save)

    @classmethod
    def subagent_end(cls, session_id: str, content: Dict[str, Any], save: bool = True) -> "AgentMessage":
        return cls.deep_message(MessageType.SUBAGENT_END, session_id, content, save=save)

    @classmethod
    def deep_status(cls, session_id: str, content: Dict[str, Any], save: bool = False) -> "AgentMessage":
        return cls.deep_message(MessageType.DEEP_STATUS, session_id, content, save=save)
