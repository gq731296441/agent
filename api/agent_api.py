from fastapi import APIRouter, Query
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from db import storage
from common.schemas import ApiResponse, ListApiResponse
from config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])


# ==================== 数据模型 ====================

class AgentConfigItem(BaseModel):
    """Agent 配置项"""
    session_id: str = Field(..., description="会话ID")
    session_name: str = Field(default="", description="会话名称")
    model_name: str = Field(default="", description="模型名称")
    config: Dict[str, Any] = Field(default={}, description="配置信息")
    updated_at: str = Field(default="", description="更新时间")


class McpIdsItem(BaseModel):
    """MCP ID 列表项"""
    selected_mcp_ids: List[str] = Field(default=[], description="选中的 MCP ID 列表")


# ==================== 接口实现 ====================

@router.post("/{session_id}", response_model=ApiResponse)
async def create_or_update_agent_config(
    session_id: str,
    model_name: str = Query("nebulacoder-v8", description="Agent 模型名称"),
    config: Optional[Dict[str, Any]] = None
):
    """创建或更新 agent 配置（包括模型名和 MCP ID 列表）"""
    try:
        success = await storage.save_agent_config(session_id, model_name, config or {})
        if not success:
            return ApiResponse.error(msg="保存 Agent 配置失败")
        return ApiResponse.success(msg="Agent 配置已保存")
    except Exception as e:
        logger.error(f"[Agent API] 创建/更新 Agent 配置失败: {e}")
        return ApiResponse.error(msg=f"保存 Agent 配置失败: {str(e)}")


@router.get("/{session_id}", response_model=ApiResponse[AgentConfigItem])
async def get_agent_config(session_id: str):
    """获取 agent 配置（包括模型名、config 和时间戳）"""
    try:
        config = await storage.get_agent_config(session_id)
        if config is None:
            return ApiResponse.error(msg="Agent 配置不存在", code=404)
        item = AgentConfigItem(
            session_id=session_id,
            model_name=config.get("model_name", ""),
            config=config.get("config", {}),
            updated_at=config.get("updated_at", ""),
        )
        return ApiResponse.success(data=item, msg="成功")
    except Exception as e:
        logger.error(f"[Agent API] 获取 Agent 配置失败: {e}")
        return ApiResponse.error(msg=f"获取 Agent 配置失败: {str(e)}")


@router.put("/{session_id}", response_model=ApiResponse)
async def update_agent_config(
    session_id: str,
    model_name: Optional[str] = Query(None, description="新的模型名称"),
    config: Optional[Dict[str, Any]] = None
):
    """更新 agent 配置（支持部分更新）"""
    try:
        success = await storage.update_agent_config(session_id, model_name, config)
        if not success:
            return ApiResponse.error(msg="Agent 配置不存在或更新失败", code=404)
        return ApiResponse.success(msg="Agent 配置已更新")
    except Exception as e:
        logger.error(f"[Agent API] 更新 Agent 配置失败: {e}")
        return ApiResponse.error(msg=f"更新 Agent 配置失败: {str(e)}")


@router.delete("/{session_id}", response_model=ApiResponse)
async def delete_agent_config(session_id: str):
    """删除 agent 配置"""
    try:
        success = await storage.delete_agent_config(session_id)
        if not success:
            return ApiResponse.error(msg="删除 Agent 配置失败")
        return ApiResponse.success(msg="Agent 配置已删除")
    except Exception as e:
        logger.error(f"[Agent API] 删除 Agent 配置失败: {e}")
        return ApiResponse.error(msg=f"删除 Agent 配置失败: {str(e)}")


@router.get("/", response_model=ListApiResponse[AgentConfigItem])
async def get_user_agent_configs(
    user_id: str = Query(..., description="用户ID")
):
    """获取用户的所有 agent 配置"""
    try:
        configs = await storage.get_user_agent_configs(user_id)
        config_items = [
            AgentConfigItem(
                session_id=c.get("session_id", ""),
                session_name=c.get("session_name", ""),
                model_name=c.get("model_name", ""),
                config=c.get("config", {}),
                updated_at=c.get("updated_at", ""),
            )
            for c in configs
        ]
        return ListApiResponse.success(data=config_items, msg="成功")
    except Exception as e:
        logger.error(f"[Agent API] 获取用户 Agent 配置列表失败: {e}")
        return ListApiResponse.error(msg=f"获取 Agent 配置列表失败: {str(e)}")


@router.get("/{session_id}/mcp-ids", response_model=ApiResponse[McpIdsItem])
async def get_agent_mcp_ids(session_id: str):
    """获取 agent 配置中选中的 MCP ID 列表"""
    try:
        mcp_ids = await storage.get_agent_config_with_mcp_ids(session_id)
        if mcp_ids is None:
            return ApiResponse.error(msg="Agent 配置不存在", code=404)
        return ApiResponse.success(data=McpIdsItem(selected_mcp_ids=mcp_ids), msg="成功")
    except Exception as e:
        logger.error(f"[Agent API] 获取 MCP ID 列表失败: {e}")
        return ApiResponse.error(msg=f"获取 MCP ID 列表失败: {str(e)}")


@router.put("/{session_id}/mcp-ids", response_model=ApiResponse)
async def update_agent_mcp_ids(
    session_id: str,
    mcp_ids: List[str] = Query([], description="MCP 配置 ID 列表")
):
    """更新 agent 配置中的 MCP ID 列表"""
    try:
        success = await storage.update_agent_mcp_ids(session_id, mcp_ids)
        if not success:
            return ApiResponse.error(msg="Agent 配置不存在", code=404)
        return ApiResponse.success(msg="MCP ID 列表已更新")
    except Exception as e:
        logger.error(f"[Agent API] 更新 MCP ID 列表失败: {e}")
        return ApiResponse.error(msg=f"更新 MCP ID 列表失败: {str(e)}")