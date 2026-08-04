"""Langfuse 观测集成（可选）。

在 Deep Agent 的 LangChain callback 链上挂载 Langfuse CallbackHandler，
将每次 run 的 LLM 调用、工具执行、子 Agent 分支以 trace 树形式发送到
Langfuse 展示（自托管或云）。

启用条件（全部满足才挂载，否则零副作用）：
- 配置项 LANGFUSE_ENABLED=true
- LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY 非空
- langfuse SDK 已安装（import 失败时优雅降级）

凭据优先级：进程环境变量（LANGFUSE_PUBLIC_KEY 等）> 项目配置项。
"""

import os

from config.config import app_config
from config import get_logger

logger = get_logger(__name__)

# 项目配置项 → Langfuse SDK 环境变量
_ENV_MAP = (
    ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_PUBLIC_KEY"),
    ("LANGFUSE_SECRET_KEY", "LANGFUSE_SECRET_KEY"),
    ("LANGFUSE_HOST", "LANGFUSE_HOST"),
)


def ensure_langfuse_env() -> None:
    """将项目配置中的 Langfuse 凭据同步到进程环境（setdefault，不覆盖已导出变量）。

    在挂载 handler 前调用，保证 .env 里配置的项目字段也能被 langfuse SDK 读到。
    """
    for cfg_name, env_name in _ENV_MAP:
        value = (getattr(app_config, cfg_name, "") or "").strip()
        if value:
            os.environ.setdefault(env_name, value)


def langfuse_available() -> bool:
    """检查是否满足挂载条件（开关 + 凭据 + SDK 可用）。"""
    if not app_config.LANGFUSE_ENABLED:
        return False
    if not (app_config.LANGFUSE_PUBLIC_KEY and app_config.LANGFUSE_SECRET_KEY):
        logger.warning(
            "[Langfuse] LANGFUSE_ENABLED=true 但缺少 LANGFUSE_PUBLIC_KEY / "
            "LANGFUSE_SECRET_KEY，Langfuse 观测未启用"
        )
        return False
    try:
        import langfuse  # noqa: F401
        from langfuse.langchain import CallbackHandler  # noqa: F401
    except Exception:
        logger.warning("[Langfuse] SDK 未安装或导入失败，Langfuse 观测未启用")
        return False
    return True


def attach_langfuse_handler(config: dict) -> dict:
    """条件挂载 Langfuse CallbackHandler 到 run config 的 callbacks。

    未启用 / 凭据缺失 / SDK 不可用时返回原 config，不影响现有链路。
    重复调用幂等（已挂载则跳过）。
    """
    if not langfuse_available():
        return config
    try:
        from langfuse.langchain import CallbackHandler
    except Exception:
        return config

    cfg = dict(config)
    callbacks = list(cfg.get("callbacks") or [])
    for cb in callbacks:
        if type(cb).__name__ == "LangchainCallbackHandler":
            return cfg  # 已挂载，幂等跳过
    handler = CallbackHandler()
    callbacks.append(handler)
    cfg["callbacks"] = callbacks
    logger.info(
        f"[Langfuse] 观测已启用 host={app_config.LANGFUSE_HOST or '默认(云)'} "
        f"public_key={app_config.LANGFUSE_PUBLIC_KEY[:12]}..."
    )
    return cfg
