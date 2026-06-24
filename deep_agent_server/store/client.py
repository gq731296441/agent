"""Store 读写薄封装（user_id 校验）。"""

from typing import Any, Optional

from deep_agent_server.workspace import validate_user_id


def assert_user_namespace(user_id: str, namespace: tuple[str, ...]) -> None:
    validate_user_id(user_id)
    if not namespace or namespace[0] != user_id:
        raise ValueError(f"namespace 首段必须与 user_id 一致: {user_id}")


async def store_put(
    store: Any,
    namespace: tuple[str, ...],
    key: str,
    value: dict,
    *,
    user_id: str,
    index: Optional[bool] = None,
    ttl: Optional[int] = None,
) -> None:
    assert_user_namespace(user_id, namespace)
    kwargs = {}
    if index is not None:
        kwargs["index"] = index
    if ttl is not None:
        kwargs["ttl"] = ttl
    await store.aput(namespace, str(key), value, **kwargs)


async def store_get(store: Any, namespace: tuple[str, ...], key: str, *, user_id: str):
    assert_user_namespace(user_id, namespace)
    return await store.aget(namespace, str(key))


async def store_delete(store: Any, namespace: tuple[str, ...], key: str, *, user_id: str) -> None:
    assert_user_namespace(user_id, namespace)
    await store.adelete(namespace, str(key))


async def store_search(
    store: Any,
    namespace_prefix: tuple[str, ...],
    *,
    user_id: str,
    query: Optional[str] = None,
    limit: int = 10,
    filter: Optional[dict] = None,
):
    assert_user_namespace(user_id, namespace_prefix)
    return await store.asearch(
        namespace_prefix,
        query=query,
        limit=limit,
        filter=filter,
    )
