"""MCP 工具管理接口"""
import hashlib
import json
import time
import httpx
import os
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Header, Query
from pydantic import BaseModel, Field
from mcp_server.mcp_manager import McpToolService
from mcp_server.mcp_mock import get_default_mock_mcp_list, merge_mock_mcp_list
from common.schemas import ListApiResponse
from config import get_logger, app_config

# 清除代理环境变量，确保直连
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
os.environ.pop("all_proxy", None)

logger = get_logger(__name__)

router = APIRouter(prefix="/mcp", tags=["mcp"])

# ==================== 数据模型 ====================

class McpToolItem(BaseModel):
    """MCP 工具项"""
    id: int = Field(..., description="工具ID")
    componentId: int = Field(..., description="组件ID")
    name: str = Field(..., description="工具名称")
    description: str = Field(..., description="工具描述")
    type: str = Field(..., description="工具类型")
    url: str = Field(..., description="工具URL")
    status: str = Field(default="unknown", description="工具状态: available/unavailable/unknown")


# MCP 工具列表响应类型别名
McpToolsResponse = ListApiResponse[McpToolItem]


# ==================== 接口实现 ====================

MCP_TOOLS_API_URL = app_config.MCP_TOOLS_API_URL
MCP_ALL_TOOLS_API_URL = app_config.MCP_ALL_TOOLS_API_URL
_user_tools_response_cache: Dict[str, Tuple[float, str, ListApiResponse]] = {}


