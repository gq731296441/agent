"""
运营数据统计 API

提供用户总数、日活用户、会话数、消息数、工具调用次数、
任务成功/中断/失败次数等运营指标的查询接口。
"""

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from db import storage
from db import deep_storage
from common.schemas import ApiResponse
from config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/stats", tags=["stats"])


# ==================== 数据模型 ====================

class OverviewData(BaseModel):
    """运营概览数据"""
    total_users: int = Field(default=0, description="用户总数")
    active_users: int = Field(default=0, description="活跃用户数（所选时间范围内）")
    total_sessions: int = Field(default=0, description="会话总数")
    total_messages: int = Field(default=0, description="消息总数")
    messages_by_type: dict = Field(default={}, description="按类型分布的消息数")
    total_tool_calls: int = Field(default=0, description="工具调用总次数")
    tool_calls_by_status: dict = Field(default={}, description="工具调用按状态分布")
    task_outcomes: dict = Field(default={}, description="任务结果分布")


class UsersData(BaseModel):
    """用户统计数据"""
    total_users: int = Field(default=0, description="用户总数")
    active_users: int = Field(default=0, description="活跃用户数")


class SessionsData(BaseModel):
    """会话统计数据"""
    total_sessions: int = Field(default=0, description="会话总数")
    sessions_by_date: list = Field(default=[], description="按日期聚合的会话创建数")


class MessagesData(BaseModel):
    """消息统计数据"""
    total_messages: int = Field(default=0, description="消息总数")
    messages_by_type: dict = Field(default={}, description="按类型分布的消息数")


class ToolCallsData(BaseModel):
    """工具调用统计数据"""
    total_tool_calls: int = Field(default=0, description="工具调用总次数")
    by_status: dict = Field(default={}, description="按状态分布")
    by_tool_name: list = Field(default=[], description="按工具名称分布（TOP N）")


class TasksData(BaseModel):
    """任务结果统计数据"""
    completed: int = Field(default=0, description="正常完成数")
    stopped: int = Field(default=0, description="已停止/异常中断数")
    error: int = Field(default=0, description="错误数")
    interrupted: int = Field(default=0, description="HITL 中断数")
    total_runs: int = Field(default=0, description="总运行次数")


class DeepStatsData(BaseModel):
    """Deep Agent 运营指标"""
    deep_run_count: int = Field(default=0)
    subagent_count: int = Field(default=0)
    artifact_bytes: int = Field(default=0)
    workspace_bytes_by_user: dict = Field(default_factory=dict)
    sandbox_active_count: int = Field(default=0)
    audit_events_count: int = Field(default=0)
    token_usage_aggregate: dict = Field(default_factory=dict)


# ==================== 接口实现 ====================


@router.get("/overview", response_model=ApiResponse[OverviewData])
async def get_overview(
    start_date: str = Query(default=None, description="开始日期 (YYYY-MM-DD)"),
    end_date: str = Query(default=None, description="结束日期 (YYYY-MM-DD)"),
):
    """获取运营概览数据（一次性返回所有核心指标）。

    支持时间范围过滤，所有指标统一按 start_date/end_date 范围统计。
    不传时间范围时统计全量数据。
    """
    try:
        total_users = await storage.get_total_users()
        active_users = await storage.get_active_users(start_date, end_date)
        total_sessions = await storage.get_session_count(start_date, end_date)
        total_messages = await storage.get_message_count(start_date, end_date)
        messages_by_type = await storage.get_messages_by_type(start_date, end_date)
        total_tool_calls = await storage.get_tool_call_count(start_date, end_date)
        tool_calls_by_status = await storage.get_tool_calls_by_status(start_date, end_date)
        task_outcomes = await storage.get_task_outcome_counts(start_date, end_date)

        data = OverviewData(
            total_users=total_users,
            active_users=active_users,
            total_sessions=total_sessions,
            total_messages=total_messages,
            messages_by_type=messages_by_type,
            total_tool_calls=total_tool_calls,
            tool_calls_by_status=tool_calls_by_status,
            task_outcomes=task_outcomes,
        )
        return ApiResponse.success(data=data, msg="获取运营概览成功")
    except Exception as e:
        logger.error(f"[Stats API] 获取运营概览失败: {e}")
        return ApiResponse.error(msg=f"获取运营概览失败: {str(e)}")


@router.get("/users", response_model=ApiResponse[UsersData])
async def get_user_stats(
    start_date: str = Query(default=None, description="活跃用户统计开始日期 (YYYY-MM-DD)"),
    end_date: str = Query(default=None, description="活跃用户统计结束日期 (YYYY-MM-DD)"),
):
    """获取用户统计详情。

    包括用户总数和指定时间范围内的活跃用户数。
    """
    try:
        total_users = await storage.get_total_users()
        active_users = await storage.get_active_users(start_date, end_date)

        data = UsersData(
            total_users=total_users,
            active_users=active_users,
        )
        return ApiResponse.success(data=data, msg="获取用户统计成功")
    except Exception as e:
        logger.error(f"[Stats API] 获取用户统计失败: {e}")
        return ApiResponse.error(msg=f"获取用户统计失败: {str(e)}")


