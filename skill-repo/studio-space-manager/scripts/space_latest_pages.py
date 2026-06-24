#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=================================================================
studio-space-manager - 空间最新页面列表模块 (v1.1.0)
=================================================================
iCenter 空间管理技能 - 获取空间最新页面列表功能

功能：
- 获取指定空间的最新更新页面列表
- 支持分页查询（offset/limit）
- 返回页面ID、标题、操作类型、操作人、更新时间等

跨平台支持：
- ✅ Linux (Ubuntu/CentOS/Debian)
- ✅ Windows 10/11
- ✅ macOS (通过 Python)

使用方式：
  # Linux / macOS
  python3 space_latest_pages.py <空间ID> [选项]

  # Windows
  python space_latest_pages.py <空间ID> [选项]

选项：
  --offset        起始偏移（默认 0）
  --limit         返回数量（默认 20，最大 100）

依赖：
- Python 3.6+
- requests

作者：Co-Claw Team
创建：2026-03-09
更新：2026-03-09
"""

import requests
import json
import argparse
import os
import platform
import tempfile
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple

from system_param import build_icenter_api_url, get_user_and_token
from _platform_utils import configure_stdio_utf8


# =============================================================================
# 跨平台支持 - 平台检测
# =============================================================================


def detect_platform() -> Tuple[str, str, bool]:
    """
    检测当前操作系统平台。

    Returns:
        Tuple[系统类型, 系统版本, 是否为Windows]
    """
    system = platform.system()
    release = platform.release()
    is_windows = system == "Windows"
    return system, release, is_windows


def get_log_dir() -> str:
    """
    获取日志目录，支持跨平台运行。
    """
    system, _, is_windows = detect_platform()

    if is_windows or os.name != "posix":
        temp_base = tempfile.gettempdir()
        log_dir = os.path.join(temp_base, "studio-space-manager", "log")
    else:
        log_dir = os.path.join(
            os.path.expanduser("~"),
            ".openclaw",
            "workspace",
            "skills",
            "studio-space-manager",
            "log",
        )

    try:
        os.makedirs(log_dir, exist_ok=True)
        if not is_windows and os.name == "posix":
            os.chmod(log_dir, 0o700)
    except Exception:
        temp_base = tempfile.gettempdir()
        log_dir = os.path.join(temp_base, "studio-space-manager", "log")
        os.makedirs(log_dir, exist_ok=True)

    return log_dir


def get_platform_string() -> str:
    """
    获取当前平台的字符串表示，用于 User-Agent。
    """
    system, _, is_windows = detect_platform()

    if is_windows:
        return "Windows"
    elif system == "Darwin":
        return "macOS"
    elif system == "Linux":
        return "Linux"
    else:
        return system


def get_user_agent() -> str:
    """
    获取当前平台的 User-Agent 字符串。
    """
    system, _, is_windows = detect_platform()

    if is_windows:
        return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
    elif system == "Darwin":
        return "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
    else:
        return "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"


# 初始化平台信息
SYSTEM, SYSTEM_RELEASE, IS_WINDOWS = detect_platform()


def get_latest_pages(space_id, emp_no, token, offset=0, limit=20):
    """
    获取空间最新页面列表

    Args:
        space_id: 空间ID
        emp_no: 用户工号
        token: 认证令牌
        offset: 起始偏移（默认 0）
        limit: 返回数量（默认 20，最大 100）

    Returns:
        dict: 查询结果
    """
    if not space_id:
        return {"status": "error", "message": "缺少必需参数：space_id（空间ID）"}

    if not emp_no or not token:
        return {"status": "error", "message": "缺少必需参数：emp_no 和 token"}

    # 构建URL
    base_url = build_icenter_api_url("/zte-rd-icenter-contents/content/latest")
    url = f"{base_url}?spaceId={space_id}&offset={offset}&limit={limit}"

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "User-Agent": get_user_agent(),
        "X-Auth-Value": token,
        "X-Emp-No": emp_no,
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        result = response.json()

        # 检查返回码是否为成功
        code_info = result.get("code", {})
        if code_info.get("code") == "0000" and "bo" in result:
            bo = result["bo"]
            pages = bo if isinstance(bo, list) else []

            # 提取需要的信息
            results = []
            for item in pages:
                # 处理更新时间
                update_date = item.get("updateDate", "")
                update_date_str = ""
                if update_date:
                    try:
                        # 解析 ISO 8601 格式: 2026-03-09T10:04:34+08:00
                        # 兼容旧版 Python
                        from datetime import timedelta

                        if "+08:00" in update_date:
                            update_date = update_date.replace("+08:00", "+0800")
                        # 手动解析
                        if update_date.endswith("+0800"):
                            date_part = update_date[:19]
                            dt = datetime.strptime(date_part, "%Y-%m-%dT%H:%M:%S")
                            update_date_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                        else:
                            update_date_str = update_date[:19].replace("T", " ")
                    except (ValueError, OSError, AttributeError):
                        update_date_str = (
                            update_date[:19].replace("T", " ") if update_date else ""
                        )

                result_item = {
                    "id": item.get("id", ""),
                    "title": item.get("title", ""),
                    "contentType": item.get("contentType", ""),
                    "type": item.get("type", ""),
                    "source": item.get("source", ""),
                    "level": item.get("level", ""),
                    "operateType": item.get("operateType", ""),
                    "operatorName": item.get("operatorName", ""),
                    "parentPath": item.get("parentPath", ""),
                    "updateDate": update_date_str,
                    "updateDateRaw": item.get("updateDate", ""),
                }
                results.append(result_item)

            return {
                "status": "success",
                "spaceId": space_id,
                "total": len(results),
                "offset": offset,
                "limit": limit,
                "pages": results,
            }
        else:
            error_msg = code_info.get("msg", code_info.get("msgId", "未知错误"))
            return {
                "status": "error",
                "message": f"API 返回异常: {error_msg}",
                "code": code_info.get("code", ""),
                "response": result,
            }

    except requests.exceptions.RequestException as e:
        return {"status": "error", "message": f"请求失败: {str(e)}"}
    except Exception as e:
        return {"status": "error", "message": f"处理失败: {str(e)}"}


def format_result_as_markdown(result, space_id=""):
    """
    将查询结果格式化为Markdown格式

    Args:
        result: 结果字典
        space_id: 空间ID（可选，用于显示）

    Returns:
        str: Markdown格式的文本
    """
    if result.get("status") == "success":
        space_id = result.get("spaceId", space_id)
        offset = result.get("offset", 0)
        limit = result.get("limit", 20)
        pages = result.get("pages", [])
        total = result.get("total", 0)

        output = f"# 空间最新页面列表\n\n"
        output += f"**空间ID**: `{space_id}`\n\n"
        output += f"- **起始位置**: 第 {offset + 1} 条\n"
        output += f"- **返回数量**: {limit} 条\n"
        output += f"- **实际返回**: {total} 条\n\n"

        if pages:
            output += "---\n\n"
            for idx, item in enumerate(pages, offset + 1):
                title = item.get("title", "无标题")
                operate_type = item.get("operateType", "")
                operator_name = item.get("operatorName", "")
                update_date = item.get("updateDate", "")
                content_type = item.get("contentType", "")
                page_id = item.get("id", "")

                output += f"## {idx}. {title}\n\n"

                if page_id:
                    output += f"**页面ID**: `{page_id}`\n\n"

                # 元信息
                meta_info = []
                if operate_type:
                    meta_info.append(f"**操作**: {operate_type}")
                if operator_name:
                    meta_info.append(f"**操作人**: {operator_name}")
                if update_date:
                    meta_info.append(f"**时间**: {update_date}")
                if content_type:
                    meta_info.append(f"**类型**: {content_type}")

                if meta_info:
                    output += " | ".join(meta_info) + "\n\n"

                output += "---\n\n"
        else:
            output += "\n该空间暂无最新页面。\n"

        return output
    else:
        error_msg = result.get("message", "未知错误")
        output = f"# 查询失败\n\n"
        output += f"❌ {error_msg}\n"
        if result.get("code"):
            output += f"\n**错误代码**: `{result.get('code')}`\n"
        return output


if __name__ == "__main__":
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(
        description="获取 ZTE iCenter 空间最新页面列表",
        epilog="提示: 工号和 Token 从环境变量 coclaw_empno 和 coclaw_token 获取",
    )
    parser.add_argument("space_id", help="空间ID（必填）")
    parser.add_argument(
        "--offset", dest="offset", type=int, default=0, help="起始偏移（默认 0，可选）"
    )
    parser.add_argument(
        "--limit", dest="limit", type=int, default=20, help="返回数量（默认 20，最大 100，可选）"
    )

    args = parser.parse_args()

    # 从环境变量获取工号和token
    emp_no, token = get_user_and_token(strict=False)

    if not emp_no or not token:
        error_result = {
            "status": "error",
            "message": "缺少必需参数：emp_no 和 token。请设置环境变量 coclaw_empno 和 coclaw_token",
        }
        print(format_result_as_markdown(error_result, args.space_id))
        exit(1)

    result = get_latest_pages(
        args.space_id,
        emp_no,
        token,
        offset=args.offset,
        limit=args.limit,
    )
    print(format_result_as_markdown(result, args.space_id))
