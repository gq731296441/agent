from langchain.agents import create_agent
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.types import Command
from langgraph.graph import END
from langchain.messages import HumanMessage
from typing import AsyncGenerator, Dict, Any

from agent_server.middleware.customMiddleWare.NodeHooksMiddleWare import NodeHooksMiddleWare
from agent_server.middleware.customMiddleWare.ToolMonitoringMiddleWare import ToolMonitoringMiddleWare
from agent_server.middleware.human_in_the_loop import create_hitl_middleware
from db import storage
from config import setup_logging, get_logger
from utils.uage_tracker import create_usage_tracker

from mcp_server.tool.delete_mrm_doc import delete_mrm_doc

# 公共模块导入
from common.enums import MessageType, SenderType, EndReason
from common.schemas import AgentMessage

import uuid
import os
import asyncio
import json
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
os.environ.pop("all_proxy", None)

logger = get_logger(__name__)

class LangGraphAgent:
    """LangGraph Agent 封装类，负责 Agent 的初始化和运行逻辑。"""
    
    def __init__(
        self,  
        model: str | BaseChatModel=None,
        system_prompt: str = None,
        tools: list = None,
        checkpointer=None,
    ):
        """
        Agent 初始化（同步部分）。
        
        注意：由于 Agent 的完整初始化需要异步操作，请使用 create() 类方法创建实例。
        
        Args:
            checkpointer: LangGraph checkpointer 实例（可选），不提供则使用内存保存器
            tools: MCP 工具列表（可选）
        """
        self.agent = None

        # ==================== 模型初始化 ====================
        self.model = model
        
        # ==================== 工具初始化 ====================
        if tools is None:
            # 默认使用基础工具集
            # self.tools = BASIC_TOOLS
            self.tools = [delete_mrm_doc]
            logger.info(f"【Agent】使用基础工具集 ({len(self.tools)} 个工具)")
        else:
            self.tools = tools or []
            logger.info(f"【Agent】使用自定义工具集 ({len(self.tools)} 个工具)")
        
        # 打印工具列表
        if self.tools:
            tool_names = [tool.name for tool in self.tools]
            logger.debug(f"【Agent】工具列表: {', '.join(tool_names)}")
        
        # ==================== 提示词初始化 ====================
        self.system_prompt = system_prompt
        
        # ==================== Checkpointer 初始化 ====================
        self.checkpointer = checkpointer
        
        try:
            self.agent = create_agent(
                model=self.model,
                tools=self.tools,
                system_prompt=self.system_prompt,
                checkpointer=self.checkpointer,
                middleware=[
                    # create_hitl_middleware(),
                    NodeHooksMiddleWare(),
                    ToolMonitoringMiddleWare()
                ],
            )
            logger.info(f"【Agent】✅ Agent构建完成")
        except Exception as e:
            logger.error(f"【Agent】❌ Agent构建失败: {e}")
            raise e

    def _parse_accumulated_tool_calls(self, accumulated_tool_calls: dict) -> list:
        """解析累积的工具调用参数，返回工具调用数据列表"""
        result = []
        for idx, tc in accumulated_tool_calls.items():
            tool_name = tc.get("name")
            args_str = tc.get("args", "{}")
            tool_id = tc.get("id")
            
            # 解析参数字符串为字典
            args_dict = {}
            if args_str:
                try:
                    args_dict = json.loads(args_str)
                except Exception as e:
                    logger.error(f"【Agent】Tool Args Error 解析失败: {e}, Content: {args_str}")
                    # 如果解析失败，尝试保留原始字符串
                    args_dict = {"raw_args": args_str}

            result.append({
                "tool_name": tool_name,
                "args": args_dict,
                "tool_call_id": tool_id
            })
        return result

    async def run(
        self, 
        input_data: dict, 
        config: dict, 
        session_id: str,
        stop_event: asyncio.Event = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        运行 Agent 的异步生成器方法。
        
        Args:
            input_data: 输入数据，包含 content 或 command
            config: LangGraph 配置，包含 configurable.thread_id
            session_id: 会话 ID
            stop_event: 停止事件，用于中断执行
            
        Yields:
            包含 type, sender, session_id, content, save 字段的消息字典
        """
        
        # 检查输入是否包含 Command
        command = input_data.get("command")

        if command and isinstance(command, Command):
            # 如果是 resume 命令，直接传递 Command
            stream_input = command
        else:
            # 用户消息
            content_str = input_data.get("content", "")
            stream_input = {"messages": [HumanMessage(content=content_str)]}
            
            # yield 用户消息（需要存储）
            if content_str:
                yield AgentMessage(
                    type=MessageType.CHAT,
                    sender=SenderType.USER,
                    session_id=session_id,
                    content={"text": content_str},
                    save=True
                ).to_dict()

        current_llm_text = ""  # 累积的 LLM 回应文本
        current_reasoning_text = "" # 累积的推理内容
        accumulated_tool_calls = {}  # 用于累积流式输出的工具调用参数
        last_node = None

        # 创建 usage tracker, 跟踪token使用情况
        usage_tracker = create_usage_tracker()
        
        # 处理并 yield 累积的 LLM 文本、推理内容和工具调用
        async def yield_accumulated_content():
            nonlocal current_llm_text, current_reasoning_text, accumulated_tool_calls
            # 处理累积的推理内容
            if current_reasoning_text:
                yield AgentMessage(
                    type=MessageType.REASONING_FINAL,
                    sender=SenderType.ASSISTANT,
                    session_id=session_id,
                    content={"text": current_reasoning_text},
                    save=True
                ).to_dict()
                current_reasoning_text = ""
            
            # 处理累积的 LLM 文本
            if current_llm_text:
                yield AgentMessage(
                    type=MessageType.LLM_TEXT_FINAL,
                    sender=SenderType.ASSISTANT,
                    session_id=session_id,
                    content={"text": current_llm_text},
                    save=True
                ).to_dict()
                current_llm_text = ""
            
            # 处理累积的工具调用
            if accumulated_tool_calls:
                tool_calls = self._parse_accumulated_tool_calls(accumulated_tool_calls)
                for tool_call_data in tool_calls:
                    yield AgentMessage(
                        type=MessageType.TOOL_CALL,
                        sender=SenderType.SYSTEM,
                        session_id=session_id,
                        content=tool_call_data,
                        save=True
                    ).to_dict()
                accumulated_tool_calls = {}

        try:
            async for mode, chunk in self.agent.astream(
                stream_input,
                config=config,
                stream_mode=["updates", "messages", "custom"],
            ):
                # 软检查：检查 Event
                if stop_event and stop_event.is_set():
                    raise asyncio.CancelledError("User requested stop via Event")

                # messages 模式：主要来自 LLM 节点，包含 token chunk 和 tool_call chunk
                if mode == "messages":
                    try:
                        message_chunk, metadata = chunk
                    except ValueError as e:
                        logger.error(f"【Agent】messages解包错误: {e}. Chunk content: {chunk}")
                        continue
                    
                    # 更新 token 使用
                    if hasattr(message_chunk, "usage_metadata") and message_chunk.usage_metadata:
                        usage_tracker.update_from_metadata(message_chunk.usage_metadata)
                    
                    node = metadata.get("langgraph_node", "unknown")

                    # 节点切换检测：离开 model 节点时，先 yield 累积内容
                    if last_node == "model" and node != "model":
                        async for msg in yield_accumulated_content():
                            yield msg
                    
                    last_node = node

                    # --- 处理 LLM 节点 ---
                    if node == "model":
                        # 提取并处理推理内容
                        reasoning_chunk = message_chunk.additional_kwargs.get("reasoning_content")
                        if reasoning_chunk:
                            current_reasoning_text += reasoning_chunk
                            # 实时推送推理流式输出（不存库，只推送）
                            # print(f"【Agent】实时推送推理流式输出: {reasoning_chunk}")
                            yield AgentMessage(
                                type=MessageType.REASONING,
                                sender=SenderType.ASSISTANT,
                                session_id=session_id,
                                content=reasoning_chunk,
                                save=False,
                            ).to_dict()

                        if message_chunk.content:
                            text_chunk = message_chunk.content
                            current_llm_text += text_chunk
                            # 实时推送 LLM 流式输出的文本（不存库，只推送）
                            yield AgentMessage(
                                type=MessageType.CHAT,
                                sender=SenderType.ASSISTANT,
                                session_id=session_id,
                                content=text_chunk,
                                save=False
                            ).to_dict()

                        # 累积流式工具调用参数
                        if message_chunk.tool_call_chunks:
                            for tc_chunk in message_chunk.tool_call_chunks:
                                index = tc_chunk.get("index")
                                if index not in accumulated_tool_calls:
                                    accumulated_tool_calls[index] = {
                                        "name": tc_chunk.get("name"),
                                        "args": tc_chunk.get("args") or "",
                                        "id": tc_chunk.get("id")
                                    }
                                else:
                                    # 合并
                                    if tc_chunk.get("name"):
                                        accumulated_tool_calls[index]["name"] = tc_chunk.get("name")
                                    if tc_chunk.get("id"):
                                        accumulated_tool_calls[index]["id"] = tc_chunk.get("id")
                                    if tc_chunk.get("args"):
                                        accumulated_tool_calls[index]["args"] += tc_chunk.get("args")
                    
                    # --- 处理工具执行节点, 该节点只输出工具执行结果 ---
                    elif node == "tools":
                        if message_chunk.type == "tool":
                            logger.info(f"【Agent】{message_chunk.name}工具执行完毕")
                            # print(message_chunk)
                            yield AgentMessage(
                                type=MessageType.TOOL_RESULT,
                                sender=SenderType.SYSTEM,
                                session_id=session_id,
                                content={
                                    "tool_name": message_chunk.name,
                                    "tool_result": message_chunk.content,
                                    "tool_call_id": message_chunk.tool_call_id,
                                    "artifact": message_chunk.artifact
                                },
                                save=True
                            ).to_dict()

                        elif isinstance(message_chunk, list):
                            # 执行多个工具，message_chunk 是一个 ToolMessage/ToolMessageChunk 列表
                            # logger.info(f"【Agent】message_chunk结构:{message_chunk}")
                            for msg in message_chunk:
                                if msg.type == "tool":
                                    logger.info(f"【Agent】 {msg.name}工具执行完毕")
                                    yield AgentMessage(
                                        type=MessageType.TOOL_RESULT,
                                        sender=SenderType.SYSTEM,
                                        session_id=session_id,
                                        content={
                                            "tool_name": msg.name,
                                            "tool_result": msg.content,
                                            "tool_call_id": msg.tool_call_id
                                        },
                                        save=True
                                    ).to_dict()

                # 自定义模式，处理工具中的 get_stream_writer() 发送的消息
                elif mode == "custom":
                    yield AgentMessage(
                        type=MessageType.STATUS,
                        sender=SenderType.SYSTEM,
                        session_id=session_id,
                        content=str(chunk),
                        save=True
                    ).to_dict()
                
                # 状态更新流，用于处理中断和工具执行结果
                elif mode == "updates":
                    if "__interrupt__" in chunk:
                        interrupt_info = chunk["__interrupt__"]
                        
                        # interrupt_info 是 Interrupt对象列表，如果有多个中断，需要严格按照中断顺序返回comman给agent
                        # 收集本次中断的所有工具请求（可能多个）
                        all_actions = []
                        for interrupt in interrupt_info:
                            if hasattr(interrupt, "value"):
                                interrupt_value = interrupt.value
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

                        # yield interrupt 消息
                        yield AgentMessage(
                            type=MessageType.INTERRUPT,
                            sender=SenderType.SYSTEM,
                            session_id=session_id,
                            content={
                                "request_id": request_id,
                                "actions": all_actions,
                                "total_count": len(all_actions),
                            },
                            save=True
                        ).to_dict()

                        yield AgentMessage(
                            type=MessageType.STATUS,
                            sender=SenderType.SYSTEM,
                            session_id=session_id,
                            content=f"等待用户审批 {len(all_actions)} 个工具调用...",
                            save=True
                        ).to_dict()
                        
                        # 遇到中断后，不再处理后续状态
                        break 
        
            # Agent正常结束任务：处理剩余累积内容
            async for msg in yield_accumulated_content():
                yield msg

            # 发送最终的 context 信息
            context_info = usage_tracker.get_usage_info()
            logger.info("【Agent】Usage Token Info: " + str(context_info))
            usage_tracker.log_summary()

            # yield 结束信号
            yield AgentMessage(
                type=MessageType.END,
                sender=SenderType.SYSTEM,
                session_id=session_id,
                content=EndReason.COMPLETED,
                save=True
            ).to_dict()
        
        except asyncio.CancelledError:
            logger.info(f" 【Agent】会话 {session_id} 收到取消请求，正在中止...")

            # 中止 Agent 任务执行
            await self._force_end_run(config)
            
            # 取消时，处理剩余累积内容
            async for msg in yield_accumulated_content():
                yield msg

            # yield 停止提示
            yield AgentMessage(
                type=MessageType.CHAT,
                sender=SenderType.ASSISTANT,
                session_id=session_id,
                content="\n\n任务已停止",
                save=False
            ).to_dict()

            # 发送最终的 context 信息
            context_info = usage_tracker.get_usage_info()
            logger.info("【Agent】Usage Token Info: " + str(context_info))
            usage_tracker.log_summary()
            
            yield AgentMessage(
                type=MessageType.END,
                sender=SenderType.SYSTEM,
                session_id=session_id,
                content=EndReason.STOP,
                save=True
            ).to_dict()
        
        except Exception as e:
            logger.error(f" 【Agent】会话 {session_id} 出现错误: {e}")
            
            # 出错时，处理剩余累积内容
            async for msg in yield_accumulated_content():
                yield msg

            yield AgentMessage(
                type=MessageType.ERROR,
                sender=SenderType.SYSTEM,
                session_id=session_id,
                content=f'\n\n抱歉，处理您的请求时出现错误: {str(e)}',
                save=True
            ).to_dict()

            # 发送最终的 context 信息
            context_info = usage_tracker.get_usage_info()
            logger.info("【Agent】Usage Token Info: " + str(context_info))
            usage_tracker.log_summary()
            
            yield AgentMessage(
                type=MessageType.END,
                sender=SenderType.SYSTEM,
                session_id=session_id,
                content=EndReason.STOP,
                save=True
            ).to_dict()
    
    async def _force_end_run(self, config: dict):
        """强制结束当前运行的 Agent，会话结束时调用。"""
        try:
            await self.agent.aupdate_state(config, values=None, as_node="model")
            logger.info("【Agent】 已通过 update_state 强制结束运行")
        except Exception as e:
            logger.error(f"【Agent】 清理状态失败: {e}")

def create_base_agent(
    model: str | BaseChatModel=None,
    system_prompt: str = None,
    tools: list = None,
    checkpointer=None,
) -> LangGraphAgent:
    """
    创建 LangGraphAgent 实例的工厂函数。
    
    Args:
        model: 语言模型实例或标识符（可选）
        system_prompt: 系统提示词（可选）
        tools: MCP 工具列表（可选）
        checkpointer: LangGraph checkpointer 实例（可选）
    
    Returns:
        LangGraphAgent 实例
    """
    
    return LangGraphAgent(
        model=model,
        system_prompt=system_prompt,
        tools=tools,
        checkpointer=checkpointer,
    )