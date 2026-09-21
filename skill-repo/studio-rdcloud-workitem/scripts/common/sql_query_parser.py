#!/usr/bin/env python
"""
标准 SQL 模式解析：select 字段 from 表名 where 条件
字段与表名通过接口解析；无法唯一确认时抛出错误（由调用方终止并提示用户确认）。
"""

import re
import json
import logging
import calendar
import importlib.util
from pathlib import Path
from datetime import datetime, timedelta

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
USER_SEARCH_MODULE = SCRIPT_DIR / "user_search.py"
SKILL_DIR = SCRIPT_DIR.parent.parent
CONFIG_FILE = SKILL_DIR / "assets" / "config.json"

from .log_helper import get_log_config, truncate_for_log, get_logger
from .rdcloud_api import fetch_fields_v2_with_usage_distinct
from .field_resolver import fetch_fields, resolve_field_key, resolve_field_key_for_select_list, resolve_field_key_for_where


DEFAULT_SELECT_KEYS = [
    "System_Id", "System_Title", "System_State",
    "System_WorkItemType", "System_ChangedDate"
]

# 列表类查询：未指定 limit 时接口 pageSize 默认；指定 limit 时与脚本层共同遵守的上限（再大也截断到此值）
QUERY_LIMIT_DEFAULT = 20
QUERY_LIMIT_MAX = 10000

logger = get_logger(__name__)

_DOT_NAME_STRING_KEYS_CACHE = None


def _get_dot_name_string_keys():
    """
    读取 string 字段允许追加 .name 的白名单（配置项：dot_name_string_keys）。
    未配置或读取失败时返回空集合（即 string 默认不加 .name）。
    """
    global _DOT_NAME_STRING_KEYS_CACHE
    if _DOT_NAME_STRING_KEYS_CACHE is not None:
        return _DOT_NAME_STRING_KEYS_CACHE
    try:
        cfg_path = SCRIPT_DIR / "field_resolver_config.json"
        if not cfg_path.exists():
            _DOT_NAME_STRING_KEYS_CACHE = frozenset()
            return _DOT_NAME_STRING_KEYS_CACHE
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        raw = cfg.get("dot_name_string_keys") or []
        if not isinstance(raw, list):
            _DOT_NAME_STRING_KEYS_CACHE = frozenset()
            return _DOT_NAME_STRING_KEYS_CACHE
        _DOT_NAME_STRING_KEYS_CACHE = frozenset(
            str(x).strip() for x in raw if str(x).strip()
        )
        return _DOT_NAME_STRING_KEYS_CACHE
    except Exception as e:
        logger.warning("读取 dot_name_string_keys 配置失败，string 字段将不追加 .name: %s", e)
        _DOT_NAME_STRING_KEYS_CACHE = frozenset()
        return _DOT_NAME_STRING_KEYS_CACHE


# 不再使用了
# 不再使用了
# 不再使用了
def parse_sql(sql_str):
    """
    解析 SQL 为 (select_list, from_table, where_str, order_by_str)。
    关键字大小写不敏感：SELECT/FROM/WHERE/ORDER BY/AND/OR、asc/desc、between/like/in、不等于可用 != 或 <> 等均按不区分大小写匹配。
    不完整 SQL 不抛异常：select 后无列 → select_list=None；from 后无表 → from_table 为空；无 from → from_table 为空；
    where 后无条件或仅有 where 关键字 → where_str 为空。仅当输入为 None 或全空白时抛出 ValueError。
    """
    s = (sql_str or "").strip()
    if not s:
        raise ValueError("SQL 不能为空")
    s_lower = s.lower()
    if "select" not in s_lower:
        # 无 select 视为残缺，仍返回结构，表名与条件为空
        return None, "", "", ""
    # 允许无 from：仅有 select 列 也正常解析，表名与条件为空。有 from 指出现「 from」或「 from 」或「 from」结尾
    if not re.search(r"\s+from\b", s_lower):
        select_part = re.sub(r"^\s*select\s+", "", s, count=1, flags=re.IGNORECASE).strip()
        if not select_part or select_part == "*" or select_part.lower() == "select":
            select_list = None
        else:
            select_list = [x.strip() for x in re.split(r"\s*,\s*", select_part) if x.strip()]
            if not select_list:
                select_list = None
        return select_list, "", "", ""
    parts = re.split(r"\s+from\s*", s, maxsplit=1, flags=re.IGNORECASE)
    select_part = parts[0].strip()
    rest = (parts[1] if len(parts) > 1 else "").strip()
    # select 后无列（空、*、或仅 select）：记为 None，下游用默认列
    select_part = re.sub(r"^\s*select\s+", "", select_part, flags=re.IGNORECASE).strip()
    if not select_part or select_part == "*" or select_part.lower() == "select":
        select_list = None
    else:
        select_list = [x.strip() for x in re.split(r"\s*,\s*", select_part) if x.strip()]
        if not select_list:
            select_list = None
    # from 后：表名紧跟 from，到第一个「 where 」「 order by 」「 limit 」或结尾为止；其后为 where/order by/limit，不再算作表名
    where_str = ""
    order_by_str = ""
    rest_lower = rest.lower()
    # 表名结束位置：第一个 空格+where、空格+order by、空格+limit 的起始位置（不含则表名到 rest 末尾）
    end_table = len(rest)
    for pattern in (r"\s+where\s+", r"\s+where\s*$", r"\s+order\s+by\s+", r"\s+limit\s+"):
        mo = re.search(pattern, rest_lower, re.IGNORECASE)
        if mo and mo.start() < end_table:
            end_table = mo.start()
    from_table = rest[:end_table].strip()
    remainder = rest[end_table:].strip()
    if remainder:
        # remainder 为 "where 条件..." 或 "order by ..." 或 "limit ..."；给 _parse_where_condition 的 where_str 不含 "where " 前缀
        if re.match(r"^where\s+", remainder, re.IGNORECASE):
            where_str = re.sub(r"^where\s+", "", remainder, count=1, flags=re.IGNORECASE).strip()
        else:
            # "order by ..." 或 "limit ..." 等也放进 where_str，后面会从中拆出 order_by_str
            where_str = remainder
    # where 后无条件（仅有 where 或 where 后空白）→ 视为空条件
    if where_str and where_str.strip().lower() == "where":
        where_str = ""
    if where_str:
        # 支持 where_str 以 "order by" 开头（表名后直接跟 order by / limit 的情况）
        mo = re.search(r"(?:^\s*|\s+)order\s+by\s+(.+?)(?:\s+limit\s+\d+(?:\s*,\s*\d+)?\s*)?$", where_str, flags=re.IGNORECASE | re.DOTALL)
        if mo:
            order_by_str = mo.group(1).strip()
            where_str = (where_str[: mo.start()] + where_str[mo.end() :]).strip() if mo.start() > 0 else ""
            if not where_str.strip():
                where_str = ""
        elif re.match(r"^\s*limit\s+\d+", where_str, re.IGNORECASE):
            where_str = ""
        else:
            # where 条件后紧跟 limit N（无 order by）：去掉尾部 limit 部分，保证 where_str 仅为条件
            where_str = re.sub(r"\s+limit\s+\d+(?:\s*,\s*\d+)?\s*$", "", where_str, flags=re.IGNORECASE).strip()
    return select_list, from_table, where_str, order_by_str


