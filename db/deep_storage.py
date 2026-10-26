"""Deep Agent 数据库操作。"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from db.database import db_manager
from config import get_logger

logger = get_logger(__name__)


def _uuid(val) -> UUID:
    if isinstance(val, UUID):
        return val
    return UUID(str(val))


# ==================== Audit ====================

async def insert_audit_log(
    user_id: str,
    action: str,
    *,
    session_id: str = None,
    run_id: str = None,
    target: str = None,
    detail: dict = None,
) -> None:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO deep_audit_logs (user_id, session_id, run_id, action, target, detail)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            """,
            user_id,
            _uuid(session_id) if session_id else None,
            run_id,
            action,
            target,
            json.dumps(detail or {}, ensure_ascii=False),
        )


# ==================== Quota ====================

async def ensure_user_quota(user_id: str) -> Dict[str, Any]:
    from config.config import app_config

    limit_b = int(app_config.WORKSPACE_QUOTA_BYTES)
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_resource_quotas (user_id, workspace_bytes_limit)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE SET
                workspace_bytes_limit = EXCLUDED.workspace_bytes_limit,
                updated_at = CURRENT_TIMESTAMP
            """,
            user_id,
            limit_b,
        )
        row = await conn.fetchrow(
            "SELECT * FROM user_resource_quotas WHERE user_id = $1",
            user_id,
        )
        return dict(row) if row else {}


async def check_quota(user_id: str, additional_bytes: int = 0) -> bool:
    from deep_agent_server.quota_service import (
        get_workspace_limit,
        measure_workspace_bytes,
    )

    await ensure_user_quota(user_id)
    used = measure_workspace_bytes(user_id)
    return (used + additional_bytes) <= get_workspace_limit()


async def update_quota_used(user_id: str, used_bytes: int) -> None:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_resource_quotas (user_id, workspace_bytes_used)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE SET
                workspace_bytes_used = $2,
                updated_at = CURRENT_TIMESTAMP
            """,
            user_id,
            used_bytes,
        )


# ==================== User files (workspace + artifact unified index) ====================

async def create_artifact(
    user_id: str,
    artifact_type: str,
    storage_path: str,
    summary: str,
    size_bytes: int,
    *,
    session_id: str = None,
    conversation_id: str = None,
    mime_type: str = "text/plain",
) -> str:
    return await create_artifact_with_path(
        user_id,
        artifact_type,
        storage_path,
        summary,
        size_bytes,
        rel_path=None,
        session_id=session_id,
        conversation_id=conversation_id,
        mime_type=mime_type,
    )


async def create_artifact_with_path(
    user_id: str,
    artifact_type: str,
    storage_path: str,
    summary: str,
    size_bytes: int,
    *,
    rel_path: str = None,
    session_id: str = None,
    conversation_id: str = None,
    mime_type: str = "text/plain",
    ref_id: str = None,
) -> str:
    rid = ref_id or str(uuid4())
    resolved_rel = rel_path or f"artifacts/{rid}.txt"
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO deep_workspace_files (
                id, user_id, session_id, rel_path, size_bytes, sha256,
                file_kind, summary, mime_type, conversation_id,
                artifact_type, storage_path, created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, NULL, 'artifact', $6, $7, $8, $9, $10, now(), now())
            ON CONFLICT (
                user_id,
                COALESCE(session_id, '00000000-0000-0000-0000-000000000000'::uuid),
                rel_path,
                COALESCE(file_kind, 'workspace')
            ) DO UPDATE SET
                size_bytes = EXCLUDED.size_bytes,
                summary = EXCLUDED.summary,
                storage_path = EXCLUDED.storage_path,
                updated_at = CURRENT_TIMESTAMP
            """,
            _uuid(rid),
            user_id,
            _uuid(session_id) if session_id else None,
            resolved_rel,
            size_bytes,
            summary,
            mime_type,
            _uuid(conversation_id) if conversation_id else None,
            artifact_type,
            storage_path,
        )
    return rid


async def get_artifact(ref_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM deep_workspace_files
            WHERE id = $1 AND user_id = $2
              AND COALESCE(file_kind, 'workspace') = 'artifact'
            """,
            _uuid(ref_id),
            user_id,
        )
        return dict(row) if row else None


async def delete_artifacts_by_session(user_id: str, session_id: str) -> None:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM deep_workspace_files
            WHERE user_id = $1 AND session_id = $2 AND file_kind = 'artifact'
            """,
            user_id,
            _uuid(session_id),
        )


async def upsert_workspace_file_index(
    user_id: str,
    session_id: str,
    rel_path: str,
    size_bytes: int,
    sha256: str = None,
    *,
    file_kind: str = "workspace",
    mime_type: str = "application/octet-stream",
    storage_path: str = None,
    conversation_id: str = None,
) -> None:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO deep_workspace_files (
                user_id, session_id, rel_path, size_bytes, sha256,
                file_kind, mime_type, storage_path, conversation_id, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, now())
            ON CONFLICT (
                user_id,
                COALESCE(session_id, '00000000-0000-0000-0000-000000000000'::uuid),
                rel_path,
                COALESCE(file_kind, 'workspace')
            ) DO UPDATE SET
                size_bytes = EXCLUDED.size_bytes,
                sha256 = EXCLUDED.sha256,
                storage_path = COALESCE(EXCLUDED.storage_path, deep_workspace_files.storage_path),
                mime_type = EXCLUDED.mime_type,
                conversation_id = COALESCE(EXCLUDED.conversation_id, deep_workspace_files.conversation_id),
                updated_at = CURRENT_TIMESTAMP
            """,
            user_id,
            _uuid(session_id) if session_id else None,
            rel_path,
            size_bytes,
            sha256,
            file_kind,
            mime_type,
            storage_path,
            _uuid(conversation_id) if conversation_id else None,
        )


async def list_workspace_files_by_conversation(
    user_id: str,
    session_id: str,
    conversation_id: str,
) -> List[Dict[str, Any]]:
    """按 conversation 查询工作区文件索引（write/edit 登记）。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT rel_path, mime_type, size_bytes, conversation_id, updated_at
            FROM deep_workspace_files
            WHERE user_id = $1
              AND session_id = $2
              AND conversation_id = $3
              AND COALESCE(file_kind, 'workspace') = 'workspace'
            ORDER BY updated_at ASC
            """,
            user_id,
            _uuid(session_id),
            _uuid(conversation_id),
        )
    result = []
    for row in rows:
        d = dict(row)
        if d.get("conversation_id"):
            d["conversation_id"] = str(d["conversation_id"])
        if d.get("updated_at"):
            d["updated_at"] = d["updated_at"].isoformat()
        result.append(d)
    return result


async def list_workspace_files_by_session(
    user_id: str,
    session_id: str,
) -> List[Dict[str, Any]]:
    """按 session 查询所有轮次的工作区文件（供前端历史加载时一次性获取）。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT rel_path, conversation_id, updated_at
            FROM deep_workspace_files
            WHERE user_id = $1
              AND session_id = $2
              AND COALESCE(file_kind, 'workspace') = 'workspace'
            ORDER BY created_at DESC
            """,
            user_id,
            _uuid(session_id),
        )
    result = []
    for row in rows:
        d = dict(row)
        if d.get("conversation_id"):
            d["conversation_id"] = str(d["conversation_id"])
        result.append(d)
    return result


async def list_workspace_rel_paths_except_conversation(
    user_id: str,
    session_id: str,
    conversation_id: str,
) -> set:
    """查询本 session 中不属于当前 conversation_id 的文件路径集合。

    用于磁盘扫描 fallback 时排除已属于其他轮次的文件，避免跨轮次重复推送。
    """
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT rel_path FROM deep_workspace_files
            WHERE user_id = $1
              AND session_id = $2
              AND conversation_id IS DISTINCT FROM $3::uuid
              AND COALESCE(file_kind, 'workspace') = 'workspace'
            """,
            user_id,
            _uuid(session_id),
            _uuid(conversation_id),
        )
    return {row["rel_path"] for row in rows}


async def delete_workspace_file_index(
    user_id: str,
    rel_path: str,
    file_kind: str = "workspace",
    session_id: str = None,
) -> None:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM deep_workspace_files
            WHERE user_id = $1
              AND rel_path = $2
              AND COALESCE(file_kind, 'workspace') = $3
              AND (
                ($4::uuid IS NULL AND session_id IS NULL)
                OR session_id = $4::uuid
              )
            """,
            user_id,
            rel_path,
            file_kind,
            _uuid(session_id) if session_id else None,
        )


async def delete_workspace_files_by_session(user_id: str, session_id: str) -> None:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM deep_workspace_files
            WHERE user_id = $1 AND session_id = $2 AND file_kind != 'artifact'
            """,
            user_id,
            _uuid(session_id),
        )


# ==================== Agent runs ====================

async def create_agent_run(
    run_id: str,
    user_id: str,
    session_id: str,
    *,
    agent_id: str = None,
    conversation_id: str = None,
    mcp_tools: list = None,
    skills: list = None,
) -> None:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO agent_runs (run_id, user_id, session_id, agent_id, conversation_id, status, mcp_tools, skills)
            VALUES ($1, $2, $3, $4, $5, 'running', $6::jsonb, $7::jsonb)
            """,
            _uuid(run_id),
            user_id,
            _uuid(session_id),
            _uuid(agent_id) if agent_id else None,
            _uuid(conversation_id) if conversation_id else None,
            json.dumps(mcp_tools or [], ensure_ascii=False),
            json.dumps(skills or [], ensure_ascii=False),
        )


