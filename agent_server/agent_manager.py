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


def _should_skip_ws_subagent_event(message: Dict[str, Any]) -> bool:
    """子智能体事件 WS 推送过滤。

    子智能体的消息按 subagent_run_id 包装为 SUBAGENT_EVENT 后，agent_manager
    无法再用消息类型区分"流式推送"与"落库专用"两条通道，这里按 content 结构
    精确区分：

    - 流式 chunk：content 为**字符串**（subevent_type 在消息外层），必须推送
    - 落库的完整文本：content 为 **dict**（{"text": ..., "subevent_type": ...}），
      流式 chunk 已推过同一内容，再推会导致前端追加到同一事件内容翻倍
    - reasoning_final：完整推理文本，同样只落库不推送

    仅过滤 dict 型消息：reasoning_final（完整推理）与 chat（完整回复）。
    """
    msg_type = message.get("type")
    if msg_type != MessageType.SUBAGENT_EVENT and msg_type != MessageType.SUBAGENT_EVENT.value:
        return False
    content = message.get("content") or {}
    if not isinstance(content, dict):
        # 流式 chunk（字符串 content）必须推送
        return False
    subevent_type = content.get("subevent_type", "")
    if subevent_type == "reasoning_final":
        return True
    if subevent_type == "chat":
        # 落库的完整回复文本，流式 chunk 已推送同一内容
        return True
    return False

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
        self._active_task_users: Dict[str, str] = {}  # session_id -> user_id
        self._stop_events: Dict[str, asyncio.Event] = {}
        # 已发出过停止请求（cancel）的任务：同一任务只取消一次，
        # 避免用户重复点击停止时二次 cancel 打断 runner 的优雅收尾（end 消息落库）
        self._stop_requested: Dict[str, asyncio.Task] = {}
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

    def _register_session_run(
        self,
        session_id: str,
        stop_event: asyncio.Event,
        user_id: str = None,
    ) -> asyncio.Task:
        self._stop_events[session_id] = stop_event
        current_task = asyncio.current_task()
        if current_task is not None:
            self._active_tasks[session_id] = current_task
            if user_id:
                self._active_task_users[session_id] = user_id
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
            self._active_task_users.pop(session_id, None)
        if task is not None and self._stop_requested.get(session_id) is task:
            self._stop_requested.pop(session_id, None)

        lock = self._session_locks.get(session_id)
        if lock is not None and lock.locked():
            lock.release()

    def list_active_runs_for_user(self, user_id: str) -> list:
        """返回该用户当前仍在执行的会话列表。"""
        items = []
        for sid, task in list(self._active_tasks.items()):
            if task is None or task.done():
                continue
            if self._active_task_users.get(sid) != user_id:
                continue
            items.append({
                "session_id": sid,
                "user_id": user_id,
                "status": "running",
            })
        return items

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
                    mcp_tools = await mcp_service.get_mcp_toollist(
                        mcp_list, user_id=user_id, user_token=user_token
                    )
                    logger.info(f"[AgentManager] 成功加载 {len(mcp_tools)} 个 MCP 工具")
                except Exception as e:
                    logger.error(f"[AgentManager] 加载 MCP 工具失败: {e}")

            # 合并内置工具（无需 MCP 配置，始终可用）
            from builtin_tools import get_builtin_tools
            builtin_tools = get_builtin_tools(
                user_id=user_id,
                user_token=user_token,
                session_id=session_id,
                capabilities={"mcp": mcp_list, "skills": selected_skill_ids},
            )
            all_tools = builtin_tools + mcp_tools  # 内置工具排前面

            logger.info(f"[AgentManager] 正在创建新 Agent 实例, session_id: {session_id}")

            agent = create_base_agent(
                model=model,
                system_prompt=system_prompt,
                tools=all_tools,
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
        # runner 是否已自行产出 end 消息（用于取消兜底去重）
        end_emitted = False

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
            current_task = self._register_session_run(session_id, stop_event, user_id=user_id)

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
                if msg_type == MessageType.END or msg_type == MessageType.END.value:
                    # runner 的优雅收尾已自行产出 end 记录
                    end_emitted = True
                if msg_type == MessageType.LLM_TEXT_FINAL or msg_type == MessageType.LLM_TEXT_FINAL.value:
                    continue
                if msg_type == MessageType.REASONING_FINAL or msg_type == MessageType.REASONING_FINAL.value:
                    continue
                # 子智能体的落库消息（REASONING_FINAL / chat 完整文本）包装为
                # SUBAGENT_EVENT 存库（确保按 subagent_run_id 分组），但 WS
                # 推送时按 subevent_type + content 结构过滤——完整文本与已推的
                # 流式 chunk 内容重复，推送会导致前端渲染两次
                if _should_skip_ws_subagent_event(message):
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
            if not end_emitted:
                # runner 的优雅收尾被打断（未产出 end）：补写一条 end(stop) 并落库，
                # 保证「停止」在 DB 与前端都有明确记录，避免停止无痕、无法排查
                fallback_end = {
                    "type": MessageType.END.value,
                    "sender": SenderType.SYSTEM.value,
                    "session_id": session_id,
                    "content": EndReason.STOP.value,
                    "conversation_id": conversation_id,
                    "group_id": 0,
                    "step_index": 0,
                    "run_id": run_id,
                }
                try:
                    await self._save_message_to_db(dict(fallback_end), user_id)
                except Exception as _e:
                    run_logger.error(f"[AgentManager] 补写 end(stop) 失败: {_e}")
                yield fallback_end
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
            current_task = self._register_session_run(session_id, stop_event, user_id=user_id)

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
                if msg_type == MessageType.REASONING_FINAL or msg_type == MessageType.REASONING_FINAL.value:
                    continue
                if _should_skip_ws_subagent_event(msg):
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
        task = self._active_tasks.get(session_id)
        if task is None or task.done():
            return
        # 同一任务只发一次 cancel：runner 收到取消后会进入"优雅收尾"
        # （finalize → 落库并推送 end(stop) → 释放沙箱）。重复 cancel 会
        # 在收尾的 await 期间再次抛入 CancelledError，打断收尾，导致
        # end(stop) 既不落库也不推送（用户重复点击停止时尤易触发）。
        if self._stop_requested.get(session_id) is task:
            return
        self._stop_requested[session_id] = task
        task.cancel()

    def owns_task(self, session_id: str, task: asyncio.Task) -> bool:
        """该 task 是否为 session 已登记的运行任务。

        调用方（WS stop 快速路径）以此判断是否需要对流任务补发 cancel：
        已登记的任务由 stop_task 统一接管（且带重复取消保护），
        只有尚未登记的早期阶段任务才需要单独取消。
        """
        return task is not None and self._active_tasks.get(session_id) is task

    def disconnect_connection(self, session_id: str = None):
        """WS 物理断开时清理路由映射，不终止 agent 任务。

        agent 继续后台运行至完成，_cleanup_session_run 在 finally 中释放锁。
        stop_task() 仅由用户主动 stop 操作或 Exception handler 调用。
        """
        if session_id:
            logger.info(f"[AgentManager] 会话 {session_id} 连接已断开，agent 继续后台运行")
