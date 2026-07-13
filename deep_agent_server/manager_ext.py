"""Deep Agent 创建路径 — 订阅/默认 Agent 解析。"""

import hashlib
import json
from typing import Any, List, Optional, Tuple

from config.config import app_config
from db import deep_storage, storage
from deep_agent_server.default_agent import build_default_agent_dict
from deep_agent_server.factory import create_deep_agent_instance
from deep_agent_server.sandbox.exceptions import SandboxUserLimitExceeded
from deep_agent_server.skills.selection_guard import SkillSelectionLockedError
from deep_agent_server.skills.session_mount import ensure_session_skill_mount
from deep_agent_server.skills.selector import (
    SkillSelector,
    effective_selected_skill_ids,
    match_skills,
    resolve_selected_pool_dirs,
    resolve_skill_dirs,
)
from mcp_server.mcp_manager import McpToolService


async def _session_agent_context(
    user_id: str,
    session_id: str,
    agent_id: str = None,
    user_token: str = None,
) -> Tuple[str, Optional[str], dict, str]:
    """返回 (agent_kind, resolved_agent_id, user_agent_dict, creator_user_id)。"""
    meta = await storage.get_session_meta(session_id) if session_id else None
    agent_kind = (meta or {}).get("agent_kind") or "default"

    if agent_kind == "subscription":
        resolved_id = agent_id or (meta or {}).get("agent_id")
        if not resolved_id and meta:
            resolved_id = str(meta["agent_id"]) if meta.get("agent_id") else None
        if not resolved_id:
            return agent_kind, None, {}, user_id
        agent = await deep_storage.resolve_agent_for_caller(
            str(resolved_id), user_id, for_runtime=True
        )
        if not agent:
            return agent_kind, str(resolved_id), {}, user_id
        creator = agent.get("user_id") or user_id
        return agent_kind, str(resolved_id), agent, creator

    # default
    return "default", None, build_default_agent_dict(user_id, user_token), user_id


async def resolve_mcp_list(
    user_id: str,
    agent_id: str,
    payload_mcp: list,
    *,
    agent_kind: str = "default",
    creator_user_id: str = "",
) -> list:
    """合并 agent 模板 MCP 绑定与请求 payload；订阅体按绑定 id + 创建者 catalog 过滤。"""
    if agent_kind == "default":
        # 默认智能体：严格以请求 capabilities.mcp 为准；空列表 = 用户未勾选任何 MCP。
        # 开发环境不再在此处静默注入 Mock（与 skills 空列表语义对齐）。
        return list(payload_mcp or [])

    binding_ids = await deep_storage.get_agent_mcp_ids(agent_id)
    if not binding_ids:
        return []

    if payload_mcp:
        allowed = set(binding_ids)
        return [m for m in payload_mcp if int(m.get("id") or 0) in allowed]

    from api.mcp_api import _resolve_mcp_source_list

    try:
        # 订阅智能体：用创建者的 user_id 获取 MCP 目录，而非订阅者
        catalog, _ = await _resolve_mcp_source_list(creator_user_id or user_id)
        id_to_item = {int(i.get("id")): i for i in catalog if i.get("id")}
        return [id_to_item[mid] for mid in binding_ids if mid in id_to_item]
    except Exception:
        if app_config.MCP_USE_MOCK or app_config.ENVIRONMENT == "development":
            return [{"id": mid} for mid in binding_ids]
        return []


async def _resolve_skill_dirs_for_agent(
    agent_kind: str,
    user_id: str,
    agent_id: str,
    creator_user_id: str,
) -> List[str]:
    if agent_kind == "subscription" and agent_id:
        from deep_agent_server.workspace import get_agent_skills_dir

        root = get_agent_skills_dir(agent_id)
        if not root.is_dir():
            return []
        return [
            str(p) for p in sorted(root.iterdir())
            if p.is_dir() and (p / "SKILL.md").is_file()
        ]
    return resolve_skill_dirs(user_id, None)