async def update_agent_run(
    run_id: str,
    status: str,
    *,
    result_summary: dict = None,
    error: str = "",
    prompt_tokens: int = None,
    completion_tokens: int = None,
    total_tokens: int = None,
    llm_model: str = None,
) -> None:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        if any(v is not None for v in (prompt_tokens, completion_tokens, total_tokens, llm_model)):
            await conn.execute(
                """
                UPDATE agent_runs SET
                    status = $2,
                    result_summary = $3::jsonb,
                    error = $4,
                    prompt_tokens = COALESCE($5, prompt_tokens),
                    completion_tokens = COALESCE($6, completion_tokens),
                    total_tokens = COALESCE($7, total_tokens),
                    llm_model = COALESCE(NULLIF($8, ''), llm_model),
                    finished_at = CURRENT_TIMESTAMP
                WHERE run_id = $1
                """,
                _uuid(run_id),
                status,
                json.dumps(result_summary or {}, ensure_ascii=False),
                error,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                llm_model or "",
            )
        else:
            await conn.execute(
                """
                UPDATE agent_runs SET
                    status = $2,
                    result_summary = $3::jsonb,
                    error = $4,
                    finished_at = CURRENT_TIMESTAMP
                WHERE run_id = $1
                """,
                _uuid(run_id),
                status,
                json.dumps(result_summary or {}, ensure_ascii=False),
                error,
            )


async def get_agent_run(run_id: str, user_id: str = None) -> Optional[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        if user_id:
            row = await conn.fetchrow(
                "SELECT * FROM agent_runs WHERE run_id = $1 AND user_id = $2",
                _uuid(run_id),
                user_id,
            )
        else:
            row = await conn.fetchrow(
                "SELECT * FROM agent_runs WHERE run_id = $1",
                _uuid(run_id),
            )
        if not row:
            return None
        d = dict(row)
        d["run_id"] = str(d["run_id"])
        if d.get("session_id"):
            d["session_id"] = str(d["session_id"])
        if d.get("agent_id"):
            d["agent_id"] = str(d["agent_id"])
        if d.get("conversation_id"):
            d["conversation_id"] = str(d["conversation_id"])
        return d


async def list_agent_runs_for_session(
    session_id: str,
    user_id: str,
) -> List[Dict[str, Any]]:
    """按会话列出 agent_runs（含 token 与耗时，供历史消息展示 run_metrics）。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT run_id, conversation_id, status,
                   prompt_tokens, completion_tokens, total_tokens,
                   started_at, finished_at, llm_model, result_summary
            FROM agent_runs
            WHERE session_id = $1 AND user_id = $2
            ORDER BY started_at ASC
            """,
            _uuid(session_id),
            user_id,
        )
    result: List[Dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        d["run_id"] = str(d["run_id"])
        if d.get("conversation_id"):
            d["conversation_id"] = str(d["conversation_id"])
        # 解析 result_summary 中的 token_usage（含 prompt_breakdown / call_details），
        # 供会话级上下文占比聚合使用
        raw_summary = d.get("result_summary")
        if isinstance(raw_summary, str):
            try:
                raw_summary = json.loads(raw_summary)
            except (ValueError, TypeError):
                raw_summary = None
        usage = (raw_summary or {}).get("token_usage") if isinstance(raw_summary, dict) else None
        if usage:
            d["token_usage"] = usage
        d.pop("result_summary", None)
        started = d.get("started_at")
        finished = d.get("finished_at")
        if started and finished:
            d["duration_ms"] = max(
                0, int((finished - started).total_seconds() * 1000)
            )
        else:
            d["duration_ms"] = 0
        result.append(d)
    return result


# ==================== Subagent runs ====================

async def create_subagent_run(
    subagent_run_id: str,
    session_id: str,
    name: str,
    *,
    parent_run_id: str = None,
    agent_id: str = None,
    input_summary: str = None,
) -> None:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO deep_subagent_runs
            (subagent_run_id, parent_run_id, session_id, agent_id, name, status, input_summary)
            VALUES ($1, $2, $3, $4, $5, 'running', $6)
            ON CONFLICT (subagent_run_id) DO UPDATE SET
                parent_run_id = COALESCE(deep_subagent_runs.parent_run_id, EXCLUDED.parent_run_id),
                agent_id = COALESCE(deep_subagent_runs.agent_id, EXCLUDED.agent_id),
                input_summary = COALESCE(deep_subagent_runs.input_summary, EXCLUDED.input_summary),
                name = EXCLUDED.name
            """,
            _uuid(subagent_run_id),
            _uuid(parent_run_id) if parent_run_id else None,
            _uuid(session_id),
            _uuid(agent_id) if agent_id else None,
            name,
            input_summary,
        )


async def finish_subagent_run(
    subagent_run_id: str,
    status: str,
    *,
    final_report: str = None,
    trace_ref_id: str = None,
) -> None:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE deep_subagent_runs SET
                status = $2, final_report = $3, trace_ref_id = $4,
                finished_at = CURRENT_TIMESTAMP
            WHERE subagent_run_id = $1
            """,
            _uuid(subagent_run_id),
            status,
            final_report,
            _uuid(trace_ref_id) if trace_ref_id else None,
        )


async def append_subagent_trace(
    subagent_run_id: str,
    event_data: dict,
    user_id: str,
    session_id: str,
) -> Optional[str]:
    """将子 Agent 中间事件写入 artifact trace（可选）。"""
    from config.config import app_config

    if not app_config.SUBAGENT_TRACE_ARTIFACT_ENABLED:
        return None
    from deep_agent_server.artifact.legacy import store_text_artifact

    return await store_text_artifact(
        user_id,
        json.dumps(event_data, ensure_ascii=False, default=str),
        artifact_type="subagent_trace",
        session_id=session_id,
    )


async def delete_subagent_runs_by_session(session_id: str) -> None:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM deep_subagent_runs WHERE session_id = $1",
            _uuid(session_id),
        )


# ==================== User agents ====================

async def create_user_agent(
    user_id: str,
    name: str,
    system_prompt: str,
    model_name: str = "",
    config: dict = None,
    description: str = "",
    visibility: str = "public",
    avatar_color: str = "",
) -> str:
    from utils.agent_avatar import avatar_color_from_seed

    agent_id = str(uuid4())
    color = (avatar_color or "").strip() or avatar_color_from_seed(agent_id)
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_agents (
                id, user_id, name, description, system_prompt, model_name, config, visibility, avatar_color
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9)
            """,
            _uuid(agent_id),
            user_id,
            name,
            description,
            system_prompt,
            model_name or "",
            json.dumps(config or {}, ensure_ascii=False),
            visibility,
            color,
        )
    return agent_id


async def get_user_agent(agent_id: str, user_id: str = None) -> Optional[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        if user_id:
            row = await conn.fetchrow(
                """
                SELECT * FROM user_agents
                WHERE id = $1 AND user_id = $2 AND is_deleted = FALSE
                """,
                _uuid(agent_id),
                user_id,
            )
        else:
            row = await conn.fetchrow(
                "SELECT * FROM user_agents WHERE id = $1 AND is_deleted = FALSE",
                _uuid(agent_id),
            )
        if not row:
            return None
        d = dict(row)
        d["id"] = str(d["id"])
        if isinstance(d.get("config"), str):
            d["config"] = json.loads(d["config"])
        return d


async def list_user_agents(user_id: str) -> List[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, name, description, model_name, config, updated_at
            FROM user_agents WHERE user_id = $1 AND is_deleted = FALSE
            ORDER BY updated_at DESC
            """,
            user_id,
        )
        result = []
        for row in rows:
            d = dict(row)
            d["id"] = str(d["id"])
            if isinstance(d.get("config"), str):
                d["config"] = json.loads(d["config"])
            result.append(d)
        return result


async def update_user_agent(
    agent_id: str,
    user_id: str,
    **fields,
) -> bool:
    allowed = {"name", "description", "system_prompt", "model_name", "config", "visibility"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return False
    sets = []
    params = [_uuid(agent_id), user_id]
    idx = 3
    for key, val in updates.items():
        if key == "config":
            sets.append(f"{key} = ${idx}::jsonb")
            params.append(json.dumps(val, ensure_ascii=False))
        else:
            sets.append(f"{key} = ${idx}")
            params.append(val)
        idx += 1
    sets.append("config_version = config_version + 1")
    sets.append("updated_at = CURRENT_TIMESTAMP")
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            f"""
            UPDATE user_agents SET {', '.join(sets)}
            WHERE id = $1 AND user_id = $2 AND is_deleted = FALSE
            """,
            *params,
        )
        return result == "UPDATE 1"


