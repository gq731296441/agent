"""User Memory Store namespace 封装。"""

from typing import Any, Dict, List, Optional

from deep_agent_server.store.client import store_delete, store_put, store_search
from deep_agent_server.store.namespaces import user_memory_namespace


def memory_store_value(
    memory_id: str,
    text: str,
    *,
    tags: Optional[List[str]] = None,
    source: str = "explicit",
    created_at: Optional[str] = None,
    updated_at: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "memory_id": memory_id,
        "text": text,
        "tags": tags or [],
        "source": source,
        "created_at": created_at,
        "updated_at": updated_at,
    }


async def put_user_memory_store(
    store: Any,
    *,
    user_id: str,
    memory_id: str,
    value: Dict[str, Any],
) -> None:
    if not store:
        return
    await store_put(
        store,
        user_memory_namespace(user_id),
        memory_id,
        value,
        user_id=user_id,
        index=True,
    )


async def delete_user_memory_store(
    store: Any,
    *,
    user_id: str,
    memory_id: str,
) -> None:
    if not store:
        return
    await store_delete(
        store,
        user_memory_namespace(user_id),
        memory_id,
        user_id=user_id,
    )


async def search_user_memory_store(
    store: Any,
    *,
    user_id: str,
    query: Optional[str] = None,
    limit: int = 5,
) -> List[Any]:
    if not store:
        return []
    return await store_search(
        store,
        user_memory_namespace(user_id),
        user_id=user_id,
        query=query,
        limit=limit,
    )