def _parse_order_by(order_by_str, resolve_fn):
    """
    解析 order by 子句为 sortItems。
    格式：字段1 [asc|desc], 字段2 [asc|desc], ... 未写 asc/desc 时视为 asc。
    返回：[{"key": "System_xxx", "isAscending": True/False}, ...]
    """
    if not order_by_str or not isinstance(order_by_str, str):
        return []
    items = []
    for part in re.split(r"\s*,\s*", order_by_str):
        part = part.strip()
        if not part:
            continue
        tokens = part.split()
        if not tokens:
            continue
        field_name = tokens[0]
        asc = True
        if len(tokens) >= 2 and tokens[1].lower() == "desc":
            asc = False
        elif len(tokens) >= 2 and tokens[1].lower() == "asc":
            asc = True
        key = resolve_fn(field_name)
        if key is None:
            logger.warning("【字段映射】order by 字段未找到匹配，已跳过: %s", field_name)
            continue
        items.append({"key": key, "isAscending": asc})
    return items


def _eval_date_expr(expr):
    """
    将日期/时间表达式求值为具体字符串，供 API 使用（API 不接受 date_sub/current_date 等原始表达式）。
    支持：
    - curdate() / current_date() / current_date → 当天 00:00:00
    - now() / current_timestamp() → 当前日期时间
    - date_format( curdate()|current_date(), "%Y-MM-DD" ) → 当年该月日 00:00:00（如 "%Y-01-01" 当年第一天）
    - date_sub( curdate()|current_date()|now(), interval N day|days|week|weeks|month|months|year|years )
    - date_add( 同上, interval N ... )
    - 已是 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS 的字符串
    返回格式：YYYY-MM-DD HH:MM:SS（无 T）。
    """
    if not expr or not isinstance(expr, str):
        return None
    s = expr.strip().strip("'\"").strip()
    # 去掉一层外层括号，便于匹配 "( date_sub(...) )"
    if len(s) >= 2 and s.startswith("(") and s.endswith(")"):
        s = s[1:-1].strip()
    if not s:
        return None
    s_lower = re.sub(r"\s+", " ", s.lower())
    now = datetime.now()

    # 单函数：curdate / current_date / now（带或不带括号）
    if s_lower in ("curdate()", "current_date()", "curdate", "current_date"):
        return now.strftime("%Y-%m-%d 00:00:00")
    if s_lower in ("now()", "current_timestamp()", "now"):
        return now.strftime("%Y-%m-%d %H:%M:%S")

    # date_format( curdate()|current_date(), "%Y-MM-DD" ) → 当年指定月日 00:00:00（如当年第一天 %Y-01-01）
    date_fmt = re.match(
        r"date_format\s*\(\s*(?:curdate|current_date)\s*\(\s*\)\s*,\s*[\"']%Y-(\d{2})-(\d{2})[\"']\s*\)",
        s_lower,
        re.IGNORECASE,
    )
    if date_fmt:
        try:
            mo, dd = int(date_fmt.group(1)), int(date_fmt.group(2))
            if 1 <= mo <= 12 and 1 <= dd <= 31:
                try:
                    res_dt = now.replace(month=mo, day=dd, hour=0, minute=0, second=0, microsecond=0)
                except ValueError:
                    # 非法日（如 2 月 30 日）→ 取该月最后一天
                    if mo == 12:
                        res_dt = (now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1))
                    else:
                        res_dt = (now.replace(month=mo + 1, day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1))
                return res_dt.strftime("%Y-%m-%d 00:00:00")
        except (ValueError, TypeError):
            pass

    # date_sub( base, interval N unit ) 或 date_add( base, interval N unit )
    # base 仅支持 curdate()|current_date()|now()
    sub_add = re.match(
        r"(date_sub|date_add)\s*\(\s*(curdate|current_date|now)\s*\(\s*\)\s*,\s*interval\s+(\d+)\s+(month|day|year|week)s?\s*\)",
        s_lower,
        re.IGNORECASE,
    )
    if sub_add:
        func, base, n_str, unit = sub_add.group(1), sub_add.group(2), sub_add.group(3), sub_add.group(4)
        n = int(n_str)
        if base == "now":
            base_dt = now
            fmt_with_time = True
        else:
            base_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
            fmt_with_time = False
        delta = _interval_to_timedelta(n, unit)
        if func == "date_sub":
            res_dt = base_dt - delta
        else:
            res_dt = base_dt + delta
        if fmt_with_time:
            return res_dt.strftime("%Y-%m-%d %H:%M:%S")
        return res_dt.strftime("%Y-%m-%d 00:00:00")

    # 已是 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS
    if re.match(r"\d{4}-\d{2}-\d{2}", s):
        return s if " " in s else (s + " 00:00:00")
    return None


def _interval_to_timedelta(n, unit):
    """interval N unit 转为 timedelta。unit 为 day|week|month|year（小写）。"""
    u = (unit or "").lower()
    if u == "day":
        return timedelta(days=n)
    if u == "week":
        return timedelta(weeks=n)
    if u == "month":
        return timedelta(days=n * 30)
    if u == "year":
        return timedelta(days=n * 365)
    return timedelta(days=n)