async def hard_delete_user_agent(agent_id: str, user_id: str) -> bool:
    """直接硬删除智能体（无需先下架）。同时冻结关联会话、取消订阅。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # 1. 冻结所有订阅者的活跃会话（仅禁止新对话，保留历史记录可查看）
            await conn.execute(
                """
                UPDATE user_sessions SET subscription_frozen = TRUE, updated_at = CURRENT_TIMESTAMP
                WHERE agent_id = $1 AND agent_kind = 'subscription'
                """,
                _uuid(agent_id),
            )
            # 2. 取消所有活跃订阅
            await conn.execute(
                """
                UPDATE user_agent_subscriptions
                SET status = 'cancelled', cancelled_at = CURRENT_TIMESTAMP
                WHERE agent_id = $1 AND status = 'active'
                """,
                _uuid(agent_id),
            )
            # 3. 仅软删除创建者自己的会话（订阅者的会话保留在列表中）
            await conn.execute(
                """
                UPDATE user_sessions
                SET is_deleted = TRUE, updated_at = CURRENT_TIMESTAMP
                WHERE agent_id = $1 AND agent_kind = 'subscription' AND user_id = $2
                """,
                _uuid(agent_id),
                user_id,
            )
            # 4. 删除关联的运行记录
            await conn.execute(
                "DELETE FROM agent_runs WHERE agent_id = $1",
                _uuid(agent_id),
            )
            # 5. 硬删除智能体（级联删除 MCP/Skill 绑定）
            result = await conn.execute(
                "DELETE FROM user_agents WHERE id = $1 AND user_id = $2",
                _uuid(agent_id),
                user_id,
            )
            return result == "DELETE 1"


async def freeze_agent_subscribers(agent_id: str, owner_user_id: str) -> None:
    """切换为私有时冻结所有非创建者的会话并取消订阅。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE user_sessions SET subscription_frozen = TRUE, updated_at = CURRENT_TIMESTAMP
                WHERE agent_id = $1 AND agent_kind = 'subscription' AND user_id != $2
                """,
                _uuid(agent_id),
                owner_user_id,
            )
            await conn.execute(
                """
                UPDATE user_agent_subscriptions
                SET status = 'cancelled', cancelled_at = CURRENT_TIMESTAMP
                WHERE agent_id = $1 AND status = 'active' AND user_id != $2
                """,
                _uuid(agent_id),
                owner_user_id,
            )


async def set_agent_mcp_bindings(
    agent_id: str, mcp_ids: List[int], *, mcp_names: Dict[int, str] | None = None
) -> None:
    """写入 MCP 绑定，可选存储名称快照。"""
    pool = await db_manager.get_pool()
    names = mcp_names or {}
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM user_agent_mcp_bindings WHERE agent_id = $1",
                _uuid(agent_id),
            )
            for mid in mcp_ids:
                name = (names.get(int(mid)) or "").strip() or None
                await conn.execute(
                    """
                    INSERT INTO user_agent_mcp_bindings (agent_id, mcp_id, snapshot_mcp_name)
                    VALUES ($1, $2, $3)
                    """,
                    _uuid(agent_id),
                    int(mid),
                    name,
                )
            await conn.execute(
                """
                UPDATE user_agents SET config_version = config_version + 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = $1
                """,
                _uuid(agent_id),
            )


async def get_agent_mcp_ids(agent_id: str) -> List[int]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT mcp_id FROM user_agent_mcp_bindings WHERE agent_id = $1",
            _uuid(agent_id),
        )
        return [r["mcp_id"] for r in rows]


async def get_agent_mcp_snapshot_names(agent_id: str) -> Dict[int, str]:
    """获取智能体绑定的 MCP 名称快照 {mcp_id: snapshot_mcp_name}。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT mcp_id, snapshot_mcp_name FROM user_agent_mcp_bindings WHERE agent_id = $1",
            _uuid(agent_id),
        )
        return {
            r["mcp_id"]: r["snapshot_mcp_name"]
            for r in rows
            if r.get("snapshot_mcp_name")
        }


async def get_or_create_default_agent(user_id: str) -> str:
    """已废弃：默认 Agent 不再落 user_agents 表。保留函数签名供旧代码路径，返回空字符串。"""
    return ""


async def bump_agent_config_version(agent_id: str) -> None:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE user_agents SET config_version = config_version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = $1
            """,
            _uuid(agent_id),
        )


# ==================== Installed skills ====================

async def install_skill_record(
    user_id: str,
    market_skill_id: str,
    skill_name: str,
    install_dir: str,
    version: str = "1.0.0",
) -> str:
    skill_id = str(uuid4())
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_installed_skills
            (id, user_id, market_skill_id, skill_name, install_dir, version)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (user_id, market_skill_id) DO UPDATE SET
                skill_name = EXCLUDED.skill_name,
                install_dir = EXCLUDED.install_dir,
                version = EXCLUDED.version,
                installed_at = CURRENT_TIMESTAMP
            RETURNING id
            """,
            _uuid(skill_id),
            user_id,
            market_skill_id,
            skill_name,
            install_dir,
            version,
        )
        row = await conn.fetchrow(
            "SELECT id FROM user_installed_skills WHERE user_id = $1 AND market_skill_id = $2",
            user_id,
            market_skill_id,
        )
        return str(row["id"]) if row else skill_id


async def increment_skill_install_count(market_skill_id: str) -> None:
    """累计安装次数 +1（每次成功安装/升级计一次）。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE skill_catalog
            SET install_count = COALESCE(install_count, 0) + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE skill_id = $1
            """,
            market_skill_id,
        )


async def list_installed_skills(user_id: str) -> List[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, market_skill_id, skill_name, install_dir, version, installed_at
            FROM user_installed_skills WHERE user_id = $1 ORDER BY installed_at DESC
            """,
            user_id,
        )
        return [
            {**dict(r), "id": str(r["id"])} for r in rows
        ]


async def delete_installed_skill(user_id: str, market_skill_id: str) -> bool:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM user_installed_skills WHERE user_id = $1 AND market_skill_id = $2",
            user_id,
            market_skill_id,
        )
        return result == "DELETE 1"


async def get_installed_skill(user_id: str, market_skill_id: str) -> Optional[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, market_skill_id, skill_name, install_dir, version, installed_at
            FROM user_installed_skills
            WHERE user_id = $1 AND market_skill_id = $2
            """,
            user_id,
            market_skill_id,
        )
        if not row:
            return None
        return {**dict(row), "id": str(row["id"])}


async def is_skill_bound_to_agents(user_id: str, market_skill_id: str) -> bool:
    """用户已安装的 skill 是否被订阅智能体绑定。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT 1 FROM user_agent_skill_bindings b
            JOIN user_installed_skills s ON s.id = b.installed_skill_id
            WHERE s.user_id = $1 AND s.market_skill_id = $2
            LIMIT 1
            """,
            user_id,
            market_skill_id,
        )
        return row is not None


# ==================== Skill catalog (market) ====================

async def upsert_skill_catalog(
    skill_id: str,
    name: str,
    description: str,
    version: str,
    tags: List[str],
    author: str,
    repo_rel_path: str,
    *,
    status: str = "published",
) -> None:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO skill_catalog (
                skill_id, name, description, version, tags, author, status, repo_rel_path
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (skill_id) DO UPDATE SET
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                version = EXCLUDED.version,
                tags = EXCLUDED.tags,
                author = EXCLUDED.author,
                repo_rel_path = EXCLUDED.repo_rel_path,
                status = CASE
                    WHEN skill_catalog.status = 'archived' AND EXCLUDED.status = 'published'
                    THEN 'published'
                    ELSE EXCLUDED.status
                END,
                updated_at = CURRENT_TIMESTAMP
            """,
            skill_id,
            name,
            description or "",
            version or "1.0.0",
            tags or [],
            author or "",
            status,
            repo_rel_path,
        )


async def get_skill_catalog(skill_id: str) -> Optional[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM skill_catalog WHERE skill_id = $1",
            skill_id,
        )
        return dict(row) if row else None


async def set_skill_catalog_status(skill_id: str, status: str) -> bool:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE skill_catalog SET status = $2, updated_at = CURRENT_TIMESTAMP
            WHERE skill_id = $1
            """,
            skill_id,
            status,
        )
        return result == "UPDATE 1"


async def list_skill_catalog_ids() -> List[str]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT skill_id FROM skill_catalog")
        return [r["skill_id"] for r in rows]


