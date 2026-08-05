# config/config.py - 统一配置模块
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional
from decouple import config as decouple_config


class BaseConfig(BaseSettings):
    """基础配置类"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ==================== 基础配置 ====================
    DEBUG: bool = Field(default=False, description="调试模式")
    ENVIRONMENT: str = Field(default="development", description="运行环境")

    # ==================== 数据库配置 ====================
    CMTOOL_AGENT_DB_ENGINE: str = Field(default='postgresql', description="数据库引擎")
    CMTOOL_AGENT_DB_NAME: str = Field(default="postgres", description="数据库名称")
    CMTOOL_AGENT_DB_USER: str = Field(default="postgres", description="数据库用户名")
    CMTOOL_AGENT_DB_PASSWORD: Optional[str] = Field(default="password", description="数据库密码")
    CMTOOL_AGENT_DB_HOST: Optional[str] = Field(default="localhost", description="数据库主机地址")
    CMTOOL_AGENT_DB_PORT: Optional[int] = Field(default=5432, description="数据库端口")

    # ==================== 日志配置 ====================
    log_level: str = Field(default="DEBUG", description="日志级别")
    log_file: str = Field(default="logs/app.log", description="日志文件路径")
    log_rotation: str = Field(default="100 MB", description="日志文件轮转大小")
    log_retention: str = Field(default="30 days", description="日志文件保留时间")

    # ==================== 模型配置 ====================
    model_name: str = Field(default="nebulacoder-cot-v8.0", description="默认模型名称")

    LLM_AGENT_MODEL: str = Field(default="deepseek-v4-flash", description="主 Agent 模型名称（已废弃：主对话改读 user_secrets，仅 test.py 等脚本可用）")
    LLM_AGENT_BASE_URL: str = Field(default="https://api.deepseek.com", description="主 Agent API 地址（已废弃，见 LLM_AGENT_MODEL）")
    LLM_AGENT_API_KEY: Optional[str] = Field(default=None, description="主 Agent API Key（已废弃，见 LLM_AGENT_MODEL）")

    LLM_AUX_MODEL: str = Field(default="nebulacoder-v8.0", description="辅助模型名称（工具判断/摘要/标题）")
    LLM_AUX_BASE_URL: str = Field(
        default="https://nebulacoder-maas.zte.com.cn/v1",
        description="辅助模型 API 地址",
    )
    LLM_AUX_API_KEY: Optional[str] = Field(default=None, description="辅助模型 API Key（请在 .env 中配置）")

    # ==================== MCP 配置 ====================
    MCP_TOOLS_API_URL: str = Field(
        default="https://aip.zte.com.cn/trial/cmtool/api/v1/component/mcp/mine",
        description="MCP 工具列表接口地址（当前用户 my 维度）",
    )
    MCP_ALL_TOOLS_API_URL: str = Field(
        default="https://aip.zte.com.cn/trial/cmtool/api/v1/component/mcp/all",
        description="MCP 全量工具列表接口地址",
    )
    MCP_USE_MOCK: bool = Field(
        default=False,
        description="为 true 时跳过外部接口，直接使用 Mock MCP 列表",
    )
    MCP_FALLBACK_TO_MOCK: bool = Field(
        default=False,
        description="外部接口失败时是否回退到 Mock MCP 列表（开发环境可开启）",
    )
    MCP_MRM_SERVER_URL: str = Field(
        default="http://127.0.0.1:6002/mcp",
        description="本地 mrm_doc_mcp.py HTTP 端点",
    )
    MCP_MRM_MOCK_NAME: str = Field(
        default="mrm",
        description="Mock MRM MCP 在 MultiServerMCPClient 中的名称",
    )
    MCP_MRM_MOCK_ID: int = Field(
        default=1001,
        description="Mock MRM MCP 工具 ID",
    )

    # ==================== 内置工具配置 ====================
    EMP_INFO_API_URL: str = Field(
        default="https://icenterapi.zte.com.cn/zte-km-icenter-addresearch/user/plain/docs/search",
        description="员工信息查询 API 地址",
    )
    ONLINE_SEARCH_MCP_URL: str = Field(
        default="http://nebulacoder.dev.zte.com.cn:40086/mcp",
        description="联网搜索 MCP Server URL（nebulacoder API 封装，供内置 online_search 工具使用）",
    )

    # ==================== WebSocket 超时配置 ====================
    HEARTBEAT_INTERVAL: int = Field(default=30, description="WebSocket 心跳间隔（秒）")
    HEARTBEAT_TIMEOUT: int = Field(default=600, description="WebSocket 心跳超时（秒）")

    # ==================== 其他配置 ====================
    ALLOWED_HOSTS: list = Field(default_factory=lambda: ["localhost"], description="允许的主机列表")

    # Agent 对外的公开访问地址（用于构建分享链接等）
    AGENTA_PUBLIC_BASE_URL: str = Field(
        default="https://aip.zte.com.cn/trial/cmtool/api/agenta",
        description="Agenta 服务对外的公开根 URL，用于构建 HTML 分享链接等",
    )

    # ==================== 连接池与限流 ====================
    DB_POOL_MIN: int = Field(default=5, description="asyncpg 连接池最小连接数")
    DB_POOL_MAX: int = Field(default=20, description="asyncpg 连接池最大连接数")
    PSYCOPG_POOL_MIN: int = Field(default=2, description="LangGraph psycopg 池最小连接数")
    PSYCOPG_POOL_MAX: int = Field(default=10, description="LangGraph psycopg 池最大连接数")
    LLM_MAX_RPM: int = Field(default=15, description="LLM 全局限流 RPM")
    LLM_MIN_INTERVAL: float = Field(default=2.0, description="LLM 最小调用间隔（秒）")
    MCP_CACHE_TTL: int = Field(default=600, description="MCP 工具验证缓存 TTL（秒）")
    MCP_VALIDATE_TIMEOUT: float = Field(default=8.0, description="单个 MCP 连通性探测超时（秒）")
    MCP_USER_TOOLS_CACHE_TTL: int = Field(default=120, description="用户 MCP 工具列表接口响应缓存 TTL（秒）")
    AGENT_CACHE_ENABLED: bool = Field(default=True, description="是否启用 Agent 编译图会话级缓存")
    TOOL_JUDGE_SKIP_MAX_CHARS: int = Field(default=500, description="工具结果低于此字符数时跳过 judge LLM")

    # ==================== Deep Agent 配置 ====================
    AGENT_RUNTIME: str = Field(default="deep", description="Agent 运行时: deep | light")
    WORKSPACE_ROOT: str = Field(
        default="/data/cmtool/agent",
        description=(
            "Deep Agent 工作区根目录。"
            "dev/test/prod 由部署侧将虚机 agent-dev|agent-test|agent-production NFS 挂到本路径，代码不区分环境。"
        ),
    )
    DEEP_TIMEOUT_SECONDS: int = Field(default=1800, description="Deep Agent 单次运行超时（秒）")
    LLM_TEST_TIMEOUT_SECONDS: int = Field(default=30, description="用户 LLM 连接测试超时（秒）")
    LLM_RATE_LIMIT_BASE_WAIT: float = Field(default=10.0, description="429 限流退避基础等待（秒）")
    LLM_RATE_LIMIT_MAX_WAIT: float = Field(default=120.0, description="429 限流退避单次最大等待（秒）")
    HITL_ENABLED: bool = Field(default=False, description="全局 HITL 开关。False 时 Agent 自动执行工具调用，无需人工审批")
    HITL_PENDING_TTL_MINUTES: int = Field(default=30, description="HITL 待审批超时（分钟），超时后自动终止 run")
    HITL_SWEEPER_INTERVAL_SECONDS: int = Field(default=60, description="HITL 过期扫描间隔（秒）；0=禁用")
    DEEP_GLOBAL_MAX_CONCURRENT: int = Field(default=20, description="全局 Deep 并发上限")
    DEEP_USER_MAX_CONCURRENT: int = Field(default=3, description="每用户 Deep 并发上限")
    REASONING_STREAM_ENABLED: bool = Field(
        default=True,
        description="为 true 时 WebSocket 流式推送推理文本（reasoning chunk）；完整推理仍存 reasoning_final",
    )
    ARTIFACT_MAX_BYTES: int = Field(default=262144, description="Artifact 单条大小上限（256KB）")
    ARTIFACT_AUTO_STORE_ENABLED: bool = Field(
        default=False,
        description="为 true 时长工具结果自动写入 artifact；默认仅消息摘要",
    )
    ARTIFACT_MIN_CHARS_TO_STORE: int = Field(
        default=8000,
        description="开启自动存储时，超过此字符数才写 artifact",
    )
    SUBAGENT_TRACE_ARTIFACT_ENABLED: bool = Field(
        default=False,
        description="为 true 时 subagent 结束写 trace artifact",
    )
    CONTENT_SUMMARY_MAX_CHARS: int = Field(default=1000, description="消息/工具结果摘要最大字符数")
    WORKSPACE_QUOTA_BYTES: int = Field(default=524288000, description="每用户工作区配额（500MB，含 skills）")
    SANDBOX_ENABLED: bool = Field(default=True, description="是否启用 Docker 沙箱 execute")
    SANDBOX_MEMORY_LIMIT: str = Field(default="512m", description="Docker 沙箱内存限制")
    SANDBOX_PIDS_LIMIT: int = Field(default=512, description="Docker 沙箱 pids 限制（0=不限制），默认512防止 fork 炸弹")
    SANDBOX_CPU_LIMIT: float = Field(default=0.5, description="Docker 沙箱 CPU 限制")
    SANDBOX_IDLE_TTL_MINUTES: int = Field(default=20, description="空闲沙箱 TTL（分钟）")
    SANDBOX_IDLE_TTL_HOURS: Optional[int] = Field(
        default=None,
        description="已废弃：若设置则覆盖 SANDBOX_IDLE_TTL_MINUTES（兼容旧 env）",
    )
    SANDBOX_IMAGE: str = Field(
        default="langchain-agent-sandbox:3.11",
        description="Docker 沙箱镜像（见 deploy/sandbox/，需先 build/pull）",
    )
    SANDBOX_BASE_IMAGE: str = Field(
        default="langchain-agent-sandbox-base:3.11",
        description="沙箱基础镜像（系统依赖层：Node.js、Chromium、curl 等），由 deploy/sandbox/build_base.sh 构建",
    )
    SANDBOX_MAX_CONCURRENT: int = Field(default=25, description="全站同时 running 的沙箱数量上限")
    SANDBOX_MAX_PER_USER: int = Field(default=3, description="每用户 running 沙箱容器上限")
    SANDBOX_STARTUP_GRACE_SECONDS: int = Field(default=15, description="沙箱 health check 轮询超时（秒）")
    SANDBOX_HARDEN: bool = Field(default=True, description="容器安全加固：只读根文件系统、降权、no-new-privileges")
    SANDBOX_PIP_CONF_PATH: Optional[str] = Field(
        default=None,
        description="挂载到容器内的 pip.conf 宿主机路径；默认 ~/.pip/pip.conf（存在则挂载）",
    )
    SANDBOX_CLEANUP_INTERVAL_MINUTES: int = Field(
        default=5,
        description="后台扫描 ttl_at 过期沙箱并 stop+remove 的间隔（分钟）；0 表示禁用",
    )
    SANDBOX_REMOVE_ON_STOP: bool = Field(
        default=False,
        description="stop_user_sandbox 时是否 docker rm（默认仅 stop，TTL 任务会 remove）",
    )
    SANDBOX_RELEASE_ON_RUN_END: bool = Field(
        default=True,
        description="Run 正常结束或 ERROR 后 stop 沙箱（用完即释，保留容器供 docker start）",
    )
    SANDBOX_RELEASE_ON_STOP: bool = Field(
        default=True,
        description="用户 stop_task / CancelledError 后 stop 沙箱",
    )
    SANDBOX_STOPPED_TTL_MINUTES: int = Field(
        default=25,
        description="stopped 沙箱容器保留时长（分钟），过期后 docker rm",
    )
    SANDBOX_WARMUP_EAGER: bool = Field(
        default=False,
        description="为 true 时 session warmup 阶段即拉起 Docker；默认 false（lazy，首问再拉）",
    )
    SANDBOX_DOCKER_HOST: Optional[str] = Field(
        default=None,
        description="远程 Docker API 地址，如 ssh://sandbox@10.0.0.1；未设置则 docker.from_env()",
    )
    SANDBOX_WORKSPACE_ROOT: Optional[str] = Field(
        default=None,
        description="远程 dockerd 侧 bind mount 工作区根路径；默认与 WORKSPACE_ROOT 相同",
    )
    DEEPAGENTS_VERSION: str = Field(default="0.6.8", description="deepagents 版本锁定")

    # ==================== Store 记忆层 ====================
    STORE_INDEX_ENABLED: bool = Field(default=True, description="是否启用 Store pgvector 语义索引")
    STORE_EMBEDDING_MODEL: Optional[str] = Field(
        default="text-embedding-3-small",
        description="全局 embedding 模型（OpenAI 兼容）",
    )
    STORE_EMBEDDING_DIMS: int = Field(default=1536, description="embedding 向量维度")
    STORE_EMBEDDING_BASE_URL: Optional[str] = Field(
        default=None,
        description="embedding API base_url；默认与 LLM_AUX_BASE_URL 相同",
    )
    STORE_EMBEDDING_API_KEY: Optional[str] = Field(
        default=None,
        description="embedding API key；默认与 LLM_AUX_API_KEY 相同",
    )
    STORE_TTL_SWEEPER_ENABLED: bool = Field(default=True, description="是否启动 Store TTL 后台清理")

    # ==================== Artifact / Tool Spill ====================
    TOOL_SPILL_ENABLED: bool = Field(default=True, description="超长工具结果 spill 到 workspace")
    # 4000 → 2000：工具结果在上下文中的累积是 token 消耗第一大项（占 ~55%）。
    # 调低阈值让更多中长结果转摘要 + workspace 引用（模型可 read_file 看全文），
    # 显著降低历史重读成本，对模型理解影响很小。
    TOOL_SPILL_MIN_CHARS: int = Field(default=2000, description="超过此字符数则 spill")
    TOOL_SPILL_VIRTUAL_DIR: str = Field(default=".tool-cache", description="workspace 内 spill 子目录")

    # ==================== 工具结果 TTL ====================
    # 每次模型调用前零成本清理过期/超量的工具结果（替换为占位符），
    # 释放上下文窗口。已 spill 外置的结果（摘要+路径）不清理。
    TOOL_RESULT_TTL_ENABLED: bool = Field(default=True, description="是否启用工具结果 TTL 清理")
    TOOL_RESULT_TTL_GAP_MINUTES: float = Field(default=5.0, description="时间触发:距上次模型调用超过此分钟数则清理旧工具结果")
    TOOL_RESULT_TTL_COUNT_TRIGGER: int = Field(default=15, description="数量触发:可压缩工具结果超过此数量则清理最旧的")
    TOOL_RESULT_TTL_KEEP_RECENT: int = Field(default=15, description="无论哪种触发,保留最近 N 个工具结果")
    ARTIFACT_DB_MIRROR_ENABLED: bool = Field(
        default=True,
        description="spill 时双写 deep_workspace_files 兼容旧 API",
    )

    # ==================== Observability: Langfuse ====================
    # 自托管 Langfuse (https://langfuse.com) 集成。开启后每次 Deep run 的
    # LLM 调用 / 工具执行 / 子 Agent 分支以 trace 树形式发送到 Langfuse 展示。
    LANGFUSE_ENABLED: bool = Field(default=False, description="是否启用 Langfuse 观测（需同时配置密钥与 Host）")
    LANGFUSE_PUBLIC_KEY: str = Field(default="", description="Langfuse public key (pk-lf-...)")
    LANGFUSE_SECRET_KEY: str = Field(default="", description="Langfuse secret key (sk-lf-...)")
    LANGFUSE_HOST: str = Field(
        default="",
        description="Langfuse 自托管地址，如 http://localhost:3000；留空且未配置环境变量时回退到 Langfuse 云",
    )

    # ==================== User Memory ====================
    USER_MEMORY_INJECT_ENABLED: bool = Field(default=True, description="Deep run 前注入用户记忆")
    USER_MEMORY_INJECT_LIMIT: int = Field(default=5, description="注入记忆条数上限")
    USER_MEMORY_REMEMBER_ENABLED: bool = Field(default=True, description="对话「记住」检测")
    USER_MEMORY_REMEMBER_PATTERNS: str = Field(
        default=r"记住|请记住|保存到记忆|帮我记一下|/remember",
        description="显式记忆写入正则",
    )

    # ==================== Skill 市场 / 仓库 ====================
    SKILL_REPO_ROOT: Optional[str] = Field(
        default=None,
        description="平台 Skill 只读源目录（内置 skill）；默认项目根 skill-repo/",
    )
    SKILL_REPO_VM_ROOT: Optional[str] = Field(
        default=None,
        description="VM 上 Skill 仓库根路径（可读写，存储用户上传和迁移的 skill）；默认 {WORKSPACE_ROOT}/.skill-repo/",
    )
    SKILL_CATALOG_AUTO_SYNC: bool = Field(
        default=True,
        description="应用启动时从 VM skill-repo 同步 skill_catalog 表",
    )
    SKILL_INTERNAL_TOKEN: Optional[str] = Field(
        default="agent-cmtool",
        description="管理接口 /internal/skills/* 鉴权 Token（X-Internal-Token）",
    )
    SKILL_UPLOAD_MAX_BYTES: int = Field(
        default=52428800,
        description="Skill ZIP 上传最大字节数（50MB）",
    )
    SKILL_UPLOAD_MAX_FILES: int = Field(
        default=500,
        description="Skill ZIP 内文件数上限",
    )
    FILE_UPLOAD_MAX_BYTES: int = Field(
        default=10485760,
        description="普通文件上传最大字节数（10MB）",
    )
    FILE_UPLOAD_CHUNK_SIZE: int = Field(
        default=1048576,
        description="分片上传每片大小（1MB）",
    )

    # ==================== 文件解析配置 ====================
    FILE_PARSE_ENABLED: bool = Field(
        default=True,
        description="是否启用后端文件解析（上传后自动解析 Office/PDF/文本/图片）",
    )
    FILE_PARSE_MAX_CHARS: int = Field(
        default=30720,
        description="文件解析最大字符数（30KB），超出后截断",
    )
    FILE_PARSE_TRUNCATE_HEAD: int = Field(
        default=10240,
        description="截断时保留开头字符数（10KB）",
    )
    FILE_PARSE_TRUNCATE_TAIL: int = Field(
        default=5120,
        description="截断时保留结尾字符数（5KB）",
    )
    FILE_PARSE_TIMEOUT: int = Field(
        default=30,
        description="文件解析超时秒数（Layer 1/2 纯文本与 Office 解析超时）",
    )
    FILE_PARSE_IMAGE_TIMEOUT: int = Field(
        default=120,
        description="图片 Vision API 解析超时秒数",
    )

    def is_tool_spill_enabled(self) -> bool:
        """TOOL_SPILL_ENABLED 优先；兼容旧 ARTIFACT_AUTO_STORE_ENABLED。"""
        if self.TOOL_SPILL_ENABLED:
            return True
        return bool(self.ARTIFACT_AUTO_STORE_ENABLED)

    def tool_spill_min_chars(self) -> int:
        if self.TOOL_SPILL_MIN_CHARS > 0:
            return self.TOOL_SPILL_MIN_CHARS
        return self.ARTIFACT_MIN_CHARS_TO_STORE

    def get_workspace_root(self) -> str:
        """返回工作区根路径（Windows 开发环境自动重定位到项目目录）。"""
        import sys
        root = self.WORKSPACE_ROOT
        if sys.platform == "win32" and root.startswith("/data/"):
            from pathlib import Path
            basename = root.rsplit("/", 1)[-1]
            return str(Path(__file__).resolve().parent.parent / "data" / basename)
        return root

    def get_skill_repo_root(self) -> str:
        """返回平台内置 Skill 只读源目录（仅内置 skill）。"""
        from pathlib import Path

        if self.SKILL_REPO_ROOT and str(self.SKILL_REPO_ROOT).strip():
            return str(self.SKILL_REPO_ROOT).strip().rstrip("/\\")
        return str(Path(__file__).resolve().parent.parent / "skill-repo")

    def get_vm_skill_repo_root(self) -> str:
        """返回 VM Skill 仓库路径（可读写，存储用户上传和迁移的 skill）。"""
        if self.SKILL_REPO_VM_ROOT and str(self.SKILL_REPO_VM_ROOT).strip():
            return str(self.SKILL_REPO_VM_ROOT).strip().rstrip("/\\")
        return self.get_workspace_root() + "/.skill-repo"

    def get_sandbox_idle_ttl(self):
        """空闲沙箱 TTL；SANDBOX_IDLE_TTL_HOURS 若设置则覆盖 minutes（兼容旧 env）。"""
        from datetime import timedelta

        if self.SANDBOX_IDLE_TTL_HOURS is not None:
            return timedelta(hours=self.SANDBOX_IDLE_TTL_HOURS)
        return timedelta(minutes=self.SANDBOX_IDLE_TTL_MINUTES)

    def get_sandbox_stopped_ttl(self):
        """stopped 沙箱固定 TTL（自 release 起算）。"""
        from datetime import timedelta

        return timedelta(minutes=self.SANDBOX_STOPPED_TTL_MINUTES)

    def get_db_uri(self) -> str:
        """获取数据库连接 URI"""
        uri = (f"{self.CMTOOL_AGENT_DB_ENGINE}://{self.CMTOOL_AGENT_DB_USER}:"
               f"{self.CMTOOL_AGENT_DB_PASSWORD}@{self.CMTOOL_AGENT_DB_HOST}:"
               f"{self.CMTOOL_AGENT_DB_PORT}/{self.CMTOOL_AGENT_DB_NAME}")
        return uri

    def get_langgraph_db_uri(self) -> str:
        """获取 LangGraph 专用的数据库连接 URI（仅基础连接参数）。

        TCP keepalive 等连接行为参数在 get_pool_kwargs() 中通过 psycopg kwargs 设置。
        """
        return f"{self.get_db_uri()}?sslmode=disable"

    def get_pool_kwargs(self) -> dict:
        """返回 psycopg AsyncConnectionPool 的 connection kwargs。

        包含 TCP keepalive 和 row_factory 设置，确保：
        - idle 连接不会被 PostgreSQL/网络设备断开
        - 活跃期间的瞬时网络故障也能通过池的重连机制恢复
        """
        from psycopg.rows import dict_row

        return {
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "keepalives": "1",
            "keepalives_idle": "60",
            "keepalives_interval": "10",
            "keepalives_count": "5",
        }


class DevConfig(BaseConfig):
    """联调开发环境（本机 10.56.161.57）"""
    DEBUG: bool = True
    ENVIRONMENT: str = "dev"
    MCP_TOOLS_API_URL: str = "https://aip.zte.com.cn/trial/cmtool/api/v1/component/mcp/mine"
    MCP_ALL_TOOLS_API_URL: str = "https://aip.zte.com.cn/trial/cmtool/api/v1/component/mcp/all"
    MCP_USE_MOCK: bool = False
    MCP_FALLBACK_TO_MOCK: bool = False


class TestConfig(BaseConfig):
    """测试环境（10.57.83.71）"""
    DEBUG: bool = True
    ENVIRONMENT: str = "test"
    MCP_TOOLS_API_URL: str = "https://aip.zte.com.cn/trial/cmtool/api/v1/component/mcp/mine"
    MCP_ALL_TOOLS_API_URL: str = "https://aip.zte.com.cn/trial/cmtool/api/v1/component/mcp/all"


class ProductionConfig(BaseConfig):
    """生产环境配置"""
    DEBUG: bool = False
    ENVIRONMENT: str = "production"
    AGENTA_PUBLIC_BASE_URL: str = "https://aip.zte.com.cn/cmtool/api/agenta"
    MCP_TOOLS_API_URL: str = "https://aip.zte.com.cn/cmtool/api/v1/component/mcp/mine"
    MCP_ALL_TOOLS_API_URL: str = "https://aip.zte.com.cn/cmtool/api/v1/component/mcp/all"
    MCP_USE_MOCK: bool = False
    MCP_FALLBACK_TO_MOCK: bool = False
    SKILL_CATALOG_AUTO_SYNC: bool = False


# ==================== 配置工厂 ====================
def get_config() -> BaseConfig:
    """根据环境变量获取对应配置"""
    environment = decouple_config('ENVIRONMENT', default='dev')

    if environment == 'production':
        return ProductionConfig()
    elif environment == 'test':
        return TestConfig()
    elif environment == 'dev':
        return DevConfig()
    # 兼容旧配置
    elif environment == 'development':
        return DevConfig()
    else:
        return DevConfig()


# 全局配置实例
app_config = get_config()
