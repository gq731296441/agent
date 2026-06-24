#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
调用 iCenter 接口从群中删除成员

使用方式：
    python groupmanage_delmem.py --groupid "sip:p1_20260121192203571551@gm.zte.com.cn" --members "00130021,00130028"

参数说明：
    --groupid  群 URI（必填，格式：sip:xxx@gm.zte.com.cn）
    --members  待删除的成员工号，逗号分隔（必填，数量须大于 0）

环境变量（必需）：
    coclaw_empno: 调用者工号
    coclaw_token: 认证令牌
"""

import os
import sys
sys.stdout.reconfigure(encoding='utf-8')
import json
import argparse
import requests
import logging
from logging.handlers import RotatingFileHandler
from typing import List, Tuple, Dict, Any
from util import getGroupInfo, get_msg_base_url

_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
if not os.path.exists(_log_dir):
    os.makedirs(_log_dir)

_log_file = os.path.join(_log_dir, "groupmanage_delmem.log")
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

_app_logger = logging.getLogger("groupmanage_delmem")
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


def can_del_member(groupuri: str, userid: str, token: str) -> Tuple[bool, str, str]:
    """
    判断用户是否有从群中删除成员的权限

    权限规则：
        仅群主（admin_userid）有权限删除成员

    Args:
        groupuri: 群的URI，格式为 sip:<groupid>@gm.zte.com.cn
        userid:   待判断的用户工号
        token:    调用者的认证令牌

    Returns:
        (has_permission, admin_empno, admin_name)
        - has_permission: True - 有权限；False - 无权限
        - admin_empno:    群主工号
        - admin_name:     群主中文姓名（无法获取时为空字符串）

    Raises:
        requests.exceptions.RequestException: 网络请求异常
        ValueError: 获取群详情失败（群不存在、调用者非成员等）
    """
    detail = getGroupInfo(groupuri, userid, token)
    gi = detail.groupinfo

    admin_empno = gi.admin_userid

    # 从成员列表中查找群主姓名
    admin_name = ""
    for member in detail.memberlist:
        if member.userid == admin_empno:
            admin_name = member.name_zh
            break

    return userid == admin_empno, admin_empno, admin_name


def del_group_member(userid: str, token: str, groupid: str, members: List[str]) -> Dict[str, Any]:
    """
    调用 iCenter 接口从群中删除成员。

    Args:
        userid:  调用者工号
        token:   认证令牌
        groupid: 目标群 URI（格式：sip:xxx@gm.zte.com.cn）
        members: 待删除的成员工号列表

    Returns:
        解析后的响应字典，包含 code 状态码和 bo 业务数据
    """
    caller_sip = build_sip(userid)
    url = f"{BASE_URL}/zte-icenter-msg-bff/services/extGroup/public-group/users/{caller_sip}/deleteGroupMemberEx"

    headers = {
        "Content-Type": "application/json",
        "X-Emp-No": userid,
        "X-Auth-Value": token,
        "Terminal-Type": "Web@Desktop_999_ID1004",
        "X-Origin-ServiceName": "zte-icenter-msg-coclaw",
        "authKey": "msgSkill",
    }

    body = {
        "groupid": groupid,
        "operuri": caller_sip,
        "members": [{"uri": build_sip(m)} for m in members],
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


def print_result(groupid: str, requested_members: List[str], resp: Dict[str, Any]) -> None:
    """
    解析响应并打印删除成员结果。

    Args:
        groupid:           目标群 URI
        requested_members: 请求 body 中要求删除的成员工号列表
        resp:              接口响应字典
    """
    code_info = resp.get("code", {})
    if str(code_info.get("code")) != "200":
        _app_logger.error("删除群成员失败：%s", code_info.get("msg", "未知错误"))
        print(f"删除群成员失败：{code_info.get('msg', '未知错误')}")
        return
        
    print(f"群URI ：{groupid}")
    print(f"成功删除成员（{len(requested_members)}）：{', '.join(requested_members)}")


def main() -> None:
    # ── 读取环境变量 ──────────────────────────────────────────────────────────
    userid = os.getenv("coclaw_empno") or os.getenv("EMP_NO")
    token = os.getenv("coclaw_token") or os.getenv("AUTH_VALUE")

    if not userid or not token:
        _app_logger.error("环境变量配置错误：%s", "请设置环境变量 coclaw_empno 和 coclaw_token（或 EMP_NO 和 AUTH_VALUE）")
        print(
            "错误：请设置环境变量 coclaw_empno 和 coclaw_token（或 EMP_NO 和 AUTH_VALUE）",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── 解析命令行参数 ────────────────────────────────────────────────────────
    parser = argparse.ArgumentParser(description="从 iCenter 群中删除成员")
    parser.add_argument(
        "--groupid",
        required=True,
        help="目标群 URI，格式：sip:xxx@gm.zte.com.cn（必填）",
    )
    parser.add_argument(
        "--members",
        required=True,
        help="待删除的成员工号，逗号分隔（必填）",
    )
    args = parser.parse_args()

    groupid: str = args.groupid.strip()
    members_raw = [m.strip() for m in args.members.split(",") if m.strip()]  # type: List[str]

    if not groupid:
        _app_logger.error("错误：%s", "群 URI 不能为空")
        print("错误：群 URI 不能为空", file=sys.stderr)
        sys.exit(1)

    if len(members_raw) < 1:
        _app_logger.error("错误：%s", "至少需要提供 1 个待删除的成员工号")
        print("错误：至少需要提供 1 个待删除的成员工号", file=sys.stderr)
        sys.exit(1)

    # ── 权限检查 ──────────────────────────────────────────────────────────────
    try:
        has_perm, admin_empno, admin_name = can_del_member(groupid, userid, token)
        if not has_perm:
            admin_info = f"{admin_name}（{admin_empno}）" if admin_name else admin_empno
            _app_logger.error("错误：%s", f"您没有从该群删除成员的权限（仅群主可操作）\n群主：{admin_info}")

            print(
                f"错误：您没有从该群删除成员的权限（仅群主可操作）\n群主：{admin_info}",
                file=sys.stderr,
            )
            sys.exit(1)
    except ValueError as e:
        _app_logger.error("权限检查失败：%s", e)
        print(f"权限检查失败：{e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        _app_logger.error("权限检查异常：%s", e)
        print(f"权限检查异常：{e}", file=sys.stderr)
        sys.exit(1)

    # ── 调用接口 ──────────────────────────────────────────────────────────────
    try:
        resp = del_group_member(userid, token, groupid, members_raw)
    except requests.exceptions.HTTPError as e:
        _app_logger.error("HTTP 请求失败：%s", e)
        print(f"HTTP 请求失败：{e}", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        _app_logger.error("错误：%s", "网络请求异常")
        print(f"网络请求异常：{e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        _app_logger.error("错误：%s", "未知错误")
        print(f"未知错误：{e}", file=sys.stderr)
        sys.exit(1)

    # ── 打印结果 ──────────────────────────────────────────────────────────────
    print_result(groupid, members_raw, resp)


if __name__ == "__main__":
    main()
