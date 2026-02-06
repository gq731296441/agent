from fastapi import APIRouter, HTTPException, Query
from typing import Dict, Any, Optional, List
from db import storage

router = APIRouter(prefix="/api/agents", tags=["agents"])

@router.post("/{session_id}")
async def create_or_update_agent_config(
    session_id: str,
    model_name: str = Query("nebulacoder-pro-7.0", description="Agent 模型名称"),
    config: Optional[Dict[str, Any]] = None
):
    """创建或更新 agent 配置（包括模型名和 MCP ID 列表）"""
    success = await storage.save_agent_config(session_id, model_name, config or {})
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save agent config")
    return {
        'status': 'ok',
        'session_id': session_id,
        'model_name': model_name
    }

@router.get("/{session_id}")
async def get_agent_config(session_id: str) -> Dict[str, Any]:
    """获取 agent 配置（包括模型名、config 和时间戳）"""
    config = await storage.get_agent_config(session_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Agent config not found")
    return config

@router.put("/{session_id}")
async def update_agent_config(
    session_id: str,
    model_name: Optional[str] = Query(None, description="新的模型名称"),
    config: Optional[Dict[str, Any]] = None
):
    """更新 agent 配置（支持部分更新）"""
    success = await storage.update_agent_config(session_id, model_name, config)
    if not success:
        raise HTTPException(status_code=404, detail="Agent config not found or update failed")
    return {'status': 'ok', 'session_id': session_id}

@router.delete("/{session_id}")
async def delete_agent_config(session_id: str):
    """删除 agent 配置"""
    success = await storage.delete_agent_config(session_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete agent config")
    return {'status': 'ok', 'session_id': session_id}

@router.get("/")
async def get_user_agent_configs(
    user_id: str = Query(..., description="用户ID")
) -> Dict[str, List[Dict[str, Any]]]:
    """获取用户的所有 agent 配置"""
    configs = await storage.get_user_agent_configs(user_id)
    return {'configs': configs}

@router.get("/{session_id}/mcp-ids")
async def get_agent_mcp_ids(session_id: str) -> Dict[str, List[str]]:
    """获取 agent 配置中选中的 MCP ID 列表"""
    mcp_ids = await storage.get_agent_config_with_mcp_ids(session_id)
    if mcp_ids is None:
        raise HTTPException(status_code=404, detail="Agent config not found")
    return {'selected_mcp_ids': mcp_ids}

@router.put("/{session_id}/mcp-ids")
async def update_agent_mcp_ids(
    session_id: str,
    mcp_ids: List[str] = Query([], description="MCP 配置 ID 列表")
):
    """更新 agent 配置中的 MCP ID 列表"""
    success = await storage.update_agent_mcp_ids(session_id, mcp_ids)
    if not success:
        raise HTTPException(status_code=404, detail="Agent config not found")
    return {'status': 'ok', 'session_id': session_id, 'selected_mcp_ids': mcp_ids}