def _looks_like_date_expr(expr):
    """判断是否像日期/时间表达式（未支持则需给出明确错误而非原样传给 API）。"""
    if not expr or not isinstance(expr, str):
        return False
    t = expr.strip().lower()
    if not t:
        return False
    return bool(
        re.search(r"date_sub|date_add|date_format|curdate|current_date|interval\s+\d+\s+(day|month|year|week)", t)
        or t.startswith("now(")
        or t in ("curdate()", "current_date()", "now()")
    )


# 日期表达式不支持时提示文案（API 不接受原始表达式，必须在此求值）
_DATE_EXPR_SUPPORTED = (
    "支持的日期/时间表达式：curdate()、current_date()、now()；"
    "date_format( curdate()|current_date(), \"%Y-MM-DD\" )（如 %Y-01-01 当年第一天）；"
    "date_sub/date_add( curdate()|current_date()|now(), interval N day|week|month|year )；"
    "或直接写 YYYY-MM-DD / YYYY-MM-DD HH:MM:SS。"
)


def _end_of_today():
    """返回当天 23:59:59，用作时间范围的 end（如「最近一周」截止到今天结束）。"""
    return datetime.now().strftime("%Y-%m-%d 23:59:59")


def _normalize_range_end_to_end_of_today(s):
    """
    时间范围的右端统一规范：若涉及当前时间（当天、当月、当周、当年），右端按今天最晚时间算。
    若 s 的日期部分是今天，则返回今天 23:59:59；否则返回 s 不变。
    """
    if not s or not isinstance(s, str):
        return s
    s = s.strip()
    # 提取日期部分：YYYY-MM-DD
    mo = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    if not mo:
        return s
    date_part = mo.group(1)
    today = datetime.now().strftime("%Y-%m-%d")
    if date_part == today:
        return _end_of_today()
    return s


def _parse_in_not_in_value(s):
    """
    解析 in / not in 的右值，得到字符串列表。
    支持：("a","b","c")、('a','b')、("a", 'b') 等；会去掉最外层括号与每项首尾引号；
    若值内含逗号（如 "a,b"），按引号边界提取，不按逗号误拆。
    """
    if not s or not isinstance(s, str):
        return []
    s = s.strip()
    # 去掉最外层括号，如 ("a","b") 或 ( "a" , "b" )
    if len(s) >= 2 and s[0] == "(" and s[-1] == ")":
        s = s[1:-1].strip()
    # 按引号边界提取：先匹配所有 "..." 和 '...'，保证值内逗号不被拆分
    out = []
    for m in re.finditer(r'"([^"]*)"|\'([^\']*)\'', s):
        out.append((m.group(1) if m.group(1) is not None else m.group(2)) or "")
    if out:
        return [x.strip() for x in out]
    # 回退：无引号时按逗号分割并去首尾空白
    return [v.strip().strip("'\"").strip() for v in s.split(",") if v.strip()]


def _is_literal(s):
    """判断是否为字面量（数字或引号字符串），此类不应作为字段名解析。"""
    if not s or not isinstance(s, str):
        return True
    t = s.strip()
    if not t:
        return True
    if t.isdigit():
        return True
    if t.startswith('"') and t.endswith('"') and len(t) >= 2:
        return True
    if t.startswith("'") and t.endswith("'") and len(t) >= 2:
        return True
    if t.replace(".", "", 1).isdigit() and t.count(".") <= 1:
        return True
    return False


_BETWEEN_AND_PLACEHOLDER = "\x00__BETWEEN_AND__\x00"


def _protect_between_and(raw_str):
    """将 between X and Y 中的「 and 」替换为占位符，避免被逻辑 and/or 拆分。"""
    out = []
    i = 0
    rest = raw_str
    while True:
        mo = re.search(r"\bbetween\s+", rest, re.IGNORECASE)
        if not mo:
            out.append(rest)
            break
        start = mo.start()
        out.append(rest[: start])
        rest = rest[mo.end() :]
        # 找 between 后的第一个「 and 」（允许中间有空格、数字、日期等）
        and_mo = re.search(r"\s+and\s+", rest, re.IGNORECASE)
        if not and_mo:
            out.append("between ")
            out.append(rest)
            break
        # 保留 between 与 and 之间的内容、占位符、and 之后到下一个 and/or 或结尾的内容
        left_part = rest[: and_mo.start()].strip()
        right_rest = rest[and_mo.end() :]
        # 右端到下一个顶层 and/or 或结尾（简单处理：取到下一个 \s+and\s+ 或 \s+or\s+ 或结尾）
        next_and_or = re.search(r"\s+(?:and|or)\s+", right_rest, re.IGNORECASE)
        if next_and_or:
            right_part = right_rest[: next_and_or.start()].strip()
            rest = right_rest[next_and_or.start() :]
        else:
            right_part = right_rest.strip()
            rest = ""
        out.append("between ")
        out.append(left_part)
        out.append(_BETWEEN_AND_PLACEHOLDER)
        out.append(right_part)
        if not rest:
            break
    return "".join(out)


def _split_by_top_level_and_or(raw_str):
    """按顶层「 and 」/「 or 」拆分（忽略括号内、且恢复 between 占位符后的片段内的占位符）。"""
    protected = _protect_between_and(raw_str)
    depth = 0
    segments = []
    current = []
    i = 0
    while i < len(protected):
        c = protected[i]
        if c == "(":
            depth += 1
            current.append(c)
            i += 1
        elif c == ")":
            depth -= 1
            current.append(c)
            i += 1
        elif depth == 0 and (protected[i : i + 5].lower() == " and " or protected[i : i + 4].lower() == " or "):
            if " and " in protected[i : i + 5].lower():
                token_len = 5
                token = " and "
            else:
                token_len = 4
                token = " or "
            seg = "".join(current).strip()
            if seg:
                segments.append((seg, "AND" if "and" in token.lower() else "OR"))
            current = []
            i += token_len
        else:
            current.append(c)
            i += 1
    seg = "".join(current).strip()
    if seg:
        segments.append((seg, None))
    return segments


def _is_paren_segment(seg):
    s = (seg or "").strip()
    return len(s) >= 2 and s.startswith("(") and s.endswith(")")


def _is_chinese_value(value):
    """判断条件值是否为中文（含 CJK 字符）；用于工作区条件时用 workspace 参数替换。"""
    if value is None:
        return False
    if isinstance(value, list):
        return any(_is_chinese_value(v) for v in value)
    s = str(value).strip()
    return bool(s and re.search(r"[\u4e00-\u9fff]", s))


