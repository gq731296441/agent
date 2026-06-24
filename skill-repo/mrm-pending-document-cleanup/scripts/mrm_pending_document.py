#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MRM 在途单据清理 — 业务接口命令行工具
与 CMTool「MRM 在途单据清理」业务能力及既有接口契约对齐。

依赖: pip install requests
认证: 见 SKILL.md 环境变量说明
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

# os.environ["coclaw_empno"] = "10344649"

try:
    import requests
except ImportError:
    print("Error: 需要安装 requests: pip install requests", file=sys.stderr)
    sys.exit(1)

# —— 默认 endpoint（可通过环境变量覆盖）——
DEFAULT_API_BASE = os.environ.get("MRM_API_BASE", "https://aip.zte.com.cn/cmtool/api").rstrip("/")
URL_QUERY_OPTIONS = f"{DEFAULT_API_BASE}/v1/mrm_pending_doc/queryOptions"
URL_ADMIN_CHECK = f"{DEFAULT_API_BASE}/v1/mrm_pending_doc/admin/check"
URL_QUERY_BY_USER = f"{DEFAULT_API_BASE}/v1/mrm_pending_doc/queryByUser"
URL_CREATE_CANCEL_STATUS = f"{DEFAULT_API_BASE}/v1/mrm_pending_doc/createCancelStatus"

TIMEOUT = 120
USE_MOCK_OPTIONS = True  # 本地调试可改为 True；正式上线前请改回 False 或删除 mock 代码

MOCK_QUERY_OPTIONS_RESPONSE: Dict[str, Any] = {
    "code": 0,
    "msg": "成功",
    "data": {
        "secondLevelOrgNames": [
            "System Product_Wired Product Operation Division",
            "系统产品_产研平台与直属",
            "系统产品_数字能源产品经营部",
            "系统产品_无线及算力产品经营部",
            "系统产品_有线产品经营部",
            "终端事业部",
            "运营管理",
        ],
        "thirdLevelOrgNames": [
            "Wired Product Planning Dept.",
            "中心研究院",
            "制造工程研究院",
            "技术规划部",
            "缺省",
            "视频产品部",
            "数字能源研究院",
            "无线及算力研究院",
            "无线及算力网络研究院",
            "无线及算力规划部",
            "质量部",
            "有线研究院",
            "有线规划部",
            "市场中心",
        ],
        "inTransitDurationSegments": [
            "<1个月",
            "1~3个月",
            "3~6个月",
            "6~12个月",
            ">=12个月",
        ],
        "orgTree": [
            {
                "secondLevelOrgName": "System Product_Wired Product Operation Division",
                "thirdLevelOrgNames": ["Wired Product Planning Dept."],
            },
            {
                "secondLevelOrgName": "系统产品_产研平台与直属",
                "thirdLevelOrgNames": ["中心研究院", "制造工程研究院", "技术规划部", "缺省", "视频产品部"],
            },
            {
                "secondLevelOrgName": "系统产品_数字能源产品经营部",
                "thirdLevelOrgNames": ["数字能源研究院"],
            },
            {
                "secondLevelOrgName": "系统产品_无线及算力产品经营部",
                "thirdLevelOrgNames": ["无线及算力研究院", "无线及算力网络研究院", "无线及算力规划部", "质量部"],
            },
            {
                "secondLevelOrgName": "系统产品_有线产品经营部",
                "thirdLevelOrgNames": ["有线研究院", "有线规划部"],
            },
            {"secondLevelOrgName": "终端事业部", "thirdLevelOrgNames": ["市场中心"]},
            {"secondLevelOrgName": "运营管理", "thirdLevelOrgNames": ["质量部"]},
        ],
    },
    "status": True,
}


def _session() -> requests.Session:
    s = requests.Session()
    return s


def backend_headers() -> Dict[str, str]:
    h: Dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
    un = os.environ.get("coclaw_empno")
    ut = os.environ.get("coclaw_token")
    if un:
        h["userNumber"] = un
    if ut:
        h["userToken"] = ut
    return h


