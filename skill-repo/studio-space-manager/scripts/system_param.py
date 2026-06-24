import os
import sys
import json
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin


MISSING_USER_AND_TOKEN_MESSAGE = (
    "无法获取用户账号和Token，请确保已设置 coclaw_empno 和 coclaw_token 环境变量，" "如仍报错请重启 Co-Claw 服务后重试"
)

ICENTER_ENV_PROD = "prod"
ICENTER_ENV_TEST = "test"
ICENTER_ENV = os.getenv("coclaw_env", ICENTER_ENV_PROD).strip().lower()
ICENTER_API_BASE_MAP = {
    ICENTER_ENV_PROD: "https://icenterapi.zte.com.cn",
    ICENTER_ENV_TEST: "https://icenter-systest.test.zte.com.cn",
}
IDRIVE_API_BASE_MAP = {
    ICENTER_ENV_PROD: "https://idrive.zte.com.cn",
    ICENTER_ENV_TEST: "https://idrive.test.zte.com.cn",
}

IDRIVE_CLIENT_ID_MAP ={
    ICENTER_ENV_PROD: "608078020840mk1rdko2rkzo98xmuojv",
    ICENTER_ENV_TEST: "496961366938kwdg72wqv8t2ypv0l3cq"
}



def get_idrive_client_id(env: str = "") -> str:
    """获取idrive_client_id，支持 prod/test 两种环境。"""
    selected_env = (env or ICENTER_ENV).strip().lower()
    if selected_env not in IDRIVE_CLIENT_ID_MAP:
        selected_env = ICENTER_ENV_PROD
    return IDRIVE_CLIENT_ID_MAP[selected_env]

def get_icenter_api_base(env: str = "") -> str:
    """获取 iCenter API 基础地址，支持 prod/test 两种环境。"""
    selected_env = (env or ICENTER_ENV).strip().lower()
    if selected_env not in ICENTER_API_BASE_MAP:
        selected_env = ICENTER_ENV_PROD
    return ICENTER_API_BASE_MAP[selected_env]

ICENTER_API_BASE = get_icenter_api_base()

def get_idrive_api_base(env: str = "") -> str:
    """获取 iCenter API 基础地址，支持 prod/test 两种环境。"""
    selected_env = (env or ICENTER_ENV).strip().lower()
    if selected_env not in IDRIVE_API_BASE_MAP:
        selected_env = ICENTER_ENV_PROD
    return IDRIVE_API_BASE_MAP[selected_env]

def build_icenter_api_url(path: str, env: str = "") -> str:
    """基于环境配置拼接 iCenter API 请求地址。"""
    normalized_path = path if path.startswith("/") else f"/{path}"
    return urljoin(f"{get_icenter_api_base(env).rstrip('/')}/", normalized_path.lstrip("/"))

def build_idrive_api_url(path: str, env: str = "") -> str:
    """基于环境配置拼接 iCenter API 请求地址。"""
    normalized_path = path if path.startswith("/") else f"/{path}"
    return urljoin(f"{get_idrive_api_base(env).rstrip('/')}/", normalized_path.lstrip("/"))


def _get_beijing_date() -> str:
    """返回当前北京时间 YYYYMMDD，用于 system 参数生成。"""
    beijing_tz = timezone(timedelta(hours=8))
    beijing_now = datetime.now(beijing_tz)
    return f"{beijing_now.year}{beijing_now.month:02d}{beijing_now.day:02d}"


def _get_hash_code_by_string(s: str) -> int:
    """与前端 getHashCodeByString 一致：对字符串做 31 倍累加哈希，结果为 32 位有符号整数。"""
    hash_val = 0
    if not s:
        return hash_val
    for c in s:
        hash_val = (31 * hash_val + ord(c)) & 0xFFFFFFFF
    if hash_val >= 0x80000000:
        hash_val -= 0x100000000
    return hash_val


def build_system_param(prefix: str = "space") -> str:
    """生成 system 参数：prefix + '-' + getHashCodeByString(当天北京时间 YYYYMMDD)。"""
    seed = _get_beijing_date()
    return f"{prefix}-{_get_hash_code_by_string(seed)}"


def _json_mode_log(message: str) -> None:
    """--json-output 时向 stderr 打一行日志，避免污染 stdout 的纯 JSON。"""
    print(f"[system_param] {message}", file=sys.stderr)


def get_user_and_token(json_output: bool = False, strict: bool = True):
    """
    返回 (user_id, token)，优先从环境变量获取。

    如果缺少必要环境变量，则统一输出错误信息并退出（与 copy_page 的行为保持一致）。
    """
    user_id = os.getenv("coclaw_empno", "")
    token = os.getenv("coclaw_token", "")

    if user_id and token:
        return user_id, token
    if not strict:
        return user_id, token

    error_msg = MISSING_USER_AND_TOKEN_MESSAGE
    if json_output:
        _json_mode_log(f"错误: {error_msg}")
        print(json.dumps({"status": "error", "message": error_msg}, ensure_ascii=False))
    else:
        print(f"❌ {error_msg}")
    sys.exit(1)

def get_uac_app_code_sec_key():
    """
    返回 (appCode, secKey)，优先从环境变量获取。
    """
    uac_app_code = os.getenv("app_code", "")
    uac_sec_key = os.getenv("secret_key", "")
    return uac_app_code, uac_sec_key
