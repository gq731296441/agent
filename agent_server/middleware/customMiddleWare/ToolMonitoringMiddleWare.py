import json
import time
from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain.tools.tool_node import ToolCallRequest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.config import get_stream_writer
from langgraph.runtime import Runtime
from langgraph.types import Command
from json_repair import repair_json

from agent_server.llm import create_tool_judge_llm
from agent_server.prompts import INTENT_INFER_PROMPT, TOOL_REFINE_PROMPT
from config import get_logger
from config.config import app_config
from db import storage
from utils.token_counter import token_counter

logger = get_logger(__name__)

SENSITIVE_ARG_KEYS = ("password", "token", "secret", "api_key", "authorization", "credential")

DEFAULT_MAX_OBSERVE_TOKENS = 10000 # 工具结果观察的默认最大 token 数，超过时会触发压缩机制
MAX_TOOL_REFINE_INPUT_TOKENS = 12000 # 工具结果压缩输入的最大 token 数，过长时先截断再压缩
MAX_JUDGE_OUTPUT_TOKENS = 6000 # 工具结果判断时，输入给判断模型的工具输出最大 token 数，过长时先截断。注意：这个值需要根据实际情况调整，过短可能导致判断不准确，过长可能增加成本和延迟。
JUDGE_HISTORY_LIMIT = 10


