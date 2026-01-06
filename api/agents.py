from fastapi import APIRouter, HTTPException
from typing import Dict, Any, Optional
from agent_server.agent_manager import AgentManager

router = APIRouter(prefix="/api/agents", tags=["agents"])

@router.post("/{user_id}/{session_id}")
async def create_or_update_agent_config(user_id: str, session_id: str, config: Dict[str, Any]):
    """创建或更新 agent 配置"""
    success = await AgentManager.create_agent_config(user_id, session_id, config)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save agent config")
    return {'status': 'ok', 'user_id': user_id, 'session_id': session_id}

@router.get("/{user_id}/{session_id}")
async def get_agent_config(user_id: str, session_id: str) -> Dict[str, Any]:
    """获取 agent 配置"""
    config = await AgentManager.get_agent_config(user_id, session_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Agent config not found")
    return {'config': config}

@router.put("/{user_id}/{session_id}")
async def update_agent_config(user_id: str, session_id: str, config: Dict[str, Any]):
    """更新 agent 配置"""
    success = await AgentManager.update_agent_config(user_id, session_id, config)
    if not success:
        raise HTTPException(status_code=404, detail="Agent config not found or update failed")
    return {'status': 'ok', 'user_id': user_id, 'session_id': session_id}

@router.delete("/{user_id}/{session_id}")
async def delete_agent_config(user_id: str, session_id: str):
    """删除 agent 配置"""
    success = await AgentManager.delete_agent_config(user_id, session_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete agent config")
    return {'status': 'ok', 'user_id': user_id, 'session_id': session_id}

@router.get("/{user_id}")
async def get_user_agent_configs(user_id: str) -> Dict[str, list]:
    """获取用户的所有 agent 配置"""
    configs = await AgentManager.get_user_agent_configs(user_id)
    return {'configs': configs}