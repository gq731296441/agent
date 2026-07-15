"""Deep Agent 运行器 — stream_events + stop/timeout。

消费 DeepAgentWrapper.agent.astream，经 DeepEventAdapter 转为 AgentMessage，
并负责 run 生命周期（DB 记录、EventBus 发布、HITL 持久化、错误重试）。
"""

import asyncio
import random
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, Optional

from langchain.messages import HumanMessage
from langgraph.types import Command

from common.enums import EndReason, MessageType, SenderType, ActionType
from common.schemas import AgentMessage
from config import get_logger
from config.config import app_config
from db import deep_storage
from db import storage as chat_storage
from deep_agent_server.adapter import DeepEventAdapter, normalize_tool_call, task_call_args_ready
from deep_agent_server.artifact import truncate_summary
from deep_agent_server.memory.middleware import inject_user_memory_into_stream_input
from deep_agent_server.concurrency import DeepConcurrencyGuard
from deep_agent_server.errors.recovery import classify_deep_error, should_retry
from deep_agent_server.events.bus import publish_event
from deep_agent_server.events.normalizer import make_run_started, normalize_agent_message
from deep_agent_server.hitl.store_ext import save_deep_interrupt
from deep_agent_server.subagent_record import summarize_subagent_input, task_subagent_name
from deep_agent_server.tool_persistence import flush_tool_messages_from_state
from deep_agent_server.turn.context import finish_turn, mark_awaiting_hitl, update_turn_step
from utils.run_events import insert_agent_run_event
from utils.token_usage import attach_token_callback

logger = get_logger(__name__)

_BUILTIN_TOOL_NAMES = {
    "write_todos", "read_file", "write_file", "edit_file",
    "ls", "glob", "grep", "execute", "task",
}


class DeepRunTimeoutError(Exception):
    """Deep Agent 单次 run 超时。"""


def build_run_metrics_content(
    *,
    run_id: str,
    conversation_id: str,
    status: str,
    token_usage: dict,
    started_at: datetime,
    finished_at: datetime,
) -> dict:
    """构造 run_metrics 消息 content（便于单测）。"""
    duration_ms = max(0, int((finished_at - started_at).total_seconds() * 1000))
    return {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "status": status,
        "token_usage": {
            "prompt_tokens": int(token_usage.get("prompt_tokens") or 0),
            "completion_tokens": int(token_usage.get("completion_tokens") or 0),
            "total_tokens": int(token_usage.get("total_tokens") or 0),
        },
        "duration_ms": duration_ms,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
    }


def _run_metrics_message(
    adapter: DeepEventAdapter,
    session_id: str,
    run_id: str,
    conversation_id: str,
    status: str,
    token_usage: dict,
    started_at: datetime,
    finished_at: datetime,
) -> dict:
    content = build_run_metrics_content(
        run_id=run_id,
        conversation_id=conversation_id,
        status=status,
        token_usage=token_usage,
        started_at=started_at,
        finished_at=finished_at,
    )
    return adapter._enrich(
        AgentMessage.run_metrics(session_id=session_id, content=content),
        track_step=False,
    )


def _is_langgraph_control_flow(exc: Exception) -> bool:
    """LangGraph HITL / interrupt 控制流异常（非业务错误）。"""
    try:
        from langgraph.errors import GraphBubbleUp
    except ImportError:
        GraphBubbleUp = ()  # type: ignore[misc, assignment]

    if GraphBubbleUp and isinstance(exc, GraphBubbleUp):
        return True
    name = type(exc).__name__.lower()
    return "interrupt" in name or name == "parentcommand"


def _format_exc_message(exc: Exception) -> str:
    text = str(exc).strip()
    exc_name = type(exc).__name__.lower()
    text_lower = text.lower()

    # 认证/鉴权错误 → 用户可读提示
    if (
        "authentication" in exc_name
        or "auth" in exc_name
        or "401" in text
        or "403" in text
        or "unauthorized" in text_lower
        or "permission" in text_lower
    ):
        return (
            f"模型认证失败（{text}），请检查 API Key 是否正确、是否过期，"
            "或 Base URL / 模型名称是否填写有误。可在「模型配置」中修改后重试。"
        )

    if text:
        return text[:500]
    name = type(exc).__name__
    if name == "NotImplementedError":
        return (
            "文件操作落到了不支持读写的 backend（沙箱下 default 仅支持 execute）。"
            "请使用 /workspace/... 或 /skills/... 等虚拟路径，避免 ls('/') 落到 exec backend。"
        )
    return f"{name}（无详细消息，可能是 HITL 中断或 LangGraph 控制流）"


