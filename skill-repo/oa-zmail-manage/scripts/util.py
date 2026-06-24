#!/usr/bin/env python3
"""
通用工具函数（含原 util + utils：邮箱/时间/JSON/日期范围/MF 解析等）。
"""

import json
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union

# 搜索默认时间范围（最近 N 天）
SEARCH_DAYS = 14

# 邮箱合法性：本地部分 1～64 字符，域名含至少一个点且 TLD≥2 字符，总长≤254（RFC 5321）
_EMAIL_RE = re.compile(r"^[^\s@]{1,64}@[^\s@]+\.[^\s@]{2,}$")
_EMAIL_MAX_LEN = 254

# 日志目录：scripts 的上一层下的 logs
_LOG_DIR = None


def _get_log_dir() -> str:
    """返回 logs 目录绝对路径（scripts 上一层的 logs）。"""
    global _LOG_DIR
    if _LOG_DIR is None:
        _LOG_DIR = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs"
        )
    return _LOG_DIR


def _get_log_path() -> str:
    """每天一个文件：zmail_yyyymmdd.log，第二天自动换新文件。"""
    log_dir = _get_log_dir()
    os.makedirs(log_dir, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    return os.path.join(log_dir, f"zmail_{today}.log")


def zmail_log(message: str, *args, **kwargs) -> None:
    """
    公共日志：按天写入 logs/zmail_yyyymmdd.log，第二天自动换新文件。
    每行格式：YYYY-MM-DD HH:MM:SS.ffffff 内容。异常不向调用方抛出。
    """
    if args or kwargs:
        try:
            message = message.format(*args, **kwargs)
        except (TypeError, KeyError, ValueError):
            message = str(message)
    try:
        log_path = _get_log_path()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        line = f"{ts} {message}\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass  # 写日志失败不影响主流程（取路径、open、write 任一异常均吞掉）


def zmail_log_error(message: str, *args, **kwargs) -> None:
    """记录 ERROR 级别日志，写入格式：{timestamp} [ERROR] {message}。"""
    if args or kwargs:
        try:
            message = message.format(*args, **kwargs)
        except (TypeError, KeyError, ValueError):
            message = str(message)
    zmail_log(f"[ERROR] {message}")


def zmail_log_warning(message: str, *args, **kwargs) -> None:
    """记录 WARNING 级别日志，写入格式：{timestamp} [WARNING] {message}。"""
    if args or kwargs:
        try:
            message = message.format(*args, **kwargs)
        except (TypeError, KeyError, ValueError):
            message = str(message)
    zmail_log(f"[WARNING] {message}")

# API 错误日志：需脱敏的 header 键名（认证、工号）
_MASK_HEADER_KEYS = frozenset(
    {"x-zmail-uid", "x-zmail-tid", "x-emp-no", "authorization", "cookie"}
)
# 工号常见格式：数字开头，5～11 位数字（如 0668001367、10182695）
_EMP_NO_RE = re.compile(r"\b\d{5,11}\b")
# 收件人字符串：整串须为工号或邮箱（发送时校验用）
_EMP_NO_FULL_RE = re.compile(r"^\d{5,11}$")


def validate_mail_id(text: Optional[str]) -> Tuple[bool, Optional[str]]:
    """
    校验字符串是否为合法的 zMail 邮件 ID。
    合法情形：1）以 ev 或 mas 开头（历史邮件 ID，不区分大小写，如 EV-、ev-、MaS-）；2）24 位十六进制且前 4 位十六进制标识在合法集合内（在线邮件 ID）。
    :return: (True, None) 合法；(False, reason) 非法，reason 为不合法原因。
    """
    valid_first4 = frozenset(
        (201, 202, 203, 204, 205, 206, 11, 12, 13, 14, 16, 17, 18, 31, 32, 1, 2, 51, 61, 71, 72, 73)
    )
    err_msg = (
        "邮件 ID 无效。请按以下步骤获取正确 ID："
        "1）优先从上下文中的邮件列表（收件箱/发件箱/搜索结果等）定位目标邮件并提取其 24 位 ID；"
        "2）若上下文中无该邮件，先执行搜索邮件，再从返回结果中提取正确的邮件 ID。"
    )
    if text is None:
        return False, err_msg
    if not isinstance(text, str):
        return False, err_msg
    s = text.strip()
    if not s:
        return False, err_msg
    # 以 ev 或 mas 开头视为历史邮件 ID，合法（兼容混合大小写，如 EV-、ev-、MaS-）
    lower = s.lower()
    if lower.startswith("ev") or lower.startswith("mas"):
        return True, None
    # 在线邮件 ID：须为 24 位十六进制字符串，且前 4 位十六进制在合法集合内
    if not re.fullmatch(r"[0-9a-fA-F]{24}", s):
        return False, err_msg
    try:
        if int(s[:4], 16) not in valid_first4:
            return False, err_msg
    except ValueError:
        return False, err_msg
    return True, None


def is_emp_no(text: Optional[str]) -> bool:
    """判断字符串是否为工号格式（整串为数字，5～11 位）。"""
    if not text or not isinstance(text, str):
        return False
    return bool(_EMP_NO_FULL_RE.match(text.strip()))


def is_history_mail_id(mail_id: Optional[str]) -> bool:
    """
    判断传入的邮件 ID 是否为历史邮件 ID。

    历史邮件 ID 示例：
      - ev-15BDCC9D75D2B2A48AD6BCED3E5CD9053_2385_16285806
      - mas-15BDCC9D75D2B2A48AD6BCED3E5CD9053_2385_16285806

    在线邮件 ID 示例：
      - 00010002bfab9bd1350c53c9 （24 个十六进制字符）

    判定规则（按需求简化）：
      - 若 ID 为 24 个十六进制字符，则视为“在线邮件”（返回 False）；
      - 否则视为“历史邮件”（返回 True）。
    """
    if not mail_id or not isinstance(mail_id, str):
        return False
    s = mail_id.strip()
    if not s:
        return False
    # 24 位十六进制：在线邮件
    if re.fullmatch(r"[0-9a-fA-F]{24}", s):
        return False
    # 其他任何格式（包括 ev-/mas- 开头）都视为历史邮件
    return True


def validate_recipient_item(item: Optional[str]) -> Tuple[bool, Optional[str]]:
    """
    校验单个收件人/抄送人：仅接受工号或邮箱地址。
    :return: (True, None) 合法；(False, error_msg) 非法。
    """
    if not item or not isinstance(item, str):
        return False, "收件人/抄送人不能为空。"
    s = item.strip()
    if not s:
        return False, "收件人/抄送人不能为空。"
    if is_emp_no(s) or is_email_address(s):
        return True, None
    return False, "to/cc 仅接受工号或邮箱地址，禁止传入姓名、群组名等关键字；请使用 search-users 确认后得到的工号/邮箱再调用发送，不要在发送时再次用关键字搜索。"


def split_email_cli_arg(raw: Optional[str]) -> Tuple[Optional[List[str]], Optional[str]]:
    """
    解析命令行 --to/--cc/--bcc：逗号分隔邮箱；空串或仅空白视为无收件人。
    :return: (邮箱列表, None) 成功；(None, 错误信息) 失败。
    """
    if raw is None or not str(raw).strip():
        return [], None
    parts: List[str] = []
    for seg in str(raw).split(","):
        e = seg.strip()
        if not e:
            continue
        if not is_email_address(e):
            return None, f"非法邮箱地址：{e!r}"
        parts.append(e)
    return parts, None


def _mask_headers_for_log(headers: Optional[dict]) -> dict:
    """复制 headers 并将认证、工号相关键的值脱敏为 ***。"""
    if not headers or not isinstance(headers, dict):
        return {}
    out = dict(headers)
    for k, v in list(out.items()):
        if k and (k.lower() in _MASK_HEADER_KEYS or "token" in k.lower()):
            out[k] = "***"
    return out


def _mask_emp_no_in_value(value: Any) -> Any:
    """递归脱敏：字符串中工号（5～11 位数字）替换为 ***；dict 中 UNO/UID/emp_no 键的值脱敏。"""
    if value is None:
        return None
    if isinstance(value, str):
        return _EMP_NO_RE.sub("***", value)
    if isinstance(value, dict):
        sensitive_keys = frozenset({"UNO", "UID", "emp_no"})
        return {
            k: "***" if (k in sensitive_keys and v) else _mask_emp_no_in_value(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_mask_emp_no_in_value(item) for item in value]
    return value


def log_api_error(
    url: str,
    headers: Optional[dict],
    body: Any,
    response: Any,
    error_msg: Optional[str] = None,
) -> None:
    """
    仅用于 API 出错时记录：写入 url、headers（认证/工号已脱敏）、body、返回信息（工号脱敏）。
    :param url: 请求 URL
    :param headers: 请求头（将脱敏 x-zmail-uid / x-zmail-tid / x-emp-no 等）
    :param body: 请求 body（dict 或 str，含工号处脱敏）
    :param response: 响应内容（dict/str，含工号处脱敏）
    :param error_msg: 可选错误说明
    """
    try:
        h = _mask_headers_for_log(headers)
        body_masked = _mask_emp_no_in_value(body)
        resp_masked = _mask_emp_no_in_value(response)
        parts = [
            "[ERROR] API_ERROR",
            f"url={url}",
            f"headers={json.dumps(h, ensure_ascii=False)}",
            f"body={json.dumps(body_masked, ensure_ascii=False) if isinstance(body_masked, (dict, list)) else str(body_masked)}",
            f"response={json.dumps(resp_masked, ensure_ascii=False) if isinstance(resp_masked, (dict, list)) else str(resp_masked)}",
        ]
        if error_msg:
            parts.append(f"error={error_msg}")
        zmail_log(" | ".join(parts))
    except Exception:
        pass


# ---------- API 参数校验（发往接口前校验格式）----------

# 日期时间格式：API 使用的 P.S / P.E 等为 "YYYY-MM-DD HH:MM:SS"
_DATETIME_FMT = "%Y-%m-%d %H:%M:%S"
_DATE_FMT = "%Y-%m-%d"


def validate_datetime_param(
    value: Optional[str],
    param_name: str,
    allow_date_only: bool = False,
    required: bool = False,
) -> None:
    """
    校验日期/时间参数格式。不合法时抛出 ValueError。
    :param value: 待校验值；None 或空串在 required=False 时视为通过。
    :param param_name: 参数名，用于错误提示。
    :param allow_date_only: 为 True 时也接受 "YYYY-MM-DD"。
    :param required: 为 True 时 value 不能为 None 或空串。
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValueError(f"参数 {param_name} 不能为空")
        return
    s = value.strip() if isinstance(value, str) else str(value)
    if allow_date_only and len(s) == 10:
        try:
            datetime.strptime(s, _DATE_FMT)
            return
        except ValueError:
            pass
    if len(s) >= 19:
        try:
            datetime.strptime(s[:19], _DATETIME_FMT)
            return
        except ValueError:
            pass
    hint = " 或日期格式 (YYYY-MM-DD)" if allow_date_only else ""
    raise ValueError(f"参数 {param_name} 必须是日期时间格式 (YYYY-MM-DD HH:MM:SS){hint}，当前值: {s!r}")


def validate_positive_int(
    value: Any,
    param_name: str,
    min_value: int = 1,
    max_value: Optional[int] = None,
) -> None:
    """校验正整数参数（如 pno、psize、days）。不合法时抛出 ValueError。"""
    if not isinstance(value, int) or value < min_value:
        raise ValueError(f"参数 {param_name} 必须为不小于 {min_value} 的整数，当前值: {value!r}")
    if max_value is not None and value > max_value:
        raise ValueError(f"参数 {param_name} 不能大于 {max_value}，当前值: {value!r}")


def validate_one_of(value: Optional[str], param_name: str, allowed: Tuple[str, ...]) -> None:
    """校验枚举字符串。不合法时抛出 ValueError。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"参数 {param_name} 不能为空，可选值: {allowed}")
    v = value.strip().lower() if isinstance(value, str) else str(value).lower()
    if v not in [a.lower() for a in allowed]:
        raise ValueError(f"参数 {param_name} 必须为其中之一: {allowed!r}，当前值: {value!r}")


def is_email_address(text: Optional[str]) -> bool:
    """
    判断字符串是否为合法邮箱地址（格式与长度符合常见规范）。
    要求：含且仅含一个 @；@ 前 1～64 字符；@ 后为域名且含点、点后至少 2 字符；总长≤254。
    :param text: 待检测字符串
    :return: bool
    """
    if not text or not isinstance(text, str):
        return False
    s = text.strip()
    if len(s) > _EMAIL_MAX_LEN or not s:
        return False
    return bool(_EMAIL_RE.match(s))


def is_valid_schedule_time(
    send_at: Optional[Union[str, int, float]],
) -> Tuple[bool, str]:
    """
    判断指定发送时间是否在有效期内。

    规则：
      1. 必须比当前时间晚至少约 1 小时（允许 60 秒误差，即时间差 >= 3540 秒即通过）
      2. 必须在当前时间起 30 天以内

    用户传入 "YYYY-MM-DD HH:MM" 时秒为 0；为免临界失败，将最早允许时间按分钟向下取整再比较，
    这样例如「11:00」在服务器 10:00:23 时仍可通过（min_ts 为 11:00:23，取整后 11:00:00）。

    :param send_at: 发送时间，支持以下格式：
        - str: "YYYY-MM-DD HH:MM:SS" 或 "YYYY-MM-DD HH:MM"
        - int/float: Unix 时间戳（秒；若 > 1e12 视为毫秒自动转换）
    :return: (bool, str) — (是否有效, 原因说明)
    """
    now = time.time()
    ts = _parse_to_timestamp(send_at)
    if ts is None:
        return False, "时间格式无法解析，请使用 YYYY-MM-DD HH:MM 格式。"

    min_ts = now + (3600 - 60)   # 至少约 1 小时后（允许 60 秒误差，避免时钟/舍入导致临界失败）
    # 按分钟向下取整，使 "YYYY-MM-DD HH:MM"（秒为 0）不会因秒数差被拒
    min_ts_effective = int(min_ts // 60) * 60
    max_ts = now + 30 * 86400    # 最多 30 天内

    if ts < min_ts_effective:
        min_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(min_ts_effective))
        return False, f"发送时间必须比当前时间晚至少 1 小时，最早可设置为 {min_time_str}。"

    if ts > max_ts:
        max_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(max_ts))
        return False, f"发送时间不能超过 30 天，最晚可设置为 {max_time_str}。"

    return True, "有效"


