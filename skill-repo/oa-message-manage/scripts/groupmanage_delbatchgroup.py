#!/usr/bin/env python3

# -*- coding: utf-8 -*-

"""
调用 iCenter 接口批量解散群聊


使用方式：

    # 方式一：已知多个群 URI 时，可先 --check-only 核对 URI 与数量（不调用删除、不校验群主）

    python groupmanage_delbatchgroup.py --groupids "sip:p1_demo_a@gm.zte.com.cn,sip:p1_demo_b@gm.zte.com.cn" --check-only


    # 方式二：用户确认后正式批量删除（单次最多 100 个群 URI，去重后计数）

    python groupmanage_delbatchgroup.py --groupids "sip:p1_demo_a@gm.zte.com.cn,sip:p1_demo_b@gm.zte.com.cn" --confirm-groupids "sip:p1_demo_a@gm.zte.com.cn,sip:p1_demo_b@gm.zte.com.cn"


    # 方式三：用户先按条件筛选候选群（例如：群名包含"开发推动"，且群主为 10145777）

    python search_group_list.py --groupname "开发推动" --adminid "10145777"


    # 方式四：从查询结果中确认目标群 URI 后，可先 --check-only，再正式删除

    python groupmanage_delbatchgroup.py --groupids "sip:rcs_demo_a@gm.zte.com.cn,sip:rcs_demo_b@gm.zte.com.cn" --check-only

    python groupmanage_delbatchgroup.py --groupids "sip:rcs_demo_a@gm.zte.com.cn,sip:rcs_demo_b@gm.zte.com.cn" --confirm-groupids "sip:rcs_demo_a@gm.zte.com.cn,sip:rcs_demo_b@gm.zte.com.cn"

    # 获取详细错误日志

    python groupmanage_delbatchgroup.py --groupids "xxx" --confirm-groupids "xxx" --verbose

约束说明：

    - 单次 --groupids 最多 100 个（去重后）。
    - 群主身份须由调用方在脚本外依据查询结果等完成核对；本脚本不调用 getGroupInfo、不校验群主。

参数说明：

    --groupids          待删除的群 URI 列表，英文逗号分隔（必填）
    --confirm-groupids  二次确认用的群 URI 列表，必须与 --groupids 完全一致（正式删除时必填）
    --check-only        仅列出待解散群 URI 与数量，不执行删除
    --verbose           输出详细的错误日志和接口原始响应

环境变量（必需）：
    coclaw_empno: 调用者工号
    coclaw_token: 认证令牌
"""

import os
import sys
sys.stdout.reconfigure(encoding='utf-8')
import argparse
import urllib3
import requests
import logging
from logging.handlers import RotatingFileHandler

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import json
from typing import Any, Dict, List, Tuple

from util import get_msg_base_url

_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
if not os.path.exists(_log_dir):
    os.makedirs(_log_dir)

_log_file = os.path.join(_log_dir, "groupmanage_delbatchgroup.log")
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

_app_logger = logging.getLogger("groupmanage_delbatchgroup")
_app_logger.setLevel(logging.DEBUG)
_app_logger.propagate = False
_app_logger.addHandler(_file_handler)
_app_logger.addHandler(_console_handler)

BASE_URL = get_msg_base_url()
TIMEOUT = 15
SIP_DOMAIN = "zte.com.cn"
MAX_GROUP_IDS_PER_CALL = 100

class BatchGroupTarget:
    """批量解散目标（仅保存群 URI，不拉取群详情）"""
    __slots__ = ("groupid",)
    def __init__(self, groupid: str) -> None:
        self.groupid = groupid


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
    groupids = []
    seen = set()
    for item in raw_groupids.split(','):
        groupid = item.strip()
        if not groupid or groupid in seen:
            continue
        seen.add(groupid)
        groupids.append(groupid)
    return groupids

