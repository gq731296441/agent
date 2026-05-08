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
    log_level: str = Field(default="INFO", description="日志级别")
    log_file: str = Field(default="logs/app.log", description="日志文件路径")
    log_rotation: str = Field(default="100 MB", description="日志文件轮转大小")
    log_retention: str = Field(default="30 days", description="日志文件保留时间")

    # ==================== 模型配置 ====================
    model_name: str = Field(default="nebulacoder-cot-v8.0", description="默认模型名称")

    # ==================== MCP 配置 ====================
    MCP_TOOLS_API_URL: str = Field(
        default="https://aip.zte.com.cn/trial/cmtool/api/v1/component/mcp/mine",
        description="MCP 工具列表接口地址"
    )

    # ==================== WebSocket 超时配置 ====================
    HEARTBEAT_INTERVAL: int = Field(default=30, description="WebSocket 心跳间隔（秒）")
    HEARTBEAT_TIMEOUT: int = Field(default=600, description="WebSocket 心跳超时（秒）")

    # ==================== 其他配置 ====================
    ALLOWED_HOSTS: list = Field(default_factory=lambda: ["localhost"], description="允许的主机列表")

    def get_db_uri(self) -> str:
        """获取数据库连接 URI"""
        uri = (f"{self.CMTOOL_AGENT_DB_ENGINE}://{self.CMTOOL_AGENT_DB_USER}:"
               f"{self.CMTOOL_AGENT_DB_PASSWORD}@{self.CMTOOL_AGENT_DB_HOST}:"
               f"{self.CMTOOL_AGENT_DB_PORT}/{self.CMTOOL_AGENT_DB_NAME}?sslmode=disable")
        print(f"数据库连接 URI: {uri}")
        return uri


class DevelopmentConfig(BaseConfig):
    """开发环境配置"""
    DEBUG: bool = True
    ENVIRONMENT: str = "development"
    MCP_TOOLS_API_URL: str = "https://aip.zte.com.cn/trial/cmtool/api/v1/component/mcp/mine"


class ProductionConfig(BaseConfig):
    """生产环境配置"""
    DEBUG: bool = False
    ENVIRONMENT: str = "production"
    MCP_TOOLS_API_URL: str = "https://aip.zte.com.cn/cmtool/api/v1/component/mcp/mine"


# ==================== 配置工厂 ====================
def get_config() -> BaseConfig:
    """根据环境变量获取对应配置"""
    environment = decouple_config('ENVIRONMENT', default='development')

    if environment == 'production':
        return ProductionConfig()
    elif environment == 'development':
        return DevelopmentConfig()
    else:
        return BaseConfig()


# 全局配置实例
app_config = get_config()
