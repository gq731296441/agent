# -*- coding: utf-8 -*-
"""
icenter-msg 公共工具：用户认证、搜索用户、搜索群组、发送消息等。
"""
import base64
import json
import logging
import os
import socket
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.request import HTTPSHandler, ProxyHandler, build_opener
from typing import Any, Dict, List, Optional, Tuple

_logger = logging.getLogger("icenter-msg")

# 内网 HTTPS 常缺完整证书链；跳过校验以避免 CERTIFICATE_VERIFY_FAILED（与 requests verify=False 一致）
_SSL_SKIP_VERIFY = ssl._create_unverified_context()

# ── 环境配置 ─────────────────────────────────────────────────────────────────
COCLAW_ENV = os.environ.get('coclaw_env', '').lower()
IS_TEST_ENV = COCLAW_ENV == 'test'

_PROD_MSG_URL = "https://icentermsg.dt.zte.com.cn"
_TEST_MSG_URL = "https://icentermsg.test.zte.com.cn"

_PROD_API_URL = "https://icenterapi.zte.com.cn"
_TEST_API_URL = "https://icenter-systest.test.zte.com.cn"

def get_msg_base_url() -> str:
    """
    获取消息接口的 base URL。

    Returns:
        测试环境返回 icentermsg.test.zte.com.cn，生产环境返回 icentermsg.dt.zte.com.cn
    """
    return _TEST_MSG_URL if IS_TEST_ENV else _PROD_MSG_URL

def get_api_base_url() -> str:
    """
    获取用户 API 的 base URL。

    Returns:
        测试环境返回 icenter-systest.test.zte.com.cn，生产环境返回 icenterapi.zte.com.cn
    """
    return _TEST_API_URL if IS_TEST_ENV else _PROD_API_URL

# 常量
SIP_HEAD_FLAG = "sip:"
AT_FLAG = "@"
URI_SUFFIX_STR = "zte.com.cn"
GURI_SUFFIX_STR = "gm.zte.com.cn"
# 统一发送消息接口（单聊/群聊合并），可通过环境变量 ICENTER_SEND_MESSAGE_URL 覆盖
SEND_MESSAGE_API_URL = os.environ.get(
    "ICENTER_SEND_MESSAGE_URL",
    "http://127.0.0.1:38089/iCenter/sendMessage",
)

SEARCH_USER_API_URL = f"{get_api_base_url()}/zte-km-icenter-addresearch/user/plain/docs/search"
SEARCH_GROUP_API_URL = f"{get_msg_base_url()}/zte-icenter-msg-bff/services/extGroup/searchGroup"
IS_GROUP_MEMBER_API_URL = f"{get_msg_base_url()}/zte-icenter-msg-bff/services/extGroup/public-group/global/isGroupmember"

TIMEOUT_SEC = 30
SEARCH_TIMEOUT_SEC = 15
MAX_AT_COUNT = 20

# 群聊内容中多个 @ 人名之间的分隔符（与 Go 侧保持一致，Unicode FIGURE SPACE U+2006）
AT_SEPARATOR = "\u2006"

# iCenter 本地可用性检查：进程名与侦听端口范围
ICENTER_HOST = "127.0.0.1"
ICENTER_PORTS = list(range(38089, 38099))  # 38089~38098
ICENTER_PROCESS_NAME_WIN = "iCenter.exe"
ICENTER_PROCESS_NAME_LINUX = "icenter"
MSG_SKILL_UNAVAILABLE = "技能不可用，需要启动 iCenter 才可以使用。"
MSG_VERSION_UPGRADE = "版本不对，需要升级 iCenter 版本。"

# 可用性检查时检测到的侦听端口，供 sendMessage 使用
_detected_icenter_port: Optional[int] = None


def is_icenter_process_running() -> bool:
    """
    检查 iCenter 进程是否在运行。

    Returns:
        如果进程在运行返回 True，否则返回 False
    """
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {ICENTER_PROCESS_NAME_WIN}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            # 直接对 bytes 进行操作，无需解码
            out_bytes = (result.stdout or b'') + (result.stderr or b'')
            # 注意：tasklist 输出中的 "No tasks" 也是 ASCII
            return b"iCenter.exe" in out_bytes and b"No tasks" not in out_bytes
        except Exception as e:
            _logger.debug("检查 iCenter 进程异常: %s", e)
            return False
    if sys.platform == "linux":
        try:
            result = subprocess.run(
                ["pgrep", "-x", ICENTER_PROCESS_NAME_LINUX],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception) as e:
            _logger.debug("检查 iCenter 进程异常: %s", e)
            return False
    return True


