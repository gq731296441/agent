import asyncio
import asyncpg
import json
from typing import Optional, List, Dict, Any
from uuid import UUID
from db.database import db_manager
from config import setup_logging, get_logger

logger = get_logger(__name__)

async def init_db():
    """初始化存储。"""
    await db_manager.init_tables()

# ==================== Session 相关操作 ====================

async def upsert_user_session(
    user_id: str,
    session_id: str,
    session_name: str = "新会话",
    agent_id: str = None,
    agent_kind: str = "default",
    subscription_frozen: bool = False,
) -> str:
    """插入或更新用户会话记录。返回会话 ID（UUID）。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        aid = UUID(agent_id) if agent_id else None
        result = await conn.fetchval(
            """
            INSERT INTO user_sessions (
                user_id, session_id, session_name, agent_id, agent_kind,
                subscription_frozen, last_active_at, is_deleted
            )
            VALUES ($1, $2, $3, $4, $5, $6, CURRENT_TIMESTAMP, FALSE)
            ON CONFLICT (session_id)
            DO UPDATE SET
                last_active_at = CURRENT_TIMESTAMP,
                is_deleted = FALSE,
                session_name = COALESCE(EXCLUDED.session_name, user_sessions.session_name),
                agent_id = COALESCE(EXCLUDED.agent_id, user_sessions.agent_id),
                agent_kind = COALESCE(EXCLUDED.agent_kind, user_sessions.agent_kind)
            RETURNING session_id
            """,
            user_id,
            UUID(session_id) if isinstance(session_id, str) else session_id,
            session_name,
            aid,
            agent_kind,
            subscription_frozen,
        )
        return str(result)


async def update_session_title(user_id: str, session_id: str, new_title: str) -> bool:
    """仅更新会话标题（用于延迟标题生成），不影响其他字段。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        try:
            result = await conn.execute(
                """
                UPDATE user_sessions
                SET session_name = $1, updated_at = CURRENT_TIMESTAMP
                WHERE session_id = $2 AND user_id = $3 AND is_deleted = FALSE
                """,
                new_title,
                UUID(session_id) if isinstance(session_id, str) else session_id,
                user_id,
            )
            return result == "UPDATE 1"
        except Exception as e:
            logger.error(f"【会话】更新标题失败 session={session_id} error={e}")
            return False


async def get_session_meta(session_id: str) -> Optional[Dict[str, Any]]:
    """获取会话元数据（含 agent_kind / frozen / epoch）。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT session_id, user_id, session_name, agent_id, agent_kind,
                   context_epoch, subscription_frozen, is_deleted, last_active_at
            FROM user_sessions WHERE session_id = $1
            """,
            UUID(session_id) if isinstance(session_id, str) else session_id,
        )
        if not row:
            return None
        d = dict(row)
        d["session_id"] = str(d["session_id"])
        if d.get("agent_id"):
            d["agent_id"] = str(d["agent_id"])
        return d


async def increment_context_epoch(session_id: str, expected_epoch: int) -> Optional[int]:
    """乐观锁递增 context_epoch；冲突返回 None。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE user_sessions
            SET context_epoch = context_epoch + 1, updated_at = CURRENT_TIMESTAMP
            WHERE session_id = $1 AND context_epoch = $2 AND is_deleted = FALSE
            RETURNING context_epoch
            """,
            UUID(session_id) if isinstance(session_id, str) else session_id,
            expected_epoch,
        )
        return int(row["context_epoch"]) if row else None


