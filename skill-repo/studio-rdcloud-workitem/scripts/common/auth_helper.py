#!/usr/bin/env python
"""
认证统一管理：
- 环境变量名：COCLAW_* / RDCLOUD_* 常量仅在本模块定义，其它代码通过本模块引用或调用 resolve_*_from_env。
- 请求头：唯一入口 get_auth_headers(input_data, base_config[, logger])。
- 人员取值解析为工号（@me/姓名→工号）由 resolve_person_to_emp_no 提供，统一走 user_search。

各原子脚本禁止再实现 get_auth_headers、禁止硬编码 coclaw_/RDCLOUD_ 环境变量名字符串。
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional

# 与 docs/认证变量约定.md 一致
COCLAW_EMPNO_ENV = "coclaw_empno"
COCLAW_TOKEN_ENV = "coclaw_token"
COCLAW_LANG_ENV = "coclaw_lang"
RDCLOUD_AUTH_TOKEN = "RDCLOUD_AUTH_TOKEN"
RDCLOUD_EMP_NO = "RDCLOUD_EMP_NO"
RDCLOUD_TENANT_ID = "RDCLOUD_TENANT_ID"
DEFAULT_LANG_ID = "zh_CN"


def resolve_auth_token_from_env() -> str:
    """仅从环境变量解析 auth_token（coclaw_token 优先，其次 RDCLOUD_AUTH_TOKEN），不含入参。"""
    return (os.getenv(COCLAW_TOKEN_ENV) or os.getenv(RDCLOUD_AUTH_TOKEN) or "").strip()


def resolve_emp_no_from_env() -> str:
    """仅从环境变量解析 emp_no（coclaw_empno 优先，其次 RDCLOUD_EMP_NO），不含入参。"""
    return (os.getenv(COCLAW_EMPNO_ENV) or os.getenv(RDCLOUD_EMP_NO) or "").strip()


def resolve_lang_id_from_env() -> str:
    """从环境变量 coclaw_lang 读取语言，缺省为 DEFAULT_LANG_ID。"""
    v = (os.getenv(COCLAW_LANG_ENV) or "").strip()
    return v if v else DEFAULT_LANG_ID


def value_error_missing_emp_no_from_env() -> str:
    """未从 headers/入参拿到工号、需用户配置环境变量时的 ValueError 文案。"""
    return f"无法获取用户工号，请设置 {COCLAW_EMPNO_ENV} 或 {RDCLOUD_EMP_NO}"


def get_auth_headers(
    input_data: Optional[Dict[str, Any]],
    base_config: Dict[str, Any],
    logger: Optional[Any] = None,
) -> Dict[str, str]:
    """
    从入参、环境变量、配置文件组装认证请求头。优先级：入参 auth > coclaw 环境变量 > RDCLOUD 环境变量 > 配置文件 tenant 等。
    各原子脚本**禁止**再实现本函数或重复校验逻辑，一律调用本入口。

    :param input_data: 调用方入参，可为 None；若为 dict 则取 input_data.get("auth") 或 {}
    :param base_config: 配置 dict，至少可含 tenant_id 默认值
    :param logger: 可选；传入时记录认证来源与 coclaw 环境变量快照（工号脱敏）
    :return: 含 X-Auth-Value, X-Emp-No, X-Tenant-Id, X-Lang-Id, Content-Type 的请求头
    :raises ValueError: 缺少 auth_token 或 emp_no 时
    """
    auth_config = (input_data.get("auth") or {}) if input_data else {}
    tenant_id = (
        auth_config.get("tenant_id")
        or os.getenv(RDCLOUD_TENANT_ID)
        or base_config.get("tenant_id", "ZTE")
    )
    auth_token = (
        auth_config.get("auth_token")
        or os.getenv(COCLAW_TOKEN_ENV)
        or os.getenv(RDCLOUD_AUTH_TOKEN)
    )
    emp_no = (
        auth_config.get("emp_no")
        or os.getenv(COCLAW_EMPNO_ENV)
        or os.getenv(RDCLOUD_EMP_NO)
    )

    auth_source: list = []
    if auth_config.get("auth_token") or auth_config.get("emp_no"):
        auth_source.append("输入数据")
    elif os.getenv(COCLAW_TOKEN_ENV) or os.getenv(COCLAW_EMPNO_ENV):
        auth_source.append("CoClaw环境变量(coclaw_empno/coclaw_token)")
    elif os.getenv(RDCLOUD_AUTH_TOKEN) or os.getenv(RDCLOUD_EMP_NO):
        auth_source.append("环境变量(RDCLOUD_*)")
    else:
        auth_source.append("配置文件")

    headers = {
        "X-Tenant-Id": tenant_id,
        "X-Auth-Value": auth_token or "",
        "X-Emp-No": emp_no or "",
        "X-Lang-Id": resolve_lang_id_from_env(),
        "Content-Type": "application/json",
    }

    if logger is not None:
        logger.info("开始获取认证信息")
        try:
            from common.log_helper import mask_emp_no_for_log as _mask_emp
        except ImportError:
            _mask_emp = lambda v: v  # type: ignore
        logger.info(
            "环境变量 %s=%s",
            COCLAW_EMPNO_ENV,
            _mask_emp(os.getenv(COCLAW_EMPNO_ENV)),
        )
        logger.info(
            "环境变量 %s=%s",
            COCLAW_TOKEN_ENV,
            "已设置" if os.getenv(COCLAW_TOKEN_ENV) else "未设置",
        )
        logger.info("认证信息来源: %s", "、".join(auth_source))

    if not headers["X-Auth-Value"]:
        raise ValueError(
            "缺少认证信息: auth_token (请通过输入 auth.auth_token 或环境变量 %s/%s 提供)"
            % (COCLAW_TOKEN_ENV, RDCLOUD_AUTH_TOKEN)
        )
    if not headers["X-Emp-No"]:
        raise ValueError(
            "缺少认证信息: emp_no (请通过输入 auth.emp_no 或环境变量 %s/%s 提供)"
            % (COCLAW_EMPNO_ENV, RDCLOUD_EMP_NO)
        )
    return headers


def _import_user_search():
    """延迟导入 user_search，避免循环依赖。"""
    script_dir = Path(__file__).resolve().parent
    user_search_path = script_dir / "user_search.py"
    if not user_search_path.exists():
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location("user_search", user_search_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def resolve_person_to_emp_no(
    value: Any,
    headers: Dict[str, str],
    log=None,
) -> str:
    """
    将人员取值解析为工号。@me/me → 当前用户工号；否则调用 user_search 取第一个匹配用户工号。
    :param value: 用户输入的取值（@me、姓名、工号等）
    :param headers: 认证头，含 X-Emp-No、X-Auth-Value
    :param log: 可选 logger，用于打日志
    :return: 工号字符串，无法解析时返回原 value
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    val = (value or "").strip()
    if val.lower() in ("@me", "me"):
        return (headers.get("X-Emp-No") or "").strip() or value
    emp_no = (headers.get("X-Emp-No") or "").strip()
    token = (headers.get("X-Auth-Value") or "").strip()
    if not token or not emp_no:
        if log:
            log.warning("人员字段解析: 缺少 token/emp_no，保留原值")
        return value
    mod = _import_user_search()
    if not mod or not hasattr(mod, "search_users"):
        if log:
            log.warning("人员字段解析: user_search 不可用，保留原值")
        return value
    try:
        result = mod.search_users(emp_no, token, val)
    except Exception as e:
        if log:
            log.warning("人员字段解析: search_users 异常 value=%s, %s", val[:30], e)
        return value
    if not result or result.get("status") != "success":
        if log:
            log.warning("人员字段解析: 未成功 keyword=%s，保留原值", val[:30])
        return value
    users = result.get("users") or []
    if not users:
        return value
    first_emp = (users[0].get("empNo") or "").strip()
    return first_emp if first_emp else value