def is_icenter_port_listening() -> Tuple[bool, Optional[int]]:
    """
    检查 127.0.0.1 上是否有端口 38089~38098 任一处于侦听。

    Returns:
        (是否侦听, 侦听端口或 None)
    """
    for port in ICENTER_PORTS:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.5)
            sock.connect((ICENTER_HOST, port))
            sock.close()
            return True, port
        except (socket.error, OSError):
            continue
    return False, None


def check_icenter_available() -> Tuple[bool, str]:
    """
    检查本机 iCenter 是否可用（进程 + 端口）。

    若检测到 38089~38098 中某端口侦听，会记录该端口供后续 sendMessage 使用。

    Returns:
        (可用, 错误信息)，可用时错误信息为空字符串
    """
    global _detected_icenter_port
    _detected_icenter_port = None
    if not is_icenter_process_running():
        proc_name = ICENTER_PROCESS_NAME_WIN if sys.platform == "win32" else ICENTER_PROCESS_NAME_LINUX
        return False, MSG_SKILL_UNAVAILABLE
    listening, port = is_icenter_port_listening()
    if not listening:
        return False, MSG_SKILL_UNAVAILABLE
    _detected_icenter_port = port
    return True, ""


def get_send_message_url() -> str:
    """
    获取 sendMessage 接口 URL。

    Returns:
        优先环境变量，其次使用可用性检查时检测到的 127.0.0.1 端口，最后使用默认地址
    """
    env_url = os.environ.get("ICENTER_SEND_MESSAGE_URL", "").strip()
    if env_url:
        return env_url
    if _detected_icenter_port is not None:
        return f"http://{ICENTER_HOST}:{_detected_icenter_port}/iCenter/sendMessage"
    return SEND_MESSAGE_API_URL


class UserInfo:
    """用户信息（与 Go UserInfo 对应）"""
    def __init__(
        self,
        name: str = "",
        name_en: str = "",
        emp_no: str = "",
        employee_type: int = 0,
        dept_name: str = "",
        email: str = "",
        dept_full_name: str = "",
        dept_name_en: str = "",
        phone: str = "",
        head_icon: str = "",
        union_id: str = "",
        signature: str = "",
    ):
        self.name = name
        self.name_en = name_en
        self.emp_no = emp_no
        self.employee_type = employee_type
        self.dept_name = dept_name
        self.email = email
        self.dept_full_name = dept_full_name
        self.dept_name_en = dept_name_en
        self.phone = phone
        self.head_icon = head_icon
        self.union_id = union_id
        self.signature = signature


class GroupInfoItem:
    """群组信息项（与 Go GroupInfoItem 对应）"""
    def __init__(
        self,
        admin_id: str = "",
        group_name: str = "",
        group_uri: str = "",
        lastmsgstamp: int = 0,
    ):
        self.admin_id = admin_id
        self.group_name = group_name
        self.group_uri = group_uri
        self.lastmsgstamp = lastmsgstamp


class SearchResult:
    """用户搜索结果（与 Go SearchResult 对应）"""
    def __init__(
        self,
        status: str = "error",
        keyword: str = "",
        total: int = 0,
        users: Optional[List[UserInfo]] = None,
        message: str = "",
    ):
        self.status = status
        self.keyword = keyword
        self.total = total
        self.users = users if users is not None else []
        self.message = message


class AtItem:
    """@ 项：工号（或 all）与展示名"""
    def __init__(
        self,
        emp_no: str = "",
        name: str = "",
    ):
        self.emp_no = emp_no
        self.name = name


def get_user_auth_info() -> Tuple[str, str]:
    """
    获取用户认证信息（从环境变量）。

    优先 coclaw_empno / coclaw_token，其次 EMP_NO / AUTH_VALUE。

    Returns:
        (emp_no, token)

    Raises:
        ValueError: 无法获取用户认证信息时抛出
    """
    _logger.debug("获取用户认证信息（从环境变量）")
    emp_no = os.environ.get("coclaw_empno", "")
    token = os.environ.get("coclaw_token", "")
    if not emp_no or not token:
        if not emp_no:
            emp_no = os.environ.get("EMP_NO", "")
        if not token:
            token = os.environ.get("AUTH_VALUE", "")
    if not emp_no or not token:
        _logger.error("无法获取用户账号和Token，请检查环境变量 coclaw_empno/coclaw_token 或 EMP_NO/AUTH_VALUE")
        raise ValueError("无法获取用户账号和Token，请检查环境变量配置")
    _logger.debug("用户认证信息获取成功 emp_no=%s", emp_no)
    return emp_no, token


