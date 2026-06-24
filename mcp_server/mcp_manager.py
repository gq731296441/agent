import hashlib
import asyncio
import json
import logging
import traceback
import time
import httpx
from typing import List, Optional, Any, Dict, Tuple
from langchain_mcp_adapters.client import MultiServerMCPClient

from config.config import app_config
from mcp_server.tool_name_utils import normalize_tools_for_llm

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MCPManager")


def _create_no_proxy_async_client(
    headers: Dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    """创建忽略系统代理的 HTTP 客户端（用于 MCP 连接）。"""
    kwargs: Dict[str, Any] = {
        "follow_redirects": True,
        "trust_env": False,
        "timeout": timeout or httpx.Timeout(30.0),
    }
    if headers is not None:
        kwargs["headers"] = headers
    if auth is not None:
        kwargs["auth"] = auth
    return httpx.AsyncClient(**kwargs)

class McpToolService:
    _validation_cache: Dict[str, Tuple[float, List[Tuple]]] = {}
    _server_validation_cache: Dict[str, Tuple[float, Tuple]] = {}

    def __init__(self, config: Optional[dict] = None):
        pass

    def _server_cache_key(self, name: str, config: Dict[str, Any]) -> str:
        url = (config.get("url") or "").strip()
        transport = (config.get("transport") or "http").strip().lower()
        payload = json.dumps(
            {"name": name, "url": url, "transport": transport},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def _get_server_cached(self, cache_key: str) -> Optional[Tuple]:
        cached = self._server_validation_cache.get(cache_key)
        if not cached:
            return None
        ts, result = cached
        if time.monotonic() - ts > app_config.MCP_CACHE_TTL:
            self._server_validation_cache.pop(cache_key, None)
            return None
        return result

    def _set_server_cached(self, cache_key: str, result: Tuple) -> None:
        self._server_validation_cache[cache_key] = (time.monotonic(), result)

    def _list_cache_key(self, mcp_list: List[Dict[str, Any]]) -> str:
        """生成 MCP 列表缓存键（url + headers + name + id）。"""
        normalized = []
        for item in mcp_list or []:
            headers = item.get("headers")
            if isinstance(headers, str):
                try:
                    headers = json.loads(headers)
                except Exception:
                    headers = headers
            normalized.append({
                "id": item.get("id"),
                "name": item.get("name"),
                "url": (item.get("url") or "").strip(),
                "type": (item.get("type") or "http").strip().lower(),
                "headers": headers if isinstance(headers, dict) else {},
            })
        normalized.sort(key=lambda x: (str(x.get("id")), x.get("name") or ""))
        payload = json.dumps(normalized, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()

    def _get_cached_validation(self, cache_key: str) -> Optional[List[Tuple]]:
        cached = self._validation_cache.get(cache_key)
        if not cached:
            return None
        ts, results = cached
        if time.monotonic() - ts > app_config.MCP_CACHE_TTL:
            self._validation_cache.pop(cache_key, None)
            return None
        return results

    def _set_cached_validation(self, cache_key: str, results: List[Tuple]) -> None:
        self._validation_cache[cache_key] = (time.monotonic(), results)
    
    async def _validate_single_server(self, name: str, config: Dict[str, str]) -> Tuple[str, bool, List[Any], str]:
        """
        验证单个 MCP 服务器
        """
        try:
            # 隔离实例化，防止单个坏服务器的 UnboundLocalError 击垮全局
            temp_client = MultiServerMCPClient({name: config})
            timeout = float(getattr(app_config, "MCP_VALIDATE_TIMEOUT", 8.0) or 8.0)
            async with asyncio.timeout(timeout):
                tools = await temp_client.get_tools()
                return name, True, tools, "available"
        except Exception as e:
            err_detail = "".join(traceback.format_exception(e)).strip()
            logger.error(f"MCP Server [{name}] 连接失败: {e}\n{err_detail}")
            return name, False, [], "unavailable"
    
    async def _run_batch_validation(
        self, mcp_list: List[Dict], *, force_refresh: bool = False
    ) -> List[Tuple]:
        """
        统一处理并发验证逻辑（单 MCP 级 TTL 缓存 + 列表级缓存）
        """
        if not force_refresh:
            cache_key = self._list_cache_key(mcp_list)
            cached = self._get_cached_validation(cache_key)
            if cached is not None:
                logger.info(f"MCP 验证命中列表缓存 key={cache_key[:12]}... 共 {len(cached)} 个服务器")
                return cached

        mcp_config = self._build_mcp_config(mcp_list)
        results: List[Tuple] = []
        pending: List[Tuple[str, Dict[str, Any], str]] = []

        for name, cfg in mcp_config.items():
            server_key = self._server_cache_key(name, cfg)
            if not force_refresh:
                server_cached = self._get_server_cached(server_key)
                if server_cached is not None:
                    results.append(server_cached)
                    continue
            pending.append((name, cfg, server_key))

        if pending:
            validated = await asyncio.gather(
                *[self._validate_single_server(name, cfg) for name, cfg, _ in pending]
            )
            for (name, cfg, server_key), result in zip(pending, validated):
                self._set_server_cached(server_key, result)
                results.append(result)

        if not force_refresh:
            list_key = self._list_cache_key(mcp_list)
            self._set_cached_validation(list_key, results)
        return results

    # ==================== 业务接口 ====================

    async def validate_mcp_servers(
        self,
        server_list: List[Dict[str, Any]],
        *,
        force_refresh: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        验证给定服务器列表并返回带状态的结构（用于前端展示）
        """
        raw_results = await self._run_batch_validation(server_list, force_refresh=force_refresh)
        
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

        all_mcp_tools = normalize_tools_for_llm(all_mcp_tools)
        logger.info(f"MCP 工具加载完成，共计 {len(all_mcp_tools)} 个可用工具")
        return all_mcp_tools

    def _build_mcp_config(self, mcp_list: list) -> Dict[str, Dict[str, Any]]:
        """
        将前端传入的列表结构转换为 MultiServerMCPClient 所需的字典配置
        """
        mcp_config = {}
        for item in mcp_list:
            name = item.get("name")
            if not name:
                continue
            server_config: Dict[str, Any] = {
                "transport": (item.get("type") or "http").strip().lower(),
                "url": (item.get("url") or "").strip(),
                # 显式禁用系统代理，避免企业代理影响内网 MCP 连接
                "httpx_client_factory": _create_no_proxy_async_client,
            }
            headers = item.get("headers")
            if isinstance(headers, str):
                try:
                    headers = json.loads(headers)
                except Exception:
                    headers = None
            if isinstance(headers, dict) and headers:
                # 透传自定义请求头，适用于需要 Token/鉴权的 MCP 服务
                server_config["headers"] = headers
            mcp_config[name] = server_config
        return mcp_config