async def get_user_sessions(
    user_id: str,
    agent_kind: str = None,
) -> List[Dict[str, Any]]:
    """获取指定用户的有效会话列表。可选按 agent_kind 过滤。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        if agent_kind:
            rows = await conn.fetch(
                """
                SELECT s.session_id, s.session_name, s.last_active_at, s.created_at,
                       s.updated_at, s.agent_id, s.agent_kind, s.context_epoch,
                       s.subscription_frozen, a.name AS agent_name, a.is_deleted AS agent_deleted,
                       a.avatar_color AS agent_avatar_color,
                       a.visibility AS agent_visibility, a.user_id AS agent_creator_user_id,
                       sub.status AS subscription_status
                FROM user_sessions s
                LEFT JOIN user_agents a ON a.id = s.agent_id
                LEFT JOIN user_agent_subscriptions sub
                    ON sub.user_id = s.user_id AND sub.agent_id = s.agent_id
                WHERE s.user_id = $1 AND s.is_deleted = FALSE AND s.agent_kind = $2
                ORDER BY s.last_active_at DESC
                """,
                user_id,
                agent_kind,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT session_id, session_name, last_active_at, created_at, updated_at,
                       agent_id, agent_kind, context_epoch, subscription_frozen
                FROM user_sessions
                WHERE user_id = $1 AND is_deleted = FALSE
                ORDER BY last_active_at DESC
                """,
                user_id,
            )
        result = []
        for row in rows:
            d = dict(row)
            d["session_id"] = str(d["session_id"])
            if d.get("agent_id"):
                d["agent_id"] = str(d["agent_id"])
            if agent_kind == "subscription":
                d["archived"] = bool(d.get("agent_deleted"))
                # 智能体已设为私有时，非创建者视为冻结
                is_creator = d.get("agent_creator_user_id") == user_id
                if d.get("agent_visibility") == "private" and not is_creator:
                    d["subscription_frozen"] = True
                else:
                    status = d.get("subscription_status")
                    if status == "active":
                        d["subscription_frozen"] = False
                    elif status == "cancelled" or d.get("subscription_frozen"):
                        d["subscription_frozen"] = True
            result.append(d)
        return result

async def soft_delete_user_session(user_id: str, session_id: str) -> bool:
    """软删除用户会话记录（标记为已删除，不真正删除数据）。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        try:
            result = await conn.execute(
                """
                UPDATE user_sessions 
                SET is_deleted = TRUE, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = $1 AND session_id = $2
                """,
                user_id, UUID(session_id) if isinstance(session_id, str) else session_id
            )
            return result == "UPDATE 1"
        except Exception as e:
            logger.error(f"Error soft deleting session: {e}")
            return False

async def delete_user_session(user_id: str, session_id: str) -> bool:
    """删除用户会话记录（物理删除，级联删除 agent_configs）。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        try:
            result = await conn.execute(
                """
                DELETE FROM user_sessions 
                WHERE user_id = $1 AND session_id = $2
                """,
                user_id, UUID(session_id) if isinstance(session_id, str) else session_id
            )
            return result == "DELETE 1"
        except Exception as e:
            logger.error(f"Error deleting session: {e}")
            return False

def _to_uuid(value, label="id"):
    """安全转换为 UUID，无效格式返回 None。"""
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    try:
        return UUID(value)
    except (ValueError, AttributeError):
        return None


async def session_exists(session_id: str) -> bool:
    """检查会话是否存在（不考虑软删除标记）。"""
    sid = _to_uuid(session_id, "session_id")
    if sid is None:
        return False
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                "SELECT 1 FROM user_sessions WHERE session_id = $1 LIMIT 1",
                sid,
            )
            return row is not None
        except Exception as e:
            logger.error(f"Error checking session exists: {e}")
            return False