def to_user_id(user_uri: str) -> str:
    """
    从 sip:xxx@domain 中提取用户 ID/工号。

    Args:
        user_uri: SIP URI 格式的字符串

    Returns:
        用户 ID/工号字符串
    """
    if not user_uri:
        return ""
    user_uri = user_uri.strip().strip('"')
    if user_uri.startswith(SIP_HEAD_FLAG):
        user_uri = user_uri[len(SIP_HEAD_FLAG):]
    idx = user_uri.find(AT_FLAG)
    if idx != -1:
        return user_uri[:idx]
    return user_uri


def to_sip_uri(in_user_id: str) -> str:
    """
    将工号转为 sip:userId@zte.com.cn 格式。

    Args:
        in_user_id: 工号字符串

    Returns:
        SIP URI 格式字符串
    """
    if not in_user_id:
        return in_user_id
    if in_user_id.startswith("sip:"):
        return in_user_id
    user_id = to_user_id(in_user_id)
    return f"{SIP_HEAD_FLAG}{user_id}{AT_FLAG}{URI_SUFFIX_STR}"


def to_guri_uri(in_user_id: str) -> str:
    """
    将工号/群ID转为 sip:xxx@gm.zte.com.cn 格式。

    Args:
        in_user_id: 工号或群ID字符串

    Returns:
        群组 SIP URI 格式字符串
    """
    if not in_user_id:
        return in_user_id
    if in_user_id.startswith("sip:"):
        return in_user_id
    user_id = to_user_id(in_user_id)
    return f"{SIP_HEAD_FLAG}{user_id}{AT_FLAG}{GURI_SUFFIX_STR}"


def _parse_at_item(s: str) -> AtItem:
    """
    解析单个 @ 项：all | 名字+工号 | 工号。

    Args:
        s: @ 项字符串

    Returns:
        AtItem 对象
    """
    s = (s or "").strip()
    if not s:
        return AtItem()
    if s.lower() == "all":
        return AtItem(emp_no="all", name="所有人")
    # 从末尾找连续数字作为工号
    i = len(s)
    while i > 0 and s[i - 1].isdigit():
        i -= 1
    emp_no = s[i:]
    name = s[:i].strip()
    return AtItem(emp_no=emp_no, name=name)


def parse_at_list(at_list: str) -> List[AtItem]:
    """
    将 | 或 , 分割的 at 字符串解析为 AtItem 列表。

    Args:
        at_list: @ 列表字符串，用 | 或 , 分隔

    Returns:
        AtItem 列表，最多 MAX_AT_COUNT 个
    """
    if not at_list:
        return []
    # 同时支持 | 和 , 作为分隔符，先统一成 | 再分割
    normalized = at_list.replace(",", "|")
    parts = normalized.split("|")
    items = []
    for p in parts:
        it = _parse_at_item(p)
        if it.emp_no:
            items.append(it)
            if len(items) >= MAX_AT_COUNT:
                break
    return items


def build_at_for_send_message(at_list: str) -> str:
    """
    将 @ 列表转为统一发送接口 msgProperty.at 值。

    Args:
        at_list: @ 列表字符串

    Returns:
        格式：姓名+工号,姓名+工号,...,all（逗号分隔）
    """
    items = parse_at_list(at_list)
    if not items:
        return ""
    parts = []
    for it in items:
        if it.emp_no and it.emp_no.lower() == "all":
            parts.append("all")
        else:
            parts.append(f"{it.name or ''}{it.emp_no or ''}")
    return ",".join(parts)


