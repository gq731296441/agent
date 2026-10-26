"""Deep Agent 创建路径 — 订阅/默认 Agent 解析。"""

import logging
from typing import Any, Dict, List, Optional, Tuple

_log = logging.getLogger(__name__)

# 追踪每个 session 的技能/MCP 选择，用于检测跨 turn 变更
_SESSION_CONFIG_STATE: Dict[str, Dict[str, str]] = {}
# key: "{user_id}#{session_id}", value: {"skills": selection_sig, "mcp": mcp_hash}

from config.config import app_config
from db import deep_storage, storage
from deep_agent_server.default_agent import build_default_agent_dict
from deep_agent_server.factory import create_deep_agent_instance
from deep_agent_server.sandbox.exceptions import SandboxUserLimitExceeded
from deep_agent_server.skills.selection_guard import SkillSelectionLockedError
from deep_agent_server.skills.session_mount import ensure_session_skill_mount
from deep_agent_server.skills.selector import (
    effective_selected_skill_ids,
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
    auth_token: str = "",
) -> list:
    """合并 agent 模板 MCP 绑定与请求 payload；订阅体按绑定 id + 全量目录过滤。"""
    if agent_kind == "default":
        # 默认智能体：严格以请求 capabilities.mcp 为准；空列表 = 用户未勾选任何 MCP。
        # 开发环境不再在此处静默注入 Mock（与 skills 空列表语义对齐）。
        return list(payload_mcp or [])

    binding_ids = await deep_storage.get_agent_mcp_ids(agent_id)
    if not binding_ids:
        return []

    if payload_mcp:
        allowed = set(binding_ids)
        # 绑定 ID 是组件 id；payload 条目优先用 componentId，缺省时退化为 id
        return [
            m for m in payload_mcp
            if int(m.get("componentId") or m.get("id") or 0) in allowed
        ]

    from api.mcp_api import _fetch_external_mcp_all_list, _normalize_mcp_item, _resolve_mcp_source_list
    from config import get_logger
    _mcp_log = get_logger(__name__)

    # 认证约束：外部 cmtool 接口要求 x-emp-no（用户 id）与 x-auth-value（token）必须匹配同一用户，
    # 不能用创建者 id + 订阅者 token（会返回 0002 令牌校验失败）。
    # 目录来源用 all（全量公共组件，订阅者未必订阅了创建者绑定的组件）；mock 环境走原逻辑。
    if app_config.MCP_USE_MOCK:
        try:
            catalog, source = await _resolve_mcp_source_list(user_id, auth_token)
        except Exception:
            catalog, source = [], ""
    else:
        try:
            raw = await _fetch_external_mcp_all_list(user_id, auth_token)
            catalog = [_normalize_mcp_item(item) for item in raw]
            source = "all"
        except Exception as e:
            _mcp_log.warning(f"[MCPResolve] 全量 MCP 目录获取失败({e})，回退订阅者 mine")
            try:
                catalog, source = await _resolve_mcp_source_list(user_id, auth_token)
            except Exception:
                catalog, source = [], ""

    try:
        # 绑定 ID 是组件 id（Component.id），目录条目通过 componentId 字段对应
        id_to_item = {int(i.get("componentId")): i for i in catalog if i.get("componentId")}
        matched = [id_to_item[mid] for mid in binding_ids if mid in id_to_item]
        missing_ids = [mid for mid in binding_ids if mid not in id_to_item]

        # ID 对不上时，用 snapshot_mcp_name 做名称兜底匹配
        if missing_ids:
            snapshots = await deep_storage.get_agent_mcp_snapshot_names(agent_id)
            seen_ids = {int(i.get("id")) for i in matched if i.get("id")}
            name_to_item = {}
            for item in catalog:
                name = (item.get("name") or "").strip()
                if name and name not in name_to_item:
                    name_to_item[name] = item
            for mid in missing_ids:
                snap_name = snapshots.get(mid)
                if snap_name and snap_name in name_to_item:
                    item = name_to_item[snap_name]
                    if int(item.get("id")) not in seen_ids:
                        matched.append(item)
                        seen_ids.add(int(item.get("id")))
                        _mcp_log.info(
                            f"[MCPResolve] id={mid} 未命中, 通过名称匹配: '{snap_name}' → id={item.get('id')}"
                        )
                    else:
                        _mcp_log.debug(
                            f"[MCPResolve] id={mid} 名称匹配 '{snap_name}' → id={item.get('id')} 已存在, 跳过"
                        )

        _mcp_log.debug(
            f"[MCPResolve] agent_id={agent_id} creator={creator_user_id} "
            f"bindings={binding_ids} catalog_size={len(catalog)} "
            f"matched={len(matched)} missing_ids={[m for m in missing_ids if m not in {int(i.get('id')) for i in matched}]}"
        )
        return matched
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
    # 内置技能对所有智能体类型默认可用
    from deep_agent_server.skills.selector import (
        _ensure_builtin_skill_in_user_dir,
        _ensure_builtin_skill_in_user_dir_for_agent,
        BUILTIN_SKILL_IDS,
    )
    from deep_agent_server.workspace import get_agent_skills_dir
    from config import get_logger
    _log = get_logger(__name__)

    builtin: List[str] = []
    for bid in BUILTIN_SKILL_IDS:
        if agent_kind == "subscription" and agent_id:
            # 订阅智能体：symlink 到 agent skills 目录
            d = _ensure_builtin_skill_in_user_dir_for_agent(agent_id, bid)
        else:
            d = _ensure_builtin_skill_in_user_dir(user_id, bid)
        if d:
            builtin.append(d)

    if agent_kind == "subscription" and agent_id:
        root = get_agent_skills_dir(agent_id)
        _log.debug(
            f"[SkillDirs] agent_id={agent_id} root={root} "
            f"exists={root.is_dir()} builtin={builtin}"
        )
        if not root.is_dir():
            _log.warning(f"[SkillDirs] agent skills 目录不存在: {root}")
            return builtin
        subdirs = [
            str(p) for p in sorted(root.iterdir())
            if p.is_dir() and (p / "SKILL.md").is_file()
        ]
        _log.debug(f"[SkillDirs] subdirs with SKILL.md: {subdirs}")
        # builtin 和 subdirs 可能有重叠，按顺序去重
        seen = set()
        result = []
        for d in builtin + subdirs:
            if d not in seen:
                seen.add(d)
                result.append(d)
        return result
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
        auth_token=user_token,
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

    # 生成 selected_skill_ids 的缓存签名
    selection_sig = ",".join(sorted(selected_skill_ids)) if selected_skill_ids else "*"
    sandbox_tag = "sbx-mount-v1" if app_config.SANDBOX_ENABLED else "no-sbx"

    # 检测同 session 内 skill/MCP 是否变化（跨 turn），push 给 runner 注入通知
    state_key = f"{user_id}#{session_id}"
    prev_state = _SESSION_CONFIG_STATE.get(state_key, {})
    config_changes: Dict[str, bool] = {}
    if prev_state:
        if prev_state.get("skills") != selection_sig:
            config_changes["skills"] = True
        if prev_state.get("mcp") != mcp_hash:
            config_changes["mcp"] = True
    _SESSION_CONFIG_STATE[state_key] = {"skills": selection_sig, "mcp": mcp_hash}

    config_version = int(user_agent.get("config_version") or 0)
    from agent_server.llm import create_agent_llm_for_user, llm_config_fingerprint

    try:
        await deep_storage.ensure_user_llm_profile(user_id)
        profile = await deep_storage.get_active_user_llm_profile(user_id) or {}
        model = profile.get("model") or ""
        base_url = profile.get("base_url") or ""
        llm_fp = llm_config_fingerprint(model, base_url) if model and base_url else "none"
    except Exception:
        llm_fp = "unknown"

    cache_agent_key = resolved_agent_id or "default"

    # 订阅智能体的 agent_skills_root 与 agent_id_for_sandbox
    agent_skills_root = None
    agent_id_for_sandbox = None
    if agent_kind == "subscription" and resolved_agent_id:
        from deep_agent_server.workspace import get_agent_skills_dir

        agent_skills_root = get_agent_skills_dir(resolved_agent_id)
        agent_id_for_sandbox = resolved_agent_id

    # 沙箱生命周期独立于 Agent 缓存：即使缓存命中，也必须确保沙箱存活。
    # 容器的 container_id 会随重建而变化，缓存的 Agent wrapper 可能持有旧 ID。
    sandbox_recreated = False
    if app_config.SANDBOX_ENABLED and not skip_sandbox:
        try:
            mount_result = await ensure_session_skill_mount(
                user_id,
                session_id,
                skill_candidate_dirs,
                selected_skill_ids,
                manager=manager,
                agent_id=agent_id_for_sandbox,
            )
            sandbox_recreated = mount_result.recreated
        except SkillSelectionLockedError as lock_err:
            return None, lock_err.to_error_payload()
        except SandboxUserLimitExceeded as limit_err:
            # 配额超限是用户可感知、可自行处理的业务错误（前端有专门弹窗），
            # 必须上报，不能被下面的降级分支吞成"无 execute 继续对话"。
            return None, limit_err.to_error_payload()
        except Exception as sandbox_err:
            # 沙箱不可用时降级为"无 execute"继续构建，而不是让整个 run 失败。
            # 常见诱因：Docker daemon 故障、ssh:// 隧道断开、远端账号异常（如密码过期）。
            # 与 factory.py 中 get_sandbox_backend_factory_if_enabled 的降级语义保持一致。
            from config import get_logger

            get_logger(__name__).warning(
                f"[Sandbox] 初始化失败，本次降级为无 execute: "
                f"user={user_id} session={session_id} err={sandbox_err}"
            )
            skip_sandbox = True
            sandbox_tag = "no-sbx"  # 让缓存 key 区分降级态，避免污染正常沙箱缓存

    # cache_key 延后至此计算：沙箱降级会改写 sandbox_tag
    cache_key = (
        f"deep:{session_id}:{cache_agent_key}:cfg{config_version}:llm{llm_fp}:"
        f"{mcp_hash}:{token_fp}:skills{selection_sig}:{sandbox_tag}"
    )

    if (
        app_config.AGENT_CACHE_ENABLED
        and not sandbox_recreated  # 沙箱重建后必须刷新 Agent（container_id 变了）
        and cache_key in manager._agent_cache
    ):
        cached, cached_fp, cached_mh, cached_aid, cached_sel = manager._agent_cache[cache_key]
        if (
            cached_fp == token_fp
            and cached_mh == mcp_hash
            and cached_aid == cache_agent_key
            and cached_sel == selection_sig
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
    builtin_tools = get_builtin_tools(
        user_id=user_id,
        user_token=user_token,
        session_id=session_id,
        capabilities={"mcp": mcp_list, "skills": selected_skill_ids},
    )
    mcp_tools = builtin_tools + mcp_tools  # 内置工具排前面，LLM 优先看到

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
            selected_skill_ids=selected_skill_ids,
            agent_kind=agent_kind,
            skip_sandbox=skip_sandbox,
            user_token=user_token,
            agent_skills_root=agent_skills_root,
            agent_id_for_sandbox=agent_id_for_sandbox,
            llm=llm,
            config_changes=config_changes,
        )
    except SandboxUserLimitExceeded as limit_err:
        return None, limit_err.to_error_payload()
    except ValueError as ve:
        return None, str(ve)
    if err:
        return None, err

    if app_config.AGENT_CACHE_ENABLED:
        manager._agent_cache[cache_key] = (wrapper, token_fp, mcp_hash, cache_agent_key, selection_sig)

    return wrapper, None
