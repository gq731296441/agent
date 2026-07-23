from datetime import timezone, timedelta
from fastapi import APIRouter, HTTPException, Request, Query
from typing import List, Dict, Any
from pydantic import BaseModel, Field
from db import storage, deep_storage
from uuid import UUID
from common.schemas import ApiResponse, ListApiResponse
from config import get_logger
from utils.session_utils import (
    get_or_create_session,
    validate_session_access,
    SESSION_ACCESS_DENIED_MSG,
)
from utils.subscription_guard import validate_ask_session
from deep_agent_server.workspace import remove_session_workspace
import json

logger = get_logger(__name__)

# 中国标准时间 (UTC+8)
_CST = timezone(timedelta(hours=8))


def _format_local_time(dt) -> str:
    """将数据库时间（UTC）转换为中国标准时间 (UTC+8) 并格式化为字符串。"""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local_dt = dt.astimezone(_CST)
    return local_dt.strftime("%Y-%m-%d %H:%M:%S")

router = APIRouter(prefix="/sessions", tags=["sessions"])


# ==================== 数据模型 ====================

class SessionItem(BaseModel):
    """会话项"""
    id: str = Field(..., description="会话ID")
    title: str = Field(default="", description="会话标题")
    last_active_at: str = Field(default="", description="最后对话时间")
    created_at: str = Field(default="", description="创建时间")
    updated_at: str = Field(default="", description="更新时间")
    agent_kind: str = Field(default="default", description="default | subscription")
    agent_id: str | None = Field(default=None, description="订阅智能体 ID")
    agent_name: str | None = Field(default=None, description="订阅智能体名称")
    subscription_frozen: bool = Field(default=False)
    archived: bool = Field(default=False, description="关联智能体已下架")
    subscription_status: str | None = Field(default=None, description="active | cancelled")
    agent_visibility: str | None = Field(default=None, description="public | private")
    agent_creator_user_id: str | None = Field(default=None, description="智能体创建者")
    agent_name: str | None = Field(default=None, description="订阅智能体名称")
    avatar_color: str | None = Field(default=None, description="智能体头像背景色")
    context_epoch: int = Field(default=1)


class HistoryItem(BaseModel):
    """历史消息项"""
    id: str = Field(..., description="消息ID")
    sender: str = Field(..., description="发送者")
    type: str = Field(..., description="消息类型")
    content: Any = Field(..., description="消息内容")
    tool_call_id: str|None = Field(default=None, description="工具调用ID")
    conversation_id: str|None = Field(default=None, description="会话轮次ID")
    run_id: str|None = Field(default=None, description="Agent 运行 ID")
    group_id: int = Field(default=0, description="分组ID")
    step_index: int = Field(default=0, description="步骤序号")
    create_time: str = Field(..., description="创建时间")
    context_epoch: int = Field(default=1, description="上下文 epoch")
    subagent_run_id: str | None = Field(default=None, description="子智能体运行 ID")


class SessionRunMetricsItem(BaseModel):
    """会话内单次 run 的指标（来自 agent_runs 表）"""
    run_id: str = Field(..., description="运行 ID")
    conversation_id: str | None = Field(default=None, description="对话轮次 ID")
    status: str = Field(default="", description="运行状态")
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)
    duration_ms: int = Field(default=0)
    llm_model: str = Field(default="")


# ==================== 接口实现 ====================