async def delete_missing_skills(present_ids: List[str]) -> int:
    """删除不在 present_ids 中的 catalog 记录（以 skill-repo 为准完全覆盖）。

    注意：present_ids 为空时跳过删除，防止 skill-repo 扫描异常导致全量清空。
    """
    if not present_ids:
        logger.warning(
            "[SkillSync] present_ids 为空，跳过删除操作 —— "
            "请检查 skill-repo 目录是否正确部署/挂载，或 SKILL_REPO_ROOT 配置是否正确"
        )
        return 0

    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM skill_catalog
            WHERE skill_id != ALL($1::text[])
            """,
            present_ids,
        )
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError):
            return 0


async def list_skill_catalog(
    *,
    page: int = 1,
    page_size: int = 20,
    q: str = "",
    tags: Optional[List[str]] = None,
    user_id: Optional[str] = None,
    status: str = "published",
) -> tuple[List[Dict[str, Any]], int]:
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    offset = (page - 1) * page_size

    conditions = ["c.status = $1"]
    params: List[Any] = [status]
    idx = 2

    if q:
        conditions.append(
            f"(c.skill_id ILIKE ${idx} OR c.name ILIKE ${idx} OR c.description ILIKE ${idx})"
        )
        params.append(f"%{q}%")
        idx += 1

    if tags:
        conditions.append(f"c.tags && ${idx}::text[]")
        params.append(tags)
        idx += 1

    where = " AND ".join(conditions)
    user_join = ""
    subscribed_col = "FALSE AS subscribed"
    if user_id:
        user_join = f"""
            LEFT JOIN user_installed_skills uis
              ON uis.market_skill_id = c.skill_id AND uis.user_id = ${idx}
        """
        params.append(user_id)
        idx += 1
        subscribed_col = "(uis.id IS NOT NULL) AS subscribed"

    count_sql = f"""
        SELECT COUNT(*) AS cnt FROM skill_catalog c
        {user_join}
        WHERE {where}
    """
    list_sql = f"""
        SELECT
            c.skill_id,
            c.name,
            c.description,
            c.version,
            c.tags,
            c.author,
            c.status,
            c.repo_rel_path,
            c.created_at,
            c.updated_at,
            COALESCE(c.install_count, 0) AS install_count,
            (SELECT COUNT(*)::int FROM user_installed_skills s
             WHERE s.market_skill_id = c.skill_id) AS subscribe_count,
            {subscribed_col}
        FROM skill_catalog c
        {user_join}
        WHERE {where}
        ORDER BY c.updated_at DESC, c.skill_id
        LIMIT ${idx} OFFSET ${idx + 1}
    """
    params.extend([page_size, offset])

    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        total_row = await conn.fetchrow(count_sql, *params[: idx - 1])
        total = int(total_row["cnt"]) if total_row else 0
        rows = await conn.fetch(list_sql, *params)
        items = []
        for r in rows:
            d = dict(r)
            if d.get("created_at"):
                d["created_at"] = d["created_at"].isoformat()
            if d.get("updated_at"):
                d["updated_at"] = d["updated_at"].isoformat()
            d["tags"] = list(d.get("tags") or [])
            items.append(d)
        return items, total


async def list_skill_catalog_tags(status: str = "published") -> List[str]:
    """返回 skill 市场中所有不重复的分类标签。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT tag
            FROM skill_catalog c, unnest(c.tags) AS tag
            WHERE c.status = $1
              AND tag IS NOT NULL
              AND btrim(tag) != ''
            ORDER BY tag
            """,
            status,
        )
    return [str(r["tag"]) for r in rows]


async def list_user_subscribed_skills(user_id: str) -> List[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                uis.id AS installed_skill_id,
                uis.market_skill_id AS skill_id,
                uis.skill_name,
                uis.install_dir,
                uis.version AS installed_version,
                uis.installed_at,
                c.name,
                c.description,
                c.version AS latest_version,
                c.tags,
                c.author,
                c.status AS catalog_status,
                c.updated_at AS catalog_updated_at
            FROM user_installed_skills uis
            LEFT JOIN skill_catalog c ON c.skill_id = uis.market_skill_id
            WHERE uis.user_id = $1
            ORDER BY uis.installed_at DESC
            """,
            user_id,
        )
        result = []
        for r in rows:
            d = dict(r)
            if d.get("installed_at"):
                d["installed_at"] = d["installed_at"].isoformat()
            if d.get("catalog_updated_at"):
                d["catalog_updated_at"] = d["catalog_updated_at"].isoformat()
            d["tags"] = list(d.get("tags") or [])
            latest = d.get("latest_version") or d.get("installed_version") or "1.0.0"
            installed = d.get("installed_version") or "1.0.0"
            from deep_agent_server.skills.version_utils import version_is_newer

            d["has_update"] = bool(
                d.get("catalog_status") == "published"
                and version_is_newer(latest, installed)
            )
            d["latest_version"] = latest
            result.append(d)
        return result


async def get_agent_skill_install_dirs(
    agent_id: str,
    creator_user_id: str,
) -> List[str]:
    """返回 Agent 绑定的 skill install_dir；优先 snapshot，否则 JOIN 创建者安装记录。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT COALESCE(b.snapshot_install_dir, s.install_dir) AS install_dir
            FROM user_agent_skill_bindings b
            LEFT JOIN user_installed_skills s ON s.id = b.installed_skill_id
            JOIN user_agents a ON a.id = b.agent_id
            WHERE b.agent_id = $1 AND a.user_id = $2
            ORDER BY COALESCE(b.snapshot_skill_name, s.skill_name)
            """,
            _uuid(agent_id),
            creator_user_id,
        )
        return [r["install_dir"] for r in rows if r.get("install_dir")]


async def set_agent_skill_bindings(
    agent_id: str,
    creator_user_id: str,
    market_skill_ids: List[str],
    *,
    skill_names: Dict[str, str] | None = None,
) -> List[str]:
    """绑定 skill（从能力市场选择）并写入快照；返回绑定的 market_skill_id 列表。"""
    names = skill_names or {}
    pool = await db_manager.get_pool()
    bound: List[str] = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM user_agent_skill_bindings WHERE agent_id = $1",
                _uuid(agent_id),
            )
            for sid in market_skill_ids:
                sid = (sid or "").strip()
                if not sid:
                    continue
                # 名称优先级：前端传入 > skill_catalog > id 兜底
                skill_name = (names.get(sid) or "").strip()
                if not skill_name:
                    cat = await conn.fetchrow(
                        "SELECT name FROM skill_catalog WHERE skill_id = $1", sid
                    )
                    skill_name = cat["name"] if cat else sid

                # 尝试获取创建者的安装记录（若已安装则关联，用于卸载时的绑定检查）
                installed_row = await conn.fetchrow(
                    """SELECT id FROM user_installed_skills
                       WHERE user_id = $1 AND market_skill_id = $2""",
                    creator_user_id,
                    sid,
                )
                installed_id = installed_row["id"] if installed_row else None

                # snapshot_install_dir 记录实际运行时路径
                from deep_agent_server.workspace import get_agent_skills_dir
                agent_skill_dir = str(get_agent_skills_dir(agent_id) / sid)

                await conn.execute(
                    """
                    INSERT INTO user_agent_skill_bindings (
                        agent_id, installed_skill_id, snapshot_install_dir,
                        snapshot_skill_name, market_skill_id
                    ) VALUES ($1, $2, $3, $4, $5)
                    """,
                    _uuid(agent_id),
                    installed_id,
                    agent_skill_dir,
                    skill_name,
                    sid,
                )
                bound.append(sid)
            await conn.execute(
                """
                UPDATE user_agents SET config_version = config_version + 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = $1
                """,
                _uuid(agent_id),
            )
    return bound


async def get_agent_skill_binding_ids(agent_id: str) -> List[str]:
    """返回智能体绑定的 market_skill_id 列表。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT market_skill_id FROM user_agent_skill_bindings WHERE agent_id = $1",
            _uuid(agent_id),
        )
        return [r["market_skill_id"] for r in rows if r.get("market_skill_id")]


async def get_agent_skill_names(agent_id: str) -> List[Dict[str, str]]:
    """获取智能体绑定的 Skill 名称列表。

    名称优先级：snapshot_skill_name > skill_catalog.name > market_skill_id 兜底。
    """
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT b.market_skill_id, b.snapshot_skill_name, c.name AS catalog_name
               FROM user_agent_skill_bindings b
               LEFT JOIN skill_catalog c ON c.skill_id = b.market_skill_id
               WHERE b.agent_id = $1""",
            _uuid(agent_id),
        )
        result: List[Dict[str, str]] = []
        for r in rows:
            mid = r["market_skill_id"] or ""
            name = r["snapshot_skill_name"] or r["catalog_name"] or mid
            result.append({"installed_skill_id": mid, "skill_name": name})
        return result


async def get_agent_market_skill_ids(agent_id: str) -> List[str]:
    """获取智能体绑定的 market_skill_id 列表（直接读 market_skill_id 列）。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT market_skill_id FROM user_agent_skill_bindings WHERE agent_id = $1",
            _uuid(agent_id),
        )
        return [r["market_skill_id"] for r in rows if r.get("market_skill_id")]


async def list_all_subscription_agent_ids() -> List[str]:
    """返回所有未删除的订阅智能体 ID。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id FROM user_agents
            WHERE is_deleted = FALSE
            ORDER BY created_at
            """
        )
        return [str(r["id"]) for r in rows]


# ==================== Sandbox LRU ====================

async def get_sandbox_session(user_id: str, session_id: str) -> Optional[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT user_id, session_id::text AS session_id, container_id, status,
                   provider, last_active_at, ttl_at, mounted_workspace_path, created_at,
                   selection_key, mounted_skill_ids
            FROM deep_sandbox_sessions
            WHERE user_id = $1 AND session_id = $2::uuid
            """,
            user_id,
            session_id,
        )
        return dict(row) if row else None


