from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    hook_config
)
from langchain_core.messages import SystemMessage, RemoveMessage, AIMessage
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langgraph.runtime import Runtime
from typing import Any, Callable, Awaitable
import asyncio
import uuid
from config import get_logger
from utils.message_cleaner import strip_reasoning_from_message

logger = get_logger(__name__)

# 自定义中间件类，用于在Agent和模型之间添加日志记录功能
class NodeHooksMiddleWare(AgentMiddleware):
    def __init__(self, system_prompt: str = None):
        super().__init__()
        self.system_prompt = system_prompt
        self.temp_sys_tag = "temp_sys_prompt_marker"

    
    def before_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        logger.info("【AgentMiddleWare】before_agent：Agent任务开始")
        return None

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        response = await self._modify_request(request, handler)

        # 在 model node 返回 state update 之前，清理 AIMessage 中的思考内容
        # 必须在这里做（而非 after_model），因为 after_model 是独立的 graph node，
        # model node 的 checkpoint 已在 after_model 执行前写入
        for msg in response.result:
            if isinstance(msg, AIMessage):
                strip_reasoning_from_message(msg)

        return response

    def _modify_request(self, request: ModelRequest, handler):
        if not self.system_prompt:
            return handler(request)
            
        existing_blocks = list(request.system_message.content_blocks) if request.system_message else []
        new_block = {"type": "text", "text": self.system_prompt}
        new_system = SystemMessage(content=existing_blocks + [new_block])
        
        return handler(request.override(system_message=new_system))

    def after_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        logger.info("【AgentMiddleWare】after_agent：Agent任务完成")
        return None