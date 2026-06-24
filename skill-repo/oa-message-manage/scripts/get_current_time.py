#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
获取当前系统时间，包括日期、时间、时区信息。
输出为JSON格式。
"""

import datetime
import json
import sys
import time
from typing import Any, Dict

def get_current_time() -> Dict[str, Any]:
    """
    获取当前系统时间，包括日期、时间、时区信息。

    Returns:
        包含以下字段的字典：
        - date: 日期，格式 YYYY-MM-DD
        - time: 时间，格式 HH:MM:SS
        - datetime: 日期时间，格式 YYYY-MM-DD HH:MM:SS
        - timestamp: Unix时间戳（秒）
        - timezone: 时区信息
        - weekday: 星期几（英文）
        - weekday_number: 星期几（数字，0=Monday, 6=Sunday）
        - day_of_year: 一年中的第几天
        - week_number: 一年中的第几周
        - year: 年份
        - month: 月份
        - day: 日期
        - hour: 小时
        - minute: 分钟
        - second: 秒
        - is_am_pm: AM/PM
        - hour_12: 12小时制小时数
    """
    now = datetime.datetime.now()

    # 获取时区信息
    try:
        import pytz
        # 尝试使用 pytz 获取本地时区；CST 等缩写可能无法识别，需捕获异常回退
        tz_name = time.tzname[0] if time.tzname else 'UTC'
        try:
            local_tz = pytz.timezone(tz_name)
            tz_info = str(local_tz)
        except (pytz.UnknownTimeZoneError, KeyError, AttributeError):
            # CST、EST 等缩写或无效名称时，用系统 UTC 偏移
            utc_offset = time.localtime().tm_gmtoff
            hours = int(utc_offset // 3600)
            minutes = int((utc_offset % 3600) // 60)
            tz_info = f"UTC{hours:+03d}:{minutes:02d}"
    except ImportError:
        # 如果 pytz 不可用，使用简单的时区偏移
        utc_offset = time.localtime().tm_gmtoff
        hours = int(utc_offset // 3600)
        minutes = int((utc_offset % 3600) // 60)
        tz_info = f"UTC{hours:+03d}:{minutes:02d}"

    # 构建时间信息字典
    time_info = {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp": int(time.time()),
        "timezone": tz_info,
        "weekday": now.strftime("%A"),
        "weekday_number": now.weekday(),  # 0=Monday, 6=Sunday
        "day_of_year": now.timetuple().tm_yday,
        "week_number": now.isocalendar()[1],
        "year": now.year,
        "month": now.month,
        "day": now.day,
        "hour": now.hour,
        "minute": now.minute,
        "second": now.second,
        "is_am_pm": "AM" if now.hour < 12 else "PM",
        "hour_12": 12 if now.hour == 0 else (now.hour if now.hour <= 12 else now.hour - 12),
    }

    return time_info


if __name__ == "__main__":
    try:
        result = get_current_time()
        print(json.dumps(result, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
