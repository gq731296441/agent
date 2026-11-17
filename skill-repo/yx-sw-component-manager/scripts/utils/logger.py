#!/usr/bin/env python3
"""
日志工具配置模块
提供统一的日志配置和管理
"""

import logging
import sys
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

class LoggerConfig:
    """日志配置类"""

    DEFAULT_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    DETAILED_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'

    @staticmethod
    def setup_logger(name: str = None,
                    level: int = logging.INFO,
                    log_file: Optional[str] = None,
                    log_dir: Optional[str] = None,
                    detailed: bool = False,
                    console: bool = True) -> logging.Logger:
        """
        配置日志记录器

        Args:
            name: 日志记录器名称，None则返回root logger
            level: 日志级别
            log_file: 日志文件路径（相对路径或绝对路径）
            log_dir: 日志目录，与log_file配合使用
            detailed: 是否使用详细格式（包含文件行号）
            console: 是否输出到控制台

        Returns:
            配置好的Logger实例
        """
        logger = logging.getLogger(name)
        logger.setLevel(level)

        # 清除现有处理器
        logger.handlers.clear()

        # 格式化器
        fmt = LoggerConfig.DETAILED_FORMAT if detailed else LoggerConfig.DEFAULT_FORMAT
        formatter = logging.Formatter(fmt)

        # 控制台处理器
        if console:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(level)
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)

        # 文件处理器
        if log_file or log_dir:
            if log_file:
                log_path = Path(log_file)
                if not log_path.is_absolute() and log_dir:
                    log_path = Path(log_dir) / log_file
            elif log_dir:
                # 生成基于时间的日志文件名
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                log_file = f"wx_sw_component_list_check_{timestamp}.log"
                log_path = Path(log_dir) / log_file

            log_path.parent.mkdir(parents=True, exist_ok=True)

            file_handler = logging.FileHandler(log_path, encoding='utf-8')
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

            logger.info(f"日志将同时保存到: {log_path}")

        return logger

def get_logger(name: str = None) -> logging.Logger:
    """
    获取指定名称的日志记录器

    Args:
        name: 记录器名称

    Returns:
        Logger实例
    """
    return logging.getLogger(name)

def setup_skill_logger(log_dir: str = None, level: int = logging.INFO) -> logging.Logger:
    """
    设置技能专用的日志记录器

    Args:
        log_dir: 日志目录，默认使用当前目录的 logs 子目录
        level: 日志级别

    Returns:
        配置好的logger
    """
    if log_dir is None:
        log_dir = Path.cwd() / 'logs'

    return LoggerConfig.setup_logger(
        name='wx_sw_component_list_check',
        level=level,
        log_dir=log_dir,
        detailed=True,
        console=True
    )

# debug级别日志便捷函数
def debug_logger():
    """快速设置debug级别的日志"""
    return LoggerConfig.setup_logger(level=logging.DEBUG, detailed=True)

# 静默模式
def silent_logger():
    """仅记录错误和警告"""
    return LoggerConfig.setup_logger(level=logging.WARNING)

if __name__ == "__main__":
    # 测试日志配置
    logger = setup_skill_logger(log_dir='./logs')
    logger.info("日志系统测试 - Info")
    logger.debug("日志系统测试 - Debug")
    logger.warning("日志系统测试 - Warning")
    logger.error("日志系统测试 - Error")