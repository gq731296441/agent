"""Sandbox 服务抽象。

定义 per-session Docker 沙箱生命周期接口，具体实现见 DockerSandboxService。
"""

from abc import ABC, abstractmethod
from typing import Optional

from deep_agent_server.sandbox.models import SandboxHandle, SandboxStatus


class SandboxService(ABC):
    """per (user, session) Docker 沙箱生命周期。"""

    @abstractmethod
    async def ensure_running(self, user_id: str, session_id: str) -> SandboxHandle:
        """确保沙箱处于 running 状态（必要时 create / unpause）。"""

    @abstractmethod
    async def pause(self, user_id: str, session_id: str) -> bool:
        """暂停沙箱（Docker pause）。"""

    @abstractmethod
    async def resume(self, user_id: str, session_id: str) -> bool:
        """恢复已暂停的沙箱。"""

    @abstractmethod
    async def stop(self, user_id: str, session_id: str) -> None:
        """停止沙箱（Docker stop）。"""

    @abstractmethod
    async def health_check(self, user_id: str, session_id: str) -> bool:
        """容器是否 healthy / running。"""

    @abstractmethod
    async def get_backend_factory(
        self,
        user_id: str,
        session_id: str,
        store=None,
        user_token: Optional[str] = None,
    ):
        """返回 deepagents BackendFactory（lambda rt -> backend）。"""

    async def get_backend(
        self,
        user_id: str,
        session_id: str,
        store=None,
        user_token: Optional[str] = None,
    ):
        factory = await self.get_backend_factory(
            user_id, session_id, store=store, user_token=user_token
        )
        return factory(None)

    async def get_status(self, user_id: str, session_id: str) -> Optional[SandboxStatus]:
        row = await self._get_db_session(user_id, session_id)
        if not row:
            return None
        try:
            return SandboxStatus(row.get("status") or "stopped")
        except ValueError:
            return SandboxStatus.STOPPED

    @abstractmethod
    async def _get_db_session(self, user_id: str, session_id: str):
        pass
