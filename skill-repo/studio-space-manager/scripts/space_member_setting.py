#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=================================================================
studio-空间-ClawHub (studio-ispace-clawhub) - v2.2.1
=================================================================
企业级 iCenter 空间管理技能

通过对话交互查询和展示 iCenter 空间的成员和管理员信息。
支持 Linux 和 Windows 双平台运行。

工作流程：
1. 从参数获取空间搜索关键词
2. 调用空间搜索接口
3. 以表格形式展示空间列表
4. 获取用户选择的空间ID
5. 调用 API 获取成员信息和管理员信息
6. 合并数据，以表格形式展示（管理员显示在最前面）

功能：
- 查询空间管理员列表
- 查询空间成员列表
- 查询公司通讯录（按姓名或工号搜索员工）
- 设置/取消管理员
- 创建新空间
- 修改空间名称
- 查找个人空间

跨平台支持：
- ✅ Linux (Ubuntu/CentOS/Debian)
- ✅ Windows 10/11
- ✅ macOS (通过 Python)

安全特性：
- 输入验证和消毒处理
- API 调用频率限制
- 敏感信息脱敏
- 操作审计日志

依赖：
- Python 3.6+
- requests

作者：Co-Claw Team
创建：2026-02-26
更新：2026-03-05
"""

import os
import sys
import json
import requests
import re
import time
import hashlib
import logging
import platform
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List, Any, Tuple
from functools import wraps

from system_param import (
    ICENTER_API_BASE,
    MISSING_USER_AND_TOKEN_MESSAGE,
    build_system_param,
    get_user_and_token,
)
from _platform_utils import configure_stdio_utf8


def _get_emp_no() -> str:
    """统一从环境变量读取当前用户工号（不做校验与退出）。"""
    emp_no, _token = get_user_and_token(strict=False)
    return (emp_no or "").strip()


# ==========================================
# 跨平台支持配置（Cross-Platform Support）
# ==========================================


def detect_platform() -> Tuple[str, str, bool]:
    """
    检测当前运行平台

    Returns:
        Tuple[系统类型, 系统版本, 是否为Windows]
        例如: ('Windows', '10', True) 或 ('Linux', '4.19.112-2.el8.x86_64', False)
    """
    system = platform.system()  # Windows, Linux, Darwin
    release = platform.release()
    is_windows = system == "Windows"
    return system, release, is_windows


def get_script_dir() -> str:
    """
    获取脚本所在目录（跨平台兼容）

    Returns:
        脚本所在目录的绝对路径
    """
    # 优先使用 __file__ 属性
    script_path = os.path.dirname(os.path.abspath(__file__))
    return script_path


def get_skill_root_dir() -> str:
    """
    获取技能根目录（跨平台兼容）

    Returns:
        技能根目录的绝对路径
    """
    script_dir = get_script_dir()
    skill_root = os.path.dirname(script_dir)
    return skill_root


def get_log_dir() -> str:
    """
    获取日志目录（跨平台兼容）
    Linux: ~/.openclaw/workspace/skills/studio-ispace-clawhub/log
    Windows: 相对路径 log

    Returns:
        日志目录的绝对路径
    """
    skill_root = get_skill_root_dir()
    log_dir = os.path.join(skill_root, "log")

    # 确保日志目录存在
    try:
        os.makedirs(log_dir, exist_ok=True)
        # 设置目录权限（仅 Linux）
        system, _, is_windows = detect_platform()
        if not is_windows and os.name == "posix":
            os.chmod(log_dir, 0o700)  # rwx------
    except OSError:
        # 如果创建失败，使用临时目录
        import tempfile

        log_dir = tempfile.gettempdir()

    return log_dir


def get_audit_log_file() -> str:
    """
    获取审计日志文件路径（跨平台兼容）

    Returns:
        审计日志文件的绝对路径
    """
    log_dir = get_log_dir()
    return os.path.join(log_dir, "audit.log")


def get_data_dir() -> str:
    """
    获取数据存储目录（跨平台兼容）

    Returns:
        数据目录的绝对路径
    """
    skill_root = get_skill_root_dir()
    data_dir = os.path.join(skill_root, "data")

    # 确保数据目录存在
    try:
        os.makedirs(data_dir, exist_ok=True)
        # 设置目录权限（仅 Linux）
        system, _, is_windows = detect_platform()
        if not is_windows and os.name == "posix":
            os.chmod(data_dir, 0o700)
    except OSError:
        # 如果创建失败，使用临时目录
        import tempfile

        data_dir = tempfile.gettempdir()

    return data_dir


def get_cached_data_file(filename: str) -> str:
    """
    获取缓存数据文件路径（跨平台兼容）

    Args:
        filename: 文件名

    Returns:
        缓存数据文件的绝对路径
    """
    data_dir = get_data_dir()
    return os.path.join(data_dir, filename)


# 初始化平台信息
SYSTEM, SYSTEM_RELEASE, IS_WINDOWS = detect_platform()

# ==========================================
# 安全配置（Security Configuration）
# ==========================================

# 审计日志配置
AUDIT_LOG_ENABLED = True
AUDIT_LOG_FILE = get_audit_log_file()

# 敏感字段脱敏规则
PII_FIELDS = {
    "employeeShortId": lambda x: f"{x[:4]}****",  # 工号脱敏
    "name": lambda x: "*" + x[1:] if len(x) > 1 else "*",  # 姓名脱敏（保留首字）
    "email": lambda x: f"{x[:3]}***@zte.com.cn" if "@" in x else "***@***.com",  # 邮箱脱敏
    "deptFullName": lambda x: "/".join(
        ["*" if i > 0 else x.split("/")[0] for i in range(len(x.split("/")))]
    )
    if "/" in x
    else "*",
}

# 输入验证规则
INPUT_VALIDATION = {
    "space_id": {"max_length": 64, "pattern": r"^[a-fA-F0-9]{8,64}$"},
    "employee_id": {"max_length": 20, "pattern": r"^[0-9]{6,20}$"},
    "space_name": {
        "max_length": 100,
        "pattern": r"^[\u4e00-\u9fa5a-zA-Z0-9\s\-_（）\(\)\[\]]+$",
    },
    "keyword": {"max_length": 50, "pattern": r"^[\u4e00-\u9fa5a-zA-Z0-9\s\-_]+$"},
}

# API 调用频率限制
RATE_LIMIT = {"max_requests": 60, "window_seconds": 60}

# ==========================================
# 日志配置（Logging Configuration）
# ==========================================


def setup_audit_log():
    """配置审计日志（Python 3.6 兼容）"""
    log_dir = os.path.dirname(AUDIT_LOG_FILE)
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    # 使用 FileHandler 支持编码（Python 3.6 兼容）
    handler = logging.FileHandler(AUDIT_LOG_FILE, encoding="utf-8")
    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    handler.setFormatter(formatter)

    # 避免重复添加 handler
    if not logger.handlers:
        logger.addHandler(handler)

    return logger


# 初始化审计日志
audit_logger = setup_audit_log() if AUDIT_LOG_ENABLED else None

# ==========================================
# 安全增强函数（Security Functions）
# ==========================================


def sanitize_input(input_str: str, field_type: str = "keyword") -> str:
    """
    输入消毒处理 - 防止注入攻击

    Args:
        input_str: 原始输入
        field_type: 字段类型（用于选择验证规则）

    Returns:
        消毒后的输入
    """
    if not input_str:
        return ""

    # 移除潜在危险字符
    dangerous_chars = [
        "<",
        ">",
        "{",
        "}",
        "[",
        "]",
        "|",
        "\\",
        "^",
        "~",
        "`",
        ";",
        "$",
        "&",
        "|",
    ]
    result = input_str
    for char in dangerous_chars:
        result = result.replace(char, "")

    # 移除多余空白
    result = re.sub(r"\s+", " ", result).strip()

    # 验证输入格式（如果配置了规则）
    if field_type in INPUT_VALIDATION:
        config = INPUT_VALIDATION[field_type]
        if "pattern" in config:
            if not re.match(config["pattern"], result):
                # 如果不匹配，移除所有特殊字符后重试
                result = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9\s]", "", result)
                result = result[: config["max_length"]]

    return result


def validate_space_id(space_id: str) -> bool:
    """验证 space_id 格式"""
    if not space_id:
        return False
    return bool(re.match(r"^[a-fA-F0-9]{8,64}$", space_id))


def validate_employee_id(employee_id: str) -> bool:
    """验证工号格式"""
    if not employee_id:
        return False
    return bool(re.match(r"^[0-9]{6,20}$", employee_id))


def mask_sensitive_data(data: Dict, fields: Dict = None) -> Dict:
    """
    敏感数据脱敏处理

    Args:
        data: 原始数据字典
        fields: 要脱敏的字段配置（默认使用 PII_FIELDS）

    Returns:
        脱敏后的数据
    """
    if fields is None:
        fields = PII_FIELDS

    result = data.copy()
    for field, mask_func in fields.items():
        if field in result and result[field]:
            result[field] = mask_func(result[field])
    return result


def audit_log(
    action: str, operator: str, target: str, result: str, details: Dict = None
):
    """
    审计日志记录

    Args:
        action: 操作类型（如：ADD_MEMBER, REMOVE_MEMBER, SET_ADMIN）
        operator: 操作人工号
        target: 目标对象
        result: 操作结果（SUCCESS/FAILED）
        details: 详细信息（可选，会被脱敏处理）
    """
    if not AUDIT_LOG_ENABLED or not audit_logger:
        return

    # 脱敏详细信息
    safe_details = {}
    if details:
        for k, v in details.items():
            if isinstance(v, dict):
                safe_details[k] = mask_sensitive_data(v)
            else:
                safe_details[k] = v

    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "action": action,
        "operator": mask_sensitive_data({"employeeShortId": operator})[
            "employeeShortId"
        ],
        "target": target,
        "result": result,
        "details": safe_details,
    }

    audit_logger.info(json.dumps(log_entry, ensure_ascii=False))


def rate_limit_decorator(max_requests: int = 60, window_seconds: int = 60):
    """
    API 调用频率限制装饰器

    Args:
        max_requests: 窗口期内最大请求数
        window_seconds: 时间窗口（秒）
    """
    request_history = []

    @wraps(max_requests)
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            now = time.time()
            # 清理过期记录
            while request_history and request_history[0] < now - window_seconds:
                request_history.pop(0)

            # 检查是否超过限制
            if len(request_history) >= max_requests:
                raise RuntimeError(
                    f"API 调用频率超出限制: 在 {window_seconds} 秒内最多允许 {max_requests} 次请求"
                )

            # 记录请求
            request_history.append(now)

            return func(*args, **kwargs)

        return wrapper

    return decorator


class SecurityError(Exception):
    """安全相关异常"""

    pass


# ==========================================
# API 配置
API_URL_ADMINS = (
    f"{ICENTER_API_BASE}/zte-km-icenter-space/spacemember/loadSpaceAdmins"
)
API_URL_MEMBERS = (
    f"{ICENTER_API_BASE}/zte-km-icenter-space/spacemember/loadMemberInfosWithPageBySpaceId"
)
API_URL_UPDATE_ROLES = (
    f"{ICENTER_API_BASE}/zte-km-icenter-space/spacemember/updateRoles"
)
API_URL_UPDATE_MEMBERS = (
    f"{ICENTER_API_BASE}/zte-km-icenter-space/spacemember/updateMembers"
)
API_URL_UPDATE_SPACE = (
    f"{ICENTER_API_BASE}/zte-km-icenter-space/space/updateSpace"
)
API_URL_CREATE_SPACE = (
    f"{ICENTER_API_BASE}/zte-km-icenter-space/space/createSpace"
)
SPACE_SEARCH_API = (
    f"{ICENTER_API_BASE}/zte-icenter-search-bff/zte-icenter-search-api/search"
)

# 通讯录 API 配置
API_URL_EMPLOYEE_SEARCH = (
    f"{ICENTER_API_BASE}/zte-km-icenter-addresearch/user/plain/docs/search"
)

# 配置
PAGE_SIZE = 30  # 成员列表每页数量
MAX_TOTAL_MEMBERS = 1000  # 最大获取成员数量

# 角色定义
ROLE_MEMBER = 0  # 普通成员
ROLE_ADMIN = 1  # 管理员


def get_auth_headers():
    """获取认证请求头"""
    emp_no, token = get_user_and_token(strict=False)
    emp_no = (emp_no or "").strip()
    token = (token or "").strip()

    if not emp_no or not token:
        raise ValueError(MISSING_USER_AND_TOKEN_MESSAGE)

    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Pragma": "no-cache",
        "User-Agent": "co-claw-skill",
        "X-Auth-Value": token,
        "X-Emp-No": emp_no,
        "X-Lang-Id": "zh_CN",
        "X-Tenant-Id": "ZTE",
    }


def get_hash_code_by_string(s: str) -> int:
    """与前端 getHashCodeByString 一致：对字符串做 31 倍累加哈希，结果为 32 位有符号整数。"""
    hash_val = 0
    if not s:
        return hash_val
    for c in s:
        hash_val = (31 * hash_val + ord(c)) & 0xFFFFFFFF
    if hash_val >= 0x80000000:
        hash_val -= 0x100000000
    return hash_val


def clean_html_tags(text: str) -> str:
    """清理 HTML 标签（复用 studio-space-search 技能的实现）"""
    if not text:
        return ""
    text = text.replace('<em class="search-highlight">', "").replace("</em>", "")
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def search_spaces(keyword: str) -> list:
    """
    搜索 iCenter 空间（复用 studio-space-search 技能的搜索逻辑）

    Args:
        keyword: 搜索关键词

    Returns:
        list: 空间列表，每个空间包含 id, name, url
    """
    emp_no, token = get_user_and_token(strict=False)
    emp_no = (emp_no or "").strip()
    token = (token or "").strip()

    if not emp_no or not token:
        raise ValueError(MISSING_USER_AND_TOKEN_MESSAGE)

    headers = {
        "X-Auth-Value": token,
        "X-Emp-No": emp_no,
        "X-Lang-Id": "zh_CN",
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
    }

    request_body = {
        "keyword": keyword,
        "app": "space",
        "searchFilters": {},
        "page": 1,
        "size": 10,
        "correctKeyword": True,
        "keywordCorrected": False,
        "domain": "universal",
        "isDeepThinking": True,
    }

    try:
        response = requests.post(
            SPACE_SEARCH_API, headers=headers, json=request_body, timeout=30
        )
        response.raise_for_status()

        result = response.json()

        # 检查返回状态
        code_info = result.get("code", {})
        if code_info.get("code") != "0000":
            error_msg = code_info.get("msg", "未知错误")
            raise Exception(f"搜索空间失败: {error_msg}")

        # 提取空间列表
        rows = result.get("bo", {}).get("rows", [])
        spaces = []

        for row in rows:
            title = clean_html_tags(row.get("title", ""))
            space_id = row.get("extraFields", {}).get("spaceId", "")

            spaces.append(
                {
                    "id": space_id,
                    "name": title,
                    "url": f"https://i.zte.com.cn/index/ispace/#/space/{space_id}",
                }
            )

        return spaces

    except requests.exceptions.RequestException as e:
        raise Exception(f"请求失败: {str(e)}")
    except json.JSONDecodeError as e:
        raise Exception(f"JSON 解析错误: {str(e)}")
    except Exception as e:
        raise Exception(f"搜索空间失败: {str(e)}")


def search_employees(keyword: str) -> list:
    """
    搜索公司通讯录（员工查询）

    Args:
        keyword: 搜索关键字（姓名或工号）

    Returns:
        list: 员工列表，每个员工包含 id, name, employeeShortId, email, deptFullName 等
    """
    emp_no = _get_emp_no()

    headers = get_auth_headers()

    # 构建请求体
    request_body = {"employeeShortId": emp_no, "enableLabel": True, "keyword": keyword}

    try:
        response = requests.post(
            API_URL_EMPLOYEE_SEARCH, headers=headers, json=request_body, timeout=30
        )
        response.raise_for_status()

        result = response.json()

        # 检查返回状态
        code_info = result.get("code", {})
        if code_info.get("code") != "0000":
            raise Exception(f"API 返回错误: {code_info.get('msg', '未知错误')}")

        # 提取员工列表
        employees = result.get("bo", [])

        # 格式化员工信息
        employee_list = []
        for emp in employees:
            employee_list.append(
                {
                    "id": emp.get("id", ""),
                    "employeeShortId": emp.get("employeeShortId", ""),
                    "employeeFullId": emp.get("employeeFullId", ""),
                    "name": emp.get("name", ""),
                    "email": emp.get("email", ""),
                    "deptFullName": emp.get("deptFullName", ""),
                    "deptName": emp.get("deptName", ""),
                    "nameEn": emp.get("nameEn", ""),
                    "deptNameEn": emp.get("deptNameEn", ""),
                    "headIcon": emp.get("headIcon", ""),
                    "employeeType": emp.get("employeeType", ""),
                    "longWorkPlace": emp.get("longWorkPlace", ""),
                    "attribute": emp.get("attribute", ""),
                }
            )

        return employee_list

    except requests.exceptions.RequestException as e:
        raise Exception(f"请求失败: {str(e)}")
    except json.JSONDecodeError as e:
        raise Exception(f"JSON 解析错误: {str(e)}")
    except Exception as e:
        raise Exception(f"搜索员工失败: {str(e)}")


def get_space_admins(space_id: str) -> list:
    """
    获取指定空间的管理员列表

    Args:
        space_id: 空间ID

    Returns:
        list: 管理员列表，每个管理员包含 id, name, isAdmin
    """
    emp_no = _get_emp_no()

    # 构建 GET 请求参数
    params = {
        "employeeShortId": emp_no,
        "spaceId": space_id,
        "all": "true",
        "environmentType": "browser",
        "system": build_system_param("space"),
    }

    headers = get_auth_headers()

    try:
        response = requests.get(
            API_URL_ADMINS, params=params, headers=headers, timeout=30
        )
        response.raise_for_status()

        result = response.json()

        # 检查返回状态
        code_info = result.get("code", {})
        if code_info.get("code") != "0000":
            raise Exception(f"API 返回错误: {code_info.get('msg', '未知错误')}")

        # 提取管理员列表
        admins = result.get("bo", [])

        # 格式化管理员信息
        admin_list = []
        for admin in admins:
            if admin.get("isAdmin") == 1:
                admin_list.append(
                    {
                        "id": admin.get("id", ""),
                        "name": admin.get("name", ""),
                        "isAdmin": 1,
                    }
                )

        return admin_list

    except requests.exceptions.RequestException as e:
        raise Exception(f"请求失败: {str(e)}")
    except json.JSONDecodeError as e:
        raise Exception(f"JSON 解析错误: {str(e)}")
    except Exception as e:
        raise Exception(f"获取管理员列表失败: {str(e)}")


def get_member_list(
    space_id: str, start_index: int = 0, page_size: int = PAGE_SIZE
) -> tuple:
    """
    获取指定空间的成员列表（分页）

    Args:
        space_id: 空间ID
        start_index: 起始索引（第一页为0）
        page_size: 每页数量

    Returns:
        tuple: (成员总数, 成员列表)
        - total: 成员总数
        - member_list: 成员列表，每个成员包含 id, name, isAdmin
    """
    emp_no = _get_emp_no()

    # 构建 GET 请求参数
    params = {
        "employeeShortId": emp_no,
        "spaceId": space_id,
        "startIndex": start_index,
        "pageSize": page_size,
        "environmentType": "browser",
        "system": build_system_param("space"),
    }

    headers = get_auth_headers()

    try:
        response = requests.get(
            API_URL_MEMBERS, params=params, headers=headers, timeout=30
        )
        response.raise_for_status()

        result = response.json()

        # 检查返回状态
        code_info = result.get("code", {})
        if code_info.get("code") != "0000":
            raise Exception(f"API 返回错误: {code_info.get('msg', '未知错误')}")

        # 提取成员总数和成员列表
        bo = result.get("bo", {})
        total = bo.get("total", 0)
        members = bo.get("rows", [])

        # 格式化成员信息
        member_list = []
        for member in members:
            member_list.append(
                {
                    "id": member.get("id", ""),
                    "name": member.get("name", ""),
                    "isAdmin": member.get("isAdmin", 0),
                }
            )

        return total, member_list

    except requests.exceptions.RequestException as e:
        raise Exception(f"请求失败: {str(e)}")
    except json.JSONDecodeError as e:
        raise Exception(f"JSON 解析错误: {str(e)}")
    except Exception as e:
        raise Exception(f"获取成员列表失败: {str(e)}")


def get_all_members_with_pagination(
    space_id: str, max_members: int = MAX_TOTAL_MEMBERS
) -> tuple:
    """
    获取空间所有成员（自动分页）

    Args:
        space_id: 空间ID
        max_members: 最大获取数量

    Returns:
        tuple: (总成员数, 成员列表)
    """
    all_members = []
    total = 0
    start_index = 0

    while start_index < max_members:
        page_total, members = get_member_list(space_id, start_index, PAGE_SIZE)
        total = page_total  # 记录总数量
        if not members:
            break
        all_members.extend(members)
        start_index += PAGE_SIZE

        # 如果已经获取了所有成员，停止
        if start_index >= total:
            break

    return total, all_members


def _get_member_and_admin_id_sets(space_id: str) -> tuple:
    """
    获取空间成员/管理员的工号集合，用于幂等判断。

    Returns:
        (member_ids, admin_ids)
    """
    _, all_members = get_all_members_with_pagination(space_id, MAX_TOTAL_MEMBERS)
    member_ids = {m.get("id", "").strip() for m in all_members if m.get("id")}
    admins = get_space_admins(space_id)
    admin_ids = {a.get("id", "").strip() for a in admins if a.get("id")}
    return member_ids, admin_ids


def format_space_table(spaces: list) -> str:
    """
    格式化空间列表为表格形式

    Args:
        spaces: 空间列表

    Returns:
        str: 格式化的表格字符串
    """
    if not spaces:
        return "未找到相关空间，可能是空间不存在或当前账号无访问权限，请确认空间名称或权限后重试。"

    headers = ["编号", "空间名称", "空间地址"]
    rows = []

    for i, space in enumerate(spaces, 1):
        rows.append([str(i), space.get("name", ""), space.get("url", "")])

    # 计算每列最大宽度
    col_widths = []
    for col_idx in range(len(headers)):
        max_width = len(headers[col_idx])
        for row in rows:
            cell_length = len(str(row[col_idx]))
            if cell_length > max_width:
                max_width = cell_length
        col_widths.append(max_width + 2)

    # 构建表格
    separator_start = "┌"
    for width in col_widths:
        separator_start += "─" * width + "┬"
    separator_start = separator_start[:-1] + "┐"

    header_line = "│"
    for i, header in enumerate(headers):
        header_line += f" {header.ljust(col_widths[i] - 1)} │"

    separator = "├"
    for width in col_widths:
        separator += "─" * width + "┤"

    separator_end = "└"
    for width in col_widths:
        separator_end += "─" * width + "┴"
    separator_end = separator_end[:-1] + "┘"

    data_lines = []
    for row in rows:
        line = "│"
        for i, cell in enumerate(row):
            line += f" {str(cell).ljust(col_widths[i] - 1)} │"
        data_lines.append(line)

    table = f"{separator_start}\n{header_line}\n{separator}\n"
    for line in data_lines:
        table += f"{line}\n"
    table += separator_end

    return table


def format_employee_table(employees: list, display_count: int = 20) -> str:
    """
    格式化员工列表为表格形式

    Args:
        employees: 员工列表
        display_count: 最大显示数量

    Returns:
        str: 格式化的表格字符串
    """
    if not employees:
        return "未找到相关员工"

    display_employees = employees[:display_count]
    remaining = len(employees) - display_count

    headers = ["编号", "工号", "姓名", "部门", "邮箱"]
    rows = []

    for i, emp in enumerate(display_employees, 1):
        rows.append(
            [
                str(i),
                emp.get("employeeShortId", ""),
                emp.get("name", ""),
                emp.get("deptName", ""),
                emp.get("email", ""),
            ]
        )

    # 计算每列最大宽度
    col_widths = []
    for col_idx in range(len(headers)):
        max_width = len(headers[col_idx])
        for row in rows:
            cell_length = len(str(row[col_idx]))
            if cell_length > max_width:
                max_width = cell_length
        col_widths.append(max_width + 2)

    # 构建表格
    separator_start = "┌"
    for width in col_widths:
        separator_start += "─" * width + "┬"
    separator_start = separator_start[:-1] + "┐"

    header_line = "│"
    for i, header in enumerate(headers):
        header_line += f" {header.ljust(col_widths[i] - 1)} │"

    separator = "├"
    for width in col_widths:
        separator += "─" * width + "┤"

    separator_end = "└"
    for width in col_widths:
        separator_end += "─" * width + "┴"
    separator_end = separator_end[:-1] + "┘"

    data_lines = []
    for row in rows:
        line = "│"
        for i, cell in enumerate(row):
            line += f" {str(cell).ljust(col_widths[i] - 1)} │"
        data_lines.append(line)

    table = f"{separator_start}\n{header_line}\n{separator}\n"
    for line in data_lines:
        table += f"{line}\n"
    table += separator_end

    if remaining > 0:
        table += f"\n📌 还有 {remaining} 个员工未显示"

    return table


def format_employee_detail(emp: dict) -> str:
    """
    格式化单个员工的详细信息

    Args:
        emp: 员工信息字典

    Returns:
        str: 格式化的详细信息字符串
    """
    lines = [
        "─" * 60,
        f"  姓名: {emp.get('name', '')}",
        f"  工号: {emp.get('employeeShortId', '')}",
        f"  全ID: {emp.get('employeeFullId', '')}",
        f"  邮箱: {emp.get('email', '')}",
        f"  部门: {emp.get('deptFullName', '')}",
        f"  英文名: {emp.get('nameEn', '')}",
        f"  部门(英): {emp.get('deptNameEn', '')}",
        f"  员工类型: {emp.get('employeeType', '')}",
        f"  工作地点: {emp.get('longWorkPlace', '')}",
        f"  属性: {emp.get('attribute', '')}",
        "─" * 60,
    ]
    return "\n".join(lines)


def format_admin_table(admins: list) -> str:
    """
    格式化管理员列表为表格形式（仅显示三列：编号、管理员工号、管理员姓名）

    Args:
        admins: 管理员列表

    Returns:
        str: 格式化的表格字符串
    """
    if not admins:
        return "该空间暂无管理员"

    headers = ["编号", "管理员工号", "管理员姓名"]
    rows = []

    for i, admin in enumerate(admins, 1):
        rows.append([str(i), admin.get("id", ""), admin.get("name", "")])

    # 计算每列最大宽度
    col_widths = []
    for col_idx in range(len(headers)):
        max_width = len(headers[col_idx])
        for row in rows:
            cell_length = len(str(row[col_idx]))
            if cell_length > max_width:
                max_width = cell_length
        col_widths.append(max_width + 2)

    # 构建表格
    separator_start = "┌"
    for width in col_widths:
        separator_start += "─" * width + "┬"
    separator_start = separator_start[:-1] + "┐"

    header_line = "│"
    for i, header in enumerate(headers):
        header_line += f" {header.ljust(col_widths[i] - 1)} │"

    separator = "├"
    for width in col_widths:
        separator += "─" * width + "┤"

    separator_end = "└"
    for width in col_widths:
        separator_end += "─" * width + "┴"
    separator_end = separator_end[:-1] + "┘"

    data_lines = []
    for row in rows:
        line = "│"
        for i, cell in enumerate(row):
            line += f" {str(cell).ljust(col_widths[i] - 1)} │"
        data_lines.append(line)

    table = f"{separator_start}\n{header_line}\n{separator}\n"
    for line in data_lines:
        table += f"{line}\n"
    table += separator_end

    return table


def format_member_table(members: list, display_count: int = 20) -> str:
    """
    格式化成员列表为表格形式（显示：编号、工号、姓名、是否管理员）

    Args:
        members: 成员列表（已按管理员优先排序）
        display_count: 最大显示数量

    Returns:
        str: 格式化的表格字符串
    """
    if not members:
        return "该空间暂无成员"

    # 只显示前 display_count 个成员
    display_members = members[:display_count]
    remaining = len(members) - display_count

    headers = ["编号", "工号", "姓名", "是否管理员"]
    rows = []

    for i, member in enumerate(display_members, 1):
        is_admin = "✓ 是" if member.get("isAdmin") == 1 else "否"
        rows.append([str(i), member.get("id", ""), member.get("name", ""), is_admin])

    # 计算每列最大宽度
    col_widths = []
    for col_idx in range(len(headers)):
        max_width = len(headers[col_idx])
        for row in rows:
            cell_length = len(str(row[col_idx]))
            if cell_length > max_width:
                max_width = cell_length
        col_widths.append(max_width + 2)

    # 构建表格
    separator_start = "┌"
    for width in col_widths:
        separator_start += "─" * width + "┬"
    separator_start = separator_start[:-1] + "┐"

    header_line = "│"
    for i, header in enumerate(headers):
        header_line += f" {header.ljust(col_widths[i] - 1)} │"

    separator = "├"
    for width in col_widths:
        separator += "─" * width + "┤"

    separator_end = "└"
    for width in col_widths:
        separator_end += "─" * width + "┴"
    separator_end = separator_end[:-1] + "┘"

    data_lines = []
    for row in rows:
        line = "│"
        for i, cell in enumerate(row):
            line += f" {str(cell).ljust(col_widths[i] - 1)} │"
        data_lines.append(line)

    table = f"{separator_start}\n{header_line}\n{separator}\n"
    for line in data_lines:
        table += f"{line}\n"
    table += separator_end

    # 添加剩余成员提示
    if remaining > 0:
        table += f"\n📌 还有 {remaining} 个成员未显示"

    return table


def format_managed_spaces_table(spaces: list, display_count: int = 50) -> str:
    """
    格式化管理的空间列表为表格形式（显示：序号、空间名称、空间链接）

    Args:
        spaces: 空间列表（已包含 url 字段）
        display_count: 最大显示数量

    Returns:
        str: 格式化的表格字符串（Markdown 格式带链接）
    """
    if not spaces:
        return "您没有管理任何空间"

    # 只显示前 display_count 个空间
    display_spaces = spaces[:display_count]
    remaining = len(spaces) - display_count

    # 生成 Markdown 格式的表格
    lines = []
    lines.append("| 序号 | 空间名称 | 访问链接 |")
    lines.append("|:----:|----------|----------|")

    for i, space in enumerate(display_spaces, 1):
        name = (
            space.get("chName", "")
            or space.get("enName", "")
            or space.get("spaceId", "")
        )
        url = space.get("url", "")

        # 使用 Markdown 链接格式
        markdown_link = f"[点击访问]({url})" if url else ""

        lines.append(f"| {i:4} | {name} | {markdown_link} |")

    table = "\n".join(lines)

    # 添加剩余空间提示
    if remaining > 0:
        table += f"\n\n📌 还有 {remaining} 个空间未显示"

    return table


def format_managed_spaces_compact(spaces: list, display_count: int = 100) -> str:
    """
    格式化管理的空间列表为紧凑格式（适合展示大量空间）

    Args:
        spaces: 空间列表（已包含 url 字段）
        display_count: 最大显示数量

    Returns:
        str: 格式化的字符串（带编号和链接的 Markdown 列表）
    """
    if not spaces:
        return "您没有管理任何空间"

    # 只显示前 display_count 个空间
    display_spaces = spaces[:display_count]
    remaining = len(spaces) - display_count

    lines = []
    for i, space in enumerate(display_spaces, 1):
        name = (
            space.get("chName", "")
            or space.get("enName", "")
            or space.get("spaceId", "")
        )
        url = space.get("url", "")

        # 使用 Markdown 链接格式
        if url:
            lines.append(f"{i}. [{name}]({url})")
        else:
            lines.append(f"{i}. {name}")

    result = "\n".join(lines)

    # 添加剩余空间提示
    if remaining > 0:
        result += f"\n\n📌 还有 {remaining} 个空间未显示..."

    return result


# 角色常量
ROLE_MEMBER = 0  # 普通成员
ROLE_ADMIN = 1  # 管理员


def _check_no_self_demotion(members_for_role_update: list) -> None:
    """
    设置成员角色时的保护：不允许将当前登录用户取消为普通成员。
    若目标成员工号 = os.getenv("coclaw_empno") 且角色为普通成员，则抛出 ValueError。
    members_for_role_update 每项为 {"employeeShortId": str, "role": int}（role 已为整数）。
    """
    current_user_id = _get_emp_no()
    if not current_user_id:
        return
    for m in members_for_role_update:
        emp_id = (m.get("employeeShortId") or "").strip()
        role = m.get("role")
        if role == ROLE_MEMBER and emp_id == current_user_id:
            raise ValueError("无法取消自己的管理员权限")


# ============================================================================
# 个人空间相关功能（从 studio-space-personal 技能集成）
# ============================================================================

# 个人空间 API 配置
BASE_URL = ICENTER_API_BASE

QUERY_USERS_BY_IDS_URL = f"{BASE_URL}/zte-km-icenter-user/user/plain/queryUsersByIds"

LOAD_SPACE_ADMINS_URL = f"{BASE_URL}/zte-km-icenter-space/spacemember/loadSpaceAdmins"
GET_SPACE_MEMBER_COUNT_URL = (
    f"{BASE_URL}/zte-km-icenter-space/spacemember/getSpaceMemberCount"
)
SEARCH_SPACE_URL = f"{BASE_URL}/zte-km-icenter-space/space/searchMySpace"
# 获取我管理空间的接口（分包获取）
SEARCH_MY_SPACE_WITH_PAGE_URL = (
    f"{BASE_URL}/zte-km-icenter-space/space/searchMySpaceWithPage"
)


def get_user_name_parts(emp_no: str, token: str) -> list:
    """
    获取当前用户用于匹配空间名的姓名部分（中文、英文等）。
    """
    headers = {
        "Accept": "application/json, text/plain",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "X-Auth-Value": token,
        "X-Emp-No": emp_no,
        "X-Lang-Id": "zh_CN",
        "X-Tenant-Id": "ZTE",
        "Content-Type": "application/json",
    }
    seen = set()
    result = []
    try:
        resp = requests.post(
            QUERY_USERS_BY_IDS_URL,
            headers=headers,
            json=[emp_no],
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        user_list = None
        if isinstance(data, list) and data:
            user_list = data
        elif isinstance(data, dict):
            if data.get("code", {}).get("code") == "0000" and "bo" in data:
                user_list = data.get("bo", [])
            elif "bo" in data:
                user_list = data.get("bo", [])
        if not isinstance(user_list, list) or not user_list:
            return result
        user = user_list[0]
        for key in ("chName", "enName", "name", "employeeName"):
            val = (user.get(key) or "").strip()
            if val and val not in seen:
                seen.add(val)
                result.append(val)
    except Exception:
        pass
    return result

    emp_no = _get_emp_no()
    try:
        params = {
            "employeeShortId": emp_no,
            "spaceId": space_id,
            "all": "true",
            "environmentType": "browser",
            "system": build_system_param("space"),
        }
        headers = get_auth_headers()
        resp = requests.get(
            LOAD_SPACE_ADMINS_URL, headers=headers, params=params, timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code", {}).get("code") == "0000" and "bo" in data:
            bo = data.get("bo", [])
            if isinstance(bo, list):
                return [a for a in bo if a.get("isAdmin") == 1]
    except Exception:
        pass
    return []

    emp_no = _get_emp_no()
    if not emp_no:
        return False
    admins = get_space_admins_only(space_id)
    return (
        len(admins) == 1 and (admins[0].get("employeeShortId") or "").strip() == emp_no
    )


def fetch_managed_spaces_with_total() -> tuple:
    """
    查询我管理的空间列表（role=1）- 分包获取所有空间

    Returns:
        tuple: (总空间数, 空间列表)
        - total: 总空间数量
        - spaces: 所有空间列表，每个空间包含 id, chName, enName 等字段
    """
    headers = get_auth_headers()
    all_spaces = []
    total = 0
    start = 0
    count = 30  # 每包获取30个

    try:
        while True:
            t = int(time.time() * 1000)
            params = {
                "role": "1",
                "start": start,
                "count": count,
                "t": t,
                "spaceId": "undefined",
                "environmentType": "browser",
                "system": build_system_param("space"),
            }
            h = dict(headers)
            h["X-Cookie-From-Cros"] = "Y"

            resp = requests.get(
                SEARCH_MY_SPACE_WITH_PAGE_URL, headers=h, params=params, timeout=15
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code", {}).get("code") == "0000":
                bo = data.get("bo", {})
                if isinstance(bo, dict):
                    # 获取总空间数
                    total = bo.get("total", 0)
                    rows = bo.get("rows", [])
                    if isinstance(rows, list):
                        all_spaces.extend(rows)

                    # 检查是否已经获取完所有空间
                    if len(all_spaces) >= total or not rows:
                        break
                else:
                    break
            else:
                break

            start += count

        return total, all_spaces

    except Exception as e:
        print(f"获取我管理的空间列表失败: {e}")
        return 0, []


def fetch_managed_spaces(start: int = 0, count: int = 30) -> list:
    """查询我管理的空间列表（role=1）- 使用分包获取，每包30条"""
    headers = get_auth_headers()
    try:
        t = int(time.time() * 1000)
        params = {
            "role": "1",
            "start": start,
            "count": count,
            "t": t,
            "spaceId": "undefined",
            "environmentType": "browser",
            "system": build_system_param("space"),
        }
        h = dict(headers)
        h["X-Cookie-From-Cros"] = "Y"
        resp = requests.get(
            SEARCH_MY_SPACE_WITH_PAGE_URL, headers=h, params=params, timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code", {}).get("code") == "0000":
            bo = data.get("bo", {})
            rows = bo.get("rows") if isinstance(bo, dict) else []
            if isinstance(rows, list):
                return rows
    except Exception:
        pass
    return []


def fetch_spaces_by_keyword(keyword: str, role: str = "1") -> list:
    """按关键字搜索「我的空间」。"""
    if not (keyword or "").strip():
        return []
    headers = get_auth_headers()
    emp_no = _get_emp_no()
    try:
        params = {"environmentType": "browser", "system": build_system_param("space")}
        h = dict(headers)
        h["Content-Type"] = "application/json;charset=UTF-8"
        h["X-Cookie-From-Cros"] = "Y"
        body = {
            "employeeShortId": emp_no,
            "keyword": (keyword or "").strip(),
            "role": role,
            "scCodes": [],
        }
        resp = requests.post(
            SEARCH_SPACE_URL, headers=h, params=params, json=body, timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code", {}).get("code") == "0000":
            bo = data.get("bo")
            if isinstance(bo, list):
                return bo
    except Exception:
        pass
    return []


def _space_display_name(space: dict, id_key: str = "id") -> str:
    """从空间对象取展示名。"""
    return (
        space.get("chName") or space.get("enName") or space.get(id_key) or ""
    ).strip()


def _space_id(space: dict, id_key: str = "id") -> str:
    """从空间对象取 spaceId。"""
    return (space.get("spaceId") or space.get(id_key) or "").strip()


def batch_get_member_counts(space_ids: list) -> dict:
    """
    批量获取空间管理员个数

    Args:
        space_ids: 空间ID列表

    Returns:
        dict: {spaceId: admin_count}
    """
    if not space_ids:
        return {}

    headers = get_auth_headers()
    result = {}

    try:
        t = int(time.time() * 1000)
        params = {
            "t": t,
            "environmentType": "browser",
            "system": build_system_param("space"),
        }
        h = dict(headers)
        h["Content-Type"] = "application/json;charset=UTF-8"
        h["X-Cookie-From-Cros"] = "Y"

        resp = requests.post(
            GET_SPACE_MEMBER_COUNT_URL,
            headers=h,
            params=params,
            json=space_ids,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code", {}).get("code") == "0000":
            bo = data.get("bo", [])
            if isinstance(bo, list):
                for item in bo:
                    space_id = item.get("spaceId", "").strip()
                    count = item.get("count")
                    if space_id and count is not None:
                        try:
                            result[space_id] = int(count)
                        except (TypeError, ValueError):
                            result[space_id] = 0

    except Exception as e:
        print(f"批量获取成员数失败: {e}")

    return result


def find_personal_space() -> dict:
    """
    查找个人空间（优化版）

    逻辑：
    1. 使用 searchMySpaceWithPage 接口分包获取所有我管理（role=1）的空间
       - 第一包从 start=0 开始，每包获取 30 个（count=30）
       - 第二包从 start=30，依次类推，直至获取完所有空间
       - 从返回参数 bo.total 获取总空间数
       - 从返回参数 bo.rows[].id 获取空间ID
       - 从返回参数 bo.rows[].chName 获取空间名称
    2. 批量获取每个空间的管理员数量（使用 getSpaceMemberCount 接口）
    3. 筛选出管理员只有1个（count=1）的空间
    4. 排序：名称包含当前用户姓名的空间排在前面，作为优先推荐

    Returns:
        dict: 包含 step, message, spaceId, spaceName, candidates 等字段
    """
    emp_no, token = get_user_and_token(strict=False)
    emp_no = (emp_no or "").strip()
    token = (token or "").strip()

    if not emp_no or not token:
        return {"step": "error", "message": "个人信息认证失败，请登录后再试。", "success": False}

    # 获取用户姓名用于匹配
    name_parts = get_user_name_parts(emp_no, token)
    name_or_emp_parts = list(name_parts)
    if emp_no and emp_no not in name_or_emp_parts:
        name_or_emp_parts.append(emp_no)

    # 步骤1: 使用分包获取所有我管理的空间
    print_step("正在获取您管理的空间列表...")
    total, managed = fetch_managed_spaces_with_total()
    print(f"共获取到 {len(managed)} / {total} 个空间")

    if not managed:
        return {
            "step": "error",
            "message": "您没有管理任何空间，或者当前账号在 iCenter 中尚未被授予空间管理员权限，请确认权限后重试。",
            "success": False,
        }

    # 步骤2: 提取空间ID和名称列表
    spaces_info = []
    for space in managed:
        space_id = space.get("id") or space.get("spaceId")
        if space_id:
            spaces_info.append(
                {
                    "spaceId": space_id.strip(),
                    "chName": (space.get("chName") or "").strip(),
                    "enName": (space.get("enName") or "").strip(),
                }
            )

    # 步骤3: 批量获取每个空间的管理员数量
    print_step("正在获取各空间的管理员数量...")
    space_ids = [s["spaceId"] for s in spaces_info]
    admin_counts = batch_get_member_counts(space_ids)

    # 步骤4: 筛选出管理员只有1个的空间
    single_admin_spaces = []
    for space in spaces_info:
        space_id = space["spaceId"]
        count = admin_counts.get(space_id, -1)
        if count == 1:  # 只有1个管理员
            space["adminCount"] = 1
            single_admin_spaces.append(space)

    if not single_admin_spaces:
        return {
            "step": "error",
            "message": "没有找到管理员只有1人的空间。",
            "success": False,
        }

    # 步骤5: 分类排序 - 优先显示名称包含自己姓名的空间
    name_contained = []  # 名称包含姓名的
    other_spaces = []  # 其他空间

    for space in single_admin_spaces:
        title = space.get("chName", "") + " " + space.get("enName", "")
        title = title.strip()

        # 检查是否包含姓名或工号
        contains_name = False
        for part in name_or_emp_parts:
            if part and part in title:
                contains_name = True
                break

        if contains_name:
            name_contained.append(space)
        else:
            other_spaces.append(space)

    # 步骤6: 合并结果（优先姓名包含，再其他）
    sorted_spaces = name_contained + other_spaces

    # 为每个空间添加访问链接
    for space in sorted_spaces:
        space_id = space.get("spaceId", "")
        space["url"] = f"https://i.zte.com.cn/index/ispace/#/space/{space_id}"

    # 步骤7: 根据数量返回结果
    if len(sorted_spaces) == 1:
        s = sorted_spaces[0]
        space_id = s["spaceId"]
        return {
            "step": "resolved",
            "message": f"✅ 已找到您的个人空间（仅您一个管理员）：{s.get('chName') or space_id}",
            "spaceId": space_id,
            "spaceName": s.get("chName") or s.get("enName") or space_id,
            "spaceUrl": s.get("url", ""),
            "adminCount": 1,
            "success": True,
        }

    # 多个候选，让用户选择
    total_name = len(name_contained)
    total_other = len(other_spaces)

    def format_space_with_url(space: dict, idx: int) -> str:
        """格式化单个空间信息，包含名称、URL"""
        name = space.get("chName", "")
        url = space.get("url", "")
        return f"{idx}. [{name}]({url})（管理员: 1）"

    if total_name > 0 and total_other > 0:
        name_list = "\n".join(
            [format_space_with_url(s, i + 1) for i, s in enumerate(name_contained)]
        )
        other_list = "\n".join(
            [
                format_space_with_url(s, i + 1)
                for i, s in enumerate(other_spaces, total_name)
            ]
        )
        message = (
            f"找到 {len(sorted_spaces)} 个符合条件的个人空间（均只有1个管理员）：\n\n"
            f"📌 推荐（名称包含「{'/'.join(name_parts)}」）（{total_name}个）：\n{name_list}\n\n"
            f"📋 其他空间（{total_other}个）：\n{other_list}"
        )
    elif total_name > 0:
        message = (
            f"找到 {len(sorted_spaces)} 个符合条件的个人空间（均只有1个管理员），名称均包含「{'/'.join(name_parts)}」：\n\n"
            + "\n".join(
                [format_space_with_url(s, i + 1) for i, s in enumerate(sorted_spaces)]
            )
        )
    else:
        message = f"找到 {len(sorted_spaces)} 个符合条件的个人空间（均只有1个管理员）：\n\n" + "\n".join(
            [format_space_with_url(s, i + 1) for i, s in enumerate(sorted_spaces)]
        )

    return {
        "step": "choose_personal_space",
        "message": message,
        "candidates": sorted_spaces,
        "nameMatched": name_contained,
        "otherSpaces": other_spaces,
        "success": True,
    }


def confirm_personal_space(candidates: list, index: int) -> dict:
    """用户从多个候选中选择后，确认个人空间。"""
    if index < 0 or index >= len(candidates):
        return {
            "step": "error",
            "message": f"序号无效，请选择 1 到 {len(candidates)} 之间的数字。",
            "success": False,
        }
    s = candidates[index]
    return {
        "step": "resolved",
        "message": f"已确认您的个人空间：{s.get('chName') or s.get('spaceId')}",
        "spaceId": s["spaceId"],
        "spaceName": s.get("chName") or s.get("enName") or s["spaceId"],
        "success": True,
    }


def format_personal_space_result(result: dict) -> str:
    """格式化个人空间查询结果。"""
    if result.get("step") == "error":
        return f"❌ 错误: {result.get('message', '未知错误')}"

    if result.get("step") == "resolved":
        space_id = result.get("spaceId", "")
        space_name = result.get("spaceName", "")
        url = result.get(
            "spaceUrl", f"https://i.zte.com.cn/index/ispace/#/space/{space_id}"
        )
        return f"""✅ 个人空间已确定！