async def is_session_owner(user_id: str, session_id: str) -> bool:
    """检查 session 是否属于 user_id 且未被软删除。"""
    sid = _to_uuid(session_id, "session_id")
    if sid is None:
        return False
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                SELECT 1 FROM user_sessions
                WHERE session_id = $1 AND user_id = $2 AND is_deleted = FALSE
                LIMIT 1
                """,
                sid,
                user_id,
            )
            return row is not None
        except Exception as e:
            logger.error(f"Error checking session owner: {e}")
            return False


async def validate_session_access(
    user_id: str,
    session_id: str,
    *,
    allow_if_missing: bool = False,
) -> bool:
    """校验用户是否有权访问该 session。

    allow_if_missing=True 时，若 session 尚未写入 user_sessions（新会话创建流程），放行。
    """
    if not user_id or not session_id:
        return False

    exists = await session_exists(session_id)
    if not exists:
        return allow_if_missing

    return await is_session_owner(user_id, session_id)

# =================== History 相关操作 ====================
import json

async def add_history_record(
    session_id: str, 
    user_id: str, 
    sender: str, 
    msg_type: str, 
    content: dict,
    conversation_id: str = None,
    group_id: int = 0,
    step_index: int = 0,
    ref_id: str = None,
    run_id: str = None,
    subagent_run_id: str = None,
    context_epoch: int = None,
):
    """添加历史消息记录。content 是一个字典，会被转换为 JSON 存储。"""
    t_id = content.get("tool_call_id")
    
    sid = UUID(session_id) if isinstance(session_id, str) else session_id
    cid = UUID(conversation_id) if isinstance(conversation_id, str) and conversation_id else None
    rid = UUID(ref_id) if ref_id else None
    run_uuid = UUID(run_id) if run_id else None
    sub_uuid = UUID(subagent_run_id) if subagent_run_id else None

    if context_epoch is None:
        meta = await get_session_meta(session_id)
        context_epoch = int(meta.get("context_epoch") or 1) if meta else 1

    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO chat_history (
                session_id, user_id, sender, type, content, tool_call_id,
                conversation_id, group_id, step_index, ref_id, run_id,
                subagent_run_id, context_epoch
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            """,
            sid,
            user_id,
            sender,
            msg_type,
            json.dumps(content, ensure_ascii=False, default=str),
            t_id,
            cid,
            group_id,
            step_index,
            rid,
            run_uuid,
            sub_uuid,
            context_epoch,
        )


async def insert_context_divider(session_id: str, user_id: str, context_epoch: int) -> None:
    """插入「以上为历史记录」分割线消息。连续多次 new-context 不重复插入。"""
    sid = UUID(session_id) if isinstance(session_id, str) else session_id
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        last_type = await conn.fetchval(
            """
            SELECT type FROM chat_history
            WHERE session_id = $1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            sid,
        )
        if last_type == "context_divider":
            return
    await add_history_record(
        session_id=session_id,
        user_id=user_id,
        sender="system",
        msg_type="context_divider",
        content={"text": "— 已开启新会话，以上为历史记录 —"},
        context_epoch=context_epoch,
    )


async def resolve_interrupt_in_history(
    session_id: str,
    request_id: str,
    *,
    decisions: List[dict],
    hitl_status: str = "resolved",
) -> bool:
    """
    更新 chat_history 中 type=interrupt 且 request_id 匹配的记录。

    decisions: 与 content.actions 按 index 对齐的用户决策列表（批准/拒绝/编辑）。
    """
    from datetime import datetime, timezone

    if not request_id:
        return False
    sid = UUID(session_id) if isinstance(session_id, str) else session_id
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, content FROM chat_history
            WHERE session_id = $1
              AND type = 'interrupt'
              AND content->>'request_id' = $2
            ORDER BY created_at DESC
            LIMIT 1
            """,
            sid,
            request_id,
        )
        if not row:
            return False
        content = row["content"]
        if isinstance(content, str):
            content = json.loads(content)
        if not isinstance(content, dict):
            content = {}
        content["hitl_status"] = hitl_status
        content["resolved_at"] = datetime.now(timezone.utc).isoformat()
        content["decisions"] = decisions
        await conn.execute(
            "UPDATE chat_history SET content = $1::jsonb WHERE id = $2",
            json.dumps(content, ensure_ascii=False),
            row["id"],
        )
        return True


