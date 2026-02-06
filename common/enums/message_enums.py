# common/enums/message_enums.py
"""
消息相关枚举定义
"""

from enum import Enum


class MessageType(str, Enum):
    """消息类型枚举"""
    CHAT = "chat"                    # 聊天消息（用户/助手）
    TOOL_CALL = "tool_call"          # 工具调用请求
    TOOL_RESULT = "tool_result"      # 工具执行结果
    INTERRUPT = "interrupt"          # HITL 中断请求
    STATUS = "status"                # 状态信息
    END = "end"                      # 流结束信号
    ERROR = "error"                  # 错误消息
    LLM_TEXT_FINAL = "llm_text_final"  # LLM 完整文本（仅存储，不推送）
    SESSION_ALLOCATED = "session_allocated"  # 会话分配通知
    REASONING = "reasoning"          # 推理内容流式输出
    REASONING_FINAL = "reasoning_final"  # 推理完整文本（仅存储，不推送）


class SenderType(str, Enum):
    """发送者类型枚举"""
    USER = "user"           # 用户
    ASSISTANT = "assistant" # AI 助手
    SYSTEM = "system"       # 系统


class EndReason(str, Enum):
    """结束原因枚举"""
    COMPLETED = "completed"  # 正常完成
    STOP = "stop"            # 用户停止或异常停止
    ERROR = "error"          # 错误终止


class ActionType(str, Enum):
    """用户动作类型枚举"""
    ASK = "ask"                  # 普通提问
    RESUME_HITL = "resume_hitl"  # human-in-the-loop 人工介入
    STOP = "stop"                # 停止生成


class DecisionType(str, Enum):
    """HITL 决策类型枚举"""
    APPROVE = "approve"  # 批准
    REJECT = "reject"    # 拒绝
    EDIT = "edit"        # 编辑
