"""Sandbox 领域模型。"""

from dataclasses import dataclass
from enum import Enum


class SandboxStatus(str, Enum):
    """Docker 沙箱容器状态。"""
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"


@dataclass
class SandboxHandle:
    """沙箱运行时句柄，sandbox_id 即 container_id。"""
    user_id: str
    session_id: str
    container_id: str
    status: SandboxStatus

    @property
    def sandbox_id(self) -> str:
        return self.container_id
