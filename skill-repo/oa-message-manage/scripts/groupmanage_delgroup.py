#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
调用 iCenter 接口删除群聊

使用方式：
    # 第一步：查询群信息（用于向用户展示二次确认，不执行删除）
    python groupmanage_delgroup.py --groupid "sip:p1_20250827143555081257@gm.zte.com.cn" --check-only

    # 第二步：用户确认后正式删除
    python groupmanage_delgroup.py --groupid "sip:p1_20250827143555081257@gm.zte.com.cn" --confirm-groupid "sip:p1_20250827143555081257@gm.zte.com.cn"

参数说明：
    --groupid          群 URI（必填，格式：sip:xxx@gm.zte.com.cn）
    --confirm-groupid  二次确认用的群 URI，必须与 --groupid 完全一致（正式删除时必填）
    --check-only       仅查询群信息和权限，不执行删除（用于向用户展示确认信息）

环境变量（必需）：
    coclaw_empno: 调用者工号
    coclaw_token: 认证令牌
"""

import os
import sys
sys.stdout.reconfigure(encoding='utf-8')
import argparse
import requests
import logging
from logging.handlers import RotatingFileHandler
from typing import Tuple, Dict, Any
from util import getGroupInfo, get_msg_base_url

_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
if not os.path.exists(_log_dir):
    os.makedirs(_log_dir)

_log_file = os.path.join(_log_dir, "groupmanage_delgroup.log")
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

_app_logger = logging.getLogger("groupmanage_delgroup")
_app_logger.setLevel(logging.DEBUG)
_app_logger.propagate = False
_app_logger.addHandler(_file_handler)
_app_logger.addHandler(_console_handler)

DELETE_GROUP_URL = f"{get_msg_base_url()}/zte-icenter-msg-bff/services/extGroup/deleteGroup"
TIMEOUT = 15


def can_delete_group(groupuri: str, userid: str, token: str) -> Tuple[bool, str, str, str, int]:
    """
    判断用户是否有删除群聊的权限

    权限规则：
        仅群主（admin_userid）有权限删除群聊

    Args:
        groupuri: 群的 URI，格式为 sip:<groupid>@gm.zte.com.cn
        userid:   待判断的用户工号
        token:    调用者的认证令牌

    Returns:
        (has_permission, admin_empno, admin_name, group_name, member_count)
        - has_permission: True - 有权限；False - 无权限
        - admin_empno:    群主工号
        - admin_name:     群主中文姓名（无法获取时为空字符串）
        - group_name:     群名称（未设置时取默认群名称）
        - member_count:   删除前群成员数量

    Raises:
        requests.exceptions.RequestException: 网络请求异常
        ValueError: 获取群详情失败（群不存在、调用者非成员等）
    """
    detail = getGroupInfo(groupuri, userid, token)
    gi = detail.groupinfo

    admin_empno = gi.admin_userid
    group_name = gi.group_name or gi.default_group_name
    member_count = len(detail.memberlist)

    admin_name = ""
    for member in detail.memberlist:
        if member.userid == admin_empno:
            admin_name = member.name_zh
            break
    return userid == admin_empno, admin_empno, admin_name, group_name, member_count


def delete_group(userid: str, token: str, groupid: str) -> Dict[str, Any]:
    """
    调用 iCenter 接口删除群聊。

    Args:
        userid:  调用者工号
        token:   认证令牌
        groupid: 目标群 URI（格式：sip:xxx@gm.zte.com.cn）

    Returns:
        解析后的响应字典；若接口未返回 JSON，则返回包装后的结果字典
    """
    headers = {
        "Content-Type": "application/json",
        "X-Emp-No": userid,
        "X-Auth-Value": token,
        "Terminal-Type": "Web@Desktop_999_ID1004",
        "X-Origin-ServiceName": "zte-icenter-msg-coclaw",
        "authKey": "msgSkill",
    }

    response = requests.post(
        DELETE_GROUP_URL,
        headers=headers,
        params={"guri": groupid},
        timeout=TIMEOUT,
        proxies={"http": None, "https": None},
        verify=False,
    )
    response.raise_for_status()

    try:
        return response.json()
    except ValueError:
        return {
            "code": {
                "code": str(response.status_code),
                "msg": response.text.strip() or "删除成功",
            }
        }


def validate_confirmation(groupid: str, confirm_groupid: str) -> None:
    """
    校验删除群聊时的二次确认参数

    Args:
        groupid:         目标群 URI
        confirm_groupid: 用户确认输入的群 URI

    Raises:
        ValueError: 二次确认参数缺失或与目标群不一致
    """
    if not confirm_groupid:
        raise ValueError("缺少二次确认参数，请传入 --confirm-groupid")

    if confirm_groupid != groupid:
        raise ValueError("二次确认失败：--confirm-groupid 与 --groupid 不一致，已取消删除")


def print_group_info(groupid: str, group_name: str, member_count: int) -> None:
    """
    打印群基本信息（用于二次确认前展示）

    Args:
        groupid:      目标群 URI
        group_name:   群名称
        member_count: 当前群成员数量
    """
    _app_logger.info("群名称：%s，群URI ：s，群成员数量：%d", group_name, groupid, member_count)
    print(f"群名称：{group_name if group_name else '（未设置）'}")
    print(f"群URI ：{groupid}")
    print(f"群成员数量：{member_count}")


def print_result(groupid: str, group_name: str, member_count: int, resp: dict) -> None:
    """
    解析响应并打印结果

    Args:
        groupid:      目标群 URI
        group_name:   群名称
        member_count: 删除前群成员数量
        resp:         接口响应字典
    """
    code_info = resp.get("code", {})
    code = str(code_info.get("code", ""))
    msg = code_info.get("msg", "")

    if code and code not in {"200", "204"}:
        _app_logger.error("删除群聊失败：%s", msg or "未知错误")
        print(f"删除群聊失败：{msg or '未知错误'}")
        return

    _app_logger.info("删除群聊成功：%s", msg or "删除成功")

    print(f"群名称：{group_name if group_name else '（未设置）'}")
    print(f"群URI ：{groupid}")
    print(f"群成员数量：{member_count}")
    print("删除结果：成功")


def main() -> None:
    # ── 读取环境变量 ──────────────────────────────────────────────────────────
    userid = os.getenv("coclaw_empno") or os.getenv("EMP_NO")
    token = os.getenv("coclaw_token") or os.getenv("AUTH_VALUE")
    if not userid or not token:
        _app_logger.error("环境变量 coclaw_empno 和 coclaw_token（或 EMP_NO 和 AUTH_VALUE）未设置")
        print(
            "错误：请设置环境变量 coclaw_empno 和 coclaw_token（或 EMP_NO 和 AUTH_VALUE）",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── 解析命令行参数 ────────────────────────────────────────────────────────
    parser = argparse.ArgumentParser(description="删除 iCenter 群聊")
    parser.add_argument(
        "--groupid",
        required=True,
        help="目标群 URI，格式：sip:xxx@gm.zte.com.cn（必填）",
    )
    parser.add_argument(
        "--confirm-groupid",
        default="",
        help="二次确认用的群 URI，必须与 --groupid 完全一致（正式删除时必填）",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="仅查询群信息和权限，不执行删除（用于二次确认前展示）",
    )
    args = parser.parse_args()

    groupid: str = args.groupid.strip()
    confirm_groupid: str = args.confirm_groupid.strip()
    check_only: bool = args.check_only

    if not groupid:
        _app_logger.error("群 URI 不能为空")
        print("错误：群 URI 不能为空", file=sys.stderr)
        sys.exit(1)

    # ── 权限检查（--check-only 和正式删除均执行） ─────────────────────────────
    try:
        has_perm, admin_empno, admin_name, group_name, member_count = can_delete_group(groupid, userid, token)
        if not has_perm:
            admin_info = f"{admin_name}（{admin_empno}）" if admin_name else admin_empno
            _app_logger.error("您没有删除该群的权限（仅群主可操作）\n群主：%s", admin_info)
            print(
                f"错误：您没有删除该群的权限（仅群主可操作）\n群主：{admin_info}",
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

    # ── 仅查询模式：打印群信息后退出，不执行删除 ──────────────────────────────
    if check_only:
        print_group_info(groupid, group_name, member_count)
        return

    # ── 正式删除：校验二次确认参数 ────────────────────────────────────────────
    try:
        validate_confirmation(groupid, confirm_groupid)
    except ValueError as e:
        _app_logger.error("错误：%s", e)
        print(f"错误：{e}", file=sys.stderr)
        sys.exit(1)

    # ── 调用接口 ──────────────────────────────────────────────────────────────
    try:
        resp = delete_group(userid, token, groupid)
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
    print_result(groupid, group_name, member_count, resp)


if __name__ == "__main__":
    main()
