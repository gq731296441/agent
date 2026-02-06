import hashlib
import asyncio
import json
import logging
import traceback
from typing import List, Optional, Any, Dict, Tuple
from langchain_mcp_adapters.client import MultiServerMCPClient

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MCPManager")

class McpToolService:
    def __init__(self, config: Optional[dict] = None):
        pass
    
    async def _validate_single_server(self, name: str, config: Dict[str, str]) -> Tuple[str, bool, List[Any], str]:
        """
        验证单个 MCP 服务器
        """
        try:
            # 隔离实例化，防止单个坏服务器的 UnboundLocalError 击垮全局
            temp_client = MultiServerMCPClient({name: config})
            async with asyncio.timeout(10):
                tools = await temp_client.get_tools()
                return name, True, tools, "available"
        except Exception as e:
            # 使用 traceback 完整记录异常，包括 ExceptionGroup
            err_detail = "".join(traceback.format_exception(e)).strip()
            logger.error(f"MCP Server [{name}] validation failed: {err_detail}")
            return name, False, [], "unavailable"
    
    async def _run_batch_validation(self, mcp_list: List[Dict]) -> List[Tuple]:
        """
        统一处理并发验证逻辑
        """
        mcp_config = self._build_mcp_config(mcp_list)
        tasks = [
            self._validate_single_server(name, cfg) 
            for name, cfg in mcp_config.items()
        ]
        # 并发执行，允许部分任务抛出异常（由内部 try-except 处理）
        return await asyncio.gather(*tasks)

    # ==================== 业务接口 ====================

    async def validate_mcp_servers(self, server_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        验证给定服务器列表并返回带状态的结构（用于前端展示）
        """
        raw_results = await self._run_batch_validation(server_list)
        
        # 建立 名字 -> 状态 的映射
        status_map = {name: status for name, success, tools, status in raw_results}
        
        # 组装返回数据，保留前端传入的所有原始字段（如 id）并附加 status
        response = []
        for s in server_list:
            item = s.copy()
            item["status"] = status_map.get(s["name"], "unavailable")
            response.append(item)
            
        return response

    async def get_mcp_toollist(self, server_list: List[Dict[str, Any]]) -> List[Any]:
        """
        获取所有可用的 MCP 工具对象（过滤不可用的）
        """
        raw_results = await self._run_batch_validation(server_list)
        
        all_mcp_tools = []
        for name, is_success, tools, status in raw_results:
            if is_success:
                all_mcp_tools.extend(tools)
        
        logger.info(f"MCP 工具加载完成，共计 {len(all_mcp_tools)} 个可用工具")
        return all_mcp_tools

    def _build_mcp_config(self, mcp_list: list) -> Dict[str, Dict[str, str]]:
        """
        将前端传入的列表结构转换为 MultiServerMCPClient 所需的字典配置
        """
        mcp_config = {}
        for item in mcp_list:
            name = item.get("name")
            if not name:
                continue
            mcp_config[name] = {
                "transport": item.get("type", "http"),
                "url": item.get("url", "")
            }
        return mcp_config