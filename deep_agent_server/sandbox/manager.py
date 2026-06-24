"""向后兼容：SandboxManager 已迁移至 DockerSandboxService。"""

from deep_agent_server.sandbox.docker_service import DockerSandboxService, SandboxManager

__all__ = ["SandboxManager", "DockerSandboxService"]
