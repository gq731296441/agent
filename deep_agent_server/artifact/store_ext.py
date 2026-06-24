"""Artifact Store namespace 读写。"""

from typing import Any, List, Optional

from deep_agent_server.artifact.models import ArtifactMetadata
from deep_agent_server.store.client import store_get, store_put, store_search
from deep_agent_server.store.namespaces import artifact_namespace


async def put_artifact_metadata(
    store: Any,
    *,
    user_id: str,
    session_id: str,
    tool_call_id: str,
    metadata: ArtifactMetadata,
) -> None:
    if not store:
        return
    ns = artifact_namespace(user_id, session_id)
    await store_put(
        store,
        ns,
        tool_call_id,
        metadata.to_store_value(),
        user_id=user_id,
        index=False,
    )


async def get_artifact_metadata(
    store: Any,
    *,
    user_id: str,
    session_id: str,
    tool_call_id: str,
) -> Optional[ArtifactMetadata]:
    if not store:
        return None
    item = await store_get(
        store,
        artifact_namespace(user_id, session_id),
        tool_call_id,
        user_id=user_id,
    )
    if not item:
        return None
    value = item.value if hasattr(item, "value") else item
    if not isinstance(value, dict):
        return None
    return ArtifactMetadata(**{k: value.get(k) for k in ArtifactMetadata.__dataclass_fields__})


async def get_artifact_by_ref_id(
    store: Any,
    *,
    user_id: str,
    ref_id: str,
    session_id: Optional[str] = None,
) -> Optional[ArtifactMetadata]:
    if not store:
        return None
    if session_id:
        ns = artifact_namespace(user_id, session_id)
        items = await store_search(store, ns, user_id=user_id, limit=200)
        for item in items or []:
            val = getattr(item, "value", None) or {}
            if isinstance(val, dict) and val.get("ref_id") == ref_id:
                return ArtifactMetadata(**{k: val.get(k) for k in ArtifactMetadata.__dataclass_fields__})
    return None


async def list_artifact_metadata(
    store: Any,
    *,
    user_id: str,
    session_id: str,
    limit: int = 100,
) -> List[ArtifactMetadata]:
    if not store:
        return []
    items = await store_search(
        store,
        artifact_namespace(user_id, session_id),
        user_id=user_id,
        limit=limit,
    )
    out: List[ArtifactMetadata] = []
    for item in items or []:
        val = getattr(item, "value", None) or {}
        if isinstance(val, dict) and val.get("ref_id"):
            out.append(
                ArtifactMetadata(**{k: val.get(k) for k in ArtifactMetadata.__dataclass_fields__})
            )
    return out