async def patch_interrupt_resolved_tool_call_id(
    session_id: str,
    request_id: str,
    *,
    index: int,
    resolved_tool_call_id: str,
    original_tool_call_id: str = None,
) -> bool:
    """
    resume 后工具真正执行时，将 LangGraph call_* 写回 interrupt 的 actions/decisions。

    保证审批条、execute start、execute result 共用同一 tool_call_id 溯源。
    """
    if not request_id or not resolved_tool_call_id or index < 0:
        return False
    sid = UUID(session_id) if isinstance(session_id, str) else session_id
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, content FROM chat_history
            WHERE session_id = $1
              AND type = 'interrupt'
              AND content->>'request_id' = $2
            ORDER BY created_at DESC
            LIMIT 1
            """,
            sid,
            request_id,
        )
        if not row:
            return False
        content = row["content"]
        if isinstance(content, str):
            content = json.loads(content)
        if not isinstance(content, dict):
            content = {}

        actions = list(content.get("actions") or [])
        decisions = list(content.get("decisions") or [])
        if index >= len(actions):
            return False

        prior = original_tool_call_id or actions[index].get("tool_call_id")
        if prior and str(prior) == str(resolved_tool_call_id):
            return True

        if prior and str(prior).startswith("hitl_"):
            actions[index]["original_hitl_tool_call_id"] = prior
        actions[index]["tool_call_id"] = resolved_tool_call_id
        actions[index]["resolved_tool_call_id"] = resolved_tool_call_id

        if index < len(decisions):
            dprior = decisions[index].get("tool_call_id") or prior
            if dprior and str(dprior).startswith("hitl_"):
                decisions[index]["original_hitl_tool_call_id"] = dprior
            decisions[index]["tool_call_id"] = resolved_tool_call_id
            decisions[index]["resolved_tool_call_id"] = resolved_tool_call_id

        content["actions"] = actions
        content["decisions"] = decisions
        await conn.execute(
            "UPDATE chat_history SET content = $1::jsonb WHERE id = $2",
            json.dumps(content, ensure_ascii=False),
            row["id"],
        )
        return True


async def load_persisted_tool_call_keys(
    session_id: str,
    conversation_id: str = None,
) -> tuple[set, set]:
    """
    读取 session 已落库的工具 tool_call_id 集合。

    Returns:
        (started_ids, completed_ids)
    """
    sid = UUID(session_id) if isinstance(session_id, str) else session_id
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        if conversation_id:
            rows = await conn.fetch(
                """
                SELECT type, content
                FROM chat_history
                WHERE session_id = $1
                  AND (
                    conversation_id::text = $2
                    OR content->>'conversation_id' = $2
                  )
                  AND content->>'tool_call_id' IS NOT NULL
                  AND content->>'tool_call_id' != ''
                """,
                sid,
                conversation_id,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT type, content
                FROM chat_history
                WHERE session_id = $1
                  AND content->>'tool_call_id' IS NOT NULL
                  AND content->>'tool_call_id' != ''
                """,
                sid,
            )

    started: set = set()
    completed: set = set()
    result_types = {
        "execute_result", "tool_result", "subagent_end",
    }
    for row in rows:
        content = row["content"]
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except json.JSONDecodeError:
                content = {}
        if not isinstance(content, dict):
            continue
        tcid = content.get("tool_call_id")
        if not tcid:
            continue
        tcid = str(tcid)
        msg_type = row["type"]
        if msg_type in result_types:
            completed.add(tcid)
            started.add(tcid)
            continue
        if msg_type in ("fs_operation", "execute", "todo_update", "tool_call", "subagent_start"):
            if content.get("tool_result") or content.get("summary"):
                completed.add(tcid)
            started.add(tcid)
    return started, completed


async def fetch_history_by_session(
        session_id: UUID, 
        limit: int = 50, 
        offset: int = 0
    ) -> List[asyncpg.Record]:
        """从数据库读取原始记录。"""
        pool = await db_manager.get_pool()
        async with pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT id, sender, type, content, tool_call_id, 
                       conversation_id, group_id, step_index, created_at, context_epoch,
                       run_id
                FROM chat_history
                WHERE session_id = $1
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
                """,
                session_id, limit, offset
            )

async def count_total_by_session(session_id: UUID) -> int:
    """获取该会话的消息总数（用于分页统计）。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM chat_history WHERE session_id = $1",
            session_id
        )