@router.get("/list", response_model=ListApiResponse[SessionItem])
async def get_user_sessions(
    userId: str = Query(..., description="用户ID"),
    agent_kind: str | None = Query(None, description="default | subscription"),
):
    """获取用户的有效会话列表（不包括软删除的会话）"""
    try:
        sessions = await storage.get_user_sessions(userId, agent_kind=agent_kind)
        session_items = [
            SessionItem(
                id=str(s.get("session_id", "")),
                title=s.get("session_name", ""),
                last_active_at=_format_local_time(s.get("last_active_at")),
                created_at=_format_local_time(s.get("created_at")),
                updated_at=_format_local_time(s.get("updated_at")),
                agent_kind=s.get("agent_kind") or "default",
                agent_id=str(s["agent_id"]) if s.get("agent_id") else None,
                agent_name=s.get("agent_name"),
                subscription_frozen=bool(s.get("subscription_frozen")),
                archived=bool(s.get("archived") or s.get("agent_deleted")),
                subscription_status=s.get("subscription_status"),
                agent_visibility=s.get("agent_visibility"),
                agent_creator_user_id=s.get("agent_creator_user_id"),
                avatar_color=s.get("agent_avatar_color") or s.get("avatar_color"),
                context_epoch=int(s.get("context_epoch") or 1),
            )
            for s in sessions
        ]
        return ListApiResponse.success(data=session_items, msg="成功")
    except Exception as e:
        logger.error(f"[Session API] 获取用户会话列表失败: {e}")
        return ListApiResponse.error(msg=f"获取会话列表失败: {str(e)}")


@router.get("/history", response_model=ListApiResponse[HistoryItem])
async def get_session_history(
    userId: str = Query(..., description="用户ID"),
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

    if not await validate_session_access(userId, session_id):
        return ListApiResponse.error(msg=SESSION_ACCESS_DENIED_MSG, code=403)

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
                conversation_id=str(r["conversation_id"]) if r.get("conversation_id") else None,
                run_id=str(r["run_id"]) if r.get("run_id") else None,
                group_id=r.get("group_id", 0) or 0,
                step_index=r.get("step_index", 0) or 0,
                create_time=_format_local_time(r["created_at"]),
                context_epoch=int(r.get("context_epoch") or 1),
                subagent_run_id=str(r["subagent_run_id"]) if r.get("subagent_run_id") else None,
            )
            for r in history
        ]

        return ListApiResponse.success(data=history_items, msg="成功")
    except Exception as e:
        logger.error(f"[Session API] 获取会话历史失败: {e}")
        return ListApiResponse.error(msg=f"获取会话历史失败: {str(e)}")


@router.get("/run-metrics", response_model=ListApiResponse[SessionRunMetricsItem])
async def get_session_run_metrics(
    userId: str = Query(..., description="用户ID"),
    session_id: str = Query(..., description="会话ID"),
):
    """获取会话内所有 agent run 的 token / 耗时指标（历史消息展示用）。"""
    try:
        target_uuid = UUID(session_id)
    except (ValueError, AttributeError):
        return ListApiResponse.error(msg=f"无效的会话ID格式: {session_id}")

    if not await validate_session_access(userId, session_id):
        return ListApiResponse.error(msg=SESSION_ACCESS_DENIED_MSG, code=403)

    try:
        runs = await deep_storage.list_agent_runs_for_session(session_id, userId)
        items = [
            SessionRunMetricsItem(
                run_id=r["run_id"],
                conversation_id=r.get("conversation_id"),
                status=r.get("status") or "",
                prompt_tokens=int(r.get("prompt_tokens") or 0),
                completion_tokens=int(r.get("completion_tokens") or 0),
                total_tokens=int(r.get("total_tokens") or 0),
                duration_ms=int(r.get("duration_ms") or 0),
                llm_model=r.get("llm_model") or "",
            )
            for r in runs
        ]
        return ListApiResponse.success(data=items, msg="成功")
    except Exception as e:
        logger.error(f"[Session API] 获取 run 指标失败: {e}")
        return ListApiResponse.error(msg=f"获取 run 指标失败: {str(e)}")


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
    request: Request,
    user_id: str = Query(..., description="用户ID"),
    session_id: str = Query(..., description="会话ID"),
):
    """永久删除用户会话（物理删除，无法恢复）

    同时清理：
    1. user_sessions / agent_configs 表（级联删除）
    2. chat_history 表
    3. LangGraph checkpointer / store 数据
    4. Deep 工作区、artifacts、deep_* 表记录
    """
    try:
        success = await storage.delete_user_session(user_id, session_id)
        if not success:
            logger.warning(f"[Session API] 会话不存在: user_id={user_id}, session_id={session_id}")
            return ApiResponse.error(msg="Session not found", code=404)

        await storage.delete_history_by_session(session_id)

        thread_id = f"{user_id}#{session_id}"
        await _cleanup_langgraph_data(request, thread_id, user_id=user_id, session_id=session_id)

        remove_session_workspace(user_id, session_id)
        await deep_storage.cleanup_deep_session_data(user_id, session_id)

        return ApiResponse.success(msg="Session permanently deleted")
    except Exception as e:
        logger.error(f"[Session API] 永久删除会话失败: {e}")
        return ApiResponse.error(msg=f"永久删除会话失败: {str(e)}")


