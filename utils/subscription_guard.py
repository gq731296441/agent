"""Ask / HITL 会话门禁（订阅智能体）。"""

from typing import Optional, Tuple

from db import deep_storage, storage


async def validate_ask_session(
    user_id: str,
    session_id: str,
    payload_agent_id: Optional[str],
) -> Tuple[bool, Optional[str]]:
    """
    校验是否允许 ask/resume。
    返回 (ok, error_message)。
    """
    meta = await storage.get_session_meta(session_id)
    if not meta:
        return True, None

    agent_kind = meta.get("agent_kind") or "default"
    session_agent_id = str(meta["agent_id"]) if meta.get("agent_id") else None

    if meta.get("subscription_frozen"):
        still_frozen = True
        if agent_kind == "subscription" and session_agent_id:
            if await deep_storage.is_subscription_active(user_id, session_agent_id):
                still_frozen = False
        if still_frozen:
            return False, "已取消订阅或智能体已下架，仅可查看历史"

    if not await deep_storage.has_user_llm_config(user_id):
        return False, "使用智能体前请先配置模型 API Key 与 Base URL"

    if agent_kind == "subscription":
        if not session_agent_id:
            return False, "订阅会话缺少 agent_id"
        if payload_agent_id and str(payload_agent_id) != session_agent_id:
            return False, "订阅智能体会话不可更换 agent_id"
        agent = await deep_storage.resolve_agent_for_caller(session_agent_id, user_id, for_runtime=True)
        if not agent:
            return False, "无权使用该订阅智能体"
        is_owner = agent.get("user_id") == user_id
        if agent.get("archived"):
            return False, "智能体已下架，仅可查看历史"
        if not is_owner and agent.get("visibility") == "private":
            return False, "智能体已设为私有，仅可查看历史"
        if not is_owner and not await deep_storage.is_subscription_active(user_id, session_agent_id):
            return False, "已取消订阅，仅可查看历史"

    return True, None
