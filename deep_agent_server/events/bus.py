"""异步内部 Event Bus。

Agent 主流 yield 消息时同步 publish 到 EventBus，
由后台 worker 异步消费（审计日志、运行指标等），不阻塞流式输出。
"""

import asyncio
from typing import Awaitable, Callable, List, Optional

from config import get_logger
from deep_agent_server.events.models import DomainEvent

logger = get_logger(__name__)

EventProcessor = Callable[[DomainEvent], Awaitable[None]]


class EventBus:
    """
    bounded queue + async processors，不阻塞 Agent 主流。

    队列满时丢弃事件并打 warning，避免背压拖慢 agent 响应。
    """

    def __init__(self, max_queue: int = 1000):
        self._processors: List[EventProcessor] = []
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue)
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False

    def register(self, processor: EventProcessor) -> None:
        self._processors.append(processor)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

    def publish(self, event: Optional[DomainEvent]) -> None:
        if event is None:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(f"[EventBus] queue full, drop {event.event_type}")

    async def _worker(self) -> None:
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            for proc in self._processors:
                try:
                    await proc(event)
                except Exception as e:
                    logger.warning(f"[EventBus] processor error {event.event_type}: {e}")


_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def publish_event(event: Optional[DomainEvent]) -> None:
    get_event_bus().publish(event)