async def list_sandbox_sessions_for_user(user_id: str) -> List[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT user_id, session_id::text AS session_id, container_id, status,
                   last_active_at, ttl_at, mounted_workspace_path,
                   selection_key, mounted_skill_ids
            FROM deep_sandbox_sessions
            WHERE user_id = $1
            ORDER BY last_active_at DESC NULLS LAST
            """,
            user_id,
        )
        return [dict(r) for r in rows]


async def count_running_sandboxes() -> int:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM deep_sandbox_sessions WHERE status = 'running'"
        ) or 0


async def count_running_sandboxes_for_user(user_id: str) -> int:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            SELECT COUNT(*) FROM deep_sandbox_sessions
            WHERE user_id = $1 AND status = 'running'
            """,
            user_id,
        ) or 0


async def list_running_session_ids_for_user(user_id: str) -> List[str]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT session_id::text AS session_id
            FROM deep_sandbox_sessions
            WHERE user_id = $1 AND status = 'running'
            ORDER BY last_active_at ASC NULLS FIRST
            """,
            user_id,
        )
        return [r["session_id"] for r in rows]


async def list_running_sandboxes_by_lru(limit: int = 1) -> List[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT user_id, session_id::text AS session_id, container_id, last_active_at
            FROM deep_sandbox_sessions
            WHERE status = 'running'
            ORDER BY last_active_at ASC NULLS FIRST
            LIMIT $1
            """,
            limit,
        )
        return [dict(r) for r in rows]


async def list_expired_sandbox_sessions(limit: int = 50) -> List[Dict[str, Any]]:
    """返回 ttl_at 已过期的沙箱记录（覆盖所有非终态）。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT user_id, session_id::text AS session_id, container_id, status
            FROM deep_sandbox_sessions
            WHERE ttl_at IS NOT NULL
              AND ttl_at < CURRENT_TIMESTAMP
              AND status IN ('running', 'paused', 'stopped', 'created', 'exited')
            ORDER BY ttl_at ASC
            LIMIT $1
            """,
            limit,
        )
        return [dict(r) for r in rows]


async def list_all_sandbox_container_ids() -> List[str]:
    """返回 DB 中登记的全部沙箱容器 ID。

    供孤儿清理判断"Docker 里这个容器在 DB 是否有记录"——这是孤儿容器的
    正确判据，而容器自身的 running/stopped 状态不能用来判断。
    """
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT container_id
            FROM deep_sandbox_sessions
            WHERE container_id IS NOT NULL
            """
        )
        return [r["container_id"] for r in rows]


async def clear_sandbox_session(user_id: str, session_id: str) -> None:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM deep_sandbox_sessions WHERE user_id = $1 AND session_id = $2::uuid",
            user_id,
            session_id,
        )


async def clear_user_sandbox_sessions(user_id: str) -> None:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM deep_sandbox_sessions WHERE user_id = $1",
            user_id,
        )


# ==================== Session readiness ====================

async def upsert_session_start_task(
    session_id: str,
    user_id: str,
    status: str,
    error: str = None,
) -> None:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO session_start_tasks (session_id, user_id, status, error, updated_at)
            VALUES ($1, $2, $3, $4, CURRENT_TIMESTAMP)
            ON CONFLICT (session_id) DO UPDATE SET
                status = EXCLUDED.status,
                error = EXCLUDED.error,
                updated_at = CURRENT_TIMESTAMP
            """,
            _uuid(session_id),
            user_id,
            status,
            error,
        )


async def get_session_start_task(session_id: str) -> Optional[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM session_start_tasks WHERE session_id = $1",
            _uuid(session_id),
        )
        return dict(row) if row else None


async def add_pending_message(session_id: str, user_id: str, payload: dict) -> str:
    msg_id = str(uuid4())
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pending_messages (id, session_id, user_id, payload)
            VALUES ($1, $2, $3, $4::jsonb)
            """,
            _uuid(msg_id),
            _uuid(session_id),
            user_id,
            json.dumps(payload),
        )
    return msg_id


async def count_pending_messages(session_id: str) -> int:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM pending_messages WHERE session_id = $1",
            _uuid(session_id),
        ) or 0


async def get_pending_messages(session_id: str) -> List[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, payload, created_at
            FROM pending_messages
            WHERE session_id = $1
            ORDER BY created_at ASC
            """,
            _uuid(session_id),
        )
        result = []
        for r in rows:
            payload = r["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            result.append({"id": str(r["id"]), "payload": payload, "created_at": r["created_at"]})
        return result


async def delete_pending_messages(session_id: str) -> int:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM pending_messages WHERE session_id = $1",
            _uuid(session_id),
        )
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError):
            return 0


async def get_session_agent_id(session_id: str) -> Optional[str]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT agent_id FROM user_sessions WHERE session_id = $1",
            _uuid(session_id),
        )
        return str(row["agent_id"]) if row and row["agent_id"] else None


async def cleanup_deep_session_data(user_id: str, session_id: str) -> None:
    """永久删除 session 时清理 Deep 相关 DB 记录。"""
    sid = _uuid(session_id)
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM deep_workspace_files
            WHERE user_id = $1 AND session_id = $2
            """,
            user_id,
            sid,
        )
        await conn.execute(
            "DELETE FROM deep_subagent_runs WHERE session_id = $1",
            sid,
        )
        await conn.execute(
            "DELETE FROM agent_runs WHERE user_id = $1 AND session_id = $2",
            user_id,
            sid,
        )
        await conn.execute(
            "DELETE FROM deep_audit_logs WHERE user_id = $1 AND session_id = $2",
            user_id,
            sid,
        )
        await conn.execute(
            "DELETE FROM pending_messages WHERE session_id = $1",
            sid,
        )
        await conn.execute(
            "DELETE FROM session_start_tasks WHERE session_id = $1",
            sid,
        )
        # HTML 分享 token 联动清理（快照目录已随 remove_session_workspace 的 rmtree 删除）
        await conn.execute(
            "DELETE FROM html_share_tokens WHERE user_id = $1 AND session_id = $2",
            user_id,
            str(sid),
        )


# ==================== User secrets ====================

LLM_SECRET_KEYS = ("llm_api_key", "llm_base_url", "llm_model")
LLM_FAST_SECRET_KEYS = ("llm_fast_api_key", "llm_fast_base_url", "llm_fast_model")


def mask_secret_value(value: str) -> str:
    """脱敏展示 secrets（仅用于 API 响应，不返回明文）。"""
    if not value:
        return ""
    v = value.strip()
    if len(v) <= 4:
        return "****"
    return f"{v[:2]}****{v[-4:]}"


async def set_user_secret(user_id: str, secret_key: str, plaintext: str) -> None:
    enc = plaintext if plaintext is not None else ""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_secrets (user_id, secret_key, secret_value_enc, updated_at)
            VALUES ($1, $2, $3, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id, secret_key) DO UPDATE SET
                secret_value_enc = EXCLUDED.secret_value_enc,
                updated_at = CURRENT_TIMESTAMP
            """,
            user_id,
            secret_key,
            enc,
        )


async def get_user_secret(user_id: str, secret_key: str) -> Optional[str]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT secret_value_enc FROM user_secrets WHERE user_id = $1 AND secret_key = $2",
            user_id,
            secret_key,
        )
        if not row:
            return None
        return row["secret_value_enc"]


async def delete_user_secret(user_id: str, secret_key: str) -> None:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM user_secrets WHERE user_id = $1 AND secret_key = $2",
            user_id,
            secret_key,
        )