async def delete_history_by_session(session_id: str) -> bool:
    """删除指定会话的所有历史消息记录（物理删除）。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                "DELETE FROM chat_history WHERE session_id = $1",
                UUID(session_id) if isinstance(session_id, str) else session_id
            )
            return True
        except Exception as e:
            logger.error(f"Error deleting history for session {session_id}: {e}")
            return False


# ==================== Agent 配置相关操作 ====================

async def save_agent_config(
    session_id: str, 
    model_name: str = 'nebulacoder-v8',
    config: Optional[Dict[str, Any]] = None
) -> bool:
    """保存 agent 配置。session_id 是主键，包含模型名和选中的 MCP ID 列表。"""
    if config is None:
        config = {}
    
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                """
                INSERT INTO agent_configs (session_id, model_name, config)
                VALUES ($1, $2, $3)
                ON CONFLICT (session_id)
                DO UPDATE SET 
                    model_name = EXCLUDED.model_name,
                    config = EXCLUDED.config,
                    updated_at = CURRENT_TIMESTAMP
                """,
                UUID(session_id) if isinstance(session_id, str) else session_id,
                model_name,
                json.dumps(config)
            )
            return True
        except Exception as e:
            logger.error(f"Error saving agent config: {e}")
            return False

async def get_agent_config(session_id: str) -> Optional[Dict[str, Any]]:
    """获取 agent 配置。包括 model_name 和 config（含 selected_mcp_ids）。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                SELECT model_name, config, updated_at 
                FROM agent_configs 
                WHERE session_id = $1
                """,
                UUID(session_id) if isinstance(session_id, str) else session_id
            )
            if not row:
                return None
            
            config_data = json.loads(row["config"]) if row["config"] else {}
            return {
                "model_name": row["model_name"],
                "config": config_data,
                "updated_at": str(row["updated_at"])
            }
        except Exception as e:
            logger.error(f"Error getting agent config: {e}")
            return None

async def update_agent_config(
    session_id: str,
    model_name: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None
) -> bool:
    """更新 agent 配置。支持部分更新。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        try:
            # 先检查是否存在
            existing = await conn.fetchrow(
                "SELECT 1 FROM agent_configs WHERE session_id = $1",
                UUID(session_id) if isinstance(session_id, str) else session_id
            )
            if not existing:
                return False

            updates = []
            params = [UUID(session_id) if isinstance(session_id, str) else session_id]
            param_index = 2

            if model_name is not None:
                updates.append(f"model_name = ${param_index}")
                params.append(model_name)
                param_index += 1
            if config is not None:
                updates.append(f"config = ${param_index}")
                params.append(json.dumps(config))
                param_index += 1

            if not updates:
                return False

            updates.append("updated_at = CURRENT_TIMESTAMP")

            query = f"""
                UPDATE agent_configs
                SET {', '.join(updates)}
                WHERE session_id = $1
            """

            result = await conn.execute(query, *params)
            return result == "UPDATE 1"
        except Exception as e:
            logger.error(f"Error updating agent config: {e}")
            return False

async def delete_agent_config(session_id: str) -> bool:
    """删除 agent 配置。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        try:
            result = await conn.execute(
                "DELETE FROM agent_configs WHERE session_id = $1",
                UUID(session_id) if isinstance(session_id, str) else session_id
            )
            return result == "DELETE 1"
        except Exception as e:
            logger.error(f"Error deleting agent config: {e}")
            return False

async def get_user_agent_configs(user_id: str) -> List[Dict[str, Any]]:
    """获取用户的所有 agent 配置。通过关联 user_sessions 表。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(
                """
                SELECT ac.session_id, us.session_name, ac.model_name, ac.config, ac.updated_at
                FROM agent_configs ac
                JOIN user_sessions us ON ac.session_id = us.session_id
                WHERE us.user_id = $1 AND us.is_deleted = FALSE
                ORDER BY ac.updated_at DESC
                """,
                user_id
            )
            configs = []
            for row in rows:
                try:
                    config_data = json.loads(row["config"]) if row["config"] else {}
                    configs.append({
                        "session_id": str(row["session_id"]),
                        "session_name": row["session_name"],
                        "model_name": row["model_name"],
                        "config": config_data,
                        "updated_at": str(row["updated_at"])
                    })
                except json.JSONDecodeError:
                    continue
            return configs
        except Exception as e:
            logger.error(f"Error getting user agent configs: {e}")
            return []

