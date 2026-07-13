"""Docker 沙箱管理。

提供 SandboxService 单例及降级逻辑：Docker 不可用时不抛异常，
仅禁用 execute 工具，文件读写仍走本地 FilesystemBackend。
"""

from typing import Optional

import asyncio

from config.config import app_config
from config import get_logger
from deep_agent_server.sandbox.exceptions import SandboxUserLimitExceeded

logger = get_logger(__name__)

_sandbox_service = None
_cleanup_task: Optional[asyncio.Task] = None


def is_docker_sdk_available() -> bool:
    try:
        import docker  # noqa: F401
        return True
    except ImportError:
        return False


def get_sandbox_service():
    """返回全局 SandboxService 单例。"""
    global _sandbox_service
    if _sandbox_service is None:
        from deep_agent_server.sandbox.docker_service import DockerSandboxService
        _sandbox_service = DockerSandboxService()
    return _sandbox_service


async def try_ensure_sandbox_running(user_id: str, session_id: str) -> bool:
    """预热/启动时尝试拉起沙箱；SandboxUserLimitExceeded 向上抛出。"""
    if not app_config.SANDBOX_ENABLED:
        return False
    if not is_docker_sdk_available():
        logger.warning(
            "[Sandbox] 未安装 docker 包，跳过沙箱预热。"
            "请 pip install docker 或设置 SANDBOX_ENABLED=false"
        )
        return False
    try:
        await get_sandbox_service().ensure_running(user_id, session_id)
        return True
    except SandboxUserLimitExceeded:
        raise
    except Exception as e:
        logger.warning(f"[Sandbox] 预热失败，降级无 execute: {e}")
        return False


async def get_sandbox_backend_factory_if_enabled(
    user_id: str,
    session_id: str,
    store=None,
    user_token: Optional[str] = None,
    *,
    agent_skills_root=None,
    agent_id: Optional[str] = None,
):
    """SANDBOX_ENABLED=false 或 Docker 不可用时返回 None（降级无 execute）。"""
    if not app_config.SANDBOX_ENABLED:
        return None
    if not is_docker_sdk_available():
        logger.warning(
            "[Sandbox] 未安装 docker 包，降级无 execute。"
            "请 pip install docker 或设置 SANDBOX_ENABLED=false"
        )
        return None
    try:
        return await get_sandbox_service().get_backend_factory(
            user_id,
            session_id=session_id,
            store=store,
            user_token=user_token,
            agent_skills_root=agent_skills_root,
            agent_id=agent_id,
        )
    except SandboxUserLimitExceeded:
        raise
    except Exception as e:
        logger.warning(f"[Sandbox] 不可用，降级无 execute: {e}")
        return None


async def get_sandbox_backend_if_enabled(
    user_id: str,
    session_id: str,
    store=None,
    user_token: Optional[str] = None,
):
    """向后兼容：返回 backend 实例。"""
    factory = await get_sandbox_backend_factory_if_enabled(
        user_id, session_id, store=store, user_token=user_token
    )
    if factory is None:
        return None
    return factory(None)


async def refresh_container_workspace_permissions(user_id: str, session_id: str) -> None:
    """Skill sync 等工作区变更后，同步容器内 /workspace 与 .sandbox 权限。"""
    if not app_config.SANDBOX_ENABLED:
        return
    try:
        svc = get_sandbox_service()
        row = await svc._get_db_session(user_id, session_id)
        cid = (row or {}).get("container_id")
        if cid and (row or {}).get("status") == "running":
            await asyncio.to_thread(
                svc._fix_container_workspace_permissions, cid, user_id
            )
    except Exception as e:
        logger.debug(f"[Sandbox] refresh workspace permissions 跳过: {e}")


async def touch_session_sandbox(user_id: str, session_id: str) -> None:
    """续期 running 沙箱 idle TTL（如 HITL 等待审批期间）。"""
    if not app_config.SANDBOX_ENABLED:
        return
    try:
        svc = get_sandbox_service()
        row = await svc._get_db_session(user_id, session_id)
        if row and row.get("container_id") and row.get("status") == "running":
            await svc._touch(user_id, session_id)
    except Exception as e:
        logger.debug(f"[Sandbox] touch session 跳过: {e}")


async def release_session_sandbox(user_id: str, session_id: str) -> bool:
    """Run 结束后 stop 沙箱，保留 container 供 docker start 复用。"""
    if not app_config.SANDBOX_ENABLED:
        return False
    try:
        released = await get_sandbox_service().release(user_id, session_id)
        if released:
            from deep_agent_server.sandbox.status_notify import notify_sandbox_status_changed

            await notify_sandbox_status_changed(user_id)
        return released
    except Exception as e:
        logger.warning(f"[Sandbox] release session 失败: {e}")
        return False


async def stop_session_sandbox(user_id: str, session_id: str) -> None:
    if not app_config.SANDBOX_ENABLED:
        return
    try:
        await get_sandbox_service().stop(user_id, session_id)
        from deep_agent_server.sandbox.status_notify import notify_sandbox_status_changed

        await notify_sandbox_status_changed(user_id)
    except Exception as e:
        logger.warning(f"[Sandbox] stop session 失败: {e}")


async def stop_user_sandboxes(user_id: str) -> None:
    """停止用户全部 session 沙箱（删用户等 bulk 操作）。"""
    if not app_config.SANDBOX_ENABLED:
        return
    try:
        await get_sandbox_service().stop_all_for_user(user_id)
        from db import deep_storage
        await deep_storage.clear_user_sandbox_sessions(user_id)
    except Exception as e:
        logger.warning(f"[Sandbox] stop all 失败: {e}")


async def stop_user_sandbox(user_id: str) -> None:
    """向后兼容：停止用户全部沙箱。"""
    await stop_user_sandboxes(user_id)


async def get_sandbox_user_status(user_id: str) -> dict:
    if not app_config.SANDBOX_ENABLED:
        return {
            "user_id": user_id,
            "max_per_user": app_config.SANDBOX_MAX_PER_USER,
            "running_count": 0,
            "available_slots": app_config.SANDBOX_MAX_PER_USER,
            "sessions": [],
            "sandbox_enabled": False,
        }
    return await get_sandbox_service().get_user_status(user_id)


async def start_sandbox_cleanup_loop() -> None:
    """后台定期清理 ttl_at 已过期的沙箱（stop + remove + 删 DB 行）。"""
    global _cleanup_task
    if not app_config.SANDBOX_ENABLED or app_config.SANDBOX_CLEANUP_INTERVAL_MINUTES <= 0:
        return
    if not is_docker_sdk_available():
        return
    if _cleanup_task is not None:
        return

    interval = app_config.SANDBOX_CLEANUP_INTERVAL_MINUTES * 60
    svc = get_sandbox_service()

    async def _loop() -> None:
        while True:
            try:
                await asyncio.sleep(interval)
                removed = await svc.cleanup_expired()
                if removed:
                    logger.info(f"[Sandbox] TTL 清理已移除 {removed} 个沙箱")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[Sandbox] TTL 清理循环异常: {e}")

    _cleanup_task = asyncio.create_task(_loop())
    logger.info(
        f"[Sandbox] TTL 清理任务已启动，间隔 {app_config.SANDBOX_CLEANUP_INTERVAL_MINUTES} 分钟"
    )


async def stop_sandbox_cleanup_loop() -> None:
    global _cleanup_task
    if _cleanup_task is None:
        return
    _cleanup_task.cancel()
    try:
        await _cleanup_task
    except asyncio.CancelledError:
        pass
    _cleanup_task = None
