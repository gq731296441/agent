import json
from typing import Callable, Union
from langchain.agents.middleware import (
    AgentMiddleware,
    wrap_tool_call
)
from langchain.tools.tool_node import ToolCallRequest
from langchain_core.messages import ToolMessage, BaseMessage, SystemMessage, HumanMessage
from langgraph.types import Command
from agent_server.llm import create_tool_judge_llm

from config import get_logger

logger = get_logger(__name__)

# 自定义中间件, 用于追踪工具日志中间件
class ToolMonitoringMiddleWare(AgentMiddleware):
    def __init__(self):
        super().__init__()
        self.judege_llm = create_tool_judge_llm()
    
    # 异步工具调用中间件
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    )-> ToolMessage | Command:
        tool_name = request.tool_call['name']
        tool_args = request.tool_call['args']

        logger.info(f"【ToolMiddleWare】准备调用工具 {tool_name}")
        try:
            # 使用awrap_tool_call必须使用await等待工具执行完毕才能获取工具结果
            result = await handler(request)

            # 追踪工具的输出内容
            if isinstance(result, ToolMessage):
                # 如果是 ToolMessage，输出其内容
                output_content = result.content
            elif isinstance(result, Command):
                # 如果是 Command，输出其更新内容
                output_content = f"Command: {result.goto} / {result.update}"
            else:
                output_content = str(result)
                
            # 2. 调用快模型进行质量判断
            # 注意：request.state 通常包含了当前的 messages 列表
            history = request.state.get("messages", [])
            
            status = await self._judge_tool_result(
                history=history,
                tool_name=tool_name,
                tool_args=tool_args,
                output=output_content
            )

            # 3. 将状态注入结果
            # 在 LangChain 中，ToolMessage 推荐将结构化状态放在 artifact 中
            if isinstance(result, ToolMessage):
                # 如果原结果已有 artifact 且是字典，则合并；否则新建
                if result.artifact and isinstance(result.artifact, dict):
                    result.artifact["status"] = status
                else:
                    result.artifact = {"status": status}
                
            logger.info(f"【ToolMiddleWare】工具 {tool_name} 判断结果: {status}")
            # print(f"工具调用结果:{result}")

            # 4. 返回结果：
            # {
            #     content: [{
            #       'type': 'text',
            #       'text': '8921单据已触发处理，当前状态处理中！',
            #       'id': 'lc_194222de-7504-402c-ac46-9b9adbc9d678'
            #     }]
            #     name: '单据处理'
            #     tool_call_id: 'chatcmpl-tool-514309afc2d745889249d6cd6dbe91ac'
            #     artifact: {
            #         'structured_content': {
            #             'result': '8921单据已触发处理，当前状态处理中！'
            #         },
            #         'status': 'success'
            #     }
            # }
            return result

        except Exception as e:
            error_message = f"Tool failed: {e}"
            logger.error(f"【ToolMiddleWare】工具 {tool_name} 调用失败，错误信息: {error_message}")
            raise
    
    async def _judge_tool_result(self, history: list, tool_name: str, tool_args: dict, output: str) -> str:
        """使用快速模型判断工具执行是否符合预期"""
        
        system_prompt = (
            "你是一个工具执行检查员。你的任务是根据对话上下文、工具输入和输出，"
            "判断该工具的执行结果在『业务逻辑』上是否成功。\n"
            "要求：\n"
            "1. 如果输出解决了用户的问题或提供了有效信息，返回 'success'。\n"
            "2. 如果输出是空结果、报错、由于参数错误导致无意义内容，返回 'fail'。\n"
            "只输出 'success' 或 'fail'，不要有任何废话。"
        )

        # 构造简单的上下文摘要，避免 Token 浪费
        context_str = "\n".join([f"{m.type}: {m.content[:200]}" for m in history[-3:]]) # 取最后3轮

        user_input = (
            f"--- 历史背景 ---\n{context_str}\n\n"
            f"--- 工具调用 ---\n工具名: {tool_name}\n参数: {json.dumps(tool_args)}\n"
            f"--- 工具输出 ---\n{output}\n\n"
            f"判断结果 (success/fail):"
        )

        try:
            response = await self.judge_llm.ainvoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_input)
            ])
            # 清洗结果，确保只拿到关键词
            res_text = response.content.strip().lower()
            return "success" if "success" in res_text else "fail"
        except Exception:
            # 如果判断模型挂了，默认返回 success 以免阻断流程
            return "success"