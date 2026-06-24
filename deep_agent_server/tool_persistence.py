"""从 LangGraph state / tools 流补持久化工具调用（HITL resume 等场景）。"""

from typing import Any, AsyncGenerator, Dict, List, Set

from langchain.messages import AIMessage, ToolMessage

from config import get_logger
from deep_agent_server.adapter import DeepEventAdapter, normalize_tool_call

logger = get_logger(__name__)


def collect_parent_tool_call_ids(messages: List[Any]) -> Set[str]:
    """主图 AIMessage 上的 tool_call id（用于区分子 agent 内部工具）。"""
    ids: Set[str] = set()
    for msg in messages or []:
        if not isinstance(msg, AIMessage):
            continue
        for tc in getattr(msg, "tool_calls", None) or []:
            if isinstance(tc, dict) and tc.get("id"):
                ids.add(str(tc["id"]))
    return ids


def build_tool_call_args_map(messages: List[Any]) -> Dict[str, dict]:
    """从 checkpoint AIMessage.tool_calls 回填 args。"""
    args_map: Dict[str, dict] = {}
    for msg in messages or []:
        if not isinstance(msg, AIMessage):
            continue
        for tc in getattr(msg, "tool_calls", None) or []:
            if not isinstance(tc, dict):
                continue
            tc_id = tc.get("id")
            if not tc_id:
                continue
            _, args = normalize_tool_call(tc)
            if args:
                args_map[str(tc_id)] = args
    return args_map


def resolve_tool_name_from_messages(
    messages: List[Any],
    tool_call_id: str,
    fallback: str = "unknown",
) -> str:
    """从 checkpoint AIMessage.tool_calls 回填 ToolMessage 缺失的 name。"""
    for msg in messages or []:
        if not isinstance(msg, AIMessage):
            continue
        for tc in getattr(msg, "tool_calls", None) or []:
            if not isinstance(tc, dict):
                continue
            if str(tc.get("id") or "") == tool_call_id:
                return tc.get("name") or tc.get("tool_name") or fallback
    return fallback


async def flush_tool_messages_from_state(
    compiled,
    config: dict,
    adapter: DeepEventAdapter,
) -> AsyncGenerator[dict, None]:
    """
    从 checkpoint state 补发尚未持久化的 ToolMessage。

    仅补当前 turn 内 DB/adapter 中尚无 result 记录的工具，避免 HITL resume 重放全量历史。
    """
    if not compiled:
        return
    try:
        state = await compiled.aget_state(config)
    except Exception as exc:
        logger.debug(f"[ToolPersist] aget_state 失败: {exc}")
        return

    messages = (getattr(state, "values", None) or {}).get("messages") or []
    parent_ids = collect_parent_tool_call_ids(messages)
    args_map = build_tool_call_args_map(messages)

    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        tool_call_id = getattr(msg, "tool_call_id", "") or ""
        if not tool_call_id:
            continue
        if tool_call_id in adapter._persisted_tool_results:
            continue
        if tool_call_id in adapter._db_completed_tool_call_ids:
            adapter._persisted_tool_results.add(tool_call_id)
            continue

        if tool_call_id in args_map:
            adapter._pending_tool_inputs.setdefault(tool_call_id, args_map[tool_call_id])

        tool_name = getattr(msg, "name", None) or "unknown"
        if tool_name == "unknown":
            tool_name = resolve_tool_name_from_messages(messages, tool_call_id, tool_name)
        if tool_name == "task":
            async for out in adapter._adapt_tool_message(msg):
                yield out
            adapter._persisted_tool_results.add(tool_call_id)
            continue

        if tool_call_id not in parent_ids:
            async for out in adapter._adapt_subagent_tool_from_message(
                msg, phase="result", parent_tool_call_ids=parent_ids
            ):
                yield out
            adapter._persisted_tool_results.add(tool_call_id)
            continue

        async for out in adapter._adapt_tool_message(msg):
            yield out
        adapter._persisted_tool_results.add(tool_call_id)


def stringify_tool_output(output: Any) -> str:
    if output is None:
        return ""
    if hasattr(output, "content"):
        content = output.content
        return content if isinstance(content, str) else str(content or "")
    return str(output)
