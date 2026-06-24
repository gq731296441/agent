"""Store 与 workspace 清理。"""

import shutil
from pathlib import Path
from typing import Any, Optional

from config import get_logger
from config.config import app_config
from deep_agent_server.store.namespaces import artifact_namespace, filesystem_namespace
from deep_agent_server.workspace import get_workspace_dir

logger = get_logger(__name__)


async def _delete_namespace_keys(store: Any, namespace: tuple[str, ...]) -> int:
    """删除 namespace 下全部条目（asearch 无 query 列举）。"""
    deleted = 0
    try:
        items = await store.asearch(namespace, limit=1000)
    except Exception as e:
        logger.warning(f"[StoreCleanup] asearch 失败 ns={namespace}: {e}")
        return 0

    for item in items or []:
        key = getattr(item, "key", None) or (item.get("key") if isinstance(item, dict) else None)
        ns = getattr(item, "namespace", None) or namespace
        if not key:
            continue
        try:
            await store.adelete(tuple(ns) if isinstance(ns, list) else ns, str(key))
            deleted += 1
        except Exception as e:
            logger.debug(f"[StoreCleanup] adelete 跳过 key={key}: {e}")
    return deleted


def _remove_tool_cache_dir(user_id: str, session_id: str) -> None:
    spill_dir = app_config.TOOL_SPILL_VIRTUAL_DIR.strip("/\\") or ".tool-cache"
    cache_path = get_workspace_dir(user_id, session_id) / spill_dir
    if cache_path.exists() and cache_path.is_dir():
        shutil.rmtree(cache_path, ignore_errors=True)
        logger.info(f"[StoreCleanup] 已删除 .tool-cache: {cache_path}")


async def cleanup_session_store(
    store: Any,
    *,
    thread_id: str,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> None:
    """清理 checkpointer thread 关联的 Store 条目与 session spill 目录。"""
    if not store:
        return

    from deep_agent_server.store.namespaces import session_store_namespaces

    if user_id and session_id:
        for ns, key in session_store_namespaces(user_id, session_id, thread_id):
            try:
                await store.adelete(ns, key)
            except Exception:
                pass
        try:
            art_deleted = await _delete_namespace_keys(
                store, artifact_namespace(user_id, session_id)
            )
            fs_deleted = await _delete_namespace_keys(
                store, filesystem_namespace(user_id, session_id)
            )
            if art_deleted or fs_deleted:
                logger.info(
                    f"[StoreCleanup] artifact={art_deleted} filesystem={fs_deleted} "
                    f"user={user_id} session={session_id}"
                )
        except Exception as e:
            logger.warning(f"[StoreCleanup] artifact ns 清理失败: {e}")
        _remove_tool_cache_dir(user_id, session_id)
    else:
        for ns in (
            ("compaction_state",),
            ("microcompact_state",),
        ):
            try:
                await store.adelete(ns, thread_id)
            except Exception:
                pass

    logger.info(f"[StoreCleanup] 已清理 store thread_id={thread_id}")