📁 空间信息:
━━━━━━━━━━━━━━━━━━
🏷️  空间名称: {space_name}
🆔  空间ID: {space_id}
🔗 访问链接: {url}
━━━━━━━━━━━━━━━━━━

📊 JSON 格式结果：
{json.dumps(result, ensure_ascii=False, indent=2)}
"""

    if result.get("step") == "choose_personal_space":
        return f"""⚠️ 找到多个个人空间，请选择：

{result.get('message', '')}

📊 JSON 格式结果：
{json.dumps(result, ensure_ascii=False, indent=2)}
"""

    return f"❌ 未知状态: {result}"


def main_find_personal_space():
    """查找个人空间"""
    if len(sys.argv) < 2 or sys.argv[1] != "--find-personal-space":
        return None

    print_step("正在查找您的个人空间...")

    result = find_personal_space()
    print(f"\n{format_personal_space_result(result)}")

    return result.get("success", False)


def main_confirm_personal_space():
    """确认个人空间（多候选时使用）"""
    if len(sys.argv) < 4 or sys.argv[1] != "--confirm-personal-space":
        return None

    try:
        index = int(sys.argv[2]) - 1  # 用户输入从1开始
        candidates_json = sys.argv[3]
        candidates = json.loads(candidates_json)

        print_step("正在确认您的个人空间...")
        result = confirm_personal_space(candidates, index)
        print(f"\n{format_personal_space_result(result)}")

        return result.get("success", False)
    except (ValueError, json.JSONDecodeError, IndexError) as e:
        print(f"\n❌ 参数错误: {str(e)}")
        return False


def list_my_managed_spaces() -> dict:
    """
    列出我管理的所有空间（带访问链接）

    Returns:
        dict: 包含 total, spaces, message 等字段
    """
    emp_no, token = get_user_and_token(strict=False)
    emp_no = (emp_no or "").strip()
    token = (token or "").strip()

    if not emp_no or not token:
        return {"step": "error", "message": "个人信息认证失败，请登录后再试。", "success": False}

    # 步骤1: 获取所有我管理的空间
    print_step("正在获取您管理的空间列表...")
    total, managed = fetch_managed_spaces_with_total()
    print(f"   共获取到 {len(managed)} / {total} 个空间")

    if not managed:
        return {
            "step": "error",
            "message": "您没有管理任何空间。",
            "success": False,
            "total": 0,
            "spaces": [],
        }

    # 步骤2: 为每个空间添加访问链接
    spaces_with_url = []
    for space in managed:
        space_id = space.get("id") or space.get("spaceId")
        if space_id:
            spaces_with_url.append(
                {
                    "spaceId": space_id.strip(),
                    "chName": (space.get("chName") or "").strip(),
                    "enName": (space.get("enName") or "").strip(),
                    "url": f"https://i.zte.com.cn/index/ispace/#/space/{space_id.strip()}",
                }
            )

    return {
        "step": "resolved",
        "message": f"您共管理 {len(spaces_with_url)} 个空间",
        "total": len(spaces_with_url),
        "spaces": spaces_with_url,
        "success": True,
    }


def format_managed_spaces_result(result: dict) -> str:
    """
    格式化管理的空间列表查询结果

    Args:
        result: 查询结果字典

    Returns:
        str: 格式化的输出字符串
    """
    if result.get("step") == "error":
        return f"❌ 错误: {result.get('message', '未知错误')}"

    if result.get("step") == "resolved":
        total = result.get("total", 0)
        spaces = result.get("spaces", [])

        output = f"✅ 您共管理 {total} 个空间\n\n"

        # 使用紧凑格式展示
        output += format_managed_spaces_compact(spaces, display_count=100)

        # JSON 格式结果
        output += f"\n\n📊 JSON 格式结果："
        output += json.dumps(result, ensure_ascii=False, indent=2)

        return output

    return f"❌ 未知状态: {result}"


def main_list_managed_spaces():
    """列出所有管理的空间"""
    if len(sys.argv) < 2 or sys.argv[1] != "--list-managed-spaces":
        return None

    print_step("正在查询您管理的空间...")

    result = list_my_managed_spaces()
    print(f"\n{format_managed_spaces_result(result)}")

    return result.get("success", False)


def set_member_role(
    space_id: str, employee_short_id: str, role: int, update_by: str
) -> dict:
    """
    设置成员的权限角色（管理员或普通成员）

    Args:
        space_id: 空间ID
        employee_short_id: 成员工号
        role: 角色 (0=普通成员, 1=管理员)
        update_by: 操作人工号

    Returns:
        dict: API 响应结果

    Raises:
        ValueError: 如果尝试取消自己的管理员权限，或操作后将没有任何管理员
    """
    # 统一规范角色和工号字符串，兼容上层传入字符串 "0"/"1" 等情况
    try:
        normalized_role = int(role)
    except (TypeError, ValueError):
        raise ValueError(f"无效的角色值: {role}")
    normalized_emp_id = (employee_short_id or "").strip()

    # 设置成员角色保护：不能将当前登录用户取消为普通成员
    _check_no_self_demotion(
        [{"employeeShortId": normalized_emp_id, "role": normalized_role}]
    )

    member_ids, current_admin_ids = _get_member_and_admin_id_sets(space_id)

    # 检查：操作后至少保留一名管理员
    if not current_admin_ids and normalized_role == ROLE_MEMBER:
        # 理论上不应该出现没有管理员还降级的情况，这里做保护
        raise ValueError("当前空间未检测到管理员，已阻止本次降级操作")

    target_id = normalized_emp_id
    next_admin_ids = set(current_admin_ids)
    if normalized_role == ROLE_ADMIN:
        # 已是管理员则无需重复设置（幂等）
        if target_id in current_admin_ids:
            return {
                "success": True,
                "skipped": True,
                "message": "用户已经是空间管理员，无须重复设置",
            }
        # 设置管理员前，确保已是空间成员，否则先作为普通成员加入
        if target_id:
            if target_id not in member_ids:
                add_space_members(space_id, [target_id], update_by)

        next_admin_ids.add(target_id)

        # 限制管理员总数不超过 15 个
        if len(next_admin_ids) > 15:
            raise ValueError("空间管理员数量最多为 15 位，已阻止本次操作")
    else:
        # 删除管理员（降级）：
        # - 若目标是管理员：执行降级为普通成员
        # - 若目标不是管理员但仍是成员：按需求直接从成员中删除，并给出提示
        # - 若目标既不是管理员也不是成员：提示无需重复删除
        if target_id not in current_admin_ids:
            if target_id in member_ids:
                remove_space_members(space_id, [target_id], update_by)
                return {
                    "success": True,
                    "skipped": True,
                    "message": "要删除的用户当前不是管理员，已从空间成员中删除",
                }
            return {
                "success": True,
                "skipped": True,
                "message": "用户当前不是空间成员，无须再重复删除",
            }

        if target_id in next_admin_ids:
            next_admin_ids.remove(target_id)

    if not next_admin_ids:
        raise ValueError("操作后将没有任何管理员，已阻止本次操作，请至少保留一位管理员")

    headers = get_auth_headers()

    # 构建请求体
    request_body = {
        "members": [{"employeeShortId": normalized_emp_id, "role": normalized_role}],
        "spaceId": space_id,
        "updateBy": update_by,
    }

    # 构建查询参数
    params = {"environmentType": "browser", "system": build_system_param("space")}

    try:
        response = requests.post(
            API_URL_UPDATE_ROLES,
            params=params,
            headers=headers,
            json=request_body,
            timeout=30,
        )
        response.raise_for_status()

        result = response.json()

        # 检查返回状态
        code_info = result.get("code", {})
        if code_info.get("code") != "0000":
            raise Exception(f"API 返回错误: {code_info.get('msg', '未知错误')}")

        return {"success": True, "message": "设置成功", "result": result}

    except requests.exceptions.RequestException as e:
        raise Exception(f"请求失败: {str(e)}")
    except json.JSONDecodeError as e:
        raise Exception(f"JSON 解析错误: {str(e)}")
    except Exception as e:
        raise Exception(f"设置成员角色失败: {str(e)}")


def set_multiple_member_roles(
    space_id: str, members_data: list, update_by: str
) -> dict:
    """
    批量设置成员的权限角色

    Args:
        space_id: 空间ID
        members_data: 成员数据列表 [{"employeeShortId": "xxx", "role": 0/1}, ...]
        update_by: 操作人工号

    Returns:
        dict: API 响应结果

    Raises:
        ValueError: 如果尝试通过批量操作取消自己的管理员权限，或操作后将没有任何管理员
    """
    # 规范化成员列表（兼容字符串角色）
    normalized_members = []
    for m in members_data:
        emp_id = (m.get("employeeShortId") or "").strip()
        raw_role = m.get("role")
        try:
            normalized_role = int(raw_role)
        except (TypeError, ValueError):
            raise ValueError(f"无效的角色值: {raw_role}")
        normalized_members.append({"employeeShortId": emp_id, "role": normalized_role})

    # 设置成员角色保护：不能将当前登录用户取消为普通成员
    _check_no_self_demotion(normalized_members)

    # 当前管理员列表
    current_admins = get_space_admins(space_id)
    current_admin_ids = {
        admin.get("id", "").strip() for admin in current_admins if admin.get("id")
    }

    # 先确保所有要设置为管理员的用户都是成员，不是成员的先加入为普通成员
    admin_target_ids = {
        m["employeeShortId"]
        for m in normalized_members
        if m["role"] == ROLE_ADMIN and m["employeeShortId"]
    }
    if admin_target_ids:
        _, all_members = get_all_members_with_pagination(space_id, MAX_TOTAL_MEMBERS)
        member_ids = {m.get("id", "").strip() for m in all_members if m.get("id")}
        need_add = [eid for eid in admin_target_ids if eid and eid not in member_ids]
        if need_add:
            add_space_members(space_id, need_add, update_by)

    # 检查：操作后至少保留一名管理员，并限制管理员总数不超过 15 个
    if not current_admin_ids:
        # 若当前未检测到管理员，则不允许执行包含降级的批量操作
        # 允许的场景：纯升级为管理员
        if all(m["role"] == ROLE_ADMIN for m in normalized_members):
            next_admin_ids = {
                m["employeeShortId"] for m in normalized_members if m["employeeShortId"]
            }
            if not next_admin_ids:
                raise ValueError("批量设置管理员数据为空，已阻止本次操作")
        else:
            raise ValueError("当前空间未检测到管理员，已阻止本次批量降级操作")
    else:
        next_admin_ids = set(current_admin_ids)
        for m in normalized_members:
            emp_id = m["employeeShortId"]
            role = m["role"]
            if not emp_id:
                continue
            if role == ROLE_ADMIN:
                next_admin_ids.add(emp_id)
            elif role == ROLE_MEMBER and emp_id in next_admin_ids:
                next_admin_ids.remove(emp_id)

        if not next_admin_ids:
            raise ValueError("批量操作后将没有任何管理员，已阻止本次操作，请至少保留一位管理员")

    # 管理员数量上限校验（最多 15 个）
    if len(next_admin_ids) > 15:
        raise ValueError("空间管理员数量最多为 15 位，已阻止本次批量操作")

    headers = get_auth_headers()

    # 构建请求体
    request_body = {
        "members": normalized_members,
        "spaceId": space_id,
        "updateBy": update_by,
    }

    # 构建查询参数
    params = {"environmentType": "browser", "system": build_system_param("space")}

    try:
        response = requests.post(
            API_URL_UPDATE_ROLES,
            params=params,
            headers=headers,
            json=request_body,
            timeout=30,
        )
        response.raise_for_status()

        result = response.json()

        # 检查返回状态
        code_info = result.get("code", {})
        if code_info.get("code") != "0000":
            raise Exception(f"API 返回错误: {code_info.get('msg', '未知错误')}")

        return {
            "success": True,
            "message": f"成功设置 {len(members_data)} 个成员的权限",
            "result": result,
        }

    except requests.exceptions.RequestException as e:
        raise Exception(f"请求失败: {str(e)}")
    except json.JSONDecodeError as e:
        raise Exception(f"JSON 解析错误: {str(e)}")
    except Exception as e:
        raise Exception(f"批量设置成员角色失败: {str(e)}")


def add_space_members(space_id: str, employee_short_ids: list, update_by: str) -> dict:
    """
    增加空间成员

    Args:
        space_id: 空间ID
        employee_short_ids: 要添加的成员工号列表
        update_by: 操作人工号

    Returns:
        dict: API 响应结果
    """
    headers = get_auth_headers()

    # 构建请求体
    request_body = {
        "spaceId": space_id,
        "updateBy": update_by,
        "add": employee_short_ids,
    }

    try:
        response = requests.post(
            API_URL_UPDATE_MEMBERS, headers=headers, json=request_body, timeout=30
        )
        response.raise_for_status()

        result = response.json()

        # 检查返回状态
        code_info = result.get("code", {})
        if code_info.get("code") != "0000":
            raise Exception(f"API 返回错误: {code_info.get('msg', '未知错误')}")

        # 提取添加成功的成员列表
        added_members = result.get("bo", {}).get("add", [])

        return {
            "success": True,
            "message": f"成功添加 {len(added_members)} 位成员",
            "addedCount": len(added_members),
            "addedMembers": added_members,
            "result": result,
        }

    except requests.exceptions.RequestException as e:
        raise Exception(f"请求失败: {str(e)}")
    except json.JSONDecodeError as e:
        raise Exception(f"JSON 解析错误: {str(e)}")
    except Exception as e:
        raise Exception(f"添加成员失败: {str(e)}，可能当前账号在该空间没有添加成员的权限，请联系空间管理员或确认权限后重试。")


def remove_space_members(
    space_id: str, employee_short_ids: list, update_by: str
) -> dict:
    """
    删除空间成员

    Args:
        space_id: 空间ID
        employee_short_ids: 要删除的成员工号列表
        update_by: 操作人工号

    Returns:
        dict: API 响应结果
    """
    headers = get_auth_headers()

    # 构建请求体
    request_body = {
        "spaceId": space_id,
        "updateBy": update_by,
        "remove": employee_short_ids,
    }

    try:
        response = requests.post(
            API_URL_UPDATE_MEMBERS, headers=headers, json=request_body, timeout=30
        )
        response.raise_for_status()

        result = response.json()

        # 检查返回状态
        code_info = result.get("code", {})
        if code_info.get("code") != "0000":
            raise Exception(f"API 返回错误: {code_info.get('msg', '未知错误')}")

        # 提取删除成功的成员列表
        removed_members = result.get("bo", {}).get("remove", [])

        return {
            "success": True,
            "message": f"成功删除 {len(removed_members)} 位成员",
            "removedCount": len(removed_members),
            "removedMembers": removed_members,
            "result": result,
        }

    except requests.exceptions.RequestException as e:
        raise Exception(f"请求失败: {str(e)}")
    except json.JSONDecodeError as e:
        raise Exception(f"JSON 解析错误: {str(e)}")
    except Exception as e:
        raise Exception(f"删除成员失败: {str(e)}，可能当前账号在该空间没有删除成员的权限，请联系空间管理员或确认权限后重试。")


def update_space_name(space_id: str, new_name: str, employee_short_id: str) -> dict:
    """
    修改空间名称

    Args:
        space_id: 空间ID
        new_name: 新空间名称
        employee_short_id: 操作人工号

    Returns:
        dict: API 响应结果
    """
    headers = get_auth_headers()

    # 构建请求体
    request_body = {
        "spaceId": space_id,
        "employeeShortId": employee_short_id,
        "name": new_name,
        "desc": None,
        "logo": None,
    }

    try:
        response = requests.post(
            API_URL_UPDATE_SPACE, headers=headers, json=request_body, timeout=30
        )
        response.raise_for_status()

        result = response.json()

        # 检查返回状态
        code_info = result.get("code", {})
        if code_info.get("code") != "0000":
            raise Exception(f"API 返回错误: {code_info.get('msg', '未知错误')}")

        # 提取更新后的空间信息
        space_info = result.get("bo", {})

        return {
            "success": True,
            "message": "修改空间名称成功",
            "spaceName": space_info.get("chName", new_name),
            "spaceInfo": space_info,
            "result": result,
        }

    except requests.exceptions.RequestException as e:
        raise Exception(f"请求失败: {str(e)}")
    except json.JSONDecodeError as e:
        raise Exception(f"JSON 解析错误: {str(e)}")
    except Exception as e:
        raise Exception(f"修改空间名称失败: {str(e)}")


def main_add_members():
    """添加空间成员"""
    if len(sys.argv) < 4 or sys.argv[1] != "--add-member":
        return None

    space_id = sys.argv[2]
    employee_id = sys.argv[3]

    try:
        emp_no = _get_emp_no()

        # 幂等判断：已是成员/管理员则无需再添加
        member_ids, _admin_ids = _get_member_and_admin_id_sets(space_id)
        if employee_id.strip() in member_ids:
            print_step("无需添加：用户已经是空间成员")
            print(f"  空间ID: {space_id}")
            print(f"  成员工号: {employee_id}")
            return True

        print_step(f"正在添加成员到空间...")
        print(f"  空间ID: {space_id}")
        print(f"  成员工号: {employee_id}")
        print(f"  操作人: {emp_no}")

        result = add_space_members(space_id, [employee_id], emp_no)

        print(f"\n✅ 添加成功!")
        print(f"  已添加 {result['addedCount']} 位成员")

        if result.get("addedMembers"):
            print(f"\n📋 添加的成员详情：")
            for member in result["addedMembers"]:
                print(
                    f"  - {member.get('name', '')} ({member.get('employeeShortId', '')})"
                )

        return True

    except ValueError as e:
        print(f"\n❌ 配置错误: {str(e)}")
        return False
    except requests.exceptions.RequestException as e:
        print(f"\n❌ 网络请求错误: {str(e)}")
        return False
    except Exception as e:
        print(f"\n❌ 错误: {str(e)}")
        return False


def main_remove_members():
    """删除空间成员"""
    if len(sys.argv) < 4 or sys.argv[1] != "--remove-member":
        return None

    space_id = sys.argv[2]
    employee_id = sys.argv[3]

    try:
        emp_no = _get_emp_no()

        # 幂等判断：不在成员列表则无需删除
        member_ids, _admin_ids = _get_member_and_admin_id_sets(space_id)
        if employee_id.strip() not in member_ids:
            print_step("无需删除：用户不是空间成员")
            print(f"  空间ID: {space_id}")
            print(f"  成员工号: {employee_id}")
            return True

        print_step(f"正在从空间删除成员...")
        print(f"  空间ID: {space_id}")
        print(f"  成员工号: {employee_id}")
        print(f"  操作人: {emp_no}")

        result = remove_space_members(space_id, [employee_id], emp_no)

        print(f"\n✅ 删除成功!")
        print(f"  已删除 {result['removedCount']} 位成员")

        if result.get("removedMembers"):
            print(f"\n📋 删除的成员详情：")
            for member in result["removedMembers"]:
                print(
                    f"  - {member.get('name', '')} ({member.get('employeeShortId', '')})"
                )

        return True

    except ValueError as e:
        print(f"\n❌ 配置错误: {str(e)}")
        return False
    except requests.exceptions.RequestException as e:
        print(f"\n❌ 网络请求错误: {str(e)}")
        return False
    except Exception as e:
        print(f"\n❌ 错误: {str(e)}")
        return False


def create_space(
    space_name: str, space_desc: str = "", employee_short_id: str = None
) -> dict:
    """
    创建新空间

    Args:
        space_name: 空间名称（必填）
        space_desc: 空间描述（可选，默认空）
        employee_short_id: 操作人工号（可选，默认从环境变量读取）

    Returns:
        dict: API 响应结果
    """
    headers = get_auth_headers()

    if not employee_short_id:
        employee_short_id = _get_emp_no()

    # 构建请求体
    request_body = {
        "employeeShortId": employee_short_id,
        "name": space_name,
        "desc": space_desc if space_desc else None,
        "logo": f"{ICENTER_API_BASE}/group1/M00/0D/BB/Ch4MdFnM5QKAZDqzAAAUUnvHQQ4586.png",
        "scCode": "301301",
        "spaceFrom": "SPACE",
    }

    try:
        response = requests.post(
            API_URL_CREATE_SPACE, headers=headers, json=request_body, timeout=30
        )
        response.raise_for_status()

        result = response.json()

        # 检查返回状态
        code_info = result.get("code", {})
        if code_info.get("code") != "0000":
            raise Exception(f"API 返回错误: {code_info.get('msg', '未知错误')}")

        # 提取创建后的空间信息
        space_info = result.get("bo", {})

        return {
            "success": True,
            "message": "创建空间成功",
            "spaceId": space_info.get("id", ""),
            "spaceName": space_info.get("chName", space_name),
            "spaceUrl": f"https://i.zte.com.cn/index/ispace/#/space/{space_info.get('id', '')}",
            "spaceInfo": space_info,
            "result": result,
        }

    except requests.exceptions.RequestException as e:
        raise Exception(f"请求失败: {str(e)}")
    except json.JSONDecodeError as e:
        raise Exception(f"JSON 解析错误: {str(e)}")
    except Exception as e:
        raise Exception(f"创建空间失败: {str(e)}")


def format_space_creation_result(result: dict) -> str:
    """
    格式化创建空间的结果

    Args:
        result: 创建空间的结果字典

    Returns:
        str: 格式化的输出字符串
    """
    if not result.get("success"):
        return f"❌ 创建失败: {result.get('message', '未知错误')}"

    space_info = result.get("spaceInfo", {})
    output = f"""✅ 空间创建成功！

