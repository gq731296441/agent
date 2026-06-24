#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=================================================================
studio-space-manager - 我浏览的页面查询模块 (v1.1.0)
=================================================================
iCenter 空间管理技能 - 查询当前用户浏览过的页面

功能：
- 查询当前用户浏览过的页面列表
- 支持分页查询
- 返回页面ID、标题、浏览时间、所在空间等信息
- ✅ 使用 POST 请求（v1.0.0）
- ✅ 支持按日期/时段/区间筛选（v1.1.0）

跨平台支持：
- ✅ Linux (Ubuntu/CentOS/Debian)
- ✅ Windows 10/11
- ✅ macOS (通过 Python)

使用方式：
  # Linux / macOS
  python3 space_visited_pages.py [选项]

  # Windows
  python space_visited_pages.py [选项]

选项：
  --start         起始位置（默认 0）
  --count         返回数量（默认 100，最大 100）
  --date          日期/时段筛选（默认 today）
  --json          输出 JSON 格式（用于程序调用）

依赖：
- Python 3.6+
- requests

作者：Co-Claw Team
创建：2026-03-10
"""

import requests
import json
import argparse
import os
import platform
import tempfile
from typing import Tuple

from system_param import (
    build_icenter_api_url,
    build_system_param,
    get_user_and_token as _get_user_and_token,
)
from _platform_utils import configure_stdio_utf8
from date_filter_utils import resolve_date_range, filter_items_by_date


def get_user_and_token() -> tuple:
    """返回 (user_id, token)，优先从环境变量获取（非 strict）。"""
    return _get_user_and_token(strict=False)


def get_auth_headers():
    """获取认证请求头"""
    emp_no, token = _get_user_and_token(strict=False)
    emp_no = (emp_no or "").strip()
    token = (token or "").strip()

    if not emp_no or not token:
        raise ValueError(
            "未找到认证信息，请确保环境变量 coclaw_empno 和 coclaw_token 已设置，如仍报错请重启 Co-Claw 服务后重试"
        )

    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Pragma": "no-cache",
        "User-Agent": "co-claw-skill",
        "X-Auth-Value": token,
        "X-Emp-No": emp_no,
        "X-Lang-Id": "zh_CN",
        "X-Tenant-Id": "ZTE",
    }


def search_visited_pages(emp_no, token, start=0, count=100, date_filter="today"):
    """
    查询当前用户浏览过的页面

    Args:
        emp_no: 用户工号
        token: 认证令牌
        start: 起始位置，默认 0
        count: 返回数量，默认 100

    Returns:
        dict: 查询结果
    """
    if not emp_no or not token:
        return {"status": "error", "message": "缺少必需参数：emp_no 和 token"}

    # POST 请求 URL
    url = build_icenter_api_url("/zte-rd-icenter-advanced/visit/page")

    # 构建 POST 请求体
    params = {
        "spaceId": "undefined",
        "environmentType": "browser",
        "system": build_system_param("space"),
    }

    # 分页参数放入请求体
    data = {"start": start, "count": min(count, 100)}  # 最多返回100条

    headers = get_auth_headers()
    # POST 请求需要设置 Content-Type
    headers["Content-Type"] = "application/json"

    # 构建带参数的 URL
    param_str = f"spaceId={params['spaceId']}&environmentType={params['environmentType']}&system={params['system']}"
    full_url = f"{url}?{param_str}"

    try:
        response = requests.post(full_url, headers=headers, json=data, timeout=30)
        response.raise_for_status()

        result = response.json()

        # 检查返回码是否为成功
        code_info = result.get("code", {})
        if (
            code_info.get("code") == "0000"
            and "bo" in result
            and "items" in result["bo"]
        ):
            bo = result["bo"]
            items = bo.get("items", [])
            total = bo.get("totalRow", len(items))

            # 提取需要的信息
            results = []
            for item in items:
                # 处理浏览时间
                view_time = item.get("viewTime", "")
                view_time_str = ""
                if view_time:
                    try:
                        # 解析 ISO 格式时间 2026-03-10T09:43:31+08:00
                        if "T" in view_time:
                            # 提取日期和时间部分
                            view_time_str = view_time.replace("T", " ")[:19]
                        else:
                            view_time_str = view_time
                    except (ValueError, OSError, AttributeError):
                        view_time_str = str(view_time)

                result_item = {
                    "contentId": item.get("contentId", ""),
                    "title": item.get("title", ""),
                    "author": item.get("author", ""),
                    "contentFrom": item.get("contentFrom", ""),
                    "viewTime": view_time_str,
                    "spaceId": item.get("spaceId", ""),
                    "spaceName": item.get("spaceName", ""),
                }
                results.append(result_item)

            start_iso, end_iso = resolve_date_range(date_filter)
            filtered_results = filter_items_by_date(
                results, "viewTime", start_iso, end_iso
            )

            # 处理分页信息 - 分页时需要后端返回的总条数，但最终结果不返回
            page_info = {
                "start": bo.get("start", start),
                "count": bo.get("count", len(items)),
                "totalRow": bo.get("totalRow", total),
            }

            return {
                "status": "success",
                "returned": len(filtered_results),
                "pageInfo": page_info,
                "results": filtered_results,
                "dateRange": {"start": start_iso, "end": end_iso},
            }
        else:
            error_msg = code_info.get("msg", "未知错误")
            return {
                "status": "error",
                "message": f"API 返回异常: {error_msg}",
                "code": code_info.get("code", ""),
                "response": result,
                "debug_url": full_url,
                "debug_headers": {
                    k: v if k != "X-Auth-Value" else "***" for k, v in headers.items()
                },
            }

    except requests.exceptions.RequestException as e:
        return {"status": "error", "message": f"请求失败: {str(e)}"}
    except Exception as e:
        return {"status": "error", "message": f"处理失败: {str(e)}"}


def format_result_as_markdown(result):
    """
    将查询结果格式化为Markdown格式

    Args:
        result: 结果字典

    Returns:
        str: Markdown格式的文本
    """
    if result.get("status") == "success":
        returned = result.get("returned", 0)
        date_range = result.get("dateRange", {})
        results = result.get("results", [])

        output = f"# 我浏览过的页面\n\n"
        if date_range:
            output += (
                f"- **时间范围**: {date_range.get('start', '')} ~ {date_range.get('end', '')}\n"
            )
        output += f"- **返回**: {returned} 条结果\n\n"

        if results:
            output += "---\n\n"
            for idx, item in enumerate(results, 1):
                title = item.get("title", "无标题")
                content_id = item.get("contentId", "")
                author = item.get("author", "")
                content_from = item.get("contentFrom", "")
                view_time = item.get("viewTime", "")
                space_name = item.get("spaceName", "")
                space_id = item.get("spaceId", "")

                output += f"## {idx}. {title}\n\n"

                # 构建页面链接
                if content_id and space_id:
                    page_url = f"https://i.zte.com.cn/#/shared/{space_id}/wiki/page/{content_id}/view"
                    output += f"🔗 [查看页面]({page_url})\n\n"

                # 元信息
                meta_info = []
                if space_name:
                    meta_info.append(f"**所在空间**: {space_name}")
                if author:
                    meta_info.append(f"**作者**: {author}")
                if view_time:
                    meta_info.append(f"**浏览时间**: {view_time}")
                if content_from:
                    meta_info.append(f"**内容类型**: {content_from}")

                if meta_info:
                    output += " | ".join(meta_info) + "\n\n"

                output += "---\n\n"
        else:
            output += "\n未找到浏览记录。\n"

        return output
    else:
        error_msg = result.get("message", "未知错误")
        output = f"# 查询失败\n\n"
        output += f"❌ {error_msg}\n"
        if result.get("code"):
            output += f"\n**错误代码**: `{result.get('code')}`\n"
        return output


def format_result_as_json(result):
    """
    将查询结果格式化为JSON格式（程序调用使用）

    Args:
        result: 结果字典

    Returns:
        str: JSON格式的文本
    """
    return json.dumps(result, ensure_ascii=False, indent=2)


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


# 初始化平台信息
SYSTEM, SYSTEM_RELEASE, IS_WINDOWS = detect_platform()


if __name__ == "__main__":
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(
        description="查询当前用户浏览过的页面（支持按日期/时段/区间筛选）",
        epilog="提示: 工号和 Token 从环境变量 coclaw_empno 和 coclaw_token 获取",
    )
    parser.add_argument("--start", dest="start", type=int, default=0, help="起始位置（默认 0）")
    parser.add_argument(
        "--count", dest="count", type=int, default=100, help="返回数量，默认 100，最大 100"
    )
    parser.add_argument(
        "--date",
        dest="date_filter",
        default="today",
        help=(
            "日期筛选：today/今天、yesterday/昨天、前天、YYYY-MM-DD、M-D、M月D日；"
            "时段支持本周/上周/本月/今年；区间支持 2026-03-01~2026-03-10 或 3-1 到 3-10"
        ),
    )
    parser.add_argument(
        "--json", dest="json_output", action="store_true", help="输出 JSON 格式（用于程序调用）"
    )

    args = parser.parse_args()

    # 从环境变量获取工号和token
    emp_no, token = get_user_and_token()

    if not emp_no or not token:
        error_result = {
            "status": "error",
            "message": "缺少必需参数：emp_no 和 token。请设置环境变量 coclaw_empno 和 coclaw_token",
        }
        if args.json_output:
            print(format_result_as_json(error_result))
        else:
            print(format_result_as_markdown(error_result))
        exit(1)

    result = search_visited_pages(
        emp_no,
        token,
        start=args.start,
        count=args.count,
        date_filter=args.date_filter,
    )

    if args.json_output:
        print(format_result_as_json(result))
    else:
        print(format_result_as_markdown(result))
