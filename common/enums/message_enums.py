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
    SESSION_WARMING = "session_warming"    # 会话预热中
    SESSION_READY = "session_ready"        # 会话就绪
    SESSION_TITLE_UPDATED = "session_title_updated"  # 会话标题更新（延迟生成）
    MESSAGE_QUEUED = "message_queued"      # 消息已排队
    REASONING = "reasoning"          # 推理状态通知（告知前端正在推理中）
    REASONING_FINAL = "reasoning_final"  # 推理完整文本（存储 + 推送）
    # Deep Agent 语义化类型
    TODO_UPDATE = "todo_update"
    FS_OPERATION = "fs_operation"
    EXECUTE = "execute"
    EXECUTE_RESULT = "execute_result"
    SKILL_CATALOG = "skill_catalog"
    SKILL_LOADED = "skill_loaded"
    SUBAGENT_START = "subagent_start"
    SUBAGENT_EVENT = "subagent_event"
    SUBAGENT_END = "subagent_end"
    WORKSPACE_ARTIFACT = "workspace_artifact"
    WORKSPACE_FILE = "workspace_file"  # 本轮 conversation Agent 写入的工作区文件列表
    SANDBOX_STATUS = "sandbox_status"  # 沙箱容量与 session 列表（WebSocket 推送）
    RUN_METRICS = "run_metrics"  # Deep run 结束指标（token + 耗时，仅推送前端）
    TOOL_ARGS_STREAM = "tool_args_stream"  # 工具调用参数的流式增量（write_file/edit_file 的 content 逐 chunk 推送）
    CONTEXT_COMPRESSING = "context_compressing"    # 上下文压缩中（前端展示进度提示）
    CONTEXT_COMPRESSED = "context_compressed"      # 上下文压缩完成


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
