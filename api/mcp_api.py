"""MCP 工具管理接口"""
import asyncio
import httpx
import os
from typing import Optional, List, Any
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from db import storage
from mcp_server.mcp_manager import McpToolService
from common.schemas import ListApiResponse
from config import get_logger

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

# 外部 MCP 工具列表接口地址
MCP_TOOLS_API_URL = "https://aip.zte.com.cn/trial/cmtool/api/v1/component/mcp/mine"


def _normalize_mcp_item(raw_item: dict) -> dict:
    """标准化外部 MCP 工具字段，避免 null/空值导致后续校验与连接失败。"""
    return {
        "id": raw_item.get("id") or 0,
        "componentId": raw_item.get("componentId") or 0,
        "name": (raw_item.get("name") or "").strip(),
        "description": raw_item.get("description") or "",
        "type": (raw_item.get("type") or "http").strip().lower(),
        "url": (raw_item.get("url") or "").strip(),
    }


@router.get("/tools", response_model=McpToolsResponse, summary="获取用户的MCP工具列表")
async def get_mcp_tools(
    user_id: str = Query(..., description="用户ID")
):
    """
    获取用户的 MCP 工具列表，并验证每个工具的可用状态。
    
    Args:
        user_id: 用户ID（路径参数）
        
    Returns:
        带有 status 字段的 MCP 工具列表
    """
    if not user_id:
        raise HTTPException(status_code=400, detail="userId 不能为空")
    
    try:
        # 1. 调用外部接口获取用户的 MCP 工具列表
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                MCP_TOOLS_API_URL,
                headers={"userNumber": user_id}
            )
            
            if response.status_code != 200:
                logger.error(f"[MCP API] 调用外部接口失败, status: {response.status_code}, body: {response.text}")
                raise HTTPException(
                    status_code=502, 
                    detail=f"获取 MCP 工具列表失败: 外部接口返回 {response.status_code}"
                )
            
            external_data = response.json()
            
        # 2. 检查外部接口返回状态
        if external_data.get("code") != 0 or not external_data.get("status"):
            logger.error(f"[MCP API] 外部接口返回错误: {external_data.get('msg')}")
            raise HTTPException(
                status_code=502, 
                detail=f"获取 MCP 工具列表失败: {external_data.get('msg', '未知错误')}"
            )
        
        # 3. 提取 data 数据
        mcp_list = external_data.get("data", [])
        
        if not mcp_list:
            # 没有工具，直接返回空列表
            return ListApiResponse.success(data=[], msg="成功")

        # 标准化字段，处理 description/type/url 等为 null 的情况
        normalized_mcp_list = [_normalize_mcp_item(item) for item in mcp_list]
        
        # 4. 调用 McpToolService 验证工具状态
        mcp_service = McpToolService()
        validated_tools = await mcp_service.validate_mcp_servers(normalized_mcp_list)
        
        # 5. 组装返回数据
        result_data = []
        for tool in validated_tools:
            result_data.append(McpToolItem(
                id=tool.get("id") or 0,
                componentId=tool.get("componentId") or 0,
                name=(tool.get("name") or "").strip(),
                description=tool.get("description") or "",
                type=(tool.get("type") or "http").strip().lower(),
                url=(tool.get("url") or "").strip(),
                status=(tool.get("status") or "unknown")
            ))
        
        logger.info(f"[MCP API] 用户 {user_id} 获取到 {len(result_data)} 个 MCP 工具")
        
        return ListApiResponse.success(data=result_data, msg="成功")
        
    except HTTPException:
        # 直接向上抛出 HTTPException
        raise
    except httpx.TimeoutException:
        logger.error(f"[MCP API] 调用外部接口超时")
        raise HTTPException(status_code=504, detail="获取 MCP 工具列表超时")
    except httpx.RequestError as e:
        logger.error(f"[MCP API] 调用外部接口网络错误: {e}")
        raise HTTPException(status_code=502, detail=f"网络错误: {str(e)}")
    except Exception as e:
        logger.error(f"[MCP API] 获取 MCP 工具列表异常: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {str(e)}")

