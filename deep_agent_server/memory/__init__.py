"""User Memory 模块门面。

注意：deepagents 虚拟路径 /memories/ 与本模块无关（后者为 StoreBackend session 文件记忆）。
"""

from deep_agent_server.memory.user_memory import UserMemoryService, get_user_memory_service

__all__ = ["UserMemoryService", "get_user_memory_service"]
