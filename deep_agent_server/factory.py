"""Deep Agent 工厂。

负责组装 deepagents.create_deep_agent 所需的 model、backend、permissions、
interrupt_on 等组件，并返回 DeepAgentWrapper 供 runner 消费。
"""

from typing import Any, List, Optional, Tuple

from deepagents import create_deep_agent

from agent_server.llm import create_agent_llm
from config import get_logger
from config.config import app_config
from deep_agent_server.backend_factory import (
    build_backend_factory,
    build_interrupt_on,
    build_permissions,
)
from deep_agent_server.sandbox import get_sandbox_backend_factory_if_enabled
from deep_agent_server.sandbox.exceptions import SandboxUserLimitExceeded
from deep_agent_server.skills.selector import build_skills_system_prompt
from agent_server.prompts import build_base_system_prompt, build_system_prompt

logger = get_logger(__name__)

BUILTIN_TOOLS = {
    "write_todos", "read_file", "write_file", "edit_file",
    "ls", "glob", "grep", "execute", "task",
}


class DeepAgentWrapper:
    """封装编译后的 Deep Agent 图及关联 store / 已触发 skills 元数据。"""

    def __init__(self, agent, store=None, loaded_skills=None):
        self.agent = agent
        self.store = store
        # trigger 匹配的 skills（用于 run 开始时推送 SKILL_LOADED 事件）
        self.loaded_skills = loaded_skills or []


async def create_deep_agent_instance(
    user_id: str,
    session_id: str,
    user_agent: dict,
    mcp_tools: list,
    checkpointer=None,
    store=None,
    exposed_skill_metas: Optional[list] = None,
    loaded_skill_metas: Optional[list] = None,
    selected_skill_ids: Optional[List[str]] = None,
    *,
    agent_kind: str = "default",
    skip_sandbox: bool = False,
    user_token: Optional[str] = None,
    matched_skill_dirs: Optional[List[str]] = None,  # deprecated, ignored
    llm=None,
) -> Tuple[Optional[DeepAgentWrapper], Optional[str]]:
    """
    创建 Deep Agent 实例。

    - exposed_skill_metas：写入 system prompt 的 skill 列表（渐进式披露）
    - loaded_skill_metas：用户消息 trigger 匹配到的 skills（仅用于事件通知）
    - skip_sandbox=True：warmup 阶段跳过 Docker 拉起，execute 暂不可用
    """
    try:
        model = llm or create_agent_llm()
        # 订阅智能体：基础提示词（不含 Role 身份） + 自定义提示词
        # 默认智能体：完整提示词（含 Role 智搭身份）
        if agent_kind == "subscription":
            base_prompt = build_base_system_prompt(user_id=user_id, user_token=user_token)
            custom_prompt = user_agent.get("system_prompt", "").strip()
            system_prompt = base_prompt + "\n\n# 智能体设定\n" + custom_prompt if custom_prompt else base_prompt
        else:
            system_prompt = build_system_prompt(user_id=user_id, user_token=user_token)
        config = user_agent.get("config") or {}
        # 渐进式披露：system prompt 只含 name/description，正文按需 read_file
        metas = exposed_skill_metas or []
        sandbox_mode = False
        backend = build_backend_factory(user_id, session_id, store=store)
        if not skip_sandbox:
            sandbox_backend_factory = await get_sandbox_backend_factory_if_enabled(
                user_id, session_id=session_id, store=store, user_token=user_token
            )
            if sandbox_backend_factory is not None:
                backend = sandbox_backend_factory(None)
                sandbox_mode = True
        if metas:
            system_prompt += build_skills_system_prompt(metas, sandbox_mode=sandbox_mode)
        allowed_skill_ids = None
        if selected_skill_ids is not None:
            allowed_skill_ids = [
                str(s).strip() for s in selected_skill_ids if str(s).strip()
            ]

        subagents = config.get("subagents") or None
        interrupt_on = build_interrupt_on(config) if app_config.AGENT_RUNTIME == "deep" else None

        permissions = build_permissions(allowed_skill_ids)

        agent = create_deep_agent(
            model=model,
            tools=mcp_tools or [],
            system_prompt=system_prompt,
            skills=None,
            backend=backend,
            permissions=permissions,
            interrupt_on=interrupt_on,
            subagents=subagents,
            checkpointer=checkpointer,
            store=store,
        )
        trigger_metas = loaded_skill_metas or []
        logger.info(
            f"[DeepFactory] Agent 创建成功 session={session_id} "
            f"exposed_skills={len(metas)} trigger_matched={len(trigger_metas)}"
        )
        return DeepAgentWrapper(agent, store=store, loaded_skills=trigger_metas), None
    except SandboxUserLimitExceeded:
        raise
    except Exception as e:
        logger.error(f"[DeepFactory] 创建失败: {e}")
        import traceback
        traceback.print_exc()
        return None, f"创建 Deep Agent 失败: {e}"