def parse_send_at(send_at: Optional[str]) -> int:
    """
    将 send_at 转为 Unix 时间戳（秒）。用于在指定时间发送。
    支持：None/空 → 当前时间；int/float（>1e12 视为毫秒）；"YYYY-MM-DD HH:MM:SS" 或 "YYYY-MM-DD HH:MM"。
    """
    if send_at is None:
        return int(time.time())
    if isinstance(send_at, (int, float)):
        if send_at > 1e12:
            return int(send_at / 1000)
        return int(send_at)
    if isinstance(send_at, str):
        send_at = send_at.strip()
        if not send_at:
            return int(time.time())
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                t = time.strptime(send_at[:19] if len(send_at) > 16 else send_at + ":00", fmt)
                return int(time.mktime(t))
            except ValueError:
                continue
    return int(time.time())


def _parse_to_timestamp(send_at):
    """
    将各种格式的时间转为 Unix 时间戳（秒），无法解析返回 None。
    """
    if send_at is None:
        return None
    if isinstance(send_at, (int, float)):
        if send_at > 1e12:
            return send_at / 1000
        return float(send_at)
    if isinstance(send_at, str):
        send_at = send_at.strip()
        if not send_at:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                t = time.strptime(send_at[:19] if len(send_at) > 16 else send_at, fmt)
                return time.mktime(t)
            except ValueError:
                continue
    return None


