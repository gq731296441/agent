from fastapi import APIRouter, HTTPException, Request, Query
from typing import List, Dict, Any
from pydantic import BaseModel, Field
from db import storage
from uuid import UUID
from common.schemas import ApiResponse, ListApiResponse
from config import get_logger
import json

logger = get_logger(__name__)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


# ==================== 数据模型 ====================

class SessionItem(BaseModel):
    """会话项"""
    id: str = Field(..., description="会话ID")
    title: str = Field(default="", description="会话标题")
    last_active_at: str = Field(default="", description="最后对话时间")
    created_at: str = Field(default="", description="创建时间")
    updated_at: str = Field(default="", description="更新时间")


class HistoryItem(BaseModel):
    """历史消息项"""
    id: str = Field(..., description="消息ID")
    sender: str = Field(..., description="发送者")
    type: str = Field(..., description="消息类型")
    content: Any = Field(..., description="消息内容")
    tool_call_id: str|None = Field(default=None, description="工具调用ID")
    create_time: str = Field(..., description="创建时间")


class CheckpointerHistoryItem(BaseModel):
    """Checkpointer 历史消息项"""
    type: str = Field(..., description="消息类型")
    sender: str = Field(..., description="发送者")
    content: Any = Field(..., description="消息内容")


# ==================== 接口实现 ====================

@router.get("/list", response_model=ListApiResponse[SessionItem])
async def get_user_sessions(
    user_id: str = Query(..., description="用户ID"),
):
    """获取用户的有效会话列表（不包括软删除的会话）"""
    try:
        sessions = await storage.get_user_sessions(user_id)
        # 转换为 SessionItem 列表
        session_items = [
            SessionItem(
                id=str(s.get("id", "")),
                title=s.get("title", ""),
                last_active_at=s.get("last_active_at", "").strftime("%Y-%m-%d %H:%M:%S") if s.get("last_active_at") else "",
                created_at=s.get("created_at", "").strftime("%Y-%m-%d %H:%M:%S") if s.get("created_at") else "",
                updated_at=s.get("updated_at", "").strftime("%Y-%m-%d %H:%M:%S") if s.get("updated_at") else ""
            )
            for s in sessions
        ]
        return ListApiResponse.success(data=session_items, msg="成功")
    except Exception as e:
        logger.error(f"[Session API] 获取用户会话列表失败: {e}")
        return ListApiResponse.error(msg=f"获取会话列表失败: {str(e)}")


@router.get("/checkpointer/history", response_model=ListApiResponse[CheckpointerHistoryItem])
async def get_session_checkpointer_history(
    request: Request,
    user_id: str = Query(..., description="用户ID"),
    session_id: str = Query(..., description="会话ID")
):
    """使用checkpointer获取会话的历史消息"""
    try:
        # 从 app.state 获取 checkpointer
        checkpointer = request.app.state.checkpointer
        if not checkpointer:
            logger.warning("[Session API] Checkpointer 未初始化")
            return ListApiResponse.error(msg="Checkpointer not initialized")
        
        thread_id = f"{user_id}#{session_id}"
        config = {"configurable": {"thread_id": thread_id}}
        checkpoint_tuple = await checkpointer.aget_tuple(config)
        if checkpoint_tuple is None:
            logger.warning(f"[Session API] 会话不存在: {session_id}")
            return ListApiResponse.error(msg="Session not found")
        
        # checkpoint_tuple.checkpoint["channel_values"]["messages"] 就是历史消息
        messages = checkpoint_tuple.checkpoint["channel_values"].get("messages", [])

        history_items = [
            CheckpointerHistoryItem(
                type="chat",
                sender="assistant" if m.type == "ai" else ("user" if m.type == "human" else m.type),
                content=m.content
            )
            for m in messages
        ]
        
        return ListApiResponse.success(data=history_items, msg="成功")

    except Exception as e:
        logger.error(f"[Session API] 获取 checkpointer 历史失败: {e}")
        return ListApiResponse.error(msg=f"获取历史失败: {str(e)}")

@router.get("/history", response_model=ListApiResponse[HistoryItem])
async def get_session_history(
    session_id: str = Query(..., description="会话ID"),
    limit: int = 50,
    page: int = 1
):
    """获取会话历史消息"""
    try:
        target_uuid = UUID(session_id)
    except (ValueError, AttributeError) as e:
        logger.error(f"[Session API] 无效的会话ID格式: {session_id}")
        return ListApiResponse.error(msg=f"无效的会话ID格式: {session_id}")
    
    try:
        offset = (page - 1) * limit
        history = await storage.fetch_history_by_session(target_uuid, limit, offset)

        history_items = [
            HistoryItem(
                id=str(r["id"]),
                sender=r["sender"],
                type=r["type"],
                content=json.loads(r["content"]) if isinstance(r["content"], str) else r["content"],
                tool_call_id=r["tool_call_id"],
                create_time=r["created_at"].strftime("%Y-%m-%d %H:%M:%S")
            )
            for r in history
        ]
        
        return ListApiResponse.success(data=history_items, msg="成功")
    except Exception as e:
        logger.error(f"[Session API] 获取会话历史失败: {e}")
        return ListApiResponse.error(msg=f"获取会话历史失败: {str(e)}")

    
@router.delete("/delete", response_model=ApiResponse)
async def soft_delete_session(
    user_id: str = Query(..., description="用户ID"),
    session_id: str = Query(..., description="会话ID"),
):
    """软删除用户会话（标记为已删除，保留数据以便恢复）"""
    try:
        success = await storage.soft_delete_user_session(user_id, session_id)
        if not success:
            logger.warning(f"[Session API] 会话不存在: user_id={user_id}, session_id={session_id}")
            return ApiResponse.error(msg="Session not found", code=404)
        return ApiResponse.success(msg="Session marked as deleted")
    except Exception as e:
        logger.error(f"[Session API] 软删除会话失败: {e}")
        return ApiResponse.error(msg=f"软删除会话失败: {str(e)}")


@router.delete("/delete/permanent", response_model=ApiResponse)
async def permanent_delete_session(
    user_id: str = Query(..., description="用户ID"),
    session_id: str = Query(..., description="会话ID"),
):
    """永久删除用户会话（物理删除，无法恢复）"""
    try:
        success = await storage.delete_user_session(user_id, session_id)
        if not success:
            logger.warning(f"[Session API] 会话不存在: user_id={user_id}, session_id={session_id}")
            return ApiResponse.error(msg="Session not found", code=404)
        return ApiResponse.success(msg="Session permanently deleted")
    except Exception as e:
        logger.error(f"[Session API] 永久删除会话失败: {e}")
        return ApiResponse.error(msg=f"永久删除会话失败: {str(e)}")