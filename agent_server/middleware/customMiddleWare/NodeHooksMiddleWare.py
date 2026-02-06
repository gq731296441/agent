from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    hook_config
)

from langgraph.runtime import Runtime
from typing import Any, Callable
import asyncio
from config import get_logger

logger = get_logger(__name__)

# 自定义中间件类，用于在Agent和模型之间添加日志记录功能
class NodeHooksMiddleWare(AgentMiddleware):
    def __init__(self):
        super().__init__()

    
    def before_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        logger.info("【AgentMiddleWare】before_agent：Agent任务开始")
        return None

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        logger.info(f"【AgentMiddleWare】before_model：模型输入内容 {len(state['messages'])} messages")
        return None

    def after_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        logger.info(f"【AgentMiddleWare】after_model：模型输出内容 {state['messages'][-1].content[:50]}……")
        return None

    def after_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        logger.info("【AgentMiddleWare】after_agent：Agent任务完成")
        return None