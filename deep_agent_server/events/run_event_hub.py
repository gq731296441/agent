"""Run 事件环形缓冲 — 单实例进程内断线续接。

每个执行中的 session/run 维护有序帧（带 seq），推送前写入缓冲；
WebSocket 重连后按 last_seq 补发。缓冲不足则返回 resync_required。
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

from config import get_logger

logger = get_logger(__name__)

DEFAULT_MAX_FRAMES = 2000
DEFAULT_TTL_SECONDS = 600  # 10 分钟
DEFAULT_RETAIN_AFTER_END_SECONDS = 60


@dataclass
class _BufferedFrame:
    seq: int
    payload: Dict[str, Any]
    ts: float = field(default_factory=time.time)


@dataclass
class _RunBuffer:
    session_id: str
    run_id: Optional[str] = None
    frames: Deque[_BufferedFrame] = field(default_factory=deque)
    next_seq: int = 1
    ended_at: Optional[float] = None
    max_frames: int = DEFAULT_MAX_FRAMES

    def append(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        seq = self.next_seq
        self.next_seq += 1
        out = dict(payload)
        out["seq"] = seq
        if "session_id" not in out:
            out["session_id"] = self.session_id
        self.frames.append(_BufferedFrame(seq=seq, payload=out))
        while len(self.frames) > self.max_frames:
            self.frames.popleft()
        msg_type = out.get("type")
        if msg_type == "end":
            self.ended_at = time.time()
        return out

    def replay_after(self, last_seq: int) -> Tuple[Optional[List[Dict[str, Any]]], bool]:
        """返回 (frames, ok)。ok=False 表示缓冲不足，需 resync。"""
        if not self.frames:
            if last_seq <= 0:
                return [], True
            return None, False
        oldest = self.frames[0].seq
        if last_seq > 0 and last_seq < oldest - 1:
            return None, False
        out = [f.payload for f in self.frames if f.seq > last_seq]
        return out, True


class RunEventHub:
    """进程内 Run 事件 Hub。"""

    def __init__(
        self,
        max_frames: int = DEFAULT_MAX_FRAMES,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        retain_after_end: int = DEFAULT_RETAIN_AFTER_END_SECONDS,
    ):
        self._buffers: Dict[str, _RunBuffer] = {}
        self._lock = asyncio.Lock()
        self.max_frames = max_frames
        self.ttl_seconds = ttl_seconds
        self.retain_after_end = retain_after_end

    def _get_or_create(self, session_id: str, run_id: Optional[str] = None) -> _RunBuffer:
        buf = self._buffers.get(session_id)
        if buf is None:
            buf = _RunBuffer(
                session_id=session_id,
                run_id=run_id,
                max_frames=self.max_frames,
            )
            self._buffers[session_id] = buf
        elif run_id and not buf.run_id:
            buf.run_id = run_id
        return buf

    async def publish(self, session_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """写入缓冲并返回带 seq 的帧。"""
        run_id = payload.get("run_id")
        async with self._lock:
            self._purge_locked()
            buf = self._get_or_create(session_id, run_id)
            return buf.append(payload)

    async def attach(
        self,
        session_id: str,
        last_seq: int = 0,
    ) -> Dict[str, Any]:
        """按 last_seq 补发。返回 {ok, frames} 或 {resync_required: True}。"""
        async with self._lock:
            self._purge_locked()
            buf = self._buffers.get(session_id)
            if buf is None:
                if last_seq > 0:
                    return {"resync_required": True, "session_id": session_id}
                return {"ok": True, "frames": [], "session_id": session_id}
            frames, ok = buf.replay_after(int(last_seq or 0))
            if not ok:
                return {"resync_required": True, "session_id": session_id}
            return {"ok": True, "frames": frames or [], "session_id": session_id}

    async def drop(self, session_id: str) -> None:
        async with self._lock:
            self._buffers.pop(session_id, None)

    def _purge_locked(self) -> None:
        now = time.time()
        dead = []
        for sid, buf in self._buffers.items():
            if buf.ended_at and (now - buf.ended_at) > self.retain_after_end:
                dead.append(sid)
                continue
            if buf.frames:
                oldest_ts = buf.frames[0].ts
                if (now - oldest_ts) > self.ttl_seconds and buf.ended_at:
                    dead.append(sid)
        for sid in dead:
            self._buffers.pop(sid, None)


# 进程单例
run_event_hub = RunEventHub()