# 自定义中间件, 用于追踪工具日志中间件
class ToolMonitoringMiddleWare(AgentMiddleware):
    def __init__(self, judge_llm=None):
        super().__init__()
        self.judge_llm = judge_llm or create_tool_judge_llm()
        self._token_counter = token_counter

    @staticmethod
    def _sanitize_tool_args(tool_args: dict) -> dict:
        if not isinstance(tool_args, dict):
            return tool_args
        sanitized = {}
        for key, value in tool_args.items():
            if any(s in str(key).lower() for s in SENSITIVE_ARG_KEYS):
                sanitized[key] = "***"
            else:
                sanitized[key] = value
        return sanitized

    def after_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """模型输出后检查并修复畸形的工具调用参数。

        LLM 流式输出有时会产生格式不完整的 JSON 参数（缺括号、缺引号等），
        LangChain 会将这些调用归入 AIMessage.invalid_tool_calls，
        LangGraph 随后会生成错误 ToolMessage 反馈给 LLM 进行重试，浪费一轮调用。

        此钩子在模型输出后、工具执行前拦截，使用 json_repair 尝试自动修复参数，
        将修复成功的工具调用从 invalid_tool_calls 提升至 tool_calls，避免无谓重试。
        """
        messages = state.get("messages", [])
        if not messages:
            return None

        last_msg = messages[-1]
        if not isinstance(last_msg, AIMessage):
            return None

        invalid_calls = getattr(last_msg, "invalid_tool_calls", None)
        if not invalid_calls:
            return None

        repaired_count = 0
        remaining_invalid = []

        for itc in invalid_calls:
            tool_name = itc.get("name", "unknown")
            tool_id = itc.get("id")
            args_str = itc.get("args")

            # 缺少关键字段的调用无法修复
            if not tool_name or not tool_id or not args_str:
                remaining_invalid.append(itc)
                continue

            try:
                repaired_args = repair_json(args_str, return_objects=True)
                if isinstance(repaired_args, dict):
                    last_msg.tool_calls.append({
                        "name": tool_name,
                        "args": repaired_args,
                        "id": tool_id,
                        "type": "tool_call",
                    })
                    repaired_count += 1
                    logger.info(
                        f"【ToolMiddleWare】🔧 工具 '{tool_name}' 畸形参数已自动修复: "
                        f"keys={list(repaired_args.keys())}"
                    )
                    continue
                else:
                    logger.warning(
                        f"【ToolMiddleWare】工具 '{tool_name}' 参数修复结果非 dict，跳过"
                    )
            except Exception as e:
                logger.warning(
                    f"【ToolMiddleWare】工具 '{tool_name}' 参数修复失败: {e}"
                )

            remaining_invalid.append(itc)

        if repaired_count > 0:
            last_msg.invalid_tool_calls = remaining_invalid
            logger.info(
                f"【ToolMiddleWare】共修复 {repaired_count} 个畸形工具调用"
                + (f"，剩余 {len(remaining_invalid)} 个无法修复" if remaining_invalid else "")
            )

        return None  # 已通过原地修改处理，无需返回状态更新

    # 异步工具调用中间件
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        tool_name = request.tool_call["name"]
        tool_args = request.tool_call["args"]
        tool_call_id = request.tool_call.get("id", "")

        logger.info(
            f"【ToolMiddleWare】准备调用工具 {tool_name}，参数 {self._sanitize_tool_args(tool_args)}"
        )
        duration_ms = 0
        try:
            # 在真正调用工具前，立即推送 tool_call 事件给前端
            writer = self._get_stream_writer_safe()
            if writer:
                writer({
                    "type": "tool_call",
                    "content": {
                        "tool_name": tool_name,
                        "args": tool_args,
                        "tool_call_id": tool_call_id,
                    },
                })

            # 使用 awrap_tool_call 必须使用 await 等待工具执行完毕才能获取工具结果
            start_ts = time.perf_counter()
            result = await handler(request)
            duration_ms = int((time.perf_counter() - start_ts) * 1000)

            output_content = self._stringify_content(result)
            if isinstance(result, ToolMessage):
                result, output_content = await self._compress_tool_result_if_needed(request, result)

            # 2. 调用快模型进行质量判断
            # 注意：request.state 通常包含了当前的 messages 列表
            history = request.state.get("messages", [])

            if len(output_content) < app_config.TOOL_JUDGE_SKIP_MAX_CHARS:
                status = "success"
            else:
                status = await self._judge_tool_result(
                    history=history,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    output=output_content,
                )

            # 3. 异步记录工具调用信息到 measure.tool_call_logs
            await self._record_tool_call_log(
                request=request,
                tool_name=tool_name,
                tool_args=tool_args,
                output_content=output_content,
                status=status,
                duration_ms=duration_ms,
            )

            # 4. 将状态注入结果
            # 在 LangChain 中，ToolMessage 推荐将结构化状态放在 artifact 中
            if isinstance(result, ToolMessage):
                if result.artifact and isinstance(result.artifact, dict):
                    result.artifact["status"] = status
                else:
                    result.artifact = {"status": status}

            logger.info(f"【ToolMiddleWare】工具 {tool_name} 判断结果: {status}")
            return result

        except Exception as e:
            error_message = f"Tool failed: {e}"
            logger.error(f"【ToolMiddleWare】工具 {tool_name} 调用失败，错误信息: {error_message}")
            raise

    async def _compress_tool_result_if_needed(
        self,
        request: ToolCallRequest,
        result: ToolMessage,
    ) -> tuple[ToolMessage, str]:
        """当工具结果过长时，先智能压缩，再将压缩结果返回给主模型和判断模型。"""
        content_text = self._stringify_content(result.content)
        content_tokens = self._count_text_tokens(content_text)
        max_observe = self._get_max_observe_tokens(request)

        if content_tokens <= max_observe:
            return result, content_text

        writer = self._get_stream_writer_safe()
        if writer:
            writer("正在总结工具结果...")

        goal = await self._resolve_tool_goal(request)
        safe_content = self._truncate_by_tokens(content_text, MAX_TOOL_REFINE_INPUT_TOKENS)
        refine_prompt = (
            TOOL_REFINE_PROMPT.format(goal=goal, safe_content=safe_content)
            + f"\n\n[输出要求]\n"
            + "1. 优先保留与当前任务直接相关的事实、字段、状态、报错、链接、ID、时间。\n"
            + "2. 删除重复内容、冗余原文和与目标无关的噪音。\n"
            + f"3. 输出尽量控制在 {max_observe} tokens 以内。"
        )

        try:
            response = await self.judge_llm.ainvoke(
                [
                    SystemMessage(
                        content=(
                            "你是工具结果压缩助手。"
                            "你的目标是在不丢失关键事实、错误信息和执行结论的前提下，"
                            "将超长工具输出提炼成后续推理可直接使用的简洁摘要。"
                        )
                    ),
                    HumanMessage(content=refine_prompt),
                ]
            )
            refined_content = self._stringify_content(response.content).strip()
        except Exception as e:
            logger.warning(f"【ToolMiddleWare】工具结果压缩失败，将回退为截断结果。错误: {e}")
            refined_content = safe_content

        if not refined_content:
            refined_content = safe_content

        wrapped_content = f"**[系统智能摘要]**\n{refined_content}"
        wrapped_tokens = self._count_text_tokens(wrapped_content)
        if wrapped_tokens > max_observe:
            wrapped_content = self._truncate_by_tokens(wrapped_content, max_observe)
            wrapped_tokens = self._count_text_tokens(wrapped_content)

        result.content = wrapped_content
        if result.artifact and isinstance(result.artifact, dict):
            result.artifact["compression"] = {
                "enabled": True,
                "original_tokens": content_tokens,
                "compressed_tokens": wrapped_tokens,
            }
        else:
            result.artifact = {
                "compression": {
                    "enabled": True,
                    "original_tokens": content_tokens,
                    "compressed_tokens": wrapped_tokens,
                }
            }

        logger.info(
            f"【ToolMiddleWare】工具 {request.tool_call.get('name', '未知工具')} 结果已压缩: "
            f"{content_tokens} -> {wrapped_tokens} tokens"
        )
        if writer:
            writer("工具结果已智能压缩，正在继续执行...")

        return result, wrapped_content

    async def _resolve_tool_goal(self, request: ToolCallRequest) -> str:
        """优先复用发起该工具调用的 AIMessage 内容，缺失时再调用模型推断工具意图。"""
        messages = request.state.get("messages", [])
        tool_call = request.tool_call or {}
        tool_call_id = tool_call.get("id")

        for msg in reversed(messages):
            if not isinstance(msg, AIMessage):
                continue

            tool_calls = getattr(msg, "tool_calls", None) or []
            if any(tc.get("id") == tool_call_id for tc in tool_calls if isinstance(tc, dict)):
                goal = self._stringify_content(msg.content).strip()
                if goal:
                    return goal
                break

        infer_prompt = INTENT_INFER_PROMPT.format(
            tool_name=tool_call.get("name", "未知工具"),
            args=json.dumps(tool_call.get("args", {}), ensure_ascii=False, default=str),
        )

        try:
            response = await self.judge_llm.ainvoke([
                *messages[-JUDGE_HISTORY_LIMIT:],
                HumanMessage(content=infer_prompt),
            ])
            goal = self._stringify_content(response.content).strip()
            if goal:
                return goal
        except Exception as e:
            logger.warning(f"【ToolMiddleWare】推断工具意图失败，使用兜底描述。错误: {e}")

        return (
            f"执行工具 {tool_call.get('name', '未知工具')}，"
            f"参数为 {json.dumps(tool_call.get('args', {}), ensure_ascii=False, default=str)}"
        )

    async def _judge_tool_result(self, history: list, tool_name: str, tool_args: dict, output: str) -> str:
        """使用快速模型判断工具执行是否符合预期。"""
        system_prompt = (
            "你是一个工具执行检查员。你的任务是根据对话上下文、工具输入和输出，"
            "判断该工具的执行结果在『业务逻辑』上是否成功。\n"
            "要求：\n"
            "1. 如果输出解决了用户的问题或提供了有效信息，返回 'success'。\n"
            "2. 如果输出是空结果、报错、由于参数错误导致无意义内容，返回 'fail'。\n"
            "只输出 'success' 或 'fail'，不要有任何废话。"
        )

        context_lines = []
        for message in history[-JUDGE_HISTORY_LIMIT:]:
            message_type = getattr(message, "type", message.__class__.__name__)
            message_content = self._stringify_content(getattr(message, "content", ""))
            context_lines.append(f"{message_type}: {message_content[:200]}")

        context_str = "\n".join(context_lines)
        safe_output = self._truncate_by_tokens(output, MAX_JUDGE_OUTPUT_TOKENS)
        user_input = (
            f"--- 历史背景 ---\n{context_str}\n\n"
            f"--- 工具调用 ---\n工具名: {tool_name}\n"
            f"参数: {json.dumps(tool_args, ensure_ascii=False, default=str)}\n"
            f"--- 工具输出 ---\n{safe_output}\n\n"
            f"判断结果 (success/fail):"
        )

        try:
            response = await self.judge_llm.ainvoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_input),
            ])
            res_text = self._stringify_content(response.content).strip().lower()
            return "success" if "success" in res_text else "fail"
        except Exception as e:
            logger.warning(f"【ToolMiddleWare】工具结果判断失败，默认按 success 处理。错误: {e}")
            return "success"

    def _get_max_observe_tokens(self, request: ToolCallRequest) -> int:
        runtime = getattr(request, "runtime", None)
        context = getattr(runtime, "context", None)

        max_observe = None
        if isinstance(context, dict):
            max_observe = context.get("max_observe")
        elif context is not None:
            max_observe = getattr(context, "max_observe", None)

        if isinstance(max_observe, int) and max_observe > 0:
            return max_observe

        return DEFAULT_MAX_OBSERVE_TOKENS

    def _count_text_tokens(self, text: str) -> int:
        return self._token_counter.count_text_tokens(text)

    def _truncate_by_tokens(self, text: str, max_tokens: int) -> str:
        return self._token_counter.truncate_text_by_tokens(text, max_tokens)

    def _stringify_content(self, content: Any) -> str:
        if isinstance(content, ToolMessage):
            return self._stringify_content(content.content)
        if isinstance(content, Command):
            return f"Command: {content.goto} / {content.update}"
        return self._token_counter.stringify_content(content)

    async def _record_tool_call_log(
        self,
        request: ToolCallRequest,
        tool_name: str,
        tool_args: dict,
        output_content: str,
        status: str,
        duration_ms: int = None,
    ) -> None:
        """将工具调用信息异步写入 measure.tool_call_logs 表。"""
        try:
            session_id, user_id, run_id, conversation_id = self._extract_metadata(request)
            tool_call_id = request.tool_call.get("id", "")

            await storage.insert_tool_call_log(
                session_id=session_id,
                user_id=user_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                tool_args=self._sanitize_tool_args(tool_args),
                tool_result=output_content,
                status=status,
                run_id=run_id,
                conversation_id=conversation_id,
                duration_ms=duration_ms,
            )
        except Exception as e:
            # 记录失败不影响主流程
            logger.warning(f"【ToolMiddleWare】记录工具调用日志失败: {e}")

    @staticmethod
    def _extract_metadata(request: ToolCallRequest) -> tuple[str, str, str, str]:
        """从 request.runtime.config.metadata 中提取 session_id / user_id / run_id / conversation_id。"""
        session_id = ""
        user_id = ""
        run_id = ""
        conversation_id = ""
        runtime = getattr(request, "runtime", None)
        if runtime is not None:
            config = getattr(runtime, "config", None) or {}
            metadata = config.get("metadata", {})
            session_id = metadata.get("session_id", "")
            user_id = metadata.get("user_id", "")
            run_id = metadata.get("run_id", "")
            conversation_id = metadata.get("conversation_id", "")
        return session_id, user_id, run_id, conversation_id

    def _get_stream_writer_safe(self):
        try:
            return get_stream_writer()
        except RuntimeError:
            return None