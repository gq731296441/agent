from fastapi.responses import StreamingResponse, JSONResponse
from fastapi import APIRouter, Body, Request
from typing import Any, Dict, AsyncGenerator, Optional
from langgraph.types import Command
import json
import asyncio

from utils.hitl_store import pop_interrupt, get_interrupt, build_hitl_decisions, build_hitl_history_decisions
from utils.session_utils import (
    get_or_create_session,
    validate_session_access,
    setup_new_session,
    SESSION_ACCESS_DENIED_MSG,
)
from db import storage

from config.config import app_config
from deep_agent_server.session.readiness import SessionReadinessService

# 公共模块导入
from config import get_logger
from common.enums import EndReason, ActionType, MessageType, SenderType
from common.schemas import AgentMessage

logger = get_logger(__name__)

AGENT_TIMEOUT = 300  # 非流式请求超时（秒）


async def _persist_hitl_resume_to_history(
    session_id: str,
    request_id: str,
    info: Dict[str, Any],
    user_decisions: list,
) -> list:
    """resume 成功后回写 chat_history：hitl_status=resolved + 逐条 decisions。返回 history decisions。"""
    decisions = build_hitl_history_decisions(
        info.get("actions") or [],
        user_decisions,
    )
    ok = await storage.resolve_interrupt_in_history(
        session_id,
        request_id,
        decisions=decisions,
        hitl_status="resolved",
    )
    if not ok:
        from config import get_logger
        get_logger(__name__).warning(
            f"[HITL] chat_history 未找到 request_id={request_id} session={session_id}"
        )
    return decisions


def _extract_capabilities(payload: Dict[str, Any]) -> tuple[list, Optional[list]]:
    """从 payload 解析 mcp 与 skills（capabilities.skills）。"""
    capabilities = payload.get("capabilities") or {}
    mcp_list = capabilities.get("mcp") or []
    skills = capabilities.get("skills")
    if skills is not None and not isinstance(skills, list):
        skills = None
    elif skills is not None:
        skills = [str(s).strip() for s in skills if str(s).strip()]
    return mcp_list, skills


router = APIRouter(prefix="/agent", tags=["agent"])


