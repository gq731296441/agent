from fastapi.responses import StreamingResponse
from fastapi import APIRouter, Body, Request
from typing import Any, Dict, AsyncGenerator, Optional
from langgraph.types import Command
import json
import uuid

# 公共模块导入
from common.enums import MessageType, SenderType, EndReason, ActionType, DecisionType
from common.schemas import AgentMessage

router = APIRouter(prefix="/api/search", tags=["search"])

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


@router.post("/stream")
async def stream_search_results(request: Request, params: Any = Body(None)):
    """
    流式返回 Agent 执行结果（SSE 格式）
    
    支持的 action 类型：
    - ask: 普通提问
    - resume_hitl: HITL 恢复
    - stop: 停止生成
    
    请求体示例：
    
    1. ask 类型：
    {
        "action": "ask",
        "user_id": "user_123",
        "user_token": "token_xxx",
        "session_id": "session_abc",  // 可选
        "question": "帮我查询id为test-001的单据",
        "attachments": []  // 可选，用于多模态扩展
    }
    
    2. resume_hitl 类型：
    {
        "action": "resume_hitl",
        "user_id": "user_123",
        "user_token": "token_xxx",
        "session_id": "session_abc",
        "request_id": "xxx-xxx",
        "decisions": [{"type": "approve"}, ...]
    }
    
    3. stop 类型：
    {
        "action": "stop",
        "user_id": "user_123",
        "session_id": "session_abc"
    }
    
    返回：
        SSE 流，每条消息格式为：data: {"type": "...", "sender": "...", ...}\n\n
    """
    # 从 app.state 获取 manager_agents
    manager_agents = request.app.state.manager_agents
    
    # 解析参数
    if not params:
        params = {}
    
    action = params.get("action", ActionType.ASK.value)
    user_id = params.get("user_id", "guest")
    user_token = params.get("user_token", "")
    session_id = params.get("session_id")
    
    # 如果没有提供 session_id，生成新的
    if not session_id:
        session_id = str(uuid.uuid4())
    
    async def event_generator():
        """SSE 事件生成器"""
        try:
            async for message in process_agent_stream(
                manager_agents=manager_agents,
                action=action,
                user_id=user_id,
                user_token=user_token,
                session_id=session_id,
                payload=params
            ):
                json_data = json.dumps(message, ensure_ascii=False)
                yield f"data: {json_data}\n\n"
                
        except Exception as e:
            # 发送错误消息
            error_msg = AgentMessage.error(
                session_id=session_id, 
                content=str(e)
            ).to_frontend_dict()
            yield f"data: {json.dumps(error_msg, ensure_ascii=False)}\n\n"
            
            # 发送结束信号
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
            "X-Accel-Buffering": "no"
        }
    )