async def _fetch_user_secret_keys(user_id: str, keys: tuple) -> set:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT secret_key FROM user_secrets
            WHERE user_id = $1 AND secret_key = ANY($2::text[])
            """,
            user_id,
            list(keys),
        )
    return {r["secret_key"] for r in rows}


async def get_user_llm_config(user_id: str) -> dict:
    keys_present = await _fetch_user_secret_keys(user_id, LLM_SECRET_KEYS + LLM_FAST_SECRET_KEYS)

    async def _get(key: str) -> Optional[str]:
        if key not in keys_present:
            return None
        return await get_user_secret(user_id, key)

    main = {
        "model": (await _get("llm_model")) or "",
        "base_url": (await _get("llm_base_url")) or "",
        "has_api_key": "llm_api_key" in keys_present,
    }
    main["configured"] = bool(main["model"] and main["base_url"] and main["has_api_key"])
    main["api_key_masked"] = ""
    if main["has_api_key"]:
        main["api_key_masked"] = mask_secret_value((await _get("llm_api_key")) or "")

    fast = {
        "model": (await _get("llm_fast_model")) or "",
        "base_url": (await _get("llm_fast_base_url")) or "",
        "has_api_key": "llm_fast_api_key" in keys_present,
    }
    fast["configured"] = bool(fast["model"] and fast["base_url"] and fast["has_api_key"])
    fast["api_key_masked"] = ""
    if fast["has_api_key"]:
        fast["api_key_masked"] = mask_secret_value((await _get("llm_fast_api_key")) or "")

    return {
        **main,
        "fast": fast,
    }


async def has_user_llm_config(user_id: str) -> bool:
    cfg = await get_user_llm_config(user_id)
    return bool(cfg.get("configured"))


async def set_user_llm_config(
    user_id: str,
    model: str,
    base_url: str,
    api_key: Optional[str] = None,
) -> None:
    await set_user_secret(user_id, "llm_model", (model or "").strip())
    await set_user_secret(user_id, "llm_base_url", (base_url or "").strip())
    if api_key is not None and str(api_key).strip():
        await set_user_secret(user_id, "llm_api_key", str(api_key).strip())


async def clear_user_fast_llm_config(user_id: str) -> None:
    for key in LLM_FAST_SECRET_KEYS:
        await delete_user_secret(user_id, key)


async def set_user_fast_llm_config(
    user_id: str,
    *,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    clear: bool = False,
) -> None:
    """更新可选快速模型；clear=True 时删除全部 fast secrets。"""
    if clear:
        await clear_user_fast_llm_config(user_id)
        return
    if model is not None:
        if str(model).strip():
            await set_user_secret(user_id, "llm_fast_model", str(model).strip())
        else:
            await delete_user_secret(user_id, "llm_fast_model")
    if base_url is not None:
        if str(base_url).strip():
            await set_user_secret(user_id, "llm_fast_base_url", str(base_url).strip())
        else:
            await delete_user_secret(user_id, "llm_fast_base_url")
    if api_key is not None and str(api_key).strip():
        await set_user_secret(user_id, "llm_fast_api_key", str(api_key).strip())


async def resolve_user_aux_llm_credentials(user_id: str) -> tuple[str, str, str]:
    """快速模型已完整配置则用 fast，否则回退当前生效主模型（user_llm_profiles）。"""
    await ensure_user_llm_profile(user_id)
    profile = await get_active_user_llm_profile(user_id)
    if not profile or not profile["model"] or not profile["base_url"] or not profile["api_key"]:
        raise ValueError("请先配置主模型 API Key 与 Base URL")
    main_model = profile["model"]
    main_base = profile["base_url"]
    main_key = profile["api_key"]

    fast_model = await get_user_secret(user_id, "llm_fast_model")
    fast_base = await get_user_secret(user_id, "llm_fast_base_url")
    fast_key = await get_user_secret(user_id, "llm_fast_api_key")
    if fast_model and fast_base and fast_key:
        return fast_model.strip(), fast_base.strip(), fast_key.strip()
    return main_model.strip(), main_base.strip(), main_key.strip()


# ==================== User LLM profiles ====================

async def set_user_llm_active_profile(
    user_id: str,
    model: str,
    base_url: str,
    api_key: str,
) -> None:
    """将 (model, base_url) 组合设为当前生效配置，取消该用户其他组合的 active。

    组合已存在时保留旧 api_key（api_key 为空）；新组合写入 api_key。
    """
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE user_llm_profiles SET is_active = FALSE WHERE user_id = $1",
            user_id,
        )
        await conn.execute(
            """
            INSERT INTO user_llm_profiles (user_id, model, base_url, api_key, is_active)
            VALUES ($1, $2, $3, $4, TRUE)
            ON CONFLICT (user_id, model, base_url) DO UPDATE SET
                is_active = TRUE,
                api_key = CASE WHEN EXCLUDED.api_key <> '' THEN EXCLUDED.api_key
                               ELSE user_llm_profiles.api_key END,
                updated_at = now()
            """,
            user_id,
            (model or "").strip(),
            (base_url or "").strip(),
            (api_key or "").strip(),
        )


async def get_active_user_llm_profile(user_id: str) -> Optional[dict]:
    """返回当前生效组合 {model, base_url, api_key}；无 active 返回 None。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT model, base_url, api_key
            FROM user_llm_profiles
            WHERE user_id = $1 AND is_active = TRUE
            LIMIT 1
            """,
            user_id,
        )
    if not row:
        return None
    return {"model": row["model"], "base_url": row["base_url"], "api_key": row["api_key"]}


async def get_user_llm_history(user_id: str) -> dict:
    """返回用户全部配置组合的历史数据（供配置页展示与自由组合判断）。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT model, base_url, api_key, is_active
            FROM user_llm_profiles
            WHERE user_id = $1
            ORDER BY updated_at DESC, id DESC
            """,
            user_id,
        )
    models: List[str] = []
    base_urls: List[str] = []
    combo_has_key: Dict[str, bool] = {}
    active = None
    active_key_masked = ""
    active_has_key = False
    for r in rows:
        m = r["model"]
        b = r["base_url"]
        if m not in models:
            models.append(m)
        if b not in base_urls:
            base_urls.append(b)
        combo_has_key[f"{m}|{b}"] = bool(r["api_key"])
        if r["is_active"] and active is None:
            active = {"model": m, "base_url": b}
            active_has_key = bool(r["api_key"])
            if r["api_key"]:
                active_key_masked = mask_secret_value(r["api_key"])
    return {
        "models": models,
        "base_urls": base_urls,
        "active": active,
        "api_key_masked": active_key_masked,
        "has_api_key": active_has_key,
        "combo_has_key": combo_has_key,
        "configured": bool(active and active_has_key),
    }


async def get_user_llm_combo_key(user_id: str, model: str, base_url: str) -> str:
    """返回指定 (model, base_url) 组合已存的 api_key；未存返回空串。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT api_key FROM user_llm_profiles
            WHERE user_id = $1 AND model = $2 AND base_url = $3
            LIMIT 1
            """,
            user_id,
            model,
            base_url,
        )
    if not row:
        return ""
    return row["api_key"] or ""


async def delete_user_llm_value(user_id: str, value_type: str, value: str) -> None:
    """删除引用到某历史 model 或 base_url 的所有配置组合。

    若删除了 active 行，将剩余组合中最近更新的一个设为 active；无剩余则 active 置空。
    """
    if value_type not in ("model", "base_url"):
        raise ValueError(f"value_type 必须是 model 或 base_url，收到: {value_type}")
    column = "model" if value_type == "model" else "base_url"
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            f"DELETE FROM user_llm_profiles WHERE user_id = $1 AND {column} = $2",
            user_id,
            value,
        )
        row = await conn.fetchrow(
            """
            SELECT COUNT(*) AS c,
                   COALESCE(MAX(CASE WHEN is_active THEN 1 ELSE 0 END), 0) AS has_active
            FROM user_llm_profiles
            WHERE user_id = $1
            """,
            user_id,
        )
        if row and row["c"] > 0 and not row["has_active"]:
            await conn.execute(
                """
                UPDATE user_llm_profiles SET is_active = TRUE
                WHERE user_id = $1 AND id = (
                    SELECT id FROM user_llm_profiles
                    WHERE user_id = $1
                    ORDER BY updated_at DESC, id DESC
                    LIMIT 1
                )
                """,
                user_id,
            )


async def ensure_user_llm_profile(user_id: str) -> None:
    """惰性幂等兜底：profiles 为空且旧 user_secrets 有主模型时迁移为 active 组合。

    用 ON CONFLICT DO NOTHING 保证并发安全；所有运行时读取点与配置接口统一调用。
    """
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) AS c FROM user_llm_profiles WHERE user_id = $1",
            user_id,
        )
        if row and row["c"] > 0:
            return
    old_model = (await get_user_secret(user_id, "llm_model") or "").strip()
    old_base = (await get_user_secret(user_id, "llm_base_url") or "").strip()
    old_key = (await get_user_secret(user_id, "llm_api_key") or "").strip()
    if not old_model or not old_base or not old_key:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_llm_profiles (user_id, model, base_url, api_key, is_active)
            VALUES ($1, $2, $3, $4, TRUE)
            ON CONFLICT (user_id, model, base_url) DO NOTHING
            """,
            user_id,
            old_model,
            old_base,
            old_key,
        )


# ==================== Subscriptions ====================

async def is_subscription_active(user_id: str, agent_id: str) -> bool:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT 1 FROM user_agent_subscriptions
            WHERE user_id = $1 AND agent_id = $2 AND status = 'active'
            """,
            user_id,
            _uuid(agent_id),
        )
        return row is not None


async def subscribe_agent(user_id: str, agent_id: str) -> None:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO user_agent_subscriptions (user_id, agent_id, status, subscribed_at, cancelled_at)
                VALUES ($1, $2, 'active', CURRENT_TIMESTAMP, NULL)
                ON CONFLICT (user_id, agent_id) DO UPDATE SET
                    status = 'active',
                    subscribed_at = CURRENT_TIMESTAMP,
                    cancelled_at = NULL
                """,
                user_id,
                _uuid(agent_id),
            )
            await conn.execute(
                """
                UPDATE user_sessions
                SET subscription_frozen = FALSE, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = $1 AND agent_id = $2 AND agent_kind = 'subscription'
                  AND is_deleted = FALSE
                """,
                user_id,
                _uuid(agent_id),
            )


async def unsubscribe_agent(user_id: str, agent_id: str) -> None:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE user_agent_subscriptions
                SET status = 'cancelled', cancelled_at = CURRENT_TIMESTAMP
                WHERE user_id = $1 AND agent_id = $2
                """,
                user_id,
                _uuid(agent_id),
            )
            await conn.execute(
                """
                UPDATE user_sessions
                SET subscription_frozen = TRUE, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = $1 AND agent_id = $2 AND agent_kind = 'subscription'
                """,
                user_id,
                _uuid(agent_id),
            )


def _agent_row_to_dict(row) -> Dict[str, Any]:
    d = dict(row)
    d["id"] = str(d["id"])
    if isinstance(d.get("config"), str):
        d["config"] = json.loads(d["config"])
    return d


async def get_user_agent_name(agent_id: str) -> Optional[str]:
    """查询用户智能体名称（订阅会话标题固定用）。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT name FROM user_agents WHERE id = $1",
            _uuid(agent_id),
        )