@router.post("/subscription/{agent_id}/ensure", response_model=ApiResponse)
async def ensure_subscription_session(
    agent_id: str,
    user_id: str = Query(..., description="用户ID"),
):
    """获取或创建订阅智能体的唯一会话。"""
    try:
        data = await deep_storage.ensure_subscription_session(user_id, agent_id)
        return ApiResponse.success(data=data)
    except ValueError as e:
        return ApiResponse.error(msg=str(e), code=400)
    except Exception as e:
        logger.error(f"[Session API] ensure subscription session failed: {e}")
        return ApiResponse.error(msg=f"创建会话失败: {e}", code=500)


@router.post("/ensure", response_model=ApiResponse)
async def ensure_session(user_id: str = Query(..., description="用户ID")):
    """进入聊天页时调用：获取或复用空会话。

    优先复用已有空会话（无聊天记录），文件不丢失。
    若无空会话则创建并写入数据库，后续调用可正确复用。
    """
    try:
        existing = await storage.find_empty_session(user_id)
        if existing:
            return ApiResponse.success(
                data={"session_id": existing, "is_new": False},
                msg="复用已有会话",
            )

        # 创建新会话并持久化到 user_sessions 表（否则 find_empty_session 查不到）
        session_id, _ = await get_or_create_session(None)
        session_title = f"新会话 {session_id[-8:]}"
        await storage.upsert_user_session(
            user_id=user_id,
            session_id=session_id,
            session_name=session_title,
            agent_id=None,
            agent_kind="default",
        )
        return ApiResponse.success(
            data={"session_id": session_id, "is_new": True},
            msg="已创建新会话",
        )
    except Exception as e:
        logger.error(f"[Session API] ensure session failed: {e}")
        return ApiResponse.error(msg=f"创建会话失败: {e}", code=500)


@router.post("/create", response_model=ApiResponse)
async def create_session(user_id: str = Query(..., description="用户ID")):
    """用户点击"新建会话"时调用：始终创建全新会话，不复用。"""
    try:
        session_id, _ = await get_or_create_session(None)
        session_title = f"新会话 {session_id[-8:]}"
        await storage.upsert_user_session(
            user_id=user_id,
            session_id=session_id,
            session_name=session_title,
            agent_id=None,
            agent_kind="default",
        )
        return ApiResponse.success(
            data={"session_id": session_id, "is_new": True},
            msg="已创建新会话",
        )
    except Exception as e:
        logger.error(f"[Session API] create session failed: {e}")
        return ApiResponse.error(msg=f"创建会话失败: {e}", code=500)


@router.get("/{session_id}/validate-send", response_model=ApiResponse)
async def validate_can_send(
    session_id: str,
    user_id: str = Query(..., description="用户ID"),
):
    """发消息前校验会话是否仍可发送（智能体未删除、订阅未取消等）。"""
    ok, error_msg = await validate_ask_session(user_id, session_id, None)
    return ApiResponse.success(data={"can_send": ok, "error": error_msg})


def _clear_agent_cache_for_session(manager, session_id: str) -> None:
    if not manager or not getattr(manager, "_agent_cache", None):
        return
    prefix = f"deep:{session_id}:"
    keys = [k for k in manager._agent_cache if k.startswith(prefix)]
    for k in keys:
        manager._agent_cache.pop(k, None)


