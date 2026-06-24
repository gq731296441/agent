#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=================================================================
studio-space-manager - 页面收藏模块 (v2.8.0)
=================================================================
iCenter 空间页面收藏技能

根据用户提供的空间页面链接，解析 space_id 与 page_id，调用 iCenter 收藏接口将页面加入收藏。
工号可从 --emp-no 或环境变量 coclaw_empno 传入；Token 仅从环境变量 coclaw_token 读取，不可通过参数传入。

跨平台支持：
- ✅ Linux (Ubuntu/CentOS/Debian)
- ✅ Windows 10/11
- ✅ macOS (通过 Python)

使用方式：
  # 收藏页面
  python3 favorite_page.py --url "<空间页面链接>"
  python3 favorite_page.py --url "<页面链接>" --emp-no "0668001028"

  # 查询我收藏的页面列表（返回标题、空间、查看链接）
  python3 favorite_page.py --list
  python3 favorite_page.py --list --page 1 --rows 30
  # 按关键字筛选收藏
  python3 favorite_page.py --list --keyword "项目"
  python3 favorite_page.py --list --keyword "项目" --page 2

依赖：
- Python 3.6+
- requests

作者：Co-Claw Team
创建：2026-03-07
更新：2026-03-07
"""

import argparse
import json
import os
import platform
import re
import sys
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

from system_param import build_icenter_api_url, get_user_and_token
from _platform_utils import configure_stdio_utf8

# 401 时的统一提示（Token 过期或权限不足）
MSG_401 = "Token 已过期或权限不足（401）。请刷新 Token 或联系管理员确认收藏权限后重试。"

# 页面详情接口（获取页面标题与摘要）
CONTENT_DETAIL_BASE = build_icenter_api_url("/zte-rdcloud-space-content/content")

# 收藏接口
FAVORITE_ADD_BASE = build_icenter_api_url("/zte-km-icenter-console/favorite/add")
# 查询收藏列表接口
FAVORITE_QUERY_LIST_BASE = build_icenter_api_url(
    "/zte-km-icenter-console/favorite/queryFavoriteList"
)

try:
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass


def parse_page_link(url: str) -> Optional[Tuple[str, str]]:
    """
    从空间页面链接中解析 space_id 和 content_id（页面ID）。

    支持的链接格式：
    - https://i.zte.com.cn/#/shared/{space_id}/wiki/page/{content_id}/view
    - https://i.zte.com.cn/index/ispace/#/space/{space_id}/wiki/page/{content_id}/view

    Returns:
        (space_id, content_id) 或 None
    """
    if not url or not url.strip():
        return None
    url = url.strip()
    part = url.split("#", 1)[-1] if "#" in url else url
    pattern = r"(?:shared|space)/([a-f0-9]{32})/wiki/page/([a-f0-9]{32})/view"
    match = re.search(pattern, part, re.IGNORECASE)
    if match:
        return match.group(1), match.group(2)
    return None


def get_page_title_and_summary(
    space_id: str,
    content_id: str,
    caller_emp_no: str,
    token: str,
    lang: str = "zh_CN",
) -> Tuple[str, str]:
    """
    从页面详情接口获取页面标题与摘要，用于收藏接口的 title、source、summary。
    返回 (title, summary)；若接口失败则返回 ("", "")。
    """
    if not space_id or not content_id or not token:
        return "", ""
    url = f"{CONTENT_DETAIL_BASE}/{content_id}"
    headers = {
        "Accept": "application/json, text/plain",
        "X-Auth-Value": token,
        "X-Emp-No": caller_emp_no,
        "X-Lang-Id": lang,
        "X-Tenant-Id": "ZTE",
        "Content-Type": "application/json",
    }
    params = {"spaceId": space_id}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        if resp.status_code == 401:
            return "", ""
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            return "", ""
        bo = data.get("bo") or data
        title = (bo.get("title") or data.get("title") or "").strip()
        summary = (
            bo.get("summary") or bo.get("content") or data.get("summary") or ""
        ).strip()
        if summary and len(summary) > 500:
            summary = summary[:500] + "…"
        if not summary and title:
            summary = title
        return title, summary
    except Exception:
        pass
    return "", ""


def add_favorite(
    space_id: str,
    page_id: str,
    caller_emp_no: str,
    token: str,
    title: str = "",
    summary: str = "",
    source: str = "",
    lang: str = "zh_CN",
) -> Tuple[bool, str, bool]:
    """
    调用收藏接口将页面加入收藏。

    Args:
        space_id: 空间 ID
        page_id: 页面 ID（即 content_id）
        caller_emp_no: 当前用户工号
        token: 认证 Token
        title: 页面标题（用于收藏展示）
        summary: 摘要（可为空或与 title 相同）
        source: 来源描述（可为标题或固定值）
        lang: 语言

    Returns:
        (是否成功, 失败时的友好提示或成功时的 message, 是否为 401 鉴权类错误)
    """
    if not space_id or not page_id or not caller_emp_no or not token:
        return False, "缺少必要参数（空间ID、页面ID或用户身份）", False

    api_url = FAVORITE_ADD_BASE
    headers = {
        "Accept": "application/json, text/plain",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Cache-Control": "no-cache",
        "Content-Type": "application/json;charset=UTF-8",
        "X-Auth-Value": token,
        "X-Emp-No": caller_emp_no,
        "X-Lang-Id": lang,
    }
    params = {"t": int(datetime.now().timestamp() * 1000), "spaceId": space_id}
    open_param = json.dumps(
        {"spaceId": space_id, "pageId": page_id}, ensure_ascii=False
    )
    title = (title or "未命名页面").strip()
    summary = (summary or title).strip()
    source = (source or title).strip()

    body = {
        "createBy": caller_emp_no,
        "openParam": open_param,
        "source": source,
        "sourceApp": "content",
        "summary": summary,
        "title": title,
    }
    try:
        response = requests.post(
            api_url,
            headers=headers,
            params=params,
            json=body,
            timeout=30,
        )
        if response.status_code == 401:
            return False, MSG_401, True
        response.raise_for_status()
        result = response.json()
        code_info = result.get("code")
        if isinstance(code_info, dict):
            code = str(code_info.get("code", "")).strip()
            msg = str(code_info.get("message") or code_info.get("desc") or "").strip()
        else:
            code = str(result.get("code", "")).strip() if result.get("code") else ""
            msg = str(result.get("message", "") or result.get("desc", "")).strip()
        if code == "0000":
            return True, "收藏成功", False
        if msg:
            return False, msg, False
        return False, f"收藏失败（返回码：{code}），请稍后重试或联系管理员。", False
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 401:
            return False, MSG_401, True
        return False, "网络请求失败，请检查链接与网络后重试。", False
    except requests.exceptions.RequestException:
        return False, "网络请求失败，请检查链接与网络后重试。", False
    except Exception:
        return False, "操作失败，请稍后重试。", False


def query_favorite_list(
    caller_emp_no: str,
    token: str,
    page: int = 1,
    rows: int = 30,
    lang: str = "zh_CN",
    search_key: str = "",
) -> Tuple[bool, str, Optional[List[Dict[str, Any]]], bool, int, int]:
    """
    查询当前用户收藏的页面列表，支持关键字筛选。

    Args:
        caller_emp_no: 当前用户工号
        token: 认证 Token
        page: 页码，从 1 开始
        rows: 每页条数
        lang: 语言
        search_key: 搜索关键字，为空则查询全部收藏

    Returns:
        (是否成功, 提示信息, 列表项 [{"title","source","url"}], 是否为 401, total 总数, current 当前页)
    """
    if not caller_emp_no or not token:
        return False, "缺少必要参数（用户身份或 Token）", None, False, 0, 0

    api_url = FAVORITE_QUERY_LIST_BASE
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "X-Auth-Value": token,
        "X-Emp-No": caller_emp_no,
        "X-Lang-Id": lang,
        "X-Tenant-Id": "31003",
    }
    search_key_str = (search_key or "").strip()
    body = {
        "bo": {"startTime": "", "dataType": "6", "searchKey": search_key_str},
        "order": "",
        "other": {},
        "page": page,
        "rows": rows,
        "sort": "",
    }
    try:
        response = requests.post(
            api_url,
            headers=headers,
            json=body,
            timeout=30,
        )
        if response.status_code == 401:
            return False, MSG_401, None, True, 0, 0
        response.raise_for_status()
        data = response.json()
        code_info = data.get("code") or {}
        code = str(code_info.get("code", "")).strip()
        if code != "0000":
            msg = str(code_info.get("msg") or code_info.get("message") or "").strip()
            return False, msg or "查询失败", None, False, 0, 0

        bo = data.get("bo") or {}
        raw_rows = bo.get("rows") or []
        total = int(bo.get("total", 0))
        current = int(bo.get("current", page))

        items = []
        for row in raw_rows:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip() or "未命名"
            source = str(row.get("source") or "").strip() or ""
            url = str(row.get("url") or "").strip()
            space_id = ""
            page_id = ""
            # 尝试从openParam解析spaceId和pageId
            open_param = str(row.get("openParam") or "").strip()
            if open_param:
                try:
                    import json

                    open_obj = json.loads(open_param)
                    space_id = open_obj.get("spaceId", "")
                    page_id = open_obj.get("pageId", "")
                except Exception:
                    pass
            # 如果API未返回URL，使用解析出的spaceId和pageId构建链接
            if not url and space_id and page_id:
                url = (
                    f"https://i.zte.com.cn/#/shared/{space_id}/wiki/page/{page_id}/view"
                )
            items.append(
                {
                    "title": title,
                    "source": source,
                    "url": url,
                    "space_id": space_id,
                    "page_id": page_id,
                }
            )

        if search_key_str:
            msg = f"关键词「{search_key_str}」共 {total} 条，当前第 {current} 页，本页 {len(items)} 条"
        else:
            msg = f"共 {total} 条，当前第 {current} 页，本页 {len(items)} 条"
        return True, msg, items, False, total, current
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 401:
            return False, MSG_401, None, True, 0, 0
        return False, "网络请求失败，请检查网络后重试。", None, False, 0, 0
    except requests.exceptions.RequestException:
        return False, "网络请求失败，请检查网络后重试。", None, False, 0, 0
    except Exception:
        return False, "查询失败，请稍后重试。", None, False, 0, 0


def main():
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(
        description="根据空间页面链接收藏该页面，或查询我收藏的页面列表；工号、Token 获取方式同 studio-space-forward（工号：--emp-no 或 coclaw_empno，Token 仅 coclaw_token 环境变量）",
    )
    parser.add_argument(
        "--url",
        dest="url",
        default=None,
        help="空间页面完整链接（支持 shared/space 两种格式）；收藏时必填",
    )
    parser.add_argument(
        "--list",
        dest="list_favorites",
        action="store_true",
        help="查询我收藏的页面列表（返回标题、空间、查看链接）",
    )
    parser.add_argument(
        "--page",
        dest="page",
        type=int,
        default=1,
        help="查询收藏列表时的页码，默认 1（仅 --list 时有效）",
    )
    parser.add_argument(
        "--rows",
        dest="rows",
        type=int,
        default=30,
        help="查询收藏列表时每页条数，默认 30（仅 --list 时有效）",
    )
    parser.add_argument(
        "--keyword",
        dest="keyword",
        default="",
        help="搜索关键字，在收藏列表中按关键词筛选（仅 --list 时有效）",
    )
    parser.add_argument(
        "--emp-no",
        dest="emp_no",
        default=get_user_and_token(strict=False)[0],
        help="当前用户工号（鉴权用，默认从环境变量 coclaw_empno 读取）",
    )
    parser.add_argument(
        "--lang",
        dest="lang",
        default="zh_CN",
        help="语言，默认 zh_CN",
    )
    args = parser.parse_args()

    caller_emp_no = (args.emp_no or "").strip()
    _, token = get_user_and_token(strict=False)
    token = (token or "").strip()
    if not caller_emp_no or not token:
        result = {
            "success": False,
            "message": "缺少用户身份：请提供 --emp-no 或设置环境变量 coclaw_empno；token 必须通过环境变量 coclaw_token 设置",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(1)

    if args.list_favorites:
        success, msg, items, auth_error_401, total, current = query_favorite_list(
            caller_emp_no,
            token,
            page=args.page,
            rows=args.rows,
            lang=args.lang,
            search_key=args.keyword or "",
        )
        result = {
            "success": success,
            "message": msg,
            "list": items or [],
            "total": total,
            "current_page": current,
        }
        if (args.keyword or "").strip():
            result["keyword"] = (args.keyword or "").strip()
        has_more = total > current * args.rows
        result["has_more"] = has_more
        if has_more:
            result["next_page"] = current + 1
            hint = f"如需查看更多已收藏的页面，可以说「查看下一页」获取第 {current + 1} 页。"
            if (args.keyword or "").strip():
                hint += " 查看下一页时请使用相同关键词。"
            result["hint"] = hint
        if auth_error_401:
            result["suggest_refresh_token"] = True
            result["auth_error"] = "Token 已过期或权限不足（401），建议刷新 Token 或确认权限后重试。"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if success else 1)

    if not args.url:
        result = {
            "success": False,
            "message": "请提供 --url 收藏页面，或使用 --list 查询我收藏的页面列表",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(1)

    parsed = parse_page_link(args.url)
    if not parsed:
        result = {
            "success": False,
            "message": "无法从链接中解析空间ID和页面ID，请确认链接格式为："
            " https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view 或 "
            "https://i.zte.com.cn/index/ispace/#/space/{spaceId}/wiki/page/{pageId}/view",
            "url": args.url,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(1)

    space_id, page_id = parsed
    title, summary = get_page_title_and_summary(
        space_id, page_id, caller_emp_no, token, args.lang
    )
    success, msg, auth_error_401 = add_favorite(
        space_id,
        page_id,
        caller_emp_no,
        token,
        title=title,
        summary=summary,
        source=title or "空间页面",
        lang=args.lang,
    )
    page_link = f"https://i.zte.com.cn/#/shared/{space_id}/wiki/page/{page_id}/view"
    result = {
        "success": success,
        "message": msg,
        "page_link": page_link,
        "space_id": space_id,
        "page_id": page_id,
        "title": title or "未命名页面",
    }
    if auth_error_401:
        result["suggest_refresh_token"] = True
        result["auth_error"] = "Token 已过期或权限不足（401），建议刷新 Token 或确认收藏权限后重试。"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if success else 1)


# =============================================================================
# 跨平台支持 - 平台检测
# =============================================================================


def detect_platform() -> Tuple[str, str, bool]:
    """
    检测当前操作系统平台。

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
    获取脚本所在目录，支持跨平台运行。

    Returns:
        脚本所在目录的绝对路径
    """
    # 获取脚本自身的目录
    script_path = os.path.abspath(__file__)
    script_dir = os.path.dirname(script_path)
    return script_dir


def get_log_dir() -> str:
    """
    获取日志目录，支持跨平台运行。

    Returns:
        日志目录路径
        - Linux: ~/.openclaw/workspace/skills/studio-space-manager/log
        - Windows / 其他: 系统临时目录下的 studio-space-manager 子目录
    """
    system, _, is_windows = detect_platform()

    if is_windows or os.name != "posix":
        # Windows 或非 POSIX 系统：使用临时目录
        temp_base = tempfile.gettempdir()
        log_dir = os.path.join(temp_base, "studio-space-manager", "log")
    else:
        # Linux / macOS：使用标准配置目录
        log_dir = os.path.join(
            os.path.expanduser("~"),
            ".openclaw",
            "workspace",
            "skills",
            "studio-space-manager",
            "log",
        )

    # 创建目录（如果不存在）
    try:
        os.makedirs(log_dir, exist_ok=True)
        # 设置目录权限（仅 Linux/macOS）
        if not is_windows and os.name == "posix":
            os.chmod(log_dir, 0o700)
    except Exception:
        # 回退到临时目录
        temp_base = tempfile.gettempdir()
        log_dir = os.path.join(temp_base, "studio-space-manager", "log")
        os.makedirs(log_dir, exist_ok=True)

    return log_dir


# 初始化平台信息
SYSTEM, SYSTEM_RELEASE, IS_WINDOWS = detect_platform()


if __name__ == "__main__":
    main()
