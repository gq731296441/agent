"""Legacy artifact 存储（用户级 artifacts/ 目录 + deep_workspace_files）。"""

import uuid
from pathlib import Path
from typing import Optional

from config.config import app_config
from db import deep_storage
from deep_agent_server.artifact.summary import truncate_summary
from deep_agent_server.quota_service import (
    QuotaExceededError,
    check_additional_bytes,
    refresh_workspace_usage,
)
from deep_agent_server.workspace import ensure_user_dirs, get_artifacts_dir


async def store_text_artifact(
    user_id: str,
    text: str,
    *,
    artifact_type: str = "tool_result",
    session_id: str = None,
    conversation_id: str = None,
    mime_type: str = "text/plain",
    rel_path: Optional[str] = None,
    ref_id: Optional[str] = None,
) -> str:
    """存储文本 artifact 到 {user}/artifacts/ 或指定 rel_path 镜像。"""
    ensure_user_dirs(user_id)
    max_bytes = app_config.ARTIFACT_MAX_BYTES
    encoded = text.encode("utf-8")
    write_bytes = min(len(encoded), max_bytes)
    quota_err = check_additional_bytes(user_id, write_bytes)
    if quota_err:
        raise QuotaExceededError(quota_err)

    rid = ref_id or str(uuid.uuid4())
    if rel_path:
        from deep_agent_server.workspace import get_workspace_dir

        if not session_id:
            raise ValueError("session_id required when rel_path is set")
        path = get_workspace_dir(user_id, session_id) / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        art_dir = get_artifacts_dir(user_id)
        art_dir.mkdir(parents=True, exist_ok=True)
        path = art_dir / f"{rid}.txt"
        rel_path = f"artifacts/{rid}.txt"

    path.write_bytes(encoded[: max_bytes * 4])
    size = len(encoded)
    if size > max_bytes:
        path.write_bytes(encoded[:max_bytes])
        size = max_bytes

    summary = truncate_summary(text)
    await deep_storage.create_artifact_with_path(
        user_id,
        artifact_type,
        str(path),
        summary,
        size,
        rel_path=rel_path,
        session_id=session_id,
        conversation_id=conversation_id,
        mime_type=mime_type,
        ref_id=rid,
    )
    await refresh_workspace_usage(user_id)
    return rid
