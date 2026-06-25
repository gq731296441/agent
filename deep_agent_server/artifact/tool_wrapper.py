"""MCP 工具 spill wrapper。"""

from dataclasses import dataclass
from typing import Any, List, Optional

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

from config import get_logger
from deep_agent_server.artifact.spill import spill_tool_result

logger = get_logger(__name__)


@dataclass
class SpillContext:
    store: Any
    user_id: str
    session_id: str
    conversation_id: Optional[str] = None


def _extract_tool_call_id(config: Optional[RunnableConfig]) -> str:
    if not config:
        return ""
    configurable = config.get("configurable") if isinstance(config, dict) else getattr(config, "configurable", None)
    if isinstance(configurable, dict):
        return str(configurable.get("tool_call_id") or configurable.get("__tool_call_id") or "")
    metadata = config.get("metadata") if isinstance(config, dict) else getattr(config, "metadata", None)
    if isinstance(metadata, dict):
        return str(metadata.get("tool_call_id") or "")
    return ""


def _result_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, ToolMessage):
        content = result.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif isinstance(block, str):
                    parts.append(block)
            return "".join(parts)
        return str(content)
    if isinstance(result, tuple) and result:
        head = result[0]
        if isinstance(head, str):
            return head
        if isinstance(head, ToolMessage):
            return _result_text(head)
        return str(head)
    return str(result)


async def _maybe_spill(
    tool: BaseTool,
    ctx: SpillContext,
    result: Any,
    config: Optional[RunnableConfig],
) -> Any:
    text = _result_text(result)
    tool_call_id = _extract_tool_call_id(config)
    try:
        spill = await spill_tool_result(
            store=ctx.store,
            user_id=ctx.user_id,
            session_id=ctx.session_id,
            tool_name=tool.name,
            tool_call_id=tool_call_id,
            result_text=text,
            conversation_id=ctx.conversation_id,
        )
        if spill.spilled:
            if isinstance(result, tuple):
                return (spill.content, *result[1:])
            if isinstance(result, ToolMessage):
                return ToolMessage(
                    content=spill.content,
                    artifact=getattr(result, "artifact", None),
                    tool_call_id=getattr(result, "tool_call_id", tool_call_id),
                    name=getattr(result, "name", tool.name),
                    status=getattr(result, "status", "success"),
                )
            return spill.content
    except Exception as e:
        logger.warning(f"[SpillWrapper] {tool.name} spill 失败，返回原文: {e}")
    return result


def _wrap_tool_ainvoke(tool: BaseTool, ctx: SpillContext) -> BaseTool:
    """在 ainvoke 层包装，避免向 MCP coroutine 注入 config 污染 tool_args。"""
    original_ainvoke = tool.ainvoke

    async def ainvoke(input: Any, config: Optional[RunnableConfig] = None, **kwargs: Any):
        result = await original_ainvoke(input, config, **kwargs)
        return await _maybe_spill(tool, ctx, result, config)

    object.__setattr__(tool, "ainvoke", ainvoke)
    return tool


def wrap_tools_with_spill(tools: List[Any], ctx: SpillContext) -> List[Any]:
    """包装 MCP 工具列表，超长结果自动 spill。"""
    if not tools or not ctx:
        return tools or []
    wrapped = []
    for tool in tools:
        if isinstance(tool, BaseTool):
            wrapped.append(_wrap_tool_ainvoke(tool, ctx))
        else:
            wrapped.append(tool)
    return wrapped
