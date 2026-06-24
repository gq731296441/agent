#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
单页统计数据：在指定时间范围内查询页面的阅读次数、评论次数、转发次数、点赞次数。

接口与鉴权与 studio-space-hotpage/scripts/space_hotpage.py 中实现一致（本文件内自包含副本，不 import 该模块）。

用法：
    python3 get_page_stat.py --space-id <spaceId> --page-id <pageId>
    python3 get_page_stat.py --url "https://i.zte.com.cn/#/shared/<spaceId>/wiki/page/<pageId>/view"
    python3 get_page_stat.py --space-id <spaceId> --page-id <pageId> --date-range 本周 --json-output
"""

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from _platform_utils import configure_stdio_utf8
from system_param import build_icenter_api_url

try:
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

# ---------------------------------------------------------------------------
# 与 space_hotpage.py 一致的统计接口（自包含副本）
# ---------------------------------------------------------------------------

VISIT_STATISTICS_URL = (
    build_icenter_api_url("/zte-km-search-core/docs/visitrecord-statistics/_search")
)
OPERATE_STATISTICS_URL = (
    build_icenter_api_url("/zte-km-search-core/docs/operatelog-statistics/_search")
)

SPACE_TENANT_ID = "345"
SPACE_TENANT_INFO = "cId7idgUYBjPYvwB"


def hmac_sha256(message: str, secret: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256)
    return mac.hexdigest().lower()


def get_path_from_url(url: str) -> str:
    return urlparse(url).path


def generate_search_headers(url: str) -> Dict[str, str]:
    timestamp = str(int(time.time() * 1000))
    path = get_path_from_url(url)
    if path.startswith("/zte-km-search-core/docs"):
        api_path = path[len("/zte-km-search-core/docs") :]
    else:
        api_path = path
    message = timestamp + SPACE_TENANT_ID + "/zte-km-search-core/docs" + api_path
    signature = hmac_sha256(message, SPACE_TENANT_INFO)
    return {
        "X-Search-Ts": timestamp,
        "X-Search-TenantId": SPACE_TENANT_ID,
        "X-Search-Signature": signature,
        "Content-Type": "application/json",
    }


def build_date_filter(start_date: str, end_date: str, date_field: str = "view_date") -> str:
    try:
        start = datetime.strptime(start_date, "%Y%m%d")
        end = datetime.strptime(end_date, "%Y%m%d")
        date_list: List[str] = []
        current = start
        while current <= end:
            date_str = current.strftime("%Y%m%d")
            date_list.append(f"{date_field}:{date_str}")
            current += timedelta(days=1)
        if date_list:
            return "(" + " OR ".join(date_list) + ")"
        return ""
    except Exception:
        return ""


def _parse_view_aggregations(data: dict, debug: bool) -> Dict[str, int]:
    view_counts: Dict[str, int] = {}
    if not isinstance(data, dict):
        return view_counts
    aggregations = data.get("aggregations") or data.get("aggregs")
    if isinstance(aggregations, list) and aggregations:
        for agg_item in aggregations:
            if not isinstance(agg_item, dict):
                continue
            buckets = agg_item.get("buckets", [])
            if not isinstance(buckets, list):
                continue
            for bucket in buckets:
                if not isinstance(bucket, dict):
                    continue
                content_id = bucket.get("key")
                sub_aggs = bucket.get("sub_aggs", [])
                view_count = 0
                for sub_agg in sub_aggs:
                    if isinstance(sub_agg, dict) and sub_agg.get("name") == "sum_view_count":
                        view_count = sub_agg.get("value", 0)
                        break
                if view_count == 0:
                    view_count = bucket.get("doc_count", 0)
                    try:
                        view_count = int(view_count) if isinstance(view_count, str) else view_count
                    except (ValueError, TypeError):
                        view_count = 0
                if content_id:
                    view_counts[str(content_id)] = int(view_count)
    elif isinstance(aggregations, dict):
        agg_field = None
        for field_name in [
            "content_id",
            "group_by_content_id",
            "content_id:50000000:cardinality:visitor_no:sum:view_count",
        ]:
            if field_name in aggregations:
                agg_field = aggregations[field_name]
                break
        if agg_field:
            if isinstance(agg_field, dict):
                buckets = agg_field.get("buckets", [])
                if isinstance(buckets, list):
                    for item in buckets:
                        if isinstance(item, dict):
                            content_id = item.get("key") or item.get("content_id")
                            view_count = (
                                item.get("value")
                                or item.get("view_count")
                                or item.get("doc_count")
                                or 0
                            )
                            if content_id:
                                view_counts[str(content_id)] = int(view_count)
            elif isinstance(agg_field, list):
                for item in agg_field:
                    if isinstance(item, dict):
                        content_id = item.get("key") or item.get("content_id")
                        view_count = (
                            item.get("value")
                            or item.get("view_count")
                            or item.get("doc_count")
                            or 0
                        )
                        if content_id:
                            view_counts[str(content_id)] = int(view_count)
    if debug:
        print(f"[DEBUG] 浏览次数解析到 {len(view_counts)} 条", file=sys.stderr)
    return view_counts


def _parse_operate_aggregations(data: dict, debug: bool, label: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    if not isinstance(data, dict):
        return counts
    aggregations = data.get("aggregations") or data.get("aggregs")
    if isinstance(aggregations, list) and aggregations:
        for agg_item in aggregations:
            if not isinstance(agg_item, dict):
                continue
            buckets = agg_item.get("buckets", [])
            if not isinstance(buckets, list):
                continue
            for bucket in buckets:
                if not isinstance(bucket, dict):
                    continue
                content_id = bucket.get("key")
                cnt = bucket.get("doc_count", 0)
                try:
                    cnt = int(cnt) if isinstance(cnt, str) else cnt
                except (ValueError, TypeError):
                    cnt = 0
                if content_id:
                    counts[str(content_id)] = int(cnt)
    elif isinstance(aggregations, dict):
        agg_field = aggregations.get("content_id") or aggregations.get("group_by_content_id")
        if agg_field:
            if isinstance(agg_field, dict):
                buckets = agg_field.get("buckets", [])
                if isinstance(buckets, list):
                    for item in buckets:
                        if isinstance(item, dict):
                            content_id = item.get("key") or item.get("content_id")
                            cnt = item.get("doc_count") or item.get("value") or 0
                            if content_id:
                                counts[str(content_id)] = int(cnt)
            elif isinstance(agg_field, list):
                for item in agg_field:
                    if isinstance(item, dict):
                        content_id = item.get("key") or item.get("content_id")
                        cnt = item.get("doc_count") or item.get("value") or 0
                        if content_id:
                            counts[str(content_id)] = int(cnt)
    if debug:
        print(f"[DEBUG] {label} 解析到 {len(counts)} 条", file=sys.stderr)
    return counts


def fetch_view_statistics_single(
    space_id: str,
    page_id: str,
    start_date: str,
    end_date: str,
    user_id: str,
    debug: bool = False,
) -> Dict[str, int]:
    date_filter = build_date_filter(start_date, end_date, "view_date")
    if not date_filter:
        return {}
    filter_str = f"{date_filter} AND (space_id:{space_id}) AND (content_id:{page_id})"
    payload = {
        "aggreg_fields": "content_id:50000000:cardinality:visitor_no:sum:view_count",
        "all_match": False,
        "count": 1,
        "debug": debug,
        "filter": filter_str,
        "fragment_size": 256,
        "highlight": False,
        "minScore": -1.0,
        "q_mode": "ftr",
        "scroll": False,
        "start_item": 0,
        "user_id": user_id,
    }
    try:
        headers = generate_search_headers(VISIT_STATISTICS_URL)
        if debug:
            print(f"[DEBUG] 浏览接口: {VISIT_STATISTICS_URL}", file=sys.stderr)
            print(f"[DEBUG] filter: {filter_str}", file=sys.stderr)
        resp = requests.post(VISIT_STATISTICS_URL, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return _parse_view_aggregations(data, debug)
    except Exception as e:
        print(f"[ERROR] 获取浏览次数失败: {e}", file=sys.stderr)
        if debug:
            import traceback

            traceback.print_exc()
        return {}


def _fetch_operate_single(
    space_id: str,
    page_id: str,
    start_date: str,
    end_date: str,
    user_id: str,
    operate_filter: str,
    debug: bool,
    label: str,
) -> Dict[str, int]:
    date_filter = build_date_filter(start_date, end_date, "operate_date")
    if not date_filter:
        return {}
    filter_str = (
        f"{operate_filter} AND {date_filter} AND (space_id:{space_id}) AND (content_id:{page_id})"
    )
    payload = {
        "aggreg_fields": "content_id",
        "all_match": False,
        "count": 1,
        "debug": False,
        "filter": filter_str,
        "fragment_size": 256,
        "highlight": False,
        "minScore": -1.0,
        "q_mode": "ftr",
        "scroll": False,
        "start_item": 0,
        "user_id": user_id,
    }
    try:
        headers = generate_search_headers(OPERATE_STATISTICS_URL)
        if debug:
            print(f"[DEBUG] {label}: {OPERATE_STATISTICS_URL}", file=sys.stderr)
            print(f"[DEBUG] filter: {filter_str}", file=sys.stderr)
        resp = requests.post(OPERATE_STATISTICS_URL, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return _parse_operate_aggregations(data, debug, label)
    except Exception as e:
        print(f"[ERROR] {label}失败: {e}", file=sys.stderr)
        if debug:
            import traceback

            traceback.print_exc()
        return {}


def fetch_forward_statistics_single(
    space_id: str, page_id: str, start_date: str, end_date: str, user_id: str, debug: bool = False
) -> Dict[str, int]:
    return _fetch_operate_single(
        space_id,
        page_id,
        start_date,
        end_date,
        user_id,
        "(operate_code:12 OR operate_code:13)",
        debug,
        "转发次数",
    )


def fetch_like_statistics_single(
    space_id: str, page_id: str, start_date: str, end_date: str, user_id: str, debug: bool = False
) -> Dict[str, int]:
    return _fetch_operate_single(
        space_id,
        page_id,
        start_date,
        end_date,
        user_id,
        "(operate_code:32)",
        debug,
        "点赞次数",
    )


def fetch_comment_statistics_single(
    space_id: str, page_id: str, start_date: str, end_date: str, user_id: str, debug: bool = False
) -> Dict[str, int]:
    return _fetch_operate_single(
        space_id,
        page_id,
        start_date,
        end_date,
        user_id,
        "(operate_code:9)",
        debug,
        "评论次数",
    )


# ---------------------------------------------------------------------------
# 日期与 URL（与 space_hotpage 行为对齐的精简副本）
# ---------------------------------------------------------------------------


def get_beijing_date() -> str:
    beijing_tz = timezone(timedelta(hours=8))
    beijing_now = datetime.now(beijing_tz)
    return f"{beijing_now.year}{beijing_now.month:02d}{beijing_now.day:02d}"


def get_beijing_date_iso() -> str:
    beijing_tz = timezone(timedelta(hours=8))
    beijing_now = datetime.now(beijing_tz)
    return f"{beijing_now.year}-{beijing_now.month:02d}-{beijing_now.day:02d}"


def get_beijing_yesterday_iso() -> str:
    beijing_tz = timezone(timedelta(hours=8))
    beijing_now = datetime.now(beijing_tz)
    yesterday = beijing_now - timedelta(days=1)
    return f"{yesterday.year}-{yesterday.month:02d}-{yesterday.day:02d}"


def get_beijing_day_before_yesterday_iso() -> str:
    beijing_tz = timezone(timedelta(hours=8))
    beijing_now = datetime.now(beijing_tz)
    d = beijing_now - timedelta(days=2)
    return f"{d.year}-{d.month:02d}-{d.day:02d}"


def get_beijing_this_week_range() -> Tuple[str, str]:
    beijing_tz = timezone(timedelta(hours=8))
    beijing_now = datetime.now(beijing_tz)
    days_since_monday = beijing_now.weekday()
    this_monday = beijing_now - timedelta(days=days_since_monday)
    start_date = f"{this_monday.year}{this_monday.month:02d}{this_monday.day:02d}"
    end_date = f"{beijing_now.year}{beijing_now.month:02d}{beijing_now.day:02d}"
    return start_date, end_date


def get_beijing_last_week_range() -> Tuple[str, str]:
    beijing_tz = timezone(timedelta(hours=8))
    beijing_now = datetime.now(beijing_tz)
    days_since_monday = beijing_now.weekday()
    last_monday = beijing_now - timedelta(days=days_since_monday + 7)
    last_sunday = last_monday + timedelta(days=6)
    start_date = f"{last_monday.year}{last_monday.month:02d}{last_monday.day:02d}"
    end_date = f"{last_sunday.year}{last_sunday.month:02d}{last_sunday.day:02d}"
    return start_date, end_date


def get_beijing_this_month_range() -> Tuple[str, str]:
    beijing_tz = timezone(timedelta(hours=8))
    beijing_now = datetime.now(beijing_tz)
    this_month_start = beijing_now.replace(day=1)
    start_date = f"{this_month_start.year}{this_month_start.month:02d}{this_month_start.day:02d}"
    end_date = f"{beijing_now.year}{beijing_now.month:02d}{beijing_now.day:02d}"
    return start_date, end_date


def get_beijing_last_month_range() -> Tuple[str, str]:
    beijing_tz = timezone(timedelta(hours=8))
    beijing_now = datetime.now(beijing_tz)
    if beijing_now.month == 1:
        last_month_start = beijing_now.replace(year=beijing_now.year - 1, month=12, day=1)
    else:
        last_month_start = beijing_now.replace(month=beijing_now.month - 1, day=1)
    if beijing_now.month == 1:
        last_month_end = datetime(beijing_now.year - 1, 12, 31, tzinfo=beijing_tz)
    else:
        last_month_end = beijing_now.replace(month=beijing_now.month, day=1) - timedelta(days=1)
    start_date = f"{last_month_start.year}{last_month_start.month:02d}{last_month_start.day:02d}"
    end_date = f"{last_month_end.year}{last_month_end.month:02d}{last_month_end.day:02d}"
    return start_date, end_date


def _parse_single_date(date_str: str) -> str:
    date_str = date_str.strip()
    s = date_str.lower()
    if s in ("today", "今天", "今日"):
        return get_beijing_date_iso()
    if s in ("yesterday", "昨天", "昨日"):
        return get_beijing_yesterday_iso()
    if s in ("day_before_yesterday", "前天", "前日"):
        return get_beijing_day_before_yesterday_iso()
    if re.match(r"^\d{4}-\d{1,2}-\d{1,2}$", date_str):
        parts = date_str.split("-")
        return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    if re.match(r"^\d{8}$", date_str):
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    return get_beijing_date_iso()


def parse_date_range(date_range: Optional[str] = None) -> Tuple[str, str]:
    if not date_range or not date_range.strip():
        today = get_beijing_date()
        return today, today
    date_range = date_range.strip()
    s = date_range.lower()
    if s in ("today", "今天", "今日"):
        today = get_beijing_date()
        return today, today
    if s in ("yesterday", "昨天", "昨日"):
        yesterday_iso = get_beijing_yesterday_iso()
        yesterday = yesterday_iso.replace("-", "")
        return yesterday, yesterday
    if s in ("day_before_yesterday", "前天", "前日"):
        d = get_beijing_day_before_yesterday_iso().replace("-", "")
        return d, d
    if s in ("this_week", "thisweek", "本周", "这周", "这星期"):
        return get_beijing_this_week_range()
    if s in ("last_week", "lastweek", "上周", "上星期"):
        return get_beijing_last_week_range()
    if s in ("this_month", "thismonth", "本月", "这个月"):
        return get_beijing_this_month_range()
    if s in ("last_month", "lastmonth", "上月", "上个月"):
        return get_beijing_last_month_range()
    if "," in date_range:
        parts = date_range.split(",")
        start_iso = _parse_single_date(parts[0].strip())
        end_iso = _parse_single_date(parts[1].strip())
        return start_iso.replace("-", ""), end_iso.replace("-", "")
    if "-" in date_range and len(date_range) > 8:
        parts = date_range.split("-")
        if len(parts) == 2:
            start = parts[0].strip()
            end = parts[1].strip()
            if len(start.split("-")) == 3 or len(end.split("-")) == 3:
                start_iso = _parse_single_date(start)
                end_iso = _parse_single_date(end)
                return start_iso.replace("-", ""), end_iso.replace("-", "")
            start = start.replace("-", "")
            end = end.replace("-", "")
            if len(start) == 8 and len(end) == 8:
                return start, end
    date_iso = _parse_single_date(date_range)
    date_str = date_iso.replace("-", "")
    return date_str, date_str


def parse_space_page_from_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    if not url:
        return None, None
    pattern_shared = r"/shared/([a-f0-9]{32})/wiki/page/([a-f0-9]{32})/view"
    m = re.search(pattern_shared, url, flags=re.IGNORECASE)
    if m:
        return m.group(1), m.group(2)
    pattern_space = r"/space/([a-f0-9]{32})/wiki/page/([a-f0-9]{32})/view"
    m = re.search(pattern_space, url, flags=re.IGNORECASE)
    if m:
        return m.group(1), m.group(2)
    return None, None


def extract_space_id_from_url(url: str) -> Optional[str]:
    if not url or not url.strip():
        return None
    url = url.strip()
    m = re.search(r"/#/shared/([a-f0-9]{32})/wiki/page/", url)
    if m:
        return m.group(1)
    m = re.search(r"/#/space/([a-f0-9]{32})/wiki/page/", url)
    if m:
        return m.group(1)
    if re.match(r"^[a-f0-9]{32}$", url, re.IGNORECASE):
        return url
    return None


def resolve_user_id() -> Optional[str]:
    user_id = os.environ.get("coclaw_empno", "").strip()
    if user_id:
        return user_id
    return None


def get_page_statistics(
    space_id: str,
    page_id: str,
    start_date: str,
    end_date: str,
    user_id: str,
    debug: bool,
) -> Dict[str, Any]:
    view_map = fetch_view_statistics_single(
        space_id, page_id, start_date, end_date, user_id, debug
    )
    forward_map = fetch_forward_statistics_single(
        space_id, page_id, start_date, end_date, user_id, debug
    )
    like_map = fetch_like_statistics_single(
        space_id, page_id, start_date, end_date, user_id, debug
    )
    comment_map = fetch_comment_statistics_single(
        space_id, page_id, start_date, end_date, user_id, debug
    )

    def pick(m: Dict[str, int]) -> int:
        if page_id in m:
            return int(m[page_id])
        for k, v in m.items():
            if k.lower() == page_id.lower():
                return int(v)
        if len(m) == 1:
            return int(next(iter(m.values())))
        return 0

    return {
        "view_count": pick(view_map),
        "comment_count": pick(comment_map),
        "forward_count": pick(forward_map),
        "like_count": pick(like_map),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="查询单页阅读/评论/转发/点赞次数（时间范围内，接口与空间热点统计一致）"
    )
    p.add_argument("--space-id", dest="space_id", default=None, help="空间 ID（与 --page-id 同用）")
    p.add_argument("--page-id", dest="page_id", default=None, help="页面 contentId（与 --space-id 同用）")
    p.add_argument(
        "--url",
        dest="url",
        default=None,
        help="页面链接，可解析 spaceId 与 pageId（与 --space-id/--page-id 二选一）",
    )
    p.add_argument(
        "--date-range",
        dest="date_range",
        default=None,
        help="时间范围，默认今天。支持 today/本周/上月、YYYYMMDD-YYYYMMDD 等（与 space_hotpage 一致）",
    )
    p.add_argument("--json-output", action="store_true", help="仅输出 JSON 到 stdout")
    p.add_argument("--debug", action="store_true", help="调试信息输出到 stderr")
    return p


def main() -> int:
    configure_stdio_utf8()
    args = build_arg_parser().parse_args()
    space_id: Optional[str] = args.space_id
    page_id: Optional[str] = args.page_id
    if args.url:
        su, pu = parse_space_page_from_url(args.url)
        if su and pu:
            space_id, page_id = su, pu
        elif not space_id:
            extracted = extract_space_id_from_url(args.url)
            if extracted:
                space_id = extracted
    if not space_id or not page_id:
        msg = "请提供 --url（完整页面链接），或同时提供 --space-id 与 --page-id"
        if args.json_output:
            print(json.dumps({"status": "error", "message": msg}, ensure_ascii=False))
        else:
            # 避免 Windows 默认非 UTF-8 控制台对 emoji 的编码问题（configure_stdio_utf8 后通常无碍，仍用纯 ASCII 前缀）
            print(f"[错误] {msg}", file=sys.stderr)
        return 1
    start_date, end_date = parse_date_range(args.date_range)
    user_id = resolve_user_id()
    if not user_id:
        msg = "缺少用户工号：请先设置环境变量 coclaw_empno"
        if args.json_output:
            print(json.dumps({"status": "error", "message": msg}, ensure_ascii=False))
        else:
            print(f"[错误] {msg}", file=sys.stderr)
        return 1
    stats = get_page_statistics(space_id, page_id, start_date, end_date, user_id, args.debug)
    page_url = f"https://i.zte.com.cn/#/shared/{space_id}/wiki/page/{page_id}/view"
    out = {
        "status": "ok",
        "space_id": space_id,
        "page_id": page_id,
        "page_url": page_url,
        "date_range": {"start": start_date, "end": end_date},
        "statistics": stats,
    }
    if args.json_output:
        print(json.dumps(out, ensure_ascii=False))
        return 0
    print(f"页面: {page_url}")
    print(f"统计区间: {start_date} ~ {end_date}（北京时间按日）")
    print(f"阅读次数: {stats['view_count']}")
    print(f"评论次数: {stats['comment_count']}")
    print(f"转发次数: {stats['forward_count']}")
    print(f"点赞次数: {stats['like_count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
