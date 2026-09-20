"""定时任务 REST 接口：创建/删除/修改/查询/手动执行。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Query, Request
from pydantic import BaseModel, Field

from common.schemas import ApiResponse, PaginatedApiResponse
from config import get_logger
from db import scheduled_store
from scheduler.cron import compute_next_run_at, format_cst, validate_cron_expr
from scheduler.executor import execute_scheduled_task

logger = get_logger(__name__)

router = APIRouter(prefix="/scheduled_tasks", tags=["scheduled_tasks"])


# ==================== 数据模型 ====================

class ScheduledTaskItem(BaseModel):
    """定时任务项（时间字段均为东八区字符串）。"""
    id: str
    name: str
    description: str = ""
    skills: List[str] = Field(default_factory=list)
    mcp: List[Dict[str, Any]] = Field(default_factory=list)
    is_enabled: bool = True
    cron_expr: str
    prompt: str = ""
    status: str = "pending"
    next_run_at: str = ""
    last_run_at: str = ""
    last_result: Dict[str, Any] = Field(default_factory=dict)
    run_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    agent_id: Optional[str] = None


class ScheduledTaskCreate(BaseModel):
    """创建定时任务请求体（能力：技能 + MCP 必传）。"""
    user_id: str = Field(..., description="用户ID")
    name: str = Field(..., description="任务名称")
    description: str = Field(default="", description="任务描述")
    skills: List[str] = Field(default_factory=list, description="技能ID列表")
    mcp: List[Dict[str, Any]] = Field(default_factory=list, description="MCP配置列表")
    cron_expr: str = Field(..., description="cron 表达式（标准5段，东八区）")
    prompt: str = Field(default="", description="任务提示词")
    is_enabled: bool = Field(default=True, description="是否启用")
    agent_id: Optional[str] = Field(default=None, description="订阅智能体ID（订阅路径）")


class ScheduledTaskUpdate(BaseModel):
    """更新定时任务请求体（部分字段可选）。"""
    name: Optional[str] = Field(default=None, description="任务名称")
    description: Optional[str] = Field(default=None, description="任务描述")
    skills: Optional[List[str]] = Field(default=None, description="技能ID列表")
    mcp: Optional[List[Dict[str, Any]]] = Field(default=None, description="MCP配置列表")
    cron_expr: Optional[str] = Field(default=None, description="cron 表达式")
    prompt: Optional[str] = Field(default=None, description="任务提示词")
    is_enabled: Optional[bool] = Field(default=None, description="是否启用")


# ==================== 辅助 ====================

def _to_item(row: Dict[str, Any]) -> ScheduledTaskItem:
    return ScheduledTaskItem(
        id=row["id"],
        name=row.get("name", ""),
        description=row.get("description", ""),
        skills=row.get("skills") or [],
        mcp=row.get("mcp") or [],
        is_enabled=bool(row.get("is_enabled", True)),
        cron_expr=row.get("cron_expr", ""),
        prompt=row.get("prompt", ""),
        status=row.get("status", "pending"),
        next_run_at=format_cst(row.get("next_run_at")),
        last_run_at=format_cst(row.get("last_run_at")),
        last_result=row.get("last_result") or {},
        run_count=int(row.get("run_count", 0) or 0),
        created_at=format_cst(row.get("created_at")),
        updated_at=format_cst(row.get("updated_at")),
        agent_id=row.get("agent_id"),
    )


# ==================== 接口实现 ====================

@router.post("", response_model=ApiResponse[ScheduledTaskItem])
async def create_scheduled_task(body: ScheduledTaskCreate = Body(...)):
    """创建定时任务：校验 cron 合法性，服务端计算初始 next_run_at。"""
    try:
        user_id = (body.user_id or "").strip()
        name = (body.name or "").strip()
        cron_expr = (body.cron_expr or "").strip()
        if not user_id or not name or not cron_expr:
            return ApiResponse.error(msg="缺少必填参数: user_id/name/cron_expr", code=400)
        if not validate_cron_expr(cron_expr):
            return ApiResponse.error(msg=f"cron 表达式不合法: {cron_expr}", code=400)

        next_run_at = compute_next_run_at(cron_expr)
        row = await scheduled_store.create_task(
            user_id=user_id,
            name=name,
            cron_expr=cron_expr,
            prompt=body.prompt or "",
            description=body.description or "",
            skills=body.skills,
            mcp=body.mcp,
            is_enabled=body.is_enabled,
            next_run_at=next_run_at,
            agent_id=body.agent_id,
        )
        return ApiResponse.success(data=_to_item(row), msg="创建成功")
    except Exception as e:
        logger.error(f"[ScheduledTask API] 创建失败: {e}")
        return ApiResponse.error(msg=f"创建失败: {str(e)}")


@router.put("/{task_id}", response_model=ApiResponse[ScheduledTaskItem])
async def update_scheduled_task(
    task_id: str,
    body: ScheduledTaskUpdate = Body(...),
    user_id: str = Query(..., description="用户ID"),
):
    """修改定时任务：cron 变化时重算 next_run_at；校验归属。"""
    try:
        existing = await scheduled_store.get_task(task_id)
        if not existing or existing.get("user_id") != user_id:
            return ApiResponse.error(msg="任务不存在或无权访问", code=404)

        fields: Dict[str, Any] = {}
        # agent_id 创建时锁定，update 不允许修改（订阅/默认路径归属不变）
        if body.name is not None:
            fields["name"] = body.name.strip()
        if body.description is not None:
            fields["description"] = body.description
        if body.prompt is not None:
            fields["prompt"] = body.prompt
        if body.is_enabled is not None:
            fields["is_enabled"] = body.is_enabled
        if body.skills is not None:
            fields["skills"] = body.skills
        if body.mcp is not None:
            fields["mcp"] = body.mcp

        # cron 变化时校验并重算 next_run_at
        new_cron = (body.cron_expr or "").strip() if body.cron_expr is not None else None
        if new_cron is not None:
            if not validate_cron_expr(new_cron):
                return ApiResponse.error(msg=f"cron 表达式不合法: {new_cron}", code=400)
            fields["cron_expr"] = new_cron
            fields["next_run_at"] = compute_next_run_at(new_cron)

        if not fields:
            return ApiResponse.success(data=_to_item(existing), msg="无变更")

        row = await scheduled_store.update_task(task_id, **fields)
        if not row:
            return ApiResponse.error(msg="任务不存在或更新失败", code=404)
        return ApiResponse.success(data=_to_item(row), msg="更新成功")
    except Exception as e:
        logger.error(f"[ScheduledTask API] 更新失败 task_id={task_id}: {e}")
        return ApiResponse.error(msg=f"更新失败: {str(e)}")


@router.delete("/{task_id}", response_model=ApiResponse)
async def delete_scheduled_task(
    task_id: str,
    user_id: str = Query(..., description="用户ID"),
):
    """删除定时任务（软删）。"""
    try:
        existing = await scheduled_store.get_task(task_id)
        if not existing or existing.get("user_id") != user_id:
            return ApiResponse.error(msg="任务不存在或无权访问", code=404)
        ok = await scheduled_store.soft_delete_task(task_id)
        if not ok:
            return ApiResponse.error(msg="删除失败", code=404)
        return ApiResponse.success(msg="删除成功")
    except Exception as e:
        logger.error(f"[ScheduledTask API] 删除失败 task_id={task_id}: {e}")
        return ApiResponse.error(msg=f"删除失败: {str(e)}")


@router.post("/{task_id}/run", response_model=ApiResponse)
async def run_scheduled_task_now(
    task_id: str,
    user_id: str = Query(..., description="用户ID"),
    request: Request = None,
):
    """手动立即执行一次定时任务。"""
    try:
        existing = await scheduled_store.get_task(task_id)
        if not existing or existing.get("user_id") != user_id:
            return ApiResponse.error(msg="任务不存在或无权访问", code=404)
        manager_agents = request.app.state.manager_agents
        asyncio.create_task(execute_scheduled_task(existing, manager_agents))
        return ApiResponse.success(msg="已触发执行")
    except Exception as e:
        logger.error(f"[ScheduledTask API] 手动执行失败 task_id={task_id}: {e}")
        return ApiResponse.error(msg=f"触发执行失败: {str(e)}")


@router.get("/{task_id}", response_model=ApiResponse[ScheduledTaskItem])
async def get_scheduled_task(
    task_id: str,
    user_id: str = Query(..., description="用户ID"),
):
    """查询单个定时任务。"""
    try:
        row = await scheduled_store.get_task(task_id)
        if not row or row.get("user_id") != user_id:
            return ApiResponse.error(msg="任务不存在或无权访问", code=404)
        return ApiResponse.success(data=_to_item(row), msg="成功")
    except Exception as e:
        logger.error(f"[ScheduledTask API] 查询失败 task_id={task_id}: {e}")
        return ApiResponse.error(msg=f"查询失败: {str(e)}")


@router.get("", response_model=PaginatedApiResponse[ScheduledTaskItem])
async def list_scheduled_tasks(
    user_id: str = Query(..., description="用户ID"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    is_enabled: Optional[bool] = Query(None, description="按启用状态过滤"),
):
    """分页查询用户的定时任务列表。"""
    try:
        items, total = await scheduled_store.list_tasks(
            user_id=user_id,
            page=page,
            page_size=page_size,
            is_enabled=is_enabled,
        )
        return PaginatedApiResponse.success(
            items=[_to_item(r) for r in items],
            total=total,
            page=page,
            page_size=page_size,
            msg="成功",
        )
    except Exception as e:
        logger.error(f"[ScheduledTask API] 列表查询失败 user_id={user_id}: {e}")
        return PaginatedApiResponse.error(msg=f"列表查询失败: {str(e)}")