def build_targets(groupids: List[str]) -> List[BatchGroupTarget]:
    """
    按 URI 列表构造批量目标对象列表。

    Args:
        groupids: 群 URI 列表

    Returns:
        BatchGroupTarget 对象列表
    """
    return [BatchGroupTarget(g) for g in groupids]

def delete_batch_group(userid: str, token: str, groupids: List[str]) -> Dict[str, Any]:
    """
    调用 iCenter 接口批量解散群聊。

    Args:
        userid:   调用者工号
        token:    认证令牌
        groupids: 待删除的群 URI 列表

    Returns:
        解析后的响应字典；若接口未返回 JSON，则返回包装后的结果字典
    """
    caller_sip = build_sip(userid)
    
    url = f"{BASE_URL}/zte-icenter-msg-bff/services/extGroup/public-group/users/{caller_sip}/deleteBatchGroup"
    headers = {
        "Content-Type": "application/json",
        "X-Emp-No": userid,
        "X-Auth-Value": token,
        "Terminal-Type": "Web@Desktop_999_ID1004",
        "X-Origin-ServiceName": "zte-icenter-msg-coclaw",
        "authKey": "msgSkill",
    }
    body = {"groups": [{"guri": groupid} for groupid in groupids]}
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
                "msg": response.text.strip() or "批量删除成功",
            }
        }

def validate_confirmation(groupids: List[str], confirm_groupids: List[str]) -> None:
    """
    校验批量删除群聊时的二次确认参数。

    Args:
        groupids:          待删除的群 URI 列表
        confirm_groupids:  用户确认的群 URI 列表

    Raises:
        ValueError: 二次确认参数缺失或与目标群不一致
    """
    if not confirm_groupids:
        raise ValueError("缺少二次确认参数，请传入 --confirm-groupids")
    if confirm_groupids != groupids:
        raise ValueError("二次确认失败：--confirm-groupids 与 --groupids 不一致，已取消删除")

def extract_group_result_map(resp: Dict[str, Any]) -> Dict[str, Tuple[str, str]]:
    """
    尽量兼容接口可能返回的逐群结果结构。

    Args:
        resp: 接口响应字典

    Returns:
        群 URI 到 (结果码, 消息) 的映射字典
    """
    candidate_lists = []
    bo = resp.get("bo")
    if isinstance(bo, list):
        candidate_lists.append(bo)
    elif isinstance(bo, dict):
        for key in ("groups", "groupList", "results", "result", "items", "data"):
            value = bo.get(key)
            if isinstance(value, list):
                candidate_lists.append(value)
    for items in candidate_lists:
        result_map = {}
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

def print_group_info(results: List[BatchGroupTarget]) -> None:
    """
    打印待解散群 URI 列表（不含群名、成员数等详情）。

    Args:
        results: BatchGroupTarget 对象列表
    """
    _app_logger.info("待解散群数量：%d", len(results))
    print(f"待解散群数量：{len(results)}")
    for index, item in enumerate(results, start=1):
        print(f"{index}. 群URI ：{item.groupid}")

