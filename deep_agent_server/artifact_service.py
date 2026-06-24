"""Artifact 存储服务。

工具结果过长时，完整内容写入磁盘 artifact，消息中仅保留 summary + ref_id，
前端可通过 ref_id 按需拉取全文。
"""

import uuid
from pathlib import Path
from typing import Optional

from config.config import app_config
from db import deep_storage
from deep_agent_server.quota_service import (
    QuotaExceededError,
    check_additional_bytes,
    refresh_workspace_usage,
)
from deep_agent_server.workspace import get_artifacts_dir, ensure_user_dirs


def truncate_summary(text: str, max_chars: int = None) -> str:
    limit = max_chars or app_config.CONTENT_SUMMARY_MAX_CHARS
    s = text or ""
    if len(s) <= limit:
        return s
    return s[:limit] + "…"


async def store_text_artifact(
    user_id: str,
    text: str,
    *,
    artifact_type: str = "tool_result",
    session_id: str = None,
    conversation_id: str = None,
    mime_type: str = "text/plain",
) -> str:
    """存储文本 artifact，超过上限仍存全文到磁盘，摘要截断。"""
    ensure_user_dirs(user_id)
    max_bytes = app_config.ARTIFACT_MAX_BYTES
    encoded = text.encode("utf-8")
    write_bytes = min(len(encoded), max_bytes)
    quota_err = check_additional_bytes(user_id, write_bytes)
    if quota_err:
        raise QuotaExceededError(quota_err)
    ref_id = str(uuid.uuid4())
    art_dir = get_artifacts_dir(user_id)
    art_dir.mkdir(parents=True, exist_ok=True)
    path = art_dir / f"{ref_id}.txt"
    # 先写入再按 ARTIFACT_MAX_BYTES 截断，DB 记录真实 size
    path.write_bytes(encoded[:max_bytes * 4])
    size = len(encoded)
    if size > max_bytes:
        path.write_bytes(encoded[:max_bytes])
        size = max_bytes

    summary = truncate_summary(text)
    await deep_storage.create_artifact(
        user_id,
        artifact_type,
        str(path),
        summary,
        size,
        session_id=session_id,
        conversation_id=conversation_id,
        mime_type=mime_type,
    )
    await refresh_workspace_usage(user_id)
    return ref_id


async def read_artifact_content(ref_id: str, user_id: str) -> Optional[str]:
    rec = await deep_storage.get_artifact(ref_id, user_id)
    if not rec:
        return None
    path = Path(rec["storage_path"])
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8", errors="replace")
