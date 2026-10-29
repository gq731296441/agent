import hashlib
import asyncio
import json
import logging
import traceback
import time
import httpx
from typing import List, Optional, Any, Dict, Tuple
from langchain_core.tools.base import ToolException
from langchain_mcp_adapters.client import MultiServerMCPClient

from config.config import app_config
from mcp_server.tool_name_utils import normalize_tools_for_llm

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MCPManager")

# 内部 MCP 鉴权请求头：x-auth-value = SSE/WS 的 user_token，x-emp-no = 用户 id
MCP_HEADER_EMP_NO = "x-emp-no"
MCP_HEADER_AUTH_VALUE = "x-auth-value"
_HTTP_TRANSPORTS = {"http", "sse", "streamable_http", "streamable-http"}


def _token_fingerprint(user_token: str) -> str:
    return hashlib.sha256((user_token or "").encode()).hexdigest()[:16]


def parse_mcp_item_headers(raw: Any) -> Dict[str, str]:
    """解析 MCP 条目自带的 headers（dict 或 JSON 字符串）。"""
    headers = raw
    if isinstance(headers, str):
        try:
            headers = json.loads(headers)
        except Exception:
            return {}
    if not isinstance(headers, dict):
        return {}
    parsed: Dict[str, str] = {}
    for key, value in headers.items():
        if key is None or value is None:
            continue
        name = str(key).strip()
        if not name:
            continue
        parsed[name] = str(value).strip()
    return parsed


def build_mcp_request_headers(
    item_headers: Dict[str, str] | None = None,
    *,
    user_id: str = "",
    user_token: str = "",
) -> Dict[str, str]:
    """合并 MCP 条目 headers，并默认注入当前会话的内部鉴权头。

    SSE / WebSocket 传入的 user_token → x-auth-value，user_id → x-emp-no。
    这两项以当前会话身份为准，覆盖条目里可能残留的同名头。
    """
    merged: Dict[str, str] = dict(item_headers or {})
    emp_no = str(user_id or "").strip()
    auth_value = str(user_token or "").strip()
    if emp_no:
        merged[MCP_HEADER_EMP_NO] = emp_no
    if auth_value:
        merged[MCP_HEADER_AUTH_VALUE] = auth_value
    return {k: v for k, v in merged.items() if k and v}


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


# MCP 工具参数校验错误关键词（用于区分 LLM 参数错误 vs 服务端内部错误）
_MCP_PARAM_ERROR_KEYWORDS = [
    "validation error",
    "missing required argument",
    "field required",
    "extra fields not permitted",
    "type_error",
    "value_error",
    "none is not an allowed",
    "not a valid",
    "validationerror",
    "pydantic",
    "argument",
    "parameter",
    "input should be",
    "unexpected value",
    "invalid type",
]


def _is_param_validation_error(err_msg: str) -> bool:
    """判断 MCP 工具错误是否为参数校验错误（LLM 可自行修正）。"""
    err_lower = err_msg.lower()
    return any(kw in err_lower for kw in _MCP_PARAM_ERROR_KEYWORDS)


def _wrap_mcp_tools_error(tools: List[Any]) -> List[Any]:
    """包装 MCP 工具 _arun：将 ToolException 转为错误字符串返回，使 LLM 可自修正。

    MCP 服务端返回 isError=True 时，langchain_mcp_adapters 会抛出 ToolException。
    原逻辑 re-raise 该异常，导致其逃逸 ToolNode（因 _default_handle_tool_errors
    只处理 ToolInvocationError），agent 直接停止。

    改为返回 (error_content, None) 元组（匹配 content_and_artifact 格式），
    ToolNode 会将其包装为 ToolMessage 返回给 LLM，LLM 可阅读错误信息并自行修正参数重试。
    """
    for tool in tools:
        if hasattr(tool, "_arun"):
            _orig = tool._arun
            async def _wrapped(*args, _orig=_orig, config=None, run_manager=None, **kwargs):
                try:
                    return await _orig(*args, config=config, run_manager=run_manager, **kwargs)
                except ToolException as e:
                    err_msg = str(e)
                    if _is_param_validation_error(err_msg):
                        error_content = (
                            f"工具调用参数错误：{err_msg}\n\n"
                            f"请根据以上错误信息修正工具参数后重新调用该工具。"
                        )
                    else:
                        error_content = (
                            f"MCP 工具调用失败：{err_msg}\n\n"
                            f"可能是 MCP 服务端内部错误，可尝试重新调用或告知用户检查服务端状态。"
                        )
                    # 返回 (content, artifact) 元组，匹配 response_format="content_and_artifact"
                    return (error_content, None)
            tool._arun = _wrapped
    return tools