def _normalize_workspace_to_list(workspace):
    """将 workspace 规范为列表；多值（逗号分隔或 list）返回列表，单值返回单元素列表，空返回 None。"""
    if workspace is None:
        return None
    if isinstance(workspace, (list, tuple)):
        out = [str(x).strip() for x in workspace if x]
        return out if out else None
    s = str(workspace).strip()
    if not s:
        return None
    if "," in s:
        out = [x.strip() for x in s.split(",") if x.strip()]
        return out if out else [s]
    return [s]


# WHERE 中视为「工作区」条件的字段 key：这些字段在 value 为中文时可用传入的 workspace 替换
WORKSPACE_FIELD_KEYS = frozenset({
    "System_Workspace",
    "System_WorkspaceKey",
    "System_WorkspaceKey.name",
    "System_Workspace.name",
})

# 研发云字段元数据中时间类 type（大小写不敏感）
_DATETIME_FIELD_TYPES = frozenset({
    "DATETIME", "DATE", "DATE_TIME", "TIMESTAMP",
})


def _field_def_for_key(field_key, field_items, field_items_fallback=None):
    """先主列表、再备选列表，按 key 匹配第一条字段定义（dict）。"""
    if not field_key:
        return None
    key = (field_key or "").strip()
    if key.endswith(".name"):
        key = key[: -len(".name")]
    for lst in (field_items or [], field_items_fallback or []):
        for it in lst:
            if not isinstance(it, dict):
                continue
            if (it.get("key") or "").strip() == key:
                return it
    return None


def _is_datetime_type_field(field_key, field_items, field_items_fallback=None):
    """判断字段在主/备选 field_items 中是否为日期/时间类型（如 dateTime）。"""
    if not field_key or (not field_items and not field_items_fallback):
        return False
    it = _field_def_for_key(field_key, field_items, field_items_fallback)
    if not it:
        return False
    ft = (it.get("type") or "").strip().upper()
    if ft in _DATETIME_FIELD_TYPES or "DATETIME" in ft:
        return True
    if ft == "DATE":
        return True
    return False


def _partial_date_equality_range(raw):
    """
    若值为仅年、仅年月、仅年月日（无时分秒），返回 (start, end) 字符串，格式 YYYY-MM-DD HH:MM:SS。
    含 ':' 或 'T'（ISO 时间）视为已带时分秒，不转换，返回 None。
    """
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip().strip("'\"").strip()
    if not s:
        return None
    if ":" in s:
        return None
    if re.search(r"[tT]\d", s):
        return None
    if re.match(r"^\d{4}$", s):
        y = int(s)
        if y < 1 or y > 9999:
            return None
        return (f"{y:04d}-01-01 00:00:00", f"{y:04d}-12-31 23:59:59")
    mo = re.match(r"^(\d{4})-(\d{2})$", s)
    if mo:
        y, month = int(mo.group(1)), int(mo.group(2))
        if month < 1 or month > 12:
            return None
        last_day = calendar.monthrange(y, month)[1]
        return (
            f"{y:04d}-{month:02d}-01 00:00:00",
            f"{y:04d}-{month:02d}-{last_day:02d} 23:59:59",
        )
    mo = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if mo:
        y, month, d = int(mo.group(1)), int(mo.group(2)), int(mo.group(3))
        try:
            datetime(y, month, d)
        except ValueError:
            return None
        return (
            f"{y:04d}-{month:02d}-{d:02d} 00:00:00",
            f"{y:04d}-{month:02d}-{d:02d} 23:59:59",
        )
    return None