@router.post("/{session_id}/new-context", response_model=ApiResponse)
async def new_context(
    request: Request,
    session_id: str,
    user_id: str = Query(..., description="用户ID"),
    expected_epoch: int = Query(..., description="当前 context_epoch，乐观锁"),
):
    """订阅智能体：清空 LangGraph 上下文，保留历史并插入分割线。"""
    if not await validate_session_access(user_id, session_id):
        return ApiResponse.error(msg=SESSION_ACCESS_DENIED_MSG, code=403)

    meta = await storage.get_session_meta(session_id)
    if not meta:
        return ApiResponse.error(msg="Session not found", code=404)
    if meta.get("agent_kind") != "subscription":
        return ApiResponse.error(msg="仅订阅智能体会话支持开始新会话", code=400)
    if meta.get("subscription_frozen"):
        return ApiResponse.error(msg="会话已冻结，无法开始新会话", code=403)

    new_epoch = await storage.increment_context_epoch(session_id, expected_epoch)
    if new_epoch is None:
        return ApiResponse.error(msg="context_epoch 冲突，请刷新后重试", code=409)

    thread_id = f"{user_id}#{session_id}"
    await _cleanup_langgraph_data(request, thread_id, user_id=user_id, session_id=session_id)

    manager = getattr(request.app.state, "manager_agents", None)
    _clear_agent_cache_for_session(manager, session_id)

    await storage.insert_context_divider(session_id, user_id, new_epoch)

    return ApiResponse.success(data={
        "session_id": session_id,
        "context_epoch": new_epoch,
    })


@router.get("/readiness", response_model=ApiResponse)
async def get_session_readiness(
    session_id: str = Query(..., description="会话ID"),
    userId: str = Query(..., description="用户ID"),
):
    """查询会话预热/就绪状态。"""
    if not await validate_session_access(userId, session_id):
        return ApiResponse.error(msg=SESSION_ACCESS_DENIED_MSG, code=403)
    from deep_agent_server.session.readiness import SessionReadinessService
    status = await SessionReadinessService.get_status(session_id)
    pending = await deep_storage.count_pending_messages(session_id)
    row = await deep_storage.get_session_start_task(session_id)
    return ApiResponse.success(data={
        "session_id": session_id,
        "status": status,
        "pending_count": pending,
        "error": (row or {}).get("error"),
    })


async def _cleanup_langgraph_data(
    request: Request,
    thread_id: str,
    *,
    user_id: str = None,
    session_id: str = None,
) -> None:
    """清理 LangGraph checkpointer 和 store 中与 thread_id 关联的数据。"""
    checkpointer = getattr(request.app.state, "checkpointer", None)
    if checkpointer:
        try:
            await checkpointer.adelete_thread(thread_id)
            logger.info(f"[Session API] ✅ 已清理 checkpointer: {thread_id}")
        except Exception as e:
            logger.error(f"[Session API] 清理 checkpointer 失败: {e}")

    kv_store = getattr(request.app.state, "kv_store", None)
    if kv_store:
        from deep_agent_server.store import cleanup_session_store
        from db import deep_storage

        await cleanup_session_store(
            kv_store,
            thread_id=thread_id,
            user_id=user_id,
            session_id=session_id,
        )
        if user_id and session_id:
            try:
                await deep_storage.delete_artifacts_by_session(user_id, session_id)
            except Exception as e:
                logger.warning(f"[Session API] 清理 legacy artifacts 失败: {e}")
        logger.info(f"[Session API] ✅ 已清理 store: {thread_id}")


# ==================== 上下文大小查询 ====================

def _find_compiled_for_session(manager_agents, session_id: str):
    """从 AgentManager 缓存中查找 session 对应的编译图。

    缓存 key 格式: deep:{session_id}:{agent_key}:cfg{version}:llm{fp}:...
    """
    cache = getattr(manager_agents, "_agent_cache", {})
    prefix = f"deep:{session_id}:"
    for key, value in cache.items():
        if key.startswith(prefix):
            wrapper = value[0] if isinstance(value, tuple) else value
            if hasattr(wrapper, "agent"):
                return wrapper.agent
    return None


