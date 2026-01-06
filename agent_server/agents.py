from langchain.agents import create_agent
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from langchain.messages import HumanMessage

from agent_server.llm import get_llm
from agent_server.prompts import SYSTEM_PROMPT
from agent_server.middleware.customMiddleWare.loggingMiddelWare import LoggingMiddleWare
from agent_server.middleware.customMiddleWare.toolLoggingMiddleWare import ToolLoggingMiddleWare
from agent_server.middleware.human_in_the_loop import create_hitl_middleware
from agent_server.tools.mcp.mcp_manager import McpToolManager

from agent_server.tools.base.delete_mrm_doc import delete_mrm_doc

import uuid
import os
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
os.environ.pop("all_proxy", None)


class LangGraphAgent:
    def __init__(self, checkpointer=None):
        """
        初始化 Agent
        
        Args:
            checkpointer: LangGraph checkpointer 实例（可选）
                         如果不提供，则使用内存保存器
        """
        self.is_initialized = False
        self.mcp_manager = McpToolManager()
        self.pending_interrupts = {} # 保存中断请求，供 resume 使用
        self.config = None
        self.checkpointer = checkpointer

    async def initialize(self):
        if not self.is_initialized:
            # 默认初始化：读取 LLM、工具列表（包含 MCP 工具和内置工具）
            llm = get_llm()
            base_tools = [delete_mrm_doc]
            mcp_tools = await self.mcp_manager.get_mcp_toollist()

            # 如果有外部配置指定工具，则以配置为准（支持 mcp/all），否则使用默认全部工具
            if self.config and isinstance(self.config.get("tools"), list):
                # 如果配置包含字符串 'mcp'，则追加 mcp_tools
                configured = []
                for t in self.config.get("tools"):
                    if t == "mcp":
                        configured += mcp_tools
                    # 未来可支持按名称选择具体工具
                all_tools = configured + base_tools
            else:
                all_tools = mcp_tools + base_tools

            # 使用提供的 checkpointer，或默认使用内存保存器
            checkpointer_to_use = self.checkpointer or MemorySaver()

            self.agent = create_agent(
                model=llm,
                tools=all_tools,
                system_prompt=SYSTEM_PROMPT,
                checkpointer=checkpointer_to_use,
                middleware=[create_hitl_middleware()],
            )

            self.is_initialized = True
            print(f">>>【Agent】Agent构建完成")

    async def initialize_from_config(self, config: dict):
        """用持久化配置初始化 agent（设置 config 与上下文消息），然后调用 initialize 完成构建。"""
        self.config = config
        await self.initialize()


    async def run(self, input_data: dict, user_id: str, session_id: str, on_status=None):
        if not self.is_initialized:
            await self.initialize()

        # 使用组合 thread_id 来区分用户和会话
        thread_id = f"{user_id}#{session_id}"
        config = {"configurable": {"thread_id": thread_id}}

        # 检查输入是否包含 Command
        command = input_data.get("command")

        if command and isinstance(command, Command):
            # 如果是 resume 命令，直接传递 Command
            stream_input = command
        else:
            # 用户消息
            stream_input = {"messages": [HumanMessage(content=input_data.get("content", ""))]}

        current_llm_text = ""

        async for mode, chunk in self.agent.astream(
            stream_input,
            config=config,
            stream_mode=["updates", "messages", "custom"],  # 同时获取 token 和自定义消息
        ):

            # messages 模式：主要来自 LLM 节点，包含 token chunk 和 tool_call chunk
            if mode == "messages":
                try:
                    # message_chunk 是一个 AIMessageChunk, metadata 是一个 dict
                    message_chunk, metadata = chunk
                except ValueError as e:
                    print(f"messages解包错误: {e}. Chunk content: {chunk}")
                    continue
                
                node = metadata.get("langgraph_node", "unknown")

                # --- 处理LLM节点 ---
                if node == "model":
                    if message_chunk.content:
                        text_chunk = message_chunk.content
                        current_llm_text += text_chunk
                        # 实时推送 LLM 正在生成的文本
                        await on_status({
                            "type": "assistant_message",
                            "content": text_chunk,  # 只发增量
                            "is_delta": True
                        })

                    # 准备调用工具节点
                    if message_chunk.tool_calls:
                        for tool_call in message_chunk.tool_calls:
                            # 完整 tool_call（非 chunk），通常在 LLM 确定调用时一次性给出
                            print(f"准备调用工具{tool_call.get('name')}")
                            await on_status({
                                "type": "tool_call",
                                "tool_name": tool_call.get("name"),
                                "args": tool_call.get("args", {}),
                                "tool_id": tool_call.get("id")
                            })
                    if message_chunk.tool_call_chunks:
                        # 如果需要实时显示工具参数的逐字生成，可以在这里处理
                        # 一般可以忽略，或仅在需要“显示正在解析参数”时使用
                        # args = ''
                        # if  message_chunk.tool_call_chunks[0]["args"]:
                        #     args += message_chunk.tool_call_chunks[0]["args"]
                        # print(args)
                        pass
                
                # --- 处理工具节点 ---
                elif node == "tools":
                    if message_chunk.type == "tool":
                        # message_chunk 就是一个 ToolMessage/ToolMessageChunk
                        print(">>>> 工具执行完毕")
                        tool_name = message_chunk.name
                        await on_status({
                            "type": "tool_result",
                            "tool_name": tool_name,
                            "content": message_chunk.content,
                            "tool_call_id": message_chunk.tool_call_id
                        })
                        # 重置当前 LLM 文本，因为工具执行完后会重新进入 LLM 生成最终回答
                        current_llm_text = ""
                    elif isinstance(message_chunk, list):
                        print(f">>> message_chunk结构:{message_chunk}")

                        for msg in message_chunk:
                            if msg.type == "tool":
                                print(f">>> {msg.name}工具执行完毕")
                                await on_status({
                                    "type": "tool_result",
                                    "tool_name": msg.name,
                                    "content": msg.content,
                                    "tool_call_id": msg.tool_call_id
                                })
                                current_llm_text = ""

            # 自定义模式，处理工具中的 get_stream_writer() 发送的消息
            elif mode == "custom":
                # custom 模式：来自工具内部的 get_stream_writer() 发送的消息
                # chunk 是字符串或任意数据（文档中是 str）
                await on_status({
                    "type": "status",  # 或者自定义 "tool_progress"
                    "content": str(chunk)
                })
            
            # 状态更新流，用于处理中断和工具执行结果
            elif mode == "updates":
                # 检查中断
                if "__interrupt__" in chunk:
                    interrupt_info = chunk["__interrupt__"]
                    
                    # interrupt_info 是 Interrupt对象列表，如果有多个中断，需要严格按照中断顺序返回comman给agent
                    # 收集本次中断的所有工具请求（可能多个）
                    all_actions = []
                    for interrupt in interrupt_info:
                        if hasattr(interrupt, "value"):
                            interrupt_value = interrupt.value
                            interrupt_id = getattr(interrupt, "id", None)
                        else:
                            continue
                        
                        action_requests = interrupt_value.get("action_requests", [])
                        review_configs = interrupt_value.get("review_configs", [])

                        for req in action_requests:
                            # 提取中断工具信息：工具名称、参数、描述、允许决策
                            tool_name = req["name"]
                            args = req.get("args", {})
                            description = req.get("description", None)
                            allowed_decisions = []

                            for cfg in review_configs:
                                if cfg.get("action_name") == tool_name:
                                    allowed_decisions = cfg.get("allowed_decisions", [])
                                    break

                            all_actions.append({
                                "tool_name": tool_name,
                                "args": args,
                                "description": description,
                                "allowed_decisions": allowed_decisions,
                            })
                        
                    if not all_actions:
                        continue

                    # 生成唯一 request_id 供前端识别
                    request_id = str(uuid.uuid4())

                    # 保存必要信息，用于后续 resume
                    self.pending_interrupts[request_id] = {
                        "config": config.copy(),  # 用于 astream(command, config=...)
                        "actions": all_actions,         # 所有待审批工具
                        "num_pending": len(all_actions), # 用于校验
                    }

                    # 统一推送一个 interrupt 消息，包含所有工具
                    if on_status:
                        await on_status({
                            "type": "interrupt",
                            "request_id": request_id,
                            "actions": all_actions,  # 数组，前端循环渲染
                            "total_count": len(all_actions),
                        })

                        await on_status({
                            "type": "status",
                            "content": f"等待用户审批 {len(all_actions)} 个工具调用..."
                        })
                    
                    # 遇到中断后，不再处理后续状态
                    break 
        
        # 结束后发送 end 信号
        if on_status:
            await on_status({"type": "end", "content": "completed"})