# 非常重要的方法，用于解析where条件中的字段和值，并转换为工作项查询接口的数据结构
def _parse_where_condition(where_str, field_items, resolve_fn, workspace=None, field_items_fallback=None):
    """解析 where 为 sourceClauses，支持 AND/OR 及常用操作符（含 SQL 标准 <> 不等于，与 != 一致）。左侧为字面量（如 1=1）的条件会跳过。
    leftGroup/rightGroup：由条件**前后括号个数**决定。条件左边有 N 个 '(' 则 leftGroup=N，右边有 M 个 ')' 则 rightGroup=M。
    实现：每进入一层 (...) 给该层首子句 leftGroup+1、末子句 rightGroup+1；递归返回时累加。
    若传入 workspace：条件中与工作区相关（WORKSPACE_FIELD_KEYS 中任一字段）且 value 为中文时，用 workspace 替换；workspace 为多值时运算符为 in。
    """
    if not (where_str or "").strip():
        return []
    raw = where_str.strip()
    segment_tuples = _split_by_top_level_and_or(raw)
    segs = []
    op_after = []
    for i, (seg, logical) in enumerate(segment_tuples):
        seg_restored = seg.replace(_BETWEEN_AND_PLACEHOLDER, " and ")
        segs.append(seg_restored)
        op_after.append(logical)

    op_map = [
        (" not between ", "not between"),
        (" between ", "between"),
        (" not like ", "not like"),
        (" like ", "like"), (" =_accurate ", "=_accurate"), (" is not null", "is not null"), (" is null", "is null"),
        (" not in ", "not in"), (" in ", "in"), (" >= ", ">="), (" <= ", "<="), (" != ", "!="),
        (" <> ", "!="),  # SQL 标准不等于，与 != 等价
        (" > ", ">"), (" < ", "<"), (" = ", "="), (">=", ">="), ("<=", "<="), ("!=", "!="), ("<>", "!="), (">", ">"), ("<", "<"), ("=", "="),
    ]
    clauses = []
    for idx, seg in enumerate(segs):
        if not seg:
            continue
        seg = re.sub(r"\s+", " ", seg.strip())
        if _is_paren_segment(seg):
            inner = seg[1:-1].strip()
            inner_clauses = _parse_where_condition(
                inner, field_items, resolve_fn, workspace=workspace, field_items_fallback=field_items_fallback
            )
            if inner_clauses:
                # logicalOperator 表示「当前子句与前一子句之间」的运算符，故用 op_after[idx-1]（前一段后面的运算符）
                first_logical = "" if not clauses else ((op_after[idx - 1] if idx >= 1 and idx - 1 < len(op_after) else None) or "AND")
                inner_clauses[0]["logicalOperator"] = first_logical
                # 本层一对括号：条件左边多 1 个 (、右边多 1 个 )，故首子句 leftGroup+1、末子句 rightGroup+1
                inner_clauses[0]["leftGroup"] = inner_clauses[0].get("leftGroup", 0) + 1
                inner_clauses[-1]["rightGroup"] = inner_clauses[-1].get("rightGroup", 0) + 1
                clauses.extend(inner_clauses)
            continue
        seg_lower = seg.lower()
        field_name = operator = value = None
        for sep, op in op_map:
            if sep in seg_lower:
                pos = seg_lower.find(sep)
                field_name = seg[:pos].strip()
                right = seg[pos + len(sep):].strip()
                value = None if op in ("is null", "is not null") else (right.strip("'\"").strip() if right else "")
                operator = op
                break
        if field_name is None or operator is None:
            raise ValueError(f"【WHERE条件字段映射】无法解析条件片段：{seg}")
        # 左侧为字面量（如 1=1）时跳过，不当作字段解析，避免「未找到匹配字段：1」
        if _is_literal(field_name):
            continue
        key_or_keys = resolve_fn(field_name)
        # 未匹配到字段时（resolve 返回 None）跳过该条件
        if key_or_keys is None:
            logger.info("【WHERE条件字段映射】WHERE 条件中未找到匹配字段，已跳过该条件: %s", field_name)
            continue
        keys = [key_or_keys] if isinstance(key_or_keys, str) else list(key_or_keys) if isinstance(key_or_keys, (list, tuple)) else []
        keys = ["System_WorkspaceKey" if (k or "").strip() == "System_Workspace" else k for k in keys]
        if not keys:
            continue
        key = keys[0]
        # logicalOperator 表示「当前子句与前一子句之间」的运算符；op_after[i] 是「第 i 段后面」的运算符，故用 op_after[idx-1]
        logical = (op_after[idx - 1] if idx >= 1 and idx - 1 < len(op_after) and op_after[idx - 1] else None) or ("AND" if idx > 0 else "")
        if logical and logical != "AND":
            logical = "OR"
        segment_logical = logical or ("" if idx == 0 else "AND")
        clause = {
            "leftGroup": 0, "rightGroup": 0, "logicalOperator": segment_logical,
            "field": key, "operator": operator,
            "value": value if value is not None else ([] if operator in ("in", "not in") else ""),
        }
        # 工作区相关条件且 value 为中文时，用传入的 workspace 替换；workspace 多值则运算符为 in
        clause_field = (clause.get("field") or "").strip()
        if workspace is not None and clause_field in WORKSPACE_FIELD_KEYS:
            if _is_chinese_value(clause.get("value")):
                ws_list = _normalize_workspace_to_list(workspace)
                if ws_list:
                    if len(ws_list) > 1:
                        clause["operator"] = "in"
                        clause["value"] = ws_list
                    else:
                        clause["value"] = ws_list[0]
        if operator == "between":
            # 多 key 时仅用第一个，保证 (key>=A and key<=B) 语义
            if len(keys) > 1:
                logger.info("【WHERE条件字段映射】between 条件多 key 仅使用第一个: %s", keys)
            # 时间范围：值形如 "A and B" 解析为 [start, end]，不拼装 between，改为两条 >= 与 <=（and 大小写不敏感）
            if isinstance(clause["value"], str) and re.search(r"\s+and\s+", clause["value"], re.IGNORECASE):
                parts = re.split(r"\s+and\s+", clause["value"], maxsplit=1, flags=re.IGNORECASE)
                start_str = _eval_date_expr(parts[0].strip()) if len(parts) > 0 else None
                end_str = _eval_date_expr(parts[1].strip()) if len(parts) > 1 else None
                if start_str is not None and end_str is not None:
                    range_start, range_end = start_str, end_str
                    range_end = _normalize_range_end_to_end_of_today(range_end)
                else:
                    range_start, range_end = parts[0].strip(), parts[1].strip()
            else:
                range_list = clause["value"] if isinstance(clause["value"], list) else []
                range_start = range_list[0] if len(range_list) > 0 else ""
                range_end = range_list[1] if len(range_list) > 1 else ""
            if range_start != "" and range_end != "":
                range_end = _normalize_range_end_to_end_of_today(range_end)
                c_ge = {"leftGroup": 0, "rightGroup": 0, "logicalOperator": clause["logicalOperator"], "field": key, "operator": ">=", "value": range_start}
                c_le = {"leftGroup": 0, "rightGroup": 0, "logicalOperator": "AND", "field": key, "operator": "<=", "value": range_end}
                clauses.append(c_ge)
                clauses.append(c_le)
                continue
        elif operator == "not between":
            if len(keys) > 1:
                logger.info("【WHERE条件字段映射】not between 条件多 key 仅使用第一个: %s", keys)
            # not between A and B → field < A OR field > B
            val = clause.get("value") or ""
            if isinstance(val, str) and re.search(r"\s+and\s+", val, re.IGNORECASE):
                parts = re.split(r"\s+and\s+", val, maxsplit=1, flags=re.IGNORECASE)
                range_start = parts[0].strip() if len(parts) > 0 else ""
                range_end = parts[1].strip() if len(parts) > 1 else ""
            else:
                range_start, range_end = "", ""
            if range_start != "" and range_end != "":
                c_lt = {"leftGroup": 0, "rightGroup": 0, "logicalOperator": clause["logicalOperator"], "field": key, "operator": "<", "value": range_start}
                c_gt = {"leftGroup": 0, "rightGroup": 0, "logicalOperator": "OR", "field": key, "operator": ">", "value": range_end}
                clauses.append(c_lt)
                clauses.append(c_gt)
                continue
        elif operator in (">=", ">") and value:
            # 日期求值：仅当为相对日期（如 date_sub/curdate）时才补「<= 今天」；字面量 YYYY-MM-DD 只保留单条 >=
            start_str = _eval_date_expr(value)
            if start_str is not None:
                if _looks_like_date_expr(value):
                    # 相对表达式（如「最近 7 天」）：补 end 到今天
                    end_str = _end_of_today()
                    c_ge = {"leftGroup": 0, "rightGroup": 0, "logicalOperator": clause["logicalOperator"], "field": key, "operator": ">=", "value": start_str}
                    c_le = {"leftGroup": 0, "rightGroup": 0, "logicalOperator": "AND", "field": key, "operator": "<=", "value": end_str}
                    clauses.append(c_ge)
                    clauses.append(c_le)
                else:
                    # 字面量日期（如 2026-01-01）：单条 >=，不补范围
                    clause["value"] = start_str
                    clauses.append(clause)
                continue
            if _looks_like_date_expr(value):
                raise ValueError(f"【WHERE条件字段映射】不支持的日期表达式：{value}。{_DATE_EXPR_SUPPORTED}")
        elif operator in ("<=", "<") and value:
            end_str = _eval_date_expr(value)
            if end_str is not None:
                if _looks_like_date_expr(value):
                    # 相对表达式：补 start 从今天起
                    end_str = _normalize_range_end_to_end_of_today(end_str)
                    start_str = _eval_date_expr("curdate()") or datetime.now().strftime("%Y-%m-%d 00:00:00")
                    c_ge = {"leftGroup": 0, "rightGroup": 0, "logicalOperator": clause["logicalOperator"], "field": key, "operator": ">=", "value": start_str}
                    c_le = {"leftGroup": 0, "rightGroup": 0, "logicalOperator": "AND", "field": key, "operator": "<=", "value": end_str}
                    clauses.append(c_ge)
                    clauses.append(c_le)
                else:
                    # 字面量日期（如 2026-12-31）：单条 <=，不补范围
                    clause["value"] = _normalize_range_end_to_end_of_today(end_str)
                    clauses.append(clause)
                continue
            if _looks_like_date_expr(value):
                raise ValueError(f"【WHERE条件字段映射】不支持的日期表达式：{value}。{_DATE_EXPR_SUPPORTED}")
        elif operator == "=" and value:
            # 时间类型字段：仅年 / 年月 / 年月日（无时分秒）的等值条件改为闭区间 [当天 00:00:00, 当天/月/年 23:59:59]
            raw_eq = value.strip().strip("'\"") if isinstance(value, str) else str(value).strip().strip("'\"")
            if _is_datetime_type_field(keys[0], field_items, field_items_fallback):
                pr = _partial_date_equality_range(raw_eq)
                if pr is not None:
                    start_str, end_str = pr
                    for i, k in enumerate(keys):
                        if len(keys) == 1:
                            c_ge = {
                                "leftGroup": 0,
                                "rightGroup": 0,
                                "logicalOperator": segment_logical,
                                "field": k,
                                "operator": ">=",
                                "value": start_str,
                            }
                            c_le = {
                                "leftGroup": 0,
                                "rightGroup": 0,
                                "logicalOperator": "AND",
                                "field": k,
                                "operator": "<=",
                                "value": end_str,
                            }
                        else:
                            c_ge = {
                                "leftGroup": 1,
                                "rightGroup": 0,
                                "logicalOperator": segment_logical if i == 0 else "OR",
                                "field": k,
                                "operator": ">=",
                                "value": start_str,
                            }
                            c_le = {
                                "leftGroup": 0,
                                "rightGroup": 1,
                                "logicalOperator": "AND",
                                "field": k,
                                "operator": "<=",
                                "value": end_str,
                            }
                        clauses.append(c_ge)
                        clauses.append(c_le)
                    continue
            # 等号右侧若是日期表达式，也求值后传给 API
            ev = _eval_date_expr(value)
            if ev is not None:
                clause["value"] = ev
            elif _looks_like_date_expr(value):
                raise ValueError(f"【WHERE条件字段映射】不支持的日期表达式：{value}。{_DATE_EXPR_SUPPORTED}")
        elif operator in ("in", "not in") and isinstance(clause["value"], str) and clause["value"]:
            clause["value"] = _parse_in_not_in_value(clause["value"])
        elif operator in ("like", "not like") and isinstance(clause["value"], str) and clause["value"]:
            # like / not like：不向接口传 %，去掉首尾通配符
            clause["value"] = clause["value"].strip("% ")
        if value and str(value).strip().lower() == "@me":
            clause["value"] = "@Me"
        if value and str(value).strip().lower() == "@today-365d":
            clause["value"] = "@Today-365d"
        # 多 key 时展开为 (key1 op v or key2 op v)，加括号（leftGroup/rightGroup）
        if len(keys) > 1:
            for i, k in enumerate(keys):
                c = {**clause, "field": k}
                c["logicalOperator"] = segment_logical if i == 0 else "OR"
                c["leftGroup"] = 1 if i == 0 else 0
                c["rightGroup"] = 1 if i == len(keys) - 1 else 0
                clauses.append(c)
        else:
            clauses.append(clause)
    if clauses:
        clauses[0]["logicalOperator"] = ""
    return clauses


