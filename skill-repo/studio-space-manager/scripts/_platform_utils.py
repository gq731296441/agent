#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨平台工具模块 (Cross-Platform Utilities)
==========================================

提供 Linux 和 Windows 双平台支持的公共工具函数。

支持：
- ✅ Linux (Ubuntu/CentOS/Debian)
- ✅ Windows 10/11
- ✅ macOS (通过 Python)

功能：
- 平台检测
- 路径处理
- 目录权限设置
- 标准输入/输出/错误尽量使用 UTF-8（`configure_stdio_utf8`），减轻 Windows 控制台默认代码页下的中文与符号输出异常，并统一管道输入编码

作者：Co-Claw Team
创建：2026-03-07
更新：2026-03-07
"""

import io
import os
import platform
import sys
import tempfile
from typing import Tuple, Optional


def get_platform_and_user_agent() -> Tuple[str, str]:
    """
    返回 (平台标识, HTTP User-Agent)，供请求头与日志使用。
    与业务脚本中历史实现保持一致，便于统一维护。
    """
    system = platform.system()
    if system == "Windows":
        return (
            "windows",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        )
    if system == "Darwin":
        return (
            "macos",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        )
    return (
        "linux",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/138.0.0.0 Safari/537.36",
    )


def get_env_with_fallback(env_name: str, fallback: str) -> str:
    """读取环境变量，未设置时返回 fallback。"""
    val = os.getenv(env_name)
    return val if val is not None else fallback


def configure_stdio_utf8() -> None:
    """
    将 stdin / stdout / stderr 尽量设为 UTF-8，统一 Linux 与 Windows 下的文本流行为：

    - **stdout / stderr**：避免 Windows 控制台默认代码页下 print 中文、符号、emoji 时触发
      UnicodeEncodeError；无法映射的字符用替换策略，保证不因编码失败而中断。
    - **stdin**：管道或重定向输入为 UTF-8 时，避免按系统默认编码误读（尤其 Windows）。

    在已是 UTF-8 或无法改写时静默跳过。
    """
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "buffer"):
            continue
        enc = getattr(stream, "encoding", None) or ""
        if enc.lower() in ("utf-8", "utf8"):
            continue
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
            else:
                # Python 3.6：无 reconfigure 时用 TextIOWrapper 包装 buffer
                line_buf = getattr(stream, "line_buffering", stream_name != "stdin")
                setattr(
                    sys,
                    stream_name,
                    io.TextIOWrapper(
                        stream.buffer,
                        encoding="utf-8",
                        errors="replace",
                        line_buffering=line_buf,
                    ),
                )
        except (AttributeError, OSError, ValueError, io.UnsupportedOperation):
            continue


def detect_platform() -> Tuple[str, str, bool]:
    """
    检测当前运行平台

    Returns:
        Tuple[系统类型, 系统版本, 是否为Windows]
        例如: ('Windows', '10', True) 或 ('Linux', '4.19.112-2.el8.x86_64', False)
    """
    system = platform.system()  # Windows, Linux, Darwin
    release = platform.release()
    is_windows = system == "Windows"
    return system, release, is_windows


def get_script_dir() -> str:
    """
    获取脚本所在目录（跨平台兼容）

    Returns:
        脚本所在目录的绝对路径
    """
    script_path = os.path.dirname(os.path.abspath(__file__))
    return script_path


def get_skill_root_dir() -> str:
    """
    获取技能根目录（跨平台兼容）

    Returns:
        技能根目录的绝对路径
    """
    script_dir = get_script_dir()
    skill_root = os.path.dirname(script_dir)
    return skill_root


def get_log_dir() -> str:
    """
    获取日志目录（跨平台兼容）

    优先使用技能根目录下的 ``log``（与脚本同级的 ``studio-space-manager/log``），创建失败时回退到系统临时目录。

    Returns:
        日志目录的绝对路径
    """
    skill_root = get_skill_root_dir()
    log_dir = os.path.join(skill_root, "log")

    # 确保日志目录存在
    try:
        os.makedirs(log_dir, exist_ok=True)
        # 设置目录权限（仅 Linux）
        system, _, is_windows = detect_platform()
        if not is_windows and os.name == "posix":
            os.chmod(log_dir, 0o700)  # rwx------
    except OSError:
        # 如果创建失败，使用临时目录
        log_dir = tempfile.gettempdir()

    return log_dir


def get_data_dir() -> str:
    """
    获取数据存储目录（跨平台兼容）

    Returns:
        数据目录的绝对路径
    """
    skill_root = get_skill_root_dir()
    data_dir = os.path.join(skill_root, "data")

    # 确保数据目录存在
    try:
        os.makedirs(data_dir, exist_ok=True)
        # 设置目录权限（仅 Linux）
        system, _, is_windows = detect_platform()
        if not is_windows and os.name == "posix":
            os.chmod(data_dir, 0o700)  # rwx------
    except OSError:
        # 如果创建失败，使用临时目录
        import tempfile

        data_dir = tempfile.gettempdir()

    return data_dir


def get_audit_log_file() -> str:
    """
    获取审计日志文件路径（跨平台兼容）

    Returns:
        审计日志文件的绝对路径
    """
    log_dir = get_log_dir()
    return os.path.join(log_dir, "audit.log")


def get_config_file(filename: str) -> str:
    """
    获取配置文件路径（跨平台兼容）

    Args:
        filename: 配置文件名

    Returns:
        配置文件的绝对路径
    """
    data_dir = get_data_dir()
    return os.path.join(data_dir, filename)


# 导出平台信息
SYSTEM, SYSTEM_RELEASE, IS_WINDOWS = detect_platform()
