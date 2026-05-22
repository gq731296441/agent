from fastapi.responses import StreamingResponse, JSONResponse
from fastapi import APIRouter, Body, Request
from typing import Any, Dict, AsyncGenerator
from langgraph.types import Command
import json

from utils.session_utils import get_or_create_session, generate_session_title
from db import storage

# 公共模块导入
from common.enums import EndReason, ActionType, DecisionType
from common.schemas import AgentMessage

router = APIRouter(prefix="/agent", tags=["agent"])

async def process_agent_stream(
    manager_agents,
    action: str,
    user_id: str,
    user_token: str,
    session_id: str,
    payload: Dict[str, Any]
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
    # 提取 capabilities 中的 mcp 配置
    capabilities = payload.get("capabilities", {})
    mcp_list = capabilities.get("mcp", [])
    
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
            mcp_list=mcp_list
        ):
            yield message
    
    elif action == ActionType.RESUME_HITL or action == ActionType.RESUME_HITL.value:
        # ==================== 处理 HITL 恢复 ====================
        request_id = payload.get("request_id")
        user_decisions = payload.get("decisions", [])
        
        # 获取 Agent 实例以访问 pending_interrupts（HITL 恢复时不需要重新初始化 MCP）
        agent, init_err = await manager_agents.create_agent(user_id, user_token, session_id, mcp_list=[])
        
        if init_err:
            yield AgentMessage.error(session_id=session_id, content=init_err).to_frontend_dict()
            return
        
        info = agent.pending_interrupts.pop(request_id, None)
        
        if not info:
            yield AgentMessage.error(
                session_id=session_id, 
                content="错误：中断请求无效或已处理。"
            ).to_frontend_dict()
            return
        
        if len(user_decisions) != info["num_pending"]:
            yield AgentMessage.error(
                session_id=session_id, 
                content="决策数量不匹配"
            ).to_frontend_dict()
            return
        
        # 构造 decisions
        decisions = []
        for dec in user_decisions:
            dec_type = dec["type"]
            if dec_type == DecisionType.APPROVE or dec_type == DecisionType.APPROVE.value:
                decisions.append({"type": dec_type})
            elif dec_type == DecisionType.REJECT or dec_type == DecisionType.REJECT.value:
                decisions.append({
                    "type": dec_type,
                    "message": "用户拒绝该工具的调用，无需再次发起调用请求"
                })
            elif dec_type == DecisionType.EDIT or dec_type == DecisionType.EDIT.value:
                decisions.append({
                    "type": dec_type,
                    "edited_action": dec["edited_action"]
                })
        
        resume_cmd = Command(resume={"decisions": decisions})
        
        # 消费 Agent 流（HITL 恢复不需要重新传递 mcp_list）
        async for message in manager_agents.get_agent_stream(
            user_id=user_id,
            user_token=user_token,
            session_id=session_id,
            input_data={"command": resume_cmd},
            mcp_list=[]
        ):
            yield message
    
    elif action == ActionType.STOP or action == ActionType.STOP.value:
        # ==================== 处理停止请求 ====================
        manager_agents.stop_task(session_id)
        yield AgentMessage.status(
            session_id=session_id, 
            content="已停止生成"
        ).to_frontend_dict()
    
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
    payload: Dict[str, Any]
) -> Dict[str, Any]:
    """
    非流式版 process_agent_stream：运行 Agent 并返回结构化结果。

    与 process_agent_stream 的 action 分发逻辑完全一致，
    但调用 manager_agents.get_agent_result() 而非 get_agent_stream()。
    """
    capabilities = payload.get("capabilities", {})
    mcp_list = capabilities.get("mcp", [])

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
        )

    elif action == ActionType.RESUME_HITL or action == ActionType.RESUME_HITL.value:
        request_id = payload.get("request_id")
        user_decisions = payload.get("decisions", [])

        agent, init_err = await manager_agents.create_agent(user_id, user_token, session_id, mcp_list=[])

        if init_err:
            return {
                "messages": [],
                "final_answer": "",
                "reasoning": "",
                "tool_calls": [],
                "tool_results": [],
                "status": "error",
                "error": init_err,
                "interrupt": {},
            }

        info = agent.pending_interrupts.pop(request_id, None)
        if not info:
            return {
                "messages": [],
                "final_answer": "",
                "reasoning": "",
                "tool_calls": [],
                "tool_results": [],
                "status": "error",
                "error": "错误：中断请求无效或已处理。",
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

        decisions = []
        for dec in user_decisions:
            dec_type = dec["type"]
            if dec_type == DecisionType.APPROVE or dec_type == DecisionType.APPROVE.value:
                decisions.append({"type": dec_type})
            elif dec_type == DecisionType.REJECT or dec_type == DecisionType.REJECT.value:
                decisions.append({
                    "type": dec_type,
                    "message": "用户拒绝该工具的调用，无需再次发起调用请求"
                })
            elif dec_type == DecisionType.EDIT or dec_type == DecisionType.EDIT.value:
                decisions.append({
                    "type": dec_type,
                    "edited_action": dec["edited_action"]
                })

        resume_cmd = Command(resume={"decisions": decisions})

        return await manager_agents.get_agent_result(
            user_id=user_id,
            user_token=user_token,
            session_id=session_id,
            input_data={"command": resume_cmd},
            mcp_list=[],
        )

    elif action == ActionType.STOP or action == ActionType.STOP.value:
        manager_agents.stop_task(session_id)
        return {
            "messages": [],
            "final_answer": "",
            "reasoning": "",
            "tool_calls": [],
            "tool_results": [],
            "status": "stopped",
            "error": "",
            "interrupt": {},
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
    session_id, is_new_session = await get_or_create_session(client_session_id)

    session_title = ""
    if is_new_session:
        try:
            title_llm = getattr(request.app.state, "title_llm", None)
            session_title = await generate_session_title(title_llm, question, session_id)
            await storage.upsert_user_session(
                user_id=user_id,
                session_id=session_id,
                session_name=session_title,
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
    manager_agents.stop_task(session_id)

    # ==================== 流式路径 ====================
    if stream_mode:
        async def event_generator():
            """SSE 事件生成器"""
            try:
                # 新会话先发送 session_allocated 事件
                if is_new_session:
                    allocated_msg = AgentMessage.session_allocated(
                        session_id=session_id,
                        content={
                            "session_id": session_id,
                            "session_title": session_title,
                        }
                    ).to_frontend_dict()
                    yield f"data: {json.dumps(allocated_msg, ensure_ascii=False)}\n\n"

                async for message in process_agent_stream(
                    manager_agents=manager_agents,
                    action=action,
                    user_id=user_id,
                    user_token=user_token,
                    session_id=session_id,
                    payload=params,
                ):
                    json_data = json.dumps(message, ensure_ascii=False)
                    yield f"data: {json_data}\n\n"

            except Exception as e:
                error_msg = AgentMessage.error(
                    session_id=session_id,
                    content=str(e)
                ).to_frontend_dict()
                yield f"data: {json.dumps(error_msg, ensure_ascii=False)}\n\n"

                end_msg = AgentMessage.end(
                    session_id=session_id,
                    reason=EndReason.STOP
                ).to_frontend_dict()
                yield f"data: {json.dumps(end_msg, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ==================== 非流式路径（默认） ====================
    result = await process_agent_result(
        manager_agents=manager_agents,
        action=action,
        user_id=user_id,
        user_token=user_token,
        session_id=session_id,
        payload=params,
    )

    response_data = {
        "session_id": session_id,
        "is_new_session": is_new_session,
        "session_title": session_title,
        "result": result,
    }

    return JSONResponse(content=response_data)