def _is_user_type_field(field_key, field_items, field_items_fallback=None):
    """判断字段在主/备选 field_items 中是否为 type=USER。用 clause 的 field 按 key 匹配。"""
    if not field_key or (not field_items and not field_items_fallback):
        return False
    key = (field_key or "").strip()
    it = _field_def_for_key(key, field_items, field_items_fallback)
    if not it:
        logger.info("【USER 字段判断】field_key=%s 未在 field_items 中匹配到，视为非 USER", key)
        return False
    field_type = (it.get("type") or "").strip()
    is_user = field_type.upper() == "USER"
    logger.info("【USER 字段判断】field_key=%s, type=%s, is_user=%s", key, field_type, is_user)
    return is_user


def _resolve_value_to_emp_no(value, headers):
    """将人员取值解析为工号：@me/me→当前用户工号，否则按名称查用户取第一个工号。"""
    if value is None:
        return value
    if isinstance(value, str):
        val = value.strip()
        if val.lower() in ("@me", "me"):
            return (headers.get("X-Emp-No") or "").strip() or value
        emp_no = (headers.get("X-Emp-No") or "").strip()
        token = (headers.get("X-Auth-Value") or "").strip()
        if not emp_no or not token:
            logger.warning("USER 字段取值转工号: 缺少 X-Emp-No/X-Auth-Value，保留原值")
            return value
        try:
            spec = importlib.util.spec_from_file_location("user_search", USER_SEARCH_MODULE)
            user_search = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(user_search)
            result = user_search.search_users(emp_no, token, val)
        except Exception as e:
            logger.warning("USER 字段取值转工号: 查询用户失败 value=%s, %s", truncate_for_log(str(value), 30), e)
            return value
        if not result or result.get("status") != "success":
            return value
        users = result.get("users") or []
        if not users:
            return value
        first_emp = (users[0].get("empNo") or "").strip()
        return first_emp if first_emp else value
    return value


