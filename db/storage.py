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

async def upsert_user_session(user_id: str, session_id: str, session_name: str = "新会话") -> str:
    """插入或更新用户会话记录。返回会话 ID（UUID）。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        # 尝试插入新会话，如果存在则更新 last_active_at 和 is_deleted 标记
        result = await conn.fetchval(
            """
            INSERT INTO user_sessions (user_id, session_id, session_name, last_active_at, is_deleted)
            VALUES ($1, $2, $3, CURRENT_TIMESTAMP, FALSE)
            ON CONFLICT (session_id)
            DO UPDATE SET 
                last_active_at = CURRENT_TIMESTAMP,
                is_deleted = FALSE,
                session_name = EXCLUDED.session_name
            RETURNING session_id
            """,
            user_id, UUID(session_id) if isinstance(session_id, str) else session_id, session_name
        )
        return str(result)

async def get_user_sessions(user_id: str) -> List[Dict[str, Any]]:
    """获取指定用户的有效会话列表（不包括软删除的会话）。按 last_active_at 排序。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT session_id, session_name, last_active_at, created_at, updated_at
            FROM user_sessions
            WHERE user_id = $1 AND is_deleted = FALSE
            ORDER BY last_active_at DESC
            """,
            user_id
        )
        return [
            {
                "session_id": str(row["session_id"]),
                "session_name": row["session_name"],
                "last_active_at": str(row["last_active_at"]),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"])
            }
            for row in rows
        ]

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

async def session_exists(session_id: str) -> bool:
    """检查会话是否存在（不考虑软删除标记）。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                "SELECT 1 FROM user_sessions WHERE session_id = $1 LIMIT 1",
                UUID(session_id) if isinstance(session_id, str) else session_id
            )
            return row is not None
        except Exception as e:
            logger.error(f"Error checking session exists: {e}")
            return False

# =================== History 相关操作 ====================
import json

async def add_history_record(session_id: str, user_id: str, sender: str, msg_type: str, content: dict):
    """添加历史消息记录。content 是一个字典，会被转换为 JSON 存储。"""
    # 自动提取 tool_call_id
    t_id = content.get("tool_call_id")
    
    # 确保 session_id 是 UUID 类型
    sid = UUID(session_id) if isinstance(session_id, str) else session_id

    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO chat_history (session_id, user_id, sender, type, content, tool_call_id)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            sid,
            user_id,
            sender,
            msg_type,
            json.dumps(content, ensure_ascii=False), # 传入 JSON 字符串, 确保中文不乱码
            t_id
        )

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
                SELECT id, sender, type, content, tool_call_id, created_at
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


# ==================== Agent 配置相关操作 ====================

async def save_agent_config(
    session_id: str, 
    model_name: str = 'nebulacoder-pro-7.0',
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
