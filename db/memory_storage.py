"""用户长期记忆（user_memories 表）。"""

from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from db.database import db_manager


def _uuid(val) -> UUID:
    if isinstance(val, UUID):
        return val
    return UUID(str(val))


async def create_user_memory(
    user_id: str,
    text: str,
    *,
    tags: Optional[List[str]] = None,
    source: str = "explicit",
    memory_id: Optional[str] = None,
) -> Dict[str, Any]:
    mid = _uuid(memory_id or uuid4())
    tag_list = tags or []
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO user_memories (id, user_id, text, tags, source)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, user_id, text, tags, source, created_at, updated_at
            """,
            mid,
            user_id,
            text,
            tag_list,
            source,
        )
    return _row_to_dict(row)


async def get_user_memory(memory_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, user_id, text, tags, source, created_at, updated_at
            FROM user_memories
            WHERE id = $1 AND user_id = $2
            """,
            _uuid(memory_id),
            user_id,
        )
    return _row_to_dict(row) if row else None


async def list_user_memories(
    user_id: str,
    *,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, user_id, text, tags, source, created_at, updated_at
            FROM user_memories
            WHERE user_id = $1
            ORDER BY updated_at DESC
            LIMIT $2 OFFSET $3
            """,
            user_id,
            limit,
            offset,
        )
    return [_row_to_dict(r) for r in rows]


async def update_user_memory(
    memory_id: str,
    user_id: str,
    *,
    text: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE user_memories SET
                text = COALESCE($3, text),
                tags = COALESCE($4, tags),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = $1 AND user_id = $2
            RETURNING id, user_id, text, tags, source, created_at, updated_at
            """,
            _uuid(memory_id),
            user_id,
            text,
            tags,
        )
    return _row_to_dict(row) if row else None


async def delete_user_memory(memory_id: str, user_id: str) -> bool:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM user_memories WHERE id = $1 AND user_id = $2",
            _uuid(memory_id),
            user_id,
        )
    return result.endswith("1")


async def delete_all_user_memories(user_id: str) -> int:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM user_memories WHERE user_id = $1",
            user_id,
        )
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError):
        return 0


def _row_to_dict(row) -> Dict[str, Any]:
    d = dict(row)
    d["id"] = str(d["id"])
    d["tags"] = list(d.get("tags") or [])
    if d.get("created_at"):
        d["created_at"] = d["created_at"].isoformat()
    if d.get("updated_at"):
        d["updated_at"] = d["updated_at"].isoformat()
    return d