def _field_def_needs_dot_name_suffix(field_def, field_key=None):
    """
    字段元数据是否应对查询使用 key.name：
    - type 为 advancedData：始终追加 .name；
    - type 为 string：默认不追加；仅当 field_key 命中配置 dot_name_string_keys 时，追加
    - 目前发现string 类型不能添加.name逻辑， usages.remoteDataSource判断有问题 先注释，后续根据反馈修改
    """
    if not isinstance(field_def, dict):
        return False
    ft = (field_def.get("type") or "").strip().lower()
    if ft == "advanceddata":
        return True
    if ft != "string":
        return False
    key = (field_key or "").strip()
    if not key:
        return False
    if key in _get_dot_name_string_keys():
        return True
     
    # usages = field_def.get("usages")
    # if not isinstance(usages, list):
    #     return False
    # for u in usages:
    #     if not isinstance(u, dict):
    #         continue
    #     rds = u.get("remoteDataSource")
    #     if rds is not None and str(rds).strip():
    #         return True
    return False 


def _expand_dot_name_in_multi_values_to_or_likes(source_clauses):
    """
    advancedData 等 .name 条件：用户写「字段 in v1,v2,...」且解析为多值列表时，
    若直接传 in 列表给接口往往为精确匹配；为保留与 like 一致的模糊语义，拆成
    (field like v1 OR field like v2 OR ...)。展开会引入一层新的括号，因此：
    - 首条子句 leftGroup = 原子句 leftGroup + 1
    - 末条子句 rightGroup = 原子句 rightGroup + 1
    单值 in 退化为单条 like（不新增括号层）。
    """
    out = []
    for c in source_clauses or []:
        if not isinstance(c, dict):
            continue
        field = (c.get("field") or "").strip()
        op = (c.get("operator") or "").strip().lower()
        val = c.get("value")
        orig_left_group = int(c.get("leftGroup") or 0)
        orig_right_group = int(c.get("rightGroup") or 0)
        if (
            not field.endswith(".name")
            or op != "in"
            or not isinstance(val, list)
            or len(val) <= 1
        ):
            out.append(c)
            continue
        vals = []
        for v in val:
            if v is None:
                continue
            s = v.strip() if isinstance(v, str) else str(v).strip()
            if s:
                vals.append(s)
        if len(vals) <= 1:
            one = vals[0] if vals else ""
            out.append({**c, "operator": "like", "value": one.strip("% ") if isinstance(one, str) else one})
            continue
        n = len(vals)
        for i, raw in enumerate(vals):
            rv = raw.strip("% ") if isinstance(raw, str) else raw
            out.append({
                **c,
                "field": field,
                "operator": "like",
                "value": rv,
                "logicalOperator": (c.get("logicalOperator") or "") if i == 0 else "OR",
                # 展开 in 为多条 OR like 会新增一层括号，原分组基础上 +1
                "leftGroup": (orig_left_group + 1) if i == 0 else 0,
                "rightGroup": (orig_right_group + 1) if i == n - 1 else 0,
            })
    return out


