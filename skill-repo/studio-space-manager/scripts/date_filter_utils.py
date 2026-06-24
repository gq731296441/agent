#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""时间范围解析与日期过滤工具。"""

import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, List, Dict, Any


def _beijing_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def _to_iso(dt: datetime) -> str:
    return f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}"


def _parse_single_date_iso(raw_value: str) -> Optional[str]:
    """解析单日日期，返回 YYYY-MM-DD；不支持时返回 None。"""
    value = (raw_value or "").strip()
    if not value:
        return None

    if re.match(r"^\d{4}-\d{1,2}-\d{1,2}$", value):
        y, m, d = value.split("-")
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"

    if re.match(r"^\d{8}$", value):
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}"

    if re.match(r"^\d{1,2}[-/]\d{1,2}$", value):
        year = _beijing_now().year
        month, day = re.split(r"[-/]", value)
        return f"{year:04d}-{int(month):02d}-{int(day):02d}"

    cn_date = re.match(r"^(\d{1,2})月(\d{1,2})[日号]?$", value)
    if cn_date:
        year = _beijing_now().year
        month = int(cn_date.group(1))
        day = int(cn_date.group(2))
        return f"{year:04d}-{month:02d}-{day:02d}"

    return None


def resolve_date_range(date_value: Optional[str]) -> Tuple[str, str]:
    """
    解析时间条件，返回 [start_iso, end_iso]（含首尾）。
    支持：
    - 单日：today/今天、yesterday/昨天、前天、YYYY-MM-DD、YYYYMMDD、M-D、M月D日
    - 时段：本周、上周、本月、今年
    - 区间：start~end 或 start 到 end
    """
    now = _beijing_now()
    today = _to_iso(now)
    value = (date_value or "today").strip()
    lower = value.lower()

    if lower in ("today", "今天", "今日"):
        return today, today
    if lower in ("yesterday", "昨天", "昨日"):
        day = _to_iso(now - timedelta(days=1))
        return day, day
    if lower in ("day_before_yesterday", "前天", "前日"):
        day = _to_iso(now - timedelta(days=2))
        return day, day

    if lower in ("this_week", "this week", "thisweek", "本周", "这周", "这星期"):
        monday = now - timedelta(days=now.weekday())
        return _to_iso(monday), today
    if lower in ("last_week", "last week", "lastweek", "上周", "上星期"):
        this_monday = now - timedelta(days=now.weekday())
        last_monday = this_monday - timedelta(days=7)
        last_sunday = this_monday - timedelta(days=1)
        return _to_iso(last_monday), _to_iso(last_sunday)
    if lower in ("this_month", "this month", "thismonth", "本月"):
        start = f"{now.year:04d}-{now.month:02d}-01"
        return start, today
    if lower in ("this_year", "this year", "thisyear", "今年"):
        start = f"{now.year:04d}-01-01"
        return start, today

    range_parts = re.split(r"\s*~\s*|\s+到\s+", value, maxsplit=1)
    if len(range_parts) == 2:
        start_iso = _parse_single_date_iso(range_parts[0])
        end_iso = _parse_single_date_iso(range_parts[1])
        if start_iso and end_iso:
            if start_iso <= end_iso:
                return start_iso, end_iso
            return end_iso, start_iso

    single = _parse_single_date_iso(value)
    if single:
        return single, single
    return today, today


def filter_items_by_date(
    items: List[Dict[str, Any]], time_field: str, start_iso: str, end_iso: str
) -> List[Dict[str, Any]]:
    """按时间字段过滤列表，字段值支持 ISO 风格时间串。"""
    if not start_iso or not end_iso:
        return items

    left = start_iso[:10]
    right = end_iso[:10]
    if left > right:
        left, right = right, left

    filtered = []
    for item in items:
        raw_time = str(item.get(time_field) or "").strip()
        day = raw_time[:10]
        if day and left <= day <= right:
            filtered.append(item)
    return filtered
