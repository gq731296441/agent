"""Deep Agent 事件 → AgentMessage 适配。

Deep Agent 基于 LangGraph astream 输出 messages / updates 两种流模式，
本模块负责将其统一转换为前端可消费的 AgentMessage 字典，并补充
run_id、conversation_id、step_index 等 Deep 模式专有字段。
"""

import json
import mimetypes
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

from langchain.messages import ToolMessage

from common.enums import MessageType, SenderType, EndReason
from common.schemas import AgentMessage
from config.config import app_config
from deep_agent_server.artifact import parse_spill_content, truncate_summary
from deep_agent_server.hitl.interrupt_handler import (
    build_interrupt_content,
    parse_hitl_actions_from_interrupts,
)
from deep_agent_server.hitl.tool_call_link import match_hitl_action_index
from utils.extractors import ThinkTagParser, ToolCallAccumulator, extract_reasoning_from_chunk, extract_streaming_json_field

# 内置工具 → 前端 MessageType 映射（按 deepagents 工具名分组）
_FS_TOOLS = {"read_file", "write_file", "edit_file", "ls", "glob", "grep"}
_EXECUTE_TOOLS = {"execute"}
_TODO_TOOLS = {"write_todos"}
_TASK_TOOLS = {"task"}

# 需要流式输出 args 内容的工具（这些工具的 args 中可能包含大文本字段）
_STREAMABLE_TOOLS = {"write_file", "edit_file", "execute"}

# 流式工具 → 大文本字段名映射
_STREAMABLE_TOOL_CONTENT_FIELDS = {
    "write_file": "content",
    "edit_file": "new_string",
    "execute": "command",
}

# 流式工具 → 提前提取的关键参数（从部分 JSON 中提取，用于进度通知）
# 在大文本字段到达前，让前端提前展示"正在生成 xxx..."
_KEY_TOOL_PARAMS: dict[str, list[str]] = {
    "write_file": ["file_path"],
    "edit_file": ["file_path"],
    "execute": [],  # execute 无小字段，仅通知工具名
}


def _extract_tool_key_params(tool_name: str, args_str: str) -> dict:
    """从部分 JSON args 中提取关键参数（用于提前进度通知）。

    在大文本字段（content/new_string/command）到达之前，
    先提取 file_path 等轻量参数，让前端提前展示进度。

    Args:
        tool_name: 工具名称
        args_str: 当前累积的 args JSON 字符串（可能不完整）

    Returns:
        提取到的 {param_name: value} 字典
    """
    key_params = {}
    fields = _KEY_TOOL_PARAMS.get(tool_name, [])
    if not fields or not args_str:
        return key_params
    for field in fields:
        value = extract_streaming_json_field(args_str, field)
        if value:
            key_params[field] = value
    return key_params