def parse_at_list_with_resolve(
    at_list: str,
    current_emp_no: str,
    token: str,
    group_id: Optional[str] = None,
) -> List[AtItem]:
    """
    解析 at 列表，并对仅含工号（无姓名）的项用工号调用搜索接口解析姓名。

    处理规则：
    - all 不查询，直接保留
    - 非 all 项在群聊场景下会检查是否群成员，不在群中则忽略
    - 已带姓名且通过群成员检查的项直接保留
    - 仅工号的项，通过搜索接口获取姓名

    Args:
        at_list:        @ 列表字符串
        current_emp_no: 当前用户工号
        token:          认证令牌
        group_id:       群 ID（可选，用于群成员检查）

    Returns:
        解析后的 AtItem 列表
    """
    raw_items = parse_at_list(at_list)
    if not raw_items:
        return []
    group_uri = ""
    if group_id:
        group_uri = group_id if group_id.startswith("sip:") else to_guri_uri(group_id)
    resolved: List[AtItem] = []
    for it in raw_items:
        if not it.emp_no:
            continue
        # all 直接保留
        if it.emp_no.lower() == "all":
            resolved.append(it)
        else:
            # 非 all 需检查是否在群中；校验失败或不在群中均忽略
            if group_uri:
                in_group, check_err = is_group_member(
                    current_emp_no,
                    token,
                    it.emp_no,
                    [group_uri],
                )
                if check_err:
                    _logger.warning(
                        "校验群成员失败，保留该@项 emp_no=%s group=%s err=%s",
                        it.emp_no,
                        group_uri,
                        check_err,
                    )
                elif not in_group:
                    _logger.info("用户不在群中，忽略该@项 emp_no=%s group=%s", it.emp_no, group_uri)
                    continue
            # 已有姓名直接保留
            if it.name:
                resolved.append(it)
                if len(resolved) >= MAX_AT_COUNT:
                    break
                continue
            # 仅工号无姓名：用工号查姓名
            res = search_users(current_emp_no, token, it.emp_no)
            if res is None or res.status != "success" or not res.users:
                continue
            u = res.users[0]
            it.name = u.name
            resolved.append(it)
        if len(resolved) >= MAX_AT_COUNT:
            break
    return resolved


def build_at_property_from_items(items: List[AtItem]) -> str:
    """
    根据已解析的 AtItem 列表生成 msgProperty.at 值。

    格式：多个 at 工号或 all 以 "," 分割，每个为 "工号#位置" 或 "all#位置"，位置从 1 递增。

    Args:
        items: AtItem 列表

    Returns:
        msgProperty.at 值字符串
    """
    if not items:
        return ""
    parts: List[str] = []
    pos = 1
    for it in items:
        if not it.emp_no:
            continue
        parts.append(f"{it.emp_no}")
        pos += 1
    return ",".join(parts)


def build_at_prefix_from_items(items: List[AtItem]) -> str:
    """
    根据已解析的 AtItem 列表生成群聊内容前的 @ 前缀。

    仅对有展示名的项按顺序拼接：@人名1<空格><分隔符>@人名2<空格>...<分隔符>，末尾也带分隔符；all 展示为 "所有人"，无名字的项不出现。分隔符为 Unicode U+2006。

    Args:
        items: AtItem 列表

    Returns:
        @ 前缀字符串
    """
    if not items:
        return ""
    parts: List[str] = []
    for it in items:
        if not it.name:
            continue
        parts.append(f"{AT_FLAG}{it.name} ")
    if not parts:
        return ""
    return AT_SEPARATOR.join(parts) + AT_SEPARATOR


def _resolve_current_user_name(current_emp_no: str, token: str) -> str:
    """
    尝试解析当前登录人的姓名。

    优先用工号搜索自己的用户信息；失败时返回空字符串。

    Args:
        current_emp_no: 当前用户工号
        token:          认证令牌

    Returns:
        用户姓名，解析失败时返回空字符串
    """
    if not current_emp_no:
        return ""
    res = search_users(current_emp_no, token, current_emp_no)
    if res is None or res.status != "success" or not res.users:
        return ""
    return res.users[0].name or ""


def _get_string(m: Dict[str, Any], key: str) -> str:
    """
    从 map 中安全取字符串。

    Args:
        m:   字典对象
        key: 键名

    Returns:
        字符串值，键不存在时返回空字符串
    """
    v = m.get(key)
    if v is None:
        return ""
    return str(v) if not isinstance(v, str) else v


def _get_int(m: Dict[str, Any], key: str) -> int:
    """
    从 map 中安全取 int（支持 int/float/str）。

    Args:
        m:   字典对象
        key: 键名

    Returns:
        int 值，失败时返回 0
    """
    v = m.get(key)
    if v is None:
        return 0
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    try:
        s = str(v).strip()
        if s == "":
            return 0
        return int(float(s))
    except Exception:
        return 0