async def get_agent_config_with_mcp_ids(session_id: str) -> Optional[List[str]]:
    """获取 agent 配置中的 MCP ID 列表。"""
    config = await get_agent_config(session_id)
    if not config:
        return None
    return config.get("config", {}).get("selected_mcp_ids", [])

async def update_agent_mcp_ids(session_id: str, mcp_ids: List[str]) -> bool:
    """更新 agent 配置中的 MCP ID 列表。"""
    config = await get_agent_config(session_id)
    if not config:
        return False
    
    updated_config = config.get("config", {})
    updated_config["selected_mcp_ids"] = mcp_ids
    
    return await update_agent_config(session_id, config=updated_config)

async def get_mcp_configs_by_ids(mcp_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """根据 MCP ID 列表获取 MCP 配置，返回以名称为键的字典格式（用于 McpToolManager 初始化）。
    
    返回格式:
    {
        "mcp_name_1": {
            "transport": "http",
            "url": "http://..."
        },
        "mcp_name_2": {
            "transport": "http",
            "url": "http://..."
        }
    }
    """
    if not mcp_ids:
        return {}
    
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        try:
            # 构建查询条件，匹配指定的 ID 列表
            placeholders = ",".join([f"${i+1}" for i in range(len(mcp_ids))])
            query = f"""
                SELECT id, name, url, type
                FROM user_mcp_configs
                WHERE id IN ({placeholders}) AND is_active = TRUE
            """
            
            # 转换 ID 为 UUID
            uuid_ids = [UUID(mcp_id) if isinstance(mcp_id, str) else mcp_id for mcp_id in mcp_ids]
            
            rows = await conn.fetch(query, *uuid_ids)
            
            # 构建返回的字典
            result = {}
            for row in rows:
                tool_name = row["name"]  # 使用工具名称作为键
                result[tool_name] = {
                    "transport": row["type"],  # type 对应 transport
                    "url": row["url"]
                }
            
            return result
        except Exception as e:
            logger.error(f"Error getting MCP configs by IDs: {e}")
            return {}


# ==================== measure.tool_call_logs 相关操作 ====================

MAX_TOOL_RESULT_CHARS = 500


async def insert_tool_call_log(
    session_id: str,
    user_id: str,
    tool_call_id: str,
    tool_name: str,
    tool_args: dict,
    tool_result: str,
    status: str,
    run_id: str = None,
    conversation_id: str = None,
    duration_ms: int = None,
) -> None:
    """将一次 MCP 工具调用信息写入 measure.tool_call_logs 表。

    Args:
        session_id: 会话 ID
        user_id: 用户 ID
        tool_call_id: 工具调用 ID
        tool_name: 工具名称
        tool_args: 工具参数（dict，存为 JSONB）
        tool_result: 工具执行结果（仅保存前 500 字符）
        status: 快模型质量判断结果（success / fail）
    """
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        try:
            # 截断 tool_result 到 500 字符
            truncated_result = tool_result[:MAX_TOOL_RESULT_CHARS] if tool_result else ""

            await conn.execute(
                """
                INSERT INTO measure.tool_call_logs
                    (session_id, user_id, tool_call_id, tool_name, tool_args, tool_result, status,
                     run_id, conversation_id, duration_ms)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                UUID(session_id) if isinstance(session_id, str) else session_id,
                user_id,
                tool_call_id,
                tool_name,
                json.dumps(tool_args, ensure_ascii=False, default=str) if tool_args else "{}",
                truncated_result,
                status,
                run_id,
                UUID(conversation_id) if conversation_id else None,
                duration_ms,
            )
        except Exception as e:
            logger.error(f"Error inserting tool call log: {e}")


# ==================== 运营统计相关操作 ====================

def _build_time_filters(start_date: str = None, end_date: str = None, table_alias: str = "created_at"):
    """构建时间范围过滤的 SQL 片段和参数列表。

    Args:
        start_date: 开始日期 (YYYY-MM-DD)
        end_date: 结束日期 (YYYY-MM-DD)
        table_alias: 时间列名

    Returns:
        (where_clauses: list, params: list)
    """
    clauses = []
    params = []
    idx = 1

    if start_date:
        clauses.append(f"{table_alias} >= ${idx}::date")
        params.append(start_date)
        idx += 1
    if end_date:
        # end_date 包含当天，所以用 < (end_date + 1 day)
        clauses.append(f"{table_alias} < ${idx}::date + INTERVAL '1 day'")
        params.append(end_date)
        idx += 1

    return clauses, params


async def get_total_users() -> int:
    """统计用户总数（去重，不含已删除会话的用户）。

    注意：仅统计在 user_sessions 表中有非软删除会话记录的用户。
    """
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(DISTINCT user_id) FROM user_sessions WHERE is_deleted = FALSE"
        )


async def get_active_users(start_date: str = None, end_date: str = None) -> int:
    """统计活跃用户数（指定时间范围内有活跃会话的用户，去重）。

    Args:
        start_date: 统计开始日期
        end_date: 统计结束日期
    """
    clauses, params = _build_time_filters(start_date, end_date, "last_active_at")
    where = "is_deleted = FALSE"
    if clauses:
        where += " AND " + " AND ".join(clauses)

    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            f"SELECT COUNT(DISTINCT user_id) FROM user_sessions WHERE {where}",
            *params
        )


async def get_session_count(start_date: str = None, end_date: str = None) -> int:
    """统计会话数（不含软删除的会话），支持时间范围过滤。

    Args:
        start_date: 统计开始日期（基于 created_at）
        end_date: 统计结束日期
    """
    clauses, params = _build_time_filters(start_date, end_date, "created_at")
    where = "is_deleted = FALSE"
    if clauses:
        where += " AND " + " AND ".join(clauses)

    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            f"SELECT COUNT(*) FROM user_sessions WHERE {where}",
            *params
        )


async def get_sessions_by_date(start_date: str = None, end_date: str = None) -> list:
    """按日期聚合会话创建数量。

    Returns:
        [{"date": "2026-01-01", "count": 42}, ...]
    """
    clauses, params = _build_time_filters(start_date, end_date, "created_at")
    where = "is_deleted = FALSE"
    if clauses:
        where += " AND " + " AND ".join(clauses)

    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT created_at::date AS date, COUNT(*) AS count
            FROM user_sessions
            WHERE {where}
            GROUP BY created_at::date
            ORDER BY date ASC
            """,
            *params
        )
        return [{"date": str(row["date"]), "count": row["count"]} for row in rows]


async def get_message_count(
    start_date: str = None,
    end_date: str = None,
    msg_type: str = None
) -> int:
    """统计消息数，支持时间范围和类型过滤。

    Args:
        start_date: 统计开始日期
        end_date: 统计结束日期
        msg_type: 消息类型过滤（如 chat, tool_call, tool_result, end, error）
    """
    clauses, params = _build_time_filters(start_date, end_date, "created_at")

    if msg_type:
        clauses.append(f"type = ${len(params) + 1}")
        params.append(msg_type)

    where = " AND ".join(clauses) if clauses else "TRUE"

    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            f"SELECT COUNT(*) FROM chat_history WHERE {where}",
            *params
        )


