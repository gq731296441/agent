#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
调用 iCenter 接口创建群聊

使用方式：
    python groupmanage_creategroup.py --members "78952136598,2575326876"
    python groupmanage_creategroup.py --groupname "202603测试沟通群" --members "78952136598,2575326876"

参数说明：
    --groupname  群名称（可选，可为空）
    --members    群成员工号，逗号分隔（必填，数量须大于1）

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

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from typing import List, Dict, Any

from util import get_msg_base_url

_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
if not os.path.exists(_log_dir):
    os.makedirs(_log_dir)

_log_file = os.path.join(_log_dir, "groupmanage_creategroup.log")
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

_app_logger = logging.getLogger("groupmanage_creategroup")
_app_logger.setLevel(logging.DEBUG)
_app_logger.propagate = False
_app_logger.addHandler(_file_handler)
_app_logger.addHandler(_console_handler)

BASE_URL = get_msg_base_url()
TIMEOUT = 15
SIP_DOMAIN = "zte.com.cn"


def build_sip(emp_no: str) -> str:
    """
    根据工号构造 SIP URI。

    Args:
        emp_no: 员工工号

    Returns:
        SIP URI，格式为 sip:<emp_no>@zte.com.cn
    """
    return f"sip:{emp_no}@{SIP_DOMAIN}"


def create_group(userid: str, token: str, groupname: str, members: List[str]) -> Dict[str, Any]:
    """
    调用 iCenter 接口创建群聊。

    Args:
        userid:    调用者工号
        token:     认证令牌
        groupname: 群名称（可为空字符串）
        members:   群成员工号列表（不含调用者时会自动补充）

    Returns:
        解析后的响应字典，包含 code 状态码和 bo 业务数据（群信息和成员列表）
    """
    # 若调用者不在成员列表中，自动加入
    all_members = list(members)
    if userid not in all_members:
        all_members.append(userid)

    url = (
        f"{BASE_URL}/zte-icenter-msg-bff/services/extGroup/addGroup"
    )

    headers = {
        "Content-Type": "application/json",
        "X-Emp-No": userid,
        "X-Auth-Value": token,
        "Terminal-Type": "Web@Desktop_999_ID1004",
        "X-Origin-ServiceName": "zte-icenter-msg-coclaw",
        "authKey": "msgSkill",
    }

    body = {
        "gname": groupname,
        "ouri": build_sip(userid),
        "members": [{"uri": build_sip(m)} for m in all_members],
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


def print_result(groupname: str, requested_members: List[str], resp: Dict[str, Any]) -> None:
    """
    解析响应并打印创建群聊结果。

    Args:
        groupname:         群名称
        requested_members: 请求 body 中要求加入的成员工号列表
        resp:              接口响应字典
    """
    code_info = resp.get("code", {})
    if str(code_info.get("code")) != "200":
        print(f"创建群聊失败：{code_info.get('msg', '未知错误')}")
        return

    bo = resp.get("bo", {})
    group_uri = bo.get("groupid", "")
    resp_members = bo.get("members") or []  # type: List[dict]

    # 将响应中成员的结果索引成 {工号: ret}
    # 响应 URI 格式: sip:xxxxx@zte.com.cn
    member_result = {}  # type: Dict[str, int]
    for m in resp_members:
        uri = m.get("uri", "")
        # 提取工号部分
        if uri.startswith("sip:") and "@" in uri:
            emp = uri[len("sip:"):uri.index("@")]
            member_result[emp] = int(m.get("ret", 0))

    # 只关注请求 body 中的成员
    success_members = []
    fail_members = []
    for emp in requested_members:
        ret = member_result.get(emp)
        if ret == 200:
            success_members.append(emp)
        else:
            fail_members.append(emp)

    _app_logger.info("群URI ：%s", group_uri)
    
    print(f"群名称：{groupname if groupname else '（未设置）'}")
    print(f"群URI ：{group_uri}")
    print(f"成功入群成员（{len(success_members)}）：{', '.join(success_members) if success_members else '无'}")
    print(f"未成功入群成员（{len(fail_members)}）：{', '.join(fail_members) if fail_members else '无'}")


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
    parser = argparse.ArgumentParser(description="创建 iCenter 群聊")
    parser.add_argument(
        "--groupname",
        default="",
        help="群名称（可选，可为空）",
    )
    parser.add_argument(
        "--members",
        required=True,
        help="群成员工号，逗号分隔，数量须大于 1（必填）",
    )
    args = parser.parse_args()

    groupname: str = args.groupname or ""
    members_raw = [m.strip() for m in args.members.split(",") if m.strip()]  # type: List[str]

    if len(members_raw) < 2:
        _app_logger.error("群成员数量须大于 1，请提供至少 2 个成员工号")
        print("错误：群成员数量须大于 1，请提供至少 2 个成员工号", file=sys.stderr)
        sys.exit(1)

    # ── 调用接口 ──────────────────────────────────────────────────────────────
    try:
        resp = create_group(userid, token, groupname, members_raw)
    except requests.exceptions.HTTPError as e:
        _app_logger.error("HTTP 请求失败：%s", e)
        print(f"HTTP 请求失败：{e}", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        _app_logger.error("网络请求异常：%s", e)
        print(f"网络请求异常：{e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        _app_logger.error("未知错误：%s", e)    
        print(f"未知错误：{e}", file=sys.stderr)
        sys.exit(1)

    # ── 打印结果 ──────────────────────────────────────────────────────────────
    print_result(groupname, members_raw, resp)


if __name__ == "__main__":
    main()
