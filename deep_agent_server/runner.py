"""Deep Agent 运行器 — stream_events + stop/timeout。

消费 DeepAgentWrapper.agent.astream，经 DeepEventAdapter 转为 AgentMessage，
并负责 run 生命周期（DB 记录、EventBus 发布、HITL 持久化、错误重试）。
"""

import asyncio
from contextlib import aclosing
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
from deep_agent_server.repetition_guard import ModelTextGuard, RepetitionDetected
from deep_agent_server.concurrency import DeepConcurrencyGuard, UserRunLimitError
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


def build_repetition_recovery_message() -> HumanMessage:
    return HumanMessage(content=(
        "系统检测到你刚才连续重复了相同的说明，当前生成已中止。"
        "请继续完成用户原任务，并换一种执行路径：先依据已有工具结果确认已完成的步骤。"
        "若已有认证或权限失败，立即停止该服务的所有重试，向用户报告。"
        "若文件、模块、接口或可靠实现不存在，最多核查一次替代路径；没有就报告无法完成。"
        "不要猜测协议或宏格式，不要重复已失败的操作。"
        "若有可执行的下一步，直接调用工具；否则明确说明阻碍并结束本轮。"
    ))


def build_run_metrics_content(
    *,
    run_id: str,
    conversation_id: str,
    status: str,
    token_usage: dict,
    started_at: datetime,
    finished_at: datetime,
    session_summary: Optional[dict] = None,
    html_url: Optional[str] = None,
) -> dict:
    """构造 run_metrics 消息 content（便于单测）。

    session_summary 为整轮会话的 token 汇总（含上下文占比），
    与 /sessions/run-metrics 接口的 summary 同源同构。

    html_url 为本轮最后生成的 HTML 文件的免登录分享链接（无则不带该字段），
    供调用方在流结束时就近取用，无需从回复文本里解析。
    """
    duration_ms = max(0, int((finished_at - started_at).total_seconds() * 1000))
    usage = {
        "prompt_tokens": int(token_usage.get("prompt_tokens") or 0),
        "completion_tokens": int(token_usage.get("completion_tokens") or 0),
        "total_tokens": int(token_usage.get("total_tokens") or 0),
    }
    # 维度化 token 统计：按消息类型分解的 prompt 构成 + 每次调用明细
    if token_usage.get("prompt_breakdown"):
        usage["prompt_breakdown"] = token_usage["prompt_breakdown"]
    if token_usage.get("call_details"):
        usage["call_count"] = token_usage.get("call_count", len(token_usage["call_details"]))
        usage["call_details"] = token_usage["call_details"]
    content = {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "status": status,
        "token_usage": usage,
        "duration_ms": duration_ms,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
    }
    if session_summary:
        content["session_summary"] = session_summary
    if html_url:
        content["html_url"] = html_url
    return content


