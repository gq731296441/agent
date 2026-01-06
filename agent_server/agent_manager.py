import hashlib
import asyncio
import time
from typing import Dict, Any, Optional, List, Tuple
from agent_server.agents import LangGraphAgent
from agent_server import storage


class AgentManager:
    """
    AgentManager两层架构：
    
    第一层（数据库存储）：session_id -> config_json
    - 持久化存储 agent 配置（工具、模型、System Prompt）
    - 目的：服务器重启后恢复配置
    
    第二层（内存缓存）：config_hash -> CompiledAgent
    - 缓存已初始化的 Agent 实例
    - TTL 机制：10 分钟无活动则销毁
    - 目的：避免重复初始化，支持配置复用
    """

    def __init__(self, checkpointer=None, ttl_seconds: int = 600):
        """
        初始化 AgentManager
        
        Args:
            checkpointer: LangGraph checkpointer 实例
                         如果不提供，各 Agent 将使用内存保存器
            ttl_seconds: Agent 实例的生存时间（秒），默认 600 秒（10 分钟）
        """
        self.checkpointer = checkpointer
        self.ttl_seconds = ttl_seconds
        
        # 内存缓存：config_hash -> (agent_instance, last_access_time, ttl_task)
        self._agent_cache: Dict[str, Tuple[LangGraphAgent, float, Optional[asyncio.Task]]] = {}
        
        # 会话路由：session_id -> config_hash，支持一个 ws 处理多个 session，每条消息根据 session_id 动态获取对应的 agent
        self._session_to_config_hash: Dict[str, str] = {}

        # 初始化锁：用于防止多个请求同时初始化同一个配置的 Agent
        self._init_locks: Dict[str, asyncio.Lock] = {}

    def _compute_config_hash(self, config: Dict[str, Any]) -> str:
        """计算配置的哈希值，用于内存缓存复用"""
        import json
        config_str = json.dumps(config, sort_keys=True)
        # 使用 MD5 哈希算法，相同配置必然得到相同哈希值，内存复用：如果有 100 个用户都使用了默认的工具配置，
        # 他们的 config_hash 会完全一样。AgentManager 只需要在内存里维持 1 个 LangGraphAgent 实例。
        return hashlib.md5(config_str.encode()).hexdigest()

    def _reset_ttl(self, config_hash: str):
        """重置 Agent 实例的 TTL 计时器"""
        if config_hash not in self._agent_cache:
            return

        agent, _, old_task = self._agent_cache[config_hash]
        
        # 取消旧的 TTL 任务
        if old_task:
            old_task.cancel()

        # 创建新的 TTL 任务
        new_task = asyncio.create_task(self._ttl_handler(config_hash))
        
        # 更新缓存的最后访问时间和任务
        self._agent_cache[config_hash] = (agent, time.time(), new_task)

    async def _ttl_handler(self, config_hash: str):
        """处理 Agent 实例的 TTL，超时后销毁"""
        try:
            await asyncio.sleep(self.ttl_seconds)
            
            # TTL 超时，从缓存中移除
            if config_hash in self._agent_cache:
                del self._agent_cache[config_hash]
                # 清理对应的锁对象以释放内存
                self._init_locks.pop(config_hash, None)
                print(f"[TTL] Agent实例 {config_hash} 已过期回收")
        except asyncio.CancelledError:
            # TTL 被重置，正常退出
            pass

    async def get_or_create_agent(
        self, 
        user_id: str, 
        session_id: str
    ) -> Tuple[Optional[LangGraphAgent], Optional[str]]:
        """
        初始化 agent，建立 session_id ↔ config_hash 映射
        
        从数据库查询配置，初始化 Agent，建立会话与配置的对应关系
        
        Args:
            user_id: 用户 ID
            session_id: 会话 ID
            
        Returns:
            (agent_instance, error_message)
            成功：(agent, None)
            失败：(None, error_msg)
        """

        try:
            # 1. 先查路由表获取 config_hash
            config_hash = self._session_to_config_hash.get(session_id)
            
            # 2. 如果没缓存或缓存已失效，则需要进入初始化流程
            if not config_hash or config_hash not in self._agent_cache:
                config = await storage.get_agent_config(user_id, session_id)
                if not config:
                    config = {"tools": ["mcp"]} # 默认配置
                    await storage.save_agent_config(user_id, session_id, config)
                
                config_hash = self._compute_config_hash(config)
                self._session_to_config_hash[session_id] = config_hash

            # 3. 检查 Agent 实例是否已经在缓存中
            if config_hash in self._agent_cache:
                self._reset_ttl(config_hash)
                return self._agent_cache[config_hash][0], None

            # 使用异步锁防止并发初始化 ---
            if config_hash not in self._init_locks:
                self._init_locks[config_hash] = asyncio.Lock()
            
            async with self._init_locks[config_hash]:
                # 再次检查（双检锁），防止等锁期间其他线程已完成初始化
                if config_hash not in self._agent_cache:
                    print(f"[AgentManager] 正在初始化新 Agent 实例: {config_hash}")
                    agent = LangGraphAgent(checkpointer=self.checkpointer)
                    await agent.initialize_from_config(config)
                    
                    self._agent_cache[config_hash] = (agent, time.time(), None)
                    self._reset_ttl(config_hash)
                
            return self._agent_cache[config_hash][0], None

        except Exception as e:
            return None, f"初始化 Agent 失败: {str(e)}"

    def disconnect_connection(self, session_id: str = None):
        """
        断开会话连接时调用
        
        移除 session_id ↔ config_hash 的映射
        Agent 实例留在内存缓存中（TTL 将决定其生存时间）
        
        Args:
            session_id: 会话 ID。如果为 None，则清理所有会话映射
        """
        if session_id and session_id in self._session_to_config_hash:
            del self._session_to_config_hash[session_id]
            print(f"[AgentManager] 会话 {session_id} 的路由已清理")

    # ==================== 数据库层操作 ====================

    @staticmethod
    async def create_agent_config(user_id: str, session_id: str, config: Dict[str, Any]) -> bool:
        """创建 agent 配置"""
        try:
            await storage.save_agent_config(user_id, session_id, config)
            return True
        except Exception:
            return False

    @staticmethod
    async def get_agent_config(user_id: str, session_id: str) -> Optional[Dict[str, Any]]:
        """获取 agent 配置"""
        try:
            return await storage.get_agent_config(user_id, session_id)
        except Exception:
            return None

    @staticmethod
    async def update_agent_config(user_id: str, session_id: str, config: Dict[str, Any]) -> bool:
        """更新 agent 配置"""
        try:
            existing = await storage.get_agent_config(user_id, session_id)
            if existing is None:
                return False
            await storage.save_agent_config(user_id, session_id, config)
            return True
        except Exception:
            return False

    @staticmethod
    async def delete_agent_config(user_id: str, session_id: str) -> bool:
        """删除 agent 配置"""
        try:
            return await storage.delete_agent_config(user_id, session_id)
        except Exception:
            return False

    @staticmethod
    async def get_user_agent_configs(user_id: str) -> List[Dict[str, Any]]:
        """获取用户的所有 agent 配置"""
        try:
            return await storage.get_user_agent_configs(user_id)
        except Exception:
            return []

    # ==================== 缓存状态查询 ====================

    def get_cache_stats(self) -> Dict[str, Any]:
        """获取内存缓存的统计信息"""
        return {
            "cached_agents": len(self._agent_cache),
            "active_sessions": len(self._session_to_config_hash),
            "ttl_seconds": self.ttl_seconds,
            "config_hashes": list(self._agent_cache.keys())
        }
