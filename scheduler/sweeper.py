"""定时任务调度轮询 — 周期扫描到期任务并触发执行。"""

from __future__ import annotations

import asyncio
from typing import Optional

from config import get_logger
from config.config import app_config
from db import scheduled_store

from .executor import execute_scheduled_task

logger = get_logger(__name__)

_sweeper_task: Optional[asyncio.Task] = None


async def sweep_due_tasks(manager_agents) -> int:
    """扫描到期任务并触发执行；返回拉起数量。

    抢占（claim）统一由 execute_scheduled_task 内部完成，
    避免此处先 claim 导致执行器二次 claim 失败而停止执行。
    """
    tasks = await scheduled_store.list_due_tasks(limit=50)
    launched = 0
    for t in tasks:
        asyncio.create_task(execute_scheduled_task(t, manager_agents))
        launched += 1
    return launched


async def _sweeper_loop(manager_agents) -> None:
    interval = max(10, int(app_config.SCHEDULED_TASK_SWEEPER_INTERVAL_SECONDS or 60))
    while True:
        try:
            count = await sweep_due_tasks(manager_agents)
            if count:
                logger.info(f"[Scheduler] 本轮拉起 {count} 个到期任务")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[Scheduler] 扫描异常: {e}")
        await asyncio.sleep(interval)


async def start_scheduled_sweeper(manager_agents) -> None:
    global _sweeper_task
    if not app_config.SCHEDULED_TASK_SWEEPER_INTERVAL_SECONDS:
        logger.info("[Scheduler] 已禁用 (SCHEDULED_TASK_SWEEPER_INTERVAL_SECONDS=0)")
        return
    if _sweeper_task and not _sweeper_task.done():
        return
    _sweeper_task = asyncio.create_task(_sweeper_loop(manager_agents))
    logger.info("[Scheduler] 已启动定时任务 sweeper")


async def stop_scheduled_sweeper() -> None:
    global _sweeper_task
    if _sweeper_task and not _sweeper_task.done():
        _sweeper_task.cancel()
        try:
            await _sweeper_task
        except asyncio.CancelledError:
            pass
    _sweeper_task = None
