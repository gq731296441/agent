"""会话就绪状态机 + Pending Messages。

Deep 模式新建 session 后需后台 warmup（创建 agent + 拉起 sandbox），
warmup 期间用户消息入 pending 队列，READY 后顺序 flush 处理。
"""

import asyncio
from typing import Any, Callable, Dict, List, Optional

from config import get_logger
from config.config import app_config
from db import deep_storage
from deep_agent_server.session.models import SessionStartStatus

logger = get_logger(__name__)

_warmup_tasks: Dict[str, asyncio.Task] = {}
_session_locks: Dict[str, asyncio.Lock] = {}


def _get_lock(session_id: str) -> asyncio.Lock:
    """每 session 一把锁，防止并发 warmup 重复执行。"""
    if session_id not in _session_locks:
        _session_locks[session_id] = asyncio.Lock()
    return _session_locks[session_id]


class SessionReadinessService:
    """管理 session warmup 与 pending 消息队列。"""

    @staticmethod
    async def get_status(session_id: str) -> str:
        row = await deep_storage.get_session_start_task(session_id)
        if not row:
            return SessionStartStatus.READY.value
        return row.get("status") or SessionStartStatus.CREATED.value

    @staticmethod
    async def is_ready(session_id: str) -> bool:
        return await SessionReadinessService.get_status(session_id) == SessionStartStatus.READY.value

    @staticmethod
    async def ensure_task(session_id: str, user_id: str) -> None:
        row = await deep_storage.get_session_start_task(session_id)
        if not row:
            await deep_storage.upsert_session_start_task(
                session_id, user_id, SessionStartStatus.CREATED.value
            )

    @staticmethod
    def start_warmup(
        session_id: str,
        user_id: str,
        user_token: str,
        agent_id: Optional[str],
        mcp_list: list,
        manager_agents,
        *,
        on_ready: Optional[Callable] = None,
        send_fn: Optional[Callable] = None,
        selected_skill_ids: Optional[List[str]] = None,
    ) -> asyncio.Task:
        """后台 warmup；若已有任务在跑则复用。"""
        existing = _warmup_tasks.get(session_id)
        if existing and not existing.done():
            return existing

        task = asyncio.create_task(
            SessionReadinessService._warmup(
                session_id,
                user_id,
                user_token,
                agent_id,
                mcp_list,
                manager_agents,
                on_ready=on_ready,
                send_fn=send_fn,
                selected_skill_ids=selected_skill_ids,
            )
        )
        _warmup_tasks[session_id] = task
        return task

    @staticmethod
    async def _warmup(
        session_id: str,
        user_id: str,
        user_token: str,
        agent_id: Optional[str],
        mcp_list: list,
        manager_agents,
        *,
        on_ready: Optional[Callable] = None,
        send_fn: Optional[Callable] = None,
        selected_skill_ids: Optional[List[str]] = None,
    ) -> None:
        """
        后台 warmup 核心流程：
        1. WARMING_AGENT — 创建 agent（always_on skills，skip_sandbox）
        2. WARMING_SANDBOX — 拉起 Docker 沙箱（可选）
        3. READY — 通知前端，触发 on_ready 回调 flush pending
        """
        lock = _get_lock(session_id)
        async with lock:
            try:
                await SessionReadinessService.ensure_task(session_id, user_id)
                await SessionReadinessService._notify_warming(
                    send_fn, session_id, SessionStartStatus.WARMING_AGENT.value
                )
                await deep_storage.upsert_session_start_task(
                    session_id, user_id, SessionStartStatus.WARMING_AGENT.value
                )

                _, err = await manager_agents.create_agent(
                    user_id,
                    user_token,
                    session_id,
                    mcp_list,
                    agent_id,
                    always_on_only=True,
                    skip_sandbox=True,
                    selected_skill_ids=selected_skill_ids,
                )
                if err:
                    raise RuntimeError(err)

                if app_config.SANDBOX_ENABLED and app_config.AGENT_RUNTIME == "deep":
                    if app_config.SANDBOX_WARMUP_EAGER:
                        await SessionReadinessService._notify_warming(
                            send_fn, session_id, SessionStartStatus.WARMING_SANDBOX.value
                        )
                        await deep_storage.upsert_session_start_task(
                            session_id, user_id, SessionStartStatus.WARMING_SANDBOX.value
                        )
                        from deep_agent_server.sandbox import try_ensure_sandbox_running
                        from deep_agent_server.sandbox.exceptions import SandboxUserLimitExceeded

                        try:
                            sandbox_ok = await try_ensure_sandbox_running(user_id, session_id)
                        except SandboxUserLimitExceeded as limit_err:
                            raise limit_err

                        if sandbox_ok:
                            from deep_agent_server.manager_ext import (
                                _resolve_skill_dirs_for_agent,
                                _session_agent_context,
                            )
                            from deep_agent_server.skills.session_mount import (
                                ensure_session_skill_mount,
                            )

                            agent_kind, resolved_agent_id, _ua, creator_uid = await _session_agent_context(
                                user_id, session_id, agent_id
                            )
                            if not resolved_agent_id and agent_kind == "subscription":
                                resolved_agent_id = await deep_storage.get_session_agent_id(session_id)
                            candidate_dirs = await _resolve_skill_dirs_for_agent(
                                agent_kind,
                                user_id,
                                resolved_agent_id or "",
                                creator_uid,
                            )
                            from deep_agent_server.skills.selection_guard import (
                                SkillSelectionLockedError,
                            )

                            try:
                                await ensure_session_skill_mount(
                                    user_id,
                                    session_id,
                                    candidate_dirs,
                                    selected_skill_ids,
                                    manager=manager_agents,
                                )
                            except SkillSelectionLockedError:
                                raise  # 业务错误（skill 勾选锁定）照常上报
                            except Exception as e:
                                # 沙箱基础设施故障：降级为"无 execute"，warmup 仍 READY。
                                # 与上方 try_ensure_sandbox_running 的语义对齐 —— 此前
                                # 这里失败会让整个 warmup ERROR、会话卡在 ERROR 状态，
                                # 而两者相隔仅二十余行。
                                logger.warning(
                                    f"[SessionReadiness] skill mount 失败，降级无 execute "
                                    f"session={session_id}: {e}"
                                )
                                sandbox_ok = False
                    else:
                        sandbox_ok = None
                else:
                    sandbox_ok = None

                await deep_storage.upsert_session_start_task(
                    session_id, user_id, SessionStartStatus.READY.value
                )
                await SessionReadinessService._notify_ready(
                    send_fn, session_id, sandbox_available=sandbox_ok
                )

                if on_ready:
                    await on_ready(session_id)

            except Exception as e:
                logger.error(f"[SessionReadiness] warmup failed session={session_id}: {e}")
                from deep_agent_server.sandbox.exceptions import SandboxUserLimitExceeded

                if isinstance(e, SandboxUserLimitExceeded):
                    err_content = e.to_error_payload()
                else:
                    err_content = f"会话预热失败: {e}"
                try:
                    await deep_storage.upsert_session_start_task(
                        session_id, user_id, SessionStartStatus.ERROR.value, error=str(e)[:500]
                    )
                except Exception as db_err:
                    # 错误处理器自身不能抛：否则新异常会取代原始异常向外传播，
                    # 把真正的失败原因掩盖掉。
                    logger.error(
                        f"[SessionReadiness] 写入 ERROR 状态失败 "
                        f"session={session_id}: {db_err}"
                    )
                if send_fn:
                    from common.enums import MessageType, SenderType
                    from common.schemas import AgentMessage
                    await send_fn(
                        AgentMessage(
                            type=MessageType.ERROR,
                            sender=SenderType.SYSTEM,
                            session_id=session_id,
                            content=err_content,
                            save=False,
                        ).to_frontend_dict(),
                        session_id,
                    )
            finally:
                _warmup_tasks.pop(session_id, None)

    @staticmethod
    async def _notify_warming(send_fn, session_id: str, status: str) -> None:
        if not send_fn:
            return
        from common.enums import MessageType, SenderType
        from common.schemas import AgentMessage
        await send_fn(
            AgentMessage(
                type=MessageType.SESSION_WARMING,
                sender=SenderType.SYSTEM,
                session_id=session_id,
                content={"status": status},
                save=False,
            ).to_frontend_dict(),
            session_id,
        )

    @staticmethod
    async def _notify_ready(
        send_fn,
        session_id: str,
        *,
        sandbox_available: Optional[bool] = None,
    ) -> None:
        if not send_fn:
            return
        from common.enums import MessageType, SenderType
        from common.schemas import AgentMessage
        content: Dict[str, Any] = {"status": SessionStartStatus.READY.value}
        if sandbox_available is not None:
            content["sandbox_available"] = sandbox_available
            if not sandbox_available:
                content["sandbox_hint"] = (
                    "execute 不可用：请启动 Docker Desktop，"
                    "或设置 SANDBOX_ENABLED=false"
                )
        await send_fn(
            AgentMessage(
                type=MessageType.SESSION_READY,
                sender=SenderType.SYSTEM,
                session_id=session_id,
                content=content,
                save=False,
            ).to_frontend_dict(),
            session_id,
        )

    @staticmethod
    async def enqueue_message(
        session_id: str,
        user_id: str,
        payload: dict,
    ) -> dict:
        """非 READY 时入队，返回 queued 响应。"""
        row = await deep_storage.get_session_start_task(session_id)
        if not row:
            return {"queued": False, "status": SessionStartStatus.READY.value}

        status = row.get("status") or SessionStartStatus.CREATED.value
        if status == SessionStartStatus.READY.value:
            return {"queued": False, "status": status}

        if status == SessionStartStatus.ERROR.value:
            # 由 ensure_warmup_started 负责恢复；此处仅排队等待重新预热
            await deep_storage.upsert_session_start_task(
                session_id, user_id, SessionStartStatus.CREATED.value
            )
            status = SessionStartStatus.CREATED.value

        await deep_storage.add_pending_message(session_id, user_id, payload)
        position = await deep_storage.count_pending_messages(session_id)
        return {"queued": True, "status": status, "position": position}

    @staticmethod
    async def ensure_warmup_started(
        session_id: str,
        user_id: str,
        user_token: str,
        payload: dict,
        manager_agents,
        *,
        send_fn: Optional[Callable] = None,
    ) -> None:
        """Deep 模式下：未 READY 且无进行中的 warmup 时自动启动/恢复预热。"""
        if (app_config.AGENT_RUNTIME or "deep").lower() == "light":
            return

        status = await SessionReadinessService.get_status(session_id)
        if status == SessionStartStatus.READY.value:
            return

        existing = _warmup_tasks.get(session_id)
        if existing and not existing.done():
            return

        if status == SessionStartStatus.ERROR.value:
            logger.info(
                f"[SessionReadiness] 从 ERROR 恢复预热 session={session_id}"
            )
            await deep_storage.upsert_session_start_task(
                session_id, user_id, SessionStartStatus.CREATED.value
            )

        capabilities = payload.get("capabilities") or {}
        mcp_list = capabilities.get("mcp") or []
        selected_skill_ids = capabilities.get("skills")
        if selected_skill_ids is not None and isinstance(selected_skill_ids, list):
            selected_skill_ids = [
                str(s).strip() for s in selected_skill_ids if str(s).strip()
            ]
        else:
            selected_skill_ids = None
        agent_id = payload.get("agent_id")

        SessionReadinessService.start_warmup(
            session_id,
            user_id,
            user_token,
            agent_id,
            mcp_list,
            manager_agents,
            send_fn=send_fn,
            selected_skill_ids=selected_skill_ids,
        )

    @staticmethod
    async def wait_until_ready(session_id: str, timeout: float = 120.0) -> bool:
        """等待 session 进入 READY（SSE 等同步场景）。

        必须先等待进行中的 warmup task，不能仅凭「无 session_start_tasks 行」判定 READY。
        """
        task = _warmup_tasks.get(session_id)
        if task:
            if not task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
                except asyncio.TimeoutError:
                    logger.warning(
                        f"[SessionReadiness] wait_until_ready timeout session={session_id}"
                    )
                    return False
            else:
                exc = task.exception()
                if exc:
                    logger.warning(
                        f"[SessionReadiness] warmup task failed session={session_id}: {exc}"
                    )
        return await SessionReadinessService.is_ready(session_id)

    @staticmethod
    async def flush_pending(
        session_id: str,
        user_id: str,
        process_fn: Callable,
        send_fn: Optional[Callable] = None,
    ) -> None:
        """顺序处理 pending messages。"""
        if not await SessionReadinessService.is_ready(session_id):
            return
        messages = await deep_storage.get_pending_messages(session_id)
        if not messages:
            return
        for item in messages:
            payload = item.get("payload") or {}
            try:
                await process_fn(session_id, user_id, payload)
            except Exception as e:
                logger.error(f"[SessionReadiness] flush error session={session_id}: {e}")
                # 本批消息紧接着会被整体删除，失败的那条不会重排队 —— 必须让
                # 用户知道它没被执行，否则消息静默消失、前端毫无感知。
                if send_fn:
                    try:
                        from common.enums import MessageType, SenderType
                        from common.schemas import AgentMessage

                        await send_fn(
                            AgentMessage(
                                type=MessageType.ERROR,
                                sender=SenderType.SYSTEM,
                                session_id=session_id,
                                content=f"消息处理失败，请重新发送: {str(e)[:200]}",
                                save=False,
                            ).to_frontend_dict(),
                            session_id,
                        )
                    except Exception as notify_err:
                        logger.warning(
                            f"[SessionReadiness] flush 失败通知发送失败: {notify_err}"
                        )
        await deep_storage.delete_pending_messages(session_id)