async def create_deep_agent_for_session(
    manager,
    user_id: str,
    user_token: str,
    session_id: str,
    mcp_list: list,
    agent_id: str = None,
    user_message: str = None,
    always_on_only: bool = False,
    skip_sandbox: bool = False,
    selected_skill_ids: Optional[List[str]] = None,
) -> Tuple[Any, Optional[str]]:
    """
    为指定 session 创建或复用 Deep Agent。

    缓存 key 由 agent_id + config_version + llm_fp + MCP + token + skill 组成。
    """
    agent_kind, resolved_agent_id, user_agent, creator_user_id = await _session_agent_context(
        user_id, session_id, agent_id, user_token
    )
    if agent_kind == "subscription" and not user_agent:
        return None, f"订阅智能体不存在或无权访问: {resolved_agent_id}"

    if agent_kind == "subscription" and not resolved_agent_id:
        return None, "订阅会话缺少 agent_id"

    mcp_list = await resolve_mcp_list(
        user_id,
        resolved_agent_id or "",
        mcp_list or [],
        agent_kind=agent_kind,
        creator_user_id=creator_user_id,
    )
    mcp_hash = manager._mcp_list_hash(mcp_list)
    token_fp = manager._token_fingerprint(user_token)

    skill_candidate_dirs = await _resolve_skill_dirs_for_agent(
        agent_kind, user_id, resolved_agent_id or "", creator_user_id
    )
    if selected_skill_ids is not None:
        selected_skill_ids = effective_selected_skill_ids(
            skill_candidate_dirs, selected_skill_ids
        )
    elif app_config.SANDBOX_ENABLED and not skip_sandbox:
        from deep_agent_server.skills.session_mount import resolve_mount_selected_skill_ids

        selected_skill_ids = await resolve_mount_selected_skill_ids(
            user_id, session_id, skill_candidate_dirs, None
        )
    if always_on_only:
        always_on_dirs = [
            m.skill_dir
            for m in SkillSelector.load_metas(skill_candidate_dirs)
            if m.always_on
        ]
        pool_dirs = resolve_selected_pool_dirs(always_on_dirs, selected_skill_ids)
    else:
        pool_dirs = resolve_selected_pool_dirs(skill_candidate_dirs, selected_skill_ids)
    exposed_metas = SkillSelector.load_metas(pool_dirs)
    _, trigger_metas = match_skills(
        user_id,
        resolved_agent_id,
        user_message,
        bound_dirs=skill_candidate_dirs,
        always_on_only=always_on_only,
        selected_skill_ids=selected_skill_ids,
    )
    selection_hash = SkillSelector.match_hash(
        selected_skill_ids if selected_skill_ids is not None else ["*"]
    )
    skill_hash = SkillSelector.match_hash(
        sorted(m.skill_id for m in exposed_metas)
    ) + ":" + selection_hash
    sandbox_tag = "sbx-mount-v1" if app_config.SANDBOX_ENABLED else "no-sbx"

    config_version = int(user_agent.get("config_version") or 0)
    from agent_server.llm import create_agent_llm_for_user, llm_config_fingerprint

    try:
        model = await deep_storage.get_user_secret(user_id, "llm_model") or ""
        base_url = await deep_storage.get_user_secret(user_id, "llm_base_url") or ""
        llm_fp = llm_config_fingerprint(model, base_url) if model and base_url else "none"
    except Exception:
        llm_fp = "unknown"

    cache_agent_key = resolved_agent_id or "default"
    cache_key = (
        f"deep:{session_id}:{cache_agent_key}:cfg{config_version}:llm{llm_fp}:"
        f"{mcp_hash}:{token_fp}:{skill_hash}:{sandbox_tag}"
    )

    if app_config.AGENT_CACHE_ENABLED and cache_key in manager._agent_cache:
        cached, cached_fp, cached_mh, cached_aid, cached_sh = manager._agent_cache[cache_key]
        if (
            cached_fp == token_fp
            and cached_mh == mcp_hash
            and cached_aid == cache_agent_key
            and cached_sh == skill_hash
        ):
            return cached, None

    mcp_service = McpToolService()
    mcp_tools = []
    if mcp_list:
        try:
            mcp_tools = await mcp_service.get_mcp_toollist(mcp_list)
        except Exception as e:
            return None, f"加载 MCP 工具失败: {e}"

    # 合并内置工具（无需 MCP 配置，始终可用）
    from builtin_tools import get_builtin_tools
    builtin_tools = get_builtin_tools(user_id=user_id, user_token=user_token)
    mcp_tools = builtin_tools + mcp_tools  # 内置工具排前面，LLM 优先看到

    # 订阅智能体的 agent_skills_root 与 agent_id_for_sandbox
    agent_skills_root = None
    agent_id_for_sandbox = None
    if agent_kind == "subscription" and resolved_agent_id:
        from deep_agent_server.workspace import get_agent_skills_dir

        agent_skills_root = get_agent_skills_dir(resolved_agent_id)
        agent_id_for_sandbox = resolved_agent_id

    if app_config.SANDBOX_ENABLED and not skip_sandbox:
        try:
            await ensure_session_skill_mount(
                user_id,
                session_id,
                skill_candidate_dirs,
                selected_skill_ids,
                manager=manager,
                agent_id=agent_id_for_sandbox,
            )
        except SkillSelectionLockedError as lock_err:
            return None, lock_err.to_error_payload()

    try:
        llm = await create_agent_llm_for_user(user_id, agent_kind)
        from deep_agent_server.artifact.tool_wrapper import SpillContext, wrap_tools_with_spill

        spill_ctx = SpillContext(
            store=manager.store,
            user_id=user_id,
            session_id=session_id,
        )
        mcp_tools = wrap_tools_with_spill(mcp_tools, spill_ctx)

        wrapper, err = await create_deep_agent_instance(
            user_id=user_id,
            session_id=session_id,
            user_agent=user_agent,
            mcp_tools=mcp_tools,
            checkpointer=manager.checkpointer,
            store=manager.store,
            exposed_skill_metas=exposed_metas,
            loaded_skill_metas=trigger_metas,
            selected_skill_ids=selected_skill_ids,
            agent_kind=agent_kind,
            skip_sandbox=skip_sandbox,
            user_token=user_token,
            agent_skills_root=agent_skills_root,
            agent_id_for_sandbox=agent_id_for_sandbox,
            llm=llm,
        )
    except SandboxUserLimitExceeded as limit_err:
        return None, limit_err.to_error_payload()
    except ValueError as ve:
        return None, str(ve)
    if err:
        return None, err

    if app_config.AGENT_CACHE_ENABLED:
        manager._agent_cache[cache_key] = (wrapper, token_fp, mcp_hash, cache_agent_key, skill_hash)

    return wrapper, None