def process_headers(fallback_emp_no: Optional[str] = None) -> Dict[str, str]:
    emp = _normalize_emp_no(fallback_emp_no or os.environ.get("coclaw_empno"))
    auth = os.environ.get("coclaw_token")
    if not emp or not auth:
        raise SystemExit("处理单据需设置 coclaw_empno 与 coclaw_token")
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Emp-No": emp,
        "X-Auth-Value": auth,
    }


def track_headers(fallback_emp_no: Optional[str] = None) -> Dict[str, str]:
    emp = _normalize_emp_no(fallback_emp_no or os.environ.get("coclaw_empno"))
    auth = os.environ.get("coclaw_token")
    if not emp:
        raise SystemExit("跟催接口需携带 X-Emp-No，请设置 coclaw_empno 或在 --operator 中包含工号")
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Emp-No": emp,
    }
    if auth:
        h["X-Auth-Value"] = auth
    return h


def query_options_headers() -> Dict[str, str]:
    emp = _normalize_emp_no(os.environ.get("coclaw_empno"))
    if not emp:
        raise SystemExit("queryOptions 需设置 coclaw_empno 用于请求头 X-Emp-No")
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Emp-No": emp,
    }


def fetch_query_options(sess: requests.Session, use_mock: bool = False) -> Dict[str, Any]:
    if use_mock:
        data = MOCK_QUERY_OPTIONS_RESPONSE
    else:
        r = sess.get(URL_QUERY_OPTIONS, headers=query_options_headers(), timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    if not data.get("status"):
        raise SystemExit(f"queryOptions 调用失败: {data}")
    payload = data.get("data") or {}
    return {
        "secondLevelOrgNames": payload.get("secondLevelOrgNames") or [],
        "thirdLevelOrgNames": payload.get("thirdLevelOrgNames") or [],
        "inTransitDurationSegments": payload.get("inTransitDurationSegments") or [],
        "orgTree": payload.get("orgTree") or [],
        "raw": data,
    }


def _split_csv(s: Optional[str]) -> List[str]:
    if not s:
        return []
    return [x.strip() for x in s.split(",") if x.strip()]


def _strip_all_spaces(s: str) -> str:
    return "".join(s.split())


def _extract_user_id(user_info: str) -> Optional[str]:
    if not user_info:
        return None
    m = re.search(r"(\d{5,})$", user_info.strip())
    if m:
        return m.group(1)
    return None


def _normalize_emp_no(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    v = str(raw).strip()
    extracted = _extract_user_id(v)
    return extracted or v


def _check_admin(sess: requests.Session, user_id: str) -> Dict[str, Any]:
    if not user_id:
        raise SystemExit("接口人权限校验需要 userId（工号）")
    r = sess.get(URL_ADMIN_CHECK, headers=backend_headers(), params={"userId": user_id}, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if not data.get("status"):
        raise SystemExit(f"接口人权限校验失败: {data}")
    return data


def _ensure_admin(sess: requests.Session, user_id: str) -> None:
    data = _check_admin(sess, user_id)
    if not (data.get("data") or {}).get("isAdmin"):
        raise SystemExit(f"当前用户非接口人，不允许执行该操作。userId={user_id}")


def _validate_query_inputs(args: argparse.Namespace, options: Dict[str, Any]) -> None:
    second_values = _split_csv(args.second)
    third_values = _split_csv(args.third)
    segment_values_raw = _split_csv(args.segments)

    valid_second = set(options.get("secondLevelOrgNames") or [])
    valid_third = set(options.get("thirdLevelOrgNames") or [])
    valid_segment_list = options.get("inTransitDurationSegments") or []
    # 分段参数强制去掉所有空格后再做标准值匹配，最终写回接口标准值
    valid_segment_map = {_strip_all_spaces(item): item for item in valid_segment_list}
    segment_values: List[str] = []
    invalid_segment: List[str] = []
    for item in segment_values_raw:
        normalized = _strip_all_spaces(item)
        if normalized in valid_segment_map:
            segment_values.append(valid_segment_map[normalized])
        else:
            invalid_segment.append(item)
    if segment_values:
        args.segments = ",".join(segment_values)

    invalid_second = [x for x in second_values if x not in valid_second]
    invalid_third = [x for x in third_values if x not in valid_third]

    if not (invalid_second or invalid_third or invalid_segment):
        return

    msg_lines = ["查询参数校验失败：输入值不在 queryOptions 返回范围内。"]
    if invalid_second:
        msg_lines.append(f"- 无效二层单位: {', '.join(invalid_second)}")
    if invalid_third:
        msg_lines.append(f"- 无效三层单位: {', '.join(invalid_third)}")
    if invalid_segment:
        msg_lines.append(f"- 无效在途时长分段: {', '.join(invalid_segment)}")
    msg_lines.append("请先执行: python scripts/mrm_pending_document.py options")
    raise SystemExit("\n".join(msg_lines))


def _normalize_segment_to_standard(segment: Optional[str], options: Dict[str, Any]) -> Optional[str]:
    if not segment:
        return None
    norm = _strip_all_spaces(segment)
    valid_segment_list = options.get("inTransitDurationSegments") or []
    valid_segment_map = {_strip_all_spaces(item): item for item in valid_segment_list}
    if norm not in valid_segment_map:
        raise SystemExit(f"无效在途时长分段: {segment}，请使用 queryOptions 返回的标准值")
    return valid_segment_map[norm]


def cmd_units_second(args: argparse.Namespace) -> None:
    sess = _session()
    opts = fetch_query_options(sess, use_mock=USE_MOCK_OPTIONS)
    names = opts["secondLevelOrgNames"]
    print(json.dumps({"second_level_units": names}, ensure_ascii=False, indent=2))


def cmd_units_third(args: argparse.Namespace) -> None:
    sess = _session()
    opts = fetch_query_options(sess, use_mock=USE_MOCK_OPTIONS)
    tree = opts["orgTree"]
    names: List[str] = []
    for node in tree:
        if node.get("secondLevelOrgName") == args.second:
            names = node.get("thirdLevelOrgNames") or []
            break
    print(json.dumps({"second": args.second, "third_level_units": names}, ensure_ascii=False, indent=2))


def cmd_segments(args: argparse.Namespace) -> None:
    sess = _session()
    opts = fetch_query_options(sess, use_mock=USE_MOCK_OPTIONS)
    names = opts["inTransitDurationSegments"]
    print(json.dumps({"in_transit_duration_segments": names}, ensure_ascii=False, indent=2))


def cmd_options(args: argparse.Namespace) -> None:
    sess = _session()
    opts = fetch_query_options(sess, use_mock=USE_MOCK_OPTIONS)
    if args.raw:
        print(json.dumps(opts["raw"], ensure_ascii=False, indent=2))
        return
    print(
        json.dumps(
            {
                "ok": True,
                "secondLevelOrgNames": opts["secondLevelOrgNames"],
                "thirdLevelOrgNames": opts["thirdLevelOrgNames"],
                "inTransitDurationSegments": opts["inTransitDurationSegments"],
                "orgTree": opts["orgTree"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_meta(args: argparse.Namespace) -> None:
    sess = _session()
    opts = fetch_query_options(sess, use_mock=USE_MOCK_OPTIONS)
    second = opts["secondLevelOrgNames"]
    third = opts["thirdLevelOrgNames"]
    segments = opts["inTransitDurationSegments"]
    payload = {
        "ok": True,
        "second_level_count": len(second),
        "third_level_count": len(third),
        "second_level_units": second,
        "third_level_units": third,
        "in_transit_duration_segments": segments,
        "hint": "筛选项已统一来自 queryOptions 接口",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _parse_cancel(s: Optional[str]) -> Any:
    if s is None:
        return None
    sl = s.strip().lower()
    if sl in ("null", "none", "空"):
        return "null"
    if sl in ("true", "1", "yes", "是"):
        return True
    if sl in ("false", "0", "no", "否"):
        return False
    raise SystemExit(f"无法解析布尔筛选: {s}，请用 true/false/null")


def cmd_query(args: argparse.Namespace) -> None:
    sess = _session()
    options = fetch_query_options(sess, use_mock=USE_MOCK_OPTIONS)
    _validate_query_inputs(args, options)
    params: Dict[str, Any] = {}
    if args.second:
        params["secondLevelOrgName"] = args.second
    if args.third:
        params["thirdLevelOrgName"] = args.third
    if args.segments:
        params["inTransitDurationSegment"] = args.segments
    ac = _parse_cancel(args.author_cancel) if args.author_cancel is not None else None
    hc = _parse_cancel(args.handler_cancel) if args.handler_cancel is not None else None
    if ac is not None:
        params["authorIsCancel"] = ac
    if hc is not None:
        params["currentHandlerIsCancel"] = hc
    if args.user:
        params["user"] = args.user

    url = f"{DEFAULT_API_BASE}/v1/mrm_pending_doc/query"
    r = sess.get(url, headers=backend_headers(), params=params, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if args.raw:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    if not data.get("status"):
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    rows = data.get("data") or []
    print(f"共 {len(rows)} 条")
    for i, row in enumerate(rows[: args.limit]):
        print(
            f"[{i+1}] {row.get('demandNumber')} | 制单人:{row.get('author')} | 处理人:{row.get('currentHandler')} | "
            f"分段:{row.get('inTransitDurationSegment')} | 二层:{row.get('secondLevelOrgName')} | 三层:{row.get('thirdLevelOrgName')}"
        )
    if len(rows) > args.limit:
        print(f"... 仅展示前 {args.limit} 条，使用 --raw 或增大 --limit")


def cmd_query_by_ids(args: argparse.Namespace) -> None:
    sess = _session()
    ids = [x.strip() for x in args.ids.split(",") if x.strip()]
    if not ids:
        raise SystemExit("请提供 --ids 单号列表，逗号分隔")
    url = f"{DEFAULT_API_BASE}/v1/mrm_pending_doc/queryByIds"
    r = sess.post(url, headers=backend_headers(), json=ids, timeout=TIMEOUT)
    r.raise_for_status()
    print(json.dumps(r.json(), ensure_ascii=False, indent=2))


def cmd_admin_check(args: argparse.Namespace) -> None:
    sess = _session()
    user_id = args.user_id or _extract_user_id(args.user_info or "")
    if not user_id:
        raise SystemExit("请通过 --user-id 或 --user-info(末尾含工号) 提供用户工号")
    data = _check_admin(sess, user_id)
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_query_by_user(args: argparse.Namespace) -> None:
    sess = _session()
    params: Dict[str, Any] = {"userInfo": args.user_info}
    if args.segment:
        options = fetch_query_options(sess, use_mock=USE_MOCK_OPTIONS)
        params["inTransitDurationSegment"] = _normalize_segment_to_standard(args.segment, options)
    r = sess.get(URL_QUERY_BY_USER, headers=backend_headers(), params=params, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if args.raw:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    rows = data.get("data") or []
    print(f"共 {len(rows)} 条")
    for i, row in enumerate(rows[: args.limit]):
        print(
            f"[{i+1}] {row.get('demandNumber')} | 制单人:{row.get('author')} | 处理人:{row.get('currentHandler')} | "
            f"分段:{row.get('inTransitDurationSegment')} | 制单人取消:{row.get('authorIsCancel')} | 处理人取消:{row.get('currentHandlerIsCancel')}"
        )
    if len(rows) > args.limit:
        print(f"... 仅展示前 {args.limit} 条，使用 --raw 或增大 --limit")


def _cancel_status_headers() -> Dict[str, str]:
    emp = os.environ.get("coclaw_empno")
    auth = os.environ.get("coclaw_token")
    if not emp:
        raise SystemExit("createCancelStatus 需设置 coclaw_empno")
    h = {"Content-Type": "application/json", "Accept": "application/json", "X-Emp-No": emp}
    if auth:
        h["X-Auth-Value"] = auth
    return h


def cmd_confirm_cancel(args: argparse.Namespace) -> None:
    sess = _session()
    if args.file:
        raw = args.file.read()
        body = json.loads(raw)
    else:
        if not args.demand:
            raise SystemExit("单条确认取消需传 --demand")
        if args.author is None and args.handler is None:
            raise SystemExit("请至少设置 --author 或 --handler")
        item: Dict[str, Any] = {"demandNumber": args.demand}
        if args.author is not None:
            item["authorIsCancel"] = args.author
        if args.handler is not None:
            item["currentHandlerIsCancel"] = args.handler
        body = [item]
    r = sess.post(URL_CREATE_CANCEL_STATUS, headers=_cancel_status_headers(), json=body, timeout=TIMEOUT)
    r.raise_for_status()
    print(json.dumps(r.json(), ensure_ascii=False, indent=2))


def cmd_confirm_cancel_by_segment(args: argparse.Namespace) -> None:
    sess = _session()
    if args.author is None and args.handler is None:
        raise SystemExit("请至少设置 --author 或 --handler")
    options = fetch_query_options(sess, use_mock=USE_MOCK_OPTIONS)
    std_segment = _normalize_segment_to_standard(args.segment, options)
    q = sess.get(
        URL_QUERY_BY_USER,
        headers=backend_headers(),
        params={"userInfo": args.user_info, "inTransitDurationSegment": std_segment},
        timeout=TIMEOUT,
    )
    q.raise_for_status()
    data = q.json()
    rows = data.get("data") or []
    if not rows:
        print(json.dumps({"status": True, "msg": "无符合条件单据", "data": []}, ensure_ascii=False, indent=2))
        return
    body: List[Dict[str, Any]] = []
    for row in rows:
        item: Dict[str, Any] = {"demandNumber": row.get("demandNumber")}
        if args.author is not None:
            item["authorIsCancel"] = args.author
        if args.handler is not None:
            item["currentHandlerIsCancel"] = args.handler
        body.append(item)
    r = sess.post(URL_CREATE_CANCEL_STATUS, headers=_cancel_status_headers(), json=body, timeout=TIMEOUT)
    r.raise_for_status()
    print(json.dumps(r.json(), ensure_ascii=False, indent=2))


def cmd_track(args: argparse.Namespace) -> None:
    sess = _session()
    operator = args.operator or os.environ.get("MRM_OPERATOR")
    if not operator:
        raise SystemExit("请设置 --operator 或环境变量 MRM_OPERATOR（对应跟催接口请求体中的 user 字段）")
    admin_user_id = args.user_id or _extract_user_id(operator)
    if not admin_user_id:
        raise SystemExit("跟催前需获得接口人工号，请传 --user-id 或确保 --operator 末尾包含工号")
    _ensure_admin(sess, admin_user_id)
    ids = [x.strip() for x in args.ids.split(",") if x.strip()]
    body = {"ids": ids, "user": operator, "type": str(args.type)}
    url = f"{DEFAULT_API_BASE}/v1/mrm_pending_doc/track"
    r = sess.post(url, headers=track_headers(admin_user_id), json=body, timeout=TIMEOUT)
    r.raise_for_status()
    print(json.dumps(r.json(), ensure_ascii=False, indent=2))


def cmd_process(args: argparse.Namespace) -> None:
    sess = _session()
    operator = args.operator or os.environ.get("MRM_OPERATOR")
    if not operator:
        raise SystemExit("请设置 --operator 或环境变量 MRM_OPERATOR")
    admin_user_id = args.user_id or _extract_user_id(operator)
    if not admin_user_id:
        raise SystemExit("处理前需获得接口人工号，请传 --user-id 或确保 --operator 末尾包含工号")
    _ensure_admin(sess, admin_user_id)
    ids = [x.strip() for x in args.ids.split(",") if x.strip()]
    body = {"demandNumbers": ids, "user": operator}
    url = f"{DEFAULT_API_BASE}/v1/mrm_pending_doc/process_doc_pending"
    r = sess.post(url, headers={**process_headers(admin_user_id)}, json=body, timeout=TIMEOUT)
    r.raise_for_status()
    print(json.dumps(r.json(), ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="MRM 在途单据清理 API CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    pmeta = sub.add_parser("meta", help="输出 queryOptions 摘要信息（单位/分段）")
    pmeta.set_defaults(func=cmd_meta)
    po = sub.add_parser("options", help="通过 queryOptions 获取全部筛选项")
    po.add_argument("--raw", action="store_true", help="打印 queryOptions 完整返回")
    po.set_defaults(func=cmd_options)

    p2 = sub.add_parser("units-second", help="列出二层单位")
    p2.set_defaults(func=cmd_units_second)

    p3 = sub.add_parser("units-third", help="按二层单位列出三层单位")
    p3.add_argument("--second", required=True, help="二层单位名称")
    p3.set_defaults(func=cmd_units_third)

    ps = sub.add_parser("segments", help="列出在途时长分段")
    ps.set_defaults(func=cmd_segments)

    pq = sub.add_parser("query", help="条件查询在途单据（查询前自动校验筛选项）")
    pq.add_argument("--second", help="二层单位，多个用英文逗号")
    pq.add_argument("--third", help="三层单位，多个用英文逗号")
    pq.add_argument("--segments", help="在途时长分段名称，多个逗号分隔")
    pq.add_argument("--author-cancel", dest="author_cancel", help="制单人是否取消: true/false/null")
    pq.add_argument("--handler-cancel", dest="handler_cancel", help="当前处理人是否取消: true/false/null")
    pq.add_argument("--user", help="按制单人或处理人精确匹配（equals），格式需与DB一致，建议用姓名+工号（如 张三10001234）")
    pq.add_argument("--limit", type=int, default=50, help="非 raw 时最多打印条数")
    pq.add_argument("--raw", action="store_true", help="打印完整 JSON")
    pq.set_defaults(func=cmd_query)

    pb = sub.add_parser("query-by-ids", help="按需求明细单号列表查询")
    pb.add_argument("--ids", required=True, help="逗号分隔单号")
    pb.set_defaults(func=cmd_query_by_ids)

    pac = sub.add_parser("admin-check", help="判断用户是否接口人（权限同管理员）")
    pac.add_argument("--user-id", help="用户工号（纯数字）")
    pac.add_argument("--user-info", help="用户信息（如 张三10001234）")
    pac.set_defaults(func=cmd_admin_check)

    pqu = sub.add_parser("query-by-user", help="按用户查询在途单据（制单人/处理人）")
    pqu.add_argument("--user-info", required=True, help="用户信息，推荐 姓名+工号 格式，支持纯姓名/纯工号模糊匹配，如 张三10001234")
    pqu.add_argument("--segment", help="在途时长分段（会去空格并匹配标准值）")
    pqu.add_argument("--limit", type=int, default=50, help="非 raw 时最多打印条数")
    pqu.add_argument("--raw", action="store_true", help="打印完整 JSON")
    pqu.set_defaults(func=cmd_query_by_user)

    pcs = sub.add_parser("confirm-cancel", help="确认取消（单条或文件批量）")
    pcs.add_argument("--demand", help="单据号；单条更新时必填")
    pcs.add_argument("--author", type=lambda x: x.lower() == "true", help="制单人是否取消: true/false")
    pcs.add_argument("--handler", type=lambda x: x.lower() == "true", help="处理人是否取消: true/false")
    pcs.add_argument("--file", type=argparse.FileType("r", encoding="utf-8"), help="JSON 文件（数组）")
    pcs.set_defaults(func=cmd_confirm_cancel)

    pcss = sub.add_parser("confirm-cancel-by-segment", help="按用户+分段批量确认取消")
    pcss.add_argument("--user-info", required=True, help="用户信息，如 张三10001234")
    pcss.add_argument("--segment", required=True, help="在途时长分段（会去空格并匹配标准值）")
    pcss.add_argument("--author", type=lambda x: x.lower() == "true", help="制单人是否取消: true/false")
    pcss.add_argument("--handler", type=lambda x: x.lower() == "true", help="处理人是否取消: true/false")
    pcss.set_defaults(func=cmd_confirm_cancel_by_segment)

    pt = sub.add_parser("track", help="邮件跟催 type=1制单人 2处理人 3两者")
    pt.add_argument("--type", choices=("1", "2", "3"), required=True)
    pt.add_argument("--ids", required=True, help="逗号分隔需求明细单号")
    pt.add_argument("--operator", help="操作者展示字符串，默认 MRM_OPERATOR")
    pt.add_argument("--user-id", help="接口人工号，用于跟催前 admin-check")
    pt.set_defaults(func=cmd_track)

    pp = sub.add_parser("process", help="处理单据（双方均已确认取消）")
    pp.add_argument("--ids", required=True, help="逗号分隔需求明细单号")
    pp.add_argument("--operator", help="默认 MRM_OPERATOR")
    pp.add_argument("--user-id", help="接口人工号，用于处理前 admin-check")
    pp.set_defaults(func=cmd_process)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