async def _check_session_readiness(
    action: str,
    session_id: str,
    user_id: str,
    payload: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Deep 模式下检查就绪/排队。返回 dict 表示应短路 yield；None 表示继续执行。"""
    if (app_config.AGENT_RUNTIME or "deep").lower() == "light":
        return None

    if action in (ActionType.STOP, ActionType.STOP.value):
        return None

    if action in (ActionType.RESUME_HITL, ActionType.RESUME_HITL.value):
        if not await SessionReadinessService.is_ready(session_id):
            return {
                "type": MessageType.ERROR.value,
                "error": "会话尚未就绪，无法恢复审批",
            }
        return None

    if action in (ActionType.ASK, ActionType.ASK.value):
        result = await SessionReadinessService.enqueue_message(session_id, user_id, payload)
        if result.get("error"):
            return {
                "type": MessageType.ERROR.value,
                "error": result["error"],
            }
        if result.get("queued"):
            return {
                "type": MessageType.MESSAGE_QUEUED.value,
                "position": result.get("position", 0),
                "status": result.get("status"),
            }
    return None


async def process_agent_stream(
    manager_agents,
    action: str,
    user_id: str,
    user_token: str,
    session_id: str,
    payload: Dict[str, Any],
    *,
    allow_if_missing: bool = False,
    wait_on_queue: bool = False,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    统一的 Agent 流处理器，支持多种 action 类型。
    
    Args:
        manager_agents: AgentManager 实例
        action: 动作类型 (ask, resume_hitl, stop)
        user_id: 用户 ID
        user_token: 用户 Token
        session_id: 会话 ID
        payload: 请求载荷（包含 action 特定的参数）
        
    Yields:
        标准化的消息字典
    """
    if action != ActionType.STOP and action != ActionType.STOP.value:
        if not await validate_session_access(
            user_id, session_id, allow_if_missing=allow_if_missing
        ):
            yield AgentMessage.error(
                session_id=session_id,
                content=SESSION_ACCESS_DENIED_MSG,
            ).to_frontend_dict()
            return

    if action not in (ActionType.STOP, ActionType.STOP.value):
        from utils.subscription_guard import validate_ask_session

        payload_agent_id = payload.get("agent_id")
        ok, err_msg = await validate_ask_session(user_id, session_id, payload_agent_id)
        if not ok:
            yield AgentMessage.error(session_id=session_id, content=err_msg).to_frontend_dict()
            return

    if action in (ActionType.ASK, ActionType.ASK.value):
        question = payload.get("question", "")
        if question and app_config.USER_MEMORY_REMEMBER_ENABLED:
            from deep_agent_server.memory.remember import parse_remember_message
            from deep_agent_server.memory import get_user_memory_service

            parsed = parse_remember_message(question)
            if parsed:
                memory_text, source = parsed
                kv_store = getattr(manager_agents, "store", None)
                svc = get_user_memory_service(kv_store)
                row = await svc.create(user_id, memory_text, source=source)
                yield AgentMessage(
                    type=MessageType.CHAT,
                    sender=SenderType.ASSISTANT,
                    session_id=session_id,
                    content={"text": f"已记住：{row.get('text', memory_text)}"},
                    save=True,
                ).to_frontend_dict()
                return

        await SessionReadinessService.ensure_warmup_started(
            session_id,
            user_id,
            user_token,
            payload,
            manager_agents,
        )

    readiness = await _check_session_readiness(action, session_id, user_id, payload)
    if readiness:
        if readiness.get("type") == MessageType.MESSAGE_QUEUED.value:
            yield AgentMessage(
                type=MessageType.MESSAGE_QUEUED,
                sender=SenderType.SYSTEM,
                session_id=session_id,
                content={
                    "position": readiness.get("position"),
                    "status": readiness.get("status"),
                },
                save=False,
            ).to_frontend_dict()
            if wait_on_queue:
                ready = await SessionReadinessService.wait_until_ready(session_id)
                if not ready:
                    yield AgentMessage.error(
                        session_id=session_id,
                        content="会话预热超时或失败，请稍后重试",
                    ).to_frontend_dict()
                    return
                from db import deep_storage

                await deep_storage.delete_pending_messages(session_id)
            else:
                return
        else:
            yield AgentMessage.error(
                session_id=session_id,
                content=readiness.get("error", "会话未就绪"),
            ).to_frontend_dict()
            return

    # 提取 capabilities
    mcp_list, selected_skill_ids = _extract_capabilities(payload)
    agent_id = payload.get("agent_id")
    continue_on_disconnect = payload.get("continue_on_disconnect", True)
    
    if action == ActionType.ASK or action == ActionType.ASK.value:
        # ==================== 处理普通提问 ====================
        question = payload.get("question", "")
        attachments = payload.get("attachments", [])  # 多模态扩展
        
        input_data = {
            "type": ActionType.ASK.value,
            "content": question,
        }
        
        # 如果有附件，添加到 input_data
        if attachments:
            input_data["attachments"] = attachments
        
        # 消费 Agent 流
        async for message in manager_agents.get_agent_stream(
            user_id=user_id,
            user_token=user_token,
            session_id=session_id,
            input_data=input_data,
            mcp_list=mcp_list,
            agent_id=agent_id,
            continue_on_disconnect=continue_on_disconnect,
            selected_skill_ids=selected_skill_ids,
        ):
            yield message
    
    elif action == ActionType.RESUME_HITL or action == ActionType.RESUME_HITL.value:
        request_id = payload.get("request_id")
        user_decisions = payload.get("decisions", [])

        kv_store = getattr(manager_agents, "store", None)
        pending = await get_interrupt(
            kv_store,
            request_id,
            user_id=user_id,
            session_id=session_id,
        )
        if pending:
            from datetime import datetime, timezone
            expires_at = pending.get("expires_at")
            if expires_at:
                try:
                    exp = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) >= exp:
                        yield AgentMessage.error(
                            session_id=session_id,
                            content="审批已超时，本次执行已终止。",
                        ).to_frontend_dict()
                        return
                except (TypeError, ValueError):
                    pass

        info = await pop_interrupt(
            kv_store,
            request_id,
            user_id=user_id,
            session_id=session_id,
        )

        if not info:
            yield AgentMessage.error(
                session_id=session_id,
                content="错误：中断请求无效、已处理或已过期。",
            ).to_frontend_dict()
            return

        if len(user_decisions) != info["num_pending"]:
            yield AgentMessage.error(
                session_id=session_id,
                content="决策数量不匹配",
            ).to_frontend_dict()
            return

        history_decisions = await _persist_hitl_resume_to_history(
            session_id, request_id, info, user_decisions
        )

        decisions = build_hitl_decisions(user_decisions)
        resume_cmd = Command(resume={"decisions": decisions})

        async for message in manager_agents.get_agent_stream(
            user_id=user_id,
            user_token=user_token,
            session_id=session_id,
            input_data={
                "command": resume_cmd,
                "resume_context": {
                    "run_id": info.get("run_id"),
                    "conversation_id": info.get("conversation_id"),
                    "step_index": info.get("step_index"),
                    "request_id": request_id,
                    "actions": info.get("actions") or [],
                    "history_decisions": history_decisions,
                },
            },
            mcp_list=mcp_list,
            agent_id=agent_id,
            selected_skill_ids=selected_skill_ids,
        ):
            yield message
    
    elif action == ActionType.STOP or action == ActionType.STOP.value:
        # ==================== 处理停止请求 ====================
        # 不向前端推送 "已停止生成"：该消息未经 adapter._enrich，没有 run_id/
        # conversation_id 标识；而 stop 走 WS 是独立 asyncio 任务异步处理，若它
        # 在新一轮会话开始后才到达，前端无法判别归属，会把新一轮的流式状态误置为
        # "已停止生成"并提前 FINISH_STREAMING。
        # 发起 stop 的客户端已在 handleStop 本地展示该反馈，其他客户端可通过被停止
        # 轮次的 end(reason=stop) 得知。
        manager_agents.stop_task(session_id, user_id=user_id)
    
    else:
        # ==================== 未知 action ====================
        yield AgentMessage.error(
            session_id=session_id, 
            content=f"未知的 action 类型: {action}"
        ).to_frontend_dict()