def _mcp_list_fingerprint(mcp_list: List[dict]) -> str:
    normalized = [
        {
            "id": item.get("id"),
            "componentId": item.get("componentId"),
            "name": item.get("name"),
            "url": item.get("url"),
            "type": item.get("type"),
        }
        for item in (mcp_list or [])
    ]
    payload = json.dumps(normalized, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _get_user_tools_cache(user_id: str, fingerprint: str) -> Optional[ListApiResponse]:
    cached = _user_tools_response_cache.get(user_id)
    if not cached:
        return None
    ts, fp, response = cached
    ttl = int(getattr(app_config, "MCP_USER_TOOLS_CACHE_TTL", 120) or 120)
    if time.monotonic() - ts > ttl:
        _user_tools_response_cache.pop(user_id, None)
        return None
    if fp != fingerprint:
        return None
    return response


def _set_user_tools_cache(user_id: str, fingerprint: str, response: ListApiResponse) -> None:
    _user_tools_response_cache[user_id] = (time.monotonic(), fingerprint, response)


def _normalize_mcp_item(raw_item: dict) -> dict:
    """标准化 MCP 工具字段，避免 null/空值导致后续校验与连接失败。"""
    return {
        "id": raw_item.get("id") or 0,
        "componentId": raw_item.get("componentId") or 0,
        "name": (raw_item.get("name") or "").strip(),
        "description": raw_item.get("description") or "",
        "type": (raw_item.get("type") or "http").strip().lower(),
        "url": (raw_item.get("url") or "").strip(),
    }


async def _fetch_external_mcp_list(user_id: str, auth_token: str = "") -> List[dict]:
    """调用外部 cmtool 接口获取 MCP 列表，失败时抛出异常。

    外部接口认证：请求头 x-emp-no（用户 id）+ x-auth-value（token）
    """
    headers = {"x-emp-no": user_id}
    if auth_token:
        headers["x-auth-value"] = auth_token
    async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
        response = await client.get(
            MCP_TOOLS_API_URL,
            headers=headers,
        )

        if response.status_code != 200:
            logger.error(
                f"[MCP API] 调用外部接口失败, status: {response.status_code}, body: {response.text}"
            )
            raise HTTPException(
                status_code=502,
                detail=f"获取 MCP 工具列表失败: 外部接口返回 {response.status_code}",
            )

        external_data = response.json()

    if external_data.get("code") != 0 or not external_data.get("status"):
        logger.error(f"[MCP API] 外部接口返回错误: {external_data.get('msg')}")
        raise HTTPException(
            status_code=502,
            detail=f"获取 MCP 工具列表失败: {external_data.get('msg', '未知错误')}",
        )

    return external_data.get("data", []) or []


async def _fetch_external_mcp_all_list(user_id: str = "", auth_token: str = "") -> List[dict]:
    """调用外部 cmtool 接口获取全量 MCP 列表（不限用户），失败时抛出异常。

    外部接口认证：请求头 x-emp-no（用户 id）+ x-auth-value（token）
    """
    headers = {}
    if user_id:
        headers["x-emp-no"] = user_id
    if auth_token:
        headers["x-auth-value"] = auth_token
    async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
        response = await client.get(MCP_ALL_TOOLS_API_URL, headers=headers or None)

        if response.status_code != 200:
            logger.error(
                f"[MCP API] 调用外部 all 接口失败, status: {response.status_code}, body: {response.text}"
            )
            raise HTTPException(
                status_code=502,
                detail=f"获取全量 MCP 工具列表失败: 外部接口返回 {response.status_code}",
            )

        external_data = response.json()

    if external_data.get("code") != 0 or not external_data.get("status"):
        logger.error(f"[MCP API] 外部 all 接口返回错误: {external_data.get('msg')}")
        raise HTTPException(
            status_code=502,
            detail=f"获取全量 MCP 工具列表失败: {external_data.get('msg', '未知错误')}",
        )

    return external_data.get("data", []) or []


async def _resolve_mcp_source_list(user_id: str, auth_token: str = "") -> tuple[List[dict], str]:
    """解析 MCP 来源：Mock / 外部 / 外部+Mock 合并 / 失败回退 Mock。"""
    if app_config.MCP_USE_MOCK:
        mock_list = get_default_mock_mcp_list()
        logger.info(f"[MCP API] 使用 Mock MCP 列表（共 {len(mock_list)} 个）")
        return mock_list, "mock"

    try:
        external_raw = await _fetch_external_mcp_list(user_id, auth_token)
        normalized = [_normalize_mcp_item(item) for item in external_raw]
        if app_config.MCP_FALLBACK_TO_MOCK:
            merged = merge_mock_mcp_list(normalized)
            source = "external+mock" if len(merged) > len(normalized) else "external"
        else:
            merged = normalized
            source = "external"
        logger.info(f"[MCP API] 外部接口返回 {len(normalized)} 个 MCP 工具")
        return merged, source
    except HTTPException:
        if not app_config.MCP_FALLBACK_TO_MOCK:
            raise
        mock_list = get_default_mock_mcp_list()
        logger.warning(
            f"[MCP API] 外部接口不可用，回退 Mock MCP（共 {len(mock_list)} 个）"
        )
        return mock_list, "mock-fallback"
    except httpx.TimeoutException:
        if not app_config.MCP_FALLBACK_TO_MOCK:
            raise HTTPException(status_code=504, detail="获取 MCP 工具列表超时")
        mock_list = get_default_mock_mcp_list()
        logger.warning("[MCP API] 外部接口超时，回退 Mock MCP")
        return mock_list, "mock-fallback"
    except httpx.RequestError as e:
        if not app_config.MCP_FALLBACK_TO_MOCK:
            raise HTTPException(status_code=502, detail=f"网络错误: {str(e)}")
        mock_list = get_default_mock_mcp_list()
        logger.warning(f"[MCP API] 外部接口网络错误，回退 Mock MCP: {e}")
        return mock_list, "mock-fallback"


async def _validate_and_build_response(
    mcp_list: List[dict],
    user_id: str,
    source: str,
    *,
    force_refresh: bool = False,
):
    if not mcp_list:
        return ListApiResponse.success(data=[], msg="成功")

    mcp_service = McpToolService()
    validated_tools = await mcp_service.validate_mcp_servers(
        mcp_list, force_refresh=force_refresh
    )

    result_data = []
    for tool in validated_tools:
        result_data.append(
            McpToolItem(
                id=tool.get("id") or 0,
                componentId=tool.get("componentId") or 0,
                name=(tool.get("name") or "").strip(),
                description=tool.get("description") or "",
                type=(tool.get("type") or "http").strip().lower(),
                url=(tool.get("url") or "").strip(),
                status=(tool.get("status") or "unknown"),
            )
        )

    logger.info(
        f"[MCP API] 用户 {user_id} 获取 {len(result_data)} 个 MCP 工具 (source={source})"
    )
    return ListApiResponse.success(data=result_data, msg="成功")


def _build_tools_without_validation(mcp_list: List[dict]) -> ListApiResponse:
    result_data = []
    for tool in mcp_list:
        result_data.append(
            McpToolItem(
                id=tool.get("id") or 0,
                componentId=tool.get("componentId") or 0,
                name=(tool.get("name") or "").strip(),
                description=tool.get("description") or "",
                type=(tool.get("type") or "http").strip().lower(),
                url=(tool.get("url") or "").strip(),
                status="unknown",
            )
        )
    return ListApiResponse.success(data=result_data, msg="成功")


@router.get("/tools", response_model=McpToolsResponse, summary="获取用户的MCP工具列表")
async def get_mcp_tools(
    user_id: str = Query(..., description="用户ID"),
    validate: bool = Query(True, description="是否探测 MCP 连通性；false 时仅返回列表"),
    refresh: bool = Query(False, description="是否强制重新探测（忽略服务端缓存）"),
    x_emp_no: str = Header(default="", description="用户ID（前端自动携带，透传外部接口）"),
    x_auth_value: str = Header(default="", description="用户 token（前端自动携带，透传外部接口）"),
):
    """
    获取用户的 MCP 工具列表，并验证每个工具的可用状态。

    - validate=false：跳过连通性探测，快速返回（status=unknown）
    - refresh=true：忽略缓存并重新探测
    开发环境默认 MCP_USE_MOCK=true，返回本地 mrm_doc_mcp.py 测试服务。
    """
    if not user_id:
        raise HTTPException(status_code=400, detail="userId 不能为空")

    try:
        # 认证优先取请求头（前端自动携带），query 的 user_id 兜底
        emp_no = x_emp_no or user_id
        mcp_list, source = await _resolve_mcp_source_list(emp_no, x_auth_value)
        if not validate:
            return _build_tools_without_validation(mcp_list)

        fingerprint = _mcp_list_fingerprint(mcp_list)
        if not refresh:
            cached = _get_user_tools_cache(user_id, fingerprint)
            if cached is not None:
                logger.info(f"[MCP API] 用户 {user_id} 命中工具列表响应缓存")
                return cached

        response = await _validate_and_build_response(
            mcp_list, user_id, source, force_refresh=refresh
        )
        _set_user_tools_cache(user_id, fingerprint, response)
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[MCP API] 获取 MCP 工具列表异常: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {str(e)}")


@router.get("/tools/all", response_model=McpToolsResponse, summary="获取全量MCP工具列表")
async def get_all_mcp_tools(
    validate: bool = Query(True, description="是否探测 MCP 连通性；false 时仅返回列表"),
    refresh: bool = Query(False, description="是否强制重新探测（忽略服务端缓存）"),
    user_id: str = Query("", description="用户ID"),
    x_emp_no: str = Header(default="", description="用户ID（前端自动携带，透传外部接口）"),
    x_auth_value: str = Header(default="", description="用户 token（前端自动携带，透传外部接口）"),
):
    """获取所有 MCP 工具（不限用户），含连通性验证。结构和 /mcp/tools 一致。"""
    import traceback

    try:
        # 认证优先取请求头（前端自动携带），query 的 user_id 兜底
        emp_no = x_emp_no or user_id
        external_raw = await _fetch_external_mcp_all_list(emp_no, x_auth_value)
        mcp_list = [_normalize_mcp_item(item) for item in external_raw]
        logger.info(f"[MCP API] 外部 all 接口返回 {len(mcp_list)} 个 MCP 工具")

        if not validate:
            return _build_tools_without_validation(mcp_list)

        fingerprint = _mcp_list_fingerprint(mcp_list)
        if not refresh:
            cached = _get_user_tools_cache("__all__", fingerprint)
            if cached is not None:
                logger.info(f"[MCP API] all 命中缓存 key=__all__")
                return cached

        response = await _validate_and_build_response(
            mcp_list, "", "external", force_refresh=refresh
        )
        if not refresh:
            _set_user_tools_cache("__all__", fingerprint, response)
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[MCP API] 获取全量 MCP 工具列表失败: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {str(e)}")