def search_users(emp_no: str, token: str, keyword: str) -> SearchResult:
    """
    根据关键字搜索人员。

    Args:
        emp_no:  查询人工号
        token:   认证令牌
        keyword: 搜索关键字

    Returns:
        SearchResult 对象，status 为 success 时 users 有数据
    """
    _logger.info("搜索用户 keyword=%s", keyword)
    headers = {
        "Accept": "application/json, text/plain",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://i.zte.com.cn",
        "Pragma": "no-cache",
        "Referer": "https://i.zte.com.cn/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        "X-Auth-Value": token,
        "X-Emp-No": emp_no,
        "X-Lang-Id": "zh_CN",
    }
    payload = {
        "employeeShortId": emp_no,
        "enableLabel": True,
        "keyword": keyword,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        SEARCH_USER_API_URL,
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            req, timeout=SEARCH_TIMEOUT_SEC, context=_SSL_SKIP_VERIFY
        ) as resp:
            if resp.status != 200:
                _logger.error("搜索用户 HTTP 错误 status=%s keyword=%s", resp.status, keyword)
                return SearchResult(
                    status="error",
                    message=f"HTTP 错误 [{resp.status}]",
                )
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        _logger.error("搜索用户 HTTPError code=%s keyword=%s", e.code, keyword, exc_info=True)
        return SearchResult(status="error", message=f"HTTP 错误 [{e.code}]")
    except urllib.error.URLError as e:
        _logger.error("搜索用户 URLError reason=%s keyword=%s", e.reason, keyword, exc_info=True)
        return SearchResult(status="error", message=f"请求失败: {e.reason}")
    except Exception as e:
        _logger.exception("搜索用户异常 keyword=%s: %s", keyword, e)
        return SearchResult(status="error", message=f"请求失败: {e}")

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as e:
        _logger.error("搜索用户响应非合法 JSON keyword=%s: %s", keyword, e)
        return SearchResult(status="error", message="API 返回的不是有效的 JSON")

    code_obj = result.get("code")
    if not isinstance(code_obj, dict):
        _logger.error("搜索用户 API 返回格式错误：缺少 code 字段 keyword=%s", keyword)
        return SearchResult(status="error", message="API 返回格式错误：缺少 code 字段")
    code = code_obj.get("code", "")
    if code != "0000":
        msg = code_obj.get("msg") or "未知错误"
        _logger.error("搜索用户 API 业务错误 code=%s msg=%s keyword=%s", code, msg, keyword)
        return SearchResult(status="error", message=str(msg))

    bo = result.get("bo")
    if not isinstance(bo, list):
        _logger.error("搜索用户 API 返回格式错误：缺少 bo 字段 keyword=%s", keyword)
        return SearchResult(status="error", message="API 返回格式错误：缺少 bo 字段")

    users_data: List[UserInfo] = []
    for user_item in bo:
        if not isinstance(user_item, dict):
            continue
        user_map = user_item
        user_info = UserInfo(
            name=_get_string(user_map, "name"),
            name_en=_get_string(user_map, "nameEn"),
            emp_no=_get_string(user_map, "employeeNO"),
            employee_type=_get_int(user_map, "employeeType"),
            email=_get_string(user_map, "email"),
            dept_name=_get_string(user_map, "deptName"),
            dept_full_name=_get_string(user_map, "deptFullName"),
            dept_name_en=_get_string(user_map, "deptNameEn"),
            head_icon=_get_string(user_map, "headIcon"),
            union_id=_get_string(user_map, "unionID"),
            signature=_get_string(user_map, "signature"),
        )
        for contact_item in user_map.get("contacts") or []:
            if not isinstance(contact_item, dict):
                continue
            if _get_string(contact_item, "type") == "phone":
                user_info.phone = _get_string(contact_item, "value")
                break
        users_data.append(user_info)

    # 二次过滤：name / nameEn / emp_no 包含 keyword
    # 已按需求关闭精确匹配，保留 API 原始返回结果。
    # filtered = [
    #     u
    #     for u in users_data
    #     if keyword in (u.name or "") or keyword in (u.name_en or "") or keyword in (u.emp_no or "")
    # ]
    _logger.info("搜索用户成功 keyword=%s 匹配数=%s", keyword, len(users_data))
    return SearchResult(
        status="success",
        keyword=keyword,
        total=len(users_data),
        users=users_data,
    )