class McpToolService:
    _validation_cache: Dict[str, Tuple[float, List[Tuple]]] = {}
    _server_validation_cache: Dict[str, Tuple[float, Tuple]] = {}

    def __init__(self, config: Optional[dict] = None):
        pass

    def _server_cache_key(self, name: str, config: Dict[str, Any]) -> str:
        url = (config.get("url") or "").strip()
        transport = (config.get("transport") or "http").strip().lower()
        headers = config.get("headers") if isinstance(config.get("headers"), dict) else {}
        safe_headers: Dict[str, str] = {}
        for key, value in headers.items():
            if str(key).lower() == MCP_HEADER_AUTH_VALUE:
                safe_headers[str(key)] = _token_fingerprint(str(value))
            else:
                safe_headers[str(key)] = str(value)
        payload = json.dumps(
            {
                "name": name,
                "url": url,
                "transport": transport,
                "headers": safe_headers,
            },
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

    def _list_cache_key(
        self,
        mcp_list: List[Dict[str, Any]],
        user_id: str = "",
        user_token: str = "",
    ) -> str:
        """生成 MCP 列表缓存键（url + headers + name + id + 会话身份）。"""
        normalized = []
        for item in mcp_list or []:
            normalized.append({
                "id": item.get("id"),
                "name": item.get("name"),
                "url": (item.get("url") or "").strip(),
                "type": (item.get("type") or "http").strip().lower(),
                "headers": parse_mcp_item_headers(item.get("headers")),
            })
        normalized.sort(key=lambda x: (str(x.get("id")), x.get("name") or ""))
        payload = json.dumps(
            {
                "items": normalized,
                "emp": str(user_id or "").strip(),
                "token_fp": _token_fingerprint(user_token),
            },
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
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
        self,
        mcp_list: List[Dict],
        *,
        force_refresh: bool = False,
        user_id: str = "",
        user_token: str = "",
    ) -> List[Tuple]:
        """
        统一处理并发验证逻辑（单 MCP 级 TTL 缓存 + 列表级缓存）
        """
        if not force_refresh:
            cache_key = self._list_cache_key(mcp_list, user_id=user_id, user_token=user_token)
            cached = self._get_cached_validation(cache_key)
            if cached is not None:
                logger.info(f"MCP 验证命中列表缓存 key={cache_key[:12]}... 共 {len(cached)} 个服务器")
                return cached

        mcp_config = self._build_mcp_config(
            mcp_list, user_id=user_id, user_token=user_token
        )
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
            list_key = self._list_cache_key(
                mcp_list, user_id=user_id, user_token=user_token
            )
            self._set_cached_validation(list_key, results)
        return results

    # ==================== 业务接口 ====================

    async def validate_mcp_servers(
        self,
        server_list: List[Dict[str, Any]],
        *,
        force_refresh: bool = False,
        user_id: str = "",
        user_token: str = "",
    ) -> List[Dict[str, Any]]:
        """
        验证给定服务器列表并返回带状态的结构（用于前端展示）
        """
        raw_results = await self._run_batch_validation(
            server_list,
            force_refresh=force_refresh,
            user_id=user_id,
            user_token=user_token,
        )
        
        # 建立 名字 -> 状态 的映射
        status_map = {name: status for name, success, tools, status in raw_results}
        
        # 组装返回数据，保留前端传入的所有原始字段（如 id）并附加 status
        response = []
        for s in server_list:
            item = s.copy()
            item["status"] = status_map.get(s["name"], "unavailable")
            response.append(item)
            
        return response

    async def get_mcp_toollist(
        self,
        server_list: List[Dict[str, Any]],
        *,
        user_id: str = "",
        user_token: str = "",
    ) -> List[Any]:
        """
        获取所有可用的 MCP 工具对象（过滤不可用的）
        """
        raw_results = await self._run_batch_validation(
            server_list, user_id=user_id, user_token=user_token
        )

        all_mcp_tools = []
        for name, is_success, tools, status in raw_results:
            if is_success:
                all_mcp_tools.extend(tools)

        all_mcp_tools = normalize_tools_for_llm(all_mcp_tools)
        all_mcp_tools = _wrap_mcp_tools_error(all_mcp_tools)
        logger.info(f"MCP 工具加载完成，共计 {len(all_mcp_tools)} 个可用工具")
        return all_mcp_tools

    def _build_mcp_config(
        self,
        mcp_list: list,
        user_id: str = "",
        user_token: str = "",
    ) -> Dict[str, Dict[str, Any]]:
        """
        将前端传入的列表结构转换为 MultiServerMCPClient 所需的字典配置。

        HTTP / SSE 传输默认带上 x-emp-no、x-auth-value（来自当前 SSE/WS 会话）。
        """
        mcp_config = {}
        for item in mcp_list:
            name = item.get("name")
            if not name:
                continue
            transport = (item.get("type") or "http").strip().lower()
            server_config: Dict[str, Any] = {
                "transport": transport,
                "url": (item.get("url") or "").strip(),
                # 显式禁用系统代理，避免企业代理影响内网 MCP 连接
                "httpx_client_factory": _create_no_proxy_async_client,
            }
            if transport in _HTTP_TRANSPORTS:
                headers = build_mcp_request_headers(
                    parse_mcp_item_headers(item.get("headers")),
                    user_id=user_id,
                    user_token=user_token,
                )
                if headers:
                    server_config["headers"] = headers
                    logger.debug(
                        "MCP [%s] 注入内部鉴权头 x-emp-no=%s x-auth-value=%s",
                        name,
                        "set" if headers.get(MCP_HEADER_EMP_NO) else "empty",
                        "set" if headers.get(MCP_HEADER_AUTH_VALUE) else "empty",
                    )
            mcp_config[name] = server_config
        return mcp_config