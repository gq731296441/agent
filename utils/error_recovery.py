"""错误分类与恢复策略模块

将 Agent 运行中的各种异常分类为不同的 ErrorCategory，
并为每种错误类型提供对应的 RecoveryAction（恢复策略）。

核心设计：
- ErrorCategory: 错误分类枚举（RATE_LIMIT / BAD_REQUEST / SERVER_ERROR / TOOL_ERROR / UNKNOWN）
- RecoveryAction: 恢复动作枚举（RETRY / FEEDBACK_AND_CONTINUE / DEGRADE / ABORT）
- ErrorClassifier: 错误分类器，检查异常链判断错误类型
- classify_error(): 主入口函数，返回 (category, action, user_message, model_hint)

与 LangGraph 消息序列修复的协作：
  错误发生 → classify_error() 分类
  → 注入 AIMessage 到 checkpoint（修复 user→user 断裂的消息序列）
  → 根据 action 执行恢复策略（重试 / 反馈模型 / 降级 / 终止）
"""

from enum import Enum
from typing import Tuple

from config import get_logger

logger = get_logger(__name__)


class ErrorCategory(str, Enum):
    """错误分类"""
    RATE_LIMIT = "rate_limit"           # 429 速率限制
    BAD_REQUEST = "bad_request"         # 400 请求格式/参数错误
    AUTH_ERROR = "auth_error"           # 401/403 认证/权限错误
    SERVER_ERROR = "server_error"       # 500/502/503 服务端错误
    TIMEOUT = "timeout"                 # 超时
    TOOL_ERROR = "tool_error"           # 工具执行失败
    CONTEXT_OVERFLOW = "context_overflow"  # 上下文长度超限
    UNKNOWN = "unknown"                 # 未知错误


class RecoveryAction(str, Enum):
    """恢复动作"""
    RETRY = "retry"                     # 可重试（同一请求直接重试）
    FEEDBACK_AND_CONTINUE = "feedback"  # 反馈给模型，让模型自我修正后继续
    DEGRADE = "degrade"                 # 降级处理（如上下文截断后重试）
    ABORT = "abort"                     # 终止，告知用户


class ErrorClassification:
    """错误分类结果"""
    __slots__ = ("category", "action", "user_message", "model_hint", "retryable")

    def __init__(
        self,
        category: ErrorCategory,
        action: RecoveryAction,
        user_message: str,
        model_hint: str,
        retryable: bool = False,
    ):
        self.category = category
        self.action = action
        self.user_message = user_message
        self.model_hint = model_hint
        self.retryable = retryable

    def __repr__(self) -> str:
        return (
            f"ErrorClassification(category={self.category.value}, "
            f"action={self.action.value}, retryable={self.retryable})"
        )


def _check_exception_chain(exc: Exception, checker) -> bool:
    """递归检查异常链（包含 __cause__ 和 __context__）。"""
    if checker(exc):
        return True
    cause = getattr(exc, "__cause__", None)
    if cause is not None and cause is not exc:
        if _check_exception_chain(cause, checker):
            return True
    context = getattr(exc, "__context__", None)
    if context is not None and context is not exc and context is not cause:
        if _check_exception_chain(context, checker):
            return True
    return False


def _get_status_code(exc: Exception) -> int | None:
    """尝试从异常中提取 HTTP 状态码。"""
    # openai 异常有 status_code 属性
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    # httpx 异常可能嵌套 response
    resp = getattr(exc, "response", None)
    if resp is not None:
        sc = getattr(resp, "status_code", None)
        if isinstance(sc, int):
            return sc
    return None


def _extract_status_from_chain(exc: Exception) -> int | None:
    """从异常链中提取第一个可用的 HTTP 状态码。"""
    code = _get_status_code(exc)
    if code is not None:
        return code
    cause = getattr(exc, "__cause__", None)
    if cause is not None and cause is not exc:
        code = _extract_status_from_chain(cause)
        if code is not None:
            return code
    return None


