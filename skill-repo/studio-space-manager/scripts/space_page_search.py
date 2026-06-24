#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=================================================================
studio-space-manager - 空间页面搜索模块 (v2.8.0)
=================================================================
iCenter 空间管理技能 - 空间页面搜索功能

功能：
- 搜索 ZTE iCenter 空间中的页面/文档
- 支持关键词搜索、标题搜索、创建人筛选、空间筛选
- 返回标题、内容摘要、空间ID和链接

跨平台支持：
- ✅ Linux (Ubuntu/CentOS/Debian)
- ✅ Windows 10/11
- ✅ macOS (通过 Python)

使用方式：
  # Linux / macOS
  python3 space_page_search.py "关键词" [选项]

  # Windows
  python space_page_search.py "关键词" [选项]

选项：
  --title-model     是否标题搜索（默认 False，表示全文搜索）
  --create-by       创建人工号（可选，多个用逗号分隔）
  --space-id        空间ID（可选，多个用逗号分隔）
  --page-size       每页数量（默认 15）
  --page-num        页码（默认 1）

依赖：
- Python 3.6+
- requests

作者：Co-Claw Team
创建：2026-02-26
更新：2026-03-07
"""

import requests
import json
import argparse
import os
import platform
import tempfile
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple

from system_param import (
    build_icenter_api_url,
    get_user_and_token as _get_user_and_token,
)
from _platform_utils import configure_stdio_utf8


def parse_date(date_str: Optional[str], end_of_day: bool = False) -> Optional[datetime]:
    """解析 YYYY-MM-DD 日期字符串。"""
    if not date_str:
        return None
    try:
        base = datetime.strptime(date_str.strip(), "%Y-%m-%d")
        if end_of_day:
            return base + timedelta(hours=23, minutes=59, seconds=59)
        return base
    except ValueError:
        raise argparse.ArgumentTypeError("日期格式必须为 YYYY-MM-DD")


def _in_date_range(
    item: Dict[str, Any], start_date: Optional[datetime], end_date: Optional[datetime]
) -> bool:
    """按 createTime/lastUpdatedTime 过滤结果。"""
    if start_date is None and end_date is None:
        return True
    candidates: List[datetime] = []
    for key in ("createTime", "lastUpdatedTime"):
        raw = item.get(key)
        if not raw:
            continue
        try:
            candidates.append(datetime.fromtimestamp(raw / 1000))
        except (TypeError, ValueError, OSError):
            continue
    if not candidates:
        return False
    for ts in candidates:
        if start_date and ts < start_date:
            continue
        if end_date and ts > end_date:
            continue
        return True
    return False


def search_space(
    keyword,
    emp_no,
    token,
    title_model=False,
    create_by=None,
    space_id=None,
    page_num: int = 1,
    page_size: int = 15,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
):
    """
    搜索 OA 空间文档

    Args:
        keyword: 搜索关键词
        emp_no: 用户工号
        token: 认证令牌
        title_model: 是否标题搜索，True 表示标题搜索，False 表示全文搜索，默认 False
        create_by: 创建人，可以是单个工号字符串或工号列表
        space_id: 空间ID，可以是单个空间ID字符串或空间ID列表

    Returns:
        dict: 搜索结果
    """
    if not emp_no or not token:
        return {"status": "error", "message": "缺少必需参数：emp_no 和 token"}

    url = build_icenter_api_url("/zte-icenter-search-bff/zte-icenter-search-api/search")

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        "X-Auth-Value": token,  # 动态获取的 Token
        "X-Device": "web",
        "X-Emp-No": emp_no,  # 动态获取的工号
        "X-Lang-Id": "zh_CN",
    }

    # 构建 searchFilters
    search_filters = {}

    # 添加 match_scope（标题搜索模式）
    if title_model is True:
        search_filters["match_scope"] = {"value": "title", "type": "match_scope"}

    # 添加 create_by（创建人）
    if create_by:
        # 确保 create_by 是列表格式
        create_by_list = create_by if isinstance(create_by, list) else [create_by]
        search_filters["create_by"] = {"type": "user", "value": create_by_list}

    # 添加 space（空间ID）
    if space_id:
        # 确保 space_id 是列表格式
        space_id_list = space_id if isinstance(space_id, list) else [space_id]
        search_filters["space"] = {"value": space_id_list, "type": "space"}

    data = {
        "keyword": keyword,
        "app": "page_v2",
        "searchFilters": search_filters,
        "page": page_num,
        "size": page_size,
        "correctKeyword": True,
        "keywordCorrected": False,
        "domain": "universal",
        "isDeepThinking": True,
        "searchSort": "DEFAULT",
    }

    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        response.raise_for_status()

        result = response.json()

        # 检查返回码是否为成功
        code_info = result.get("code", {})
        if (
            code_info.get("code") == "0000"
            and "bo" in result
            and "rows" in result["bo"]
        ):
            bo = result["bo"]
            rows = bo.get("rows", [])
            total = bo.get("total", 0)
            filtered_rows = [
                item for item in rows if _in_date_range(item, start_date, end_date)
            ]

            # 提取需要的信息
            results = []
            for item in filtered_rows:
                # 处理标题，清理 HTML 标签
                title = item.get("title", "")
                if title:
                    title = title.replace('<em class="search-highlight">', "").replace(
                        "</em>", ""
                    )

                # 处理内容，优先使用 content，如果为 null 则使用 summary
                content = item.get("content") or item.get("summary") or ""
                if content:
                    content = (
                        content.replace('<em class="search-highlight">', "")
                        .replace("</em>", "")
                        .replace("\n", " ")
                        .strip()
                    )
                    # 截取前200字符
                    content = content[:200] + "..." if len(content) > 200 else content

                # 处理时间戳（毫秒转日期）
                create_time = item.get("createTime")
                create_time_str = ""
                if create_time:
                    try:
                        create_time_str = datetime.fromtimestamp(
                            create_time / 1000
                        ).strftime("%Y-%m-%d %H:%M:%S")
                    except (ValueError, OSError):
                        create_time_str = str(create_time)

                last_updated_time = item.get("lastUpdatedTime")
                last_updated_time_str = ""
                if last_updated_time:
                    try:
                        last_updated_time_str = datetime.fromtimestamp(
                            last_updated_time / 1000
                        ).strftime("%Y-%m-%d %H:%M:%S")
                    except (ValueError, OSError):
                        last_updated_time_str = str(last_updated_time)

                # 从 bottomInfos 中提取空间名称
                space_name = ""
                bottom_infos = item.get("bottomInfos", [])
                for info in bottom_infos:
                    value = str(info.get("value", ""))
                    if "空间：" in value:
                        space_name = value.replace("空间：", "").strip()
                        break

                # 处理创建人（格式可能是 "中文名工号"）
                create_by = item.get("createBy", "")

                result_item = {
                    "title": title,
                    "content": content,
                    "spaceId": item.get("extraFields", {}).get("spaceId", ""),
                    "spaceName": space_name,
                    "sourceUrl": item.get("sourceUrl", ""),
                    "createTime": create_time_str,
                    "createBy": create_by,
                    "lastUpdatedTime": last_updated_time_str,
                    "lastUpdatedBy": item.get("lastUpdatedBy", ""),
                    "type": item.get("type", ""),
                    "id": item.get("id", ""),
                }
                results.append(result_item)

            return {
                "status": "success",
                "keyword": keyword,
                "total": total,
                "current": bo.get("current", 1),
                "dateFilterApplied": bool(start_date or end_date),
                "startDate": start_date.strftime("%Y-%m-%d") if start_date else "",
                "endDate": end_date.strftime("%Y-%m-%d") if end_date else "",
                "returned": len(results),
                "results": results,
            }
        else:
            error_msg = code_info.get("msg", "未知错误")
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


def format_result_as_markdown(result):
    """
    将搜索结果格式化为Markdown格式

    Args:
        result: 结果字典

    Returns:
        str: Markdown格式的文本
    """
    if result.get("status") == "success":
        keyword = result.get("keyword", "")
        total = result.get("total", 0)
        current = result.get("current", 1)
        returned = result.get("returned", 0)
        results = result.get("results", [])
        date_filter_applied = result.get("dateFilterApplied", False)
        start_date = result.get("startDate", "")
        end_date = result.get("endDate", "")

        output = f"# 搜索结果\n\n"
        output += f"**关键词**: `{keyword}`\n\n"
        output += f"- **总计**: {total} 条\n"
        output += f"- **当前页**: 第 {current} 页\n"
        output += f"- **返回**: {returned} 条结果\n\n"
        if date_filter_applied:
            output += f"- **时间范围过滤**: {start_date or '不限'} ~ {end_date or '不限'}\n\n"

        if results:
            output += "---\n\n"
            for idx, item in enumerate(results, 1):
                title = item.get("title", "无标题")
                content = item.get("content", "")
                source_url = item.get("sourceUrl", "")
                space_name = item.get("spaceName", "")
                space_id = item.get("spaceId", "")
                create_time = item.get("createTime", "")
                create_by = item.get("createBy", "")
                last_updated_time = item.get("lastUpdatedTime", "")
                last_updated_by = item.get("lastUpdatedBy", "")

                output += f"## {idx}. {title}\n\n"

                if source_url:
                    output += f"🔗 [查看页面]({source_url})\n\n"

                if content:
                    output += f"**摘要**: {content}\n\n"

                # 元信息
                meta_info = []
                if space_name:
                    meta_info.append(f"**空间**: {space_name}")
                if space_id:
                    meta_info.append(f"**空间ID**: `{space_id}`")
                if create_time:
                    meta_info.append(f"**创建时间**: {create_time}")
                if create_by:
                    meta_info.append(f"**创建人**: {create_by}")
                if last_updated_time:
                    meta_info.append(f"**更新时间**: {last_updated_time}")
                if last_updated_by:
                    meta_info.append(f"**更新人**: {last_updated_by}")

                if meta_info:
                    output += " | ".join(meta_info) + "\n\n"

                output += "---\n\n"
        else:
            output += "\n未找到相关结果。\n"

        return output
    else:
        error_msg = result.get("message", "未知错误")
        output = f"# 搜索失败\n\n"
        output += f"❌ {error_msg}\n"
        if result.get("code"):
            output += f"\n**错误代码**: `{result.get('code')}`\n"
        return output


def get_user_and_token() -> tuple:
    """返回 (user_id, token)，优先从环境变量获取（非 strict）。"""
    return _get_user_and_token(strict=False)


def parse_optional_bool(value: Optional[str]) -> bool:
    """
    解析可选布尔参数，兼容：
    - 仅传开关：--title-model            -> True
    - 显式值： --title-model true/false -> 对应布尔值
    """
    if value is None:
        return True
    normalized = str(value).strip().lower()
    if normalized in ("true", "1", "yes", "on"):
        return True
    if normalized in ("false", "0", "no", "off"):
        return False
    raise argparse.ArgumentTypeError(
        "布尔参数仅支持 true/false/1/0/yes/no/on/off"
    )


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
        description="搜索 ZTE iCenter 空间文档",
        epilog="提示: 工号和 Token 从环境变量 coclaw_empno 和 coclaw_token 获取",
    )
    parser.add_argument("keyword", help="搜索关键词")
    parser.add_argument(
        "--title-model",
        dest="title_model",
        nargs="?",
        const=True,
        type=parse_optional_bool,
        default=False,
        help="是否标题搜索。可写 --title-model（等价 true）或 --title-model false",
    )
    parser.add_argument("--create-by", dest="create_by", help="创建人工号，多个工号用逗号分隔（可选）")
    parser.add_argument("--space-id", dest="space_id", help="空间ID，多个空间ID用逗号分隔（可选）")
    parser.add_argument(
        "--page-size",
        dest="page_size",
        type=int,
        default=15,
        help="每页数量，默认 15（可选）",
    )
    parser.add_argument(
        "--page-num",
        dest="page_num",
        type=int,
        default=1,
        help="页码，默认 1（可选）",
    )
    parser.add_argument(
        "--start-date",
        dest="start_date",
        type=lambda x: parse_date(x, end_of_day=False),
        help="起始日期，格式 YYYY-MM-DD（可选）",
    )
    parser.add_argument(
        "--end-date",
        dest="end_date",
        type=lambda x: parse_date(x, end_of_day=True),
        help="结束日期，格式 YYYY-MM-DD（可选）",
    )

    args = parser.parse_args()

    # 从环境变量获取工号和token
    emp_no, token = get_user_and_token()

    if not emp_no or not token:
        error_result = {
            "status": "error",
            "message": "缺少必需参数：emp_no 和 token。请设置环境变量 coclaw_empno 和 coclaw_token",
        }
        print(format_result_as_markdown(error_result))
        exit(1)

    # 处理 create_by 和 space_id，支持逗号分隔的多个值
    create_by_list = None
    if args.create_by:
        create_by_list = [
            item.strip() for item in args.create_by.split(",") if item.strip()
        ]

    space_id_list = None
    if args.space_id:
        space_id_list = [
            item.strip() for item in args.space_id.split(",") if item.strip()
        ]

    if args.page_size <= 0 or args.page_num <= 0:
        error_result = {
            "status": "error",
            "message": "page-size 和 page-num 必须为正整数",
        }
        print(format_result_as_markdown(error_result))
        exit(1)

    if args.start_date and args.end_date and args.start_date > args.end_date:
        error_result = {
            "status": "error",
            "message": "start-date 不能晚于 end-date",
        }
        print(format_result_as_markdown(error_result))
        exit(1)

    result = search_space(
        args.keyword,
        emp_no,
        token,
        title_model=args.title_model,
        create_by=create_by_list,
        space_id=space_id_list,
        page_num=args.page_num,
        page_size=args.page_size,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    print(format_result_as_markdown(result))
