import hashlib
import asyncio
import json
import logging
from typing import List, Optional, Any
from langchain_mcp_adapters.client import MultiServerMCPClient

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MCPManager")

class McpToolManager:
    def __init__(self, config: Optional[dict] = None):
        # 如果没有传入配置，使用默认配置
        self.config = config or {
            "mrm": {
                "transport": "http",
                "url": "http://127.0.0.1:6002/mcp"
            }
        }
        self.client = MultiServerMCPClient(self.config)
        self._cached_tools = []
        self._is_initialized = False

    async def get_mcp_toollist(self, timeout: int = 10) -> List[Any]:
        """
        获取 MCP 工具列表，带错误处理和超时控制
        
        Args:
            timeout: 请求超时时间（秒）
        Returns:
            tools: 工具列表，失败则返回空列表 []
        """
        try:
            logger.info(f"[MCP] 正在尝试从服务端加载工具列表...")
            
            # 使用 asyncio.wait_for 强制超时，防止因为服务端无响应导致协程死锁
            async with asyncio.timeout(timeout):
                # 获取工具列表
                tools = await self.client.get_tools()
                
                if not tools:
                    logger.warning("[MCP] 服务端返回的工具列表为空，请检查 MCP 服务端配置。")
                else:
                    logger.info(f"[MCP] 成功加载 {len(tools)} 个工具。")
                
                self._cached_tools = tools
                self._is_initialized = True
                return tools

        except asyncio.TimeoutError:
            logger.error(f"[MCP Error] 加载工具超时 (>{timeout}s)，请检查 MCP 服务地址是否可达。")
            return self._handle_failure()

        except ConnectionRefusedError:
            logger.error("[MCP Error] 连接被拒绝：MCP 服务端未启动或端口配置错误。")
            return self._handle_failure()

        except Exception as e:
            # 捕获其他未知异常（如 HTTP 500, Pydantic 解析错误等）
            logger.error(f"[MCP Error] 发生未知异常: {str(e)}")
            import traceback
            logger.debug(traceback.format_exc())
            return self._handle_failure()

    def _handle_failure(self) -> List:
        """失败时的处理逻辑：如果有缓存返回缓存，否则返回空"""
        if self._cached_tools:
            logger.info("[MCP] 使用上一次成功的工具列表缓存。")
            return self._cached_tools
        return []

    async def check_health(self) -> bool:
        """检查 MCP 服务是否健康"""
        try:
            await self.get_mcp_toollist(timeout=3)
            return True
        except:
            return False