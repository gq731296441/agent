"""定时任务单次执行器：按任务归属（agent_id）分流执行。

- 订阅智能体路径（agent_id 非空）：在订阅智能体的既有会话继续对话，执行前清空该会话的 LLM 上下文。
- 默认智能体路径（agent_id 为空）：新开默认会话，注入记录的能力快照（SKILL/MCP）触发对话。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from config import get_logger
from config.config import app_config
from db import scheduled_store
from deep_agent_server.session.readiness import SessionReadinessService

from .cron import compute_next_run_at

logger = get_logger(__name__)

_MAX_FINAL_ANSWER_LEN = 1000


async def _run_once(task: Dict[str, Any], manager_agents) -> Dict[str, Any]:
    """执行单次任务（调用方已 claim），返回执行结果 dict。

    agent_id 非空（订阅智能体路径）→ 清空订阅会话上下文并继续对话；
    agent_id 为空（默认智能体路径）→ 新开默认会话离线回放能力快照。
    """
    if task.get("agent_id"):
        return await _run_subscription_once(task, manager_agents)
    return await _run_default_once(task, manager_agents)


async def _run_default_once(task: Dict[str, Any], manager_agents) -> Dict[str, Any]:
    """默认智能体路径：新开默认会话，注入记录的能力快照（skills + mcp）。"""
    user_id = task["user_id"]
    skills = task.get("skills") or []
    mcp = task.get("mcp") or []
    session_id = ""
    try:
        # 1. 新开会话（默认智能体，不绑定 agent）
        from utils.session_utils import setup_new_session

        session_id = str(uuid.uuid4())
        title = str(task.get("name") or "").strip() or "定时任务"
        await setup_new_session(
            user_id, session_id, title, agent_id=None, agent_kind="default"
        )

        # 2. 预热（注入记录的能力）
        SessionReadinessService.start_warmup(
            session_id, user_id, "", None, mcp, manager_agents,
            selected_skill_ids=skills,
        )
        if not await SessionReadinessService.wait_until_ready(session_id):
            return {"status": "error", "error": "会话预热超时或失败"}

        # 3. 执行对话（非流式，纯服务端）
        from api.search_api import process_agent_result

        payload = {
            "action": "ask",
            "user_id": user_id,
            "user_token": "",
            "session_id": session_id,
            "question": task["prompt"],
            "agent_id": None,
            "capabilities": {"mcp": mcp, "skills": skills},
        }
        timeout = max(30, int(app_config.SCHEDULED_TASK_RUN_TIMEOUT or 900))
        result = await asyncio.wait_for(
            process_agent_result(
                manager_agents=manager_agents,
                action="ask",
                user_id=user_id,
                user_token="",
                session_id=session_id,
                payload=payload,
                allow_if_missing=True,
            ),
            timeout=timeout,
        )
        return _normalize_result(result)
    except asyncio.TimeoutError:
        logger.warning(f"[Scheduler] 任务超时 task_id={task.get('id')}")
        return {"status": "error", "error": "执行超时"}
    except Exception as e:
        logger.warning(
            f"[Scheduler] 任务执行异常 task_id={task.get('id')} session={session_id}: {e}"
        )
        return {"status": "error", "error": str(e)[:500]}


def _normalize_result(result: Any) -> Dict[str, Any]:
    """将 process_agent_result 的返回值规范化为 (status, final_answer/error) dict。"""
    if not isinstance(result, dict):
        return {"status": "error", "error": "执行返回异常结果"}
    status = result.get("status", "")
    final_answer = str(result.get("final_answer") or "")[: _MAX_FINAL_ANSWER_LEN]
    if status in ("completed", "success"):
        return {"status": "completed", "final_answer": final_answer}
    return {
        "status": "error",
        "error": str(result.get("error") or f"执行失败: {status}")[:500],
    }


async def _run_subscription_once(task: Dict[str, Any], manager_agents) -> Dict[str, Any]:
    """订阅智能体路径：清空订阅会话上下文，然后在既有会话继续对话。

    不记录/注入 MCP/SKILL 快照——订阅智能体绑定的能力在运行时按 agent_id 解析。
    """
    user_id = task["user_id"]
    agent_id = str(task.get("agent_id") or "").strip()
    session_id = ""
    try:
        from db import deep_storage, storage

        # 1. 获取/确认订阅智能体的唯一会话
        session = await deep_storage.ensure_subscription_session(user_id, agent_id)
        session_id = str(session.get("session_id") or "")
        if not session_id:
            return {"status": "error", "error": "订阅智能体会话不存在"}
        if session.get("subscription_frozen"):
            return {"status": "error", "error": "订阅智能体会话已冻结，无法执行定时任务"}

        # 2. busy 判定：会话正被用户使用（warmup 中/有 pending 消息/有运行中 turn）则跳过，等下个周期
        if await _is_busy(manager_agents, user_id, session_id):
            return {"status": "skipped", "error": "会话使用中，跳过本次执行"}

        # 3. 清空 LLM 上下文（复用 new-context 清理逻辑，保留历史消息）
        meta = await storage.get_session_meta(session_id)
        expected_epoch = int((meta or {}).get("context_epoch") or 0)
        new_epoch = await storage.increment_context_epoch(session_id, expected_epoch)
        if new_epoch is None:
            return {"status": "skipped", "error": "会话上下文已变更（epoch 冲突），跳过本次执行"}

        thread_id = f"{user_id}#{session_id}"
        from api.session_api import _clear_agent_cache_for_session, _cleanup_langgraph_data

        await _cleanup_langgraph_data(
            getattr(manager_agents, "checkpointer", None),
            getattr(manager_agents, "store", None),
            thread_id,
            user_id=user_id,
            session_id=session_id,
        )
        _clear_agent_cache_for_session(manager_agents, session_id)
        await storage.insert_context_divider(session_id, user_id, new_epoch)
        logger.info(f"[Scheduler] 已清空订阅会话上下文 session={session_id} epoch={new_epoch}")

        # 4. 预热（按智能体绑定能力解析，无 MCP/SKILL 快照注入）
        SessionReadinessService.start_warmup(
            session_id, user_id, "", agent_id, [], manager_agents,
            selected_skill_ids=None,
        )
        if not await SessionReadinessService.wait_until_ready(session_id):
            return {"status": "error", "error": "会话预热超时或失败"}

        # 5. 在订阅会话继续对话
        from api.search_api import process_agent_result

        payload = {
            "action": "ask",
            "user_id": user_id,
            "user_token": "",
            "session_id": session_id,
            "question": task["prompt"],
            "agent_id": agent_id,
            "capabilities": {"mcp": [], "skills": []},
        }
        timeout = max(30, int(app_config.SCHEDULED_TASK_RUN_TIMEOUT or 900))
        result = await asyncio.wait_for(
            process_agent_result(
                manager_agents=manager_agents,
                action="ask",
                user_id=user_id,
                user_token="",
                session_id=session_id,
                payload=payload,
                allow_if_missing=True,
            ),
            timeout=timeout,
        )
        return _normalize_result(result)
    except asyncio.TimeoutError:
        logger.warning(f"[Scheduler] 订阅任务超时 task_id={task.get('id')}")
        return {"status": "error", "error": "执行超时"}
    except Exception as e:
        logger.warning(
            f"[Scheduler] 订阅任务执行异常 task_id={task.get('id')} "
            f"agent_id={agent_id} session={session_id}: {e}"
        )
        return {"status": "error", "error": str(e)[:500]}


async def _is_busy(manager_agents, user_id: str, session_id: str) -> bool:
    """判定订阅会话是否正被用户使用（占用时定时任务应跳过）。

    判据（任一命中即 busy）：
    1. 会话未就绪（warmup 中 / ERROR / CREATED）
    2. 有 pending 消息未消费
    3. 存在运行中 turn（kv_store 的 turn_active 记录）
    """
    try:
        from db import deep_storage

        status = await SessionReadinessService.get_status(session_id)
        if status != "READY":
            return True
        if await deep_storage.count_pending_messages(session_id) > 0:
            return True
        kv_store = getattr(manager_agents, "store", None)
        if kv_store:
            from deep_agent_server.store.namespaces import TURN_ACTIVE

            try:
                active = await kv_store.aget(TURN_ACTIVE, session_id)
            except Exception:
                active = None
            if active:
                return True
    except Exception as e:
        logger.warning(f"[Scheduler] busy 检测异常 session={session_id}: {e}")
    return False


async def execute_scheduled_task(task: Dict[str, Any], manager_agents) -> None:
    """调度入口：执行到期任务并回写状态/下次执行时间/上次结果。

    用 try/finally 保证任何路径（含异常/CancelledError）都复位状态，
    避免任务永久卡在 running。
    """
    task_id = task["id"]
    if not await scheduled_store.claim_task(task_id):
        logger.debug(f"[Scheduler] 任务抢占失败 task_id={task_id}")
        return  # 已被其他实例抢占，防重入

    last_result: Dict[str, Any] = {"status": "error", "error": "执行中断"}
    try:
        last_result = await _run_once(task, manager_agents)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # 兜底
        logger.warning(f"[Scheduler] 任务执行兜底异常 task_id={task_id}: {e}")
        last_result = {"status": "error", "error": str(e)[:500]}
    finally:
        # 无论成败都重算下次执行时间；失败保持 pending 让 cron 下次自然重跑
        try:
            next_run_at = compute_next_run_at(task["cron_expr"])
        except Exception:
            next_run_at = None
        if next_run_at is None:
            await scheduled_store.reset_task_status(task_id, "pending")
        else:
            await scheduled_store.finish_task(
                task_id,
                status="pending",
                next_run_at=next_run_at,
                last_result={
                    **last_result,
                    "ran_at": datetime.now(timezone.utc).isoformat(),
                },
                last_run_at=datetime.now(timezone.utc),
            )
        logger.info(
            f"[Scheduler] 任务执行完成 task_id={task_id} "
            f"result={last_result.get('status')} "
            f"next_run_at={next_run_at}"
        )
