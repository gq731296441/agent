from langchain.agents.middleware import (
    AgentMiddleware,
    wrap_tool_call
)
from langchain.tools.tool_node import ToolCallRequest
from langchain.messages import ToolMessage
from langgraph.types import Command
from typing import Any, Callable, Awaitable

# 自定义中间件, 用于追踪工具日志中间件
class ToolLoggingMiddleWare(AgentMiddleware):
    def __init__(self, on_status: Callable[[str], Any]=None):
        super().__init__()
        # 确保 callable 属性是可调用的，如果未提供则使用一个空函数
        self.on_status = on_status or (lambda x: None)
        pass
    
    # 异步工具调用中间件
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[Any], Awaitable[ToolMessage | Command]]
    )-> ToolMessage | Command:
        tool_name = request.tool_call['name']
        tool_args = request.tool_call['args']

        print(f">>> 正在调用工具 {tool_name}")
        # self.on_status(f"正在调用工具 {tool_name}")

        # 调用工具前推送
        await self.on_status({
            "type": "tool_call",
            "tool_name": tool_name,
            "args": tool_args
        })

        try:
            # 使用awrap_tool_call必须使用await等待工具执行完毕才能获取工具结果
            result = await handler(request)
            print(f">>>工具执行成功")

            # self.on_status("工具执行成功")
            # 追踪工具的输出内容
            if isinstance(result, ToolMessage):
                # 如果是 ToolMessage，输出其内容
                output_content = result.content
            elif isinstance(result, Command):
                # 如果是 Command，输出其更新内容
                output_content = f"Command: {result.goto} / {result.update}"
            else:
                output_content = str(result)
                
            print(f">>> 工具输出内容: {output_content}")

            # 工具执行结果推送
            await self.on_status({
                "type": "tool_result",
                "tool_name": tool_name,
                "content": output_content
            })
            
            return result
        except Exception as e:
            error_message = f"Tool failed: {e}"
            print(error_message)
            # self.on_status(error_message)
            # 确保抛出异常，以便 Agent 流程能够感知到错误
            raise