#!/usr/bin/env python3
"""
UDS 凭据 + zMail 会话 Token 一体化模块。

1. 从环境变量获取工号与 Token（coclaw_empno, coclaw_token），并获取本机 IP。
2. 使用上述凭据调用 zmail SysTokenLogin，获取 zmail 会话 token (D.T)。
"""

import json
import logging
import os
import socket
import time
import uuid
import urllib.parse
import requests
from typing import Dict, Optional, Tuple

from api_endpoints import get_zmail_services_url
from request_client import request_post

logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 进程内缓存：保证一次 main.py 运行期间最多登录一次（SysTokenLogin）。
# 由于本技能以 CLI 单进程为主，模块级缓存足够；后续如需可加锁或做失效策略。
_ZMAIL_TOKEN_CACHE: Optional[Tuple[str, str, Optional[str], str, str, str]] = None


def clear_zmail_token_cache() -> None:
    """清理进程内 zMail token 缓存（仅影响当前进程）。"""
    global _ZMAIL_TOKEN_CACHE
    _ZMAIL_TOKEN_CACHE = None


# ---------- UDS 凭据 ----------

def _get_local_ip() -> str:
    """获取本机 IP（用于外网出口的 IPv4 地址）"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return ""


def _fetch_empno_and_token() -> Optional[Dict]:
    """
    从环境变量获取工号和 Token（与 get_token.py 一致）。

    Returns:
        dict: {"empNo": "...", "token": "...", "localIp": "..."} 或 None
    """
    empno = os.getenv("coclaw_empno")
    token = os.getenv("coclaw_token")

    if not empno or not token:
        logger.error("环境变量 coclaw_empno 或 coclaw_token 未设置")
        return None

    return {
        "empNo": empno,
        "token": token,
        "localIp": '127.0.0.1',
    }


def get_emp_no() -> str:
    """从环境变量获取当前用户工号（coclaw_empno）。未设置时返回空字符串。"""
    return (os.getenv("coclaw_empno") or "").strip()


# ---------- zMail 会话 Token ----------


def _is_domain(ip_or_host: str) -> bool:
    """判断为域名（含字母）而非纯 IPv4 地址。"""
    if not ip_or_host or not isinstance(ip_or_host, str):
        return False
    return any(c.isalpha() for c in ip_or_host)


def _context_from_iplist(resp: dict) -> str:
    """
    从响应 FD 数组中 K="ServerInfo" 的 V 中解析 IPList，取 IP 为域名的项对应的 CT 作为 context。
    未找到则返回空字符串。
    """
    fd = resp.get("FD") or []
    if not isinstance(fd, list) or len(fd) == 0 or not isinstance(fd[0], dict):
        return ""
    v_str = ""
    for item in fd:
        if not isinstance(item, dict):
            continue
        if str(item.get("K") or "").strip() == "ServerInfo":
            v_str = item.get("V") or ""
            break
    if not v_str:
        return ""
    try:
        server_info = json.loads(v_str)
        iplist_str = server_info.get("IPList")
        if not iplist_str:
            return ""
        iplist = json.loads(iplist_str)
        if not isinstance(iplist, list):
            return ""
        for item in iplist:
            if not isinstance(item, dict):
                continue
            ip_val = item.get("IP") or ""
            if _is_domain(ip_val):
                ct = item.get("CT")
                if ct:
                    return str(ct).strip()
    except (json.JSONDecodeError, TypeError):
        pass
    return ""


def get_zmail_token(
    context: Optional[str] = None,
) -> Tuple[str, str, Optional[str], str, str, str]:
    """
    使用 UDS 工号与 Token 调用 zmail SysTokenLogin 接口，获取 zmail 的 T（会话 token）。

    请求体中的 IP 使用 UDS 返回的 localIp，UNO 为工号，TK 为 UDS token，ID 为本次请求的唯一标识。
    响应为 URL 编码的 JSON，解析后取 D.T 作为 zmail token 返回。

    Args:
        context: 机房/地域标识，用于拼入请求 URL 路径；默认 None 表示不使用上下文。

    Returns:
        tuple: (emp_no, zmail_token, context, emp_uin, is_test, resid)，
            工号、zmail 会话 token、本次使用的 context（None 表示未指定）、从 D.F 中解析得到的 UID（字符串，缺失时为空串）、
            从 D.F 中解析得到的测试标识（IPU: N=>0，Y=>1；缺失时默认 0）、
            从 D.F 中解析得到的 resid（UA 字段；缺失时为空串）

    Raises:
        RuntimeError: UDS 不可用或 SysTokenLogin 请求失败/返回未成功
    """
    global _ZMAIL_TOKEN_CACHE
    if _ZMAIL_TOKEN_CACHE is not None:
        return _ZMAIL_TOKEN_CACHE

    services_url = get_zmail_services_url(context)

    cred = _fetch_empno_and_token()
    if not cred:
        raise RuntimeError(
            "无法连接到 UDS 客户端，请确认 UDS 桌面云客户端正在运行。"
        )
    emp_no = cred["empNo"]
    token = cred["token"]
    local_ip = cred.get("localIp", "")
    req_id = f"1020060072026013003__zMail_SysTokenLogin_{int(time.time() * 1000)}_{uuid.uuid4().hex}"
    url = f"{services_url}?1=1&c=SysTokenLogin&reqid={urllib.parse.quote(req_id)}"

    login_param_inner = {
        "time_zone": "8",
        "time_zone_code": "Asia/Shanghai",
        "zmail_client_version": "",
        "zmail_client_type": "web",
        "zmail_os_type": ""
    }
    d_obj = {
        "login_param": json.dumps(login_param_inner),
        "zmail_client_version": "",
        "time_zone": "8",
        "time_zone_code": "Asia/Shanghai",
        "IP": local_ip,
        "TK": token,
        "UNO": emp_no,
        "NT": "1"
    }
    d_str = json.dumps(d_obj, ensure_ascii=False)
    dt_value = time.strftime("%Y-%m-%d+%H:%M:%S", time.localtime())

    payload = {
        "C": "SysTokenLogin",
        "T": "",
        "L": "2052",
        "P": None,
        "D": d_str,
        "F": [
            {"K": "IP", "V": local_ip},
            {"K": "TK", "V": token},
            {"K": "UNO", "V": emp_no},
            {"K": "NT", "V": "1"},
            {"K": "ZV", "V": "20191128"}
        ],
        "VER": "1.0",
        "DT": dt_value,
        "FD": [{"K": "F", "V": "1"}],
        "ID": req_id
    }
    body_str = json.dumps(payload, ensure_ascii=False)
    body_bytes = urllib.parse.quote(body_str, safe="").encode("utf-8")

    headers = {
        "Accept": "text/plain, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "x-requested-with": "XMLHttpRequest",
        "Referer": "https://zmail.zte.com.cn"
    }

    try:
        resp = request_post(url, data=body_bytes, headers=headers, timeout=30)
        raw = resp.content.decode("utf-8", errors="replace")
        if not (200 <= resp.status_code < 300):
            raise RuntimeError(
                f"zmail SysTokenLogin 请求失败 (HTTP {resp.status_code}): {raw}"
            )
    except requests.exceptions.RequestException as e:
        reason = getattr(e, "reason", None) or str(e)
        raise RuntimeError(f"zmail 请求网络错误: {reason}")

    try:
        decoded = urllib.parse.unquote(raw)
        resp = json.loads(decoded)
    except json.JSONDecodeError:
        resp = json.loads(raw)

    if not resp.get("S"):
        msg = resp.get("M") or resp.get("C") or "登录失败"
        if resp.get("C") in ("1100075", "1100076"):
            retry_context = _context_from_iplist(resp)
            if retry_context:
                return get_zmail_token(context=retry_context)
        raise RuntimeError(f"zmail SysTokenLogin 未成功: {msg}")

    d = resp.get("D") or {}
    zmail_t = d.get("T")
    if not zmail_t:
        raise RuntimeError("zmail 响应中未包含 D.T (zmail token)")
    # 从 D.F 对象中解析 UID 字段，作为 emp_uin 返回；若不存在则返回空字符串
    emp_uin = ""
    # 从 D.F 对象中解析 IPU 字段，映射为 is_test（N=>0，Y=>1）
    is_test = "0"
    # 从 D.F 对象中解析 UA 字段，作为 resid（作为请求头需要）
    # 默认值为空：只有在响应中解析到 UA 字段时才填充到请求头 resid
    resid = ""
    if isinstance(d, dict):
        f_obj = d.get("F")
        if isinstance(f_obj, dict):
            uid_val = f_obj.get("UID") or f_obj.get("uid")
            if uid_val is not None:
                emp_uin = str(uid_val).strip()
            ipu_val = f_obj.get("IPU") or f_obj.get("ipu")
            if ipu_val is not None:
                ipu_norm = str(ipu_val).strip().upper()
                if ipu_norm == "Y":
                    is_test = "1"
                elif ipu_norm == "N":
                    is_test = "0"
            ua_val = f_obj.get("UA") or f_obj.get("ua")
            if ua_val is not None:
                ua_str = str(ua_val).strip()
                # 尝试将 UA 标准化为数字字符串（便于后续 header 使用）
                try:
                    resid = str(int(ua_str))
                except (ValueError, TypeError):
                    resid = ua_str
    # 成功时：若入参 context 已有则用入参，否则从 resp 的 FD（ServerInfo.IPList）解析
    resolved_context = context or _context_from_iplist(resp)
    _ZMAIL_TOKEN_CACHE = (emp_no, zmail_t, resolved_context, emp_uin, is_test, resid)
    return _ZMAIL_TOKEN_CACHE