async def _maybe_release_sandbox(
    user_id: str,
    session_id: str,
    end_reason: EndReason,
) -> None:
    """Run 结束后 stop 沙箱（HITL 中断路径不调用本函数）。"""
    if not app_config.SANDBOX_ENABLED:
        return
    if end_reason == EndReason.STOP and not app_config.SANDBOX_RELEASE_ON_STOP:
        return
    if end_reason in (EndReason.COMPLETED, EndReason.ERROR) and not app_config.SANDBOX_RELEASE_ON_RUN_END:
        return
    from deep_agent_server.sandbox import release_session_sandbox

    await release_session_sandbox(user_id, session_id)


async def _yield_interrupts_from_state(
    compiled,
    config: dict,
    adapter: DeepEventAdapter,
    agent_wrapper,
) -> AsyncGenerator[dict, None]:
    """从 graph state 补发 INTERRUPT 消息（HITL 暂停）。"""
    state = await compiled.aget_state(config)
    async for msg in adapter.build_interrupt_from_state(state):
        await _persist_interrupt(msg, config, adapter, agent_wrapper)
        _publish_agent_message(msg, adapter)
        yield msg


async def run_deep_agent(
    agent_wrapper,
    input_data: dict,
    config: dict,
    session_id: str,
    user_id: str,
    stop_event: asyncio.Event = None,
    conversation_id: str = None,
    run_id: str = None,
    continue_on_disconnect: bool = False,
    agent_id: str = None,
    initial_step_index: int = 0,
    is_resume: bool = False,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Deep Agent 流式运行，yield AgentMessage dict。

    流程：创建 run 记录 → 推送 SKILL_LOADED → astream 消费 →
    遇 INTERRUPT 则中断并更新状态，否则 COMPLETED / ERROR / STOP。
    """
    run_id = run_id or str(uuid.uuid4())
    conversation_id = conversation_id or str(uuid.uuid4())
    compiled = agent_wrapper.agent
    store = getattr(agent_wrapper, "store", None)

    resolved_agent_id = agent_id or await deep_storage.get_session_agent_id(session_id)

    adapter = DeepEventAdapter(
        session_id=session_id,
        run_id=run_id,
        conversation_id=conversation_id,
        user_id=user_id,
        agent_id=resolved_agent_id,
        initial_step_index=initial_step_index,
    )
    # checkpoint 含整 thread 历史 ToolMessage；须按 session 全量去重，
    # 否则 resume 时会把旧 turn 的工具以新 conversation_id 重复落库。
    started_ids, completed_ids = await chat_storage.load_persisted_tool_call_keys(
        session_id, None
    )
    adapter.preload_persisted_tool_keys(started_ids, completed_ids)

    if is_resume:
        resume_ctx = input_data.get("resume_context") or {}
        request_id = resume_ctx.get("request_id")
        if request_id:
            adapter.bind_hitl_resume(
                request_id,
                resume_ctx.get("actions") or [],
                resume_ctx.get("history_decisions") or [],
            )

    if is_resume:
        await deep_storage.update_agent_run(run_id, "running")
    else:
        mcp_summary = [
            {"name": getattr(t, "name", ""), "description": getattr(t, "description", "")}
            for t in (getattr(agent_wrapper, "mcp_tools", None) or [])
        ]
        skill_summary = [
            {"skill_id": getattr(m, "skill_id", ""),
             "name": getattr(m, "name", ""),
             "version": getattr(m, "version", "")}
            for m in (getattr(agent_wrapper, "exposed_skill_metas", None) or [])
        ]
        await deep_storage.create_agent_run(
            run_id, user_id, session_id,
            agent_id=resolved_agent_id,
            conversation_id=conversation_id,
            mcp_tools=mcp_summary,
            skills=skill_summary,
        )
        publish_event(make_run_started(
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            conversation_id=conversation_id,
        ))

    loaded_skills = getattr(agent_wrapper, "loaded_skills", None) or []
    for meta in loaded_skills:
        skill_name = getattr(meta, "name", None) or getattr(meta, "skill_id", "")
        skill_msg = adapter._enrich(
            AgentMessage(
                type=MessageType.SKILL_LOADED,
                sender=SenderType.SYSTEM,
                session_id=session_id,
                content={
                    "skill_name": skill_name,
                    "skill_dir": getattr(meta, "skill_dir", ""),
                    "triggers": getattr(meta, "triggers", []),
                },
                save=False,
            ),
            track_step=False,
        )
        skill_msg["user_id"] = user_id
        publish_event(normalize_agent_message(skill_msg))
        yield skill_msg

    if not app_config.SANDBOX_ENABLED:
        logger.debug("[DeepRunner] SANDBOX_ENABLED=false，execute 工具不可用（文件读写仍可用）")

    # 存库用户提问（与 Light agent_runner 一致）
    if not input_data.get("command"):
        question = (input_data.get("content") or "").strip()
        if question:
            yield adapter._enrich(
                AgentMessage(
                    type=MessageType.CHAT,
                    sender=SenderType.USER,
                    session_id=session_id,
                    content={"text": question},
                    save=True,
                ),
                track_step=False,
            )

    stream_input = _build_input(input_data)
    if not input_data.get("command"):
        question = (input_data.get("content") or "").strip()
        if question:
            stream_input = await inject_user_memory_into_stream_input(
                stream_input,
                store=store,
                user_id=user_id,
                query=question,
            )
    timeout_sec = app_config.DEEP_TIMEOUT_SECONDS
    retry_count = 0
    max_retries = 2
    run_config, token_acc = attach_token_callback(config)
    run_started_at = datetime.now(timezone.utc)

    async def _finalize_tokens(status: str, error: str = "") -> dict:
        usage = token_acc.as_dict()
        finished_at = datetime.now(timezone.utc)
        model_name = ""
        try:
            model_name = await deep_storage.get_user_secret(user_id, "llm_model") or ""
        except Exception:
            pass
        summary = {"conversation_id": conversation_id, "token_usage": usage}
        await deep_storage.update_agent_run(
            run_id,
            status,
            result_summary=summary,
            error=error,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            llm_model=model_name,
        )
        if status != "interrupted":
            await finish_turn(store, session_id)
        return build_run_metrics_content(
            run_id=run_id,
            conversation_id=conversation_id,
            status=status,
            token_usage=usage,
            started_at=run_started_at,
            finished_at=finished_at,
        )

    def _yield_run_metrics(metrics: dict):
        return _run_metrics_message(
            adapter,
            session_id,
            run_id,
            conversation_id,
            metrics["status"],
            metrics["token_usage"],
            run_started_at,
            datetime.fromisoformat(metrics["finished_at"]),
        )

    async def _yield_workspace_files_async():
        files_msg = await adapter.async_conversation_files_message()
        if files_msg:
            yield files_msg

    async def _yield_run_end(reason: EndReason):
        async for msg in _yield_workspace_files_async():
            yield msg
        yield adapter.end_message(reason)

    async with DeepConcurrencyGuard(user_id):
        while True:
            if stop_event and stop_event.is_set():
                metrics = await _finalize_tokens("stopped")
                yield _yield_run_metrics(metrics)
                async for msg in _yield_run_end(EndReason.STOP):
                    yield msg
                await _maybe_release_sandbox(user_id, session_id, EndReason.STOP)
                return

            try:
                was_interrupted = False
                async for msg in _stream_with_timeout(
                    compiled, stream_input, run_config, adapter, stop_event,
                    agent_wrapper, timeout_sec,
                ):
                    if msg.get("type") == MessageType.INTERRUPT.value:
                        was_interrupted = True
                    yield msg

                if was_interrupted:
                    metrics = await _finalize_tokens("interrupted")
                    yield _yield_run_metrics(metrics)
                    return

                metrics = await _finalize_tokens("completed")
                yield _yield_run_metrics(metrics)
                async for msg in _yield_run_end(EndReason.COMPLETED):
                    yield msg
                await _maybe_release_sandbox(user_id, session_id, EndReason.COMPLETED)
                return

            except DeepRunTimeoutError:
                metrics = await _finalize_tokens("error", error="timeout")
                yield _yield_run_metrics(metrics)
                async for msg in _yield_run_end(EndReason.ERROR):
                    yield msg
                await _maybe_release_sandbox(user_id, session_id, EndReason.ERROR)
                return

            except asyncio.CancelledError:
                logger.info(f"[DeepRunner] 会话 {session_id} 取消")
                metrics = await _finalize_tokens("stopped")
                yield _yield_run_metrics(metrics)
                async for msg in _yield_run_end(EndReason.STOP):
                    yield msg
                await _maybe_release_sandbox(user_id, session_id, EndReason.STOP)
                raise

            except Exception as e:
                if _is_langgraph_control_flow(e) or "__interrupt__" in str(e):
                    logger.info(
                        f"[DeepRunner] 检测到 HITL/Graph 中断 ({type(e).__name__})，"
                        f"session={session_id}"
                    )
                    async for msg in _yield_interrupts_from_state(
                        compiled, run_config, adapter, agent_wrapper
                    ):
                        yield msg
                    metrics = await _finalize_tokens("interrupted")
                    yield _yield_run_metrics(metrics)
                    return

                classification = classify_deep_error(e)
                err_text = _format_exc_message(e)
                logger.error(f"[DeepRunner] 错误: {err_text}\n{traceback.format_exc()}")
                yield AgentMessage.error(
                    session_id=session_id, content=err_text, save=True
                ).to_dict()

                if should_retry(classification) and retry_count < max_retries:
                    retry_count += 1

                    # 429 / 限流：指数退避后重试，避免立即重试加剧限流
                    if classification.category == "rate_limit":
                        base = app_config.LLM_RATE_LIMIT_BASE_WAIT
                        cap = app_config.LLM_RATE_LIMIT_MAX_WAIT
                        wait = min(base * (2 ** (retry_count - 1)), cap)
                        jitter = random.uniform(0, wait * 0.15)
                        total_wait = wait + jitter
                        logger.warning(
                            f"[DeepRunner] 429 限流，等待 {total_wait:.0f}s 后重试 "
                            f"(第 {retry_count}/{max_retries} 次)"
                        )
                        yield AgentMessage(
                            type=MessageType.STATUS,
                            sender=SenderType.SYSTEM,
                            session_id=session_id,
                            content={
                                "text": f"API 限流，等待 {total_wait:.0f}s 后重试 "
                                        f"(第 {retry_count}/{max_retries} 次)…"
                            },
                            save=False,
                        ).to_dict()
                        await asyncio.sleep(total_wait)

                    stream_input = {"messages": [HumanMessage(content="请继续完成之前的任务。")]}
                    continue

                metrics = await _finalize_tokens("error", error=err_text)
                yield _yield_run_metrics(metrics)
                async for msg in _yield_run_end(EndReason.ERROR):
                    yield msg
                await _maybe_release_sandbox(user_id, session_id, EndReason.ERROR)
                return


def _build_input(input_data: dict):
    """将 ask / resume_hitl 等输入统一转为 LangGraph 可消费的 stream_input。"""
    if input_data.get("command"):
        return input_data["command"]
    content = input_data.get("content") or ""
    if input_data.get("type") == ActionType.ASK.value or not input_data.get("command"):
        return {"messages": [HumanMessage(content=content)]}
    return input_data


async def _stream_with_timeout(
    compiled,
    stream_input,
    config: dict,
    adapter: DeepEventAdapter,
    stop_event: asyncio.Event,
    agent_wrapper,
    timeout_sec: int,
) -> AsyncGenerator[dict, None]:
    if timeout_sec and timeout_sec > 0:
        try:
            async with asyncio.timeout(timeout_sec):
                async for msg in _stream(
                    compiled, stream_input, config, adapter, stop_event, agent_wrapper
                ):
                    yield msg
            return
        except TimeoutError:
            await deep_storage.insert_audit_log(
                adapter.user_id, "run_timeout",
                session_id=adapter.session_id, run_id=adapter.run_id,
            )
            yield AgentMessage.error(
                session_id=adapter.session_id,
                content=f"Deep Agent 运行超时（{timeout_sec}秒）",
                save=True,
            ).to_dict()
            raise DeepRunTimeoutError(f"timeout after {timeout_sec}s")

    async for msg in _stream(
        compiled, stream_input, config, adapter, stop_event, agent_wrapper
    ):
        yield msg


async def _yield_adapter_messages(
    msg: dict,
    adapter: DeepEventAdapter,
    compiled,
    config: dict,
    agent_wrapper,
    store,
) -> AsyncGenerator[dict, None]:
    """统一 enrich / persist / publish / yield。"""
    if msg.get("type") == MessageType.INTERRUPT.value:
        await _persist_interrupt(msg, config, adapter, agent_wrapper)
        await update_turn_step(store, adapter.session_id, adapter.step_index)
    elif msg.get("type") == MessageType.SUBAGENT_START.value:
        msg = await _enrich_task_subagent_start(msg, adapter, compiled, config)
    elif msg.get("type") == MessageType.SUBAGENT_END.value:
        msg = await _finish_task_subagent(msg, adapter, compiled, config)
    _publish_agent_message(msg, adapter)
    if msg.get("save") and adapter.step_index:
        await update_turn_step(store, adapter.session_id, adapter.step_index)
    yield msg


async def _stream(
    compiled,
    stream_input,
    config: dict,
    adapter: DeepEventAdapter,
    stop_event: asyncio.Event,
    agent_wrapper,
) -> AsyncGenerator[dict, None]:
    """核心 astream 循环：messages + updates + tools，遇 interrupt 前补刷工具落库。"""
    store = getattr(agent_wrapper, "store", None)

    async def _flush_state_tools() -> AsyncGenerator[dict, None]:
        async for msg in flush_tool_messages_from_state(compiled, config, adapter):
            async for out in _yield_adapter_messages(
                msg, adapter, compiled, config, agent_wrapper, store
            ):
                yield out

    async for item in compiled.astream(
        stream_input,
        config=config,
        stream_mode=["messages", "updates", "tools", "custom"],
        subgraphs=True,
    ):
        if stop_event and stop_event.is_set():
            raise asyncio.CancelledError("User stop")

        namespace: tuple = ()
        mode: str
        chunk: Any
        if isinstance(item, tuple) and len(item) == 3:
            namespace, mode, chunk = item
        elif isinstance(item, tuple) and len(item) == 2:
            mode, chunk = item
        else:
            continue

        # 诊断日志：记录每次 astream yield 的 mode 和 chunk 大小
        # from config import get_logger as _runner_get_logger
        # _rdbg = _runner_get_logger(__name__)
        # _chunk_repr = ""
        # if mode == "messages":
        #     _msg, _meta = chunk
        #     _tcc_len = sum(len(tc.get("args") or "") for tc in (getattr(_msg, "tool_call_chunks", None) or []))
        #     _content_len = len(str(getattr(_msg, "content", "") or ""))
        #     _chunk_repr = f"content={_content_len}, tcc_total_args={_tcc_len}, node={_meta.get('langgraph_node')}"
        # elif mode == "tools":
        #     _chunk_repr = f"event={chunk[0] if isinstance(chunk, tuple) else '?'}"
        # elif mode == "updates":
        #     _chunk_repr = f"keys={list(chunk.keys()) if isinstance(chunk, dict) else '?'}"
        # _rdbg.info(f"[STREAM] mode={mode}, {_chunk_repr}")

        if mode == "tools":
            # tools 事件走 adapt_tools_stream_chunk，会绕过 _adapt_messages_chunk 中的
            # model→tools 转换检测（adapter.py:798），导致模型生成的 CHAT 文本未在
            # 工具调用之前刷新落库。此处兜底：若有待刷新的 model 段文本，先刷再发工具调用。
            if adapter._current_llm_text or adapter._current_reasoning_text:
                async for msg in adapter.finalize_model_segment():
                    async for out in _yield_adapter_messages(
                        msg, adapter, compiled, config, agent_wrapper, store
                    ):
                        yield out
                async for msg in adapter._emit_pending_tool_starts():
                    async for out in _yield_adapter_messages(
                        msg, adapter, compiled, config, agent_wrapper, store
                    ):
                        yield out

            async for msg in adapter.adapt_tools_stream_chunk(namespace, chunk):
                async for out in _yield_adapter_messages(
                    msg, adapter, compiled, config, agent_wrapper, store
                ):
                    yield out
            continue

        if mode == "custom":
            async for msg in adapter.adapt_custom_event(chunk):
                async for out in _yield_adapter_messages(
                    msg, adapter, compiled, config, agent_wrapper, store
                ):
                    yield out
            continue

        async for msg in adapter.adapt_astream_chunk(mode, chunk):
            async for out in _yield_adapter_messages(
                msg, adapter, compiled, config, agent_wrapper, store
            ):
                yield out

        if adapter.interrupted:
            async for out in _flush_state_tools():
                yield out
            break

    if not adapter.interrupted:
        async for msg in adapter.finalize_model_segment():
            async for out in _yield_adapter_messages(
                msg, adapter, compiled, config, agent_wrapper, store
            ):
                yield out

    # 流结束后补检 state.interrupts（部分 HITL 场景 updates 模式未捕获）
    if not adapter.interrupted:
        state = await compiled.aget_state(config)
        async for msg in adapter.build_interrupt_from_state(state):
            async for out in _yield_adapter_messages(
                msg, adapter, compiled, config, agent_wrapper, store
            ):
                yield out


def _publish_agent_message(msg: dict, adapter: DeepEventAdapter) -> None:
    """将 AgentMessage 规范化后发布到内部 EventBus（审计 / 指标处理器消费）。"""
    enriched = dict(msg)
    enriched.setdefault("user_id", adapter.user_id)
    enriched.setdefault("run_id", adapter.run_id)
    enriched.setdefault("conversation_id", adapter.conversation_id)
    publish_event(normalize_agent_message(enriched))


async def _lookup_tool_call_args(compiled, config: dict, tool_call_id: str) -> dict:
    """从 graph state 的 AIMessage.tool_calls 回填 task 参数（流式未捕获时）。"""
    if not compiled or not tool_call_id:
        return {}
    try:
        state = await compiled.aget_state(config)
        messages = (getattr(state, "values", None) or {}).get("messages") or []
        for msg in reversed(messages):
            for tc in getattr(msg, "tool_calls", None) or []:
                if isinstance(tc, dict) and tc.get("id") == tool_call_id:
                    _, args = normalize_tool_call(tc)
                    return args
    except Exception as exc:
        logger.debug(f"[DeepRunner] 回填 tool_call args 失败: {exc}")
    return {}


async def _resolve_task_args(
    content: dict,
    compiled,
    config: dict,
) -> dict:
    args = content.get("args") or {}
    if args and task_call_args_ready(args):
        return args
    tool_call_id = content.get("tool_call_id") or ""
    recovered = await _lookup_tool_call_args(compiled, config, tool_call_id)
    return recovered or args


async def _enrich_task_subagent_start(
    msg: dict,
    adapter: DeepEventAdapter,
    compiled=None,
    config: dict = None,
) -> dict:
    """task 工具开始时入库，并 enrich 消息供前端渲染启动卡片。"""
    content = dict(msg.get("content") or {})
    if content.get("tool_name") != "task":
        return msg

    subagent_run_id = content.get("subagent_run_id")
    args = await _resolve_task_args(content, compiled, config or {})
    content["args"] = args
    name = task_subagent_name(args)
    input_summary = summarize_subagent_input(
        args, max_chars=app_config.CONTENT_SUMMARY_MAX_CHARS
    )

    if subagent_run_id and subagent_run_id not in adapter._subagent_runs_persisted:
        adapter._subagent_runs_persisted.add(subagent_run_id)
        await deep_storage.create_subagent_run(
            subagent_run_id,
            adapter.session_id,
            name,
            parent_run_id=adapter.run_id,
            agent_id=adapter.agent_id,
            input_summary=input_summary,
        )

    content["name"] = name
    content["input_summary"] = input_summary
    content["status"] = "running"
    out = dict(msg)
    out["content"] = content
    return out


async def _finish_task_subagent(
    msg: dict,
    adapter: DeepEventAdapter,
    compiled=None,
    config: dict = None,
) -> dict:
    """task 工具结束时更新 final_report（输出仍可选写 trace artifact）。"""
    content = dict(msg.get("content") or {})
    if content.get("tool_name") != "task":
        return msg

    subagent_run_id = content.get("subagent_run_id") or str(uuid.uuid4())
    final_report = content.get("final_report") or content.get("tool_result") or ""
    args = await _resolve_task_args(content, compiled, config or {})
    content["args"] = args
    name = content.get("name") or task_subagent_name(args)
    input_summary = content.get("input_summary") or summarize_subagent_input(
        args, max_chars=app_config.CONTENT_SUMMARY_MAX_CHARS
    )

    if subagent_run_id not in adapter._subagent_runs_persisted:
        adapter._subagent_runs_persisted.add(subagent_run_id)
        await deep_storage.create_subagent_run(
            subagent_run_id,
            adapter.session_id,
            name,
            parent_run_id=adapter.run_id,
            agent_id=adapter.agent_id,
            input_summary=input_summary,
        )

    trace = {"event": "subagent_internal", "output": str(final_report)[:2000]}
    ref_id = await deep_storage.append_subagent_trace(
        subagent_run_id, trace, adapter.user_id, adapter.session_id
    )
    report_summary = truncate_summary(str(final_report), max_chars=500)
    await deep_storage.finish_subagent_run(
        subagent_run_id,
        "completed",
        final_report=report_summary,
        trace_ref_id=ref_id,
    )

    content["name"] = name
    content["input_summary"] = input_summary
    content["status"] = "completed"
    content["final_report"] = report_summary
    out = dict(msg)
    out["content"] = content
    return out


async def _persist_interrupt(msg: dict, config: dict, adapter: DeepEventAdapter, agent_wrapper):
    """将 HITL 中断写入 LangGraph Store，供 resume_hitl 恢复。"""
    content = msg.get("content") or {}
    store = getattr(agent_wrapper, "store", None)
    thread_id = config.get("configurable", {}).get("thread_id", "")
    request_id = content.get("request_id")
    await save_deep_interrupt(
        store,
        request_id=request_id,
        thread_id=thread_id,
        session_id=adapter.session_id,
        user_id=adapter.user_id,
        actions=content.get("actions", []),
        num_pending=content.get("total_count", 0),
        run_id=adapter.run_id,
        conversation_id=adapter.conversation_id,
        step_index=adapter.step_index,
    )
    await mark_awaiting_hitl(store, adapter.session_id, adapter.step_index)
    await insert_agent_run_event(
        run_id=adapter.run_id,
        session_id=adapter.session_id,
        user_id=adapter.user_id,
        conversation_id=adapter.conversation_id,
        event_type="hitl_pending",
        message=str(request_id or "")[:2000],
    )
    from deep_agent_server.sandbox import touch_session_sandbox

    await touch_session_sandbox(adapter.user_id, adapter.session_id)


async def run_deep_agent_collect(
    agent_wrapper,
    input_data: dict,
    config: dict,
    session_id: str,
    user_id: str,
    stop_event: asyncio.Event = None,
    conversation_id: str = None,
    run_id: str = None,
    agent_id: str = None,
    initial_step_index: int = 0,
    is_resume: bool = False,
) -> Dict[str, Any]:
    """非流式收集 Deep 运行结果。"""
    messages = []
    final_answer = ""
    status = "completed"
    interrupt = {}
    tool_calls = []
    tool_results = []
    subagent_runs = []
    error = ""
    token_usage = {}

    async for msg in run_deep_agent(
        agent_wrapper, input_data, config, session_id, user_id,
        stop_event=stop_event,
        conversation_id=conversation_id,
        run_id=run_id,
        agent_id=agent_id,
        initial_step_index=initial_step_index,
        is_resume=is_resume,
    ):
        messages.append(msg)
        mt = msg.get("type")
        content = msg.get("content")
        if mt == MessageType.CHAT.value and msg.get("sender") == SenderType.ASSISTANT.value:
            if isinstance(content, str):
                final_answer += content
        elif mt == MessageType.END.value:
            status = str(content) if content else "completed"
        elif mt == MessageType.ERROR.value:
            status = "error"
            error = str(content)
        elif mt == MessageType.INTERRUPT.value:
            status = "interrupted"
            interrupt = content if isinstance(content, dict) else {}
        elif mt == MessageType.TOOL_CALL.value:
            tool_calls.append(content)
        elif mt == MessageType.TOOL_RESULT.value:
            tool_results.append(content)
        elif mt == MessageType.SUBAGENT_END.value:
            subagent_runs.append(content)
        elif mt == MessageType.RUN_METRICS.value:
            c = content if isinstance(content, dict) else {}
            token_usage = c.get("token_usage") or token_usage

    return {
        "status": status,
        "agent_mode": "deep",
        "run_id": run_id,
        "conversation_id": conversation_id,
        "final_answer": final_answer,
        "reasoning": "",
        "messages": messages,
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "subagent_runs": subagent_runs,
        "todos": [],
        "filesystem_ops": [],
        "skills_used": [],
        "token_usage": token_usage,
        "error": error,
        "interrupt": interrupt,
    }
