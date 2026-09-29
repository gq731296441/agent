"""Deep Agent 事件 → AgentMessage 适配。

Deep Agent 基于 LangGraph astream 输出 messages / updates 两种流模式，
本模块负责将其统一转换为前端可消费的 AgentMessage 字典，并补充
run_id、conversation_id、step_index 等 Deep 模式专有字段。
"""

import json
import mimetypes
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

from langchain.messages import ToolMessage


def _to_timestamp(value: Optional[Any]) -> Optional[float]:
    """datetime / isoformat 字符串 / 数字 → epoch 秒时间戳（run 时间窗口用）。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    if hasattr(value, "timestamp"):
        return value.timestamp()
    return None

from common.enums import MessageType, SenderType, EndReason
from common.schemas import AgentMessage
from config.config import app_config
from deep_agent_server.artifact import parse_spill_content, truncate_summary
from deep_agent_server.hitl.interrupt_handler import (
    build_interrupt_content,
    parse_hitl_actions_from_interrupts,
)
from deep_agent_server.hitl.tool_call_link import match_hitl_action_index
from deep_agent_server.subagent_record import summarize_subagent_input, task_subagent_name
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


def _extract_tool_call_id_from_ns(namespace: tuple) -> Optional[str]:
    """从 v2 namespace 提取 task 工具的 tool_call_id。

    LangGraph v2 子图流中 namespace="tools:<tool_call_id>",
    其中 tool_call_id 就是主 agent 调用 task() 时的 call_* ID。
    """
    ids = _extract_tool_call_ids_from_ns(namespace)
    return ids[0] if ids else None


def _extract_tool_call_ids_from_ns(namespace: tuple) -> list:
    """从 v2 namespace 提取所有 tools: 段的 tool_call_id。

    嵌套子图（subagent 内部工具调用）的 namespace 可能包含多个 tools: 段，
    如 ("tools:<task_id>", "tools:<inner_tool_call_id>", ...)。
    返回全部 ID，供上层按序匹配 _active_subagents 映射。
    """
    if not namespace:
        return []
    return [seg.split(":", 1)[1] for seg in namespace if seg.startswith("tools:")]


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
        agent_wrapper=None,
    ):
        self.session_id = session_id
        self.run_id = run_id
        self.conversation_id = conversation_id
        self.user_id = user_id
        self.agent_id = agent_id
        self.group_id = group_id
        self.step_index = int(initial_step_index or 0)
        # run 开始时刻（runner 启动后赋值）：execute 兜底扫描窗口下界用，
        # 避免把上一轮生成的文件误收为本轮新文件
        self.run_started_at: Optional[datetime] = None
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
        # 当前 stream 的 LangGraph namespace（子图标识），用于并发子 agent 归属判断
        self._active_namespace: tuple = ()
        # 上次 model 节点对应的 namespace，检测跨子 agent 切換时强制 flush 缓冲区
        self._last_model_namespace: tuple = ()
        # 按 namespace 保存 tool_call_accumulator 状态，防止并发子 agent 的 chunk 交叉污染
        self._ns_accumulator_backup: dict[tuple, dict] = {}
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
        # HTML 分享链接：agent_wrapper 引用 + 本轮新创建的链接
        self._agent_wrapper = agent_wrapper
        self._html_share_links: Dict[str, str] = {}
        # 当前 model 段是否已将推理刷为 reasoning_final（避免 CoT 每 chunk 重复刷库）
        self._reasoning_segment_flushed: bool = False
        # 上下文压缩后抑制当前 model 段输出（防止摘要推理和摘要内容泄露到前端/DB）
        self._summarization_suppress: bool = False
        # task 输入 FIFO 队列：namespace UUID 与 LLM tool_call_id 不匹配时，
        # 按子智能体派发顺序关联正确的 task_input
        self._unmatched_subagent_uuids: deque = deque()

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

    def _wrap_subagent_event(self, enriched: dict, subevent_type: str) -> dict:
        """为 subagent_event 消息添加 subevent_type 字段，方便前端按原类型渲染。"""
        enriched["subevent_type"] = subevent_type
        return enriched

    def _current_subagent_id(self) -> Optional[str]:
        """返回当前子 agent 的 UUID。

        v2 namespace "tools:<tool_call_id>" → _active_subagents[tool_call_id] → UUID。
        嵌套子图 namespace 可能含多个 tools: 段（subagent 内部工具调用），
        依次查找能命中 _active_subagents 映射的 task tool_call_id。
        未命中时返回 None，由调用方回退到 _subagent_stack —— 绝不现场生成
        幽灵 UUID。否则 subagent 内部工具调用（write_file 等）的 start/result
        事件会归属到不同的 subagent_run_id，前端无法将同一子智能体的操作
        渲染到同一分组。
        """
        ns = getattr(self, "_active_namespace", ()) or ()
        if not ns:
            return None
        for tool_call_id in _extract_tool_call_ids_from_ns(ns):
            sid = self._active_subagents.get(tool_call_id)
            if sid:
                return sid
        return None

    def _current_subagent_id_or_stack(self) -> Optional[str]:
        """当前子 agent UUID；namespace 无法映射时回退到子 agent 栈顶。

        用于消息归属判断（chat / reasoning / 工具参数流等），保证 subagent
        上下文中的消息始终归属到真实 subagent_run_id——即使 namespace 只含
        subagent 内部工具调用的 tool_call_id（无法命中 _active_subagents），
        也不会丢失归属或产生幽灵 UUID。
        """
        sid = self._current_subagent_id()
        if sid:
            return sid
        if self._subagent_stack:
            return self._subagent_stack[-1].get("subagent_run_id")
        return None

    def _emit_subagent_start_if_needed(self, tool_call_id: str) -> None:
        """首次见到 tool_call_id 的 namespace 事件时，发射 SUBAGENT_START。

        仅处理 task 子 agent 的 namespace 事件；subagent 内部工具调用
        （write_file/edit_file 等）的 namespace 不在此建立归属，直接跳过，
        避免为内部工具调用现场生成幽灵 subagent_run_id 并误发 SUBAGENT_START。
        """
        if not hasattr(self, "_started_subagent_ids"):
            self._started_subagent_ids: set[str] = set()
        if tool_call_id in self._started_subagent_ids:
            return
        # 非 task 门：tool_call_id 既无 task 映射、又无待处理 task 输入、
        # FIFO 队列也无候选时，说明是 subagent 内部工具调用的 namespace
        if (tool_call_id not in self._active_subagents
                and tool_call_id not in self._pending_subagent_inputs
                and not self._unmatched_subagent_uuids):
            return
        self._started_subagent_ids.add(tool_call_id)

        # 通过 _active_subagents 获取 UUID（_emit_single_tool_start 已建立映射）
        sid = self._active_subagents.get(tool_call_id)
        if not sid:
            sid = str(uuid.uuid4())
            self._active_subagents[tool_call_id] = sid

        # 查找 task input：按 UUID 查 _active_subagent_inputs →
        # 按 tool_call_id 查 _pending_subagent_inputs →
        # 按 FIFO 队列（namespace UUID 与 LLM call_* ID 不匹配时回退）
        task_input = self._active_subagent_inputs.get(sid)
        if not task_input:
            task_input = self._pending_subagent_inputs.pop(tool_call_id, {})
        if not task_input:
            # namespace 中的 tool_call_id（LangGraph 内部 UUID）可能与
            # LLM 的 tool_call_id（call_* 格式）不同。此时按派发顺序从
            # FIFO 队列取出最近一个 task UUID 获取正确的 args。
            while self._unmatched_subagent_uuids:
                candidate_uuid = self._unmatched_subagent_uuids.popleft()
                candidate = self._active_subagent_inputs.get(candidate_uuid)
                if candidate and isinstance(candidate, dict) and (
                    candidate.get("subagent_type") or candidate.get("description")
                ):
                    task_input = candidate
                    # 将 namespace UUID 关联到正确的子 agent UUID，
                    # 并同步更新 sid，确保 subagent_start 与后续
                    # subagent_event 使用相同的 subagent_run_id
                    self._active_subagents[tool_call_id] = candidate_uuid
                    sid = candidate_uuid
                    break
        if not task_input:
            # 所有回退均失败：记录告警信息辅助排查
            from config import get_logger as _get_logger
            _get_logger(__name__).warning(
                f"[Adapter] _emit_subagent_start_if_needed 无法定位 task_input: "
                f"tool_call_id={tool_call_id}, sid={sid}, "
                f"active_subagents_keys={list(self._active_subagents.keys())}, "
                f"active_subagent_inputs_keys={list(self._active_subagent_inputs.keys())}, "
                f"unmatched_queue={list(self._unmatched_subagent_uuids)}"
            )
        if not task_input:
            task_input = {}
        self._active_subagent_inputs.setdefault(sid, task_input)

        name = task_subagent_name(task_input) if task_input else "general-purpose"
        input_summary = summarize_subagent_input(
            task_input, max_chars=app_config.CONTENT_SUMMARY_MAX_CHARS
        )

        content = base_content(
            run_id=self.run_id,
            conversation_id=self.conversation_id,
            tool_name="task",
            tool_call_id=tool_call_id,
            args=task_input,
            subagent_run_id=sid,
        )
        content["name"] = name
        content["input_summary"] = input_summary
        content["status"] = "running"
        msg = self._enrich(
            AgentMessage(
                type=MessageType.SUBAGENT_START,
                sender=SenderType.SYSTEM,
                session_id=self.session_id,
                content=content,
                save=True,
            ),
        )

        if not hasattr(self, "_pending_activate_msgs"):
            self._pending_activate_msgs: list[dict] = []
        self._pending_activate_msgs.append(msg)

    async def _emit_chat_chunk(self, text: str) -> AsyncGenerator[dict, None]:
        if not text or self._summarization_suppress:
            return
        self._current_llm_text += text
        self._chat_streamed = True
        sid = self._current_subagent_id_or_stack()
        msg = self._enrich(
            AgentMessage(
                type=MessageType.SUBAGENT_EVENT if sid else MessageType.CHAT,
                sender=SenderType.ASSISTANT,
                session_id=self.session_id,
                content=text,
                save=False,
            ),
            track_step=False,
        )
        if sid:
            msg["subagent_run_id"] = sid
            self._wrap_subagent_event(msg, "chat")
        yield msg

    def _emit_reasoning_msg(self, raw_type: str, content, save: bool, track_step: bool = False) -> dict:
        """产出推理消息；若在子 agent 上下文中自动包装为 SUBAGENT_EVENT。

        主 agent 的 REASONING_FINAL（累积完整推理，save=True）仅落库不推送前端，
        由 agent_manager 的类型过滤统一拦截。

        子 agent 的 REASONING_FINAL 也包装为 SUBAGENT_EVENT（确保 DB 中按
        subagent_run_id 正确分组），由 agent_manager 按 subevent_type 过滤
        避免推送到前端。

        子 agent 上下文中的 content 会附加 subevent_type 字段，
        便于前端从历史记录加载时区分思考/回复。
        """
        sid = self._current_subagent_id_or_stack()
        is_db_only = raw_type in (MessageType.REASONING_FINAL,)
        if is_db_only:
            # REASONING_FINAL：子 agent 上下文中也转为 SUBAGENT_EVENT，
            # 确保历史记录按 subagent_run_id 正确分组；
            # agent_manager 按 subevent_type=="reasoning_final" 过滤 WS 推送
            msg_type = MessageType.SUBAGENT_EVENT if sid else raw_type
        else:
            msg_type = MessageType.SUBAGENT_EVENT if sid else raw_type

        # 子 agent 上下文中，content 附加 subevent_type 供历史记录识别
        subevent_type = raw_type.value if isinstance(raw_type, type(MessageType.CHAT)) else str(raw_type)
        if sid and isinstance(content, dict):
            content = {**content, "subevent_type": subevent_type}

        msg = self._enrich(
            AgentMessage(
                type=msg_type,
                sender=SenderType.ASSISTANT,
                session_id=self.session_id,
                content=content,
                save=save,
            ),
            track_step=track_step,
        )
        if sid:
            msg["subagent_run_id"] = sid
            if msg_type == MessageType.SUBAGENT_EVENT:
                self._wrap_subagent_event(msg, subevent_type)
        return msg

    async def _append_reasoning_chunk(self, chunk: str) -> AsyncGenerator[dict, None]:
        """累积推理文本；开启 REASONING_STREAM_ENABLED 时按 chunk 推前端。"""
        if not chunk or self._summarization_suppress:
            return
        self._current_reasoning_text += chunk
        if app_config.REASONING_STREAM_ENABLED:
            yield self._emit_reasoning_msg(
                MessageType.REASONING, {"text": chunk}, save=False
            )
        elif not self._reasoning_notified:
            self._reasoning_notified = True
            yield self._emit_reasoning_msg(
                MessageType.REASONING, "正在推理中...", save=False
            )

    async def _flush_reasoning_text(self, persist: bool = True) -> AsyncGenerator[dict, None]:
        if self._summarization_suppress:
            self._current_reasoning_text = ""
            return
        text = (self._current_reasoning_text or "").strip()
        if not text:
            return
        if persist:
            yield self._emit_reasoning_msg(
                MessageType.REASONING_FINAL, {"text": text}, save=True,
                track_step=not app_config.REASONING_STREAM_ENABLED,
            )
        self._current_reasoning_text = ""

    async def _flush_reasoning_if_needed(self, persist: bool = True) -> AsyncGenerator[dict, None]:
        """将当前 model 段累积的推理一次性刷为 reasoning_final。
        persist=False 时仅清缓冲不落库，用于 namespace 切换场景。
        """
        if self._reasoning_segment_flushed or self._summarization_suppress:
            return
        text = (self._current_reasoning_text or "").strip()
        if not text:
            return
        self._reasoning_segment_flushed = True
        async for msg in self._flush_reasoning_text(persist=persist):
            yield msg

    async def flush_assistant_text(self, persist: bool = True) -> AsyncGenerator[dict, None]:
        """刷新助手文本：推前端 CHAT（若未流式推送）+ 存库 LLM_TEXT_FINAL。
        persist=False 时仅推送前端不落库，用于 namespace 切换场景。
        """
        if self._summarization_suppress:
            self._current_llm_text = ""
            self._chat_streamed = False
            return
        text = (self._current_llm_text or "").strip()
        if not text:
            return
        sid = self._current_subagent_id_or_stack()
        if not self._chat_streamed:
            msg = self._enrich(
                AgentMessage(
                    type=MessageType.SUBAGENT_EVENT if sid else MessageType.CHAT,
                    sender=SenderType.ASSISTANT,
                    session_id=self.session_id,
                    content=text,
                    save=False,
                ),
                track_step=False,
            )
            if sid:
                msg["subagent_run_id"] = sid
                self._wrap_subagent_event(msg, "chat")
            yield msg
        # LLM_TEXT_FINAL 仅落库；子 agent 上下文中包装为 SUBAGENT_EVENT
        # persist=False 时跳过落库（namespace 切换场景）
        if persist:
            subevent = "chat"
            content = {"text": text}
            if sid:
                content["subevent_type"] = subevent
            llm_msg = self._enrich(
                AgentMessage(
                    type=MessageType.LLM_TEXT_FINAL if not sid else MessageType.SUBAGENT_EVENT,
                    sender=SenderType.ASSISTANT,
                    session_id=self.session_id,
                    content=content,
                    save=True,
                )
            )
            if sid:
                llm_msg["subagent_run_id"] = sid
                self._wrap_subagent_event(llm_msg, subevent)
            yield llm_msg
        self._current_llm_text = ""
        self._chat_streamed = False

    async def finalize_model_segment(
        self, *, skip_reasoning: bool = False, persist: bool = True
    ) -> AsyncGenerator[dict, None]:
        """离开 model 节点或流结束时：刷 think 缓冲、推理与助手正文（与 Light runner 对齐）。

        persist=False 时仅推送前端不落库，用于 namespace 切换场景。
        """
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
            async for msg in self._flush_reasoning_if_needed(persist=persist):
                yield msg
            async for msg in self._emit_chat_chunk(answer_part):
                yield msg
        if not skip_reasoning:
            async for msg in self._flush_reasoning_if_needed(persist=persist):
                yield msg
        async for msg in self.flush_assistant_text(persist=persist):
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

    def _pre_activate_namespace(self, namespace: tuple) -> None:
        """在 namespace 首次出现时预激活，确保 SUBAGENT_START 先于内容推送。"""
        tool_call_id = _extract_tool_call_id_from_ns(namespace)
        if not tool_call_id:
            return
        self._emit_subagent_start_if_needed(tool_call_id)

    async def adapt_astream_chunk(
        self, mode: str, chunk: Any, *, namespace: tuple = ()
    ) -> AsyncGenerator[dict, None]:
        """处理 astream 的 messages / updates 模式（含 HITL 中断）。"""
        if mode == "updates":
            async for msg in self._adapt_updates_chunk(chunk):
                yield msg
        elif mode == "messages":
            self._active_namespace = namespace  # 暂存 namespace 供下游推断子 agent 归属
            # 预激活：确保 SUBAGENT_START 在内容（reasoning/chat）之前推送
            self._pre_activate_namespace(namespace)
            for msg in self._drain_pending_activations():
                yield msg
            async for msg in self._adapt_messages_chunk(chunk):
                yield msg
            self._active_namespace = ()
        # 排空延迟发射的 SUBAGENT_START 消息
        for msg in self._drain_pending_activations():
            yield msg

    def _drain_pending_activations(self) -> list[dict]:
        """排空 _activate_pregel 产生的延迟 SUBAGENT_START 消息。"""
        msgs = getattr(self, "_pending_activate_msgs", None)
        if not msgs:
            return []
        self._pending_activate_msgs = []
        return msgs

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

        # 离开 model 节点时：先刷推理/回复，再 emit 缓存的 tool_call start
        # 优先使用当前 chunk 的 namespace（saved_ns），仅在 saved_ns 为空
        # （主 agent 层面）时回退到 _last_model_namespace。防止并发子 agent
        # 场景下 _last_model_namespace 已被另一个子 agent 覆盖导致归属错误。
        if self._last_node == "model" and node != "model":
            saved_ns = getattr(self, "_active_namespace", ())
            effective_ns = saved_ns if saved_ns else self._last_model_namespace
            if effective_ns:
                self._active_namespace = effective_ns
            try:
                async for msg in self.finalize_model_segment():
                    yield msg
                # 离开 model 节点：流式 chunks 已到齐，强制合并（未闭合的包 _raw）
                async for msg in self._emit_pending_tool_starts(force_merge=True):
                    yield msg
            finally:
                self._active_namespace = saved_ns

        if node == "model":
            current_ns = getattr(self, "_active_namespace", ()) or ()
            # 检测 namespace 变化（子 agent 切换）：发射旧 namespace 的待处理
            # tool_calls，然后备份整个状态（含文本缓冲区和 _pending_tool_calls），
            # 再恢复新 namespace 的状态。不在此时 flush 文本 — 文本在各自
            # namespace 中持续累积，仅在 model→tools 转换或 tools 事件时落库。
            if (self._last_node == "model"
                    and self._last_model_namespace
                    and self._last_model_namespace != current_ns):
                saved_ns = self._active_namespace
                self._active_namespace = self._last_model_namespace
                try:
                    # 发射旧 namespace 的待处理 tool_calls（归属到正确的子 agent）；
                    # 旧 namespace 的流已结束，强制合并
                    async for msg in self._emit_pending_tool_starts(force_merge=True):
                        yield msg
                finally:
                    self._active_namespace = saved_ns
                # 保存旧 namespace 的 accumulator + 文本缓冲区 + pending_tool_calls
                old_ns = self._last_model_namespace
                self._ns_accumulator_backup[old_ns] = {
                    "accumulated": dict(self._tool_call_accumulator._accumulated),
                    "streamed_args": dict(self._streamed_tool_args),
                    "progress_emitted": set(self._tool_progress_emitted),
                    "reasoning_text": self._current_reasoning_text,
                    "llm_text": self._current_llm_text,
                    "chat_streamed": self._chat_streamed,
                    "reasoning_notified": self._reasoning_notified,
                    "reasoning_segment_flushed": self._reasoning_segment_flushed,
                    "pending_tool_calls": list(self._pending_tool_calls),
                    "pending_tool_inputs": dict(self._pending_tool_inputs),
                }
                self._pending_tool_calls.clear()
                self._pending_tool_inputs.clear()
                if current_ns in self._ns_accumulator_backup:
                    state = self._ns_accumulator_backup.pop(current_ns)
                    self._tool_call_accumulator._accumulated = state["accumulated"]
                    self._streamed_tool_args = state["streamed_args"]
                    self._tool_progress_emitted = state["progress_emitted"]
                    self._current_reasoning_text = state.get("reasoning_text", "")
                    self._current_llm_text = state.get("llm_text", "")
                    self._chat_streamed = state.get("chat_streamed", False)
                    self._reasoning_notified = state.get("reasoning_notified", False)
                    self._reasoning_segment_flushed = state.get("reasoning_segment_flushed", False)
                    self._pending_tool_calls = state.get("pending_tool_calls", [])
                    self._pending_tool_inputs = state.get("pending_tool_inputs", {})
                else:
                    self._tool_call_accumulator._accumulated.clear()
                    self._streamed_tool_args.clear()
                    self._tool_progress_emitted.clear()
                    self._current_reasoning_text = ""
                    self._current_llm_text = ""
                    self._chat_streamed = False
                    self._reasoning_notified = False
                    self._reasoning_segment_flushed = False
                    self._pending_tool_calls.clear()
                    self._pending_tool_inputs.clear()
            self._last_model_namespace = current_ns
            if self._last_node != "model":
                self._reasoning_notified = False
                self._reasoning_segment_flushed = False
                self._think_parser.reset()
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
                # 子 agent 上下文跳过 — 只推送 start + result，不推送 tool_args_stream
                if not self._current_subagent_id_or_stack():
                    async for msg in self._emit_tool_args_stream():
                        yield msg

            tool_calls = getattr(message_chunk, "tool_calls", None) or []
            if tool_calls:
                # 模型可能先给出无 ID/名称的中间结构；只缓存可与后续结果关联的完整调用。
                self._pending_tool_calls = [
                    tc for tc in tool_calls
                    if isinstance(tc, dict) and tc.get("id")
                    and normalize_tool_call(tc)[0] != "unknown"
                ]
                for tc in self._pending_tool_calls:
                    tc_id = tc["id"]
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
        tool_call_id = tc.get("id")
        # 所有入口共用此发射边界，不能为不完整调用补造 ID 并写入 history。
        if not tool_call_id or tool_name == "unknown":
            return
        if tool_call_id in self._emitted_tool_starts:
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
        _original_msg_type = msg_type  # 保存原始类型，子 agent 包装时用作 subevent_type

        subagent_run_id = None
        if tool_name in _TASK_TOOLS:
            # 生成 UUID 作为 subagent_run_id，存入 _active_subagents 建立
            # tool_call_id → UUID 的映射。namespace 中提取 tool_call_id 后
            # 通过此映射获取 UUID，100% 精准。
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
            # 将 UUID 排入 FIFO 队列，供 _emit_subagent_start_if_needed 在
            # namespace UUID 与 LLM tool_call_id 不匹配时按派发顺序恢复 args
            self._unmatched_subagent_uuids.append(subagent_run_id)
            # task 工具不在此发射 SUBAGENT_START — 延迟到首次 pregel 事件
            # 直接 return，跳过下方的消息发射
            return
        elif self._subagent_stack:
            # 子 agent 内部发出的工具调用（非 task），用 namespace 映射获取正确的 subagent_run_id
            # 不能用 _subagent_stack[-1]：并发子 agent 时栈顶不一定是当前执行的子 agent
            subagent_run_id = self._current_subagent_id()
            if not subagent_run_id:
                subagent_run_id = self._subagent_stack[-1].get("subagent_run_id")
            msg_type = MessageType.SUBAGENT_EVENT

        content = base_content(
            run_id=self.run_id,
            conversation_id=self.conversation_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            args=tool_input,
            subagent_run_id=subagent_run_id,
        )
        msg = self._enrich(
            AgentMessage(
                type=msg_type,
                sender=SenderType.SYSTEM,
                session_id=self.session_id,
                content=content,
                save=True,
            )
        )
        if subagent_run_id and msg_type == MessageType.SUBAGENT_EVENT:
            msg["subagent_run_id"] = subagent_run_id
            self._wrap_subagent_event(msg, _original_msg_type.value)
        yield msg
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

        在子 agent 上下文中自动包装为 SUBAGENT_EVENT。
        """
        sid = self._current_subagent_id_or_stack()
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
            msg_type = MessageType.SUBAGENT_EVENT if sid else MessageType.TOOL_ARGS_STREAM

            # 阶段 1：内容未开始 → 尝试发送进度通知
            if len(current_content) <= len(prev_content):
                if index not in self._tool_progress_emitted:
                    key_params = _extract_tool_key_params(name, args_str)
                    self._tool_progress_emitted.add(index)
                    content = {
                        "tool_name": name,
                        "tool_call_id": tool_call_id,
                        "tool_call_index": index,
                        "field_name": field_name,
                        "delta": "",
                        "key_params": key_params,
                    }
                    if sid:
                        content["subagent_run_id"] = sid
                    ts_msg = self._enrich(
                        AgentMessage(
                            type=msg_type,
                            sender=SenderType.SYSTEM,
                            session_id=self.session_id,
                            content=content,
                            save=False,
                        ),
                        track_step=False,
                    )
                    if sid:
                        ts_msg["subagent_run_id"] = sid
                        self._wrap_subagent_event(ts_msg, "tool_args_stream")
                    yield ts_msg
                continue

            # 阶段 2：内容已到达 → 推送增量
            delta = current_content[len(prev_content):]
            self._streamed_tool_args[index] = current_content

            if delta:
                content = {
                    "tool_name": name,
                    "tool_call_id": tool_call_id,
                    "tool_call_index": index,
                    "field_name": field_name,
                    "delta": delta,
                }
                if sid:
                    content["subagent_run_id"] = sid
                msg = self._enrich(
                    AgentMessage(
                        type=msg_type,
                        sender=SenderType.SYSTEM,
                        session_id=self.session_id,
                        content=content,
                        save=False,
                    ),
                    track_step=False,
                )
                if sid:
                    msg["subagent_run_id"] = sid
                    self._wrap_subagent_event(msg, "tool_args_stream")
                yield msg

    def _merge_accumulated_tool_calls(self, force: bool = False) -> None:
        """将流式 tool_call_chunks 合并进 pending 列表。

        直接从 accumulator 读取原始累积数据，不经过 parse_and_clear 的
        JSON 解析，让模型流式参数自然拼接。参数解析由下游 normalize_tool_call
        统一处理（失败时自动包 _raw）。

        force=False（tools 事件先于 messages 完整 chunks 到达时）：
        JSON 未闭合（解析失败）的条目说明流式 chunks 仍在途中，跳过合并并
        保留 accumulator，等待后续 chunk 补全后由离开 model 节点时的
        force=True 合并统一处理。避免把半截参数提前合并/清空，导致前端展示
        的 args 与 langchain 实际执行的完整参数不一致（表现为 _raw 残缺 JSON）。
        force=True（离开 model 节点 / namespace 切换，流已结束）：
        无论 JSON 是否闭合都合并，未闭合的包 _raw 记录事实。
        """
        raw_accumulated = self._tool_call_accumulator._accumulated
        merged_keys: list = []
        for idx, tc in list(raw_accumulated.items()):
            tool_name = tc.get("name")
            tool_id = tc.get("id")
            args_raw = tc.get("args") or ""

            if not tool_name or not tool_id:
                continue

            # 校验是否为已注册工具（过滤幻觉工具调用）
            valid_names = self._tool_call_accumulator._valid_tool_names
            if valid_names and tool_name not in valid_names:
                continue

            # 非强制模式下跳过未闭合的 JSON（流式 chunks 未到齐），不清空条目
            if not force and args_raw.strip():
                try:
                    json.loads(args_raw)
                except json.JSONDecodeError:
                    continue

            entry = {
                "id": tool_id,
                "name": tool_name,
                "args": args_raw,  # 原始字符串，不解析，由 normalize_tool_call 兜底
            }
            tc_id = entry["id"]
            if tc_id:
                self._pending_tool_inputs[tc_id] = args_raw
                # 同步填充 _pending_subagent_inputs，确保流式 chunk 场景下
                # _emit_subagent_start_if_needed 的 tool_call_id 回退查找也能命中
                if tool_name == "task" or is_task_tool_call(entry):
                    # 尝试解析 args JSON 字符串为 dict（流式场景下可能不完整）
                    parsed_args = args_raw
                    if isinstance(args_raw, str):
                        try:
                            parsed_args = json.loads(args_raw) if args_raw.strip() else {}
                        except json.JSONDecodeError:
                            parsed_args = {"_raw": args_raw}
                    self._pending_subagent_inputs[tc_id] = parsed_args if isinstance(parsed_args, dict) else {"_raw": str(parsed_args)}
            existing_ids = {
                item.get("id") for item in self._pending_tool_calls if isinstance(item, dict)
            }
            if tc_id not in existing_ids:
                self._pending_tool_calls.append(entry)
            merged_keys.append(idx)

        if merged_keys:
            # 只清理已合并的条目；未闭合条目保留等待补全
            for idx in merged_keys:
                self._tool_call_accumulator._accumulated.pop(idx, None)
            self._streamed_tool_args.clear()
            self._tool_progress_emitted.clear()

    async def _ensure_tool_start(
        self,
        tool_call_id: str,
        tool_name: str,
        tool_input: Optional[dict] = None,
    ) -> AsyncGenerator[dict, None]:
        """在 tool_result 前补发缺失的 tool_call（MCP 等工具与 task 同理）。"""
        # 结果缺少调用 ID 时无法可靠关联原始调用，不补发孤立的 tool_call。
        if not tool_call_id:
            return
        if tool_call_id and tool_call_id in self._emitted_tool_starts:
            return
        if not tool_name or tool_name == "unknown":
            return

        resolved_input = tool_input
        if resolved_input is None and tool_call_id:
            resolved_input = self._pending_tool_inputs.get(tool_call_id, {})
        resolved_input = resolved_input or {}

        tc = {
            "id": tool_call_id,
            "name": tool_name,
            "args": resolved_input,
        }
        async for msg in self._emit_single_tool_start(tc, tool_name, resolved_input):
            yield msg

    async def _emit_pending_tool_starts(self, force_merge: bool = False) -> AsyncGenerator[dict, None]:
        self._merge_accumulated_tool_calls(force=force_merge)
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

        fallback_args = self._pending_subagent_inputs.pop(tool_call_id, {})
        # 优先从 _active_subagents 查找（主 agent 的 task tool_call_id），
        # 若未命中且 _subagent_stack 非空，说明当前在子 agent 内部执行，
        # 用 namespace 映射获取正确的 subagent_run_id
        subagent_run_id = self._active_subagents.get(tool_call_id)
        if not subagent_run_id and self._subagent_stack:
            subagent_run_id = self._current_subagent_id()
            if not subagent_run_id:
                subagent_run_id = self._subagent_stack[-1].get("subagent_run_id")
        # 子 agent 内部的工具消息应标为 subagent_event 而非裸 fs_operation
        msg_type = _tool_message_type(resolved_name, False)
        _original_result_type = msg_type  # 保存原始类型，用作 subevent_type
        if subagent_run_id and resolved_name not in _TASK_TOOLS:
            msg_type = MessageType.SUBAGENT_EVENT

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
            # _merge_accumulated_tool_calls 可能透传原始 JSON 字符串
            if isinstance(tool_input, str):
                try:
                    tool_input = json.loads(tool_input) if tool_input.strip() else {}
                except json.JSONDecodeError:
                    tool_input = {"_raw": tool_input}
            if not isinstance(tool_input, dict):
                tool_input = {}
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

        result_msg = self._enrich(
            AgentMessage(
                type=msg_type,
                sender=SenderType.SYSTEM,
                session_id=self.session_id,
                content=content,
                save=True,
            )
        )
        if subagent_run_id and msg_type == MessageType.SUBAGENT_EVENT:
            result_msg["subagent_run_id"] = subagent_run_id
            self._wrap_subagent_event(result_msg, _original_result_type.value)
        yield result_msg
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
        # 设置 _active_namespace，使 _current_subagent_id() 能通过 namespace 映射
        # 正确解析并发子 agent 的归属
        saved_ns = getattr(self, "_active_namespace", ())
        self._active_namespace = namespace
        ctx = None
        try:
            if in_subagent:
                sid = self._current_subagent_id()
                if sid:
                    ctx = {
                        "subagent_run_id": sid,
                        "parent_tool_call_id": None,  # tools 模式下无 tool_call_id
                    }
                if not ctx and self._subagent_stack:
                    ctx = self._subagent_stack[-1]
                if not ctx and self._active_subagents:
                    parent_id = list(self._active_subagents.keys())[-1]
                    ctx = {
                        "subagent_run_id": self._active_subagents[parent_id],
                        "parent_tool_call_id": parent_id,
                    }
        finally:
            self._active_namespace = saved_ns

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
                # 防止重复发射：messages 模式的 _ensure_tool_start 可能已
                # 发射过同一 tool_call_id 的启动事件
                if tool_call_id in self._emitted_tool_starts:
                    return
                self._emitted_tool_starts.add(tool_call_id)
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
        # _pending_tool_inputs 可能存原始 JSON 字符串，统一转 dict
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError:
                args = {}
        args = args or {}
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

    async def async_conversation_files_message(
        self,
        *,
        run_started_at: Optional[Any] = None,
        run_ended_at: Optional[Any] = None,
    ) -> Optional[dict]:
        """内存登记为空时，从 DB / 磁盘回填文件列表。

        run_started_at / run_ended_at：datetime 或可转 timestamp 的对象；
        提供时磁盘兜底用 run 生命周期窗口（[start, end]）扫描——
        覆盖正常完成/出错/用户 stop 全形态，不受固定秒数窗口限制。
        """
        msg = self.conversation_files_message()
        if msg:
            # 内存登记（含 execute 兜底登记的）也做跨轮次排他：
            # 跳过已登记到其他轮次的文件（上一轮 execute 生成的文档可能被
            # 本轮 execute 兜底窗口扫到，这里按 DB 索引排除，不依赖时间窗口）
            if self.conversation_id:
                try:
                    from db import deep_storage

                    stale = await deep_storage.list_workspace_rel_paths_except_conversation(
                        self.user_id, self.session_id, self.conversation_id
                    )
                    if stale:
                        files = (msg.get("content") or {}).get("files") or []
                        kept = [f for f in files if f.get("virtual_path") not in stale]
                        if len(kept) != len(files):
                            msg["content"]["files"] = kept
                            if not kept:
                                return None
                except Exception:
                    pass
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
                time_window = None
                if run_started_at is not None or run_ended_at is not None:
                    time_window = (
                        _to_timestamp(run_started_at),
                        _to_timestamp(run_ended_at),
                    )
                cards = await list_recent_session_workspace_cards(
                    self.user_id,
                    self.session_id,
                    self.conversation_id or "",
                    time_window=time_window,
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
            find_recent_document_files,
            is_write_success,
            resolve_agent_file_path,
            resolve_execute_output_tool,
            resolve_write_tool_name,
        )

        if not is_write_success(result_text):
            return
        effective_tool = resolve_write_tool_name(tool_name, args)
        if not effective_tool:
            # execute 工具：命令/结果提及文档文件时登记
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
        agent_paths: List[str] = []
        if agent_path:
            agent_paths.append(agent_path)
        elif effective_tool == "execute":
            # execute 兜底：命令未显式指定输出路径（如 python gen.py out.pptx、
            # soffice --convert-to 等），扫描工作区本轮 run 内写入的全部文档文件
            # （同一 execute 可能同时生成多个文档；下界用 run 开始时刻，
            # 避免把上一轮生成的文件误收为本轮新文件）
            agent_paths = find_recent_document_files(
                self.user_id,
                self.session_id,
                since_ts=_to_timestamp(getattr(self, "run_started_at", None)),
            )
        if not agent_paths:
            from config import get_logger

            get_logger(__name__).debug(
                "[DeepAdapter] skip conversation file record tool=%s call=%s",
                tool_name,
                tool_call_id,
            )
            return
        for ap in agent_paths:
            virtual_path = agent_path_to_virtual(self.session_id, ap)
            if not virtual_path:
                continue
            self._conversation_files[virtual_path] = {
                "agent_path": ap,
                "tool_call_id": tool_call_id,
                "tool_name": effective_tool,
                # 最近一次写入时间：同一文件多次写入时定位「本轮最后生成的 HTML」
                # （dict 的 key 位置不会因重复写入而更新，不能靠遍历顺序判断）
                "updated_ts": time.time(),
            }
            self._schedule_conversation_file_index(virtual_path)
            self._schedule_html_share(virtual_path)

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

    # ── HTML 分享链接自动创建 ──────────────────────────────────────────────

    def _schedule_html_share(self, virtual_path: str) -> None:
        """fire-and-forget 入口：检查扩展名后异步创建分享 token。"""
        ext = (virtual_path or "").rsplit(".", 1)[-1].lower()
        if ext not in ("html", "htm"):
            return
        import asyncio
        asyncio.ensure_future(self._create_html_share_async(virtual_path))

    async def _create_html_share_async(self, virtual_path: str) -> None:
        """读磁盘文件，若为完整 HTML（含闭合标签）则创建分享 token 并缓存 URL。

        同一 (user_id, virtual_path) 的创建通过 wrapper 级异步锁串行化，
        避免同路径删除再生成（连续 write_file）时并发任务各自创建 token，
        导致同一文件产生多个分享链接（查重-插入竞态）。
        """
        try:
            import asyncio

            from config import get_logger as _get_logger

            wrapper = self._agent_wrapper
            lock_key = f"{self.user_id}:{virtual_path}"
            if wrapper is not None:
                lock = wrapper._html_share_locks.setdefault(lock_key, asyncio.Lock())
                async with lock:
                    # 锁内先查 wrapper 缓存：同路径已创建过链接则直接复用，
                    # 跳过 DB 往返（token 指向路径，文件内容更新不影响有效性）
                    existing_url = wrapper.html_share_links.get(virtual_path)
                    if existing_url:
                        self._html_share_links[virtual_path] = existing_url
                        return
                    share_url = await self._do_create_html_share(virtual_path)
            else:
                share_url = await self._do_create_html_share(virtual_path)

            if share_url:
                self._html_share_links[virtual_path] = share_url
                if wrapper is not None:
                    wrapper.html_share_links[virtual_path] = share_url
                _get_logger(__name__).info(
                    f"[HtmlShare] token ready path={virtual_path} url={share_url}"
                )
        except Exception as e:
            from config import get_logger as _get_logger
            _get_logger(__name__).warning(
                f"[HtmlShare] create share failed path={virtual_path}: {e}"
            )

    async def _do_create_html_share(self, virtual_path: str) -> str:
        """创建分享链接并返回 URL；文件不完整或不存在时返回空串。

        与前端分享、工具中间件共用 ensure_html_share 入口（复用链 + 快照），
        同一文件不重复创建 token；内容指纹参与"改名后复用"判定。
        """
        from deep_agent_server.html_share_service import ensure_html_share

        return await ensure_html_share(
            user_id=self.user_id,
            session_id=self.session_id,
            vpath=virtual_path,
        )

    async def latest_html_share_url(self) -> Optional[str]:
        """本轮 run 最后生成的 HTML 文件的分享链接（无 HTML / 未生成链接则 None）。

        供 run_metrics 消息的 html_url 字段使用。取本轮 conversation_files 中
        最近写入（updated_ts 最大）的 .html/.htm：
          ① 优先读 wrapper 共享缓存 —— HtmlShareLinkMiddleware 已在工具返回前写入，
             正常路径零额外开销；
          ② 缓存缺失（中间件关闭 / 异步创建尚未完成）时兜底创建一次，避免漏报。
        """
        candidates = [
            (vpath, item.get("updated_ts") or 0.0)
            for vpath, item in self._conversation_files.items()
            if (vpath or "").rsplit(".", 1)[-1].lower() in ("html", "htm")
        ]
        if not candidates:
            return None
        vpath = max(candidates, key=lambda pair: pair[1])[0]

        links = getattr(self._agent_wrapper, "html_share_links", None)
        if isinstance(links, dict) and links.get(vpath):
            return links[vpath]

        from deep_agent_server.html_share_service import ensure_html_share

        url = await ensure_html_share(
            user_id=self.user_id,
            session_id=self.session_id,
            vpath=vpath,
        )
        if url and isinstance(links, dict):
            links[vpath] = url
        return url or None

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
