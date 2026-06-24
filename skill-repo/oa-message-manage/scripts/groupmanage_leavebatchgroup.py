#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
调用 iCenter 接口批量退出群聊（当前用户退群）

使用方式：
    # 方式一：已知多个群 URI 时，先查询群信息（用于向用户展示二次确认，不执行退群）
    python groupmanage_leavebatchgroup.py --groupids "sip:p1_demo_a@gm.zte.com.cn,sip:p1_demo_b@gm.zte.com.cn" --check-only

    # 方式二：用户确认后正式批量退出这些群
    python groupmanage_leavebatchgroup.py --groupids "sip:p1_demo_a@gm.zte.com.cn,sip:p1_demo_b@gm.zte.com.cn" --confirm-groupids "sip:p1_demo_a@gm.zte.com.cn,sip:p1_demo_b@gm.zte.com.cn"

    # 方式三：用户先按条件筛选候选群（例如：群名包含“开发推动”，且没有正式群名）
    python search_group_list.py --groupname "开发推动" --has-group-name 0

    # 方式四：从查询结果中确认目标群 URI 后，先做二次确认展示
    python groupmanage_leavebatchgroup.py --groupids "sip:rcs_demo_a@gm.zte.com.cn,sip:rcs_demo_b@gm.zte.com.cn" --check-only

    # 方式五：用户确认后正式批量退出这些群
    python groupmanage_leavebatchgroup.py --groupids "sip:rcs_demo_a@gm.zte.com.cn,sip:rcs_demo_b@gm.zte.com.cn" --confirm-groupids "sip:rcs_demo_a@gm.zte.com.cn,sip:rcs_demo_b@gm.zte.com.cn"

参数说明：
    --groupids          待退出的群 URI 列表，英文逗号分隔（必填）
    --confirm-groupids  二次确认用的群 URI 列表，必须与 --groupids 完全一致（正式退群时必填）
    --check-only        仅查询群信息，不执行退群（用于向用户展示确认信息）

环境变量（推荐）：
    coclaw_empno: 调用者工号
    coclaw_token: 认证令牌
