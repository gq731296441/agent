#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=================================================================
studio-space-manager - 空间页面评论模块 (v1.1.0)
=================================================================
iCenter 空间页面评论发布与查看

功能：
- 发布评论（根评论）
- 获取评论列表
- 回复评论（支持多层级：根评论→子评论→孙评论）

跨平台支持：
- ✅ Linux (Ubuntu/CentOS/Debian)
- ✅ Windows 10/11
- ✅ macOS (通过 Python)

认证：
- coclaw_empno：用户工号
- coclaw_token：认证令牌

作者：Co-Claw Team
创建：2026-03-07
更新：2026-03-09
"""

import argparse
import json
import os
import re
import sys
import time

import requests

from system_param import (
    ICENTER_API_BASE,
    MISSING_USER_AND_TOKEN_MESSAGE,
    build_system_param,
    get_user_and_token,
)
from _platform_utils import IS_WINDOWS, SYSTEM, configure_stdio_utf8

# 配置
ICENTER_BASE_URL = "https://i.zte.com.cn"
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

# 环境变量键名
ENV_EMP_NO = "coclaw_empno"
ENV_TOKEN = "coclaw_token"


def get_env(key, required=False, default=None):
    """获取环境变量"""
    value = os.environ.get(key, default)
    if required and not value:
        print(
            json.dumps(
                {
                    "success": False,
                    "message": f"缺少必要的环境变量: {key}",
                    "error": f"Environment variable {key} not set",
                },
                ensure_ascii=False,
            )
        )
        sys.exit(1)
    return value


def parse_url(url):
    """
    从页面URL解析 space_id 和 content_id
    支持格式：
    1. https://i.zte.com.cn/#/shared/{space_id}/wiki/page/{content_id}/view
    2. https://i.zte.com.cn/index/ispace/#/space/{space_id}/wiki/page/{content_id}/view
    """
    # 匹配格式1: shared
    pattern1 = r"/shared/([a-f0-9]+)/wiki/page/([a-f0-9]+)/view"
    match = re.search(pattern1, url)
    if match:
        return match.group(1), match.group(2)

    # 匹配格式2: space
    pattern2 = r"/space/([a-f0-9]+)/wiki/page/([a-f0-9]+)/view"
    match = re.search(pattern2, url)
    if match:
        return match.group(1), match.group(2)

    return None, None


def get_platform_string() -> str:
    """
    获取当前平台的字符串表示，用于 User-Agent。
    """
    if IS_WINDOWS:
        return "Windows"
    elif SYSTEM == "Darwin":
        return "macOS"
    elif SYSTEM == "Linux":
        return "Linux"
    else:
        return SYSTEM


def get_common_headers(emp_no, token):
    """构建基础请求头（用于 icenterapi 接口），支持跨平台"""
    auth_value = token

    # 根据平台设置 User-Agent
    platform_str = get_platform_string()
    if IS_WINDOWS:
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
    elif SYSTEM == "Darwin":
        user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
    else:
        user_agent = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"

    return {
        "Accept": "application/json, text/plain",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-US;q=0.7",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://i.zte.com.cn",
        "Pragma": "no-cache",
        "Referer": "https://i.zte.com.cn/",
        "X-Auth-Value": auth_value,
        "X-Cookie-From-Cros": "Y",
        "X-Emp-No": emp_no,
        "X-Lang-Id": "zh_CN",
        "X-Tenant-Id": "ZTE",
        "sec-ch-ua": '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
        "sec-ch-ua-mobile": "?0",
        f"sec-ch-ua-platform": f'"{platform_str}"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "User-Agent": user_agent,
    }


def format_comment_html(content):
    """将纯文本转换为HTML格式"""
    if content.startswith("<div"):
        return content
    escaped = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f'<div style="font-size:14px;color:#13161a"><p><span style="" data-from="manual-input">{escaped}</span></p></div>'


def list_comments(space_id, content_id, limit=50, offset=0, emp_no=None, token=None):
    """
    获取评论列表
    使用 icenterapi.zte.com.cn 接口
    """
    url = f"{ICENTER_API_BASE}/zte-rd-icenter-extend/comments/businessTypes/content/businesses/{content_id}/"

    params = {
        "t": int(time.time() * 1000),
        "spaceId": space_id,
        "environmentType": "mobile-browser",
        "system": build_system_param("space"),
        "commentId": "",
        "limit": limit,
        "offset": offset,
    }

    headers = get_common_headers(emp_no, token)

    try:
        response = requests.get(url, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        result = response.json()
        bo = result.get("bo") if isinstance(result, dict) else {}
        if not isinstance(bo, dict):
            bo = {}
        meta = bo.get("meta")
        if not isinstance(meta, dict):
            meta = {}
        rows = bo.get("rows")
        if not isinstance(rows, list):
            rows = []

        return {
            "success": True,
            "message": "获取评论列表成功",
            "action": "list",
            "total": meta.get("commentTotalSize", 0),
            "comments": rows,
        }
    except requests.exceptions.RequestException as e:
        return {"success": False, "message": f"获取评论列表失败: {str(e)}", "error": str(e)}


def reply_comment(
    space_id, content_id, parent_id, comment, jump_url=None, emp_no=None, token=None
):
    """
    回复某条评论
    使用 icenterapi.zte.com.cn 接口
    """
    url = f"{ICENTER_API_BASE}/zte-rd-icenter-extend/comments/businessTypes/content/businesses/{content_id}/"

    params = {
        "t": int(time.time() * 1000),
        "spaceId": space_id,
        "environmentType": "browser",
        "system": build_system_param("space"),
        "noRedirect": "true",
    }

    headers = get_common_headers(emp_no, token)

    if not jump_url:
        jump_url = (
            f"https://i.zte.com.cn/#/shared/{space_id}/wiki/page/{content_id}/view"
        )

    payload = {
        "employees": [],
        "groups": [],
        "commentDetail": format_comment_html(comment),
        "parentId": parent_id,
        "jumpUrl": jump_url,
        "groupKeyList": [],
    }

    try:
        response = requests.post(
            url, params=params, json=payload, headers=headers, timeout=30
        )
        response.raise_for_status()
        result = response.json()

        if result.get("code", {}).get("code") == "0000":
            return {
                "success": True,
                "message": "评论回复成功",
                "action": "reply",
                "data": result.get("bo", {}),
            }
        else:
            return {
                "success": False,
                "message": f"评论回复失败: {result.get('code', {}).get('msgId', '未知错误')}",
                "error": result,
            }
    except requests.exceptions.RequestException as e:
        return {"success": False, "message": f"评论回复失败: {str(e)}", "error": str(e)}


def publish_comment(space_id, content_id, comment, emp_no=None, token=None):
    """
    发布评论（根评论）
    """
    url = f"{ICENTER_API_BASE}/zte-rd-icenter-extend/comments/businessTypes/content/businesses/{content_id}/"

    params = {
        "t": int(time.time() * 1000),
        "spaceId": space_id,
        "environmentType": "browser",
        "system": build_system_param("space"),
        "noRedirect": "true",
    }

    headers = get_common_headers(emp_no, token)
    jump_url = f"https://i.zte.com.cn/#/shared/{space_id}/wiki/page/{content_id}/view"

    payload = {
        "employees": [],
        "groups": [],
        "commentDetail": format_comment_html(comment),
        "parentId": "",
        "jumpUrl": jump_url,
        "groupKeyList": [],
    }

    try:
        response = requests.post(
            url, params=params, json=payload, headers=headers, timeout=30
        )
        response.raise_for_status()
        result = response.json()

        if result.get("code", {}).get("code") == "0000":
            return {
                "success": True,
                "message": "评论发布成功",
                "action": "publish",
                "data": result.get("bo", {}),
            }
        else:
            return {
                "success": False,
                "message": f"评论发布失败: {result.get('code', {}).get('msgId', '未知错误')}",
                "error": result,
            }
    except requests.exceptions.RequestException as e:
        return {"success": False, "message": f"评论发布失败: {str(e)}", "error": str(e)}


def main():
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(description="iCenter 空间页面评论发布与查看工具")
    parser.add_argument(
        "--action",
        choices=["publish", "list", "reply"],
        required=True,
        help="操作类型: publish(发布评论)、list(获取评论列表)、reply(回复评论)",
    )
    parser.add_argument("--space-id", help="空间ID (若提供 --url 则可不传)")
    parser.add_argument("--content-id", help="页面/内容ID (若提供 --url 则可不传)")
    parser.add_argument("--url", help="页面完整链接，会自动解析 space-id 和 content-id")
    parser.add_argument("--comment", help="评论内容 (publish/reply 时必填)")
    parser.add_argument("--parent-id", help="父评论ID (reply 时必填)")
    parser.add_argument("--limit", type=int, default=50, help="评论列表获取数量，默认50")
    parser.add_argument("--offset", type=int, default=0, help="评论列表偏移量，默认0")
    parser.add_argument("--emp-no", help="当前用户工号，默认从环境变量 coclaw_empno 读取")
    parser.add_argument("--token", help="认证Token，默认从环境变量 coclaw_token 读取")

    args = parser.parse_args()

    # 获取认证信息
    emp_no_env, token_env = get_user_and_token(strict=False)
    emp_no = (args.emp_no or emp_no_env or "").strip()
    token = (args.token or token_env or "").strip()
    if not emp_no or not token:
        print(
            json.dumps(
                {"success": False, "message": MISSING_USER_AND_TOKEN_MESSAGE},
                ensure_ascii=False,
            )
        )
        sys.exit(1)

    # 解析URL（优先级高于单独的 space-id 和 content-id）
    if args.url:
        space_id, content_id = parse_url(args.url)
        if not space_id or not content_id:
            print(
                json.dumps(
                    {
                        "success": False,
                        "message": "无法从URL中解析space_id和content_id",
                        "url": args.url,
                        "error": "URL format not supported",
                    },
                    ensure_ascii=False,
                )
            )
            sys.exit(1)
    else:
        space_id = args.space_id
        content_id = args.content_id

    # 验证必要参数
    if not space_id or not content_id:
        print(
            json.dumps(
                {
                    "success": False,
                    "message": "缺少必要参数：space-id 和 content-id（或通过 --url 解析）",
                },
                ensure_ascii=False,
            )
        )
        sys.exit(1)

    # 执行操作
    if args.action == "list":
        result = list_comments(
            space_id,
            content_id,
            limit=args.limit,
            offset=args.offset,
            emp_no=emp_no,
            token=token,
        )
    elif args.action == "publish":
        if not args.comment:
            print(
                json.dumps(
                    {"success": False, "message": "发布评论需要提供 --comment 参数"},
                    ensure_ascii=False,
                )
            )
            sys.exit(1)
        result = publish_comment(
            space_id, content_id, comment=args.comment, emp_no=emp_no, token=token
        )
    elif args.action == "reply":
        if not args.comment:
            print(
                json.dumps(
                    {"success": False, "message": "回复评论需要提供 --comment 参数"},
                    ensure_ascii=False,
                )
            )
            sys.exit(1)
        if not args.parent_id:
            print(
                json.dumps(
                    {"success": False, "message": "回复评论需要提供 --parent-id 参数"},
                    ensure_ascii=False,
                )
            )
            sys.exit(1)
        result = reply_comment(
            space_id,
            content_id,
            parent_id=args.parent_id,
            comment=args.comment,
            emp_no=emp_no,
            token=token,
        )
    else:
        result = {"success": False, "message": f"不支持的操作: {args.action}"}

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
