"""用户文件上传 API：普通上传 + 分片上传。"""

from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from common.schemas import ApiResponse
from deep_agent_server.upload_service import (
    UploadError,
    abort_chunked_upload,
    complete_chunked_upload,
    save_upload_file,
    start_chunked_upload,
    upload_chunk,
)

router = APIRouter(prefix="/workspace", tags=["workspace"])


# ── 普通上传（≤10MB）─────────────────────────────────────────────


@router.post("/upload", response_model=ApiResponse)
async def upload_file(
    user_id: str = Query(...),
    file: UploadFile = File(...),
    session_id: Optional[str] = Query(None, description="会话ID，传入时保存到会话工作区 uploads/"),
):
    """上传文件（单次请求，最大 10MB）。

    - 传入 session_id：文件保存到 workspace/{session_id}/uploads/{filename}
    - 不传 session_id：保存到 uploads/{filename}（用户级）
    """
    if not file.filename:
        return ApiResponse.error(msg="文件名为空", code=400)

    try:
        file_data = await file.read()
        result = await save_upload_file(
            user_id, file_data, file.filename,
            session_id=session_id or "",
        )
        return ApiResponse.success(data=result, msg="上传成功")
    except UploadError as e:
        raise HTTPException(status_code=e.code, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上传失败: {e}") from e


# ── 分片上传（大文件）─────────────────────────────────────────────


@router.post("/upload/chunked/start", response_model=ApiResponse)
async def start_chunked(
    user_id: str = Query(...),
    filename: str = Form(...),
    total_size: int = Form(..., description="文件总字节数"),
    total_chunks: int = Form(..., description="分片总数"),
    session_id: Optional[str] = Query(None),
):
    """初始化分片上传，返回 upload_id。

    前端计算分片：total_chunks = ceil(total_size / CHUNK_SIZE)（CHUNK_SIZE=1MB）
    """
    try:
        result = await start_chunked_upload(
            user_id, filename, total_size, total_chunks,
            session_id=session_id or "",
        )
        return ApiResponse.success(data=result, msg="分片上传已初始化")
    except UploadError as e:
        raise HTTPException(status_code=e.code, detail=str(e)) from e


@router.post("/upload/chunked/{upload_id}", response_model=ApiResponse)
async def upload_chunk_part(
    upload_id: str,
    user_id: str = Query(...),
    chunk_index: int = Query(..., description="分片序号，从 0 开始"),
    chunk: UploadFile = File(...),
):
    """上传单个分片。逐个上传，完成后手动调用 /complete 合并。"""
    try:
        chunk_data = await chunk.read()
        result = await upload_chunk(user_id, upload_id, chunk_index, chunk_data)
        return ApiResponse.success(data=result, msg=f"分片 {chunk_index} 已上传")
    except UploadError as e:
        raise HTTPException(status_code=e.code, detail=str(e)) from e


@router.post("/upload/chunked/{upload_id}/complete", response_model=ApiResponse)
async def complete_chunked(
    upload_id: str,
    user_id: str = Query(...),
):
    """合并所有分片为最终文件，返回文件路径。"""
    try:
        result = await complete_chunked_upload(user_id, upload_id)
        return ApiResponse.success(data=result, msg="上传完成")
    except UploadError as e:
        raise HTTPException(status_code=e.code, detail=str(e)) from e


@router.delete("/upload/chunked/{upload_id}", response_model=ApiResponse)
async def abort_chunked(
    upload_id: str,
    user_id: str = Query(...),
):
    """取消分片上传，清理临时文件。"""
    try:
        await abort_chunked_upload(user_id, upload_id)
        return ApiResponse.success(msg="已取消")
    except UploadError as e:
        raise HTTPException(status_code=e.code, detail=str(e)) from e