class ContextUsageData(BaseModel):
    """上下文使用情况数据"""
    token_count: int = Field(..., description="当前已使用的 token 数")
    max_input_tokens: int = Field(..., description="模型最大输入 token 数")
    percentage: float = Field(..., description="使用百分比 (0.0-100.0)")
    message_count: int = Field(..., description="消息总数")
    warning_level: str = Field(..., description="预警级别: normal | warning | critical")
    model_name: str = Field(default="", description="模型名称")
    session_id: str = Field(default="", description="会话 ID")


@router.get("/context-usage", response_model=ApiResponse[ContextUsageData])
async def get_session_context_usage(
    request: Request,
    session_id: str = Query(..., description="会话ID"),
    userId: str = Query(..., description="用户ID"),
    model_name: str = Query(default="", description="模型名称（可选，用于推断 max_input_tokens）"),
    max_input_tokens: int = Query(default=None, description="手动指定最大输入 token 数（可选）"),
):
    """查询当前会话的上下文使用情况（token 数 / 百分比 / 预警级别）。"""
    if not await validate_session_access(userId, session_id):
        return ApiResponse.error(msg=SESSION_ACCESS_DENIED_MSG, code=403)

    manager_agents = getattr(request.app.state, "manager_agents", None)
    if manager_agents is None:
        return ApiResponse.error(msg="AgentManager 不可用", code=500)

    # 尝试从缓存获取已编译的 Agent 图（AGENT_CACHE_ENABLED=True 时命中）
    compiled = _find_compiled_for_session(manager_agents, session_id)

    # 解析模型名（供 token 计算和 max_input_tokens 推断）
    resolved_model_name = model_name
    if not resolved_model_name:
        try:
            from db import deep_storage
            resolved_model_name = (
                await deep_storage.get_user_secret(userId, "llm_model") or ""
            )
        except Exception:
            resolved_model_name = ""

    # 构建系统提示词
    system_prompt_text = ""
    try:
        from db import storage as chat_storage
        from agent_server.prompts import build_base_system_prompt, build_system_prompt

        meta = await chat_storage.get_session_meta(session_id)
        agent_kind = (meta or {}).get("agent_kind") or "default"

        if agent_kind == "subscription":
            system_prompt_text = build_base_system_prompt(user_id=userId, user_token="")
        else:
            system_prompt_text = build_system_prompt(user_id=userId, user_token="")
    except Exception as e:
        logger.warning(f"[Session API] 无法构建系统提示词，跳过计数: {e}")

    try:
        if compiled is not None:
            # 缓存命中：用编译图内置的 token_counter 精确计数
            from utils.context_size import get_session_context_size
            result = await get_session_context_size(
                compiled=compiled,
                user_id=userId,
                session_id=session_id,
                max_input_tokens=max_input_tokens,
                model_name=resolved_model_name,
                system_prompt_text=system_prompt_text,
            )
        else:
            # 缓存未命中（极少见，仅当 AGENT_CACHE_ENABLED=False 时）：
            # 不做完整 Agent 创建，返回快速估算，避免 MCP HTTP + Agent 编译
            from utils.context_size import ContextSizeResult
            limit = max_input_tokens or 262144
            result = ContextSizeResult(
                token_count=0, max_input_tokens=limit, percentage=0.0,
                message_count=0, warning_level="normal", model_name=resolved_model_name,
            )

        return ApiResponse.success(data=ContextUsageData(
            token_count=result.token_count,
            max_input_tokens=result.max_input_tokens,
            percentage=result.percentage,
            message_count=result.message_count,
            warning_level=result.warning_level,
            model_name=result.model_name,
            session_id=session_id,
        ))
    except ValueError as e:
        return ApiResponse.error(msg=str(e), code=404)
    except Exception as e:
        logger.error(f"[Session API] 获取上下文使用情况失败: {e}")
        return ApiResponse.error(msg=f"获取失败: {str(e)}", code=500)
