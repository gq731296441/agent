"""Artifact 存储门面（spill + legacy + 读取）。"""

from pathlib import Path
from typing import Any, Optional

from db import deep_storage
from deep_agent_server.artifact.legacy import store_text_artifact
from deep_agent_server.artifact.spill import parse_spill_content, spill_tool_result
from deep_agent_server.artifact.summary import truncate_summary

__all__ = [
    "truncate_summary",
    "store_text_artifact",
    "read_artifact_content",
    "spill_tool_result",
    "parse_spill_content",
]


async def read_artifact_content(
    ref_id: str,
    user_id: str,
    *,
    store: Any = None,
    session_id: Optional[str] = None,
) -> Optional[str]:
    rec = await deep_storage.get_artifact(ref_id, user_id)
    if rec:
        path = Path(rec["storage_path"])
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace")

    if store:
        meta = await get_artifact_by_ref_id(
            store, user_id=user_id, ref_id=ref_id, session_id=session_id
        )
        if meta and meta.storage_path:
            path = Path(meta.storage_path)
            if path.exists():
                return path.read_text(encoding="utf-8", errors="replace")
    return None
