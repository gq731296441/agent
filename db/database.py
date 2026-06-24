import asyncpg
from typing import Optional
import os
import sys
from pathlib import Path
import asyncio
import time
import random

# 添加根目录到路径，以便导入 config
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from config.config import app_config
from config import get_logger

logger = get_logger(__name__)


class DatabaseManager:
    """数据库连接管理器，负责创建和管理 PostgreSQL 连接池。

    支持自动重连机制：
    - 连接池为空时自动创建
    - 连接失效时自动重建连接池
    - 数据库重启后无需重启应用即可恢复连接

    优化策略：
    - 使用缓存时间窗口避免频繁健康检查
    - 依赖 asyncpg 内置连接有效性检测
    """

    def __init__(self, host: str = None, port: int = None, user: str = None,
                 password: str = None, database: str = None):
        # 如果参数为空，则从 config 中读取
        self.host = host or app_config.CMTOOL_AGENT_DB_HOST
        self.port = port or app_config.CMTOOL_AGENT_DB_PORT
        self.user = user or app_config.CMTOOL_AGENT_DB_USER
        self.password = password or app_config.CMTOOL_AGENT_DB_PASSWORD
        self.database = database or app_config.CMTOOL_AGENT_DB_NAME
        self._pool: Optional[asyncpg.pool.Pool] = None
        self._reconnect_lock = asyncio.Lock()  # 防止并发重连
        self._last_health_check = 0.0  # 上次健康检查时间戳
        self._health_check_interval: float = 30.0  # 30 秒内不重复检查

    def _get_pool_config(self) -> dict:
        """获取连接池配置参数

        asyncpg 内置连接有效性检测：
        - acquire() 时自动检查连接是否可用
        - 自动移除失效连接并创建新连接
        - max_inactive_connection_lifetime 自动清理空闲连接
        """
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "database": self.database,
            "min_size": app_config.DB_POOL_MIN,
            "max_size": app_config.DB_POOL_MAX,
            "timeout": 30,
            "command_timeout": 60,
            "max_inactive_connection_lifetime": 300.0,
        }

    async def _create_pool(self) -> asyncpg.pool.Pool:
        """创建新的连接池"""
        pool = await asyncpg.create_pool(**self._get_pool_config())
        logger.info(f"【数据库】✅ 连接池创建成功 - {self.database}@{self.host}:{self.port}")
        return pool

    async def _reconnect(self) -> asyncpg.pool.Pool:
        """重建连接池（线程安全）"""
        async with self._reconnect_lock:
            # 双重检查，避免重复创建
            if self._pool is not None:
                try:
                    # 尝试获取一个连接（asyncpg 会自动检测有效性）
                    async with self._pool.acquire() as conn:
                        await conn.fetchval("SELECT 1")
                    self._last_health_check = time.time()
                    return self._pool
                except Exception:
                    # 旧连接失效，关闭并重建
                    logger.warning("【数据库】⚠️ 检测到旧连接失效，关闭连接池")
                    await self._pool.close()
                    self._pool = None

            # 指数退避重试
            max_retries = 5
            base_delay = 2.0  # 基础延迟秒数

            for attempt in range(1, max_retries + 1):
                try:
                    logger.info(f"【数据库】🔄 重连中... (尝试 {attempt}/{max_retries})")
                    self._pool = await self._create_pool()
                    return self._pool
                except Exception as e:
                    if attempt < max_retries:
                        # 指数退避 + 随机抖动，避免惊群效应
                        delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1)
                        logger.warning(f"【数据库】❌ 重连失败，{delay:.1f}秒后重试：{e}")
                        await asyncio.sleep(delay)
                    else:
                        logger.error(f"【数据库】❌ 重连失败，已达最大重试次数：{e}")
                        raise

    async def get_pool(self) -> asyncpg.pool.Pool:
        """获取数据库连接池，如果不存在或失效则自动重建。

        优化策略：
        - 缓存时间窗口内跳过 SELECT 1 检查
        - 依赖 asyncpg 内置连接有效性检测
        - 仅在超时或异常时触发重连
        """
        now = time.time()

        # 快速路径：连接池已存在且在缓存窗口期内
        if self._pool is not None:
            if now - self._last_health_check < self._health_check_interval:
                return self._pool

            # 缓存过期，需要健康检查
            try:
                async with self._pool.acquire() as conn:
                    await conn.fetchval("SELECT 1")
                self._last_health_check = now
                return self._pool
            except Exception as e:
                logger.warning(f"【数据库】⚠️ 连接池健康检查失败：{e}")
                return await self._reconnect()

        # 慢速路径：连接池不存在，需要创建
        return await self._reconnect()

    async def init_tables(self):
        """初始化所有数据库表（入口方法）。"""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            await self._init_user_sessions_table(conn)
            await self._init_agent_configs_table(conn)
            await self._init_chat_history_table(conn)
            await self._init_measure_schema_and_tables(conn)
            await self._init_agent_run_events_table(conn)
            await self._migrate_tool_call_logs_columns(conn)
            await self._init_deep_agent_tables(conn)
            await self._migrate_deep_columns(conn)
            await self._migrate_subscription_schema(conn)

    async def _migrate_tool_call_logs_columns(self, conn):
        """为 tool_call_logs 添加可观测性扩展列。"""
        alter_columns = [
            "ALTER TABLE measure.tool_call_logs ADD COLUMN IF NOT EXISTS run_id TEXT",
            "ALTER TABLE measure.tool_call_logs ADD COLUMN IF NOT EXISTS conversation_id UUID",
            "ALTER TABLE measure.tool_call_logs ADD COLUMN IF NOT EXISTS duration_ms INTEGER",
        ]
        for col_sql in alter_columns:
            try:
                await conn.execute(col_sql)
            except Exception:
                pass

    async def _init_agent_run_events_table(self, conn):
        """初始化 measure.agent_run_events 表（Agent 运行事件追踪）。"""
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS measure.agent_run_events (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                run_id TEXT NOT NULL,
                session_id UUID NOT NULL,
                user_id TEXT NOT NULL,
                conversation_id UUID,
                event_type TEXT NOT NULL,
                category TEXT,
                action TEXT,
                message TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_agent_run_events_run
            ON measure.agent_run_events(run_id, created_at ASC);
            CREATE INDEX IF NOT EXISTS idx_agent_run_events_session
            ON measure.agent_run_events(session_id, created_at DESC);
        """)
        logger.debug("【数据库】✅ measure.agent_run_events 表初始化完成")

    async def _init_user_sessions_table(self, conn):
        """初始化 user_sessions 表（用户会话管理）。"""
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
        logger.debug("【数据库】✅ user_sessions 表初始化完成")

    async def _init_agent_configs_table(self, conn):
        """初始化 agent_configs 表（Agent 配置）。"""
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_configs (
                session_id UUID PRIMARY KEY REFERENCES user_sessions(session_id) ON DELETE CASCADE,
                model_name TEXT DEFAULT 'nebulacoder-v8',
                config JSONB,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
            );
        """)
        logger.debug("【数据库】✅ agent_configs 表初始化完成")

    async def _init_chat_history_table(self, conn):
        """初始化 chat_history 表及索引（聊天记录）。"""
        # 创建表
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                session_id UUID NOT NULL REFERENCES user_sessions(session_id) ON DELETE CASCADE,
                user_id TEXT NOT NULL,
                sender TEXT NOT NULL,
                type TEXT NOT NULL,
                content JSONB NOT NULL,
                tool_call_id TEXT,
                conversation_id UUID,
                group_id INTEGER DEFAULT 0,
                step_index INTEGER DEFAULT 0,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_chat_history_session_time
            ON chat_history(session_id, created_at ASC);
        """)

        # 添加新列（已存在则忽略）
        alter_columns = [
            "ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS conversation_id UUID",
            "ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS group_id INTEGER DEFAULT 0",
            "ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS step_index INTEGER DEFAULT 0",
        ]
        for col_sql in alter_columns:
            try:
                await conn.execute(col_sql)
            except Exception:
                pass  # 列已存在

        # 创建索引
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chat_history_conversation
            ON chat_history(conversation_id);
        """)
        logger.debug("【数据库】✅ chat_history 表及索引初始化完成")

    async def _init_measure_schema_and_tables(self, conn):
        """初始化 measure schema 及 tool_call_logs 表（工具调用统计）。"""
        # 创建 schema
        await conn.execute("CREATE SCHEMA IF NOT EXISTS measure;")

        # 创建工具调用记录表
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS measure.tool_call_logs (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                session_id UUID NOT NULL,
                user_id TEXT NOT NULL,
                tool_call_id TEXT,
                tool_name TEXT NOT NULL,
                tool_args JSONB,
                tool_result TEXT,
                status TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_tool_call_logs_session
            ON measure.tool_call_logs(session_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_tool_call_logs_user
            ON measure.tool_call_logs(user_id, created_at DESC);
        """)
        logger.debug("【数据库】✅ measure.tool_call_logs 表初始化完成")

    async def _migrate_deep_columns(self, conn):
        """Deep Agent 相关列迁移。"""
        alter_columns = [
            "ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS ref_id UUID",
            "ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS subagent_run_id UUID",
            "ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS run_id UUID",
            "ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS agent_id UUID",
        ]
        for col_sql in alter_columns:
            try:
                await conn.execute(col_sql)
            except Exception:
                pass

    async def _backfill_user_agent_avatar_colors(self, conn):
        """按 agent id 回填头像色，保证每个智能体稳定且互不相同。"""
        from utils.agent_avatar import avatar_color_from_seed

        rows = await conn.fetch("SELECT id FROM user_agents")
        for row in rows:
            agent_id = str(row["id"])
            await conn.execute(
                "UPDATE user_agents SET avatar_color = $1 WHERE id = $2",
                avatar_color_from_seed(agent_id),
                row["id"],
            )

    async def _migrate_subscription_schema(self, conn):
        """订阅智能体相关表与列迁移。"""
        alter_columns = [
            "ALTER TABLE user_agents ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'public'",
            "ALTER TABLE user_agents ADD COLUMN IF NOT EXISTS config_version INT NOT NULL DEFAULT 1",
            "ALTER TABLE user_agent_skill_bindings ADD COLUMN IF NOT EXISTS snapshot_install_dir TEXT",
            "ALTER TABLE user_agent_skill_bindings ADD COLUMN IF NOT EXISTS snapshot_skill_name TEXT",
            "ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS agent_kind TEXT NOT NULL DEFAULT 'default'",
            "ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS context_epoch INT NOT NULL DEFAULT 1",
            "ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS subscription_frozen BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS context_epoch INT NOT NULL DEFAULT 1",
            "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS prompt_tokens INT DEFAULT 0",
            "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS completion_tokens INT DEFAULT 0",
            "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS total_tokens INT DEFAULT 0",
            "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS llm_model TEXT DEFAULT ''",
            "ALTER TABLE user_agents ADD COLUMN IF NOT EXISTS avatar_color TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE skill_catalog ADD COLUMN IF NOT EXISTS install_count BIGINT NOT NULL DEFAULT 0",
        ]
        for col_sql in alter_columns:
            try:
                await conn.execute(col_sql)
            except Exception:
                pass

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_secrets (
                user_id TEXT NOT NULL,
                secret_key TEXT NOT NULL,
                secret_value_enc TEXT NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                PRIMARY KEY (user_id, secret_key)
            );
            CREATE INDEX IF NOT EXISTS idx_user_secrets_user
            ON user_secrets(user_id);

            CREATE TABLE IF NOT EXISTS user_agent_subscriptions (
                user_id TEXT NOT NULL,
                agent_id UUID NOT NULL REFERENCES user_agents(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'active',
                subscribed_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                cancelled_at TIMESTAMP WITH TIME ZONE,
                PRIMARY KEY (user_id, agent_id)
            );
            CREATE INDEX IF NOT EXISTS idx_agent_subscriptions_agent
            ON user_agent_subscriptions(agent_id) WHERE status = 'active';
        """)

        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_subscription_session
            ON user_sessions(user_id, agent_id)
            WHERE agent_kind = 'subscription' AND is_deleted = FALSE AND agent_id IS NOT NULL
        """)

        # 清理历史 Default Agent 行（项目未上线，可直接清）
        await conn.execute("""
            DELETE FROM user_agent_mcp_bindings
            WHERE agent_id IN (
                SELECT id FROM user_agents
                WHERE name = 'Default Agent' OR system_prompt LIKE '%智搭助手%'
            )
        """)
        await conn.execute("""
            DELETE FROM user_agent_skill_bindings
            WHERE agent_id IN (
                SELECT id FROM user_agents
                WHERE name = 'Default Agent' OR system_prompt LIKE '%智搭助手%'
            )
        """)
        await conn.execute("""
            DELETE FROM user_agents
            WHERE name = 'Default Agent' OR system_prompt LIKE '%智搭助手%'
        """)
        await conn.execute("""
            UPDATE user_sessions
            SET agent_id = NULL, agent_kind = 'default'
            WHERE agent_id IS NOT NULL
              AND agent_kind IS DISTINCT FROM 'subscription'
        """)
        await self._backfill_user_agent_avatar_colors(conn)
        await self._backfill_skill_install_counts(conn)
        logger.debug("【数据库】✅ 订阅智能体 schema 迁移完成")

    async def _backfill_skill_install_counts(self, conn):
        """用历史安装审计记录回填累计安装次数（仅当仍为 0 时）。"""
        await conn.execute("""
            UPDATE skill_catalog c
            SET install_count = sub.cnt
            FROM (
                SELECT target AS skill_id, COUNT(*)::bigint AS cnt
                FROM deep_audit_logs
                WHERE action IN ('skill_subscribe', 'skill_upgrade')
                  AND target IS NOT NULL
                  AND btrim(target) != ''
                GROUP BY target
            ) sub
            WHERE c.skill_id = sub.skill_id
              AND COALESCE(c.install_count, 0) = 0
        """)

    async def _init_deep_agent_tables(self, conn):
        """初始化 Deep Agent 相关表。"""
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_agents (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT 'Default Agent',
                description TEXT DEFAULT '',
                system_prompt TEXT DEFAULT '',
                model_name TEXT DEFAULT 'nebulacoder-v8',
                config JSONB DEFAULT '{}',
                is_deleted BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_user_agents_user
            ON user_agents(user_id) WHERE is_deleted = FALSE;

            CREATE TABLE IF NOT EXISTS user_agent_mcp_bindings (
                agent_id UUID NOT NULL REFERENCES user_agents(id) ON DELETE CASCADE,
                mcp_id INTEGER NOT NULL,
                PRIMARY KEY (agent_id, mcp_id)
            );

            CREATE TABLE IF NOT EXISTS user_agent_skill_bindings (
                agent_id UUID NOT NULL REFERENCES user_agents(id) ON DELETE CASCADE,
                installed_skill_id UUID NOT NULL,
                PRIMARY KEY (agent_id, installed_skill_id)
            );

            CREATE TABLE IF NOT EXISTS user_installed_skills (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id TEXT NOT NULL,
                market_skill_id TEXT NOT NULL,
                skill_name TEXT NOT NULL,
                install_dir TEXT NOT NULL,
                version TEXT DEFAULT '1.0.0',
                installed_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                UNIQUE(user_id, market_skill_id)
            );
            CREATE INDEX IF NOT EXISTS idx_installed_skills_user
            ON user_installed_skills(user_id);

            CREATE TABLE IF NOT EXISTS skill_catalog (
                skill_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                version TEXT NOT NULL DEFAULT '1.0.0',
                tags TEXT[] DEFAULT '{}',
                author TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'published',
                repo_rel_path TEXT NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_skill_catalog_name
            ON skill_catalog (name);
            CREATE INDEX IF NOT EXISTS idx_skill_catalog_tags
            ON skill_catalog USING GIN (tags);
            CREATE INDEX IF NOT EXISTS idx_skill_catalog_status
            ON skill_catalog (status);

            CREATE TABLE IF NOT EXISTS deep_workspace_files (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id TEXT NOT NULL,
                session_id UUID,
                rel_path TEXT NOT NULL,
                size_bytes BIGINT DEFAULT 0,
                sha256 TEXT,
                file_kind TEXT DEFAULT 'workspace',
                summary TEXT,
                mime_type TEXT DEFAULT 'application/octet-stream',
                conversation_id UUID,
                artifact_type TEXT,
                storage_path TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_deep_workspace_user
            ON deep_workspace_files(user_id, session_id);

            CREATE TABLE IF NOT EXISTS deep_artifacts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id TEXT NOT NULL,
                session_id UUID,
                conversation_id UUID,
                artifact_type TEXT NOT NULL,
                storage_path TEXT NOT NULL,
                summary TEXT,
                size_bytes BIGINT DEFAULT 0,
                mime_type TEXT DEFAULT 'text/plain',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_deep_artifacts_user
            ON deep_artifacts(user_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS user_memories (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id TEXT NOT NULL,
                text TEXT NOT NULL,
                tags TEXT[] DEFAULT '{}',
                source TEXT NOT NULL DEFAULT 'explicit',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_user_memories_user
            ON user_memories(user_id, updated_at DESC);

            CREATE TABLE IF NOT EXISTS deep_subagent_runs (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                subagent_run_id UUID NOT NULL UNIQUE,
                parent_run_id UUID,
                session_id UUID NOT NULL,
                agent_id UUID,
                name TEXT NOT NULL,
                status TEXT DEFAULT 'running',
                input_summary TEXT,
                trace_ref_id UUID,
                final_report TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                finished_at TIMESTAMP WITH TIME ZONE
            );
            CREATE INDEX IF NOT EXISTS idx_deep_subagent_session
            ON deep_subagent_runs(session_id, created_at DESC);
            ALTER TABLE deep_subagent_runs
            ADD COLUMN IF NOT EXISTS input_summary TEXT;

            CREATE TABLE IF NOT EXISTS deep_sandbox_sessions (
                user_id TEXT NOT NULL,
                session_id UUID NOT NULL,
                container_id TEXT NOT NULL,
                status TEXT DEFAULT 'running',
                provider TEXT DEFAULT 'docker',
                last_active_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                ttl_at TIMESTAMP WITH TIME ZONE,
                mounted_workspace_path TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                PRIMARY KEY (user_id, session_id)
            );
            CREATE INDEX IF NOT EXISTS idx_sandbox_status_lru
            ON deep_sandbox_sessions(status, last_active_at ASC);
            ALTER TABLE deep_sandbox_sessions
            ADD COLUMN IF NOT EXISTS selection_key TEXT;
            ALTER TABLE deep_sandbox_sessions
            ADD COLUMN IF NOT EXISTS mounted_skill_ids JSONB DEFAULT '[]'::jsonb;

            CREATE TABLE IF NOT EXISTS session_start_tasks (
                session_id UUID PRIMARY KEY,
                user_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'CREATED',
                error TEXT,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_session_start_user
            ON session_start_tasks(user_id);

            CREATE TABLE IF NOT EXISTS pending_messages (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                session_id UUID NOT NULL,
                user_id TEXT NOT NULL,
                payload JSONB NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_pending_session
            ON pending_messages(session_id, created_at ASC);

            CREATE TABLE IF NOT EXISTS deep_audit_logs (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id TEXT NOT NULL,
                session_id UUID,
                run_id TEXT,
                action TEXT NOT NULL,
                target TEXT,
                detail JSONB DEFAULT '{}',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_deep_audit_user
            ON deep_audit_logs(user_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS user_resource_quotas (
                user_id TEXT PRIMARY KEY,
                workspace_bytes_limit BIGINT DEFAULT 104857600,
                workspace_bytes_used BIGINT DEFAULT 0,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
            );

            CREATE TABLE IF NOT EXISTS agent_runs (
                run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id TEXT NOT NULL,
                session_id UUID NOT NULL,
                agent_id UUID,
                conversation_id UUID,
                status TEXT DEFAULT 'running',
                result_summary JSONB DEFAULT '{}',
                error TEXT DEFAULT '',
                started_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                finished_at TIMESTAMP WITH TIME ZONE
            );
            CREATE INDEX IF NOT EXISTS idx_agent_runs_user
            ON agent_runs(user_id, started_at DESC);
            CREATE INDEX IF NOT EXISTS idx_agent_runs_session
            ON agent_runs(session_id, started_at DESC);
        """)
        await self._migrate_workspace_files_schema(conn)
        await self._migrate_sandbox_sessions_schema(conn)
        logger.debug("【数据库】✅ Deep Agent 相关表初始化完成")

    async def _migrate_sandbox_sessions_schema(self, conn):
        """deep_sandbox_sessions 从 per-user PK 迁移为 (user_id, session_id) 复合 PK。"""
        table_exists = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'deep_sandbox_sessions'
            )
            """
        )
        if not table_exists:
            return

        has_session_id = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'deep_sandbox_sessions'
                  AND column_name = 'session_id'
            )
            """
        )
        if not has_session_id:
            await conn.execute(
                "ALTER TABLE deep_sandbox_sessions ADD COLUMN session_id UUID"
            )

        try:
            await conn.execute(
                "ALTER TABLE deep_sandbox_sessions ADD COLUMN IF NOT EXISTS mounted_workspace_path TEXT"
            )
        except Exception:
            pass

        # 旧 per-user 行无法映射 session，清理后按新模型重建
        await conn.execute("DELETE FROM deep_sandbox_sessions WHERE session_id IS NULL")

        pk_cols = await conn.fetch(
            """
            SELECT a.attname
            FROM pg_index i
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = 'deep_sandbox_sessions'::regclass AND i.indisprimary
            ORDER BY array_position(i.indkey, a.attnum)
            """
        )
        pk_names = [r["attname"] for r in pk_cols]
        if pk_names == ["user_id"]:
            await conn.execute(
                "ALTER TABLE deep_sandbox_sessions DROP CONSTRAINT IF EXISTS deep_sandbox_sessions_pkey"
            )
            await conn.execute(
                "ALTER TABLE deep_sandbox_sessions ADD PRIMARY KEY (user_id, session_id)"
            )

        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sandbox_user_status
            ON deep_sandbox_sessions(user_id, status)
            """
        )

    async def _migrate_workspace_files_schema(self, conn):
        """扩展 deep_workspace_files、合并 deep_artifacts、建立唯一索引。"""
        alters = [
            "ALTER TABLE deep_workspace_files ADD COLUMN IF NOT EXISTS file_kind TEXT DEFAULT 'workspace'",
            "ALTER TABLE deep_workspace_files ADD COLUMN IF NOT EXISTS summary TEXT",
            "ALTER TABLE deep_workspace_files ADD COLUMN IF NOT EXISTS mime_type TEXT DEFAULT 'application/octet-stream'",
            "ALTER TABLE deep_workspace_files ADD COLUMN IF NOT EXISTS conversation_id UUID",
            "ALTER TABLE deep_workspace_files ADD COLUMN IF NOT EXISTS artifact_type TEXT",
            "ALTER TABLE deep_workspace_files ADD COLUMN IF NOT EXISTS storage_path TEXT",
            "ALTER TABLE deep_workspace_files ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT now()",
        ]
        for sql in alters:
            try:
                await conn.execute(sql)
            except Exception:
                pass

        has_artifacts = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'deep_artifacts'
            )
            """
        )
        if has_artifacts:
            await conn.execute(
                """
                INSERT INTO deep_workspace_files (
                    id, user_id, session_id, rel_path, size_bytes,
                    file_kind, summary, mime_type, conversation_id,
                    artifact_type, storage_path, created_at, updated_at
                )
                SELECT
                    a.id,
                    a.user_id,
                    a.session_id,
                    'artifacts/' || a.id::text || '.txt',
                    a.size_bytes,
                    'artifact',
                    a.summary,
                    COALESCE(a.mime_type, 'text/plain'),
                    a.conversation_id,
                    a.artifact_type,
                    a.storage_path,
                    a.created_at,
                    COALESCE(a.created_at, now())
                FROM deep_artifacts a
                WHERE NOT EXISTS (
                    SELECT 1 FROM deep_workspace_files w
                    WHERE w.id = a.id
                )
                """
            )
            await conn.execute("DROP TABLE IF EXISTS deep_artifacts")

        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_deep_workspace_files_unique
            ON deep_workspace_files (
                user_id,
                COALESCE(session_id, '00000000-0000-0000-0000-000000000000'::uuid),
                rel_path,
                COALESCE(file_kind, 'workspace')
            )
            """
        )
        try:
            await conn.execute(
                """
                ALTER TABLE user_resource_quotas
                ALTER COLUMN workspace_bytes_limit SET DEFAULT 104857600
                """
            )
        except Exception:
            pass

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