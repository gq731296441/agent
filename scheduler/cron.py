"""定时任务 cron 表达式计算（全模块统一东八区语义）。

约定：
- 计算：now 取东八区 aware 时间，croniter 用东八区解释分/时，返回 naive 东八区。
- 存库：naive 结果补 _CST 时区后 astimezone(UTC) 得到 aware UTC，写入 timestamptz 列。
- 展示：DB 返回 aware UTC 经 format_cst 输出东八区字符串。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from croniter import croniter

_CST = timezone(timedelta(hours=8))
UTC = timezone.utc


def now_cst() -> datetime:
    """当前东八区 aware 时间。"""
    return datetime.now(_CST)


def validate_cron_expr(expr: str) -> bool:
    """校验 cron 表达式是否合法（croniter 能否构造）。"""
    if not expr or not str(expr).strip():
        return False
    try:
        croniter(str(expr).strip())
        return True
    except Exception:
        return False


def compute_next_run_at(cron_expr: str, after: datetime | None = None) -> datetime | None:
    """计算 cron 表达式在 after 之后的首次执行时间。

    after 可为 naive（视为东八区）或 aware；返回 aware UTC（写库用）。
    解析失败返回 None。
    """
    if after is None:
        after = now_cst()
    if after.tzinfo is None:
        after = after.replace(tzinfo=_CST)
    after_cst = after.astimezone(_CST)
    try:
        nxt = croniter(str(cron_expr).strip(), after_cst).get_next(datetime)
    except Exception:
        return None
    # nxt 为 naive 东八区；补时区后转 aware UTC
    return nxt.replace(tzinfo=_CST).astimezone(UTC)


def format_cst(dt) -> str:
    """展示层：将 naive/aware 时间统一转东八区字符串 %Y-%m-%d %H:%M:%S。"""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(_CST).strftime("%Y-%m-%d %H:%M:%S")
