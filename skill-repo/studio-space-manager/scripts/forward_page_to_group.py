#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=================================================================
studio-space-manager - 页面转发到群组模块 (v1.0.1)
=================================================================
iCenter 空间页面转发到群组技能

功能：
1. 获取当前用户所在群组列表
2. 根据群组名称匹配群组（优先使用 group_name，为空时使用 default_groupname）
3. 将空间页面转发到指定群组

跨平台支持：
- ✅ Linux (Ubuntu/CentOS/Debian)
- ✅ Windows 10/11
- ✅ macOS (通过 Python)

使用方式：
  # Linux / macOS
  python3 forward_page_to_group.py --url "<页面链接>" --group-name "群组名称"
  python3 forward_page_to_group.py --url "<页面链接>" --list-groups  # 列出所有群组

  # Windows
  python forward_page_to_group.py --url "<页面链接>" --group-name "群组名称"

依赖：
- Python 3.6+
- requests

作者：Co-Claw Team
创建：2026-03-11
更新：2026-03-11 (v1.0.1 添加跨平台支持)
"""

import argparse
import json
import os
import platform
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import requests

from system_param import build_icenter_api_url, get_user_and_token
from _platform_utils import configure_stdio_utf8

# 禁用 SSL 证书验证警告
try:
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

# API 配置
# 获取群组列表接口
GET_GROUP_LIST_URL = build_icenter_api_url(
    "/zte-km-icenter-bff/zte-icenter-msg/services/public-group/users/{user_uri}/getGroupListEx"
)

# 转发页面到群组接口
FORWARD_TO_GROUP_URL_TEMPLATE = build_icenter_api_url(
    "/zte-rd-icenter-contents/content/{content_id}/msgForward/"
)

# 当前用户工号
_env_emp_no, _env_token = get_user_and_token(strict=False)
EMP_NO = (_env_emp_no or "").strip()
# 用户 URI（用于获取群组列表）
USER_URI = f"sip:{EMP_NO}@zte.com.cn" if EMP_NO else ""


# =============================================================================
# 跨平台支持 - 平台检测与配置 (v1.0.1 新增)
# =============================================================================


def detect_platform() -> Tuple[str, str, bool]:
    """
    检测当前操作系统平台

    Returns:
        Tuple[系统名称, 版本号, 是否Windows]
        系统名称: 'Linux', 'Windows', 'Darwin' (macOS)
    """
    system = platform.system()
    release = platform.release()
    is_windows = system == "Windows"
    return system, release, is_windows


def get_user_agent() -> str:
    """
    根据操作系统生成对应的 User-Agent 字符串

    Returns:
        str: 平台对应的 User-Agent
    """
    system, _, _ = detect_platform()

    if system == "Windows":
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        )
    elif system == "Darwin":  # macOS
        return (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        )
    else:  # Linux and others
        return (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        )


def get_sec_ch_ua_platform() -> str:
    """
    获取 Chrome sec-ch-ua-platform 头的值

    Returns:
        str: 平台标识符
    """
    system, _, _ = detect_platform()

    platform_map = {
        "Windows": '"Windows"',
        "Darwin": '"macOS"',
        "Linux": '"Linux"',
    }
    return platform_map.get(system, '"Linux"')


SYSTEM, SYSTEM_RELEASE, IS_WINDOWS = detect_platform()
USER_AGENT = get_user_agent()
SEC_CH_UA_PLATFORM = get_sec_ch_ua_platform()


def get_log_dir() -> str:
    """
    获取日志目录，支持跨平台运行

    Returns:
        str: 日志目录绝对路径
    """
    if IS_WINDOWS or os.name != "posix":
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
        if not IS_WINDOWS and os.name == "posix":
            os.chmod(log_dir, 0o700)
    except Exception:
        temp_base = tempfile.gettempdir()
        log_dir = os.path.join(temp_base, "studio-space-manager", "log")
        os.makedirs(log_dir, exist_ok=True)

    return log_dir


def get_data_dir() -> str:
    """
    获取数据目录，支持跨平台运行

    Returns:
        str: 数据目录绝对路径
    """
    if IS_WINDOWS or os.name != "posix":
        temp_base = tempfile.gettempdir()
        data_dir = os.path.join(temp_base, "studio-space-manager", "data")
    else:
        data_dir = os.path.join(
            os.path.expanduser("~"),
            ".openclaw",
            "workspace",
            "skills",
            "studio-space-manager",
            "data",
        )

    try:
        os.makedirs(data_dir, exist_ok=True)
        if not IS_WINDOWS and os.name == "posix":
            os.chmod(data_dir, 0o700)
    except Exception:
        temp_base = tempfile.gettempdir()
        data_dir = os.path.join(temp_base, "studio-space-manager", "data")
        os.makedirs(data_dir, exist_ok=True)

    return data_dir


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
    # 取 # 后的部分（hash 路由）或整段 URL 做匹配
    part = url.split("#", 1)[-1] if "#" in url else url
    # 匹配 /shared/ 或 /space/ 后的 32 位十六进制 id，以及 /wiki/page/ 后的 32 位十六进制 id
    pattern = r"(?:shared|space)/([a-f0-9]{32})/wiki/page/([a-f0-9]{32})/view"
    match = re.search(pattern, part, re.IGNORECASE)
    if match:
        return match.group(1), match.group(2)
    return None


def get_auth_headers(lang: str = "zh_CN") -> dict:
    """
    获取认证请求头（跨平台支持，自动适配不同操作系统）

    Args:
        lang: 语言设置，默认 zh_CN

    Returns:
        dict: 请求头字典
    """
    _, token = get_user_and_token(strict=False)
    token = (token or "").strip()

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Connection": "keep-alive",
        "Content-Type": "application/json",
        "Origin": "https://i.zte.com.cn",
        "Referer": "https://i.zte.com.cn/",
        "User-Agent": USER_AGENT,  # 使用动态生成的 User-Agent
        "X-Auth-Value": token,
        "X-Emp-No": EMP_NO or "",
        "X-Lang-Id": lang,
        "Terminal-Type": "Web@Desktop_98_ID1004",
    }

    # 添加 Chrome 安全相关头（跨平台适配）
    headers[
        "sec-ch-ua"
    ] = '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"'
    headers["sec-ch-ua-mobile"] = "?0"
    headers["sec-ch-ua-platform"] = SEC_CH_UA_PLATFORM  # 使用动态平台标识
    headers["Sec-Fetch-Dest"] = "empty"
    headers["Sec-Fetch-Mode"] = "cors"
    headers["Sec-Fetch-Site"] = "same-site"

    return headers


def get_group_list(user_uri: str, lang: str = "zh_CN") -> List[Dict]:
    """
    获取当前用户所在群组列表

    Args:
        user_uri: 用户 URI，格式如 sip:0668000519@zte.com.cn
        lang: 语言设置

    Returns:
        List[Dict]: 群组列表，每个群组包含 guri, group_name, default_groupname 等字段
    """
    if not user_uri:
        raise ValueError("未找到用户 URI，请确保环境变量 coclaw_empno 已设置")

    url = GET_GROUP_LIST_URL.format(user_uri=user_uri)
    headers = get_auth_headers(lang)

    try:
        response = requests.post(
            url, headers=headers, json={}, timeout=30, verify=False
        )
        response.raise_for_status()
        result = response.json()

        code_info = result.get("code", {})
        if code_info.get("code") != "200":
            raise Exception(f"获取群组列表失败: {code_info.get('msg', '未知错误')}")

        return result.get("bo", [])
    except requests.exceptions.RequestException as e:
        raise Exception(f"请求获取群组列表失败: {str(e)}")
    except json.JSONDecodeError as e:
        raise Exception(f"JSON 解析错误: {str(e)}")


def match_group_by_name(
    groups: List[Dict], group_name: str
) -> Tuple[Optional[Dict], List[Dict]]:
    """
    根据群组名称匹配群组
    优先使用 group_name 字段匹配，为空时使用 default_groupname 字段匹配

    Args:
        groups: 群组列表
        group_name: 用户输入的群组名称（支持模糊匹配）

    Returns:
        Tuple[匹配的群组, 候选群组列表]
        - 如果只匹配到一个群组，返回 (matched_group, [])
        - 如果匹配到多个群组，返回 (None, candidate_groups)
        - 如果没有匹配到群组，返回 (None, [])
    """
    if not groups or not group_name:
        return None, []

    group_name = group_name.strip().lower()
    exact_matches = []
    fuzzy_matches = []

    for group in groups:
        # 获取群组名称：优先使用 group_name，为空时使用 default_groupname
        display_name = group.get("group_name") or group.get("default_groupname") or ""
        display_name_lower = display_name.lower().strip()

        if not display_name:
            continue

        # 精确匹配（包含关系）
        if group_name in display_name_lower:
            fuzzy_matches.append(group)
        # 完全相等匹配
        elif display_name_lower == group_name:
            exact_matches.append(group)

    # 优先返回精确匹配
    if len(exact_matches) == 1:
        return exact_matches[0], []
    elif len(exact_matches) > 1:
        return None, exact_matches

    # 没有精确匹配时，返回模糊匹配结果
    if len(fuzzy_matches) == 1:
        return fuzzy_matches[0], []
    elif len(fuzzy_matches) > 1:
        return None, fuzzy_matches

    return None, []


def format_group_table(groups: List[Dict]) -> str:
    """
    格式化群组列表为表格形式

    Args:
        groups: 群组列表

    Returns:
        str: 格式化的表格字符串
    """
    if not groups:
        return "暂无群组数据"

    # 准备表头和数据
    headers = ["编号", "群组名称", "群组ID (guri)", "成员数"]
    rows = []

    for i, group in enumerate(groups, 1):
        # 获取显示名称：优先使用 group_name，为空时使用 default_groupname
        display_name = (
            group.get("group_name") or group.get("default_groupname") or "未命名群组"
        )
        guri = group.get("guri", "")
        member_count = group.get("member_count", "")

        rows.append([str(i), display_name, guri, member_count])

    # 计算每列最大宽度
    col_widths = []
    for col_idx in range(len(headers)):
        max_width = len(headers[col_idx])
        for row in rows:
            cell_length = len(str(row[col_idx]))
            if cell_length > max_width:
                max_width = cell_length
        col_widths.append(max_width + 2)  # 两侧留空格

    # 构建表格
    def build_line(
        parts: List[str], left_char: str, mid_char: str, right_char: str
    ) -> str:
        line = left_char
        for i, part in enumerate(parts):
            content = str(part).ljust(col_widths[i] - 1)
            line += f" {content}{mid_char}"
        return line

    # 表头
    header_line = build_line(headers, "│", "│", "│")

    # 分隔线
    def build_separator(left: str, mid: str, right: str) -> str:
        sep = left
        for width in col_widths:
            sep += "─" * width + mid
        return sep[:-1] + right

    separator_start = build_separator("┌", "┬", "┐")
    separator_mid = build_separator("├", "┼", "┤")
    separator_end = build_separator("└", "┴", "┘")

    # 数据行
    data_lines = []
    for row in rows:
        data_lines.append(build_line(row, "│", "│", "│"))

    # 组装完整表格
    lines = [separator_start, header_line, separator_mid]
    lines.extend(data_lines)
    lines.append(separator_end)

    return "\n".join(lines)


def forward_page_to_group(
    space_id: str, content_id: str, group_id: str, group_name: str, lang: str = "zh_CN"
) -> Tuple[bool, str]:
    """
    将页面转发到群组

    Args:
        space_id: 空间ID
        content_id: 页面ID
        group_id: 群组ID（guri）
        group_name: 群组名称（用于显示）
        lang: 语言设置

    Returns:
        Tuple[是否成功, 失败原因或成功消息]
    """
    _, token = get_user_and_token(strict=False)
    token = (token or "").strip()

    url = FORWARD_TO_GROUP_URL_TEMPLATE.format(content_id=content_id)
    headers = {
        "Accept": "application/json, text/plain",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Connection": "keep-alive",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://i.zte.com.cn",
        "Referer": "https://i.zte.com.cn/",
        "User-Agent": USER_AGENT,  # 使用动态生成的 User-Agent (跨平台支持)
        "X-Auth-Value": token,
        "X-Cookie-From-Cros": "Y",
        "X-Emp-No": EMP_NO or "",
        "X-Lang-Id": lang,
        "X-Tenant-Id": "ZTE",
        "Pinpoint-Flags": "0",
        "Pinpoint-pAppName": "ICENTER_zte-km-icenter-ui",
        "Pinpoint-pAppType": "UI",
        "apm_biz_key": json.dumps(
            {
                "serviceName": "ICENTER_zte-km-icenter-ui",
                "spaceId": space_id,
                "contentId": content_id,
                "X-Emp-No": EMP_NO,
            }
        ),
    }

    # 添加 Chrome 安全相关头（跨平台适配）
    headers[
        "sec-ch-ua"
    ] = '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"'
    headers["sec-ch-ua-mobile"] = "?0"
    headers["sec-ch-ua-platform"] = SEC_CH_UA_PLATFORM  # 使用动态平台标识
    headers["Sec-Fetch-Dest"] = "empty"
    headers["Sec-Fetch-Mode"] = "cors"
    headers["Sec-Fetch-Site"] = "same-site"

    # 生成页面链接
    link_pc = f"https://i.zte.com.cn/index/ispace/#/shared/{space_id}/wiki/page/{content_id}/view"
    link_mobile = build_icenter_api_url(
        f"/zte-rd-ispace-mobile/#/wiki/page/{content_id}/view?fromType=MOA&spaceId={space_id}"
    )

    # 请求参数
    params = {
        "t": int(datetime.now().timestamp() * 1000),
        "spaceId": space_id,
        "environmentType": "browser",
        "system": "space-1920484507",
    }

    body = {
        "employees": [],
        "groups": [{"groupId": group_id, "groupName": group_name}],
        "forwardOverfolow": False,
        "maxNum": 1000,
        "link": {"linkPC": link_pc, "linkMobile": link_mobile},
        "groupKeyList": [],
        "spaceId": space_id,
    }

    try:
        response = requests.post(
            url, headers=headers, params=params, json=body, timeout=30, verify=False
        )
        response.raise_for_status()
        result = response.json()

        # 检查返回状态
        code_info = result.get("code", {})
        if code_info.get("code") == "0000":
            return True, f"已成功将页面转发到群组「{group_name}」"

        # 解析错误信息
        error_msg = code_info.get("msg") or code_info.get("message") or "未知错误"
        return False, f"转发失败: {error_msg}"

    except requests.exceptions.RequestException as e:
        return False, f"请求失败: {str(e)}"
    except json.JSONDecodeError as e:
        return False, f"响应解析错误: {str(e)}"
    except Exception as e:
        return False, f"转发失败: {str(e)}"


def main():
    configure_stdio_utf8()
    global EMP_NO, USER_URI

    parser = argparse.ArgumentParser(
        description="将空间页面转发到 iCenter 群组",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 列出所有群组
  python3 forward_page_to_group.py --list-groups

  # 转发到指定群组
  python3 forward_page_to_group.py --url "https://i.zte.com.cn/#/shared/xxx/wiki/page/yyy/view" --group-name "群组名称"

  # 模糊匹配群组
  python3 forward_page_to_group.py --url "https://i.zte.com.cn/#/shared/xxx/wiki/page/yyy/view" --group-name "项目"
        """,
    )

    parser.add_argument(
        "--url",
        dest="url",
        help="空间页面完整链接",
    )

    parser.add_argument(
        "--group-name",
        dest="group_name",
        help="群组名称（支持模糊匹配）",
    )

    parser.add_argument(
        "--list-groups",
        dest="list_groups",
        action="store_true",
        help="列出当前用户所有群组",
    )

    parser.add_argument(
        "--emp-no",
        dest="emp_no",
        default=EMP_NO,
        help="当前用户工号（默认从环境变量 coclaw_empno 读取）",
    )

    parser.add_argument(
        "--lang",
        dest="lang",
        default="zh_CN",
        help="语言，默认 zh_CN",
    )

    args = parser.parse_args()

    # 检查必需参数
    if not args.list_groups and (not args.url or not args.group_name):
        parser.error("缺少必需参数：请提供 --url 和 --group-name，或使用 --list-groups 列出群组")

    _, token = get_user_and_token(strict=False)
    token = (token or "").strip()
    if not args.emp_no or not token:
        result = {
            "success": False,
            "message": "缺少用户身份：请提供 --emp-no 或设置环境变量 coclaw_empno；token 必须通过环境变量 coclaw_token 设置",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(1)

    EMP_NO = args.emp_no
    USER_URI = f"sip:{EMP_NO}@zte.com.cn"

    # 列出所有群组
    if args.list_groups:
        try:
            groups = get_group_list(USER_URI, args.lang)

            result = {
                "success": True,
                "message": f"找到 {len(groups)} 个群组",
                "groups": [
                    {
                        "guri": g.get("guri", ""),
                        "group_name": g.get("group_name", ""),
                        "default_groupname": g.get("default_groupname", ""),
                        "member_count": g.get("member_count", ""),
                        "owner_uri": g.get("owner_uri", ""),
                    }
                    for g in groups
                ],
            }

            print(json.dumps(result, ensure_ascii=False, indent=2))

            # 同时输出格式化表格
            print(f"\n群组列表：")
            print(format_group_table(groups))

            sys.exit(0)

        except Exception as e:
            result = {"success": False, "message": f"获取群组列表失败: {str(e)}"}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            sys.exit(1)

    # 解析页面链接
    parsed = parse_page_link(args.url)
    if not parsed:
        result = {
            "success": False,
            "message": "无法从链接中解析空间ID和页面ID，请确认链接格式为："
            "https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view 或 "
            "https://i.zte.com.cn/index/ispace/#/space/{spaceId}/wiki/page/{pageId}/view",
            "url": args.url,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(1)

    space_id, content_id = parsed

    # 获取群组列表
    try:
        groups = get_group_list(USER_URI, args.lang)
    except Exception as e:
        result = {"success": False, "message": f"获取群组列表失败: {str(e)}"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(1)

    # 匹配群组
    matched_group, candidates = match_group_by_name(groups, args.group_name)

    # 没有匹配到群组
    if matched_group is None:
        if not candidates:
            result = {
                "success": False,
                "message": f"未找到名称包含「{args.group_name}」的群组",
                "search_name": args.group_name,
                "suggestion": "请使用 --list-groups 查看所有群组后重新尝试",
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            sys.exit(1)

        # 匹配到多个群组，返回候选列表让用户选择
        result = {
            "success": False,
            "need_choice": True,
            "message": f"找到 {len(candidates)} 个名称包含「{args.group_name}」的群组，请使用 --group-name 指定完整群组名称：",
            "search_name": args.group_name,
            "candidates": [
                {
                    "guri": g.get("guri", ""),
                    "group_name": g.get("group_name", ""),
                    "default_groupname": g.get("default_groupname", ""),
                }
                for g in candidates
            ],
            "page_link": args.url,
            "space_id": space_id,
            "content_id": content_id,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))

        # 同时输出候选列表
        print(f"\n候选群组列表：")
        print(format_group_table(candidates))
        print(f'\n请使用 --group-name "完整群组名称" 重新调用')

        sys.exit(1)

    # 获取显示名称
    display_name = (
        matched_group.get("group_name")
        or matched_group.get("default_groupname")
        or "未命名群组"
    )
    group_id = matched_group.get("guri", "")

    if not group_id:
        result = {"success": False, "message": "群组ID为空，无法转发"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(1)

    # 转发页面到群组
    success, message = forward_page_to_group(
        space_id, content_id, group_id, display_name, args.lang
    )

    result = {
        "success": success,
        "message": message,
        "page_link": f"https://i.zte.com.cn/#/shared/{space_id}/wiki/page/{content_id}/view",
        "space_id": space_id,
        "content_id": content_id,
        "group": {
            "groupId": group_id,
            "groupName": display_name,
        },
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
