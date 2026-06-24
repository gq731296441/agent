"""Docker SDK 客户端工厂 — 支持本地 socket 与远程 ssh:// / tcp://。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional
from urllib.parse import urlparse

from config.config import app_config
from config import get_logger

if TYPE_CHECKING:
    import docker

logger = get_logger(__name__)

_client: Optional["docker.DockerClient"] = None


def format_sandbox_docker_host_for_log(host: str) -> str:
    """日志用脱敏展示（隐藏密码等）。"""
    raw = (host or "").strip()
    if not raw:
        return "(local socket / DOCKER_HOST env)"
    try:
        parsed = urlparse(raw)
        if parsed.scheme:
            netloc = parsed.hostname or parsed.netloc or "?"
            if parsed.port:
                netloc = f"{netloc}:{parsed.port}"
            user = f"{parsed.username}@" if parsed.username else ""
            return f"{parsed.scheme}://{user}{netloc}"
    except Exception:
        pass
    return raw.split("@")[-1][:80]


def get_docker_client() -> "docker.DockerClient":
    """
    返回 Docker SDK 客户端。

    - 未设置 SANDBOX_DOCKER_HOST：docker.from_env()（本地 socket / 环境变量 DOCKER_HOST）
    - 已设置：DockerClient(base_url=SANDBOX_DOCKER_HOST)，如 ssh://sandbox@10.0.0.1
    """
    global _client
    if _client is not None:
        return _client

    import docker

    host = (app_config.SANDBOX_DOCKER_HOST or "").strip()
    if host:
        logger.info(
            f"[Sandbox] Docker client remote: {format_sandbox_docker_host_for_log(host)}"
        )
        _client = docker.DockerClient(base_url=host)
    else:
        _client = docker.from_env()
    return _client


def reset_docker_client_cache() -> None:
    """测试用：清除单例缓存。"""
    global _client
    if _client is not None:
        try:
            _client.close()
        except Exception:
            pass
    _client = None
