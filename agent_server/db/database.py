import asyncpg
from typing import Optional
import os
import sys
from pathlib import Path

# 添加根目录到路径，以便导入 config
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from config import app_config


class DatabaseManager:
    """数据库连接管理器，负责创建和管理 PostgreSQL 连接池。"""

    def __init__(self, host: str = None, port: int = None, user: str = None,
                 password: str = None, database: str = None):
        # 如果参数为空，则从 config 中读取
        self.host = host or app_config.DB_HOST
        self.port = port or app_config.DB_PORT
        self.user = user or app_config.DB_USER
        self.password = password or app_config.DB_PASSWORD
        self.database = database or app_config.DB_NAME
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
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_sessions (
                    id SERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    session_name TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                    UNIQUE(user_id, session_id)
                )
                """
            )

            # 创建 agent_configs 表
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_configs (
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    config JSONB,
                    PRIMARY KEY(user_id, session_id)
                )
                """
            )

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