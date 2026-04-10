# config.py
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Config(BaseSettings):

    model_config = SettingsConfigDict(
        env_file=".env",                  # 指定 .env 文件
        env_file_encoding="utf-8",        # 防止中文乱码
        # env_prefix="CMTOOL_AGENT_",             # ← 关键！所有字段自动加这个前缀去匹配环境变量
        extra="ignore",                   # 忽略 .env 中多余的变量（可选，但推荐）
    )

    # ==================== 数据库配置 ====================

    CMTOOL_AGENT_DB_ENGINE: str = Field(
        default='postgresql',
        description="数据库引擎"
    )
    CMTOOL_AGENT_DB_NAME: str = Field(
        default="postgres",
        description="数据库名称"
    )
    CMTOOL_AGENT_DB_USER: str = Field(
        default="postgres",
        description="数据库用户名"
    )
    CMTOOL_AGENT_DB_PASSWORD: Optional[str] = Field(
        default="password",
        description="数据库密码"
    )
    CMTOOL_AGENT_DB_HOST: Optional[str] = Field(
        default="localhost",
        description="数据库主机地址"
    )
    CMTOOL_AGENT_DB_PORT: Optional[str] = Field(
        default=5432,
        description="数据库端口"
    )
    # ==================== 日志配置 ====================
    log_level: str = Field(
        default="INFO",
        description="日志级别：DEBUG, INFO, WARNING, ERROR, CRITICAL"
    )

    log_file: str = Field(
        default="logs/app.log",
        description="日志文件路径"
    )

    log_rotation: str = Field(
        default="100 MB",
        description="日志文件轮转大小"
    )

    log_retention: str = Field(
        default="30 days",
        description="日志文件保留时间"
    )

    # ==================== 模型配置 ====================

    model_name: str = Field(
        default="Qwen3-235B-A22B",
        description="默认模型名称"
    )


    def get_db_uri(self) -> str:
        DB_URI = f"{self.CMTOOL_AGENT_DB_ENGINE}://{self.CMTOOL_AGENT_DB_USER}:{self.CMTOOL_AGENT_DB_PASSWORD}@{self.CMTOOL_AGENT_DB_HOST}:{self.CMTOOL_AGENT_DB_PORT}/{self.CMTOOL_AGENT_DB_NAME}?sslmode=disable"
        print(f"数据库连接 URI: {DB_URI}")  # 调试输出
        return DB_URI

    # ==================== webocket 超时配置 ====================

    HEARTBEAT_INTERVAL: int = Field(
        default=30,
        description="WebSocket 心跳间隔（秒）"
    )

    HEARTBEAT_TIMEOUT: int = Field(
        default=600,
        description="WebSocket 心跳超时（秒）"
    )

app_config = Config()