#
def build_query_data_from_select_and_conditions(
    select_items_str, conditions_str, workspace, headers, for_my_workspace=False,
    order_by_str=None, limit=None, raw_user_input=None, work_item_type_key=None,
):
    """
    从「查询列」与「查询条件」构建查询请求体（不解析 from，不按工作项类型过滤）。
    :param select_items_str: 查询列，英文逗号分隔的字段名称，如 "标题,状态,标识"；空或 None 表示使用默认列。
    :param conditions_str: 查询条件，与 where 后内容同格式，支持 AND/OR、() 组合与嵌套；空或 None 表示无条件。
    :param workspace: 工作区 key。
    :param headers: 认证头。
    :param for_my_workspace: 是否为我相关查询。
    :param order_by_str: 排序，如 "更新时间 desc"；空或 None 表示默认按更新时间倒序。
    :param limit: 返回条数上限，正整数；None 或 0 表示使用默认 pageSize（QUERY_LIMIT_DEFAULT）；超过 QUERY_LIMIT_MAX 时按 QUERY_LIMIT_MAX 截断。
    :param raw_user_input: 用户原始话术，用于前后匹配时判断候选字段名是否在用户表述中出现；不传则用 select/conditions 字符串。
    :param work_item_type_key: 可选；若已知当前查询的工作项类型 key，会传给字段解析，用于 field_resolver_config.json 中 field_recommended_names 的分级匹配。
    :return: 与 build_query_data_from_sql 相同结构的 query_data。
    """

    #抓取指定工作区workspace下的字段；备选 v2/withUsageDistinct（跨工作区字段列表）
    field_items = fetch_fields(workspace, headers)
    field_items_fallback = fetch_fields_v2_with_usage_distinct(workspace, headers)

    def _field_display_name(key, items, items_fallback=None):
        """从主/备选 field_items 中取 key 对应的展示名（nameZh/name/nameEn），无则返回 key。key 后缀为 .name 时先去掉再匹配。"""
        key = (key or "").strip()
        if not key:
            return key
        lookup_key = key[:-5] if key.endswith(".name") else key
        for lst in (items or [], items_fallback or []):
            for it in lst:
                if not isinstance(it, dict):
                    continue
                if (it.get("key") or "").strip() == lookup_key:
                    nz = (it.get("nameZh") or "").strip()
                    n = (it.get("name") or "").strip()
                    ne = (it.get("nameEn") or "").strip()
                    return nz or n or ne or key
        return key

    # 给排序字段解析使用
    def resolve(name):
        return resolve_field_key(
            name,
            field_items,
            headers,
            raise_on_not_found=False,
            workspace=workspace,
            field_items_fallback=field_items_fallback,
        )

    select_list = None
    if select_items_str and isinstance(select_items_str, str):
        raw = (select_items_str or "").strip()
        if raw and raw.lower() != "*":
            select_list = [x.strip() for x in re.split(r"\s*,\s*", raw) if x.strip()]
            select_list = ["System_WorkspaceKey" if (x or "").strip() == "System_Workspace" else (x or "").strip() for x in select_list]

    # 前后匹配时以用户原始话术为上下文，未传则退化为 select 字符串
    original_input_for_match = (raw_user_input or "").strip() or ((select_items_str or "").strip() if select_items_str else "")
    select_multi_match = []
    select_keys = DEFAULT_SELECT_KEYS
    if select_list:
        logger.info("【SELECT字段映射】开始解析 select 字段列表（精确采纳；前后匹配需在用户话术中出现）: %s", select_list)
        select_keys = []
        for n in select_list:
            # 解析selectItems中的keys,也就是大模型识别的字段名转换为key
            keys = resolve_field_key_for_select_list(
                n,
                field_items,
                headers,
                raise_on_not_found=False,
                original_input=original_input_for_match,
                workspace=workspace,
                field_items_fallback=field_items_fallback,
                work_item_type_key=work_item_type_key,
            )
            if keys:
                if len(keys) > 1:
                    key_infos = [
                        {"key": k, "field_name": _field_display_name(k, field_items, field_items_fallback)}
                        for k in keys
                    ]
                    select_multi_match.append({"name": n, "keys": list(keys), "key_infos": key_infos})
                select_keys.extend(keys)
            else:
                logger.info("【SELECT字段映射】select 字段未匹配或未在原始输入中出现，已跳过: %s", n)
        for k in DEFAULT_SELECT_KEYS:
            if k not in select_keys:
                select_keys.append(k)
        if not select_keys:
            select_keys = DEFAULT_SELECT_KEYS
        else:
            logger.info("【SELECT字段映射】select 字段列表解析完成，匹配到的 key: %s", select_keys)
    else:
        logger.info("【SELECT字段映射】未指定 select 字段，使用默认 key: %s", select_keys)

    where_str = (conditions_str or "").strip() if conditions_str else ""
    # where 前后匹配时以用户原始话术为上下文，未传则退化为 conditions 字符串
    where_original_input = (raw_user_input or "").strip() or where_str

    def _maybe_advanced_data_suffix(key_or_keys):
        """单 key 或 key 列表：advancedData，或 string 且 usages 含非空 remoteDataSource 时追加 .name。"""
        if key_or_keys is None:
            return None
        if isinstance(key_or_keys, str):
            one = key_or_keys
            for lst in (field_items or [], field_items_fallback or []):
                for it in lst:
                    if not isinstance(it, dict):
                        continue
                    if (it.get("key") or "").strip() == one and _field_def_needs_dot_name_suffix(it, one):
                        return one + ".name"
            return one
        out = []
        for k in key_or_keys:
            suf = k
            for lst in (field_items or [], field_items_fallback or []):
                for it in lst:
                    if not isinstance(it, dict):
                        continue
                    if (it.get("key") or "").strip() == k and _field_def_needs_dot_name_suffix(it, k):
                        suf = k + ".name"
                        break
                if suf != k:
                    break
            out.append(suf)
        return out

    where_multi_match = []

    def resolve_for_where(name):
        # 解析查询条件中的字段key，也即是将字段中文转换为字段key
        key_or_keys = resolve_field_key_for_where(
            name,
            field_items,
            headers,
            original_input=where_original_input,
            workspace=workspace,
            field_items_fallback=field_items_fallback,
            work_item_type_key=work_item_type_key,
        )
        # 对 advancedData / string+remoteDataSource 字段，转换为 key.name
        result = _maybe_advanced_data_suffix(key_or_keys)
        if isinstance(result, list) and len(result) > 1:
            key_infos = [
                {"key": k, "field_name": _field_display_name(k, field_items, field_items_fallback)}
                for k in result
            ]
            where_multi_match.append({"name": name, "keys": list(result), "key_infos": key_infos})
        return result

    logger.info("【WHERE条件字段映射】解析开始: %s", where_str or "(空)")
    # 将查询条件转换为工作项查询接口的数据结构，也即是将类sql条件表达式转换为工作项的filterItem结构
    # 这里面涉及到工作区字段解析、工作项类型字段解析、和其他通用类字段解析
    source_clauses = _parse_where_condition(
        where_str, field_items, resolve_for_where, workspace=workspace, field_items_fallback=field_items_fallback
    )
    source_clauses = _expand_dot_name_in_multi_values_to_or_likes(source_clauses)
    #
    for c in source_clauses:
        if (c.get("field") or "").endswith(".name"):
            _op = (c.get("operator") or "").strip()
            _op_lo = _op.lower()
            # advancedData / string+remoteDataSource 等 .name 语义：等值/包含多用 like；不等于需用 not like
            if _op in ("!=", "<>") or _op_lo in ("!=", "<>"):
                c["operator"] = "not like"
            elif _op_lo == "not like":
                c["operator"] = "not like"
            else:
                c["operator"] = "like"
            if isinstance(c.get("value"), str) and c["value"]:
                c["value"] = c["value"].strip("% ")
    for c in source_clauses:
        if not _is_user_type_field((c.get("field") or "").strip(), field_items, field_items_fallback):
            continue
        v = c.get("value")
        if isinstance(v, list):
            c["value"] = [_resolve_value_to_emp_no(x, headers) for x in v]
        else:
            c["value"] = _resolve_value_to_emp_no(v, headers)
    if source_clauses:
        source_clauses[0]["logicalOperator"] = ""

    emp_no = (headers.get("X-Emp-No") or "").strip()
    for c in source_clauses:
        v = c.get("value")
        if isinstance(v, str) and v.strip().lower() == "@me":
            c["value"] = emp_no
        elif isinstance(v, list):
            c["value"] = [emp_no if (isinstance(x, str) and x.strip().lower() == "@me") else x for x in v]
    logger.info("【WHERE条件字段映射】解析完成")

    order_raw = (order_by_str or "").strip() if order_by_str else ""
    if order_raw:
        sort_items = _parse_order_by(order_raw, resolve)
        if not sort_items:
            sort_items = [{"isAscending": False, "key": "System_ChangedDate"}]
    else:
        sort_items = [{"isAscending": False, "key": "System_ChangedDate"}]
    page_size = QUERY_LIMIT_DEFAULT
    if limit is not None and limit != "":
        try:
            n = int(limit)
            if n > 0:
                page_size = min(n, QUERY_LIMIT_MAX)
        except (TypeError, ValueError):
            pass
    query_data = {
        "crossWorkspace": for_my_workspace,
        "myWorkspaceConditions": [],
        "queryCondition": {"type": "flat", "sourceClauses": source_clauses},
        "selectItems": [{"key": k} for k in select_keys],
        "sortItems": sort_items,
        "pageNo": 1,
        "pageSize": page_size,
    }
    query_data["_multi_match_fields"] = {"select": select_multi_match, "where": where_multi_match}
    return query_data
