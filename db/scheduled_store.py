"""定时任务（scheduled_tasks）数据访问层。

字段说明：
- skills: 技能 id（目录名）列表，创建时从当前对话能力快照而来
- mcp:    已解析 MCP 配置列表，可离线回放
- status: pending(待运行) / running(执行中) / error(上次异常)
- is_enabled: 调度开关；status 为运行态，二者语义分离
- next_run_at: 存 aware UTC（timestamptz）；cron 语义为东八区，由 scheduler/cron.py 换算
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from uuid import UUID

from db.database import db_manager

_COLUMNS = (
    "id, user_id, name, description, skills, mcp, is_enabled, cron_expr, prompt, "
    "status, next_run_at, last_run_at, last_result, run_count, "
    "is_deleted, created_at, updated_at, agent_id"
)


def _uuid(value) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _row_to_dict(row) -> Dict[str, Any]:
    d = dict(row)
    d["id"] = str(d["id"])
    for json_field in ("last_result", "skills", "mcp"):
        if isinstance(d.get(json_field), str):
            try:
                d[json_field] = json.loads(d[json_field])
            except (json.JSONDecodeError, TypeError):
                d[json_field] = [] if json_field in ("skills", "mcp") else {}
    return d


async def create_task(
    *,
    user_id: str,
    name: str,
    cron_expr: str,
    prompt: str = "",
    description: str = "",
    skills: Optional[List[Any]] = None,
    mcp: Optional[List[Any]] = None,
    is_enabled: bool = True,
    next_run_at=None,
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """创建定时任务，返回完整记录。

    agent_id 非空表示订阅智能体路径（skills/mcp 通常为空，执行时按绑定能力解析）；
    为空表示默认智能体路径（skills/mcp 为能力快照，执行时新开默认会话回放）。
    """
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            INSERT INTO scheduled_tasks
                (user_id, name, description, skills, mcp, is_enabled, cron_expr,
                 prompt, next_run_at, status, agent_id)
            VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, $7, $8, $9, 'pending', $10)
            RETURNING {_COLUMNS}
            """,
            user_id,
            name,
            description,
            json.dumps(skills or [], ensure_ascii=False),
            json.dumps(mcp or [], ensure_ascii=False, default=str),
            bool(is_enabled),
            cron_expr,
            prompt,
            next_run_at,
            agent_id or None,
        )
        return _row_to_dict(row)


async def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_COLUMNS} FROM scheduled_tasks "
            "WHERE id = $1 AND is_deleted = FALSE",
            _uuid(task_id),
        )
        return _row_to_dict(row) if row else None


async def update_task(task_id: str, **fields) -> Optional[Dict[str, Any]]:
    """按白名单更新任务字段，返回更新后的记录；任务不存在/已软删返回 None。"""
    allowed = {
        "name", "description", "is_enabled", "cron_expr", "prompt",
        "status", "next_run_at", "skills", "mcp", "agent_id",
    }
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return await get_task(task_id)

    sets = []
    params: List[Any] = []
    idx = 1
    for key, val in updates.items():
        if key == "next_run_at":
            sets.append(f"{key} = ${idx}::timestamptz")
            params.append(val)
        elif key in ("skills", "mcp"):
            sets.append(f"{key} = ${idx}::jsonb")
            params.append(json.dumps(val or [], ensure_ascii=False, default=str))
        else:
            sets.append(f"{key} = ${idx}")
            params.append(val)
        idx += 1
    sets.append("updated_at = CURRENT_TIMESTAMP")
    params.append(_uuid(task_id))

    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE scheduled_tasks SET {', '.join(sets)} "
            f"WHERE id = ${idx} AND is_deleted = FALSE RETURNING {_COLUMNS}",
            *params,
        )
        return _row_to_dict(row) if row else None


async def soft_delete_task(task_id: str) -> bool:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE scheduled_tasks SET is_deleted = TRUE, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = $1 AND is_deleted = FALSE",
            _uuid(task_id),
        )
        return result == "UPDATE 1"


async def list_tasks(
    *,
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    is_enabled: Optional[bool] = None,
) -> tuple[List[Dict[str, Any]], int]:
    """分页查询用户的定时任务（不含已软删）。"""
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    offset = (page - 1) * page_size

    conditions = ["user_id = $1", "is_deleted = FALSE"]
    params: List[Any] = [user_id]
    idx = 2
    if is_enabled is not None:
        conditions.append(f"is_enabled = ${idx}")
        params.append(bool(is_enabled))
        idx += 1
    where = " AND ".join(conditions)

    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM scheduled_tasks WHERE {where}",
            *params,
        )
        rows = await conn.fetch(
            f"SELECT {_COLUMNS} FROM scheduled_tasks WHERE {where} "
            f"ORDER BY created_at DESC LIMIT ${idx} OFFSET ${idx + 1}",
            *params,
            page_size,
            offset,
        )
        items = [_row_to_dict(r) for r in rows]
        return items, int(total or 0)


async def list_due_tasks(limit: int = 50) -> List[Dict[str, Any]]:
    """查询到期且未被占用的任务（调度轮询用）。"""
    limit = max(1, min(limit, 200))
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {_COLUMNS} FROM scheduled_tasks "
            "WHERE is_deleted = FALSE AND is_enabled = TRUE "
            "  AND status <> 'running' "
            "  AND next_run_at IS NOT NULL AND next_run_at <= now() "
            f"ORDER BY next_run_at ASC LIMIT {limit}"
        )
        return [_row_to_dict(r) for r in rows]


async def claim_task(task_id: str) -> bool:
    """原子抢占任务：仅当非 running 时置为 running，返回是否抢占成功（防重入）。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE scheduled_tasks SET status = 'running', updated_at = CURRENT_TIMESTAMP "
            "WHERE id = $1 AND status <> 'running' AND is_deleted = FALSE",
            _uuid(task_id),
        )
        return result == "UPDATE 1"


async def finish_task(
    task_id: str,
    *,
    status: str,
    next_run_at,
    last_result: dict,
    last_run_at,
) -> None:
    """执行收尾统一回写：状态、下次执行时间、上次执行结果、运行次数。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE scheduled_tasks SET
                status = $2,
                next_run_at = $3::timestamptz,
                last_result = $4::jsonb,
                last_run_at = $5::timestamptz,
                run_count = run_count + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = $1
            """,
            _uuid(task_id),
            status,
            next_run_at,
            json.dumps(last_result, ensure_ascii=False, default=str),
            last_run_at,
        )


async def reset_task_status(task_id: str, status: str = "pending") -> bool:
    """异常兜底：将任务状态复位（如从 running 恢复为 pending）。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE scheduled_tasks SET status = $2, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = $1",
            _uuid(task_id),
            status,
        )
        return result == "UPDATE 1"
