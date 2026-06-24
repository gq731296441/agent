#!/usr/bin/env python3
"""
获取当前系统时间，包括日期、时间、时区信息。
输出为JSON格式，便于Claude解析。
（自 time-interpreter 技能复制，供本技能内时间解析使用）
"""

import json
import datetime
import time
from typing import Any, Dict

def get_current_time() -> Dict[str, Any]:
    """获取当前时间信息"""
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
