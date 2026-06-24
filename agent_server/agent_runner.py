"""
Agent 运行逻辑模块。

将 LangGraphAgent 的运行过程（流式消费、错误恢复、消息组装）
从类定义中分离，作为独立的异步生成器函数。

调用方式：
    from agent_server.agent_runner import run_agent

    async for msg in run_agent(agent, input_data, config, session_id, ...):
        ...
"""

from langgraph.types import Command
from langgraph.errors import GraphInterrupt
from langchain.messages import HumanMessage
from langchain_core.messages import AIMessage, ToolMessage
from typing import AsyncGenerator, Dict, Any

from utils.error_recovery import (
    classify_error, RecoveryAction, ErrorClassification
)
from config import get_logger
from utils.uage_tracker import create_usage_tracker

from common.enums import MessageType, SenderType, EndReason
from common.schemas import AgentMessage
from utils.extractors import (
    ThinkTagParser,
    ToolCallAccumulator,
    extract_reasoning_from_chunk,
    extract_tool_result
)
from utils.hitl_store import save_interrupt
from utils.run_events import insert_agent_run_event

import uuid
import asyncio
import traceback

logger = get_logger(__name__)


# ==================== 公共 API ====================

async def run_agent(
    agent,
    input_data: dict,
    config: dict,
    session_id: str,
    stop_event: asyncio.Event = None,
    conversation_id: str = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    运行 Agent 的异步生成器函数。

    将 LangGraphAgent 实例的运行过程（流消费、推理/回答分离、
    工具调用累积、错误恢复重试）完整封装在此函数中。

    Args:
        agent: LangGraphAgent 实例（需包含 .agent 编译图 和 .tools 工具列表）
        input_data: 输入数据，包含 content 或 command
        config: LangGraph 配置，包含 configurable.thread_id
        session_id: 会话 ID
        stop_event: 停止事件，用于中断执行
        conversation_id: 会话轮次ID，同一轮提问的所有消息共享

    Yields:
        包含 type, sender, session_id, content, save 字段的消息字典
    """

    # ==================== 错误恢复配置 ====================
    MAX_ERROR_RETRIES = 2     # 可恢复错误最大重试次数
    _error_retry_count = 0    # 当前重试计数

    # ==================== 分组与排序追踪 ====================
    _group_id = 0      # 分组ID：同一处理阶段（模型推理/工具执行）的消息共享
    _step_index = 0    # 步骤序号：本轮会话中的全局递增序号

    metadata = config.get("metadata", {}) or {}
    run_id = metadata.get("run_id")
    user_id_meta = metadata.get("user_id", "")

    async def _emit_run_event(
        event_type: str,
        category: str = None,
        action: str = None,
        message: str = None,
    ) -> None:
        if not run_id:
            return
        await insert_agent_run_event(
            run_id=run_id,
            session_id=session_id,
            user_id=user_id_meta,
            conversation_id=conversation_id,
            event_type=event_type,
            category=category,
            action=action,
            message=message,
        )

    def _enrich(msg_dict: dict, track_step: bool = True) -> dict:
        """为消息字典注入 conversation_id / group_id / step_index

        Args:
            msg_dict: 消息字典
            track_step: 是否递增 step_index。流式输出块设为 False，
                        避免 step_index 因大量流式 chunk 膨胀到几百。
        """
        nonlocal _step_index
        msg_dict["conversation_id"] = conversation_id
        msg_dict["group_id"] = _group_id
        msg_dict["step_index"] = _step_index
        if run_id:
            msg_dict["run_id"] = run_id
        if track_step:
            _step_index += 1
        return msg_dict

    # 检查输入是否包含 Command
    command = input_data.get("command")

    if command and isinstance(command, Command):
        # 如果是 resume 命令，直接传递 Command
        stream_input = command
    else:
        # 用户消息
        content_str = input_data.get("content", "")
        stream_input = {"messages": [HumanMessage(content=content_str)]}

        # yield 用户消息（需要存储）— group 0 表示用户输入
        if content_str:
            yield _enrich(AgentMessage(
                type=MessageType.CHAT,
                sender=SenderType.USER,
                session_id=session_id,
                content={"text": content_str},
                save=True
            ).to_dict())

    current_llm_text = ""  # 累积的 LLM 回应文本
    current_reasoning_text = ""  # 累积的推理内容
    last_node = None
    _reasoning_notified = False  # 是否已通知前端"正在推理"

    # ==================== 提取器初始化 ====================
    think_parser = ThinkTagParser()
    valid_tool_names = {tool.name for tool in agent.tools} if agent.tools else set()
    tool_accumulator = ToolCallAccumulator(valid_tool_names=valid_tool_names)
    emitted_tool_call_ids: set[str] = set()

    # 创建 usage tracker, 跟踪token使用情况
    usage_tracker = create_usage_tracker()

    # 处理并 yield 累积的 LLM 文本、推理内容和工具调用
    async def yield_accumulated_content():
        nonlocal current_llm_text, current_reasoning_text
        # 刷新 <think> 缓冲区中残留的内容
        flush_reasoning, flush_answer = think_parser.flush()
        if flush_reasoning:
            current_reasoning_text += flush_reasoning
        if flush_answer:
            current_llm_text += flush_answer
        # 处理累积的推理内容
        if current_reasoning_text:
            yield _enrich(AgentMessage(
                type=MessageType.REASONING_FINAL,
                sender=SenderType.ASSISTANT,
                session_id=session_id,
                content={"text": current_reasoning_text},
                save=True
            ).to_dict())
            current_reasoning_text = ""

        # 处理累积的 LLM 文本
        if current_llm_text:
            yield _enrich(AgentMessage(
                type=MessageType.LLM_TEXT_FINAL,
                sender=SenderType.ASSISTANT,
                session_id=session_id,
                content={"text": current_llm_text},
                save=True
            ).to_dict())
            current_llm_text = ""

        # 处理累积的工具调用
        if tool_accumulator.has_pending:
            # tool_call 的前端推送与存库已迁移到 ToolMonitoringMiddleWare.awrap_tool_call
            # 这里仅清空累积缓冲，避免尾部重复推送
            tool_accumulator.parse_and_clear()

    async def _consume_stream(stream, *, handle_interrupts=True):
        """统一的流处理内部函数，消费 astream 的三种模式并 yield AgentMessage。

        Args:
            stream: agent.agent.astream(...) 返回的异步迭代器
            handle_interrupts: 是否处理 updates 模式中的中断（重试时设为 False）
        """
        nonlocal last_node, _group_id, _reasoning_notified, current_llm_text, current_reasoning_text

        async for mode, chunk in stream:
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
                    logger.debug("【Agent】更新 token 使用信息: " + str(message_chunk.usage_metadata))
                    usage_tracker.update_from_metadata(message_chunk.usage_metadata)

                node = metadata.get("langgraph_node", "unknown")

                # 节点切换检测：节点变化时 yield 累积内容并递增 group_id
                if last_node is not None and last_node != node:
                    if last_node == "model":
                        # 离开 model 节点，先 yield 当前 group 的累积内容
                        async for msg in yield_accumulated_content():
                            yield msg
                    # 进入新节点 → 新的分组
                    _group_id += 1

                # 首次进入节点时初始化分组
                if last_node is None:
                    _group_id += 1

                # (重新)进入 model 节点时，重置推理通知标志和 <think> 解析状态
                if node == "model" and last_node != "model":
                    _reasoning_notified = False
                    think_parser.reset()

                last_node = node

                # --- 处理 LLM 节点 ---
                if node == "model":
                    # ===== 方式1：additional_kwargs 中的 reasoning_content =====
                    reasoning_chunk = extract_reasoning_from_chunk(message_chunk)
                    if reasoning_chunk:
                        # 使用 additional_kwargs 方式时，禁用 <think> 标签解析
                        think_parser.disable()
                        if not _reasoning_notified:
                            _reasoning_notified = True
                            yield _enrich(AgentMessage(
                                type=MessageType.REASONING,
                                sender=SenderType.ASSISTANT,
                                session_id=session_id,
                                content="正在推理中...",
                                save=False,
                            ).to_dict(), track_step=False)
                        current_reasoning_text += reasoning_chunk

                    # ===== 处理 content（可能含 <think> 标签，方式2） =====
                    if message_chunk.content:
                        thinking_part, answer_part = think_parser.process_chunk(message_chunk.content)

                        # 处理思考内容（来自 <think> 标签）
                        if thinking_part:
                            if not _reasoning_notified:
                                _reasoning_notified = True
                                yield _enrich(AgentMessage(
                                    type=MessageType.REASONING,
                                    sender=SenderType.ASSISTANT,
                                    session_id=session_id,
                                    content="正在推理中...",
                                    save=False,
                                ).to_dict(), track_step=False)
                            current_reasoning_text += thinking_part

                        # 处理正式回答内容
                        if answer_part:
                            # 如果有累积的推理内容，在流式回答前先推送完整推理
                            if current_reasoning_text:
                                yield _enrich(AgentMessage(
                                    type=MessageType.REASONING_FINAL,
                                    sender=SenderType.ASSISTANT,
                                    session_id=session_id,
                                    content={"text": current_reasoning_text},
                                    save=True
                                ).to_dict())
                                current_reasoning_text = ""

                            current_llm_text += answer_part
                            # 实时推送 LLM 流式输出的文本（不存库，只推送）
                            # track_step=False: 流式 chunk 不递增 step_index
                            yield _enrich(AgentMessage(
                                type=MessageType.CHAT,
                                sender=SenderType.ASSISTANT,
                                session_id=session_id,
                                content=answer_part,
                                save=False
                            ).to_dict(), track_step=False)

                    # 检测并记录 invalid_tool_calls（LLM 返回了格式错误的工具调用）
                    ToolCallAccumulator.check_invalid_tool_calls(message_chunk)

                    # 累积流式工具调用参数
                    # 注意：中间件（如 LLMToolSelectorMiddleware）可能产生 AIMessage 而非
                    # AIMessageChunk，前者没有 tool_call_chunks 属性，需要 hasattr 保护
                    if hasattr(message_chunk, "tool_call_chunks") and message_chunk.tool_call_chunks:
                        tool_accumulator.accumulate_chunks(message_chunk.tool_call_chunks)

                # --- 处理工具执行节点, 该节点只输出工具执行结果 ---
                elif node == "tools":
                    tool_result_data = extract_tool_result(message_chunk)
                    if tool_result_data:
                        logger.info(f"【Agent】{tool_result_data['tool_name']}工具执行完毕")
                        yield _enrich(AgentMessage(
                            type=MessageType.TOOL_RESULT,
                            sender=SenderType.SYSTEM,
                            session_id=session_id,
                            content=tool_result_data,
                            save=True
                        ).to_dict())

                    elif isinstance(message_chunk, list):
                        for msg in message_chunk:
                            msg_result = extract_tool_result(msg)
                            if msg_result:
                                logger.info(f"【Agent】{msg_result['tool_name']}工具执行完毕")
                                yield _enrich(AgentMessage(
                                    type=MessageType.TOOL_RESULT,
                                    sender=SenderType.SYSTEM,
                                    session_id=session_id,
                                    content=msg_result,
                                    save=True
                                ).to_dict())

            # 自定义模式，处理工具中的 get_stream_writer() 发送的消息
            elif mode == "custom":
                # 支持中间件提前推送 tool_call 事件（调用工具前）
                if isinstance(chunk, dict) and chunk.get("type") == "tool_call":
                    tool_call_data = chunk.get("content") if isinstance(chunk.get("content"), dict) else {}
                    if tool_call_data:
                        tool_call_id = tool_call_data.get("tool_call_id")
                        if tool_call_id and tool_call_id in emitted_tool_call_ids:
                            continue
                        if tool_call_id:
                            emitted_tool_call_ids.add(tool_call_id)

                        yield _enrich(AgentMessage(
                            type=MessageType.TOOL_CALL,
                            sender=SenderType.SYSTEM,
                            session_id=session_id,
                            content=tool_call_data,
                            save=True
                        ).to_dict())
                        continue

                yield _enrich(AgentMessage(
                    type=MessageType.STATUS,
                    sender=SenderType.SYSTEM,
                    session_id=session_id,
                    content=str(chunk),
                    save=True
                ).to_dict())

            # 状态更新流，用于处理中断和工具执行结果
            elif mode == "updates":
                if "__interrupt__" not in chunk:
                    continue
                if not handle_interrupts:
                    continue

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

                thread_id = config.get("configurable", {}).get("thread_id", "")
                user_id_meta = config.get("metadata", {}).get("user_id", "")
                store = getattr(agent, "store", None)
                await save_interrupt(
                    store,
                    request_id=request_id,
                    thread_id=thread_id,
                    session_id=session_id,
                    user_id=user_id_meta,
                    actions=all_actions,
                    num_pending=len(all_actions),
                )
                await _emit_run_event("interrupted", message=f"request_id={request_id}")

                # yield interrupt 消息
                yield _enrich(AgentMessage(
                    type=MessageType.INTERRUPT,
                    sender=SenderType.SYSTEM,
                    session_id=session_id,
                    content={
                        "request_id": request_id,
                        "actions": all_actions,
                        "total_count": len(all_actions),
                    },
                    save=True
                ).to_dict())

                yield _enrich(AgentMessage(
                    type=MessageType.STATUS,
                    sender=SenderType.SYSTEM,
                    session_id=session_id,
                    content=f"等待用户审批 {len(all_actions)} 个工具调用...",
                    save=True
                ).to_dict())

                # 遇到中断后，不再处理后续状态
                break

    # ==================== 主流程 ====================
    compiled_graph = agent.agent  # 编译后的 LangGraph 实例

    try:
        stream = compiled_graph.astream(
            stream_input,
            config=config,
            stream_mode=["updates", "messages", "custom"],
        )
        async for msg in _consume_stream(stream):
            yield msg

        # Agent正常结束任务：处理剩余累积内容
        async for msg in yield_accumulated_content():
            yield msg

        # yield 结束信号
        yield _enrich(AgentMessage(
            type=MessageType.END,
            sender=SenderType.SYSTEM,
            session_id=session_id,
            content=EndReason.COMPLETED,
            save=True
        ).to_dict())

    except asyncio.CancelledError:
        logger.info(f" 【Agent】会话 {session_id} 收到取消请求，正在中止...")
        await _emit_run_event("stopped")

        # 中止 Agent 任务执行
        await _force_end_run(compiled_graph, config)

        # 取消时，处理剩余累积内容
        async for msg in yield_accumulated_content():
            yield msg

        # yield 停止提示
        yield _enrich(AgentMessage(
            type=MessageType.CHAT,
            sender=SenderType.ASSISTANT,
            session_id=session_id,
            content="\n\n任务已停止",
            save=False
        ).to_dict(), track_step=False)

        yield _enrich(AgentMessage(
            type=MessageType.END,
            sender=SenderType.SYSTEM,
            session_id=session_id,
            content=EndReason.STOP,
            save=True
        ).to_dict())

    except Exception as e:
        full_error = traceback.format_exc()
        logger.error(f" 【Agent】会话 {session_id} 出现错误: {e}")
        logger.debug(f"详细堆栈:\n{full_error}")

        # 出错时，处理剩余累积内容
        async for msg in yield_accumulated_content():
            yield msg

        # ==================== 错误分类 ====================
        classification = classify_error(e)
        logger.info(
            f"【Agent】错误分类: {classification.category.value}, "
            f"恢复策略: {classification.action.value}, "
            f"可重试: {classification.retryable}"
        )
        await _emit_run_event(
            "error_classified",
            category=classification.category.value,
            action=classification.action.value,
            message=str(e)[:500],
        )

        # ==================== 注入 AIMessage 修复消息序列 ====================
        # 无论什么错误，都需要向 checkpoint 注入一条 AIMessage，
        # 确保消息序列保持 user→assistant 交替，防止后续请求报错
        recovery_ai_content = _build_recovery_ai_content(classification, e)
        await _inject_recovery_message(compiled_graph, config, recovery_ai_content)
        logger.info(f"【Agent】已注入 AIMessage 到 checkpoint 修复消息序列")

        # ==================== 根据恢复策略执行 ====================
        if classification.retryable and _error_retry_count < MAX_ERROR_RETRIES:
            _error_retry_count += 1
            logger.info(
                f"【Agent】可恢复错误，准备重试 "
                f"({_error_retry_count}/{MAX_ERROR_RETRIES})"
            )
            await _emit_run_event(
                "retry",
                category=classification.category.value,
                action=classification.action.value,
                message=f"attempt={_error_retry_count}",
            )

            # 通知前端正在重试
            if classification.user_message:
                yield _enrich(AgentMessage(
                    type=MessageType.STATUS,
                    sender=SenderType.SYSTEM,
                    session_id=session_id,
                    content=classification.user_message,
                    save=False
                ).to_dict(), track_step=False)

            # 对于 FEEDBACK_AND_CONTINUE：用 model_hint 构造新输入让模型自我修正
            # 对于 RETRY / DEGRADE：直接用原始输入重试
            if classification.action == RecoveryAction.FEEDBACK_AND_CONTINUE:
                retry_input = {
                    "messages": [HumanMessage(content=classification.model_hint)]
                }
            else:
                # RETRY 和 DEGRADE 直接重新触发 astream（checkpoint 已有上下文）
                retry_input = {"messages": [HumanMessage(content="请继续完成之前的任务。")]}

            # 递归重试（通过 astream 继续执行 Agent）
            try:
                stream = compiled_graph.astream(
                    retry_input,
                    config=config,
                    stream_mode=["updates", "messages", "custom"],
                )
                async for msg in _consume_stream(stream, handle_interrupts=False):
                    yield msg

                # 重试成功：处理剩余累积内容
                async for msg in yield_accumulated_content():
                    yield msg

                # 重试成功后正常结束
                yield _enrich(AgentMessage(
                    type=MessageType.END,
                    sender=SenderType.SYSTEM,
                    session_id=session_id,
                    content=EndReason.COMPLETED,
                    save=True
                ).to_dict())
                return  # 重试成功，直接返回

            except asyncio.CancelledError:
                raise  # 取消请求向上传播
            except Exception as retry_err:
                logger.error(
                    f"【Agent】重试也失败: {retry_err}，"
                    f"将终止并返回错误给用户"
                )
                # 重试失败，也要注入 AIMessage
                retry_classification = classify_error(retry_err)
                retry_recovery_content = _build_recovery_ai_content(
                    retry_classification, retry_err
                )
                await _inject_recovery_message(compiled_graph, config, retry_recovery_content)

                # 重试失败后 fall through 到下面的终止逻辑
                error_content = (
                    f"\n\n抱歉，系统已自动重试但仍然失败。\n"
                    f"错误信息: {str(retry_err)[:200]}\n\n"
                    f"请稍后重试，或尝试简化您的请求。"
                )
                yield _enrich(AgentMessage(
                    type=MessageType.ERROR,
                    sender=SenderType.SYSTEM,
                    session_id=session_id,
                    content=error_content,
                    save=True
                ).to_dict())
                yield _enrich(AgentMessage(
                    type=MessageType.END,
                    sender=SenderType.SYSTEM,
                    session_id=session_id,
                    content=EndReason.ERROR,
                    save=True
                ).to_dict())
                return

        # ==================== 不可重试 / 重试次数耗尽 → 终止 ====================
        error_content = classification.user_message
        if not error_content:
            error_content = f'\n\n抱歉，处理您的请求时出现错误: {str(e)[:200]}'

        yield _enrich(AgentMessage(
            type=MessageType.ERROR,
            sender=SenderType.SYSTEM,
            session_id=session_id,
            content=error_content,
            save=True
        ).to_dict())

        yield _enrich(AgentMessage(
            type=MessageType.END,
            sender=SenderType.SYSTEM,
            session_id=session_id,
            content=EndReason.ERROR,
            save=True
        ).to_dict())


async def run_agent_ainvoke(
    agent,
    input_data: dict,
    config: dict,
    session_id: str,
    stop_event: asyncio.Event = None,
    conversation_id: str = None,
) -> Dict[str, Any]:
    """
    非流式版 run_agent：使用 compiled_graph.ainvoke() 执行 Agent 并返回结构化结果。

    与 run_agent() (astream) 的核心区别：
    - 不逐 token 推送，一次性返回最终结果
    - 中间件的 get_stream_writer() 输出被静默丢弃
    - HITL 中断抛出 GraphInterrupt，本函数捕获并返回 "interrupted" 状态
    - 推理内容（reasoning）在 ainvoke 下不可用（after_model 已剥离）

    Returns:
        {messages, final_answer, reasoning, tool_calls, tool_results, status, error, interrupt}
    """
    compiled_graph = agent.agent
    messages: list = []
    final_answer: str = ""
    tool_calls: list = []
    tool_results: list = []
    status: str = "completed"
    error: str = ""
    interrupt: dict = {}

    step_index = 0

    def _enrich(msg_dict: dict, track_step: bool = True) -> dict:
        nonlocal step_index
        msg_dict["conversation_id"] = conversation_id
        msg_dict["group_id"] = 1
        msg_dict["step_index"] = step_index
        if track_step:
            step_index += 1
        return msg_dict

    # ==================== 构建输入 ====================
    command = input_data.get("command")
    content_str = ""
    user_msg_marker = None  # 用于在 final state 中定位本轮的起点

    if command and isinstance(command, Command):
        stream_input = command
    else:
        content_str = input_data.get("content", "")
        stream_input = {"messages": [HumanMessage(content=content_str)]}
        user_msg_marker = content_str

    # ==================== 执行 ainvoke ====================
    try:
        result = await _ainvoke_with_stop(compiled_graph, stream_input, config, stop_event)
        state_messages = result.get("messages", [])

        # 定位本轮新消息的起点
        # 对于 ask：找到我们输入 HumanMessage 的位置，只处理其后的消息
        # 对于 resume_hitl (Command)：所有消息均为 checkpoint 历史，处理末尾新产生的
        start_idx = 0
        if user_msg_marker is not None:
            for i in range(len(state_messages) - 1, -1, -1):
                msg = state_messages[i]
                if isinstance(msg, HumanMessage) and msg.content == user_msg_marker:
                    start_idx = i + 1
                    break

        # ==================== 处理本轮新消息 ====================
        for i in range(start_idx, len(state_messages)):
            msg = state_messages[i]

            if isinstance(msg, HumanMessage):
                # 仅处理本轮用户消息（i == start_idx 且是 text 输入时）
                if i == start_idx and user_msg_marker is not None:
                    messages.append(_enrich(AgentMessage(
                        type=MessageType.CHAT,
                        sender=SenderType.USER,
                        session_id=session_id,
                        content={"text": content_str},
                        save=True,
                    ).to_dict()))

            elif isinstance(msg, AIMessage):
                ai_content = msg.content if isinstance(msg.content, str) else ""

                # 提取 tool_calls
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        tc_dict = {
                            "tool_name": tc.get("name", ""),
                            "tool_call_id": tc.get("id", ""),
                            "args": tc.get("args", {}),
                        }
                        tool_calls.append(tc_dict)
                        messages.append(_enrich(AgentMessage(
                            type=MessageType.TOOL_CALL,
                            sender=SenderType.SYSTEM,
                            session_id=session_id,
                            content=tc_dict,
                            save=True,
                        ).to_dict()))

                if ai_content:
                    final_answer = ai_content
                    messages.append(_enrich(AgentMessage(
                        type=MessageType.LLM_TEXT_FINAL,
                        sender=SenderType.ASSISTANT,
                        session_id=session_id,
                        content={"text": ai_content},
                        save=True,
                    ).to_dict()))

            elif isinstance(msg, ToolMessage):
                tr_dict = {
                    "tool_name": getattr(msg, "name", ""),
                    "tool_call_id": getattr(msg, "tool_call_id", ""),
                    "result": str(msg.content) if msg.content else "",
                }
                tool_results.append(tr_dict)
                messages.append(_enrich(AgentMessage(
                    type=MessageType.TOOL_RESULT,
                    sender=SenderType.SYSTEM,
                    session_id=session_id,
                    content=tr_dict,
                    save=True,
                ).to_dict()))

        # 正常结束
        messages.append(_enrich(AgentMessage(
            type=MessageType.END,
            sender=SenderType.SYSTEM,
            session_id=session_id,
            content=EndReason.COMPLETED,
            save=True,
        ).to_dict()))

    except GraphInterrupt:
        status = "interrupted"
        interrupt = {"message": "Agent 执行被中断，请使用 stream 模式查看详情并继续"}
        messages.append(_enrich(AgentMessage(
            type=MessageType.INTERRUPT,
            sender=SenderType.SYSTEM,
            session_id=session_id,
            content=interrupt,
            save=True,
        ).to_dict()))

    except asyncio.CancelledError:
        status = "stopped"
        messages.append(_enrich(AgentMessage(
            type=MessageType.END,
            sender=SenderType.SYSTEM,
            session_id=session_id,
            content=EndReason.STOP,
            save=True,
        ).to_dict()))

    except Exception as e:
        status = "error"
        error = str(e)
        logger.error(f"【Agent】run_agent_ainvoke 异常: {e}")
        messages.append(_enrich(AgentMessage(
            type=MessageType.ERROR,
            sender=SenderType.SYSTEM,
            session_id=session_id,
            content=error,
            save=True,
        ).to_dict()))

    return {
        "messages": messages,
        "final_answer": final_answer,
        "reasoning": "",
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "status": status,
        "error": error,
        "interrupt": interrupt,
    }


async def _ainvoke_with_stop(
    compiled_graph, stream_input, config: dict, stop_event: asyncio.Event = None
):
    """包装 ainvoke，通过 asyncio.Task + 轮询 stop_event 支持外部取消。"""
    task = asyncio.ensure_future(
        compiled_graph.ainvoke(stream_input, config=config)
    )
    while not task.done():
        if stop_event and stop_event.is_set():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            raise asyncio.CancelledError("stopped by user")
        await asyncio.sleep(0.1)
    return await task


# ==================== 辅助函数 ====================

async def _force_end_run(compiled_graph, config: dict):
    """强制结束当前运行的 Agent，会话结束时调用。"""
    try:
        await compiled_graph.aupdate_state(config, values=None, as_node="model")
        logger.info("【Agent】 已通过 update_state 强制结束运行")
    except Exception as e:
        logger.error(f"【Agent】 清理状态失败: {e}")


async def _inject_recovery_message(compiled_graph, config: dict, content: str) -> None:
    """向 checkpoint 注入一条 AIMessage，修复消息序列的 user→assistant 交替结构。

    当 Agent 运行出错时，LangGraph 的 messages 中没有对应的 assistant 回复，
    导致后续用户发送消息时出现 user→user 的连续结构，部分 LLM API 会拒绝。

    通过 aupdate_state 注入 AIMessage（as_node="model"），让 LangGraph 认为
    model 节点已正常输出，恢复消息交替结构。
    """
    try:
        await compiled_graph.aupdate_state(
            config,
            values={"messages": [AIMessage(content=content)]},
            as_node="model",
        )
        logger.debug("【Agent】✅ 已注入恢复 AIMessage 到 checkpoint")
    except Exception as e:
        logger.error(f"【Agent】❌ 注入恢复消息失败: {e}")


def _build_recovery_ai_content(
    classification: ErrorClassification, exc: Exception
) -> str:
    """构建注入到 checkpoint 的 AIMessage 内容。

    这条消息会被模型在后续对话中看到（作为 assistant 角色的历史消息），
    目的是让模型知道上一次发生了什么错误，以便自我修正。

    Args:
        classification: 错误分类结果
        exc: 原始异常

    Returns:
        AIMessage 的 content 字符串
    """
    category = classification.category.value
    brief_error = str(exc)[:300]

    # 对于可重试且有 model_hint 的错误，使用 hint 内容
    if classification.model_hint:
        return classification.model_hint

    # 通用错误回复模板
    return (
        f"[系统消息] 上一次处理因 {category} 错误而中断: {brief_error}\n"
        f"如果用户继续提问，请基于已有上下文继续帮助用户。"
    )
