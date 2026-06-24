"""Workspace tool result spill + Store metadata。"""

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config import get_logger
from config.config import app_config
from deep_agent_server.artifact.legacy import store_text_artifact
from deep_agent_server.artifact.models import ArtifactMetadata, SpillResult
from deep_agent_server.artifact.store_ext import put_artifact_metadata
from deep_agent_server.artifact.summary import truncate_summary
from deep_agent_server.quota_service import QuotaExceededError, check_additional_bytes, refresh_workspace_usage
from deep_agent_server.workspace import get_workspace_dir

logger = get_logger(__name__)

SPILL_MARKER = "[工具结果已外置]"


def _virtual_path(tool_call_id: str) -> str:
    subdir = (app_config.TOOL_SPILL_VIRTUAL_DIR or ".tool-cache").strip("/\\")
    return f"/workspace/{subdir}/{tool_call_id}.txt"


def _storage_path(user_id: str, session_id: str, tool_call_id: str) -> Path:
    subdir = (app_config.TOOL_SPILL_VIRTUAL_DIR or ".tool-cache").strip("/\\")
    return get_workspace_dir(user_id, session_id) / subdir / f"{tool_call_id}.txt"


def format_spill_content(
    *,
    summary: str,
    virtual_path: str,
    ref_id: Optional[str] = None,
) -> str:
    lines = [SPILL_MARKER, f"摘要: {summary}", f"路径: {virtual_path}"]
    if ref_id:
        lines.append(f"ref_id: {ref_id}")
    return "\n".join(lines)


def parse_spill_content(content: str) -> dict:
    """从 ToolMessage 解析 spill 字段。"""
    text = content or ""
    if SPILL_MARKER not in text:
        return {}
    out = {"spilled": True}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("摘要:"):
            out["summary"] = line[3:].strip()
        elif line.startswith("路径:"):
            out["virtual_path"] = line[3:].strip()
        elif line.startswith("ref_id:"):
            out["ref_id"] = line[7:].strip()
    return out


async def spill_tool_result(
    *,
    store: Any,
    user_id: str,
    session_id: str,
    tool_name: str,
    tool_call_id: str,
    result_text: str,
    conversation_id: Optional[str] = None,
) -> SpillResult:
    """超长工具结果 spill；返回替换后的 checkpoint 内容与展示字段。"""
    raw = result_text or ""
    summary = truncate_summary(raw)
    truncated = len(raw) > app_config.CONTENT_SUMMARY_MAX_CHARS

    if not app_config.is_tool_spill_enabled():
        return SpillResult(content=raw, summary=summary, truncated=truncated, spilled=False)

    min_chars = app_config.tool_spill_min_chars()
    if len(raw) < min_chars:
        return SpillResult(content=raw, summary=summary, truncated=truncated, spilled=False)

    call_id = (tool_call_id or "").strip() or str(uuid.uuid4())
    virtual = _virtual_path(call_id)
    path = _storage_path(user_id, session_id, call_id)

    max_bytes = app_config.ARTIFACT_MAX_BYTES
    encoded = raw.encode("utf-8")
    write_bytes = min(len(encoded), max_bytes)
    quota_err = check_additional_bytes(user_id, write_bytes)
    if quota_err:
        raise QuotaExceededError(quota_err)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded[:max_bytes])
    size = len(encoded) if len(encoded) <= max_bytes else max_bytes

    ref_id = str(uuid.uuid4())
    rel_path = f"{(app_config.TOOL_SPILL_VIRTUAL_DIR or '.tool-cache').strip('/')}/{call_id}.txt"

    if app_config.ARTIFACT_DB_MIRROR_ENABLED:
        try:
            ref_id = await store_text_artifact(
                user_id,
                raw,
                artifact_type="tool_result_v2",
                session_id=session_id,
                conversation_id=conversation_id,
                rel_path=rel_path,
                ref_id=ref_id,
            )
        except QuotaExceededError:
            raise
        except Exception as e:
            logger.warning(f"[ArtifactSpill] DB mirror 失败: {e}")

    created_at = datetime.now(timezone.utc).isoformat()
    metadata = ArtifactMetadata(
        ref_id=ref_id,
        tool_call_id=call_id,
        tool_name=tool_name,
        virtual_path=virtual,
        storage_path=str(path),
        size_bytes=size,
        summary=summary,
        conversation_id=conversation_id,
        created_at=created_at,
    )
    try:
        await put_artifact_metadata(
            store,
            user_id=user_id,
            session_id=session_id,
            tool_call_id=call_id,
            metadata=metadata,
        )
    except Exception as e:
        logger.warning(f"[ArtifactSpill] Store metadata 失败: {e}")

    try:
        await refresh_workspace_usage(user_id)
    except Exception as e:
        logger.debug(f"[ArtifactSpill] quota refresh 跳过: {e}")

    content = format_spill_content(summary=summary, virtual_path=virtual, ref_id=ref_id)
    return SpillResult(
        content=content,
        summary=summary,
        truncated=True,
        spilled=True,
        ref_id=ref_id,
        virtual_path=virtual,
        tool_call_id=call_id,
    )