def aggregate_session_token_summary(runs: list) -> dict:
    """聚合会话内所有 run 的 token_usage，得到会话级上下文使用情况与占比。

    对应 /sessions/run-metrics 接口 summary 字段（同源同构），维度与
    _finalize_tokens 日志一致：calls / prompt / completion / total /
    system / user / ai / tool / estimated_total / peak_*，
    breakdown_ratio 为各分类占估算总 token 的比例（0~1）。
    """
    run_count = call_count = 0
    prompt = completion = total = 0
    sys_t = user_t = ai_t = tool_t = est_total = api_total = 0
    peak_call = peak_prompt = peak_msgs = 0
    for r in runs or []:
        usage = r.get("token_usage") or {}
        prompt += int(usage.get("prompt_tokens") or 0)
        completion += int(usage.get("completion_tokens") or 0)
        total += int(usage.get("total_tokens") or 0)
        call_count += int(usage.get("call_count") or 0)
        bd = usage.get("prompt_breakdown") or {}
        sys_t += int(bd.get("system_tokens") or 0)
        user_t += int(bd.get("user_tokens") or 0)
        ai_t += int(bd.get("ai_tokens") or 0)
        tool_t += int(bd.get("tool_tokens") or 0)
        est_total += int(bd.get("estimated_total") or 0)
        api_total += int(bd.get("api_total") or 0)
        for d in usage.get("call_details") or []:
            p = int(d.get("prompt_tokens") or 0)
            if p > peak_prompt:
                peak_prompt = p
                peak_call = int(d.get("call") or 0)
                peak_msgs = int(d.get("messages_count") or 0)
        run_count += 1
    denom = est_total or 1
    return {
        "run_count": run_count,
        "call_count": call_count,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "system_tokens": sys_t,
        "user_tokens": user_t,
        "ai_tokens": ai_t,
        "tool_tokens": tool_t,
        "estimated_total": est_total,
        "api_total": api_total,
        "breakdown_ratio": {
            "system_tokens": round(sys_t / denom, 4),
            "user_tokens": round(user_t / denom, 4),
            "ai_tokens": round(ai_t / denom, 4),
            "tool_tokens": round(tool_t / denom, 4),
        },
        "peak_call": peak_call,
        "peak_prompt_tokens": peak_prompt,
        "peak_messages_count": peak_msgs,
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
    html_url: Optional[str] = None,
) -> dict:
    content = build_run_metrics_content(
        run_id=run_id,
        conversation_id=conversation_id,
        status=status,
        token_usage=token_usage,
        started_at=started_at,
        finished_at=finished_at,
        html_url=html_url,
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
    detail = f"（{text[:200]}）" if text else ""

    # 模型接口 404：模型名称或 Base URL 不存在（常见于配置填错）。
    # 文案包含「配置模型 API Key」，前端据此展示“去配置模型”入口。
    if "notfounderror" in exc_name or "404" in text:
        return (
            f"模型配置错误：模型名称或接口地址不存在（404）{detail}。"
            "请检查模型名称与 Base URL，并在「模型配置」中重新配置模型 API Key 后重试。"
        )

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
            f"模型配置错误：认证失败{detail}，请检查 API Key 是否正确、是否过期，"
            "以及 Base URL / 模型名称是否填写有误。"
            "请在「模型配置」中重新配置模型 API Key 后重试。"
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
        agent_wrapper=agent_wrapper,
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
        await deep_storage.create_agent_run(
            run_id, user_id, session_id,
            agent_id=resolved_agent_id,
            conversation_id=conversation_id,
            mcp_tools=mcp_summary,
            skills=[],  # skills 由 SkillsMiddleware 管理，不再手动记录
        )
        publish_event(make_run_started(
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            conversation_id=conversation_id,
        ))

    if not app_config.SANDBOX_ENABLED:
        logger.debug("[DeepRunner] SANDBOX_ENABLED=false，execute 工具不可用（文件读写仍可用）")

    # 存库用户提问（与 Light agent_runner 一致）
    if not input_data.get("command"):
        question = (input_data.get("content") or "").strip()
        attachments = input_data.get("attachments") or []
        if question:
            user_content: dict = {"text": question}
            if attachments:
                user_content["attachments"] = attachments
            yield adapter._enrich(
                AgentMessage(
                    type=MessageType.CHAT,
                    sender=SenderType.USER,
                    session_id=session_id,
                    content=user_content,
                    save=True,
                ),
                track_step=False,
            )

    stream_input = await _build_input(input_data, user_id=user_id)
    if not input_data.get("command"):
        question = (input_data.get("content") or "").strip()
        if question:
            stream_input = await inject_user_memory_into_stream_input(
                stream_input,
                store=store,
                user_id=user_id,
                query=question,
            )
    # ── HTML 分享链接注入 ──
    wrapper = agent_wrapper
    if hasattr(wrapper, "html_share_links") and wrapper.html_share_links:
        announced = getattr(wrapper, "_announced_html_shares", set())
        new_links = {
            vp: url
            for vp, url in wrapper.html_share_links.items()
            if vp not in announced
        }
        if new_links:
            lines = []
            for vp, url in new_links.items():
                filename = vp.split("/")[-1]
                lines.append(
                    f"- **{filename}**（路径 `{vp}`）: `{url}`"
                    f"（可直接浏览器打开，支持 iframe 嵌入）"
                )
            notice = (
                "📌 **HTML 文件分享链接（会话历史中生成）**\n\n"
                "以下链接为本会话此前生成的 HTML 文件自动创建的免登录分享链接。\n"
                "如与当前问题相关，可在回复中引用这些链接；如无关请忽略：\n"
                + "\n".join(lines)
                + "\n\n你可以将这些链接提供给用户，或在回复中引用这些链接。"
            )
            messages = stream_input.get("messages", [])
            messages.append(HumanMessage(content=notice))
            stream_input["messages"] = messages
            # 原地更新（勿重新赋值新 set）：该集合与 HtmlShareLinkMiddleware
            # 共享同一引用，替换会断开共享 —— 中间件本轮注入的路径将不在
            # 新集合中，导致下一轮重复回灌同一链接。
            announced.update(new_links.keys())
            logger.info(
                f"[DeepRunner] 已注入 HTML 分享链接 session={session_id} "
                f"count={len(new_links)}"
            )
    timeout_sec = app_config.DEEP_TIMEOUT_SECONDS
    retry_count = 0
    max_retries = 1
    repetition_retries = 0
    run_config, token_acc = attach_token_callback(config)
    # ── Langfuse 观测（可选）：凭据同步 + 挂载 CallbackHandler ──
    from deep_agent_server.langfuse_integration import (
        attach_langfuse_handler,
        ensure_langfuse_env,
    )

    ensure_langfuse_env()
    run_config = attach_langfuse_handler(run_config)

    # ── 检测同 session 内 skill/MCP 变更，清过期状态 + 通知 agent ──
    config_changes = getattr(agent_wrapper, "config_changes", None) or {}
    change_notices = []
    if config_changes.get("skills"):
        change_notices.append(
            "⚠️ **技能配置已变更**：当前会话启用的技能发生了变化。"
            "请使用 `ls /skills` 重新查看可用技能列表，不要依赖之前的记忆。"
        )
    if config_changes.get("mcp"):
        change_notices.append(
            "⚠️ **MCP 工具配置已变更**：当前会话可用的 MCP 工具发生了变化。"
            "请重新确认可用的工具列表，不要依赖之前的记忆。"
        )
    if change_notices:
        notice = "\n\n".join(change_notices)
        messages = stream_input.get("messages", [])
        messages.append(HumanMessage(content=notice))
        stream_input["messages"] = messages
        logger.info(
            f"[DeepRunner] 已注入配置变更通知 session={session_id} "
            f"changes={list(config_changes.keys())}"
        )
    run_started_at = datetime.now(timezone.utc)
    adapter.run_started_at = run_started_at

    async def _finalize_tokens(status: str, error: str = "") -> dict:
        usage = token_acc.as_dict()
        finished_at = datetime.now(timezone.utc)
        model_name = ""
        try:
            profile = await deep_storage.get_active_user_llm_profile(user_id) or {}
            model_name = profile.get("model") or ""
        except Exception:
            pass
        # 本轮生成的 HTML 分享链接（无则不带）：一处解析，run_metrics 推送与
        # result_summary 落库共用（此前只推 SSE，排查时无法从 DB 核查）
        html_url = ""
        try:
            html_url = await adapter.latest_html_share_url() or ""
        except Exception:
            logger.warning(f"[DeepRunner] 解析 HTML 分享链接失败 run={run_id}", exc_info=True)
        summary = {"conversation_id": conversation_id, "token_usage": usage}
        if html_url:
            summary["html_url"] = html_url
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
        # 会话级 token 汇总（整轮会话上下文占比），与 /sessions/run-metrics summary 同源
        session_summary = None
        try:
            session_runs = await deep_storage.list_agent_runs_for_session(session_id, user_id)
            session_summary = aggregate_session_token_summary(session_runs)
            # 补充当前上下文占比：本轮 API 实际输入 token（最近一次 LLM 调用
            # 送入的完整上下文）与模型窗口上限的比值
            from utils.context_size import _resolve_max_input_tokens, get_context_percentage
            max_input = _resolve_max_input_tokens(model_name or "")
            # 分子取最后一次 LLM 调用的 prompt_tokens —— 顶层 prompt_tokens 是本 run
            # 内所有调用的累加值（计费口径），一次 run 有几十次调用，用它算占比
            # 会得到数倍于窗口的假值（与 utils.context_size 同一口径）
            call_details = usage.get("call_details") or []
            context_tokens = int(
                (call_details[-1].get("prompt_tokens") if call_details else 0)
                or usage.get("prompt_tokens")
                or 0
            )
            session_summary["max_input_tokens"] = max_input
            session_summary["percentage"] = get_context_percentage(context_tokens, max_input)
        except Exception:
            logger.warning(
                f"[DeepRunner] 聚合会话 token 汇总失败 run={run_id}", exc_info=True
            )
        # 维度化 token 日志：按消息类型输出 prompt 构成，便于定位上下文消耗来源
        breakdown = usage.get("prompt_breakdown") or {}
        if breakdown:
            top_details = usage.get("call_details") or []
            max_call = ""
            if top_details:
                peak = max(top_details, key=lambda d: d.get("prompt_tokens") or 0)
                max_call = (
                    f" peak_call={peak.get('call')} "
                    f"peak_prompt={peak.get('prompt_tokens')} "
                    f"peak_messages={peak.get('messages_count')}"
                )
            logger.info(
                f"[TokenUsage] run={run_id} status={status} "
                f"calls={usage.get('call_count')} "
                f"prompt={usage.get('prompt_tokens')} completion={usage.get('completion_tokens')} "
                f"total={usage.get('total_tokens')} "
                f"system={breakdown.get('system_tokens')} "
                f"user={breakdown.get('user_tokens')} "
                f"ai={breakdown.get('ai_tokens')} tool={breakdown.get('tool_tokens')} "
                f"estimated_total={breakdown.get('estimated_total')}"
                f"{max_call}"
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
            session_summary=session_summary,
            html_url=html_url or None,
        )

    async def _yield_run_metrics(metrics: dict):
        """推送 run_metrics（html_url 已随 _finalize_tokens 一并解析并落库）。"""
        return _run_metrics_message(
            adapter,
            session_id,
            run_id,
            conversation_id,
            metrics["status"],
            metrics["token_usage"],
            run_started_at,
            datetime.fromisoformat(metrics["finished_at"]),
            html_url=metrics.get("html_url"),
        )

    async def _yield_workspace_files_async(finished_at: Optional[str] = None):
        # 磁盘兜底用 run 生命周期窗口 [run_started_at, finished_at] 扫描，
        # 覆盖正常完成/出错/用户 stop 全形态（不依赖固定秒数窗口）
        files_msg = await adapter.async_conversation_files_message(
            run_started_at=run_started_at,
            run_ended_at=finished_at,
        )
        if files_msg:
            yield files_msg

    async def _emit_end_then_finalize(
        reason: EndReason,
        status: str,
        *,
        error: str = "",
    ):
        """先推 end 解除前端停止按钮，再做慢收尾（token/文件）。

        以往顺序是 finalize → workspace 扫盘 → end，NFS/DB 慢时用户已看到最终
        回复，但输入框仍卡在停止按钮。end 提前推送后，metrics / workspace_file
        仍可迟到补齐；前端需容忍这些迟到帧，不得重新打开 isGenerating。
        """
        yield adapter.end_message(reason)
        try:
            metrics = await _finalize_tokens(status, error=error)
            yield await _yield_run_metrics(metrics)
            async for msg in _yield_workspace_files_async(metrics["finished_at"]):
                yield msg
        except Exception as e:
            logger.warning(
                f"[DeepRunner] 收尾失败（end 已推送）session={session_id} "
                f"run={run_id} status={status}: {e}",
                exc_info=True,
            )

    try:
        _concurrency_cm = DeepConcurrencyGuard(user_id, session_id)
        await _concurrency_cm.acquire()
    except UserRunLimitError as e:
        yield AgentMessage.error(
            session_id=session_id,
            content=str(e),
            save=True,
        ).to_dict()
        yield adapter.end_message(EndReason.ERROR)
        return

    try:
        while True:
            if stop_event and stop_event.is_set():
                async for msg in _emit_end_then_finalize(EndReason.STOP, "stopped"):
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
                    yield await _yield_run_metrics(metrics)
                    return

                async for msg in _emit_end_then_finalize(EndReason.COMPLETED, "completed"):
                    yield msg
                await _maybe_release_sandbox(user_id, session_id, EndReason.COMPLETED)
                return

            except RepetitionDetected as error:
                logger.warning(
                    "[DeepRunner] repetition detected session={} run={} period={} retry={}",
                    session_id, run_id, error.hit.period_chars, repetition_retries,
                )
                adapter.discard_model_segment()
                await deep_storage.insert_audit_log(
                    user_id, "model_repetition_detected",
                    session_id=session_id, run_id=run_id,
                    detail={
                        "period_chars": error.hit.period_chars,
                        "repeat_count": error.hit.repeat_count,
                        "attempt": repetition_retries + 1,
                    },
                )
                if repetition_retries == 0 and not (stop_event and stop_event.is_set()):
                    repetition_retries += 1
                    yield adapter._enrich(AgentMessage.status(
                        session_id=session_id,
                        content="检测到模型重复输出，正在尝试其他执行路径。",
                        save=False,
                    ), track_step=False)
                    stream_input = {"messages": [build_repetition_recovery_message()]}
                    continue
                yield adapter._enrich(AgentMessage.error(
                    session_id=session_id,
                    content="模型再次重复输出，已停止本轮执行。此前已完成的工具操作仍保留。",
                    save=True,
                ))
                async for msg in _emit_end_then_finalize(
                    EndReason.ERROR, "error", error="model_repetition_detected"
                ):
                    yield msg
                await _maybe_release_sandbox(user_id, session_id, EndReason.ERROR)
                return

            except DeepRunTimeoutError:
                async for msg in _emit_end_then_finalize(
                    EndReason.ERROR, "error", error="timeout"
                ):
                    yield msg
                await _maybe_release_sandbox(user_id, session_id, EndReason.ERROR)
                return

            except asyncio.CancelledError:
                logger.info(f"[DeepRunner] 会话 {session_id} 取消")
                async for msg in _emit_end_then_finalize(EndReason.STOP, "stopped"):
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
                    yield await _yield_run_metrics(metrics)
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

                async for msg in _emit_end_then_finalize(
                    EndReason.ERROR, "error", error=err_text
                ):
                    yield msg
                await _maybe_release_sandbox(user_id, session_id, EndReason.ERROR)
                return
    finally:
        _concurrency_cm.release()


async def _build_input(input_data: dict, user_id: str = ""):
    """将 ask / resume_hitl 等输入统一转为 LangGraph 可消费的 stream_input。

    若有附件，将其元数据追加到 HumanMessage.content 末尾（仅 LLM 可见，
    不推送到前端）。附件信息仅存在于当前轮消息中，不会跨轮累积。
    """
    if input_data.get("command"):
        return input_data["command"]
    content = input_data.get("content") or ""
    attachments = input_data.get("attachments") or []

    if attachments and user_id:
        from deep_agent_server.attachment_service import build_attachment_message
        ctx = await build_attachment_message(user_id, attachments)
        if ctx:
            content = content + "\n\n" + ctx

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
    """核心 astream 循环：v2 格式，直接从 ns 提取 tool_call_id 识别子 agent。"""
    store = getattr(agent_wrapper, "store", None)

    async def _flush_state_tools() -> AsyncGenerator[dict, None]:
        async for msg in flush_tool_messages_from_state(compiled, config, adapter):
            async for out in _yield_adapter_messages(
                msg, adapter, compiled, config, agent_wrapper, store
            ):
                yield out

    guard = ModelTextGuard()
    stream = compiled.astream(
        stream_input,
        config=config,
        stream_mode=["messages", "updates", "tools", "custom"],
        subgraphs=True,
        version="v2",
    )
    async with aclosing(stream):
        async for item in stream:
            async for output in _process_stream_item(
                item, guard, adapter, compiled, config, agent_wrapper, store,
                stop_event, _flush_state_tools,
            ):
                yield output
            if adapter.interrupted:
                break

    if not adapter.interrupted:
        async for msg in adapter.finalize_model_segment():
            async for out in _yield_adapter_messages(
                msg, adapter, compiled, config, agent_wrapper, store
            ):
                yield out

    if not adapter.interrupted:
        state = await compiled.aget_state(config)
        async for msg in adapter.build_interrupt_from_state(state):
            async for out in _yield_adapter_messages(
                msg, adapter, compiled, config, agent_wrapper, store
            ):
                yield out


async def _process_stream_item(
    item, guard, adapter, compiled, config, agent_wrapper, store,
    stop_event, flush_state_tools,
) -> AsyncGenerator[dict, None]:
        if stop_event and stop_event.is_set():
            raise asyncio.CancelledError("User stop")

        # v2 格式：统一 dict {type, ns, data}
        mode: str = item["type"]
        namespace: tuple = item["ns"]
        chunk: Any = item["data"]
        guard.inspect(mode, namespace, chunk)

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
            # tools 事件绕过 _adapt_messages_chunk，model→tools 转换可能未触发。
            # 无论缓冲区是否为空都刷一次：空则 no-op，有内容则落库 + 推送。
            #
            # namespace 优先级：
            # - 子 agent 内部的 tools 事件带有非空 namespace（如 ("tools:xxx",)），
            #   此时应使用 namespace 作为 flush 目标，确保并发子 agent 场景下
            #   文本归属到正确的 subagent_run_id。
            # - 主 agent 层面的 tools 事件（如 task tool-finished）namespace 为空，
            #   此时回退到 _last_model_namespace，防止 subagent_run_id 丢失。
            saved_ns = getattr(adapter, "_active_namespace", ())
            flush_ns = namespace if namespace else adapter._last_model_namespace
            adapter._active_namespace = flush_ns
            # 预激活 + 排空 SUBAGENT_START，确保其先于 reasoning/chat 推送
            adapter._pre_activate_namespace(flush_ns)
            for msg in adapter._drain_pending_activations():
                async for out in _yield_adapter_messages(
                    msg, adapter, compiled, config, agent_wrapper, store
                ):
                    yield out
            try:
                # finalize_model_segment 使用 flush_ns（文本归属到产生文本的 model）
                async for msg in adapter.finalize_model_segment():
                    async for out in _yield_adapter_messages(
                        msg, adapter, compiled, config, agent_wrapper, store
                    ):
                        yield out
            finally:
                adapter._active_namespace = saved_ns

            # _emit_pending_tool_starts 在 finally 之后执行，使用 saved_ns
            # 而非 flush_ns。工具归属到当前上下文，而非上一个 model 节点。
            # 非强制合并：tools 事件可能先于 messages 完整 chunks 到达，
            # 未闭合的 args 跳过合并、保留 accumulator，等离开 model 节点时
            # force 合并补全（避免展示与 langchain 实际执行参数不一致）。
            async for msg in adapter._emit_pending_tool_starts():
                async for out in _yield_adapter_messages(
                    msg, adapter, compiled, config, agent_wrapper, store
                ):
                    yield out

            # tools 事件本身使用原始 namespace（子 agent 内部工具调用需要正确归属）
            async for msg in adapter.adapt_tools_stream_chunk(namespace, chunk):
                async for out in _yield_adapter_messages(
                    msg, adapter, compiled, config, agent_wrapper, store
                ):
                    yield out
            return

        if mode == "custom":
            async for msg in adapter.adapt_custom_event(chunk):
                async for out in _yield_adapter_messages(
                    msg, adapter, compiled, config, agent_wrapper, store
                ):
                    yield out
            return

        async for msg in adapter.adapt_astream_chunk(mode, chunk, namespace=namespace):
            async for out in _yield_adapter_messages(
                msg, adapter, compiled, config, agent_wrapper, store
            ):
                yield out

        # 排空延迟发射的 SUBAGENT_START（_activate_pregel 产生）
        for msg in adapter._drain_pending_activations():
            async for out in _yield_adapter_messages(
                msg, adapter, compiled, config, agent_wrapper, store
            ):
                yield out

        if adapter.interrupted:
            async for out in flush_state_tools():
                yield out
            return

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
    adapter: Optional[DeepEventAdapter] = None,
) -> dict:
    """从 content / graph state / adapter 内部状态三级恢复 task 参数。"""
    args = content.get("args") or {}
    if args and task_call_args_ready(args):
        return args
    tool_call_id = content.get("tool_call_id") or ""

    # 第一回退：graph state 中按 tool_call_id 查找
    recovered = await _lookup_tool_call_args(compiled, config, tool_call_id)
    if recovered and task_call_args_ready(recovered):
        return recovered

    # 第二回退：adapter 内部状态（处理流式 chunk / 事件乱序场景）
    if adapter is not None:
        subagent_run_id = content.get("subagent_run_id") or ""
        # 按 subagent_run_id 查 _active_subagent_inputs
        if subagent_run_id:
            cached = adapter._active_subagent_inputs.get(subagent_run_id)
            if cached and isinstance(cached, dict) and task_call_args_ready(cached):
                return cached
        # 按 tool_call_id 查 _pending_subagent_inputs
        if tool_call_id:
            cached = adapter._pending_subagent_inputs.get(tool_call_id)
            if cached and isinstance(cached, dict) and task_call_args_ready(cached):
                return cached
        # 按 tool_call_id 查 _active_subagents 得 UUID，再查 _active_subagent_inputs
        if tool_call_id:
            uuid_from_tc = adapter._active_subagents.get(tool_call_id)
            if uuid_from_tc:
                cached = adapter._active_subagent_inputs.get(uuid_from_tc)
                if cached and isinstance(cached, dict) and task_call_args_ready(cached):
                    return cached

        # 第三回退：FIFO 队列（namespace UUID 与 LLM call_* ID 不匹配场景）
        unmatched = getattr(adapter, "_unmatched_subagent_uuids", None)
        if unmatched:
            for _ in range(len(unmatched)):
                candidate_uuid = unmatched.popleft()
                candidate = adapter._active_subagent_inputs.get(candidate_uuid)
                if candidate and isinstance(candidate, dict) and task_call_args_ready(candidate):
                    return candidate
                # 未匹配的放回队尾（可能属于后续的子智能体）
                unmatched.append(candidate_uuid)

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
    args = await _resolve_task_args(content, compiled, config or {}, adapter=adapter)
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
    args = await _resolve_task_args(content, compiled, config or {}, adapter=adapter)
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
    html_url = ""

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
            html_url = c.get("html_url") or html_url

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
        "html_url": html_url,
        "error": error,
        "interrupt": interrupt,
    }
