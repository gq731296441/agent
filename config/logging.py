"""
日志配置模块
使用 loguru 提供统一的日志管理
"""

import logging as _stdlib_logging
import sys
from pathlib import Path
from typing import Optional
from loguru import logger

from .config import app_config


class _InterceptHandler(_stdlib_logging.Handler):
    """把标准库 logging 的记录转发到 loguru。

    marketplace 等模块仍使用 `logging.getLogger(__name__)`，若不做拦截，
    这些日志会走 stdlib 默认 handler（无输出/仅 stderr），无法落盘。
    """

    def emit(self, record: _stdlib_logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except (ValueError, KeyError):
            level = record.levelno
        # 动态定位调用栈中第一个「非 logging 内部」的帧，作为 loguru 的 depth，
        # 使日志中的模块名/行号指向真实调用处而非 logging 内部。
        frame = _stdlib_logging.currentframe()
        depth = 1
        while frame is not None:
            frame = frame.f_back
            if frame is None:
                break
            mod = frame.f_globals.get("__name__", "")
            if mod != "logging" and not mod.startswith("logging."):
                break
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def install_stdlib_interceptor(level: str = "INFO") -> None:
    """注册 stdlib → loguru 拦截器（幂等，可重复调用）。

    uvicorn 初始化时会重置 root logger，因此除模块导入期外，
    应用 lifespan 启动阶段需再调用一次兜底。
    """
    root = _stdlib_logging.getLogger()
    for h in list(root.handlers):
        if isinstance(h, _InterceptHandler):
            return
    root.addHandler(_InterceptHandler())
    # root 的 effective level 在 NOTSET 时会退化为 WARNING，
    # 必须显式设置，否则 INFO 级业务日志仍会被丢弃。
    root.setLevel(level.upper() if isinstance(level, str) else _stdlib_logging.INFO)
    # 第三方库噪声压制（这些库的 DEBUG/INFO 会淹没业务日志）
    for noisy in (
        "urllib3", "httpx", "httpcore", "asyncio", "multipart",
        "python_multipart", "watchfiles", "aiosqlite", "langchain",
        "langgraph", "openai", "anthropic",
    ):
        _stdlib_logging.getLogger(noisy).setLevel(_stdlib_logging.WARNING)


def setup_logging(
    log_level: Optional[str] = None,
    log_file: Optional[str] = None,
    rotation: Optional[str] = None,
    retention: Optional[str] = None,
) -> None:
    """
    配置应用日志系统
    
    使用 loguru 提供结构化日志，支持：
    - 控制台彩色输出
    - 文件日志轮转
    - 自动清理过期日志
    - 异常追踪
    
    Args:
        log_level: 日志级别，默认从配置读取
        log_file: 日志文件路径，默认从配置读取
        rotation: 日志轮转规则，默认从配置读取
        retention: 日志保留时间，默认从配置读取
    """
    # 使用配置中的默认值
    log_level = log_level or app_config.log_level
    log_file = log_file or app_config.log_file
    rotation = rotation or app_config.log_rotation
    retention = retention or app_config.log_retention
    
    # 移除默认的 handler
    logger.remove()

    is_production = (app_config.ENVIRONMENT or "").lower() == "production"
    diagnose = not is_production
    
    # ==================== 控制台日志 ====================
    # 添加彩色控制台输出，格式化更易读
    logger.add(
        sys.stderr,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        level=log_level,
        colorize=True,
        backtrace=True,  # 显示完整的异常追踪
        diagnose=diagnose,
    )
    
    # ==================== 文件日志 ====================
    # 确保日志目录存在
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # 添加文件日志，支持轮转和自动清理
    logger.add(
        log_file,
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
            "{level: <8} | "
            "{name}:{function}:{line} | "
            "{message}"
        ),
        level=log_level,
        rotation=rotation,      # 文件大小达到限制时轮转
        retention=retention,    # 保留指定时间的日志
        compression="zip",      # 压缩旧日志
        backtrace=True,
        diagnose=diagnose,
        enqueue=True,          # 异步写入，提高性能
    )

    # 接管标准库 logging（marketplace 等模块使用 logging.getLogger）
    install_stdlib_interceptor(log_level)

    # 抑制 LangGraph Store batch 后台 Task 被取消时的 asyncio 噪音警告
    # "Task was destroyed but it is pending!" — 无害，是 langgraph 内部清理时序问题
    # 注意：必须在 install_stdlib_interceptor 之后设置，否则会被 root level 覆盖
    _stdlib_logging.getLogger("asyncio").setLevel(_stdlib_logging.CRITICAL)

    logger.info(f"📝 日志系统初始化完成 - 级别: {log_level}, 文件: {log_file}")


def get_logger(name: str):
    """
    获取指定名称的 logger
    
    Args:
        name: logger 名称，通常使用模块的 __name__
        
    Returns:
        配置好的 logger 实例
        
    Example:
        >>> from config import get_logger
        >>> logger = get_logger(__name__)
        >>> logger.info("这是一条日志")
    """
    return logger.bind(name=name)


# 在模块导入时自动初始化日志系统
if "pytest" not in sys.modules:
    setup_logging()

