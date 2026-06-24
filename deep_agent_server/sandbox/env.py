"""沙箱内用户身份环境变量（skill 脚本等 consume）。"""

from typing import Dict, Optional

# 与 CoClaw / skill 脚本约定一致
SANDBOX_ENV_EMPNO = "coclaw_empno"
SANDBOX_ENV_TOKEN = "colaw_token"


def build_container_user_env(user_id: str) -> Dict[str, str]:
    """容器创建时写入的稳定环境变量（per-user 容器）。"""
    uid = (user_id or "").strip()
    if not uid:
        return {}
    return {SANDBOX_ENV_EMPNO: uid}


def build_exec_user_env(user_id: str, user_token: Optional[str] = None) -> Dict[str, str]:
    """每次 docker exec 注入的环境变量（含可能轮换的 token）。"""
    env = build_container_user_env(user_id)
    token = (user_token or "").strip()
    if token:
        env[SANDBOX_ENV_TOKEN] = token
    return env


def shell_env_prefix(env: Dict[str, str]) -> str:
    """bash -lc 前缀：export KEY='value'（单引号转义）。"""
    if not env:
        return ""
    parts = []
    for key, val in env.items():
        safe = str(val).replace("'", "'\"'\"'")
        parts.append(f"export {key}='{safe}'")
    return " ".join(parts) + "; "
