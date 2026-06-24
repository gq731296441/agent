#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
调用 iCenter 接口根据名字搜索自己加入的群

使用方式：
    python search_group.py --groupname "xxxxxx"

参数说明：
    --groupname  群名称关键字（必填）

环境变量（必需）：
    coclaw_empno: 调用者工号
    coclaw_token: 认证令牌
"""

import os
import sys
sys.stdout.reconfigure(encoding='utf-8')
import json
import argparse
import urllib3
import requests
import logging
from logging.handlers import RotatingFileHandler
from typing import Dict, Any

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from util import get_msg_base_url

_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
if not os.path.exists(_log_dir):
    os.makedirs(_log_dir)

_log_file = os.path.join(_log_dir, "search_group.log")
_file_handler = RotatingFileHandler(
    _log_file,
    maxBytes=20 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
_file_handler.setLevel(logging.DEBUG)
_formatter = logging.Formatter("%(asctime)s - [%(name)s] - %(levelname)s - %(message)s")
_file_handler.setFormatter(_formatter)

_console_handler = logging.StreamHandler(sys.stderr)
_console_handler.setLevel(logging.DEBUG)
_console_handler.setFormatter(_formatter)

_app_logger = logging.getLogger("search_group")
_app_logger.setLevel(logging.DEBUG)
_app_logger.propagate = False
_app_logger.addHandler(_file_handler)
_app_logger.addHandler(_console_handler)

BASE_URL = get_msg_base_url()
TIMEOUT = 15


def search_group(userid: str, token: str, groupname: str) -> Dict[str, Any]:
    """
    调用 iCenter 接口搜索群聊。

    Args:
        userid:    调用者工号
        token:     认证令牌
        groupname: 群名称关键字

    Returns:
        解析后的响应字典，包含 code 状态码和 bo 业务数据（群列表）
    """
    url = f"{BASE_URL}/zte-icenter-msg-bff/services/extGroup/searchGroup"

    headers = {
        "Content-Type": "application/json",
        "X-Emp-No": userid,
        "X-Auth-Value": token,
        "Terminal-Type": "Web@Desktop_999_ID1004",
        "X-Origin-ServiceName": "zte-icenter-msg-coclaw",
        "authKey": "msgSkill",
    }

    body = {
        "groupname": groupname,
    }

    response = requests.post(
        url,
        headers=headers,
        json=body,
        timeout=TIMEOUT,
        proxies={"http": None, "https": None},
        verify=False,
    )
    response.raise_for_status()
    return response.json()


def print_result(resp: Dict[str, Any]) -> None:
    """
    解析响应并打印搜索结果。

    Args:
        resp: 接口响应字典
    """
    code_info = resp.get("code", {})
    if str(code_info.get("code")) != "200":
        _app_logger.error("搜索群聊失败：%s", code_info.get("msg", "未知错误"))
        print(f"搜索群聊失败：{code_info.get('msg', '未知错误')}")
        return

    bo = resp.get("bo", [])
    if not bo:
        _app_logger.info("未找到匹配的群聊")
        print("未找到匹配的群聊")
        return

    for group in bo:
        print(f"群名称：{group.get('groupName', '')}，管理员：{group.get('adminID', '')}，群uri：{group.get('groupUri', '')}")


def main() -> None:
    # ── 读取环境变量 ──────────────────────────────────────────────────────────
    userid = os.getenv("coclaw_empno") or os.getenv("EMP_NO")
    token = os.getenv("coclaw_token") or os.getenv("AUTH_VALUE")

    if not userid or not token:
        _app_logger.error("请设置环境变量 coclaw_empno 和 coclaw_token（或 EMP_NO 和 AUTH_VALUE）")
        print(
            "错误：请设置环境变量 coclaw_empno 和 coclaw_token（或 EMP_NO 和 AUTH_VALUE）",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── 解析命令行参数 ────────────────────────────────────────────────────────
    parser = argparse.ArgumentParser(description="搜索 iCenter 群聊")
    parser.add_argument(
        "--groupname",
        required=True,
        help="群名称关键字（必填）",
    )
    args = parser.parse_args()

    groupname: str = args.groupname.strip()

    if not groupname:
        _app_logger.error("群名称关键字不能为空")
        print("错误：群名称关键字不能为空", file=sys.stderr)
        sys.exit(1)

    # ── 调用接口 ──────────────────────────────────────────────────────────────
    try:
        resp = search_group(userid, token, groupname)
    except requests.exceptions.HTTPError as e:
        _app_logger.error("HTTP 请求失败：%s", e)
        print(f"HTTP 请求失败：{e}", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        _app_logger.error("错误：%s", e)
        print(f"网络请求异常：{e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        _app_logger.error("错误：%s", e)
        print(f"未知错误：{e}", file=sys.stderr)
        sys.exit(1)

    # ── 打印结果 ──────────────────────────────────────────────────────────────
    print_result(resp)


if __name__ == "__main__":
    main()