# ─── JSON / 格式化辅助 ──────────────────────────────────────────────────────


def ats_to_list(ats_raw: str) -> List[dict]:
    """将 ATS 字符串（JSON）转为附件项列表（每项为 dict，含 ID、N 等）。"""
    if not ats_raw or not isinstance(ats_raw, str):
        return []
    s = ats_raw.strip()
    if not s:
        return []
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        return []
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if isinstance(obj, dict):
        return [obj]
    return []


def ats_to_exact_attach_list(ats_raw: Any) -> List[Dict[str, Any]]:
    """
    只返回“确切附件”（非正文内联图片）。

    约定：ATS 单项里 I=1 表示正文内联资源（常见为插入正文的图片），这类不应作为“附件列表”返回。
    """
    lst = ats_to_list(ats_raw)  # type: ignore[arg-type]
    if not lst:
        return []
    out: List[Dict[str, Any]] = []
    for item in lst:
        if not isinstance(item, dict):
            continue
        i_val = item.get("I") if "I" in item else item.get("i")
        if str(i_val).strip() == "1":
            continue
        out.append(item)
    return out


def to_json_object(value: Optional[Any]) -> Optional[Union[dict, list]]:
    """将字符串解析为 JSON 对象（dict 或 list），无法解析返回 None。"""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


