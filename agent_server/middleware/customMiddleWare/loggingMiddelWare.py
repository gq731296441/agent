from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
)

from langgraph.runtime import Runtime
from typing import Any, Callable
import asyncio

# 自定义中间件类，用于在Agent和模型之间添加日志记录功能
class LoggingMiddleWare(AgentMiddleware):
    def __init__(self, on_status: Callable[[str], Any]=None):
        super().__init__()
        self.on_status = on_status or (lambda x: None)

    async def before_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        if self.on_status:
            # 异步回调需要使用 await
            if asyncio.iscoroutinefunction(self.on_status):
                await self.on_status({"type": "status", "content": "Agent开始任务"})
            else:
                self.on_status({"type": "status", "content": "Agent开始任务"})
        return None

    def before_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        print(f">>> 模型输入内容 {len(state['messages'])} messages")
        # self.on_status(f"模型输入内容 {len(state['messages'])} messages")
        return None

    def after_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        print(f">>> 模型输出内容 {state['messages'][-1].content}")
        # self.on_status(f"模型输出内容 {state['messages'][-1].content}")
        return None

    def after_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        print(f">>> Agent任务完成， {len(state['messages'])} messages")
        if self.on_status:
            self.on_status({"type": "status", "content": "Agent任务完成"})
            self.on_status({"type": "end", "content": "[DONE]"})
        return None



