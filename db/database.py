import asyncpg
from typing import Optional
import os
import sys
from pathlib import Path

# 添加根目录到路径，以便导入 config
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from config.config import app_config


class DatabaseManager:
    """数据库连接管理器，负责创建和管理 PostgreSQL 连接池。"""

    def __init__(self, host: str = None, port: int = None, user: str = None,
                 password: str = None, database: str = None):
        # 如果参数为空，则从 config 中读取
        self.host = host or app_config.CMTOOL_AGENT_DB_HOST
        self.port = port or app_config.CMTOOL_AGENT_DB_PORT
        self.user = user or app_config.CMTOOL_AGENT_DB_USER
        self.password = password or app_config.CMTOOL_AGENT_DB_PASSWORD
        self.database = database or app_config.CMTOOL_AGENT_DB_NAME
        self._pool: Optional[asyncpg.pool.Pool] = None

    async def get_pool(self) -> asyncpg.pool.Pool:
        """获取数据库连接池，如果不存在则创建。"""
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database
            )
        return self._pool

    async def init_tables(self):
        """初始化数据库表。"""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            # 创建 user_sessions 表
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS user_sessions (
                    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id TEXT NOT NULL,
                    session_name TEXT DEFAULT '新会话',
                    is_deleted BOOLEAN DEFAULT FALSE,
                    last_active_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                );
                CREATE INDEX IF NOT EXISTS idx_user_sessions_list 
                ON user_sessions(user_id, last_active_at DESC) 
                WHERE is_deleted = FALSE;
            """)

            # 创建 agent_configs 表
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_configs (
                    session_id UUID PRIMARY KEY REFERENCES user_sessions(session_id) ON DELETE CASCADE,
                    model_name TEXT DEFAULT 'nebulacoder-pro-7.0',
                    config JSONB, -- 包含 {"selected_mcp_ids": []}
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                );
            """)

            # 创建历史记录表
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_history (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    session_id UUID NOT NULL REFERENCES user_sessions(session_id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    type TEXT NOT NULL,
                    content JSONB NOT NULL,
                    tool_call_id TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                );
                CREATE INDEX IF NOT EXISTS idx_chat_history_session_time 
                ON chat_history(session_id, created_at ASC);
            """)

    async def close(self):
        """关闭连接池。"""
        if self._pool:
            await self._pool.close()
            self._pool = None

# 全局数据库管理器实例（使用 config.py 中的配置自动初始化）
db_manager = DatabaseManager()

# 便捷函数
async def get_pool():
    return await db_manager.get_pool()

async def init_db():
    await db_manager.init_tables()