def format_timestamp(ts: Optional[Union[int, float]]) -> str:
    """将 Unix 时间戳（秒或毫秒）转为 'YYYY-MM-DD HH:MM:SS' 格式字符串。"""
    if not ts:
        return ""
    try:
        ts_num = float(ts)
        if ts_num > 1e12:
            ts_num = ts_num / 1000
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts_num))
    except (ValueError, TypeError, OSError):
        return str(ts)


def format_mt_list(mt_list: Optional[List[Any]]) -> List[str]:
    """将 MT 收件人列表格式化为 ['名字工号', ...] 的字符串列表。"""
    if not mt_list or not isinstance(mt_list, list):
        return []
    result = []
    for item in mt_list:
        if isinstance(item, dict):
            name = item.get("N") or ""
            uno = item.get("UNO") or ""
            result.append(f"{name}{uno}" if (name or uno) else "")
        else:
            result.append(str(item) if item else "")
    return [r for r in result if r]


def display_name_from_user(obj: Optional[Dict[str, Any]]) -> str:
    """
    单条 MF/MT/MC 项展示名：优先姓名+工号，否则姓名/工号，最后邮箱。

    与 `.cursor/skills/oa-zmail-manage/scripts/zmail_reply_forward_base.py` 中 `_display_name` 语义保持一致。
    """
    if not obj or not isinstance(obj, dict):
        return ""
    n = (obj.get("N") or "").strip()
    uno = (obj.get("UNO") or "").strip()
    e = (obj.get("E") or "").strip()
    if n and uno:
        return f"{n}{uno}"
    if n:
        return n
    if uno:
        return uno
    return e or ""