def search_group(groupname: str) -> Tuple[List[GroupInfoItem], Optional[str]]:
    """
    根据群名称搜索群组。

    需要先获取认证信息。

    Args:
        groupname: 群名称关键字

    Returns:
        (groups, error_message)，成功时 error_message 为 None
    """
    _logger.info("搜索群组 groupname=%s", groupname)
    emp_no, token = get_user_auth_info()
    uri = to_sip_uri(emp_no)
    headers = {
        "Cache-Control": "no-cache",
        "Content-Type": "application/json",
        "Terminal-Type": "Web@Desktop_999_ID1004",
        "uri": uri,
        "X-Emp-No": emp_no,
        "X-Auth-Value": token,
        "X-Origin-ServiceName": "zte-icenter-msg-coclaw",
        "authKey": "msgSkill",
    }
    payload = {"userID": emp_no, "groupname": groupname}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        SEARCH_GROUP_API_URL,
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            req, timeout=TIMEOUT_SEC, context=_SSL_SKIP_VERIFY
        ) as resp:
            if resp.status != 200:
                _logger.error("搜索群组 HTTP 错误 status=%s groupname=%s", resp.status, groupname)
                return [], f"请求失败，状态码: {resp.status}"
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        _logger.error("搜索群组 HTTPError code=%s groupname=%s", e.code, groupname, exc_info=True)
        return [], f"请求失败，状态码: {e.code}"
    except urllib.error.URLError as e:
        _logger.error("搜索群组 URLError reason=%s groupname=%s", e.reason, groupname, exc_info=True)
        return [], f"请求失败: {e.reason}"
    except Exception as e:
        _logger.exception("搜索群组异常 groupname=%s: %s", groupname, e)
        return [], f"请求失败: {e}"

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as e:
        _logger.error("搜索群组响应非合法 JSON groupname=%s: %s", groupname, e)
        return [], f"解析响应失败: {e}"

    code_obj = result.get("code") or {}
    c = code_obj.get("code") if isinstance(code_obj, dict) else None
    if str(c) != "200":
        msg = code_obj.get("msg", "未知错误") if isinstance(code_obj, dict) else "未知错误"
        _logger.error("搜索群组 API 业务错误 code=%s msg=%s groupname=%s", c, msg, groupname)
        return [], f"搜索群组失败，错误码: {c}, 错误信息: {msg}"

    bo = result.get("bo")
    if not isinstance(bo, list):
        _logger.error("搜索群组 API 返回格式错误：缺少 bo 字段 groupname=%s", groupname)
        return [], "API 返回格式错误：缺少 bo 字段"

    groups = []
    for item in bo:
        if not isinstance(item, dict):
            continue
        groups.append(GroupInfoItem(
            admin_id=_get_string(item, "adminID"),
            group_name=_get_string(item, "groupName"),
            group_uri=_get_string(item, "groupUri"),
            lastmsgstamp=int(item.get("lastmsgstamp") or 0),
        ))
    _logger.info("搜索群组成功 groupname=%s 匹配数=%s", groupname, len(groups))
    return groups, None


def is_group_member(
    emp_no: str,
    token: str,
    user_id: str,
    group_list: List[str],
) -> Tuple[bool, Optional[str]]:
    """
    查询用户是否为群成员。

    Args:
        emp_no:    查询人工号
        token:     认证令牌
        user_id:   待查询用户工号
        group_list: 群 URI 列表

    Returns:
        (is_member, error_message)，调用成功时 error_message 为 None
    """
    if not user_id:
        return False, "user_id 不能为空"
    if not group_list:
        return False, "group_list 不能为空"

    normalized_groups: List[str] = []
    for gid in group_list:
        if not gid:
            continue
        normalized_groups.append(gid if gid.startswith("sip:") else to_guri_uri(gid))
    if not normalized_groups:
        return False, "group_list 不能为空"

    headers = {
        "Content-Type": "application/json",
        "Terminal-Type": "Web@Desktop_999_ID1004",
        "X-Emp-No": emp_no,
        "X-Auth-Value": token,
        "authKey": "msgSkill",
    }
    payload = {
        "userid": user_id,
        "group_list": normalized_groups,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        IS_GROUP_MEMBER_API_URL,
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            req, timeout=TIMEOUT_SEC, context=_SSL_SKIP_VERIFY
        ) as resp:
            if resp.status != 200:
                _logger.error("查询群成员 HTTP 错误 status=%s user_id=%s", resp.status, user_id)
                return False, f"请求失败，状态码: {resp.status}"
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        _logger.error("查询群成员 HTTPError code=%s user_id=%s", e.code, user_id, exc_info=True)
        return False, f"请求失败，状态码: {e.code}"
    except urllib.error.URLError as e:
        _logger.error("查询群成员 URLError reason=%s user_id=%s", e.reason, user_id, exc_info=True)
        return False, f"请求失败: {e.reason}"
    except Exception as e:
        _logger.exception("查询群成员异常 user_id=%s: %s", user_id, e)
        return False, f"请求失败: {e}"

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as e:
        _logger.error("查询群成员响应非合法 JSON user_id=%s: %s", user_id, e)
        return False, f"解析响应失败: {e}"

    code_obj = result.get("code") or {}
    code = code_obj.get("code") if isinstance(code_obj, dict) else ""
    msg = code_obj.get("msg", "") if isinstance(code_obj, dict) else ""
    if str(code) != "200":
        _logger.error("查询群成员 API 业务错误 code=%s msg=%s user_id=%s", code, msg, user_id)
        return False, f"查询群成员失败，错误码: {code}, 错误信息: {msg}"

    bo = result.get("bo")
    if not isinstance(bo, dict):
        _logger.error("查询群成员 API 返回格式错误：缺少 bo 字段 user_id=%s", user_id)
        return False, "API 返回格式错误：缺少 bo 字段"
    is_member = str(bo.get("result", "")).strip() == "1"
    return is_member, None