async def process_agent_result(
    manager_agents,
    action: str,
    user_id: str,
    user_token: str,
    session_id: str,
    payload: Dict[str, Any],
    *,
    allow_if_missing: bool = False,
) -> Dict[str, Any]:
    """
    非流式版 process_agent_stream：运行 Agent 并返回结构化结果。

    与 process_agent_stream 的 action 分发逻辑完全一致，
    但调用 manager_agents.get_agent_result() 而非 get_agent_stream()。
    """
    if action != ActionType.STOP and action != ActionType.STOP.value:
        if not await validate_session_access(
            user_id, session_id, allow_if_missing=allow_if_missing
        ):
            return {
                "messages": [],
                "final_answer": "",
                "reasoning": "",
                "tool_calls": [],
                "tool_results": [],
                "status": "error",
                "error": SESSION_ACCESS_DENIED_MSG,
                "interrupt": {},
            }

    readiness = await _check_session_readiness(action, session_id, user_id, payload)
    if readiness:
        return {
            "messages": [],
            "final_answer": "",
            "reasoning": "",
            "tool_calls": [],
            "tool_results": [],
            "status": "error" if readiness.get("type") == MessageType.ERROR.value else "queued",
            "error": readiness.get("error", ""),
            "interrupt": {},
            "queued": readiness.get("type") == MessageType.MESSAGE_QUEUED.value,
            "queue_position": readiness.get("position"),
        }

    capabilities = payload.get("capabilities", {})
    mcp_list, selected_skill_ids = _extract_capabilities(payload)
    agent_id = payload.get("agent_id")

    if action == ActionType.ASK or action == ActionType.ASK.value:
        question = payload.get("question", "")
        attachments = payload.get("attachments", [])

        input_data = {
            "type": ActionType.ASK.value,
            "content": question,
        }
        if attachments:
            input_data["attachments"] = attachments

        return await manager_agents.get_agent_result(
            user_id=user_id,
            user_token=user_token,
            session_id=session_id,
            input_data=input_data,
            mcp_list=mcp_list,
            agent_id=agent_id,
            selected_skill_ids=selected_skill_ids,
        )

    elif action == ActionType.RESUME_HITL or action == ActionType.RESUME_HITL.value:
        request_id = payload.get("request_id")
        user_decisions = payload.get("decisions", [])

        kv_store = getattr(manager_agents, "store", None)
        pending = await get_interrupt(
            kv_store,
            request_id,
            user_id=user_id,
            session_id=session_id,
        )
        if pending:
            from datetime import datetime, timezone
            expires_at = pending.get("expires_at")
            if expires_at:
                try:
                    exp = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) >= exp:
                        return {
                            "messages": [],
                            "final_answer": "",
                            "reasoning": "",
                            "tool_calls": [],
                            "tool_results": [],
                            "status": "error",
                            "error": "审批已超时，本次执行已终止。",
                            "interrupt": {},
                        }
                except (TypeError, ValueError):
                    pass

        info = await pop_interrupt(
            kv_store,
            request_id,
            user_id=user_id,
            session_id=session_id,
        )
        if not info:
            return {
                "messages": [],
                "final_answer": "",
                "reasoning": "",
                "tool_calls": [],
                "tool_results": [],
                "status": "error",
                "error": "错误：中断请求无效、已处理或已过期。",
                "interrupt": {},
            }

        if len(user_decisions) != info["num_pending"]:
            return {
                "messages": [],
                "final_answer": "",
                "reasoning": "",
                "tool_calls": [],
                "tool_results": [],
                "status": "error",
                "error": "决策数量不匹配",
                "interrupt": {},
            }

        history_decisions = await _persist_hitl_resume_to_history(
            session_id, request_id, info, user_decisions
        )

        decisions = build_hitl_decisions(user_decisions)
        resume_cmd = Command(resume={"decisions": decisions})

        return await manager_agents.get_agent_result(
            user_id=user_id,
            user_token=user_token,
            session_id=session_id,
            input_data={
                "command": resume_cmd,
                "resume_context": {
                    "run_id": info.get("run_id"),
                    "conversation_id": info.get("conversation_id"),
                    "step_index": info.get("step_index"),
                    "request_id": request_id,
                    "actions": info.get("actions") or [],
                    "history_decisions": history_decisions,
                },
            },
            mcp_list=mcp_list,
            agent_id=agent_id,
            selected_skill_ids=selected_skill_ids,
        )

    elif action == ActionType.STOP or action == ActionType.STOP.value:
        manager_agents.stop_task(session_id, user_id=user_id)
        return {
            "messages": [],
            "final_answer": "",
            "reasoning": "",
            "tool_calls": [],
            "tool_results": [],
            "status": "stopped",
            "error": "",
            "interrupt": {},
            "agent_mode": "deep",
            "run_id": None,
            "subagent_runs": [],
            "todos": [],
            "token_usage": {},
        }

    else:
        return {
            "messages": [],
            "final_answer": "",
            "reasoning": "",
            "tool_calls": [],
            "tool_results": [],
            "status": "error",
            "error": f"未知的 action 类型: {action}",
            "interrupt": {},
        }