def display_names_from_user_objs(
    raw: Optional[Union[str, Dict[str, Any], List[Any]]],
    sep: str = "；",
) -> str:
    """
    多人员展示字符串，默认使用中文分号 `；` 连接。

    输入 raw 可为：
    - list[dict]（如 MT/MC）
    - dict（单个对象）
    - str（JSON 字符串，内容为 list/dict）
    """
    obj = to_json_object(raw) if isinstance(raw, str) else raw
    if obj is None:
        return ""
    if isinstance(obj, dict):
        return display_name_from_user(obj)
    if not isinstance(obj, list):
        return ""
    out: List[str] = []
    for item in obj:
        if isinstance(item, dict):
            s = display_name_from_user(item)
            if s:
                out.append(s)
    return sep.join(out)


# ─── 日期范围 / MF 解析（原 utils）────────────────────────────────────────────

# 查找邮件：结束日期须在当前时间往前推半年内，超过则终止并提示
SEARCH_MAX_DAYS = 180
SEARCH_BEYOND_HALF_YEAR_NOTICE = "半年前的邮件点击历史邮件查询"


def validate_search_end_date(end_date_str: Optional[str]) -> Tuple[bool, Optional[str]]:
    """
    校验查找邮件的结束日期是否在半年内（结束日期 >= 当前日期 - 6 个月）。
    若不符合返回 (False, SEARCH_BEYOND_HALF_YEAR_NOTICE)；符合返回 (True, None)。
    """
    if not end_date_str or not str(end_date_str).strip():
        return True, None
    try:
        end_d = datetime.strptime(str(end_date_str).strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return True, None
    cutoff = (datetime.now() - timedelta(days=SEARCH_MAX_DAYS)).date()
    if end_d < cutoff:
        return False, SEARCH_BEYOND_HALF_YEAR_NOTICE
    return True, None


def user_from_mf(mf_str: Optional[str]) -> str:
    """从 MF 的 JSON 字符串中解析 UNO 和 N，返回 'N' + 'UNO'，如 王翔0668000426。"""
    if not mf_str or not isinstance(mf_str, str):
        return ""
    try:
        mf = json.loads(mf_str)
        if not isinstance(mf, dict):
            return ""
        n = mf.get("N") or mf.get("Y") or ""
        uno = mf.get("UNO")
        if uno is None or uno == "":
            return n
        return f"{n}{uno}"
    except (json.JSONDecodeError, TypeError):
        return ""


def _normalize_start_end(start_dt: datetime, end_dt: datetime) -> tuple:
    """开始大于结束则以较大者为准，开始和结束设为同一天。返回 (start_str, end_str)。"""
    if start_dt > end_dt:
        end_dt = start_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    return (
        start_dt.strftime("%Y-%m-%d"),
        end_dt.strftime("%Y-%m-%d"),
    )


def resolve_date_range(
    days: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    sender: str = "",
    search_days: Optional[int] = None,
) -> Tuple[str, str]:
    """
    根据 days 或 start_date/end_date 解析出实际使用的起止日期。
    优先级：start_date（可配 end_date 或仅开始）> days。
    返回 (resolved_start_date, resolved_end_date)。
    """
    default_days = search_days if search_days is not None else SEARCH_DAYS
    has_sender = bool(sender and sender.strip())

    if start_date and start_date.strip():
        sd = start_date.strip()
        if len(sd) > 10:
            start_dt = datetime.strptime(sd, "%Y-%m-%d %H:%M:%S")
        else:
            start_dt = datetime.strptime(sd, "%Y-%m-%d").replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        if end_date and end_date.strip():
            ed = end_date.strip()
            if len(ed) > 10:
                end_dt = datetime.strptime(ed, "%Y-%m-%d %H:%M:%S")
            else:
                end_dt = datetime.strptime(ed, "%Y-%m-%d").replace(
                    hour=23, minute=59, second=59, microsecond=999999
                )
        else:
            end_dt = datetime.now().replace(
                hour=23, minute=59, second=59, microsecond=999999
            )
        return _normalize_start_end(start_dt, end_dt)

    requested_days = days if days is not None else default_days
    if has_sender:
        actual_days = requested_days
    else:
        actual_days = default_days if (days is not None and days > default_days) else requested_days
    now = datetime.now()
    end_dt = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    start_dt = (now - timedelta(days=actual_days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return _normalize_start_end(start_dt, end_dt)
