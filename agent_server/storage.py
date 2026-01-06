import asyncio
import json
from typing import Optional, List, Dict, Any
from agent_server.db.database import db_manager

async def init_db():
    """初始化存储。"""
    await db_manager.init_tables()

# ==================== Session 相关操作 ====================

async def upsert_user_session(user_id: str, session_id: str, session_name: str = "新会话"):
    """插入或更新用户会话记录。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_sessions (user_id, session_id, session_name, updated_at)
            VALUES ($1, $2, $3, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id, session_id)
            DO UPDATE SET updated_at = CURRENT_TIMESTAMP, session_name = EXCLUDED.session_name
            """,
            user_id, session_id, session_name
        )

async def get_user_sessions(user_id: str) -> List[Dict[str, Any]]:
    """获取指定用户的会话列表。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT session_id, session_name, created_at, updated_at
            FROM user_sessions
            WHERE user_id = $1
            ORDER BY updated_at DESC
            """,
            user_id
        )
        return [
            {
                "session_id": row["session_id"],
                "session_name": row["session_name"],
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"])
            }
            for row in rows
        ]

async def delete_user_session(user_id: str, session_id: str) -> bool:
    """删除用户会话记录。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM user_sessions WHERE user_id = $1 AND session_id = $2",
            user_id, session_id
        )
        return result == "DELETE 1"

async def session_exists(session_id: str) -> bool:
    """检查会话是否存在（通过 agent_configs 表）。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM agent_configs WHERE session_id = $1 LIMIT 1",
            session_id
        )
        return row is not None

# ==================== Agent 配置相关操作 ====================

async def save_agent_config(user_id: str, session_id: str, config: Dict[str, Any]):
    """保存 agent 配置。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO agent_configs(user_id, session_id, config)
            VALUES($1, $2, $3)
            ON CONFLICT (user_id, session_id)
            DO UPDATE SET config = EXCLUDED.config
            """,
            user_id,
            session_id,
            json.dumps(config),
        )

async def get_agent_config(user_id: str, session_id: str) -> Optional[Dict[str, Any]]:
    """获取 agent 配置。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT config FROM agent_configs WHERE user_id = $1 AND session_id = $2",
            user_id, session_id
        )
        if not row:
            return None
        try:
            return json.loads(row["config"])
        except json.JSONDecodeError:
            return None

async def update_agent_config(user_id: str, session_id: str, config: Dict[str, Any]) -> bool:
    """更新 agent 配置（如果不存在则返回 False）。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        # 先检查是否存在
        existing = await conn.fetchrow(
            "SELECT 1 FROM agent_configs WHERE user_id = $1 AND session_id = $2",
            user_id, session_id
        )
        if not existing:
            return False

        await conn.execute(
            "UPDATE agent_configs SET config = $3 WHERE user_id = $1 AND session_id = $2",
            user_id, session_id, json.dumps(config)
        )
        return True

async def delete_agent_config(user_id: str, session_id: str) -> bool:
    """删除 agent 配置。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM agent_configs WHERE user_id = $1 AND session_id = $2",
            user_id, session_id
        )
        return result == "DELETE 1"

async def get_user_agent_configs(user_id: str) -> List[Dict[str, Any]]:
    """获取用户的所有 agent 配置。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT session_id, config FROM agent_configs WHERE user_id = $1",
            user_id
        )
        configs = []
        for row in rows:
            try:
                config_data = json.loads(row["config"])
                configs.append({
                    "session_id": row["session_id"],
                    "config": config_data
                })
            except json.JSONDecodeError:
                continue
        return configs