"""

import os
import sys
sys.stdout.reconfigure(encoding="utf-8")
import argparse
import logging
from logging.handlers import RotatingFileHandler
import requests
from typing import Any, Dict, List, Tuple
from util import getGroupInfo, get_msg_base_url

_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
if not os.path.exists(_log_dir):
    os.makedirs(_log_dir)

_log_file = os.path.join(_log_dir, "groupmanage_leavebatchgroup.log")
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

_app_logger = logging.getLogger("groupmanage_leavebatchgroup")
_app_logger.setLevel(logging.DEBUG)
_app_logger.propagate = False
_app_logger.addHandler(_file_handler)
_app_logger.addHandler(_console_handler)

BASE_URL = get_msg_base_url()
TIMEOUT = 15
SIP_DOMAIN = "zte.com.cn"


class GroupLeaveCheckResult:
    """批量退群前的群信息"""

    def __init__(self, groupid: str, group_name: str, member_count: int):
        self.groupid = groupid
        self.group_name = group_name
        self.member_count = member_count


def build_sip(emp_no: str) -> str:
    """
    根据工号构造 SIP URI。

    Args:
        emp_no: 员工工号

    Returns:
        SIP URI，格式为 sip:<emp_no>@zte.com.cn
    """
    return f"sip:{emp_no}@{SIP_DOMAIN}"


def parse_groupids(raw_groupids: str) -> List[str]:
    """
    解析并去重群 URI 列表，保持原有顺序。

    Args:
        raw_groupids: 逗号分隔的群 URI 字符串

    Returns:
        去重后的群 URI 列表
    """
    groupids: List[str] = []
    seen = set()
    for item in raw_groupids.split(","):
        groupid = item.strip()
        if not groupid or groupid in seen:
            continue
        seen.add(groupid)
        groupids.append(groupid)
    return groupids


def collect_leave_targets(groupids: List[str], userid: str, token: str) -> List[GroupLeaveCheckResult]:
    """
    逐个查询群信息。退群不需要群主权限，但如果群不存在或查询失败，需要提前终止。

    Args:
        groupids: 群 URI 列表
        userid:   调用者工号
        token:    认证令牌

    Returns:
        GroupLeaveCheckResult 对象列表

    Raises:
        ValueError: 批量查询群信息失败
    """
    results: List[GroupLeaveCheckResult] = []
    errors: List[str] = []

    for groupid in groupids:
        try:
            detail = getGroupInfo(groupid, userid, token)
            gi = detail.groupinfo
            group_name = gi.group_name or gi.default_group_name
            member_count = len(detail.memberlist)
        except requests.exceptions.RequestException as e:
            _app_logger.error("网络请求异常：%s", e)
            errors.append(f"{groupid}：网络请求异常：{e}")
            continue
        except ValueError as e:
            _app_logger.error("错误：%s", e)
            errors.append(f"{groupid}：{e}")
            continue
        except Exception as e:
            _app_logger.error("错误：%s", e)
            errors.append(f"{groupid}：{e}")
            continue

        results.append(GroupLeaveCheckResult(groupid, group_name, member_count))

    if errors:
        raise ValueError("批量查询群信息失败：\n" + "\n".join(errors))

    return results


def leave_batch_group(userid: str, token: str, groupids: List[str]) -> Dict[str, Any]:
    """
    调用 iCenter 接口批量退出群聊。

    Args:
        userid:   调用者工号
        token:    认证令牌
        groupids: 待退出的群 URI 列表

    Returns:
        解析后的响应字典；若接口未返回 JSON，则返回包装后的结果字典
    """
    caller_sip = build_sip(userid)
    url = f"{BASE_URL}/zte-icenter-msg-bff/services/extGroup/public-group/users/{caller_sip}/leaveBatchGroup"

    headers = {
        "Content-Type": "application/json",
        "X-Emp-No": userid,
        "X-Auth-Value": token,
        "Terminal-Type": "Web@Desktop_999_ID1004",
        "X-Origin-ServiceName": "zte-icenter-msg-coclaw",
        "authKey": "msgSkill",
    }

    body = {
        "groups": [{"guri": groupid} for groupid in groupids],
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

    try:
        return response.json()
    except ValueError:
        return {
            "code": {
                "code": str(response.status_code),
                "msg": response.text.strip() or "批量退出群聊完成",
            }
        }


def validate_confirmation(groupids: List[str], confirm_groupids: List[str]) -> None:
    """
    校验批量退出群聊时的二次确认参数。

    Args:
        groupids:          待退出的群 URI 列表
        confirm_groupids:  用户确认的群 URI 列表

    Raises:
        ValueError: 二次确认参数缺失或与目标群不一致
    """
    if not confirm_groupids:
        raise ValueError("缺少二次确认参数，请传入 --confirm-groupids")

    if confirm_groupids != groupids:
        raise ValueError("二次确认失败：--confirm-groupids 与 --groupids 不一致，已取消退群")


def extract_group_result_map(resp: Dict[str, Any]) -> Dict[str, Tuple[str, str]]:
    """
    尽量兼容接口可能返回的逐群结果结构。

    Args:
        resp: 接口响应字典

    Returns:
        群 URI 到 (结果码, 消息) 的映射字典
    """
    candidate_lists: List[List[Dict[str, Any]]] = []
    bo = resp.get("bo")

    if isinstance(bo, list):
        candidate_lists.append(bo)
    elif isinstance(bo, dict):
        for key in ("groups", "groupList", "results", "result", "items", "data"):
            value = bo.get(key)
            if isinstance(value, list):
                candidate_lists.append(value)

    for items in candidate_lists:
        result_map: Dict[str, Tuple[str, str]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue

            groupid = item.get("guri") or item.get("groupid") or item.get("groupUri") or item.get("uri")
            if not groupid:
                continue

            raw_code = ""
            if "ret" in item:
                raw_code = item.get("ret")
            elif "code" in item:
                raw_code = item.get("code")
            elif "result" in item:
                raw_code = item.get("result")

            msg = item.get("msg") or item.get("message") or ""
            result_map[groupid] = (str(raw_code).strip(), msg)

        if result_map:
            return result_map

    return {}


def print_group_info(results: List[GroupLeaveCheckResult]) -> None:
    """
    打印批量退群前的群基本信息。

    Args:
        results: GroupLeaveCheckResult 对象列表
    """
    _app_logger.info("待退出群数量：%s", len(results))
    print(f"待退出群数量：{len(results)}")
    for index, item in enumerate(results, start=1):
        print(f"{index}. 群名称：{item.group_name if item.group_name else '（未设置）'}")
        print(f"   群URI ：{item.groupid}")
        print(f"   群成员数量：{item.member_count}")


def print_result(results: List[GroupLeaveCheckResult], resp: Dict[str, Any]) -> None:
    """
    解析响应并打印批量退群结果。

    Args:
        results: 退群目标列表
        resp:    接口响应字典
    """
    code_info = resp.get("code", {})
    overall_code = str(code_info.get("code", "")).strip()
    overall_msg = code_info.get("msg", "")

    if overall_code and overall_code not in {"200", "204"}:
        _app_logger.error("批量退出群聊失败：%s", overall_msg or "未知错误")
        print(f"批量退出群聊失败：{overall_msg or '未知错误'}")
        return

    result_map = extract_group_result_map(resp)
    success_items: List[GroupLeaveCheckResult] = []
    failed_items: List[Tuple[GroupLeaveCheckResult, str]] = []

    for item in results:
        item_code, item_msg = result_map.get(item.groupid, ("", ""))
        if not item_code or item_code in {"200", "204"}:
            success_items.append(item)
        else:
            failed_items.append((item, item_msg or "未知错误"))

    _app_logger.info("成功退出群数量：%s", len(success_items))
    print(f"待退出群数量：{len(results)}")
    print(f"成功退出群数量：{len(success_items)}")
    print(f"失败群数量：{len(failed_items)}")

    for index, item in enumerate(success_items, start=1):
        print(f"成功[{index}] 群名称：{item.group_name if item.group_name else '（未设置）'}")
        print(f"成功[{index}] 群URI ：{item.groupid}")
        print(f"成功[{index}] 群成员数量：{item.member_count}")
        print(f"成功[{index}] 退群结果：成功")

    for index, failed in enumerate(failed_items, start=1):
        item, fail_msg = failed
        print(f"失败[{index}] 群名称：{item.group_name if item.group_name else '（未设置）'}")
        print(f"失败[{index}] 群URI ：{item.groupid}")
        print(f"失败[{index}] 原因：{fail_msg}")


def main() -> None:
    # 实际使用时建议从环境变量获取；这里与现有删除脚本保持风格一致
    userid = os.getenv("coclaw_empno") or os.getenv("EMP_NO")
    token = os.getenv("coclaw_token") or os.getenv("AUTH_VALUE")
    if not userid or not token:
        _app_logger.error("请设置环境变量 coclaw_empno 和 coclaw_token（或 EMP_NO 和 AUTH_VALUE）")
        print(
            "错误：请设置环境变量 coclaw_empno 和 coclaw_token（或 EMP_NO 和 AUTH_VALUE）",
            file=sys.stderr,
        )
        sys.exit(1)

    parser = argparse.ArgumentParser(description="批量退出 iCenter 群聊")
    parser.add_argument(
        "--groupids",
        required=True,
        help="待退出的群 URI 列表，英文逗号分隔（必填）",
    )
    parser.add_argument(
        "--confirm-groupids",
        default="",
        help="二次确认用的群 URI 列表，必须与 --groupids 完全一致（正式退群时必填）",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="仅查询群信息，不执行退群（用于二次确认前展示）",
    )
    args = parser.parse_args()

    groupids = parse_groupids(args.groupids)
    confirm_groupids = parse_groupids(args.confirm_groupids)
    check_only = args.check_only

    if not groupids:
        _app_logger.error("至少需要提供 1 个群 URI")
        print("错误：至少需要提供 1 个群 URI", file=sys.stderr)
        sys.exit(1)

    try:
        results = collect_leave_targets(groupids, userid, token)
    except ValueError as e:
        _app_logger.error("错误：%s", e)
        print(str(e), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        _app_logger.error("错误：%s", e)
        print(f"批量查询群信息异常：{e}", file=sys.stderr)
        sys.exit(1)

    if check_only:
        print_group_info(results)
        return

    try:
        validate_confirmation(groupids, confirm_groupids)
    except ValueError as e:
        _app_logger.error("错误：%s", e)
        print(f"错误：{e}", file=sys.stderr)
        sys.exit(1)

    try:
        resp = leave_batch_group(userid, token, groupids)
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

    print_result(results, resp)


if __name__ == "__main__":
    main()