async def get_messages_by_type(start_date: str = None, end_date: str = None) -> dict:
    """按消息类型统计消息数。

    Returns:
        {"chat": 1000, "tool_call": 500, "tool_result": 500, "end": 200, "error": 10, ...}
    """
    clauses, params = _build_time_filters(start_date, end_date, "created_at")
    where = " AND ".join(clauses) if clauses else "TRUE"

    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT type, COUNT(*) AS count
            FROM chat_history
            WHERE {where}
            GROUP BY type
            """,
            *params
        )
        return {row["type"]: row["count"] for row in rows}


async def get_tool_call_count(
    start_date: str = None,
    end_date: str = None,
    tool_name: str = None,
    status: str = None
) -> int:
    """统计工具调用次数，支持时间范围、工具名、状态过滤。

    Args:
        start_date: 统计开始日期
        end_date: 统计结束日期
        tool_name: 工具名称过滤
        status: 工具调用状态过滤 (success / fail)
    """
    clauses, params = _build_time_filters(start_date, end_date, "created_at")

    if tool_name:
        clauses.append(f"tool_name = ${len(params) + 1}")
        params.append(tool_name)
    if status:
        clauses.append(f"status = ${len(params) + 1}")
        params.append(status)

    where = " AND ".join(clauses) if clauses else "TRUE"

    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            f"SELECT COUNT(*) FROM measure.tool_call_logs WHERE {where}",
            *params
        )


async def get_tool_calls_by_status(start_date: str = None, end_date: str = None) -> dict:
    """按状态统计工具调用次数。

    Returns:
        {"success": 7200, "fail": 1700}
    """
    clauses, params = _build_time_filters(start_date, end_date, "created_at")
    where = " AND ".join(clauses) if clauses else "TRUE"

    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT status, COUNT(*) AS count
            FROM measure.tool_call_logs
            WHERE {where}
            GROUP BY status
            """,
            *params
        )
        return {row["status"] or "unknown": row["count"] for row in rows}