def classify_error(exc: Exception) -> ErrorClassification:
    """对异常进行分类，返回分类结果和恢复策略。

    分类优先级（从高到低）：
    1. 429 速率限制 → RETRY
    2. 上下文溢出 → DEGRADE（截断后重试）
    3. 超时 → RETRY
    4. 401/403 认证错误 → ABORT
    5. 400 请求错误 → FEEDBACK_AND_CONTINUE（模型自修正）
    6. 5xx 服务端错误 → RETRY
    7. 其他 → ABORT

    Args:
        exc: 异常实例

    Returns:
        ErrorClassification 包含 category, action, user_message, model_hint, retryable
    """
    err_str = str(exc).lower()
    status_code = _extract_status_from_chain(exc)

    # ① 429 速率限制（已有 RateLimitMiddleware 处理过一轮，到这里说明重试耗尽）
    try:
        import openai
        if _check_exception_chain(exc, lambda e: isinstance(e, openai.RateLimitError)):
            return ErrorClassification(
                category=ErrorCategory.RATE_LIMIT,
                action=RecoveryAction.ABORT,  # 中间件已重试过，这里直接终止
                user_message=(
                    "正在执行中……"
                ),
                model_hint="",  # 不需要反馈模型，直接终止
                retryable=False,
            )
    except ImportError:
        pass

    if "429" in str(exc) or "rate limit" in err_str or "too many requests" in err_str:
        return ErrorClassification(
            category=ErrorCategory.RATE_LIMIT,
            action=RecoveryAction.ABORT,
            user_message=(
                "正在执行中……"
            ),
            model_hint="",
            retryable=False,
        )

    # ② 上下文溢出
    context_overflow_keywords = [
        "maximum context length",
        "context_length_exceeded",
        "max_tokens",
        "too many tokens",
        "context window",
        "token limit",
    ]
    if any(kw in err_str for kw in context_overflow_keywords):
        return ErrorClassification(
            category=ErrorCategory.CONTEXT_OVERFLOW,
            action=RecoveryAction.DEGRADE,
            user_message=(
                "对话上下文过长，系统正在自动压缩历史消息..."
            ),
            model_hint=(
                "系统错误：上下文长度超出模型限制，已自动截断历史消息。"
                "请基于当前可见的上下文继续处理用户的最新请求。"
                "如果缺少之前的信息，请如实告知用户。"
            ),
            retryable=True,
        )

    # ③ 超时
    timeout_keywords = ["timeout", "timed out", "deadline exceeded", "read timeout"]
    if any(kw in err_str for kw in timeout_keywords):
        return ErrorClassification(
            category=ErrorCategory.TIMEOUT,
            action=RecoveryAction.RETRY,
            user_message="请求超时，正在重试...",
            model_hint=(
                "系统提示：上一次调用因超时失败，系统已自动重试。"
                "请继续处理用户的请求。"
            ),
            retryable=True,
        )

    # ④ 401/403 认证错误
    if status_code in (401, 403):
        return ErrorClassification(
            category=ErrorCategory.AUTH_ERROR,
            action=RecoveryAction.ABORT,
            user_message=(
                "API 认证失败，请检查 API Key 配置或联系管理员。"
            ),
            model_hint="",
            retryable=False,
        )

    # ⑤ 工具参数验证错误（LLM 传给工具的参数不合法，需反馈模型自修正）
    tool_validation_keywords = [
        "validation error",
        "validation errors for call",
        "pydantic",
        "field required",
        "value is not a valid",
    ]
    if any(kw in err_str for kw in tool_validation_keywords):
        brief_error = str(exc)[:500]
        return ErrorClassification(
            category=ErrorCategory.TOOL_ERROR,
            action=RecoveryAction.FEEDBACK_AND_CONTINUE,
            user_message="",  # 不直接通知用户，让模型自修正工具参数
            model_hint=(
                f"系统错误：你上一次调用工具时传入了不合法的参数，工具执行失败。\n"
                f"错误详情：{brief_error}\n\n"
                f"请仔细阅读上述验证错误，理解每个参数的正确类型和格式要求，"
                f"修正参数后重新调用该工具。\n"
                f"注意：不要重复犯同样的错误，确保所有必填参数都已提供且类型正确。"
            ),
            retryable=True,
        )

    # ⑥ 400 请求错误（模型自修正）
    if status_code == 400 or "bad request" in err_str or "badrequest" in err_str:
        # 提取关键错误信息（去掉过长的堆栈）
        brief_error = str(exc)[:300]
        return ErrorClassification(
            category=ErrorCategory.BAD_REQUEST,
            action=RecoveryAction.FEEDBACK_AND_CONTINUE,
            user_message="",  # 不直接通知用户，让模型尝试自修正
            model_hint=(
                f"系统错误：上一次 API 调用返回 400 Bad Request。\n"
                f"错误详情：{brief_error}\n\n"
                f"可能的原因：消息格式异常、参数错误、或工具调用参数畸形。\n"
                f"请分析错误原因，调整策略后继续帮助用户完成任务。\n"
                f"如果无法自行修正，请向用户说明情况并建议解决方案。"
            ),
            retryable=True,
        )

    # ⑥ 5xx 服务端错误
    if status_code is not None and 500 <= status_code < 600:
        return ErrorClassification(
            category=ErrorCategory.SERVER_ERROR,
            action=RecoveryAction.RETRY,
            user_message="正在执行中……",
            model_hint=(
                f"系统提示：上一次调用因服务端错误（{status_code}）失败，"
                f"系统已自动重试。请继续处理用户的请求。"
            ),
            retryable=True,
        )
    if "internal server error" in err_str or "502" in str(exc) or "503" in str(exc):
        return ErrorClassification(
            category=ErrorCategory.SERVER_ERROR,
            action=RecoveryAction.RETRY,
            user_message="正在执行中……",
            model_hint="系统提示：上一次调用因服务端错误失败，系统已自动重试。请继续处理用户的请求。",
            retryable=True,
        )

    # ⑦ 兜底：未知错误
    brief_error = str(exc)[:200]
    return ErrorClassification(
        category=ErrorCategory.UNKNOWN,
        action=RecoveryAction.ABORT,
        user_message=f"抱歉，处理您的请求时出现错误: {brief_error}",
        model_hint="",
        retryable=False,
    )
