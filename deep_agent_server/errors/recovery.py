"""Deep Agent 错误恢复策略。

对 Deep 运行中的异常进行分类（timeout / rate_limit / network 等），
决定是否自动重试及用户可见的错误文案。
"""

from dataclasses import dataclass
from typing import Optional

import asyncio

from deep_agent_server.sandbox.exceptions import SandboxUnavailable


@dataclass
class DeepErrorClassification:
    """Deep 错误分类结果。"""
    retryable: bool
    category: str
    user_message: str = ""


def _looks_like_sandbox_failure(msg: str) -> bool:
    """消息模式兜底：识别未被包装成 SandboxUnavailable 的沙箱基础设施故障。

    覆盖 `containers.run` / `exec_run` 等处直接抛出的第三方 DockerException。
    """
    if "error while fetching server api version" in msg:
        return True
    if "docker" in msg or "sandbox" in msg:
        return any(k in msg for k in (
            "connection", "connect", "daemon", "timeout",
            "refused", "aborted", "unavailable",
        ))
    return False


def classify_deep_error(exc: Exception) -> DeepErrorClassification:
    """将异常映射为可重试/不可重试分类。"""
    msg = str(exc).lower()
    exc_name = type(exc).__name__

    # 沙箱基础设施故障必须早于下面的 "connection" 规则判定：这类报错文本含
    # "Connection aborted"，否则会被误判为可重试的网络错误。而 daemon 故障、
    # ssh 隧道断开、远端账号过期都不会因重试而恢复 —— 重试只是白跑整轮 agent
    # 并把用户引向"网络错误"这个错误方向。
    if isinstance(exc, SandboxUnavailable) or _looks_like_sandbox_failure(msg):
        return DeepErrorClassification(
            False,
            "sandbox_unavailable",
            "沙箱服务暂时不可用，本次未执行代码。请稍后重试；若持续出现请联系管理员。",
        )

    if isinstance(exc, asyncio.TimeoutError) or "timeout" in msg:
        return DeepErrorClassification(True, "timeout", "请求超时，正在重试…")
    if "rate limit" in msg or "429" in msg:
        return DeepErrorClassification(True, "rate_limit", "限流，正在重试…")
    if "context" in msg and "overflow" in msg:
        return DeepErrorClassification(True, "context_overflow", "上下文过长，正在压缩并重试…")
    if "connection" in msg or "network" in msg:
        return DeepErrorClassification(True, "network", "网络错误，正在重试…")
    # MCP 工具错误 — 参数校验失败或服务端错误，应让 Agent 看到错误并自修正
    # 这是兜底安全网：正常情况下 _wrap_mcp_tools_error 会将 ToolException 转为
    # ToolMessage 返回给 LLM，但如果异常因其他路径逃逸到顶层，应标记为可重试
    if "tool" in msg or "tool" in exc_name.lower() or "mcp" in msg:
        if any(kw in msg for kw in [
            "validation", "required", "missing", "argument", "parameter",
            "field required", "pydantic", "type_error", "value_error",
        ]):
            return DeepErrorClassification(
                True, "mcp_param_error",
                "MCP 工具调用参数有误，正在让模型修正后重试…",
            )
        # 通用 MCP/Tool 错误（非认证类）
        return DeepErrorClassification(
            True, "mcp_tool_error",
            "MCP 工具调用异常，正在重试…",
        )

    # 认证/鉴权错误（错误的 API Key / Base URL / 模型名）
    if (
        "authentication" in exc_name.lower()
        or "auth" in exc_name.lower()
        or "401" in msg
        or "403" in msg
        or "permission" in msg
        or "unauthorized" in msg
    ):
        return DeepErrorClassification(
            False, "auth_error",
            "模型认证失败，请检查 API Key、Base URL 和模型名称是否正确。"
            "可在「模型设置」中修改后重试。",
        )
    return DeepErrorClassification(False, "unknown", str(exc)[:200])


def should_retry(classification: DeepErrorClassification) -> bool:
    return classification.retryable