def _mime_for_path_adapter(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def normalize_tool_call(tc: Any) -> tuple[str, dict]:
    """从流式/结构化 tool_call 提取 name 与 args（兼容 OpenAI function 嵌套格式）。"""
    if not isinstance(tc, dict):
        return "unknown", {}

    name = (tc.get("name") or "").strip()
    args = tc.get("args")

    fn = tc.get("function")
    if isinstance(fn, dict):
        name = name or (fn.get("name") or "").strip()
        if args is None:
            raw = fn.get("arguments")
            if isinstance(raw, str):
                try:
                    args = json.loads(raw) if raw.strip() else {}
                except json.JSONDecodeError:
                    args = {"_raw": raw}
            elif isinstance(raw, dict):
                args = raw

    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError:
            args = {"_raw": args}
    if not isinstance(args, dict):
        args = {}

    if not name or name == "unknown":
        if args.get("subagent_type") or args.get("description") or args.get("task"):
            name = "task"

    return name or "unknown", args


def is_task_tool_call(tc: Any) -> bool:
    name, args = normalize_tool_call(tc if isinstance(tc, dict) else {})
    return name == "task" or bool(
        args.get("subagent_type") or args.get("description") or args.get("task")
    )


def task_call_args_ready(args: dict) -> bool:
    """task 工具 args 是否已足够发出 subagent_start。"""
    return bool(args.get("description") or args.get("subagent_type") or args.get("task"))


# LangGraph 运行时内部 key，工具 args 中不应带入前端/DB
_RUNTIME_KEYS = frozenset({"runtime", "config", "configurable", "store", "stream_writer"})

def _strip_runtime(obj: Any) -> Any:
    """递归移除 LangGraph 运行时对象（不可序列化）。"""
    if isinstance(obj, dict):
        return {k: _strip_runtime(v) for k, v in obj.items() if k not in _RUNTIME_KEYS}
    if isinstance(obj, list):
        return [_strip_runtime(v) for v in obj]
    return obj


def base_content(
    *,
    run_id: str,
    conversation_id: str = None,
    **extra,
) -> dict:
    """构造 Deep 模式消息 content 公共字段（工具调用 / 状态 / 中断等共用）。"""
    payload = {
        "agent_mode": "deep",
        "run_id": run_id,
        "conversation_id": conversation_id,
        "parent_tool_call_id": extra.pop("parent_tool_call_id", None),
        "subagent_run_id": extra.pop("subagent_run_id", None),
        "workspace_path": extra.pop("workspace_path", None),
        "sandbox_id": extra.pop("sandbox_id", None),
        "skill_name": extra.pop("skill_name", None),
        "ref_id": extra.pop("ref_id", None),
        "summary": extra.pop("summary", None),
        "truncated": extra.pop("truncated", False),
    }
    # 清理 args 中的 LangGraph 运行时对象（MCP 工具适配器会泄露进来）
    if "args" in extra:
        extra["args"] = _strip_runtime(extra["args"])
    payload.update(extra)
    return payload


def _tool_result_fields(result_text: str) -> dict:
    """从工具结果（含 spill 格式）提取展示字段。"""
    parsed = parse_spill_content(result_text)
    if parsed.get("spilled"):
        summary = parsed.get("summary") or truncate_summary(result_text)
        return {
            "tool_result": summary,
            "summary": summary,
            "truncated": True,
            "ref_id": parsed.get("ref_id"),
            "workspace_path": parsed.get("virtual_path"),
        }
    summary = truncate_summary(result_text)
    return {
        "tool_result": summary,
        "summary": summary,
        "truncated": len(result_text or "") > app_config.CONTENT_SUMMARY_MAX_CHARS,
        "ref_id": None,
        "workspace_path": None,
    }


def _tool_message_type(tool_name: str, is_start: bool) -> MessageType:
    """根据工具名与阶段（开始/结束）返回对应 MessageType。"""
    if tool_name in _TODO_TOOLS:
        return MessageType.TODO_UPDATE
    if tool_name in _FS_TOOLS:
        return MessageType.FS_OPERATION
    if tool_name in _EXECUTE_TOOLS:
        return MessageType.EXECUTE if is_start else MessageType.EXECUTE_RESULT
    if tool_name in _TASK_TOOLS:
        return MessageType.SUBAGENT_START if is_start else MessageType.SUBAGENT_END
    if tool_name.startswith("mcp_"):
        return MessageType.TOOL_CALL if is_start else MessageType.TOOL_RESULT
    return MessageType.TOOL_CALL if is_start else MessageType.TOOL_RESULT


def _stringify_tool_stream_output(output: Any) -> str:
    if output is None:
        return ""
    if hasattr(output, "content"):
        content = output.content
        return content if isinstance(content, str) else str(content or "")
    return str(output)


class DeepEventAdapter:
    """
    LangGraph astream 输出 → AgentMessage 流式适配器。

    处理 messages 模式下的 model/tools 节点切换，以及 updates 模式下的
    HITL __interrupt__ 事件；同时管理推理文本、助手回复、工具调用的
    分段缓冲与 step_index 递增。
    """

    def __init__(
        self,
        session_id: str,
        run_id: str,
        conversation_id: str,
        user_id: str,
        group_id: int = 0,
        agent_id: Optional[str] = None,
        initial_step_index: int = 0,
    ):
        self.session_id = session_id
        self.run_id = run_id
        self.conversation_id = conversation_id
        self.user_id = user_id
        self.agent_id = agent_id
        self.group_id = group_id
        self.step_index = int(initial_step_index or 0)
        # task 子 agent：tool_call_id → subagent_run_id，用于 SUBAGENT_END 关联
        self._active_subagents: Dict[str, str] = {}
        self._active_subagent_inputs: Dict[str, dict] = {}
        self._pending_subagent_inputs: Dict[str, dict] = {}
        self._subagent_runs_persisted: set = set()
        self._current_llm_text = ""
        self._last_node: Optional[str] = None
        # model 节点流式输出 tool_calls 时先缓存，等离开 model 节点再 emit start
        self._pending_tool_calls: List[dict] = []
        self._pending_tool_inputs: Dict[str, dict] = {}
        self._tool_call_accumulator = ToolCallAccumulator()
        # 工具参数流式输出：追踪每个 tool call index 已发送的内容
        self._streamed_tool_args: dict[int, str] = {}
        # 工具进度通知：追踪是否已发送过"正在生成"通知
        self._tool_progress_emitted: set = set()
        self._emitted_tool_starts: set = set()
        self._emitted_tool_names: Dict[str, str] = {}
        self._persisted_tool_results: set = set()
        self._db_started_tool_call_ids: set = set()
        self._db_completed_tool_call_ids: set = set()
        # 子 agent 栈：{subagent_run_id, parent_tool_call_id}
        self._subagent_stack: List[dict] = []
        self.interrupted = False
        self._think_parser = ThinkTagParser()
        self._reasoning_notified = False
        self._current_reasoning_text = ""
        # 是否已通过 CHAT chunk 推过前端（决定 flush 时是否补发整段）
        self._chat_streamed = False
        # HITL resume：工具真正执行后把 call_* 写回 interrupt
        self._hitl_resume_request_id: Optional[str] = None
        self._hitl_resume_actions: List[dict] = []
        self._hitl_resume_decisions: List[dict] = []
        self._hitl_matched_indices: set = set()
        # 本轮 conversation 内 write/edit 登记的文件（virtual_path → meta）
        self._conversation_files: Dict[str, dict] = {}
        self._tool_event_inputs: Dict[str, dict] = {}
        # 当前 model 段是否已将推理刷为 reasoning_final（避免 CoT 每 chunk 重复刷库）
        self._reasoning_segment_flushed: bool = False
        # 上下文压缩后抑制当前 model 段输出（防止摘要推理和摘要内容泄露到前端/DB）
        self._summarization_suppress: bool = False

    def bind_hitl_resume(
        self,
        request_id: str,
        actions: List[dict],
        history_decisions: List[dict],
    ) -> None:
        """绑定 resume 上下文，供工具 start 时回填真实 tool_call_id。"""
        self._hitl_resume_request_id = request_id or None
        self._hitl_resume_actions = list(actions or [])
        self._hitl_resume_decisions = list(history_decisions or [])
        self._hitl_matched_indices = set()

    async def _link_resumed_tool_call(
        self,
        tool_name: str,
        tool_input: dict,
        tool_call_id: str,
    ) -> None:
        if not self._hitl_resume_request_id or not tool_call_id:
            return
        idx = match_hitl_action_index(
            self._hitl_resume_actions,
            tool_name,
            tool_input or {},
            self._hitl_matched_indices,
            self._hitl_resume_decisions,
        )
        if idx is None:
            return
        self._hitl_matched_indices.add(idx)
        action = self._hitl_resume_actions[idx]
        prior_id = str(action.get("tool_call_id") or "")
        if prior_id == str(tool_call_id):
            return
        if prior_id and not prior_id.startswith("hitl_"):
            return

        from db import storage as chat_storage

        ok = await chat_storage.patch_interrupt_resolved_tool_call_id(
            self.session_id,
            self._hitl_resume_request_id,
            index=idx,
            resolved_tool_call_id=str(tool_call_id),
            original_tool_call_id=prior_id or None,
        )
        if not ok:
            return
        action["tool_call_id"] = str(tool_call_id)
        action["resolved_tool_call_id"] = str(tool_call_id)
        if idx < len(self._hitl_resume_decisions):
            self._hitl_resume_decisions[idx]["tool_call_id"] = str(tool_call_id)
            self._hitl_resume_decisions[idx]["resolved_tool_call_id"] = str(tool_call_id)

    def preload_persisted_tool_keys(
        self,
        started_ids: set,
        completed_ids: set,
    ) -> None:
        """从 chat_history 预加载已落库 tool_call_id，避免 resume/HITL 重复写入。"""
        self._db_started_tool_call_ids = set(started_ids or ())
        self._db_completed_tool_call_ids = set(completed_ids or ())
        self._emitted_tool_starts.update(self._db_started_tool_call_ids)
        self._persisted_tool_results.update(self._db_completed_tool_call_ids)

    def _tool_already_completed(self, tool_call_id: str) -> bool:
        if not tool_call_id:
            return False
        return (
            tool_call_id in self._persisted_tool_results
            or tool_call_id in self._db_completed_tool_call_ids
        )

    def _resolve_tool_name(
        self,
        tool_call_id: str,
        tool_name: str,
        args: Optional[dict] = None,
    ) -> str:
        from deep_agent_server.conversation_files import infer_fs_tool_name
        from mcp_server.tool_name_utils import mcp_original_names

        name = (tool_name or "").strip()
        if name and name != "unknown":
            return mcp_original_names.get(name, name)
        if tool_call_id:
            stored = self._emitted_tool_names.get(tool_call_id)
            if stored and stored != "unknown":
                return mcp_original_names.get(stored, stored)
        inferred = infer_fs_tool_name(args or {})
        return mcp_original_names.get(inferred or "", inferred) or name or "unknown"

    @staticmethod
    def _stringify_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif isinstance(item, str):
                    parts.append(item)
            return "".join(parts)
        if content is None:
            return ""
        return str(content)

    async def _emit_chat_chunk(self, text: str) -> AsyncGenerator[dict, None]:
        if not text or self._summarization_suppress:
            return
        self._current_llm_text += text
        self._chat_streamed = True
        yield self._enrich(
            AgentMessage(
                type=MessageType.CHAT,
                sender=SenderType.ASSISTANT,
                session_id=self.session_id,
                content=text,
                save=False,
            ),
            track_step=False,
        )

    async def _append_reasoning_chunk(self, chunk: str) -> AsyncGenerator[dict, None]:
        """累积推理文本；开启 REASONING_STREAM_ENABLED 时按 chunk 推前端。"""
        if not chunk or self._summarization_suppress:
            return
        self._current_reasoning_text += chunk
        if app_config.REASONING_STREAM_ENABLED:
            yield self._enrich(
                AgentMessage(
                    type=MessageType.REASONING,
                    sender=SenderType.ASSISTANT,
                    session_id=self.session_id,
                    content={"text": chunk},
                    save=False,
                ),
                track_step=False,
            )
        elif not self._reasoning_notified:
            self._reasoning_notified = True
            yield self._enrich(
                AgentMessage(
                    type=MessageType.REASONING,
                    sender=SenderType.ASSISTANT,
                    session_id=self.session_id,
                    content="正在推理中...",
                    save=False,
                ),
                track_step=False,
            )

    async def _flush_reasoning_text(self) -> AsyncGenerator[dict, None]:
        if self._summarization_suppress:
            self._current_reasoning_text = ""
            return
        text = (self._current_reasoning_text or "").strip()
        if not text:
            return
        yield self._enrich(
            AgentMessage(
                type=MessageType.REASONING_FINAL,
                sender=SenderType.ASSISTANT,
                session_id=self.session_id,
                content={"text": text},
                save=True,
            ),
            # 流式 reasoning chunk 不递增 step_index，final 须保持一致以便前端合并
            track_step=not app_config.REASONING_STREAM_ENABLED,
        )
        self._current_reasoning_text = ""

    async def _flush_reasoning_if_needed(self) -> AsyncGenerator[dict, None]:
        """将当前 model 段累积的推理一次性刷为 reasoning_final。"""
        if self._reasoning_segment_flushed or self._summarization_suppress:
            return
        text = (self._current_reasoning_text or "").strip()
        if not text:
            return
        self._reasoning_segment_flushed = True
        async for msg in self._flush_reasoning_text():
            yield msg

    async def flush_assistant_text(self) -> AsyncGenerator[dict, None]:
        """刷新助手文本：推前端 CHAT（若未流式推送）+ 存库 LLM_TEXT_FINAL。"""
        if self._summarization_suppress:
            self._current_llm_text = ""
            self._chat_streamed = False
            return
        text = (self._current_llm_text or "").strip()
        if not text:
            return
        if not self._chat_streamed:
            yield self._enrich(
                AgentMessage(
                    type=MessageType.CHAT,
                    sender=SenderType.ASSISTANT,
                    session_id=self.session_id,
                    content=text,
                    save=False,
                ),
                track_step=False,
            )
        yield self._enrich(
            AgentMessage(
                type=MessageType.LLM_TEXT_FINAL,
                sender=SenderType.ASSISTANT,
                session_id=self.session_id,
                content={"text": text},
                save=True,
            )
        )
        self._current_llm_text = ""
        self._chat_streamed = False

    async def finalize_model_segment(
        self, *, skip_reasoning: bool = False
    ) -> AsyncGenerator[dict, None]:
        """离开 model 节点或流结束时：刷 think 缓冲、推理与助手正文（与 Light runner 对齐）。"""
        # 上下文压缩触发后，跳过当前 model 段的 finalize，
        # 防止摘要推理和摘要内容被落库为 REASONING_FINAL / LLM_TEXT_FINAL
        if self._summarization_suppress:
            self._current_llm_text = ""
            self._current_reasoning_text = ""
            self._chat_streamed = False
            self._reasoning_segment_flushed = False
            self._reasoning_notified = False
            self._think_parser.reset()
            return
        thinking_part, answer_part = self._think_parser.flush()
        if thinking_part:
            self._current_reasoning_text += thinking_part
        if answer_part:
            async for msg in self._flush_reasoning_if_needed():
                yield msg
            async for msg in self._emit_chat_chunk(answer_part):
                yield msg
        if not skip_reasoning:
            async for msg in self._flush_reasoning_if_needed():
                yield msg
        async for msg in self.flush_assistant_text():
            yield msg

    def _enrich(self, msg: AgentMessage, *, track_step: bool = True) -> dict:
        """为 AgentMessage 附加 Deep 模式元数据；track_step=False 时不递增 step_index。"""
        d = msg.to_dict()
        d["conversation_id"] = self.conversation_id
        d["group_id"] = self.group_id
        if track_step:
            self.step_index += 1
        d["step_index"] = self.step_index
        d["run_id"] = self.run_id
        return d

    async def adapt_stream_event(
        self, event: dict
    ) -> AsyncGenerator[dict, None]:
        """适配 astream_events 回调格式（on_chat_model_stream / on_tool_* 等）。"""
        et = event.get("event", "")
        data = event.get("data") or {}
        name = event.get("name", "")

        if et == "on_chat_model_stream":
            chunk = data.get("chunk")
            if chunk and hasattr(chunk, "content") and chunk.content:
                text = chunk.content if isinstance(chunk.content, str) else str(chunk.content)
                self._current_llm_text += text
                yield self._enrich(
                    AgentMessage(
                        type=MessageType.CHAT,
                        sender=SenderType.ASSISTANT,
                        session_id=self.session_id,
                        content=text,
                        save=False,
                    ),
                    track_step=False,
                )

        elif et == "on_chat_model_end":
            async for msg in self.flush_assistant_text():
                yield msg

        elif et == "on_tool_start":
            tool_name = event.get("name") or data.get("name") or "unknown"
            tool_input = data.get("input") or {}
            tool_call_id = event.get("run_id") or str(uuid.uuid4())
            if tool_call_id and tool_input:
                self._tool_event_inputs[tool_call_id] = tool_input
            msg_type = _tool_message_type(tool_name, True)

            subagent_run_id = None
            if tool_name in _TASK_TOOLS:
                subagent_run_id = str(uuid.uuid4())
                self._active_subagents[tool_call_id] = subagent_run_id
                self._active_subagent_inputs[subagent_run_id] = tool_input

            content = base_content(
                run_id=self.run_id,
                conversation_id=self.conversation_id,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                args=tool_input,
                subagent_run_id=subagent_run_id,
            )
            yield self._enrich(
                AgentMessage(
                    type=msg_type,
                    sender=SenderType.SYSTEM,
                    session_id=self.session_id,
                    content=content,
                    save=True,
                )
            )

        elif et == "on_tool_end":
            tool_name = event.get("name") or "unknown"
            output = data.get("output")
            if hasattr(output, "content"):
                result_text = output.content
            else:
                result_text = str(output) if output is not None else ""

            fields = _tool_result_fields(result_text if isinstance(result_text, str) else str(result_text))
            summary = fields["tool_result"]
            ref_id = fields.get("ref_id")
            truncated = fields.get("truncated", False)

            msg_type = _tool_message_type(tool_name, False)
            tool_call_id = event.get("run_id") or ""
            subagent_run_id = self._active_subagents.pop(tool_call_id, None)

            content = base_content(
                run_id=self.run_id,
                conversation_id=self.conversation_id,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                tool_result=summary,
                ref_id=ref_id,
                truncated=truncated,
                workspace_path=fields.get("workspace_path"),
                subagent_run_id=subagent_run_id,
            )
            if tool_name in _TASK_TOOLS and subagent_run_id:
                content["final_report"] = summary
                content["args"] = self._active_subagent_inputs.pop(subagent_run_id, {})

            yield self._enrich(
                AgentMessage(
                    type=msg_type,
                    sender=SenderType.SYSTEM,
                    session_id=self.session_id,
                    content=content,
                    save=True,
                )
            )
            tool_input = self._tool_event_inputs.pop(tool_call_id, {})
            self._record_conversation_file(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                args=tool_input,
                workspace_path=fields.get("workspace_path"),
                result_text=result_text if isinstance(result_text, str) else str(result_text),
            )

    async def adapt_custom_event(self, chunk: Any) -> AsyncGenerator[dict, None]:
        """处理 stream_mode=custom 事件（中间件通过 stream_writer 发射）。"""
        if not isinstance(chunk, dict):
            return
        event_type = chunk.get("type", "")
        if event_type == "context_compressing":
            # 中间件在调用 _acreate_summary 之前发射，通知前端开始压缩
            yield self._enrich(
                AgentMessage(
                    type=MessageType.CONTEXT_COMPRESSING,
                    sender=SenderType.SYSTEM,
                    session_id=self.session_id,
                    content={
                        "message": "正在压缩上下文...",
                        "cutoff_index": chunk.get("cutoff_index"),
                    },
                    save=False,
                ),
                track_step=False,
            )

    async def adapt_astream_chunk(
        self, mode: str, chunk: Any
    ) -> AsyncGenerator[dict, None]:
        """处理 astream 的 messages / updates 模式（含 HITL 中断）。"""
        if mode == "updates":
            async for msg in self._adapt_updates_chunk(chunk):
                yield msg
        elif mode == "messages":
            async for msg in self._adapt_messages_chunk(chunk):
                yield msg

    async def _adapt_updates_chunk(self, chunk: Any) -> AsyncGenerator[dict, None]:
        if not isinstance(chunk, dict):
            return

        # 检测上下文压缩事件（SummarizationMiddleware 完成压缩后写入）
        if "_summarization_event" in chunk:
            event = chunk["_summarization_event"]
            if isinstance(event, dict) and event.get("cutoff_index", 0) > 0:
                from config import get_logger as _get_logger
                _get_logger(__name__).info(
                    f"[Adapter] 检测到上下文压缩: cutoff={event.get('cutoff_index')}, "
                    f"file={event.get('file_path', 'N/A')}"
                )
                # 清除压缩过程中泄露的推理/聊天文本，防止被 finalize 落库
                self._summarization_suppress = True
                self._current_llm_text = ""
                self._current_reasoning_text = ""
                self._chat_streamed = False
                self._reasoning_notified = False
                self._reasoning_segment_flushed = False
                self._think_parser.reset()
                # 同时清理工具调用累积器中的待处理数据（摘要模型调用可能产生幻觉工具）
                self._merge_accumulated_tool_calls()
                self._pending_tool_calls.clear()
                self._pending_tool_inputs.clear()
                self._pending_subagent_inputs.clear()

                yield self._enrich(
                    AgentMessage(
                        type=MessageType.CONTEXT_COMPRESSED,
                        sender=SenderType.SYSTEM,
                        session_id=self.session_id,
                        content={
                            "message": "上下文已压缩",
                            "cutoff_index": event.get("cutoff_index"),
                            "file_path": event.get("file_path"),
                        },
                        save=False,
                    ),
                    track_step=False,
                )

        if "__interrupt__" not in chunk:
            return
        actions = parse_hitl_actions_from_interrupts(chunk["__interrupt__"])
        async for msg in self._yield_interrupt_messages(actions):
            yield msg

    async def build_interrupt_from_state(self, state) -> AsyncGenerator[dict, None]:
        """从 graph state 快照补检 pending interrupt。"""
        interrupts = getattr(state, "interrupts", None) or ()
        if not interrupts:
            return
        actions = parse_hitl_actions_from_interrupts(interrupts)
        async for msg in self._yield_interrupt_messages(actions):
            yield msg

    async def _yield_interrupt_messages(
        self, actions: List[Dict[str, Any]]
    ) -> AsyncGenerator[dict, None]:
        if not actions:
            return
        self.interrupted = True
        # 审批前先落库推理，避免 interrupt 后 finalize 再把思考插在审批与执行之间
        if (self._current_reasoning_text or "").strip() and not self._reasoning_segment_flushed:
            async for msg in self._flush_reasoning_text():
                yield msg
            self._reasoning_segment_flushed = True
        content = build_interrupt_content(
            actions,
            run_id=self.run_id,
            conversation_id=self.conversation_id,
        )
        yield self._enrich(
            AgentMessage(
                type=MessageType.INTERRUPT,
                sender=SenderType.SYSTEM,
                session_id=self.session_id,
                content=content,
                save=True,
            )
        )
        yield self._enrich(
            AgentMessage(
                type=MessageType.STATUS,
                sender=SenderType.SYSTEM,
                session_id=self.session_id,
                content=f"等待用户审批 {len(content['actions'])} 个工具调用...",
                save=True,
            )
        )

    async def _adapt_messages_chunk(self, chunk: Any) -> AsyncGenerator[dict, None]:
        try:
            message_chunk, metadata = chunk
        except (TypeError, ValueError):
            return

        node = metadata.get("langgraph_node", "unknown")

        # 诊断日志：记录每次进入 _adapt_messages_chunk 的基本信息
        from config import get_logger
        _dbg = get_logger(__name__)
        has_content = bool(getattr(message_chunk, "content", None))
        has_tcc = bool(getattr(message_chunk, "tool_call_chunks", None))
        has_tc = bool(getattr(message_chunk, "tool_calls", None))
        if has_tcc or has_tc:  # 只记录与工具调用相关的chunk
            tcc_info = ""
            if has_tcc:
                tcc_lens = [len(tc.get("args") or "") for tc in message_chunk.tool_call_chunks]
                tcc_info = f"tcc={len(message_chunk.tool_call_chunks)}, args_lens={tcc_lens}"
            tc_info = ""
            if has_tc:
                tc_info = f"tc={len(message_chunk.tool_calls)}"
            _dbg.info(
                f"[TOOL_ARGS_STREAM] node={node}, content={has_content}, "
                f"last_node={self._last_node}, {tcc_info} {tc_info}"
            )

        # 离开 model 节点时：先刷推理/回复，再 emit 缓存的 tool_call start
        if self._last_node == "model" and node != "model":
            async for msg in self.finalize_model_segment():
                yield msg
            async for msg in self._emit_pending_tool_starts():
                yield msg

        if node == "model":
            if self._last_node != "model":
                self._reasoning_notified = False
                self._reasoning_segment_flushed = False
                self._think_parser.reset()
                # 进入新的 model 段，清除压缩抑制标志（正常输出不受影响）
                self._summarization_suppress = False

            # 优先处理 reasoning_content 字段（CoT 模型）；有则禁用 think 标签解析
            reasoning_chunk = extract_reasoning_from_chunk(message_chunk)
            if reasoning_chunk:
                self._think_parser.disable()
                async for msg in self._append_reasoning_chunk(reasoning_chunk):
                    yield msg

            raw_content = getattr(message_chunk, "content", None)
            if raw_content:
                content_text = self._stringify_content(raw_content)
                if content_text:
                    thinking_part, answer_part = self._think_parser.process_chunk(content_text)

                    if thinking_part:
                        async for msg in self._append_reasoning_chunk(thinking_part):
                            yield msg

                    if answer_part:
                        async for msg in self._flush_reasoning_if_needed():
                            yield msg
                        async for msg in self._emit_chat_chunk(answer_part):
                            yield msg

            # tool_calls / tool_call_chunks 在流式 chunk 中可能分多次到达
            if hasattr(message_chunk, "tool_call_chunks") and message_chunk.tool_call_chunks:
                self._tool_call_accumulator.accumulate_chunks(message_chunk.tool_call_chunks)
                # 流式输出工具参数增量（write_file/edit_file 的大文本字段）
                async for msg in self._emit_tool_args_stream():
                    yield msg

            tool_calls = getattr(message_chunk, "tool_calls", None) or []
            if tool_calls:
                self._pending_tool_calls = list(tool_calls)
                for tc in tool_calls:
                    tc_id = tc.get("id") if isinstance(tc, dict) else None
                    if not tc_id:
                        continue
                    _name, tc_args = normalize_tool_call(tc)
                    if tc_args:
                        self._pending_tool_inputs[tc_id] = tc_args
                    if tc_args and (_name == "task" or is_task_tool_call(tc)):
                        self._pending_subagent_inputs[tc_id] = tc_args
                async for msg in self._maybe_emit_ready_task_starts():
                    yield msg

        elif node == "tools":
            async for msg in self._emit_pending_tool_starts():
                yield msg
            messages = message_chunk if isinstance(message_chunk, list) else [message_chunk]
            for msg in messages:
                async for out in self._adapt_tool_message(msg):
                    yield out

        self._last_node = node

    async def _maybe_emit_ready_task_starts(self) -> AsyncGenerator[dict, None]:
        """参数已齐的 task 立即发 subagent_start（不等到离开 model 节点）。"""
        for tc in list(self._pending_tool_calls):
            tool_call_id = tc.get("id") if isinstance(tc, dict) else None
            if not tool_call_id or tool_call_id in self._emitted_tool_starts:
                continue
            tool_name, tool_input = normalize_tool_call(tc)
            if tool_name != "task" or not task_call_args_ready(tool_input):
                continue
            async for msg in self._emit_single_tool_start(tc, tool_name, tool_input):
                yield msg

    async def _emit_single_tool_start(
        self, tc: dict, tool_name: str, tool_input: dict
    ) -> AsyncGenerator[dict, None]:
        tool_call_id = tc.get("id") or str(uuid.uuid4())
        if tool_call_id in self._emitted_tool_starts:
            return
        if tool_name == "unknown" and not tool_input:
            return

        # stream_mode=tools 可能在 model 段 finalize 之前到达，先落库思考再 emit tool start
        # HITL resume 路径跳过：避免思考插在已审批 interrupt 与 execute 之间
        if (self._current_reasoning_text or "").strip() and not self._hitl_resume_request_id:
            async for msg in self._flush_reasoning_text():
                yield msg

        self._emitted_tool_starts.add(tool_call_id)
        if tool_name and tool_name != "unknown":
            self._emitted_tool_names[tool_call_id] = tool_name
        if tool_name not in _TASK_TOOLS and tool_input:
            self._pending_tool_inputs[tool_call_id] = tool_input
        msg_type = _tool_message_type(tool_name, True)

        subagent_run_id = None
        if tool_name in _TASK_TOOLS:
            subagent_run_id = self._active_subagents.get(tool_call_id)
            if not subagent_run_id:
                subagent_run_id = str(uuid.uuid4())
                self._active_subagents[tool_call_id] = subagent_run_id
            self._active_subagent_inputs[subagent_run_id] = tool_input
            self._pending_subagent_inputs.pop(tool_call_id, None)
            self._subagent_stack.append({
                "subagent_run_id": subagent_run_id,
                "parent_tool_call_id": tool_call_id,
            })

        content = base_content(
            run_id=self.run_id,
            conversation_id=self.conversation_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            args=tool_input,
            subagent_run_id=subagent_run_id,
        )
        yield self._enrich(
            AgentMessage(
                type=msg_type,
                sender=SenderType.SYSTEM,
                session_id=self.session_id,
                content=content,
                save=True,
            )
        )
        await self._link_resumed_tool_call(tool_name, tool_input, tool_call_id)

        # 工具调用正式发出后，清理对应的流式 args 追踪状态
        if tool_name in _STREAMABLE_TOOLS and tool_call_id:
            for index in list(self._streamed_tool_args):
                acc = self._tool_call_accumulator.peek(index)
                if acc and acc.get("id") == tool_call_id:
                    del self._streamed_tool_args[index]
                    self._tool_progress_emitted.discard(index)
                    break

    async def _ensure_subagent_start(
        self,
        tool_call_id: str,
        tool_input: dict,
        subagent_run_id: Optional[str] = None,
    ) -> AsyncGenerator[dict, None]:
        """在 subagent_end 前补发缺失的 subagent_start。"""
        if tool_call_id and tool_call_id in self._emitted_tool_starts:
            return

        if not subagent_run_id and tool_call_id:
            subagent_run_id = self._active_subagents.get(tool_call_id)
        if not subagent_run_id:
            subagent_run_id = str(uuid.uuid4())
            if tool_call_id:
                self._active_subagents[tool_call_id] = subagent_run_id

        self._active_subagent_inputs.setdefault(subagent_run_id, tool_input or {})

        tc = {
            "id": tool_call_id or str(uuid.uuid4()),
            "name": "task",
            "args": tool_input or {},
        }
        async for msg in self._emit_single_tool_start(tc, "task", tool_input or {}):
            yield msg

    async def _emit_tool_args_stream(self) -> AsyncGenerator[dict, None]:
        """对 _STREAMABLE_TOOLS 的工具调用，检查累积状态并流式推送。

        两个阶段：
        1. 内容尚未到达 → 如果工具名已知且有关键参数可提取，发送进度通知
           (delta="" + key_params)，让前端展示"正在生成 xxx..."
        2. 内容开始到达 → 逐 chunk 推送 delta 增量
        """
        for index, state in self._tool_call_accumulator.peek_all().items():
            name = state.get("name")
            if not name or name not in _STREAMABLE_TOOLS:
                continue

            field_name = _STREAMABLE_TOOL_CONTENT_FIELDS.get(name)
            if not field_name:
                continue

            args_str = state.get("args") or ""
            tool_call_id = state.get("id") or None
            current_content = extract_streaming_json_field(args_str, field_name)
            prev_content = self._streamed_tool_args.get(index, "")

            # 阶段 1：内容未开始 → 尝试发送进度通知
            if len(current_content) <= len(prev_content):
                if index not in self._tool_progress_emitted:
                    key_params = _extract_tool_key_params(name, args_str)
                    self._tool_progress_emitted.add(index)
                    yield self._enrich(
                        AgentMessage(
                            type=MessageType.TOOL_ARGS_STREAM,
                            sender=SenderType.SYSTEM,
                            session_id=self.session_id,
                            content={
                                "tool_name": name,
                                "tool_call_id": tool_call_id,
                                "tool_call_index": index,
                                "field_name": field_name,
                                "delta": "",
                                "key_params": key_params,
                            },
                            save=False,
                        ),
                        track_step=False,
                    )
                continue

            # 阶段 2：内容已到达 → 推送增量
            delta = current_content[len(prev_content):]
            self._streamed_tool_args[index] = current_content

            if delta:
                yield self._enrich(
                    AgentMessage(
                        type=MessageType.TOOL_ARGS_STREAM,
                        sender=SenderType.SYSTEM,
                        session_id=self.session_id,
                        content={
                            "tool_name": name,
                            "tool_call_id": tool_call_id,
                            "tool_call_index": index,
                            "field_name": field_name,
                            "delta": delta,
                        },
                        save=False,
                    ),
                    track_step=False,
                )

    def _merge_accumulated_tool_calls(self) -> None:
        """将流式 tool_call_chunks 合并进 pending 列表。

        直接从 accumulator 读取原始累积数据，不经过 parse_and_clear 的
        JSON 解析，让模型流式参数自然拼接。参数解析由下游 normalize_tool_call
        统一处理（失败时自动包 _raw）。
        """
        raw_accumulated = self._tool_call_accumulator._accumulated
        for idx, tc in raw_accumulated.items():
            tool_name = tc.get("name")
            tool_id = tc.get("id")
            args_raw = tc.get("args") or ""

            if not tool_name or not tool_id:
                continue

            # 校验是否为已注册工具（过滤幻觉工具调用）
            valid_names = self._tool_call_accumulator._valid_tool_names
            if valid_names and tool_name not in valid_names:
                continue

            entry = {
                "id": tool_id,
                "name": tool_name,
                "args": args_raw,  # 原始字符串，不解析，由 normalize_tool_call 兜底
            }
            tc_id = entry["id"]
            if tc_id:
                self._pending_tool_inputs[tc_id] = args_raw
            existing_ids = {
                item.get("id") for item in self._pending_tool_calls if isinstance(item, dict)
            }
            if tc_id not in existing_ids:
                self._pending_tool_calls.append(entry)

        self._tool_call_accumulator._accumulated.clear()
        self._streamed_tool_args.clear()
        self._tool_progress_emitted.clear()

    async def _ensure_tool_start(
        self,
        tool_call_id: str,
        tool_name: str,
        tool_input: Optional[dict] = None,
    ) -> AsyncGenerator[dict, None]:
        """在 tool_result 前补发缺失的 tool_call（MCP 等工具与 task 同理）。"""
        if tool_call_id and tool_call_id in self._emitted_tool_starts:
            return
        if not tool_name or tool_name == "unknown":
            return

        resolved_input = tool_input
        if resolved_input is None and tool_call_id:
            resolved_input = self._pending_tool_inputs.get(tool_call_id, {})
        resolved_input = resolved_input or {}

        tc = {
            "id": tool_call_id or str(uuid.uuid4()),
            "name": tool_name,
            "args": resolved_input,
        }
        async for msg in self._emit_single_tool_start(tc, tool_name, resolved_input):
            yield msg

    async def _emit_pending_tool_starts(self) -> AsyncGenerator[dict, None]:
        self._merge_accumulated_tool_calls()
        for tc in self._pending_tool_calls:
            tool_name, tool_input = normalize_tool_call(tc)
            async for msg in self._emit_single_tool_start(tc, tool_name, tool_input):
                yield msg
        self._pending_tool_calls = []

    async def _adapt_tool_message(self, msg: Any) -> AsyncGenerator[dict, None]:
        if not isinstance(msg, ToolMessage):
            return

        tool_name = getattr(msg, "name", None) or "unknown"
        result_text = msg.content if isinstance(msg.content, str) else str(msg.content or "")
        tool_call_id = getattr(msg, "tool_call_id", "") or ""
        pending_args = self._pending_tool_inputs.get(tool_call_id, {})
        # 兼容 _merge_accumulated_tool_calls 透传的原始 args 字符串
        if isinstance(pending_args, str):
            try:
                pending_args = json.loads(pending_args) if pending_args.strip() else {}
            except json.JSONDecodeError:
                pending_args = {}
        resolved_name = self._resolve_tool_name(tool_call_id, tool_name, pending_args)

        if self._tool_already_completed(tool_call_id):
            self._record_conversation_file(
                tool_name=resolved_name,
                tool_call_id=tool_call_id,
                args=pending_args,
                workspace_path=_tool_result_fields(result_text).get("workspace_path"),
                result_text=result_text,
            )
            return

        fields = _tool_result_fields(result_text)
        summary = fields["tool_result"]
        ref_id = fields.get("ref_id")
        truncated = fields.get("truncated", False)

        msg_type = _tool_message_type(resolved_name, False)
        fallback_args = self._pending_subagent_inputs.pop(tool_call_id, {})
        subagent_run_id = self._active_subagents.get(tool_call_id)

        if resolved_name in _TASK_TOOLS:
            async for start_msg in self._ensure_subagent_start(
                tool_call_id, fallback_args, subagent_run_id=subagent_run_id
            ):
                yield start_msg
            subagent_run_id = self._active_subagents.pop(tool_call_id, None) or subagent_run_id
            if not subagent_run_id:
                subagent_run_id = str(uuid.uuid4())
            stored_args = self._active_subagent_inputs.pop(subagent_run_id, fallback_args)
        else:
            tool_input = _strip_runtime(self._pending_tool_inputs.pop(tool_call_id, {}))
            resolved_name = self._resolve_tool_name(tool_call_id, resolved_name, tool_input)
            async for start_msg in self._ensure_tool_start(
                tool_call_id, resolved_name, tool_input
            ):
                yield start_msg
            subagent_run_id = self._active_subagents.pop(tool_call_id, None)
            stored_args = tool_input or {}

        content = base_content(
            run_id=self.run_id,
            conversation_id=self.conversation_id,
            tool_name=resolved_name,
            tool_call_id=tool_call_id,
            tool_result=summary,
            ref_id=ref_id,
            truncated=truncated,
            workspace_path=fields.get("workspace_path"),
            subagent_run_id=subagent_run_id,
        )
        if stored_args:
            content["args"] = stored_args
        if resolved_name in _TASK_TOOLS and subagent_run_id:
            content["final_report"] = summary
            content["args"] = stored_args
            self._pending_subagent_inputs.pop(tool_call_id, None)
            self._pop_subagent_stack(subagent_run_id)

        yield self._enrich(
            AgentMessage(
                type=msg_type,
                sender=SenderType.SYSTEM,
                session_id=self.session_id,
                content=content,
                save=True,
            )
        )
        if tool_call_id:
            self._persisted_tool_results.add(tool_call_id)
        self._record_conversation_file(
            tool_name=resolved_name,
            tool_call_id=tool_call_id,
            args=stored_args,
            workspace_path=fields.get("workspace_path"),
            result_text=result_text,
        )

    def _pop_subagent_stack(self, subagent_run_id: Optional[str]) -> None:
        if not subagent_run_id or not self._subagent_stack:
            return
        for i in range(len(self._subagent_stack) - 1, -1, -1):
            if self._subagent_stack[i].get("subagent_run_id") == subagent_run_id:
                self._subagent_stack.pop(i)
                return

    def _active_subagent_context(self) -> Optional[dict]:
        return self._subagent_stack[-1] if self._subagent_stack else None

    def _is_subagent_namespace(self, namespace: tuple) -> bool:
        return bool(namespace)

    async def adapt_tools_stream_chunk(
        self,
        namespace: tuple,
        payload: dict,
    ) -> AsyncGenerator[dict, None]:
        """处理 stream_mode=tools 事件（含子图 namespace）。"""
        if not isinstance(payload, dict):
            return
        event = payload.get("event") or ""
        tool_call_id = str(payload.get("tool_call_id") or "")
        raw_tool_name = payload.get("tool_name") or payload.get("name") or "unknown"
        if not tool_call_id:
            return

        in_subagent = self._is_subagent_namespace(namespace)
        ctx = self._active_subagent_context() if in_subagent else None
        if in_subagent and not ctx:
            if self._subagent_stack:
                ctx = self._subagent_stack[-1]
            elif self._active_subagents:
                parent_id = list(self._active_subagents.keys())[-1]
                ctx = {
                    "subagent_run_id": self._active_subagents[parent_id],
                    "parent_tool_call_id": parent_id,
                }

        if event == "tool-started":
            tool_input = payload.get("input") or {}
            if isinstance(tool_input, str):
                try:
                    tool_input = json.loads(tool_input) if tool_input.strip() else {}
                except json.JSONDecodeError:
                    tool_input = {"_raw": tool_input}
            if not isinstance(tool_input, dict):
                tool_input = {}

            self._pending_tool_inputs[tool_call_id] = tool_input
            tool_name = self._resolve_tool_name(tool_call_id, raw_tool_name, tool_input)
            if tool_name and tool_name != "unknown":
                self._emitted_tool_names[tool_call_id] = tool_name

            if in_subagent and ctx:
                async for msg in self._emit_subagent_tool_event(
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    args=tool_input,
                    phase="start",
                    subagent_run_id=ctx.get("subagent_run_id"),
                    parent_tool_call_id=ctx.get("parent_tool_call_id"),
                ):
                    yield msg
                return

            if tool_call_id in self._emitted_tool_starts:
                return
            tc = {"id": tool_call_id, "name": tool_name, "args": tool_input}
            async for msg in self._emit_single_tool_start(tc, tool_name, tool_input):
                yield msg

        elif event in ("tool-finished", "tool-error"):
            result_text = _stringify_tool_stream_output(payload.get("output"))
            if event == "tool-error":
                result_text = payload.get("message") or result_text or "tool error"
            if self._tool_already_completed(tool_call_id):
                return

            stored_args = self._pending_tool_inputs.get(tool_call_id) or {}
            tool_name = self._resolve_tool_name(tool_call_id, raw_tool_name, stored_args)
            if tool_name and tool_name != "unknown":
                self._emitted_tool_names[tool_call_id] = tool_name

            if in_subagent and ctx:
                stored_args = self._pending_tool_inputs.pop(tool_call_id, stored_args)
                async for msg in self._emit_subagent_tool_event(
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    args=stored_args,
                    phase="result",
                    subagent_run_id=ctx.get("subagent_run_id"),
                    parent_tool_call_id=ctx.get("parent_tool_call_id"),
                    result_text=result_text,
                ):
                    yield msg
                self._persisted_tool_results.add(tool_call_id)
                return

            from langchain.messages import ToolMessage

            fake = ToolMessage(
                content=result_text,
                name=tool_name,
                tool_call_id=tool_call_id,
            )
            async for msg in self._adapt_tool_message(fake):
                yield msg
            self._persisted_tool_results.add(tool_call_id)

    async def _emit_subagent_tool_event(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        args: dict,
        phase: str,
        subagent_run_id: Optional[str],
        parent_tool_call_id: Optional[str],
        result_text: str = "",
    ) -> AsyncGenerator[dict, None]:
        fields = _tool_result_fields(result_text) if phase == "result" else {}
        content = base_content(
            run_id=self.run_id,
            conversation_id=self.conversation_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            args=args or {},
            subagent_run_id=subagent_run_id,
            parent_tool_call_id=parent_tool_call_id,
            event_phase=phase,
        )
        if phase == "result":
            content["tool_result"] = fields.get("tool_result") or truncate_summary(result_text)
            content["summary"] = content["tool_result"]
            content["truncated"] = fields.get("truncated", False)
            content["ref_id"] = fields.get("ref_id")
            content["workspace_path"] = fields.get("workspace_path")

        yield self._enrich(
            AgentMessage(
                type=MessageType.SUBAGENT_EVENT,
                sender=SenderType.SYSTEM,
                session_id=self.session_id,
                content=content,
                save=True,
            )
        )
        if phase == "result":
            from deep_agent_server.conversation_files import resolve_write_tool_name

            effective_tool = resolve_write_tool_name(tool_name, args or {}) or tool_name
            self._record_conversation_file(
                tool_name=effective_tool,
                tool_call_id=tool_call_id,
                args=args or {},
                workspace_path=fields.get("workspace_path"),
                result_text=result_text,
            )

    async def _adapt_subagent_tool_from_message(
        self,
        msg: Any,
        *,
        phase: str = "result",
        parent_tool_call_ids: Optional[set] = None,
    ) -> AsyncGenerator[dict, None]:
        tool_name = getattr(msg, "name", None) or "unknown"
        tool_call_id = getattr(msg, "tool_call_id", "") or ""
        ctx = self._active_subagent_context()
        result_text = msg.content if isinstance(msg.content, str) else str(msg.content or "")
        args = self._pending_tool_inputs.pop(tool_call_id, {})
        resolved_name = self._resolve_tool_name(tool_call_id, tool_name, args)
        if resolved_name and resolved_name != "unknown":
            self._emitted_tool_names[tool_call_id] = resolved_name
        async for out in self._emit_subagent_tool_event(
            tool_name=resolved_name,
            tool_call_id=tool_call_id,
            args=args,
            phase=phase,
            subagent_run_id=(ctx or {}).get("subagent_run_id"),
            parent_tool_call_id=(ctx or {}).get("parent_tool_call_id"),
            result_text=result_text if phase == "result" else "",
        ):
            yield out

    def end_message(self, reason: EndReason) -> dict:
        """生成 run 结束消息（COMPLETED / STOP / ERROR）。"""
        reason_text = reason.value if isinstance(reason, EndReason) else str(reason)
        return self._enrich(
            AgentMessage.end(session_id=self.session_id, reason=reason_text, save=True)
        )

    def conversation_files_message(self) -> Optional[dict]:
        """生成本轮 conversation 写入文件列表（WORKSPACE_FILE，存库供历史恢复）。"""
        from deep_agent_server.conversation_files import build_conversation_files_payload

        payload = build_conversation_files_payload(
            user_id=self.user_id,
            session_id=self.session_id,
            conversation_id=self.conversation_id,
            run_id=self.run_id,
            recorded=self._conversation_files,
        )
        if not payload.get("files"):
            return None
        return self._enrich(
            AgentMessage(
                type=MessageType.WORKSPACE_FILE,
                sender=SenderType.SYSTEM,
                session_id=self.session_id,
                content=payload,
                save=True,
            )
        )

    async def async_conversation_files_message(self) -> Optional[dict]:
        """内存登记为空时，从 DB / 磁盘最近写入回填文件列表。"""
        msg = self.conversation_files_message()
        if msg:
            return msg
        cards: List[dict] = []
        try:
            from deep_agent_server.workspace_service import (
                list_conversation_workspace_cards,
                list_recent_session_workspace_cards,
            )

            if self.conversation_id:
                cards = await list_conversation_workspace_cards(
                    self.user_id, self.session_id, self.conversation_id
                )
            if not cards:
                cards = await list_recent_session_workspace_cards(
                    self.user_id,
                    self.session_id,
                    self.conversation_id or "",
                )
        except Exception as e:
            from config import get_logger

            get_logger(__name__).warning(
                f"[DeepAdapter] conversation files DB fallback failed: {e}"
            )
            return None
        if not cards:
            return None
        from deep_agent_server.conversation_files import _MAX_CARD_FILES

        if len(cards) > _MAX_CARD_FILES:
            cards = cards[:_MAX_CARD_FILES]
        payload = {
            "agent_mode": "deep",
            "run_id": self.run_id,
            "conversation_id": self.conversation_id,
            "files": cards,
        }
        return self._enrich(
            AgentMessage(
                type=MessageType.WORKSPACE_FILE,
                sender=SenderType.SYSTEM,
                session_id=self.session_id,
                content=payload,
                save=True,
            )
        )

    def _record_conversation_file(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        args: Optional[dict],
        workspace_path: Optional[str],
        result_text: str,
    ) -> None:
        from deep_agent_server.conversation_files import (
            agent_path_to_virtual,
            is_write_success,
            resolve_agent_file_path,
            resolve_execute_output_tool,
            resolve_write_tool_name,
        )

        if not is_write_success(result_text):
            return
        effective_tool = resolve_write_tool_name(tool_name, args)
        if not effective_tool:
            # execute 工具：仅收集命令中显式指定的文档输出文件
            if tool_name == "execute":
                effective_tool = resolve_execute_output_tool(args, result_text)
            if not effective_tool:
                return
        agent_path = resolve_agent_file_path(
            tool_name=effective_tool,
            args=args or {},
            workspace_path=workspace_path,
            session_id=self.session_id,
            result_text=result_text,
        )
        if not agent_path:
            from config import get_logger

            get_logger(__name__).debug(
                "[DeepAdapter] skip conversation file record tool=%s call=%s",
                tool_name,
                tool_call_id,
            )
            return
        virtual_path = agent_path_to_virtual(self.session_id, agent_path)
        if not virtual_path:
            return
        self._conversation_files[virtual_path] = {
            "agent_path": agent_path,
            "tool_call_id": tool_call_id,
            "tool_name": effective_tool,
        }
        self._schedule_conversation_file_index(virtual_path)

    def _schedule_conversation_file_index(self, virtual_path: str) -> None:
        import asyncio

        async def _run() -> None:
            try:
                from deep_agent_server.workspace import compute_sha256
                from deep_agent_server.workspace_service import resolve_virtual_path
                from db import deep_storage

                abs_path, rel_path, _, file_kind = resolve_virtual_path(
                    self.user_id, virtual_path
                )
                if file_kind != "workspace" or not abs_path.is_file():
                    return
                await deep_storage.upsert_workspace_file_index(
                    self.user_id,
                    self.session_id,
                    rel_path,
                    abs_path.stat().st_size,
                    compute_sha256(abs_path),
                    file_kind=file_kind,
                    mime_type=_mime_for_path_adapter(abs_path),
                    storage_path=str(abs_path),
                    conversation_id=self.conversation_id,
                )
            except Exception as e:
                from config import get_logger

                get_logger(__name__).warning(
                    f"[ConversationFiles] index failed path={virtual_path}: {e}"
                )

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_run())
        except RuntimeError:
            asyncio.run(_run())

    def status_message(self, text: str) -> dict:
        """生成 Deep 运行时状态通知（DEEP_STATUS，不存库）。"""
        content = base_content(
            run_id=self.run_id,
            conversation_id=self.conversation_id,
            message=text,
        )
        return self._enrich(
            AgentMessage(
                type=MessageType.DEEP_STATUS,
                sender=SenderType.SYSTEM,
                session_id=self.session_id,
                content=content,
                save=False,
            ),
            track_step=False,
        )