def _normalize_msg_content(msg: str) -> str:
    """
    特殊处理发送内容：将字面量两字符 \\n 替换为换行符，\\r 替换为回车符。

    Args:
        msg: 原始消息内容

    Returns:
        处理后的消息内容
    """
    if not msg:
        return msg
    return msg.replace("\\n", "\x0A").replace("\\r", "\x0D")


def _error_from_send_msg_status(status_code: int, headers: Any) -> Optional[str]:
    """
    根据发送消息接口的 HTTP 状态码返回错误描述。

    Args:
        status_code: HTTP 状态码
        headers:     响应头

    Returns:
        错误描述字符串，成功返回 None
    """
    if status_code in (200, 202):
        return None
    if status_code == 400:
        return MSG_VERSION_UPGRADE
    if status_code == 403:
        return "认证不通过"
    if status_code == 404:
        return "找不到接口路由"
    if status_code == 405:
        return "没有权限调用此接口"
    if status_code == 429:
        return "此次调用受到流量控制"
    if status_code == 500:
        return "服务器内部错误"
    return f"请求失败，状态码: {status_code}"


def _error_from_response_code(code: Any, msg: str = "") -> Optional[str]:
    """
    根据响应体 code.code 返回错误描述。

    与 _error_from_send_msg_status 语义一致。

    Args:
        code: 响应状态码
        msg:  错误信息

    Returns:
        错误描述字符串，成功返回 None
    """
    c = str(code).strip() if code is not None else ""
    if c == "200":
        return None
    if c == "400":
        return msg or MSG_VERSION_UPGRADE
    if c == "403":
        return msg or "认证不通过"
    if c == "404":
        return msg or "找不到接口路由"
    if c == "405":
        return msg or "没有权限调用此接口"
    if c == "429":
        return msg or "此次调用受到流量控制"
    if c == "500":
        return msg or "服务器内部错误"
    return msg or f"请求失败，错误码: {code}"