@router.get("/sessions", response_model=ApiResponse[SessionsData])
async def get_session_stats(
    start_date: str = Query(default=None, description="开始日期 (YYYY-MM-DD)"),
    end_date: str = Query(default=None, description="结束日期 (YYYY-MM-DD)"),
):
    """获取会话统计详情。

    包括会话总数和按日期聚合的创建趋势。
    """
    try:
        total_sessions = await storage.get_session_count(start_date, end_date)
        sessions_by_date = await storage.get_sessions_by_date(start_date, end_date)

        data = SessionsData(
            total_sessions=total_sessions,
            sessions_by_date=sessions_by_date,
        )
        return ApiResponse.success(data=data, msg="获取会话统计成功")
    except Exception as e:
        logger.error(f"[Stats API] 获取会话统计失败: {e}")
        return ApiResponse.error(msg=f"获取会话统计失败: {str(e)}")


@router.get("/messages", response_model=ApiResponse[MessagesData])
async def get_message_stats(
    start_date: str = Query(default=None, description="开始日期 (YYYY-MM-DD)"),
    end_date: str = Query(default=None, description="结束日期 (YYYY-MM-DD)"),
):
    """获取消息统计详情。

    包括消息总数和按类型（chat/tool_call/tool_result/end/error 等）的分类统计。
    """
    try:
        total_messages = await storage.get_message_count(start_date, end_date)
        messages_by_type = await storage.get_messages_by_type(start_date, end_date)

        data = MessagesData(
            total_messages=total_messages,
            messages_by_type=messages_by_type,
        )
        return ApiResponse.success(data=data, msg="获取消息统计成功")
    except Exception as e:
        logger.error(f"[Stats API] 获取消息统计失败: {e}")
        return ApiResponse.error(msg=f"获取消息统计失败: {str(e)}")


@router.get("/tool-calls", response_model=ApiResponse[ToolCallsData])
async def get_tool_call_stats(
    start_date: str = Query(default=None, description="开始日期 (YYYY-MM-DD)"),
    end_date: str = Query(default=None, description="结束日期 (YYYY-MM-DD)"),
    top_n: int = Query(default=20, description="按工具名称统计的 TOP N 数量"),
):
    """获取工具调用统计详情。

    包括总调用次数、按状态（success/fail）分布、按工具名称分布（TOP N）。
    """
    try:
        total_tool_calls = await storage.get_tool_call_count(start_date, end_date)
        by_status = await storage.get_tool_calls_by_status(start_date, end_date)
        by_tool_name = await storage.get_tool_calls_by_name(start_date, end_date, limit=top_n)

        data = ToolCallsData(
            total_tool_calls=total_tool_calls,
            by_status=by_status,
            by_tool_name=by_tool_name,
        )
        return ApiResponse.success(data=data, msg="获取工具调用统计成功")
    except Exception as e:
        logger.error(f"[Stats API] 获取工具调用统计失败: {e}")
        return ApiResponse.error(msg=f"获取工具调用统计失败: {str(e)}")


@router.get("/tasks", response_model=ApiResponse[TasksData])
async def get_task_stats(
    start_date: str = Query(default=None, description="开始日期 (YYYY-MM-DD)"),
    end_date: str = Query(default=None, description="结束日期 (YYYY-MM-DD)"),
):
    """获取任务结果统计详情。

    统计完成（completed）、停止（stopped）、错误（error）、
    HITL 中断（interrupted）的次数，以及总运行次数。
    """
    try:
        outcomes = await storage.get_task_outcome_counts(start_date, end_date)
        total_runs = sum(outcomes.values())

        data = TasksData(
            completed=outcomes.get("completed", 0),
            stopped=outcomes.get("stopped", 0),
            error=outcomes.get("error", 0),
            interrupted=outcomes.get("interrupted", 0),
            total_runs=total_runs,
        )
        return ApiResponse.success(data=data, msg="获取任务统计成功")
    except Exception as e:
        logger.error(f"[Stats API] 获取任务统计失败: {e}")
        return ApiResponse.error(msg=f"获取任务统计失败: {str(e)}")


@router.get("/deep", response_model=ApiResponse[DeepStatsData])
async def get_deep_stats(
    start_date: str = Query(default=None, description="开始日期 (YYYY-MM-DD)"),
    end_date: str = Query(default=None, description="结束日期 (YYYY-MM-DD)"),
):
    """获取 Deep Agent 专属运营指标。"""
    try:
        raw = await deep_storage.get_deep_overview_stats(start_date, end_date)
        data = DeepStatsData(**raw)
        return ApiResponse.success(data=data, msg="获取 Deep 统计成功")
    except Exception as e:
        logger.error(f"[Stats API] 获取 Deep 统计失败: {e}")
        return ApiResponse.error(msg=f"获取 Deep 统计失败: {str(e)}")