def print_result(results: List[BatchGroupTarget], resp: Dict[str, Any], verbose: bool = False) -> None:
    """
    解析响应并打印批量解散结果（每行：群uri|是否成功）。

    Args:
        results: 待解散群目标列表
        resp:    接口响应字典
        verbose: 是否输出详细错误信息
    """
    code_info = resp.get("code", {})
    overall_code = str(code_info.get("code", "")).strip()
    overall_msg = code_info.get("msg", "")

    def _line(uri: str, ok: bool, msg: str = "") -> None:
        status = "是" if ok else "否"
        if msg and verbose:
            print(f"群uri：{uri}|是否成功：{status}|错误信息：{msg}")
        else:
            print(f"群uri：{uri}|是否成功：{status}")

    if verbose:
        print("\n" + "="*60)
        print("原始响应内容：")
        print(json.dumps(resp, ensure_ascii=False, indent=2))
        print("="*60 + "\n")

    if overall_code and overall_code not in {"200", "204"}:
        print(f"批量解散群聊失败：{overall_msg or '未知错误'}")
        for item in results:
            _line(item.groupid, False)
        return

    result_map = extract_group_result_map(resp)
    success_count = 0
    fail_count = 0

    for item in results:
        item_code, item_msg = result_map.get(item.groupid, ("", ""))
        ok = (not item_code) or (item_code in {"200", "204"})
        if ok:
            success_count += 1
        else:
            fail_count += 1

    _app_logger.info("成功解散群数量：%d", success_count)
    _app_logger.info("失败群数量：%d", fail_count)

    print(f"待解散群数量：{len(results)}")
    print(f"成功解散群数量：{success_count}")
    print(f"失败群数量：{fail_count}")
    print()

    for item in results:
        item_code, item_msg = result_map.get(item.groupid, ("", ""))
        ok = (not item_code) or (item_code in {"200", "204"})
        _line(item.groupid, ok, item_msg if verbose else "")

    if fail_count > 0 and verbose:
        print("\n" + "="*60)
        print("失败详情汇总：")
        for item in results:
            item_code, item_msg = result_map.get(item.groupid, ("", ""))
            if not ((not item_code) or (item_code in {"200", "204"})):
                print(f"  - {item.groupid}: 错误码={item_code or '无'}, 消息={item_msg or '无'}")
        print("="*60)

def main() -> None:
    userid = os.getenv("coclaw_empno") or os.getenv("EMP_NO")
    token = os.getenv("coclaw_token") or os.getenv("AUTH_VALUE")
    if not userid or not token:
        _app_logger.error("请设置环境变量 coclaw_empno 和 coclaw_token（或 EMP_NO 和 AUTH_VALUE）")
        print("错误：请设置环境变量 coclaw_empno 和 coclaw_token（或 EMP_NO 和 AUTH_VALUE）", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(description="批量删除 iCenter 群聊")
    parser.add_argument("--groupids", required=True, help=f"待删除的群 URI 列表，英文逗号分隔（必填，单次最多 {MAX_GROUP_IDS_PER_CALL} 个，去重后）")
    parser.add_argument("--confirm-groupids", default="", help="二次确认用的群 URI 列表，必须与 --groupids 完全一致（正式删除时必填）")
    parser.add_argument("--check-only", action="store_true", help="仅列出待解散群 URI 与数量，不执行删除（不校验群主）")
    parser.add_argument("--verbose", action="store_true", help="输出详细的错误日志和接口原始响应")

    args = parser.parse_args()

    groupids = parse_groupids(args.groupids)
    confirm_groupids = parse_groupids(args.confirm_groupids)
    check_only = args.check_only
    verbose = args.verbose

    if not groupids:
        _app_logger.error("至少需要提供 1 个群 URI")
        print("错误：至少需要提供 1 个群 URI", file=sys.stderr)
        sys.exit(1)

    if len(groupids) > MAX_GROUP_IDS_PER_CALL:
        _app_logger.error("单次最多传入 %d 个群 URI（去重后），当前 %d 个，请分批执行", MAX_GROUP_IDS_PER_CALL, len(groupids))
        print(f"错误：单次最多传入 {MAX_GROUP_IDS_PER_CALL} 个群 URI（去重后），当前 {len(groupids)} 个，请分批执行", file=sys.stderr)
        sys.exit(1)

    results = build_targets(groupids)

    if check_only:
        print_group_info(results)
        return

    try:
        validate_confirmation(groupids, confirm_groupids)
    except ValueError as e:
        _app_logger.error("确认群 URI 与待删除群 URI 不一致：%s", e)
        print(f"错误：{e}", file=sys.stderr)
        sys.exit(1)

    try:
        resp = delete_batch_group(userid, token, groupids)
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

    print_result(results, resp, verbose=verbose)

if __name__ == "__main__":
    main()
