"""User Memory REST API。"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from deep_agent_server.memory import get_user_memory_service

router = APIRouter(prefix="/memory", tags=["memory"])


class MemoryCreateBody(BaseModel):
    user_id: str
    text: str = Field(..., min_length=1)
    tags: Optional[List[str]] = None
    source: str = "api"


class MemoryUpdateBody(BaseModel):
    user_id: str
    text: Optional[str] = None
    tags: Optional[List[str]] = None


def _get_store(request: Request):
    return getattr(request.app.state, "kv_store", None)


@router.get("")
async def list_memories(
    request: Request,
    user_id: str = Query(..., description="用户ID"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    svc = get_user_memory_service(_get_store(request))
    rows = await svc.list(user_id, limit=limit, offset=offset)
    return {"items": rows, "limit": limit, "offset": offset}


@router.get("/search")
async def search_memories(
    request: Request,
    user_id: str = Query(...),
    q: str = Query(..., min_length=1),
    limit: int = Query(5, ge=1, le=20),
):
    svc = get_user_memory_service(_get_store(request))
    rows = await svc.search(user_id, query=q, limit=limit)
    return {"items": rows, "query": q}


@router.post("")
async def create_memory(request: Request, body: MemoryCreateBody):
    svc = get_user_memory_service(_get_store(request))
    row = await svc.create(
        body.user_id,
        body.text,
        tags=body.tags,
        source=body.source,
    )
    return row


@router.put("/{memory_id}")
async def update_memory(memory_id: str, request: Request, body: MemoryUpdateBody):
    svc = get_user_memory_service(_get_store(request))
    row = await svc.update(
        memory_id,
        body.user_id,
        text=body.text,
        tags=body.tags,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return row


@router.delete("/{memory_id}")
async def delete_memory(
    memory_id: str,
    request: Request,
    user_id: str = Query(...),
):
    svc = get_user_memory_service(_get_store(request))
    ok = await svc.delete(memory_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Not found")
    return {"deleted": True, "id": memory_id}


@router.delete("")
async def delete_all_memories(
    request: Request,
    user_id: str = Query(...),
):
    svc = get_user_memory_service(_get_store(request))
    count = await svc.delete_all(user_id)
    return {"deleted_count": count}