📁 空间信息:
━━━━━━━━━━━━━━━━━━
🏷️  空间名称: {space_info.get('chName', 'N/A')}
🆔  空间ID: {space_info.get('id', 'N/A')}
📝  英文名称: {space_info.get('enName', 'N/A')}
📄  描述: {space_info.get('chDesc', '(空)') or '(空)'}
🔢  编码: {space_info.get('scCode', 'N/A')}
📅 创建时间: {space_info.get('createDate', 'N/A')}
👤 创建人: {space_info.get('createBy', 'N/A')}
🔗 访问链接: {result.get('spaceUrl', 'N/A')}
━━━━━━━━━━━━━━━━━━
"""
    return output


def main_update_space_name():
    """修改空间名称"""
    if len(sys.argv) < 4 or sys.argv[1] != "--update-space-name":
        return None

    space_id = sys.argv[2]
    new_name = sys.argv[3]

    try:
        emp_no = _get_emp_no()

        print_step(f"正在修改空间名称...")
        print(f"  空间ID: {space_id}")
        print(f"  新名称: {new_name}")
        print(f"  操作人: {emp_no}")

        result = update_space_name(space_id, new_name, emp_no)

        print(f"\n✅ 修改成功!")
        print(f"  空间新名称: {result['spaceName']}")

        return True

    except ValueError as e:
        print(f"\n❌ 配置错误: {str(e)}")
        return False
    except requests.exceptions.RequestException as e:
        print(f"\n❌ 网络请求错误: {str(e)}")
        return False
    except Exception as e:
        print(f"\n❌ 错误: {str(e)}")
        return False


def main_create_space():
    """创建空间"""
    if len(sys.argv) < 3 or sys.argv[1] != "--create-space":
        return None

    space_name = sys.argv[2]
    space_desc = sys.argv[3] if len(sys.argv) > 3 else ""

    try:
        emp_no = _get_emp_no()

        print_step(f"正在创建空间: {space_name}")
        if space_desc:
            print(f"  空间描述: {space_desc}")
        print(f"  操作人: {emp_no}")

        result = create_space(space_name, space_desc, emp_no)

        # 格式化输出
        print(f"\n{format_space_creation_result(result)}")

        return True

    except ValueError as e:
        print(f"\n❌ 配置错误: {str(e)}")
        return False
    except requests.exceptions.RequestException as e:
        print(f"\n❌ 网络请求错误: {str(e)}")
        return False
    except Exception as e:
        print(f"\n❌ 错误: {str(e)}")
        return False


def print_step(message: str, emoji: str = "🔧"):
    """打印步骤信息"""
    print(f"\n{emoji} {message}")


def main_employee_search():
    """搜索公司通讯录"""
    if len(sys.argv) < 3 or sys.argv[1] != "--employee":
        return None

    keyword = sys.argv[2]

    try:
        print_step(f"正在搜索员工: 「{keyword}」")
        employees = search_employees(keyword)

        if not employees:
            print(f"\n❌ 未找到包含「{keyword}」的员工")
            return

        print(f"\n✅ 找到 {len(employees)} 位员工：")
        print(format_employee_table(employees, 20))

        # 输出第一个员工的详细信息
        if employees:
            print(f"\n📋 员工详细信息：")
            print(format_employee_detail(employees[0]))

        # 输出 JSON 格式结果
        print(f"\n📊 JSON 格式结果：")
        print(
            json.dumps(
                {
                    "keyword": keyword,
                    "total": len(employees),
                    "employees": employees[:10],  # 只输出前10个
                },
                ensure_ascii=False,
                indent=2,
            )
        )

        return True

    except ValueError as e:
        print(f"\n❌ 配置错误: {str(e)}")
        return False
    except requests.exceptions.RequestException as e:
        print(f"\n❌ 网络请求错误: {str(e)}")
        return False
    except json.JSONDecodeError as e:
        print(f"\n❌ JSON 解析错误: {str(e)}")
        return False
    except Exception as e:
        print(f"\n❌ 错误: {str(e)}")
        return False


def main():
    """主函数"""
    # 检查是否是员工搜索模式
    if len(sys.argv) >= 2 and sys.argv[1] == "--employee":
        success = main_employee_search()
        sys.exit(0 if success else 1)

    # 检查是否是添加成员模式
    if len(sys.argv) >= 4 and sys.argv[1] == "--add-member":
        success = main_add_members()
        sys.exit(0 if success else 1)

    # 检查是否是删除成员模式
    if len(sys.argv) >= 4 and sys.argv[1] == "--remove-member":
        success = main_remove_members()
        sys.exit(0 if success else 1)

    # 检查是否是修改空间名称模式
    if len(sys.argv) >= 4 and sys.argv[1] == "--update-space-name":
        success = main_update_space_name()
        sys.exit(0 if success else 1)

    # 检查是否是创建空间模式
    if len(sys.argv) >= 3 and sys.argv[1] == "--create-space":
        success = main_create_space()
        sys.exit(0 if success else 1)

    # 检查是否是查找个人空间模式
    if len(sys.argv) >= 2 and sys.argv[1] == "--find-personal-space":
        success = main_find_personal_space()
        sys.exit(0 if success else 1)

    # 检查是否是确认个人空间模式
    if len(sys.argv) >= 4 and sys.argv[1] == "--confirm-personal-space":
        success = main_confirm_personal_space()
        sys.exit(0 if success else 1)

    # 检查是否是列出所有管理的空间模式
    if len(sys.argv) >= 2 and sys.argv[1] == "--list-managed-spaces":
        success = main_list_managed_spaces()
        sys.exit(0 if success else 1)

    if len(sys.argv) < 2:
        print("=" * 60)
        print("    studio-空间-ClawHub (studio-ispace-clawhub)")
        print("    企业级 iCenter 空间管理技能")
        print("=" * 60)
        print(
            f"\n📍 当前平台: {SYSTEM} {SYSTEM_RELEASE} {'(Windows)' if IS_WINDOWS else '(Linux)'}"
        )
        print("\n📖 用法:")
        print("  ")
        print("  【空间管理】")
        print(
            "  1. 搜索空间:           python space_member_setting.py <空间关键词>   # 若提示未找到空间，也可能是当前账号无权限访问该空间"
        )
        print(
            "  2. 创建空间:           python space_member_setting.py --create-space <名称> [描述]"
        )
        print(
            "  3. 修改空间名称:       python space_member_setting.py --update-space-name <spaceId> <新名称>"
        )
        print("  4. 查找个人空间:       python space_member_setting.py --find-personal-space")
        print("  5. 列出所有管理空间:   python space_member_setting.py --list-managed-spaces")
        print("  ")
        print("  【成员管理】")
        print("  6. 搜索员工:           python space_member_setting.py --employee <姓名或工号>")
        print(
            "  7. 添加成员:           python space_member_setting.py --add-member <spaceId> <工号>"
        )
        print(
            "  8. 删除成员:           python space_member_setting.py --remove-member <spaceId> <工号>"
        )
        print(
            "  9. 确认个人空间:       python space_member_setting.py --confirm-personal-space <序号> <candidates-json>"
        )
        print("  ")
        print("  【页面搜索】")
        print("  10. 搜索空间页面:      python space_page_search.py <关键词> [选项]")
        print("      选项:")
        print("        --title-model     是否标题搜索（默认 False）")
        print("        --create-by       创建人工号（可选，多个用逗号分隔）")
        print("        --space-id        空间ID（可选，多个用逗号分隔）")
        print("        --page-size       每页数量（默认 15）")
        print("        --page-num        页码（默认 1）")
        print("  ")
        print(
            "  11. 获取页面内容:      python fetch_space_page.py --space-id <spaceId> --page-id <pageId>"
        )
        print("\n📝 示例:")
        print("  # 空间管理")
        print("  python space_member_setting.py 测试空间")
        print("  python space_member_setting.py --create-space '新产品开发空间' '这是描述'")
        print("  ")
        print("  # 成员管理")
        print("  python space_member_setting.py --employee 李峰")
        print("  python space_member_setting.py --add-member 1920485432 0668000126")
        print("  ")
        print("  # 页面搜索")
        print("  python space_page_search.py 项目管理")
        print(
            "  python space_page_search.py 技术方案 --title-model true --create-by 0668000519"
        )
        print(
            "  python space_page_search.py 周报 --space-id ca16509db4d644cdb17f19426b3473dc"
        )
        print("  ")
        print("  # 获取页面内容")
        print("  python fetch_space_page.py --space-id xxx --page-id yyy")
        print("  python space_member_setting.py --find-personal-space")
        print("  python space_member_setting.py --list-managed-spaces")
        print("\n✅ 功能:")
        print("  1. 搜索 iCenter 空间")
        print("  2. 展示空间成员和管理员")
        print("  3. 设置/取消管理员")
        print("  4. 查询公司通讯录（按姓名或工号）")
        print("  5. 创建新空间")
        print("  6. 修改空间名称")
        print("  7. 查找个人空间（集成自 studio-space-personal 技能）")
        print("  8. 列出所有管理的空间（带访问链接）")
        print("\n🌐 跨平台支持: Linux / Windows / macOS")
        sys.exit(1)

    keyword = sys.argv[1]

    try:
        # Step 1: 搜索空间
        print_step(f"正在搜索空间: 「{keyword}」")
        spaces = search_spaces(keyword)

        if not spaces:
            print(f"\n❌ 未找到包含「{keyword}」的空间，可能是空间不存在或当前账号无访问权限，请确认空间名称或权限后重试。")
            sys.exit(0)

        # Step 2: 展示空间列表
        print(f"\n找到 {len(spaces)} 个匹配的空间：")
        print(format_space_table(spaces))

        # Step 3: 获取用户选择
        print(f"\n📋 请输入编号选择空间（1-{len(spaces)}）：")
        while True:
            try:
                choice = input().strip()
                if not choice:
                    continue

                idx = int(choice)
                if 1 <= idx <= len(spaces):
                    selected_space = spaces[idx - 1]
                    break
                else:
                    print(f"⚠️ 请输入 1-{len(spaces)} 之间的数字：")
            except ValueError:
                print("⚠️ 请输入有效的数字：")

        # Step 4: 获取管理员列表
        print_step("正在获取管理员信息...")
        admins = get_space_admins(selected_space["id"])

        # Step 5: 获取成员列表
        print_step("正在获取成员列表...")
        total_members, all_members = get_all_members_with_pagination(
            selected_space["id"], MAX_TOTAL_MEMBERS
        )
        print(f"该空间共有 {total_members} 位成员")

        # Step 7: 合并管理员和成员信息
        # 创建管理员ID集合
        admin_ids = {admin["id"] for admin in admins}

        # 标记成员是否为管理员
        for member in all_members:
            if member["id"] in admin_ids:
                member["isAdmin"] = 1

        # Step 8: 按管理员优先排序
        # 管理员排在最前面
        sorted_members = sorted(
            all_members,
            key=lambda x: (0 if x.get("isAdmin") == 1 else 1, x.get("name", "")),
        )

        # Step 9: 展示成员表格
        print(f"\n✅ 空间「{selected_space['name']}」的成员列表（共 {total_members} 位）：")
        print(format_member_table(sorted_members, 20))

        # 输出 JSON 格式结果
        print(f"\n📊 JSON 格式结果：")
        print(
            json.dumps(
                {
                    "spaceId": selected_space["id"],
                    "spaceName": selected_space["name"],
                    "spaceUrl": selected_space["url"],
                    "totalMembers": total_members,
                    "adminCount": len(admins),
                    "adminList": admins[:10],  # 只输出前10个管理员
                    "displayMembers": sorted_members[:20],  # 只输出前20个成员
                },
                ensure_ascii=False,
                indent=2,
            )
        )

        sys.exit(0)

    except ValueError as e:
        print(f"\n❌ 配置错误: {str(e)}")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"\n❌ 网络请求错误: {str(e)}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"\n❌ JSON 解析错误: {str(e)}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 错误: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    configure_stdio_utf8()
    main()