async def resolve_agent_for_caller(
    agent_id: str,
    caller_user_id: str,
    *,
    for_runtime: bool = False,
    include_prompt: bool = False,
) -> Optional[Dict[str, Any]]:
    """跨用户读取订阅 Agent：owner / active 订阅者可读；catalog 不含 prompt。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM user_agents WHERE id = $1",
            _uuid(agent_id),
        )
        if not row:
            return None
        agent = _agent_row_to_dict(row)
        is_owner = agent["user_id"] == caller_user_id
        is_subscribed = await is_subscription_active(caller_user_id, agent_id)
        is_public = agent.get("visibility") == "public" and not agent.get("is_deleted")

        if agent.get("is_deleted") and not is_owner and not is_subscribed:
            return None

        if for_runtime or include_prompt:
            if not (is_owner or is_subscribed):
                return None
        elif not (is_owner or is_subscribed or is_public):
            return None

        if not include_prompt and not for_runtime and not is_owner and not is_subscribed:
            agent.pop("system_prompt", None)

        agent["is_owner"] = is_owner
        agent["is_subscribed"] = is_subscribed
        agent["archived"] = bool(agent.get("is_deleted"))
        return agent


async def list_subscription_catalog(
    user_id: str,
    tab: str,
    q: str = "",
) -> List[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    params: List[Any] = []
    where_parts = []
    idx = 1

    if tab == "mine":
        where_parts.append(f"a.user_id = ${idx}")
        params.append(user_id)
        idx += 1
    else:
        where_parts.append("a.is_deleted = FALSE")

    if tab == "subscribed":
        where_parts.append(f"""
            EXISTS (
                SELECT 1 FROM user_agent_subscriptions s
                WHERE s.agent_id = a.id AND s.user_id = ${idx} AND s.status = 'active'
            )
        """)
        params.append(user_id)
        idx += 1
    elif tab == "all":
        where_parts.append("a.visibility = 'public'")
    # tab == "mine": 不加 visibility 过滤，创建者可以看到自己公开和私有的智能体

    if q:
        where_parts.append(f"a.name ILIKE ${idx}")
        params.append(f"%{q}%")
        idx += 1

    where_sql = " AND ".join(where_parts)
    sql = f"""
        SELECT a.id, a.user_id, a.name, a.description, a.visibility, a.config_version,
               a.updated_at, a.is_deleted, a.avatar_color,
               (SELECT COUNT(*) FROM user_agent_subscriptions s
                WHERE s.agent_id = a.id AND s.status = 'active') AS subscriber_count,
               (SELECT COUNT(DISTINCT r.user_id) FROM agent_runs r WHERE r.agent_id = a.id) AS used_user_count,
               EXISTS (
                   SELECT 1 FROM user_agent_subscriptions s
                   WHERE s.agent_id = a.id AND s.user_id = ${idx} AND s.status = 'active'
               ) AS is_subscribed
        FROM user_agents a
        WHERE {where_sql}
        ORDER BY a.updated_at DESC
    """
    params.append(user_id)
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    result = []
    for row in rows:
        d = dict(row)
        d["id"] = str(d["id"])
        d["creator_user_id"] = d.pop("user_id")
        d["archived"] = bool(d.get("is_deleted"))
        if tab != "mine" and not d.get("is_subscribed") and d["creator_user_id"] != user_id:
            pass
        result.append(d)
    return result


async def list_used_subscription_agents(user_id: str) -> List[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (s.agent_id)
                s.session_id, s.agent_id, s.last_active_at, s.subscription_frozen,
                a.name AS agent_name, a.description, a.is_deleted AS agent_deleted,
                a.user_id AS creator_user_id,
                sub.status AS subscription_status
            FROM user_sessions s
            JOIN user_agents a ON a.id = s.agent_id
            LEFT JOIN user_agent_subscriptions sub
                ON sub.user_id = s.user_id AND sub.agent_id = s.agent_id
            WHERE s.user_id = $1 AND s.agent_kind = 'subscription' AND s.is_deleted = FALSE
            ORDER BY s.agent_id, s.last_active_at DESC
            """,
            user_id,
        )
    out = []
    for row in rows:
        d = dict(row)
        d["session_id"] = str(d["session_id"])
        d["agent_id"] = str(d["agent_id"])
        d["archived"] = bool(d.get("agent_deleted"))
        d["read_only"] = bool(
            d.get("subscription_frozen")
            or d.get("agent_deleted")
            or d.get("subscription_status") == "cancelled"
        )
        out.append(d)
    return sorted(out, key=lambda x: x.get("last_active_at") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


async def ensure_subscription_session(user_id: str, agent_id: str) -> Dict[str, Any]:
    from db import storage

    agent = await resolve_agent_for_caller(agent_id, user_id, for_runtime=True, include_prompt=True)

    # 非 owner 非订阅用户：检查是否为公开智能体（公开智能体自动订阅）
    if not agent:
        # resolve_agent_for_caller 仅对 owner/订阅用户返回，此处检查公开智能体
        agent = await resolve_agent_for_caller(agent_id, user_id, for_runtime=False, include_prompt=False)
        if not agent:
            raise ValueError("智能体不存在或无权访问")
        # 私有智能体：非 owner 用户不可调用
        is_public = agent.get("visibility") == "public" and not agent.get("is_deleted")
        if not is_public:
            raise ValueError("该智能体为私有，仅创建者可调用")
        # 公开智能体：自动订阅
        await subscribe_agent(user_id, agent_id)
        # 重新获取（已订阅，for_runtime=True 将返回完整信息）
        agent = await resolve_agent_for_caller(agent_id, user_id, for_runtime=True, include_prompt=True)
        if not agent:
            raise ValueError("智能体不存在或无权访问")

    if agent.get("archived"):
        raise ValueError("智能体已下架")

    is_owner = agent.get("user_id") == user_id
    is_public = agent.get("visibility") == "public" and not agent.get("is_deleted")

    # 私有智能体仅创建者可调用
    if not is_public and not is_owner:
        raise ValueError("该智能体为私有，仅创建者可调用")

    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT session_id, context_epoch, subscription_frozen
            FROM user_sessions
            WHERE user_id = $1 AND agent_id = $2 AND agent_kind = 'subscription'
              AND is_deleted = FALSE
            """,
            user_id,
            _uuid(agent_id),
        )
        if row:
            return {
                "session_id": str(row["session_id"]),
                "agent_id": agent_id,
                "agent_name": agent.get("name"),
                "context_epoch": row["context_epoch"],
                "subscription_frozen": row["subscription_frozen"],
                "is_new": False,
            }

    session_id = str(uuid4())
    await storage.upsert_user_session(
        user_id=user_id,
        session_id=session_id,
        session_name=agent.get("name") or "订阅智能体",
        agent_id=agent_id,
        agent_kind="subscription",
        subscription_frozen=False,
    )
    from deep_agent_server.workspace import ensure_session_dirs
    from config.config import app_config

    if (app_config.AGENT_RUNTIME or "deep").lower() != "light":
        await ensure_session_dirs(user_id, session_id)

    return {
        "session_id": session_id,
        "agent_id": agent_id,
        "agent_name": agent.get("name"),
        "context_epoch": 1,
        "subscription_frozen": False,
        "is_new": True,
    }


async def get_agent_stats(agent_id: str, owner_user_id: str) -> Optional[Dict[str, Any]]:
    agent = await get_user_agent(agent_id, owner_user_id)
    if not agent:
        return None
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        stats = await conn.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (WHERE status IN ('completed','error','stopped','interrupted')) AS terminal_runs,
                COUNT(*) FILTER (WHERE status = 'completed') AS completed_runs,
                COUNT(DISTINCT user_id) AS user_count,
                COALESCE(SUM(total_tokens), 0) AS total_tokens,
                COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) AS completion_tokens
            FROM agent_runs WHERE agent_id = $1
            """,
            _uuid(agent_id),
        )
    terminal = int(stats["terminal_runs"] or 0)
    completed = int(stats["completed_runs"] or 0)
    completion_rate = (completed / terminal) if terminal > 0 else 0.0
    return {
        "agent_id": agent_id,
        "user_count": int(stats["user_count"] or 0),
        "total_tokens": int(stats["total_tokens"] or 0),
        "prompt_tokens": int(stats["prompt_tokens"] or 0),
        "completion_tokens": int(stats["completion_tokens"] or 0),
        "completion_rate": round(completion_rate, 4),
        "terminal_runs": terminal,
        "completed_runs": completed,
    }


