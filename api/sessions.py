from fastapi import APIRouter, HTTPException, Request, Query
from typing import List, Dict, Any
from agent_server import storage

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

@router.post("/add/{user_id}/{session_id}")
async def create_or_update_session(
    user_id: str,
    session_id: str,
    payload: Dict[str, Any]
):
    """创建或更新用户会话"""
    session_name = payload.get('session_name', '新会话')
    try:
        await storage.upsert_user_session(user_id, session_id, session_name)
        return {'status': 'ok', 'session_id': session_id, 'session_name': session_name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create/update session: {str(e)}")

@router.get("/list/{user_id}")
async def get_user_sessions(user_id: str) -> Dict[str, List[Dict[str, Any]]]:
    """获取用户的会话列表"""
    try:
        sessions = await storage.get_user_sessions(user_id)
        return {'sessions': sessions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get sessions: {str(e)}")

@router.get("/history")
async def get_session_history(
    request: Request,
    user_id: str = Query(..., description="用户ID"),      # 2. 使用 Query 明确参数来源
    session_id: str = Query(..., description="会话ID")    # 3. "..." 表示该参数必填
) -> Dict[str, List[Dict[str, Any]]]:
    """获取会话的历史消息"""
    try:
        # 从 app.state 获取 checkpointer
        checkpointer = request.app.state.checkpointer
        if not checkpointer:
            return {'error': 'Checkpointer not initialized', 'messages': []}
        
        thread_id = f"{user_id}#{session_id}"
        config = {"configurable": {"thread_id": thread_id}}
        checkpoint_tuple = await checkpointer.aget_tuple(config)
        if checkpoint_tuple is None:
            return {'messages': []}
        # checkpoint_tuple.checkpoint["channel_values"]["messages"] 就是历史消息
        messages = checkpoint_tuple.checkpoint["channel_values"].get("messages", [])

        return {'messages': [{"role": m.type, "content": m.content} for m in messages]}

        # messages = []
        # if state and state.values:
        #     for msg in state.values.get('messages', []):
        #         messages.append({
        #             'role': msg.type,
        #             'content': msg.content,
        #             'timestamp': str(msg.additional_kwargs.get('timestamp', '')) if hasattr(msg, 'additional_kwargs') else ''
        #         })
        # return {'messages': messages}
    except Exception as e:
        return {'error': str(e), 'messages': []}

@router.delete("/delete/{user_id}/{session_id}")
async def delete_session(user_id: str, session_id: str):
    """删除用户会话"""
    try:
        # 这里需要实现删除会话的逻辑，可能需要扩展 storage.py
        # 暂时返回未实现
        raise HTTPException(status_code=501, detail="Delete session not implemented yet")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete session: {str(e)}")