def send_message(
    emp_no: str,
    token: str,
    msg_content: str,
    msg_type: str = "text",
    user_id: Optional[str] = None,
    user_name: Optional[str] = None,
    group_id: Optional[str] = None,
    group_name: Optional[str] = None,
    at_list: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    统一发送消息接口（单聊、群聊或同时发送）。

    参数说明：
    - user_id/user_name：单聊接收者工号/姓名，出现则发单聊
    - group_id/group_name：群聊群 ID/群名，出现则发群聊
    - msg_content：文本时为消息内容；image/file 时为图片或文件绝对路径
    - msg_type：text | image | file，默认 text
    - at_list：@ 列表，仅群聊有效，格式同 --at 参数

    Args:
        emp_no:     发送人工号
        token:      认证令牌
        msg_content: 消息内容
        msg_type:   消息类型：text | image | file，默认 text
        user_id:    单聊接收者工号（可选）
        user_name:  单聊接收者姓名（可选）
        group_id:   群聊群 ID（可选）
        group_name: 群聊群名（可选）
        at_list:    @ 列表字符串（可选，仅群聊有效）

    Returns:
        (response_dict, error_message)，成功时 error_message 为 None
    """
    if not user_id and not group_id:
        _logger.error("发送消息至少需要 user_id 或 group_id")
        return None, "至少需要指定单聊接收者(user_id)或群聊(group_id)"
    msg_type = (msg_type or "text").lower()
    if msg_type not in ("text", "image", "file"):
        msg_type = "text"
    msg_content = _normalize_msg_content(msg_content or "")

    body: Dict[str, Any] = {}
    if user_id:
        body["userID"] = user_id
        if user_name is not None:
            body["userName"] = user_name
    if group_id:
        body["groupID"] = group_id if group_id.startswith("sip:") else to_guri_uri(group_id)
        if group_name is not None:
            body["groupName"] = group_name

    # 先处理 @ 信息（仅群聊有效），以便在文本消息中拼接 @ 前缀
    at_items: List[AtItem] = []
    at_property_val = ""
    at_prefix = ""
    if at_list and group_id:
        at_items = parse_at_list_with_resolve(at_list, emp_no, token, group_id)
        if at_items:
            # 优化：若 @ 列表里包含"@自己"，则：
            # - msgProperty.at 不追加自己的工号；
            # - @ 前缀不追加自己的名字；
            # - 仅在最终 @ 前缀末尾追加一段纯文本 "@自己的名字"。
            self_name = ""
            mentioned_self = False
            filtered_items: List[AtItem] = []
            for it in at_items:
                if not it.emp_no:
                    continue
                if it.emp_no.lower() != "all" and it.emp_no == emp_no:
                    # 记录自己姓名（优先用 at_list 里带的名字）
                    mentioned_self = True
                    if it.name and not self_name:
                        self_name = it.name
                    continue
                filtered_items.append(it)

            at_property_val = build_at_property_from_items(filtered_items)
            at_prefix = build_at_prefix_from_items(filtered_items)

            # 仅当 at_list 中确实 @ 了自己时，才在最终 @ 前缀末尾追加纯文本 "@自己的名字"
            if mentioned_self:
                if not self_name:
                    self_name = _resolve_current_user_name(emp_no, token)
                if self_name:
                    # 仅追加纯文本，不参与 msgProperty.at
                    at_prefix = (at_prefix or "") + f"{AT_FLAG}{self_name} "
            # 对文本消息：在消息正文前拼接 @ 前缀，行为对齐 Go 版本
            if msg_type == "text" and at_prefix:
                msg_content = at_prefix + msg_content

    if msg_type == "text":
        msg_body = base64.b64encode(msg_content.encode("utf-8")).decode("ascii")
    else:
        msg_body = msg_content  # image/file 时为绝对路径

    body["msgContent"] = {
        "msgtype": msg_type,
        "msgenCode": "base64",
        "msgBody": msg_body,
    }

    if at_property_val:
        body["msgProperty"] = {"at": at_property_val}

    _logger.info(
        "发送消息 user_id=%s group_id=%s msg_type=%s",
        user_id or "",
        group_id or "",
        msg_type,
    )
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Emp-No": emp_no,
        "X-Auth-Value": token,
    }
    url = get_send_message_url()
    _logger.info("发送url: %s", url)
    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        # 对于本地地址（127.0.0.1 或 localhost），禁用代理以避免代理拦截
        if "127.0.0.1" in url or "localhost" in url.lower():
            # 创建不使用代理的 opener
            no_proxy_handler = ProxyHandler({})
            opener = build_opener(no_proxy_handler)
            resp = opener.open(req, timeout=TIMEOUT_SEC)
        else:
            resp = urllib.request.urlopen(req, timeout=TIMEOUT_SEC)
        status_code = resp.status
        resp_headers = resp.headers
        resp_body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        status_code = e.code
        resp_headers = e.headers
        resp_body = (e.fp.read().decode("utf-8") if e.fp else "")
        err = _error_from_send_msg_status(status_code, resp_headers)
        err_msg = resp_body or err
        _logger.error(
            "发送消息 HTTPError status=%s err=%s body=%s",
            status_code, err_msg, resp_body[:200] if resp_body else "",
        )
        return None, err_msg
    except urllib.error.URLError as e:
        _logger.error("发送消息 URLError reason=%s", e.reason, exc_info=True)
        return None, f"请求失败: {e.reason}"
    except Exception as e:
        _logger.exception("发送消息异常: %s", e)
        return None, f"请求失败: {e}"

    err = _error_from_send_msg_status(status_code, resp_headers)
    if err:
        err_msg = resp_body or err
        _logger.error(
            "发送消息业务错误 status=%s err=%s body=%s",
            status_code, err_msg, resp_body[:200] if resp_body else "",
        )
        return None, err_msg

    try:
        result = json.loads(resp_body) if resp_body.strip() else {}
    except json.JSONDecodeError as e:
        _logger.error("发送消息响应非合法 JSON: %s body=%s", e, resp_body[:200] if resp_body else "")
        return None, f"解析响应失败: {e}, 响应内容: {resp_body}"
        
    code_obj = result.get("code")
    if isinstance(code_obj, dict):
        c = code_obj.get("code")
        code_msg = code_obj.get("msg", "")
        err = _error_from_response_code(c, code_msg)
        if err:
            _logger.error("发送消息业务错误 code=%s msg=%s", c, code_msg)
            return None, err
    elif code_obj is not None:
        err = _error_from_response_code(code_obj, "")
        if err:
            return None, err
    _logger.info("发送消息成功 user_id=%s group_id=%s", user_id or "", group_id or "")
    return result, None