async def list_agent_runs_for_owner(
    agent_id: str,
    owner_user_id: str,
    limit: int = 20,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    agent = await get_user_agent(agent_id, owner_user_id)
    if not agent:
        return []
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT run_id, user_id, session_id, status, started_at, finished_at,
                   total_tokens, prompt_tokens, completion_tokens, llm_model, error
            FROM agent_runs
            WHERE agent_id = $1
            ORDER BY started_at DESC
            LIMIT $2 OFFSET $3
            """,
            _uuid(agent_id),
            limit,
            offset,
        )
    return [
        {
            **dict(r),
            "run_id": str(r["run_id"]),
            "session_id": str(r["session_id"]),
        }
        for r in rows
    ]


# ==================== Deep 运营统计 ====================

def _date_filter(column: str, start_date: str = None, end_date: str = None):
    clauses = []
    args = []
    idx = 1
    if start_date:
        clauses.append(f"{column} >= ${idx}::date")
        args.append(start_date)
        idx += 1
    if end_date:
        clauses.append(f"{column} < (${idx}::date + interval '1 day')")
        args.append(end_date)
        idx += 1
    where = (" AND " + " AND ".join(clauses)) if clauses else ""
    return where, args


async def get_deep_overview_stats(
    start_date: str = None,
    end_date: str = None,
) -> Dict[str, Any]:
    pool = await db_manager.get_pool()
    run_where, run_args = _date_filter("started_at", start_date, end_date)
    sub_where, sub_args = _date_filter("created_at", start_date, end_date)
    audit_where, audit_args = _date_filter("created_at", start_date, end_date)
    async with pool.acquire() as conn:
        deep_run_count = await conn.fetchval(
            f"SELECT COUNT(*) FROM agent_runs WHERE 1=1{run_where}",
            *run_args,
        )
        subagent_count = await conn.fetchval(
            f"SELECT COUNT(*) FROM deep_subagent_runs WHERE 1=1{sub_where}",
            *sub_args,
        )
        artifact_bytes = await conn.fetchval(
            """
            SELECT COALESCE(SUM(size_bytes), 0) FROM deep_workspace_files
            WHERE file_kind = 'artifact'
            """
        )
        audit_events_count = await conn.fetchval(
            f"SELECT COUNT(*) FROM deep_audit_logs WHERE 1=1{audit_where}",
            *audit_args,
        )
        sandbox_active_count = await conn.fetchval(
            "SELECT COUNT(*) FROM deep_sandbox_sessions WHERE status = 'running'"
        )
        workspace_rows = await conn.fetch(
            """
            SELECT user_id, workspace_bytes_used
            FROM user_resource_quotas
            ORDER BY workspace_bytes_used DESC
            LIMIT 50
            """
        )
        workspace_bytes_by_user = {
            r["user_id"]: r["workspace_bytes_used"] or 0 for r in workspace_rows
        }
    return {
        "deep_run_count": deep_run_count or 0,
        "subagent_count": subagent_count or 0,
        "artifact_bytes": artifact_bytes or 0,
        "workspace_bytes_by_user": workspace_bytes_by_user,
        "sandbox_active_count": sandbox_active_count or 0,
        "audit_events_count": audit_events_count or 0,
        "token_usage_aggregate": {},
    }


# ==================== 管理员表 ====================


async def is_admin(user_id: str) -> bool:
    """检查用户是否为 Skill 管理员。"""
    if not user_id:
        return False
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM admin_users WHERE user_id = $1", user_id
        )
        return row is not None


async def list_admins() -> List[Dict[str, Any]]:
    """列出所有管理员。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT user_id, role, created_by, created_at FROM admin_users ORDER BY created_at"
        )
        return [dict(r) for r in rows]


async def add_admin(user_id: str, created_by: str = "", role: str = "admin") -> None:
    """添加管理员。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO admin_users (user_id, role, created_by)
               VALUES ($1, $2, $3)
               ON CONFLICT (user_id) DO UPDATE SET role = EXCLUDED.role""",
            user_id, role, created_by,
        )


async def remove_admin(user_id: str) -> bool:
    """移除管理员。返回是否实际删除。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM admin_users WHERE user_id = $1", user_id
        )
        return result != "DELETE 0"


# ==================== 审批队列 ====================


async def create_pending_action(
    skill_id: str,
    action_type: str,
    requested_by: str,
    *,
    old_version: str = None,
    new_version: str = None,
    detail: dict = None,
) -> str:
    """创建待审批操作记录，返回 action_id。"""
    action_id = str(uuid4())
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO pending_skill_actions
               (id, skill_id, action_type, requested_by, old_version, new_version, detail)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            _uuid(action_id), skill_id, action_type, requested_by,
            old_version, new_version, json.dumps(detail or {}),
        )
        return action_id


async def list_pending_actions(
    *,
    page: int = 1,
    page_size: int = 20,
    status: str = "pending",
) -> tuple:
    """分页查询待审批操作列表。"""
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    offset = (page - 1) * page_size

    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM pending_skill_actions WHERE status = $1", status
        )
        rows = await conn.fetch(
            """SELECT p.*, c.name AS skill_name
               FROM pending_skill_actions p
               LEFT JOIN skill_catalog c ON c.skill_id = p.skill_id
               WHERE p.status = $1
               ORDER BY p.created_at DESC
               LIMIT $2 OFFSET $3""",
            status, page_size, offset,
        )
        items = []
        for r in rows:
            d = dict(r)
            if d.get("created_at"):
                d["created_at"] = d["created_at"].isoformat()
            if d.get("reviewed_at"):
                d["reviewed_at"] = d["reviewed_at"].isoformat()
            if d.get("detail") and isinstance(d["detail"], str):
                try:
                    d["detail"] = json.loads(d["detail"])
                except (json.JSONDecodeError, TypeError):
                    d["detail"] = {}
            items.append(d)
        return items, int(total or 0)


async def get_pending_action(action_id: str) -> Optional[Dict[str, Any]]:
    """查询单条审批记录。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM pending_skill_actions WHERE id = $1", action_id
        )
        return dict(row) if row else None


async def list_reviews_for_skill(skill_id: str) -> List[Dict[str, Any]]:
    """查询某个 skill 的所有审批记录。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM pending_skill_actions
               WHERE skill_id = $1 ORDER BY created_at DESC""",
            skill_id,
        )
        return [dict(r) for r in rows]


async def approve_action(action_id: str, reviewed_by: str) -> bool:
    """审批通过一个待审批操作。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """UPDATE pending_skill_actions
               SET status = 'approved', reviewed_by = $2, reviewed_at = CURRENT_TIMESTAMP
               WHERE id = $1 AND status = 'pending'""",
            action_id, reviewed_by,
        )
        return result == "UPDATE 1"


async def reject_action(action_id: str, reviewed_by: str, comment: str = "") -> bool:
    """审批拒绝一个待审批操作。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """UPDATE pending_skill_actions
               SET status = 'rejected', reviewed_by = $2, review_comment = $3,
                   reviewed_at = CURRENT_TIMESTAMP
               WHERE id = $1 AND status = 'pending'""",
            action_id, reviewed_by, comment,
        )
        return result == "UPDATE 1"


# ==================== Skill Catalog 扩展 ====================


async def upsert_skill_catalog_with_owner(
    skill_id: str,
    name: str,
    description: str,
    version: str,
    tags: List[str],
    author: str,
    repo_rel_path: str,
    *,
    creator_user_id: str = "",
    skill_type: str = "platform",
    source_path: str = "",
    file_size_bytes: int = 0,
    status: str = "published",
) -> None:
    """扩展版 upsert：支持 creator_user_id、skill_type、source_path、file_size_bytes。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO skill_catalog (
                skill_id, name, description, version, tags, author, status,
                repo_rel_path, creator_user_id, skill_type, source_path, file_size_bytes
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ON CONFLICT (skill_id) DO UPDATE SET
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                version = EXCLUDED.version,
                tags = EXCLUDED.tags,
                author = EXCLUDED.author,
                repo_rel_path = EXCLUDED.repo_rel_path,
                status = CASE
                    WHEN skill_catalog.status = 'archived' AND EXCLUDED.status = 'published'
                    THEN 'published'
                    WHEN skill_catalog.status = 'draft' AND EXCLUDED.status = 'draft'
                    THEN 'draft'
                    ELSE EXCLUDED.status
                END,
                creator_user_id = CASE
                    WHEN skill_catalog.creator_user_id = '' AND EXCLUDED.creator_user_id != ''
                    THEN EXCLUDED.creator_user_id
                    ELSE skill_catalog.creator_user_id
                END,
                skill_type = EXCLUDED.skill_type,
                source_path = EXCLUDED.source_path,
                file_size_bytes = EXCLUDED.file_size_bytes,
                updated_at = CURRENT_TIMESTAMP
            """,
            skill_id, name, description or "", version or "1.0.0",
            tags or [], author or "", status,
            repo_rel_path, creator_user_id, skill_type, source_path, file_size_bytes,
        )


async def list_skills_by_creator(creator_user_id: str) -> List[Dict[str, Any]]:
    """列出某个用户创建的所有 skill（不含内置 skill）。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM skill_catalog
               WHERE creator_user_id = $1
               ORDER BY updated_at DESC""",
            creator_user_id,
        )
        items = []
        for r in rows:
            d = dict(r)
            if d.get("created_at"):
                d["created_at"] = d["created_at"].isoformat()
            if d.get("updated_at"):
                d["updated_at"] = d["updated_at"].isoformat()
            d["tags"] = list(d.get("tags") or [])
            items.append(d)
        return items


async def delete_skill_catalog_full(skill_id: str) -> bool:
    """物理删除 catalog 记录（管理员审批删除时调用）。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM skill_catalog WHERE skill_id = $1", skill_id
        )
        return result == "DELETE 1"