@router.post("/chat")
async def stream_search_results(request: Request, params: Any = Body(None)):
    """
    Agent 执行接口，默认非流式返回 JSON，传 stream: true 则走 SSE 流式。

    请求体示例：

    1. 非流式 ask（默认）：
    {
        "action": "ask",
        "user_id": "user_123",
        "user_token": "token_xxx",
        "session_id": "session_abc",
        "question": "帮我查询id为test-001的单据",
        "stream": false
    }

    2. 流式 ask：
    {
        "action": "ask",
        "user_id": "user_123",
        "session_id": "session_abc",
        "question": "...",
        "stream": true
    }

    3. resume_hitl / stop 同样支持 stream 参数。

    非流式返回（JSON）：
    {
        "session_id": "uuid",
        "is_new_session": true,
        "session_title": "...",
        "result": {
            "status": "completed|stopped|error|interrupted",
            "final_answer": "...",
            "reasoning": "...",
            "tool_calls": [...],
            "tool_results": [...],
            "messages": [...],
            "error": "",
            "interrupt": {}
        }
    }
    """
    manager_agents = request.app.state.manager_agents

    if not params:
        params = {}

    action = params.get("action", ActionType.ASK.value)
    user_id = params.get("user_id", "")
    user_token = params.get("user_token", "")
    client_session_id = params.get("session_id")
    question = params.get("question", "")
    stream_mode = params.get("stream", False)
    agent_id = (params.get("agent_id") or "").strip() or None

    # ==================== 参数校验 ====================
    if not str(user_id).strip():
        return JSONResponse(status_code=400, content={"error": "缺少必填参数: user_id"})
    if not str(user_token).strip():
        return JSONResponse(status_code=400, content={"error": "缺少必填参数: user_token"})
    if not str(action).strip():
        return JSONResponse(status_code=400, content={"error": "缺少必填参数: action"})
    if not str(question).strip():
        return JSONResponse(status_code=400, content={"error": "缺少必填参数: question"})

    # ==================== 会话生命周期（与 WebSocket 一致） ====================
    resolved_agent_kind = "default"
    if client_session_id:
        session_id, is_new_session = await get_or_create_session(client_session_id)
        # 预创建订阅会话后对话：session_id 与 agent_id 同时传入时
        # 以 agent_id 为准标记为订阅会话，避免 setup_new_session 把
        # agent_kind 覆盖回 "default"
        if agent_id:
            resolved_agent_kind = "subscription"
    elif agent_id:
        from db import deep_storage as _ds

        result = await _ds.ensure_subscription_session(user_id, agent_id)
        session_id = result["session_id"]
        is_new_session = result.get("is_new", False)
        resolved_agent_kind = "subscription"
    else:
        session_id, is_new_session = await get_or_create_session(None)

    session_title = ""
    resolved_agent_id = None

    # 首条消息判定：新会话 OR 之前预创建但未发过消息的空会话
    is_first_message = is_new_session
    if not is_first_message:
        from db.storage import count_total_by_session
        from uuid import UUID as _UUID
        try:
            cnt = await count_total_by_session(_UUID(session_id))
            is_first_message = (cnt == 0)
            logger.info(
                f"[Session] session={session_id} is_new=False, "
                f"chat_history_count={cnt}, is_first_message={is_first_message}"
            )
        except Exception:
            pass

    if is_first_message:
        try:
            # 立即使用兜底标题，不阻塞等待 LLM
            from utils.session_utils import _build_fallback_session_title
            session_title = _build_fallback_session_title(session_id)
            resolved_agent_id = await setup_new_session(
                user_id=user_id,
                session_id=session_id,
                session_title=session_title,
                agent_id=agent_id,
                agent_kind=resolved_agent_kind,
            )
            capabilities = params.get("capabilities", {})
            mcp_list = capabilities.get("mcp", [])
            SessionReadinessService.start_warmup(
                session_id,
                user_id,
                user_token,
                resolved_agent_id,
                mcp_list,
                manager_agents,
            )
            if not await SessionReadinessService.wait_until_ready(session_id):
                return JSONResponse(
                    status_code=503,
                    content={
                        "session_id": session_id,
                        "error": "会话预热超时或失败，请稍后重试",
                    },
                )
        except Exception as e:
            return JSONResponse(
                status_code=500,
                content={
                    "session_id": session_id,
                    "error": f"会话创建失败: {str(e)}",
                },
            )

    # 取消该 session 的旧任务（避免并发冲突）
    manager_agents.stop_task(session_id, user_id=user_id)

    # ==================== 流式路径 ====================
    if stream_mode:
        async def event_generator():
            """SSE 事件生成器。

            agent 流在独立的 producer task 中消费，本生成器只负责从队列取帧下发。
            这样做的目的：
            1) agent 长时间无输出（大上下文 LLM 思考、长工具调用）时，仍能按
               固定间隔下发注释帧心跳，避免 nginx proxy_read_timeout（默认 60s）
               或调用方 httpx read timeout 掐断连接；
            2) 客户端断开时对 producer 走显式 task.cancel()（producer 不在
               Starlette 的 anyio cancel scope 内），runner 的优雅收尾
               （落库 stopped / end / 释放沙箱）仍能执行。
            """
            queue: asyncio.Queue = asyncio.Queue(maxsize=256)

            async def _produce():
                try:
                    # 新会话先发送 session_allocated 事件
                    if is_new_session:
                        allocated_msg = AgentMessage.session_allocated(
                            session_id=session_id,
                            content={
                                "session_id": session_id,
                                "session_title": session_title,
                                "agent_id": resolved_agent_id,
                            }
                        ).to_frontend_dict()
                        await queue.put(f"data: {json.dumps(allocated_msg, ensure_ascii=False)}\n\n")

                    async for message in process_agent_stream(
                        manager_agents=manager_agents,
                        action=action,
                        user_id=user_id,
                        user_token=user_token,
                        session_id=session_id,
                        payload=params,
                        allow_if_missing=is_new_session,
                        wait_on_queue=True,
                    ):
                        json_data = json.dumps(message, ensure_ascii=False)
                        await queue.put(f"data: {json_data}\n\n")

                    # 延迟标题生成：首轮正常完成后生成并推送（新会话或之前空会话的首条消息）。
                    # get_agent_stream 会吞掉 CancelledError 正常返回，这里用 cancelling()
                    # 判断本任务是否已被取消（客户端断开），避免白跑一次标题生成 LLM 调用。
                    task = asyncio.current_task()
                    if is_first_message and not (task and task.cancelling()):
                        logger.info(
                            f"[Session] 开始延迟标题生成 session={session_id} "
                            f"is_new={is_new_session} is_first={is_first_message}"
                        )
                        from utils.session_utils import deferred_generate_and_update_title
                        new_title = await deferred_generate_and_update_title(
                            session_id=session_id,
                            user_id=user_id,
                            question=question,
                        )
                        if new_title:
                            title_msg = AgentMessage.session_title_updated(
                                session_id=session_id,
                                content={
                                    "session_id": session_id,
                                    "session_title": new_title,
                                }
                            ).to_frontend_dict()
                            await queue.put(f"data: {json.dumps(title_msg, ensure_ascii=False)}\n\n")
                        else:
                            logger.info(
                                f"[Session] 跳过标题推送（用户已自定义标题） "
                                f"session={session_id}"
                            )

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    error_msg = AgentMessage.error(
                        session_id=session_id,
                        content=str(e)
                    ).to_frontend_dict()
                    await queue.put(f"data: {json.dumps(error_msg, ensure_ascii=False)}\n\n")

                    end_msg = AgentMessage.end(
                        session_id=session_id,
                        reason=EndReason.STOP
                    ).to_frontend_dict()
                    await queue.put(f"data: {json.dumps(end_msg, ensure_ascii=False)}\n\n")
                finally:
                    # 用 put_nowait：当前任务若正处于取消状态，await 会被立即打断
                    try:
                        queue.put_nowait(None)
                    except asyncio.QueueFull:
                        pass

            producer = asyncio.create_task(
                _produce(), name=f"sse-producer:{session_id}"
            )
            heartbeat_interval = max(1, app_config.SSE_HEARTBEAT_INTERVAL)
            try:
                while True:
                    try:
                        item = await asyncio.wait_for(
                            queue.get(), timeout=heartbeat_interval
                        )
                    except asyncio.TimeoutError:
                        # SSE 注释帧：EventSource / SSE 解析器都会忽略，
                        # 但能持续刷新 nginx 与调用方的读超时计时器
                        yield ": keepalive\n\n"
                        continue
                    if item is None:
                        break
                    yield item
            finally:
                # producer 是独立 task（不在 Starlette 的 anyio cancel scope 内），
                # 显式取消即可让 runner 走正常的取消收尾。这里不 await：生成器
                # 此刻可能正处于取消传播中，await 会立即再抛；producer 会在后台
                # 完成收尾后自行结束。
                if not producer.done():
                    producer.cancel()
                # 排空队列：解除 producer 可能因队列满而卡在 queue.put 上的收尾消息
                while True:
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream; charset=utf-8",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ==================== 非流式路径（默认） ====================
    try:
        result = await asyncio.wait_for(
            process_agent_result(
                manager_agents=manager_agents,
                action=action,
                user_id=user_id,
                user_token=user_token,
                session_id=session_id,
                payload=params,
                allow_if_missing=is_new_session,
            ),
            timeout=AGENT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return JSONResponse(
            status_code=504,
            content={
                "session_id": session_id,
                "is_new_session": is_new_session,
                "session_title": session_title,
                "result": {
                    "status": "error",
                    "final_answer": "",
                    "reasoning": "",
                    "tool_calls": [],
                    "tool_results": [],
                    "messages": [],
                    "error": f"请求超时（{AGENT_TIMEOUT}秒），请简化问题或稍后重试",
                    "interrupt": {},
                },
            },
        )

    response_data = {
        "session_id": session_id,
        "is_new_session": is_new_session,
        "session_title": session_title,
        "agent_id": resolved_agent_id,
        "result": result,
    }

    # 首条消息后台延迟生成标题（非流式路径，不阻塞响应）；
    # 通过 spawn 登记任务，用户重命名时可取消生成与推送
    if is_first_message:
        from utils.session_utils import (
            deferred_generate_and_update_title,
            spawn_deferred_title_task,
        )
        spawn_deferred_title_task(
            session_id,
            deferred_generate_and_update_title(
                session_id=session_id,
                user_id=user_id,
                question=question,
            ),
        )

    return JSONResponse(content=response_data)