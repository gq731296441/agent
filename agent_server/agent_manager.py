import asyncio
import uuid
from typing import Dict, Any, Optional, Tuple, AsyncGenerator
from agent_server.agents import LangGraphAgent, create_base_agent
from agent_server.agent_runner import run_agent, run_agent_ainvoke
from agent_server.llm import create_agent_llm_for_user, create_user_aux_llms
from agent_server.prompts import build_system_prompt
from mcp_server.mcp_manager import McpToolService
from db import storage, deep_storage
from config import get_logger
from config.config import app_config
from utils.run_events import insert_agent_run_event
from deep_agent_server.manager_ext import create_deep_agent_for_session
from deep_agent_server.runner import run_deep_agent, run_deep_agent_collect
from deep_agent_server.turn.context import load_turn, mark_resuming, resolve_resume_step_index, start_turn

import hashlib
import json

# 公共模块导入
from common.enums import MessageType, SenderType, EndReason
from common.schemas import AgentMessage

logger = get_logger(__name__)

SESSION_RUN_WAIT_TIMEOUT = 5.0


class AgentManager:
    """
    AgentManager 负责管理 Agent 的创建和会话交互。

    主要功能：
    - 根据 session_id 和 mcp_list 创建 Agent 实例
    - 将 agent 配置持久化到数据库
    - 管理会话的停止事件与 per-session 互斥
    """

    def __init__(self, checkpointer=None, store=None, summary_llm=None, tool_judge_llm=None):
        self.checkpointer = checkpointer
        self.store = store
        self.summary_llm = summary_llm
        self.tool_judge_llm = tool_judge_llm

        self._active_tasks: Dict[str, asyncio.Task] = {}
        self._stop_events: Dict[str, asyncio.Event] = {}
        self._session_locks: Dict[str, asyncio.Lock] = {}
        self._agent_cache: Dict[str, Tuple[Any, str, str, str]] = {}

    @property
    def is_deep_runtime(self) -> bool:
        return (app_config.AGENT_RUNTIME or "deep").lower() != "light"

    @staticmethod
    def _mcp_list_hash(mcp_list: list) -> str:
        ids = sorted(str(item.get("id")) for item in (mcp_list or []) if item.get("id"))
        return hashlib.md5(json.dumps(ids, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def _token_fingerprint(user_token: str) -> str:
        return hashlib.sha256((user_token or "").encode()).hexdigest()[:16]

    def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        if session_id not in self._session_locks:
            self._session_locks[session_id] = asyncio.Lock()
        return self._session_locks[session_id]

    async def _prepare_session_run(self, session_id: str) -> None:
        """获取 session 锁并等待/停止上一轮任务。"""
        lock = self._get_session_lock(session_id)
        await lock.acquire()

        prev_task = self._active_tasks.get(session_id)
        current = asyncio.current_task()
        if prev_task and prev_task is not current and not prev_task.done():
            self.stop_task(session_id)
            try:
                await asyncio.wait_for(prev_task, timeout=SESSION_RUN_WAIT_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning(
                    f"[AgentManager] 等待 session {session_id} 旧任务结束超时，继续执行"
                )
            except asyncio.CancelledError:
                pass

    def _register_session_run(self, session_id: str, stop_event: asyncio.Event) -> asyncio.Task:
        self._stop_events[session_id] = stop_event
        current_task = asyncio.current_task()
        if current_task is not None:
            self._active_tasks[session_id] = current_task
        return current_task

    def _cleanup_session_run(
        self,
        session_id: str,
        stop_event: asyncio.Event,
        task: Optional[asyncio.Task],
    ) -> None:
        if self._stop_events.get(session_id) is stop_event:
            self._stop_events.pop(session_id, None)
        if task is not None and self._active_tasks.get(session_id) is task:
            self._active_tasks.pop(session_id, None)

        lock = self._session_locks.get(session_id)
        if lock is not None and lock.locked():
            lock.release()

    async def create_agent(
        self,
        user_id: str,
        user_token: str,
        session_id: str,
        mcp_list: list = None,
        agent_id: str = None,
        user_message: str = None,
        always_on_only: bool = False,
        skip_sandbox: bool = False,
        selected_skill_ids: list = None,
    ) -> Tuple[Optional[Any], Optional[str]]:
        if self.is_deep_runtime:
            return await create_deep_agent_for_session(
                self,
                user_id,
                user_token,
                session_id,
                mcp_list,
                agent_id,
                user_message=user_message,
                always_on_only=always_on_only,
                skip_sandbox=skip_sandbox,
                selected_skill_ids=selected_skill_ids,
            )

        if mcp_list is None:
            mcp_list = []

        mcp_hash = self._mcp_list_hash(mcp_list)
        token_fp = self._token_fingerprint(user_token)
        cache_key = f"{session_id}:{mcp_hash}:{token_fp}"

        if app_config.AGENT_CACHE_ENABLED and cache_key in self._agent_cache:
            cached_agent, cached_token_fp, cached_mcp_hash = self._agent_cache[cache_key]
            if cached_token_fp == token_fp and cached_mcp_hash == mcp_hash:
                logger.info(f"[AgentManager] 命中 Agent 缓存 session_id={session_id}")
                return cached_agent, None

        meta = await storage.get_session_meta(session_id) if session_id else None
        agent_kind = (meta or {}).get("agent_kind") or "default"
        try:
            model = await create_agent_llm_for_user(user_id, agent_kind)
            aux = await create_user_aux_llms(user_id)
        except ValueError as ve:
            return None, str(ve)
        system_prompt = build_system_prompt(user_id, user_token)
        mcp_service = McpToolService()

        try:
            db_config = {
                "selected_mcp_ids": [item.get("id") for item in mcp_list if item.get("id")]
            }
            await storage.save_agent_config(session_id, 'nebulacoder-v8', db_config)
            logger.info(
                f"[AgentManager] 已保存 Agent 配置到数据库, session_id: {session_id}, "
                f"mcp工具数: {len(db_config['selected_mcp_ids'])}"
            )

            mcp_tools = []
            if mcp_list:
                try:
                    mcp_tools = await mcp_service.get_mcp_toollist(mcp_list)
                    logger.info(f"[AgentManager] 成功加载 {len(mcp_tools)} 个 MCP 工具")
                except Exception as e:
                    logger.error(f"[AgentManager] 加载 MCP 工具失败: {e}")

            logger.info(f"[AgentManager] 正在创建新 Agent 实例, session_id: {session_id}")

            agent = create_base_agent(
                model=model,
                system_prompt=system_prompt,
                tools=mcp_tools,
                checkpointer=self.checkpointer,
                store=self.store,
                summary_llm=aux["summary"],
                tool_judge_llm=aux["tool_judge"],
            )

            if app_config.AGENT_CACHE_ENABLED:
                self._agent_cache[cache_key] = (agent, token_fp, mcp_hash)

            return agent, None

        except Exception as e:
            import traceback
            traceback.print_exc()
            logger.error(f"[AgentManager] 创建 Agent 失败: {e}")
            return None, f"创建 Agent 失败: {str(e)}"

    def _create_agent_config(
        self,
        user_id: str,
        user_token: str,
        session_id: str,
        run_id: str = None,
        conversation_id: str = None,
        agent_id: str = None,
    ) -> dict:
        thread_id = f"{user_id}#{session_id}"

        configurable = {
            "thread_id": thread_id,
        }

        metadata = {
            "user_id": user_id,
            "session_id": session_id,
        }
        if run_id:
            metadata["run_id"] = run_id
        if conversation_id:
            metadata["conversation_id"] = conversation_id
        if agent_id:
            metadata["agent_id"] = agent_id

        return {
            "configurable": configurable,
            "metadata": metadata,
        }

    async def _save_message_to_db(self, message: Dict[str, Any], user_id: str):
        session_id = message.get("session_id")
        sender = message.get("sender")
        msg_type = message.get("type")
        content = message.get("content")
        conversation_id = message.get("conversation_id")
        group_id = message.get("group_id", 0)
        step_index = message.get("step_index", 0)
        ref_id = message.get("ref_id") or (content.get("ref_id") if isinstance(content, dict) else None)
        run_id = message.get("run_id") or (content.get("run_id") if isinstance(content, dict) else None)
        subagent_run_id = message.get("subagent_run_id") or (
            content.get("subagent_run_id") if isinstance(content, dict) else None
        )

        if msg_type == MessageType.LLM_TEXT_FINAL or msg_type == MessageType.LLM_TEXT_FINAL.value:
            msg_type = MessageType.CHAT.value

        try:
            db_content = content
            if not isinstance(db_content, dict):
                db_content = {"text": str(content)}

            await storage.add_history_record(
                session_id=session_id,
                user_id=user_id,
                sender=sender,
                msg_type=msg_type,
                content=db_content,
                conversation_id=conversation_id,
                group_id=group_id,
                step_index=step_index,
                ref_id=ref_id,
                run_id=run_id,
                subagent_run_id=subagent_run_id,
            )
        except Exception as e:
            logger.error(f">>> [History Error] 保存失败: {e}")

    async def get_agent_stream(
        self,
        user_id: str,
        user_token: str,
        session_id: str,
        input_data: dict,
        mcp_list: list = None,
        agent_id: str = None,
        continue_on_disconnect: bool = True,
        selected_skill_ids: list = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        if mcp_list is None:
            mcp_list = []

        stop_event = asyncio.Event()
        current_task = None
        is_resume = bool(input_data.get("command"))
        initial_step_index = 0
        turn_interrupted = False

        if self.is_deep_runtime:
            if is_resume:
                resume_ctx = input_data.get("resume_context") or {}
                turn = await load_turn(self.store, session_id)
                run_id = (
                    resume_ctx.get("run_id")
                    or (turn or {}).get("run_id")
                    or str(uuid.uuid4())
                )
                conversation_id = (
                    resume_ctx.get("conversation_id")
                    or (turn or {}).get("conversation_id")
                    or str(uuid.uuid4())
                )
                initial_step_index = resolve_resume_step_index(turn, resume_ctx)
                await mark_resuming(self.store, session_id)
            else:
                turn = await start_turn(self.store, session_id, user_id)
                run_id = turn["run_id"]
                conversation_id = turn["conversation_id"]
                initial_step_index = 0
        else:
            run_id = str(uuid.uuid4())
            conversation_id = str(uuid.uuid4())

        run_logger = logger.bind(run_id=run_id, session_id=session_id, user_id=user_id)

        try:
            await self._prepare_session_run(session_id)
            current_task = self._register_session_run(session_id, stop_event)

            if self.is_deep_runtime:
                if is_resume:
                    await insert_agent_run_event(
                        run_id=run_id,
                        session_id=session_id,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        event_type="hitl_resolved",
                    )
                else:
                    await insert_agent_run_event(
                        run_id=run_id,
                        session_id=session_id,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        event_type="turn_start",
                    )
            else:
                await insert_agent_run_event(
                    run_id=run_id,
                    session_id=session_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    event_type="run_start",
                )

            user_msg = None
            if input_data and not input_data.get("command"):
                user_msg = (input_data.get("content") or "").strip() or None

            agent, init_err = await self.create_agent(
                user_id,
                user_token,
                session_id,
                mcp_list,
                agent_id,
                user_message=user_msg,
                selected_skill_ids=selected_skill_ids,
            )

            if init_err:
                yield AgentMessage.error(session_id=session_id, content=init_err).to_frontend_dict()
                yield AgentMessage.end(session_id=session_id, reason=EndReason.ERROR).to_frontend_dict()
                return

            if not agent_id:
                agent_id = await deep_storage.get_session_agent_id(session_id)

            config = self._create_agent_config(
                user_id,
                user_token,
                session_id,
                run_id=run_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
            )

            if self.is_deep_runtime:
                stream_fn = run_deep_agent(
                    agent,
                    input_data=input_data,
                    config=config,
                    session_id=session_id,
                    user_id=user_id,
                    stop_event=stop_event,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    continue_on_disconnect=continue_on_disconnect,
                    agent_id=agent_id,
                    initial_step_index=initial_step_index,
                    is_resume=is_resume,
                )
            else:
                stream_fn = run_agent(
                    agent=agent,
                    input_data=input_data,
                    config=config,
                    session_id=session_id,
                    stop_event=stop_event,
                    conversation_id=conversation_id,
                )

            async for message in stream_fn:
                should_save = message.pop("save", False)
                if should_save:
                    await self._save_message_to_db(message, user_id)

                msg_sender = message.get("sender")
                if msg_sender == SenderType.USER or msg_sender == SenderType.USER.value:
                    continue

                msg_type = message.get("type")
                if msg_type == MessageType.LLM_TEXT_FINAL or msg_type == MessageType.LLM_TEXT_FINAL.value:
                    continue

                if (
                    self.is_deep_runtime
                    and (msg_type == MessageType.INTERRUPT or msg_type == MessageType.INTERRUPT.value)
                ):
                    turn_interrupted = True

                yield message

        except asyncio.CancelledError:
            run_logger.info(f"[AgentManager] 会话 {session_id} 被取消")
            if self.is_deep_runtime:
                await insert_agent_run_event(
                    run_id=run_id,
                    session_id=session_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    event_type="turn_end",
                    category="stopped",
                )
            else:
                await insert_agent_run_event(
                    run_id=run_id,
                    session_id=session_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    event_type="stopped",
                )
            yield AgentMessage.end(session_id=session_id, reason=EndReason.STOP).to_frontend_dict()
        except Exception as e:
            run_logger.error(f"[AgentManager] 会话 {session_id} 出错: {e}")
            if self.is_deep_runtime:
                await insert_agent_run_event(
                    run_id=run_id,
                    session_id=session_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    event_type="turn_end",
                    category="error",
                    message=str(e)[:500],
                )
            else:
                await insert_agent_run_event(
                    run_id=run_id,
                    session_id=session_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    event_type="error_classified",
                    message=str(e)[:500],
                )
            yield AgentMessage.error(session_id=session_id, content=str(e)).to_frontend_dict()
            yield AgentMessage.end(session_id=session_id, reason=EndReason.ERROR).to_frontend_dict()
        finally:
            if not self.is_deep_runtime:
                await insert_agent_run_event(
                    run_id=run_id,
                    session_id=session_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    event_type="run_end",
                )
            elif turn_interrupted:
                pass
            self._cleanup_session_run(session_id, stop_event, current_task)

    async def get_agent_result(
        self,
        user_id: str,
        user_token: str,
        session_id: str,
        input_data: dict,
        mcp_list: list = None,
        agent_id: str = None,
        selected_skill_ids: list = None,
    ) -> Dict[str, Any]:
        if mcp_list is None:
            mcp_list = []

        stop_event = asyncio.Event()
        current_task = None
        is_resume = bool(input_data.get("command"))
        initial_step_index = 0

        if self.is_deep_runtime:
            if is_resume:
                resume_ctx = input_data.get("resume_context") or {}
                turn = await load_turn(self.store, session_id)
                run_id = (
                    resume_ctx.get("run_id")
                    or (turn or {}).get("run_id")
                    or str(uuid.uuid4())
                )
                conversation_id = (
                    resume_ctx.get("conversation_id")
                    or (turn or {}).get("conversation_id")
                    or str(uuid.uuid4())
                )
                initial_step_index = resolve_resume_step_index(turn, resume_ctx)
                await mark_resuming(self.store, session_id)
            else:
                turn = await start_turn(self.store, session_id, user_id)
                run_id = turn["run_id"]
                conversation_id = turn["conversation_id"]
        else:
            run_id = str(uuid.uuid4())
            conversation_id = str(uuid.uuid4())

        try:
            await self._prepare_session_run(session_id)
            current_task = self._register_session_run(session_id, stop_event)

            if self.is_deep_runtime:
                if is_resume:
                    await insert_agent_run_event(
                        run_id=run_id,
                        session_id=session_id,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        event_type="hitl_resolved",
                    )
                else:
                    await insert_agent_run_event(
                        run_id=run_id,
                        session_id=session_id,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        event_type="turn_start",
                    )
            else:
                await insert_agent_run_event(
                    run_id=run_id,
                    session_id=session_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    event_type="run_start",
                )

            user_msg = None
            if input_data and not input_data.get("command"):
                user_msg = (input_data.get("content") or "").strip() or None

            agent, init_err = await self.create_agent(
                user_id,
                user_token,
                session_id,
                mcp_list,
                agent_id,
                user_message=user_msg,
                selected_skill_ids=selected_skill_ids,
            )
            if init_err:
                return {
                    "messages": [],
                    "final_answer": "",
                    "reasoning": "",
                    "tool_calls": [],
                    "tool_results": [],
                    "status": "error",
                    "error": init_err,
                    "interrupt": {},
                    "agent_mode": "deep" if self.is_deep_runtime else "light",
                    "run_id": run_id,
                }

            if not agent_id:
                agent_id = await deep_storage.get_session_agent_id(session_id)

            config = self._create_agent_config(
                user_id,
                user_token,
                session_id,
                run_id=run_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
            )

            if self.is_deep_runtime:
                result = await run_deep_agent_collect(
                    agent,
                    input_data=input_data,
                    config=config,
                    session_id=session_id,
                    user_id=user_id,
                    stop_event=stop_event,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    agent_id=agent_id,
                    initial_step_index=initial_step_index,
                    is_resume=is_resume,
                )
            else:
                result = await run_agent_ainvoke(
                    agent=agent,
                    input_data=input_data,
                    config=config,
                    session_id=session_id,
                    stop_event=stop_event,
                    conversation_id=conversation_id,
                )

            for msg in result["messages"]:
                should_save = msg.pop("save", False)
                if should_save:
                    await self._save_message_to_db(msg, user_id)

            frontend_messages = []
            for msg in result["messages"]:
                msg_sender = msg.get("sender")
                if msg_sender == SenderType.USER or msg_sender == SenderType.USER.value:
                    continue
                msg_type = msg.get("type")
                if msg_type == MessageType.LLM_TEXT_FINAL or msg_type == MessageType.LLM_TEXT_FINAL.value:
                    continue
                frontend_messages.append(msg)

            result["messages"] = frontend_messages
            return result

        except asyncio.CancelledError:
            logger.info(f"[AgentManager] 非流式会话 {session_id} 被取消")
            return {
                "messages": [],
                "final_answer": "",
                "reasoning": "",
                "tool_calls": [],
                "tool_results": [],
                "status": "stopped",
                "error": "",
                "interrupt": {},
            }
        except Exception as e:
            logger.error(f"[AgentManager] 非流式会话 {session_id} 出错: {e}")
            return {
                "messages": [],
                "final_answer": "",
                "reasoning": "",
                "tool_calls": [],
                "tool_results": [],
                "status": "error",
                "error": str(e),
                "interrupt": {},
            }
        finally:
            if not self.is_deep_runtime:
                await insert_agent_run_event(
                    run_id=run_id,
                    session_id=session_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    event_type="run_end",
                )
            self._cleanup_session_run(session_id, stop_event, current_task)

    def stop_task(self, session_id: str, user_id: str = None):
        if session_id in self._stop_events:
            self._stop_events[session_id].set()
        if session_id in self._active_tasks:
            task = self._active_tasks[session_id]
            if not task.done():
                task.cancel()

    def disconnect_connection(self, session_id: str = None):
        if session_id:
            self.stop_task(session_id)
            logger.info(f"[AgentManager] 会话 {session_id} 已断开连接，已触发停止")