async def get_tool_calls_by_name(
    start_date: str = None,
    end_date: str = None,
    limit: int = 20
) -> list:
    """按工具名称统计调用次数（TOP N）。

    Returns:
        [{"tool_name": "xxx", "count": 100, "success_count": 80, "fail_count": 20}, ...]
    """
    clauses, params = _build_time_filters(start_date, end_date, "created_at")
    where = " AND ".join(clauses) if clauses else "TRUE"

    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT
                tool_name,
                COUNT(*) AS count,
                COUNT(*) FILTER (WHERE status = 'success') AS success_count,
                COUNT(*) FILTER (WHERE status = 'fail') AS fail_count
            FROM measure.tool_call_logs
            WHERE {where}
            GROUP BY tool_name
            ORDER BY count DESC
            LIMIT {int(limit)}
            """,
            *params
        )
        return [
            {
                "tool_name": row["tool_name"],
                "count": row["count"],
                "success_count": row["success_count"],
                "fail_count": row["fail_count"],
            }
            for row in rows
        ]


async def get_task_outcome_counts(start_date: str = None, end_date: str = None) -> dict:
    clauses, params = _build_time_filters(start_date, end_date, "created_at")
    where = " AND ".join(clauses) if clauses else "TRUE"

    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        # 统计 END 消息中的 completed/stop
        end_rows = await conn.fetch(
            f"""
            SELECT content, COUNT(*) AS count
            FROM chat_history
            WHERE type = 'end' AND ({where})
            GROUP BY content
            """,
            *params
        )

        result = {"completed": 0, "stopped": 0, "error": 0, "interrupted": 0}

        def _end_reason_text(content) -> str:
            if content is None:
                return ""
            if isinstance(content, dict):
                return str(content.get("text", ""))
            if isinstance(content, str):
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict):
                        return str(parsed.get("text", ""))
                except json.JSONDecodeError:
                    pass
                return content
            return str(content)

        for row in end_rows:
            text = _end_reason_text(row["content"])
            if text in ("completed",):
                result["completed"] += row["count"]
            elif text in ("stop",):
                result["stopped"] += row["count"]
            elif text in ("error",):
                result["error"] += row["count"]

        # 统计 type='error' 的消息数
        error_clauses = [f"type = 'error'"]
        if clauses:
            error_clauses.extend(clauses)
        error_where = " AND ".join(error_clauses)
        result["error"] = await conn.fetchval(
            f"SELECT COUNT(*) FROM chat_history WHERE {error_where}",
            *params
        )

        # 统计 type='interrupt' 的消息数
        interrupt_clauses = [f"type = 'interrupt'"]
        if clauses:
            interrupt_clauses.extend(clauses)
        interrupt_where = " AND ".join(interrupt_clauses)
        result["interrupted"] = await conn.fetchval(
            f"SELECT COUNT(*) FROM chat_history WHERE {interrupt_where}",
            *params
        )

        return result
