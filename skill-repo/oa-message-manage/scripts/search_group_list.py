#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
调用 iCenter 接口按条件查询当前用户所在的群列表

使用方式：
    python search_group_list.py --groupname "开发推动"
    python search_group_list.py --adminid "10145777" --has-group-name 1
    python search_group_list.py --inactive-days 30 --has-group-name 0
    python search_group_list.py --inactive-years 1 --adminid "10145777" --has-group-name 1

参数说明：
    --groupname        群名称关键字（可选）
    --adminid          群主工号（可选）
    --inactive-days    查询最近多少天没有新消息的群（可选）
    --inactive-years   查询最近多少年没有新消息的群（可选）
    --has-group-name   群正式名称过滤：0-无正式名称，1-有正式名称，2-不按此条件过滤（默认）

环境变量（必需）：
    coclaw_empno: 调用者工号
    coclaw_token: 认证令牌
"""

import os
import sys
import time
sys.stdout.reconfigure(encoding='utf-8')
import argparse
import urllib3
import requests
import logging
from logging.handlers import RotatingFileHandler

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from typing import Any, Dict, Optional

from util import get_msg_base_url

_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
if not os.path.exists(_log_dir):
    os.makedirs(_log_dir)

_log_file = os.path.join(_log_dir, "search_group_list.log")
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

_app_logger = logging.getLogger("search_group_list")
_app_logger.setLevel(logging.DEBUG)
_app_logger.propagate = False
_app_logger.addHandler(_file_handler)
_app_logger.addHandler(_console_handler)

BASE_URL = get_msg_base_url()
TIMEOUT = 15
SECONDS_PER_DAY = 24 * 60 * 60
SECONDS_PER_YEAR = 365 * SECONDS_PER_DAY


def build_lastmsgstamp(inactive_days: Optional[int], inactive_years: Optional[int]) -> Optional[int]:
    """
    将用户输入的天数/年数转换为接口所需的 unix 时间戳（秒）。

    Args:
        inactive_days:  不活跃天数
        inactive_years: 不活跃年数

    Returns:
        Unix 时间戳（秒）；如果两个参数都为 None，则返回 None
    """
    if inactive_days is not None:
        return int(time.time()) - inactive_days * SECONDS_PER_DAY
    if inactive_years is not None:
        return int(time.time()) - inactive_years * SECONDS_PER_YEAR
    return None


def search_group_list(
    userid: str,
    token: str,
    groupname: str = "",
    adminid: str = "",
    inactive_days: Optional[int] = None,
    inactive_years: Optional[int] = None,
    has_group_name: int = 2,
) -> dict:
    """
    调用 iCenter 接口按条件查询当前用户所在的群列表

    Args:
        userid:          调用者工号
        token:           认证令牌
        groupname:       群名称关键字
        adminid:         群主工号
        inactive_days:   查询最近多少天没有新消息的群
        inactive_years:  查询最近多少年没有新消息的群
        has_group_name:  0-无正式名称，1-有正式名称，2-不按此条件过滤

    Returns:
        解析后的响应字典
    """
    url = f"{BASE_URL}/zte-icenter-msg-bff/services/extGroup/getMyGroupList"

    headers = {
        "Content-Type": "application/json",
        "X-Emp-No": userid,
        "X-Auth-Value": token,
        "Terminal-Type": "Web@Desktop_999_ID1004",
        "X-Origin-ServiceName": "zte-icenter-msg-coclaw",
        "authKey": "msgSkill",
    }

    lastmsgstamp = build_lastmsgstamp(inactive_days, inactive_years)

    body: Dict[str, Any] = {}
    if lastmsgstamp is not None:
        body["beforeMsgStamp"] = lastmsgstamp
    if groupname:
        body["groupName"] = groupname
    if adminid:
        body["adminID"] = adminid
    if has_group_name in (0, 1):
        body["hasGroupName"] = has_group_name

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


def validate_filters(
    groupname: str,
    adminid: str,
    inactive_days: Optional[int],
    inactive_years: Optional[int],
    has_group_name: int,
) -> None:
    """
    校验查询条件，避免无条件拉取全部群列表。

    Args:
        groupname:       群名称关键字
        adminid:         群主工号
        inactive_days:   查询最近多少天没有新消息的群
        inactive_years:  查询最近多少年没有新消息的群
        has_group_name:  群正式名称过滤：0-无正式名称，1-有正式名称，2-不按此条件过滤

    Raises:
        ValueError: 查询条件不合法
    """
    if inactive_days is not None and inactive_years is not None:
        raise ValueError("--inactive-days 和 --inactive-years 不能同时使用")

    has_any_filter = bool(
        groupname or adminid or inactive_days is not None or inactive_years is not None or has_group_name in (0, 1)
    )
    if not has_any_filter:
        raise ValueError(
            "至少需要提供一个查询条件：--groupname / --adminid / --inactive-days / --inactive-years / --has-group-name"
        )

    if inactive_days is not None and inactive_days <= 0:
        raise ValueError("--inactive-days 必须为大于 0 的整数")

    if inactive_years is not None and inactive_years <= 0:
        raise ValueError("--inactive-years 必须为大于 0 的整数")


def format_group_name(group: Dict[str, Any]) -> str:
    """
    优先返回正式群名，没有时返回默认群名。

    Args:
        group: 群信息字典

    Returns:
        群名称字符串
    """
    return group.get("groupName") or group.get("defaultGroupName") or "（未设置）"


def print_result(resp: Dict[str, Any]) -> None:
    """
    解析响应并打印查询群列表结果。

    Args:
        resp: 接口响应字典
    """
    code_info = resp.get("code", {})
    if str(code_info.get("code")) != "200":
        _app_logger.error("查询群列表失败：%s", code_info.get("msg", "未知错误"))
        print(f"查询群列表失败：{code_info.get('msg', '未知错误')}")
        return

    groups = resp.get("bo") or []
    if not groups:
        _app_logger.info("未找到符合条件的群聊")
        print("未找到符合条件的群聊")
        return

    print(f"匹配群数量：{len(groups)}")
    for index, group in enumerate(groups, start=1):
        group_uri = group.get("groupuri") or group.get("groupUri") or ""
        group_name = group.get("groupName") or ""
        default_group_name = group.get("defaultGroupName") or ""
        admin_id = group.get("adminID") or ""
        last_msg_stamp = group.get("lastMsgStamp")

        print(f"{index}. 群名称：{format_group_name(group)}")
        print(f"   正式群名：{group_name if group_name else '（未设置）'}")
        print(f"   默认群名：{default_group_name if default_group_name else '（未设置）'}")
        print(f"   群主工号：{admin_id}")
        print(f"   最后一条消息时间戳：{last_msg_stamp if last_msg_stamp is not None else '（未知）'}")
        print(f"   群URI ：{group_uri}")


def main() -> None:
    userid = os.getenv("coclaw_empno") or os.getenv("EMP_NO")
    token = os.getenv("coclaw_token") or os.getenv("AUTH_VALUE")

    if not userid or not token:
        _app_logger.error("请设置环境变量 coclaw_empno 和 coclaw_token（或 EMP_NO 和 AUTH_VALUE）")
        print(
            "错误：请设置环境变量 coclaw_empno 和 coclaw_token（或 EMP_NO 和 AUTH_VALUE）",
            file=sys.stderr,
        )
        sys.exit(1)

    parser = argparse.ArgumentParser(description="按条件查询当前用户所在的 iCenter 群列表")
    parser.add_argument(
        "--groupname",
        default="",
        help="群名称关键字（可选）",
    )
    parser.add_argument(
        "--adminid",
        default="",
        help="群主工号（可选）",
    )
    parser.add_argument(
        "--inactive-days",
        type=int,
        default=None,
        help="查询最近多少天没有新消息的群（可选）",
    )
    parser.add_argument(
        "--inactive-years",
        type=int,
        default=None,
        help="查询最近多少年没有新消息的群（可选）",
    )
    parser.add_argument(
        "--has-group-name",
        type=int,
        choices=[0, 1, 2],
        default=2,
        help="群正式名称过滤：0-无正式名称，1-有正式名称，2-不按此条件过滤（默认）",
    )
    args = parser.parse_args()

    groupname = args.groupname.strip()
    adminid = args.adminid.strip()
    inactive_days = args.inactive_days
    inactive_years = args.inactive_years
    has_group_name = args.has_group_name

    try:
        validate_filters(groupname, adminid, inactive_days, inactive_years, has_group_name)
    except ValueError as e:
        _app_logger.error("错误：%s", e)
        print(f"错误：{e}", file=sys.stderr)
        sys.exit(1)

    try:
        resp = search_group_list(
            userid=userid,
            token=token,
            groupname=groupname,
            adminid=adminid,
            inactive_days=inactive_days,
            inactive_years=inactive_years,
            has_group_name=has_group_name,
        )
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

    print_result(resp)


if __name__ == "__main__":
    main()
