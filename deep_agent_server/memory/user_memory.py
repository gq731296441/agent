"""User Memory 业务服务（DB + Store 双写）。"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config import get_logger
from config.config import app_config
from db import memory_storage
from deep_agent_server.memory.store_memory import (
    delete_user_memory_store,
    memory_store_value,
    put_user_memory_store,
    search_user_memory_store,
)

logger = get_logger(__name__)


class UserMemoryService:
    def __init__(self, store: Any = None):
        self.store = store

    async def create(
        self,
        user_id: str,
        text: str,
        *,
        tags: Optional[List[str]] = None,
        source: str = "explicit",
    ) -> Dict[str, Any]:
        row = await memory_storage.create_user_memory(
            user_id, text, tags=tags, source=source
        )
        await self._sync_store(user_id, row)
        return row

    async def get(self, memory_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        return await memory_storage.get_user_memory(memory_id, user_id)

    async def list(
        self,
        user_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        return await memory_storage.list_user_memories(user_id, limit=limit, offset=offset)

    async def update(
        self,
        memory_id: str,
        user_id: str,
        *,
        text: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        row = await memory_storage.update_user_memory(
            memory_id, user_id, text=text, tags=tags
        )
        if row:
            await self._sync_store(user_id, row)
        return row

    async def delete(self, memory_id: str, user_id: str) -> bool:
        ok = await memory_storage.delete_user_memory(memory_id, user_id)
        if ok:
            try:
                await delete_user_memory_store(
                    self.store, user_id=user_id, memory_id=memory_id
                )
            except Exception as e:
                logger.warning(f"[UserMemory] Store 删除失败 id={memory_id}: {e}")
        return ok

    async def delete_all(self, user_id: str) -> int:
        rows = await memory_storage.list_user_memories(user_id, limit=1000)
        count = await memory_storage.delete_all_user_memories(user_id)
        for row in rows:
            try:
                await delete_user_memory_store(
                    self.store, user_id=user_id, memory_id=row["id"]
                )
            except Exception:
                pass
        return count

    async def search(
        self,
        user_id: str,
        *,
        query: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        lim = limit or app_config.USER_MEMORY_INJECT_LIMIT
        has_index = bool(getattr(self.store, "index_config", None))
        if self.store and query and has_index:
            try:
                items = await search_user_memory_store(
                    self.store, user_id=user_id, query=query, limit=lim
                )
                if items:
                    return [self._item_to_dict(it) for it in items]
            except Exception as e:
                logger.warning(f"[UserMemory] semantic search 失败，回退 DB: {e}")

        rows = await memory_storage.list_user_memories(user_id, limit=lim)
        if query:
            q = query.lower()
            rows = [r for r in rows if q in (r.get("text") or "").lower()]
        return rows[:lim]

    async def _sync_store(self, user_id: str, row: Dict[str, Any]) -> None:
        if not self.store:
            return
        now = datetime.now(timezone.utc).isoformat()
        value = memory_store_value(
            row["id"],
            row["text"],
            tags=row.get("tags"),
            source=row.get("source") or "explicit",
            created_at=row.get("created_at") or now,
            updated_at=row.get("updated_at") or now,
        )
        try:
            await put_user_memory_store(
                self.store,
                user_id=user_id,
                memory_id=row["id"],
                value=value,
            )
        except Exception as e:
            logger.warning(f"[UserMemory] Store 同步失败 id={row['id']}: {e}")

    @staticmethod
    def _item_to_dict(item: Any) -> Dict[str, Any]:
        val = getattr(item, "value", None) or {}
        if not isinstance(val, dict):
            return {"text": str(val)}
        return {
            "id": val.get("memory_id") or getattr(item, "key", ""),
            "text": val.get("text") or "",
            "tags": val.get("tags") or [],
            "source": val.get("source") or "explicit",
        }


_service: Optional[UserMemoryService] = None


def get_user_memory_service(store: Any = None) -> UserMemoryService:
    global _service
    if store is not None:
        return UserMemoryService(store=store)
    if _service is None:
        _service = UserMemoryService(store=None)
    return _service
