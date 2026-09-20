"""内置工具：让对话 Agent 管理当前用户的定时任务。

支持动作（action）：
- create:  创建定时任务（记录当前对话的能力快照：技能 + MCP，不再绑定智能体）
- list:    分页列出当前用户的定时任务
- get:     查看单个任务详情
- update:  更新任务（名称/描述/提示词/cron/技能/MCP/启用状态）
- delete:  删除任务（软删）
- toggle:  启用 / 停用任务
- run:     立即执行一次（将 next_run_at 置为当前时刻，由调度器下一轮拉起）

复用现有存储层与 cron 计算，不引入新表：
- db/scheduled_store.py   scheduled_tasks 表的 CRUD
- scheduler/cron.py       cron 校验、下次执行时间计算、东八区格式化
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from langchain.tools import tool
from pydantic import BaseModel, Field

from config import get_logger
from db import deep_storage, scheduled_store
from scheduler.cron import compute_next_run_at, format_cst, validate_cron_expr

logger = get_logger(__name__)

_MAX_NAME_LEN = 64
_MAX_DESC_LEN = 500
_MAX_PROMPT_LEN = 4000
_MAX_PAGE_SIZE = 50


class _ScheduledTaskInput(BaseModel):
    """manage_scheduled_task 的输入参数（单 schema 合并，按 action 取用）。"""

    action: str = Field(
        ...,
        description=(
            "操作类型: create(创建) / list(列表) / get(详情) / update(更新) / "
            "delete(删除) / toggle(启用停用) / run(立即执行)"
        ),
    )
    task_id: Optional[str] = Field(None, description="任务ID（get/update/delete/toggle/run 必填）")
    name: Optional[str] = Field(None, description="任务名称（create 必填，update 可选）")
    description: Optional[str] = Field(None, description="任务描述（create/update 可选）")
    cron_expr: Optional[str] = Field(
        None,
        description="cron 表达式（标准5段，东八区），create 必填，update 变更时可选",
    )
    prompt: Optional[str] = Field(None, description="任务执行提示词/指令（create/update 可选）")
    is_enabled: Optional[bool] = Field(None, description="是否启用（create/update 可选）")
    page: int = Field(1, ge=1, description="list 页码")
    page_size: int = Field(20, ge=1, le=_MAX_PAGE_SIZE, description="list 每页数量")


def _err(msg: str) -> str:
    return f"错误：{msg}"


def _format_skills(row: Dict[str, Any]) -> str:
    skills = row.get("skills") or []
    if not skills:
        return "-"
    return ", ".join(str(s).strip() for s in skills if str(s).strip())


def _format_mcp(row: Dict[str, Any]) -> str:
    mcp = row.get("mcp") or []
    if not mcp:
        return "-"
    names = [str(m.get("name") or "").strip() for m in mcp if str(m.get("name") or "").strip()]
    return ", ".join(names) if names else f"{len(mcp)} 个"


def _format_task_detail(row: Dict[str, Any]) -> str:
    """单任务详情文本（时间为东八区字符串）。"""
    last = row.get("last_result") or {}
    last_status = last.get("status", "")
    last_detail = ""
    if last.get("final_answer"):
        last_detail = str(last["final_answer"])[:200]
    elif last.get("error"):
        last_detail = str(last["error"])[:200]

    agent_id = str(row.get("agent_id") or "").strip()
    agent_desc = f"智能体 {agent_id}" if agent_id else "默认智能体"

    lines = [
        f"任务: {row.get('name', '')}",
        f"  ID: {row.get('id', '')}",
        f"  cron: {row.get('cron_expr', '')}",
        f"  归属: {agent_desc}",
        f"  技能: {_format_skills(row)}",
        f"  MCP: {_format_mcp(row)}",
        f"  状态: {'启用' if row.get('is_enabled') else '停用'} (运行态 {row.get('status', '')})",
        f"  下次执行: {format_cst(row.get('next_run_at'))}",
        f"  上次执行: {format_cst(row.get('last_run_at'))}",
        f"  执行次数: {int(row.get('run_count', 0) or 0)}",
    ]
    if row.get("description"):
        lines.append(f"  描述: {row.get('description', '')}")
    if row.get("prompt"):
        lines.append(f"  提示词: {row.get('prompt', '')}")
    if last_status:
        lines.append(f"  上次结果: [{last_status}] {last_detail}")
    return "\n".join(lines)


def _format_task_list(items: List[Dict[str, Any]], total: int, page: int, page_size: int) -> str:
    if not items:
        return "当前没有定时任务。"
    lines = ["定时任务列表:"]
    for r in items:
        state = "启用" if r.get("is_enabled") else "停用"
        lines.append(
            f"  [{r.get('id', '')}] {r.get('name', '')} "
            f"({r.get('cron_expr', '')}) {state} 下次: {format_cst(r.get('next_run_at'))}"
        )
    pages = max(1, (total + page_size - 1) // page_size)
    lines.append(f"共 {total} 条，第 {page}/{pages} 页。")
    return "\n".join(lines)


def create_scheduled_task_tool(
    user_id: str,
    user_token: str = "",
    session_id: str = "",
    capabilities: Optional[Dict[str, Any]] = None,
):
    """工厂函数：创建绑定了当前用户身份的 manage_scheduled_task 工具。

    通过闭包注入 user_id/session_id/capabilities：
    - session_id 用于反查当前会话是否订阅智能体（决定任务归属 agent_id）；
    - capabilities 是当前会话已解析的能力 {"mcp": [...], "skills": [...]}，
      默认智能体会话（无绑定 agent）靠它记录能力快照；
    - 定时任务归属鉴权仍只用 user_id + 数据库归属。
    """
    _uid = user_id or ""
    _sid = session_id or ""
    _capabilities = capabilities or {}

    async def _collect_capabilities() -> tuple[List[str], List[Dict[str, Any]], Optional[str]]:
        """采集当前会话的能力归属，返回 (skill_ids, mcp_configs, agent_id)。

        - 订阅智能体会话：agent_id = 该智能体，skills/mcp 置空（执行时按智能体绑定能力运行时解析）。
        - 默认智能体会话：agent_id = None，记录注入的当前会话能力快照（排除内置技能）。
        """
        from deep_agent_server.skills.selector import BUILTIN_SKILL_IDS

        bound = None
        if _sid:
            try:
                bound = await deep_storage.get_session_agent_id(_sid)
            except Exception as e:
                logger.warning(f"[ScheduledTask] 反查会话智能体失败 sid={_sid} err={e}")
        if bound:
            # 订阅智能体路径：关联到该智能体，不记录能力快照
            return [], [], str(bound)

        # 默认智能体路径：记录注入的当前会话能力，排除内置技能（执行时默认可用）
        skill_ids: List[str] = []
        for s in _capabilities.get("skills") or []:
            sid = str(s).strip()
            if sid and sid not in BUILTIN_SKILL_IDS:
                skill_ids.append(sid)
        mcp_configs: List[Dict[str, Any]] = list(_capabilities.get("mcp") or [])
        return skill_ids, mcp_configs, None

    async def _create(inp: Dict[str, Any]) -> str:
        name = str(inp.get("name") or "").strip()
        cron_expr = str(inp.get("cron_expr") or "").strip()
        if not name:
            return _err("缺少必填字段 name（任务名称）。")
        if len(name) > _MAX_NAME_LEN:
            return _err(f"任务名称过长（最多 {_MAX_NAME_LEN} 字符）。")
        if not cron_expr:
            return _err("缺少必填字段 cron_expr（cron 表达式）。")
        if not validate_cron_expr(cron_expr):
            return _err(f"cron 表达式不合法: {cron_expr}")

        description = str(inp.get("description") or "").strip()
        if len(description) > _MAX_DESC_LEN:
            return _err(f"任务描述过长（最多 {_MAX_DESC_LEN} 字符）。")
        prompt = str(inp.get("prompt") or "").strip()
        if len(prompt) > _MAX_PROMPT_LEN:
            return _err(f"任务提示词过长（最多 {_MAX_PROMPT_LEN} 字符）。")
        is_enabled = bool(inp.get("is_enabled", True))

        skills, mcp, agent_id = await _collect_capabilities()

        next_run_at = compute_next_run_at(cron_expr)
        row = await scheduled_store.create_task(
            user_id=_uid,
            name=name,
            cron_expr=cron_expr,
            prompt=prompt,
            description=description,
            skills=skills,
            mcp=mcp,
            is_enabled=is_enabled,
            next_run_at=next_run_at,
            agent_id=agent_id,
        )
        logger.info(
            f"[ScheduledTask] 创建成功 task_id={row.get('id')} user_id={_uid} "
            f"agent_id={agent_id or 'default'}"
        )
        return "创建成功：\n" + _format_task_detail(row)

    async def _list(inp: Dict[str, Any]) -> str:
        page = int(inp.get("page") or 1)
        page_size = int(inp.get("page_size") or 20)
        items, total = await scheduled_store.list_tasks(
            user_id=_uid, page=page, page_size=page_size
        )
        return _format_task_list(items, total, page, page_size)

    async def _get_owned(task_id: str) -> tuple[Optional[Dict[str, Any]], str]:
        """按所有权读取任务：返回 (row, "") 或 (None, 错误文本)。"""
        task_id = str(task_id or "").strip()
        if not task_id:
            return None, _err("缺少 task_id。")
        try:
            row = await scheduled_store.get_task(task_id)
        except Exception as e:
            logger.warning(f"[ScheduledTask] 任务ID格式错误 task_id={task_id} err={e}")
            return None, _err(f"任务ID格式错误: {task_id}")
        if not row or row.get("user_id") != _uid:
            return None, _err("任务不存在或无权访问。")
        return row, ""

    async def _get(inp: Dict[str, Any]) -> str:
        row, hint = await _get_owned(inp.get("task_id"))
        if not row:
            return hint
        return _format_task_detail(row)

    async def _update(inp: Dict[str, Any]) -> str:
        row, hint = await _get_owned(inp.get("task_id"))
        if not row:
            return hint

        fields: Dict[str, Any] = {}
        if inp.get("name") is not None:
            name = str(inp["name"]).strip()
            if not name:
                return _err("任务名称不能为空。")
            if len(name) > _MAX_NAME_LEN:
                return _err(f"任务名称过长（最多 {_MAX_NAME_LEN} 字符）。")
            fields["name"] = name
        if inp.get("description") is not None:
            description = str(inp["description"]).strip()
            if len(description) > _MAX_DESC_LEN:
                return _err(f"任务描述过长（最多 {_MAX_DESC_LEN} 字符）。")
            fields["description"] = description
        if inp.get("prompt") is not None:
            prompt = str(inp["prompt"]).strip()
            if len(prompt) > _MAX_PROMPT_LEN:
                return _err(f"任务提示词过长（最多 {_MAX_PROMPT_LEN} 字符）。")
            fields["prompt"] = prompt
        if inp.get("is_enabled") is not None:
            fields["is_enabled"] = bool(inp["is_enabled"])

        new_cron = str(inp.get("cron_expr") or "").strip() if inp.get("cron_expr") is not None else ""
        if new_cron:
            if not validate_cron_expr(new_cron):
                return _err(f"cron 表达式不合法: {new_cron}")
            fields["cron_expr"] = new_cron
            fields["next_run_at"] = compute_next_run_at(new_cron)

        if not fields:
            return _format_task_detail(row)
        updated = await scheduled_store.update_task(str(row["id"]), **fields)
        if not updated:
            return _err("任务不存在或更新失败。")
        logger.info(f"[ScheduledTask] 更新成功 task_id={row.get('id')}")
        return "更新成功：\n" + _format_task_detail(updated)

    async def _delete(inp: Dict[str, Any]) -> str:
        row, hint = await _get_owned(inp.get("task_id"))
        if not row:
            return hint
        await scheduled_store.soft_delete_task(str(row["id"]))
        logger.info(f"[ScheduledTask] 删除成功 task_id={row.get('id')}")
        return f"任务 [{row.get('name', '')}] 已删除。"

    async def _toggle(inp: Dict[str, Any]) -> str:
        row, hint = await _get_owned(inp.get("task_id"))
        if not row:
            return hint
        new_state = not bool(row.get("is_enabled", True))
        updated = await scheduled_store.update_task(str(row["id"]), is_enabled=new_state)
        if not updated:
            return _err("任务不存在或更新失败。")
        logger.info(f"[ScheduledTask] 切换状态 task_id={row.get('id')} enabled={new_state}")
        return f"任务 [{row.get('name', '')}] 已{'启用' if new_state else '停用'}。"

    async def _run(inp: Dict[str, Any]) -> str:
        row, hint = await _get_owned(inp.get("task_id"))
        if not row:
            return hint
        # 置为当前时刻，由调度器下一轮自动捡走执行（防重入由 claim_task 保证）
        await scheduled_store.update_task(
            str(row["id"]), next_run_at=datetime.now(timezone.utc)
        )
        logger.info(f"[ScheduledTask] 触发立即执行 task_id={row.get('id')}")
        return (
            f"已触发任务 [{row.get('name', '')}] 立即执行，"
            "将在调度周期内由调度器拉起，执行结果稍后可查看任务详情。"
        )

    @tool("manage_scheduled_task", args_schema=_ScheduledTaskInput)
    async def manage_scheduled_task(
        action: str,
        task_id: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        cron_expr: Optional[str] = None,
        prompt: Optional[str] = None,
        is_enabled: Optional[bool] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> str:
        """管理当前用户的定时任务：创建、查看、更新、删除、启用停用、立即执行。

        创建时自动记录当前对话的能力快照（技能 + MCP），任务不再绑定具体智能体；
        执行时以新会话 + 记录的能力运行。手动执行后任务会在调度周期内被拉起，不阻塞等待结果。
        """
        inp: Dict[str, Any] = {
            "action": action,
            "task_id": task_id,
            "name": name,
            "description": description,
            "cron_expr": cron_expr,
            "prompt": prompt,
            "is_enabled": is_enabled,
            "page": page,
            "page_size": page_size,
        }
        try:
            if action == "create":
                return await _create(inp)
            if action == "list":
                return await _list(inp)
            if action == "get":
                return await _get(inp)
            if action == "update":
                return await _update(inp)
            if action == "delete":
                return await _delete(inp)
            if action == "toggle":
                return await _toggle(inp)
            if action == "run":
                return await _run(inp)
            return _err(f"未知 action: {action}")
        except Exception as e:
            logger.exception(f"[ScheduledTask] 工具执行异常 action={action}")
            return _err(f"操作失败: {e}")

    return manage_scheduled_task
