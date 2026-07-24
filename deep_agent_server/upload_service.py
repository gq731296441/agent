"""用户文件上传服务：上传到会话级或用户级 uploads/，含配额检查 + 分片上传。"""

import shutil
from pathlib import Path
from uuid import uuid4

from config.config import app_config
from deep_agent_server.quota_service import (
    check_additional_bytes,
    refresh_workspace_usage,
)
from deep_agent_server.workspace import (
    get_session_uploads_dir,
    get_uploads_dir,
    get_user_root,
    validate_user_id,
)


class UploadError(Exception):
    """上传异常。"""
    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.code = code


def validate_upload_size(size_bytes: int) -> None:
    """校验上传文件大小不超过限制。"""
    max_bytes = app_config.FILE_UPLOAD_MAX_BYTES
    if size_bytes > max_bytes:
        raise UploadError(
            f"文件超过大小限制（{max_bytes // 1024 // 1024}MB）", 413
        )
    if size_bytes <= 0:
        raise UploadError("文件不能为空", 400)


def sanitize_filename(filename: str) -> str:
    """安全化文件名：去除路径组件、拒绝危险字符。"""
    safe = Path(filename).name
    if not safe or safe.startswith("."):
        raise UploadError("非法文件名", 400)
    if ".." in safe or "/" in safe or "\\" in safe:
        raise UploadError("文件名包含非法字符", 400)
    return safe


async def save_upload_file(
    user_id: str,
    file_data: bytes,
    filename: str,
    *,
    session_id: str = "",
) -> dict:
    """上传文件到用户或会话 uploads 目录。

    若传了 session_id，文件保存到会话工作区 uploads/ 子目录：
        {user_id}/workspace/{session_id}/uploads/{filename}
    否则保存到用户级 uploads/ 目录（向后兼容）：
        {user_id}/uploads/{filename}

    Returns:
        {"path": "workspace/{sid}/uploads/filename" 或 "uploads/filename",
         "size_bytes": int, "filename": str}
    """
    uid = validate_user_id(user_id)
    safe_name = sanitize_filename(filename)

    validate_upload_size(len(file_data))

    err = check_additional_bytes(uid, len(file_data))
    if err:
        raise UploadError(err, 413)

    if session_id and session_id.strip():
        target_dir = get_session_uploads_dir(uid, str(session_id))
        path_prefix = f"workspace/{session_id}/uploads"
    else:
        target_dir = get_uploads_dir(uid)
        path_prefix = "uploads"

    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / safe_name
    if dest.exists():
        stem = dest.stem
        suffix = dest.suffix
        counter = 1
        while dest.exists():
            dest = target_dir / f"{stem}_{counter}{suffix}"
            counter += 1
    dest.write_bytes(file_data)

    await refresh_workspace_usage(uid)

    return {
        "path": f"{path_prefix}/{dest.name}",
        "size_bytes": len(file_data),
        "filename": dest.name,
    }


# ── 分片上传 ──────────────────────────────────────────────────────


def _chunks_dir(user_id: str) -> Path:
    """分片上传临时目录。"""
    return get_user_root(user_id) / ".chunks"


def _get_chunk_temp_dir(user_id: str, upload_id: str) -> Path:
    return _chunks_dir(user_id) / upload_id


async def start_chunked_upload(
    user_id: str,
    filename: str,
    total_size: int,
    total_chunks: int,
    *,
    session_id: str = "",
) -> dict:
    """初始化分片上传，返回 upload_id。

    校验总大小不超过配额上限，创建临时目录存放分片。
    """
    uid = validate_user_id(user_id)
    safe_name = sanitize_filename(filename)
    validate_upload_size(total_size)

    # 分片数合理性校验
    max_chunks = 1000
    if total_chunks < 1 or total_chunks > max_chunks:
        raise UploadError(f"分片数需在 1~{max_chunks} 之间", 400)

    err = check_additional_bytes(uid, total_size)
    if err:
        raise UploadError(err, 413)

    upload_id = str(uuid4())
    temp_dir = _get_chunk_temp_dir(uid, upload_id)
    temp_dir.mkdir(parents=True, exist_ok=True)

    # 写入元数据文件
    import json
    meta = {
        "filename": safe_name,
        "total_size": total_size,
        "total_chunks": total_chunks,
        "session_id": session_id or "",
    }
    (temp_dir / ".meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    return {
        "upload_id": upload_id,
        "chunk_size": app_config.FILE_UPLOAD_CHUNK_SIZE,
        "total_chunks": total_chunks,
    }


def _read_meta(temp_dir: Path) -> dict | None:
    """读取分片上传元数据。"""
    import json
    meta_path = temp_dir / ".meta.json"
    if not meta_path.is_file():
        return None
    return json.loads(meta_path.read_text(encoding="utf-8"))


async def upload_chunk(
    user_id: str,
    upload_id: str,
    chunk_index: int,
    chunk_data: bytes,
) -> dict:
    """上传单个分片。"""
    uid = validate_user_id(user_id)
    temp_dir = _get_chunk_temp_dir(uid, upload_id)
    if not temp_dir.is_dir():
        raise UploadError("上传会话不存在或已过期，请重新开始上传", 404)

    meta = _read_meta(temp_dir)
    if not meta:
        raise UploadError("上传会话元数据丢失", 500)

    total_chunks = meta["total_chunks"]
    if chunk_index < 0 or chunk_index >= total_chunks:
        raise UploadError(f"分片索引 {chunk_index} 超出范围 0~{total_chunks - 1}", 400)

    max_chunk = app_config.FILE_UPLOAD_CHUNK_SIZE
    if len(chunk_data) > max_chunk:
        raise UploadError(f"单个分片超过大小限制（{max_chunk // 1024 // 1024}MB）", 413)

    chunk_file = temp_dir / f"chunk_{chunk_index:06d}"
    chunk_file.write_bytes(chunk_data)

    # 统计已上传分片数
    existing = sorted(temp_dir.glob("chunk_*"))
    uploaded = len(existing)

    done = uploaded >= total_chunks
    return {
        "upload_id": upload_id,
        "chunk_index": chunk_index,
        "uploaded_chunks": uploaded,
        "total_chunks": total_chunks,
        "done": done,
    }


async def complete_chunked_upload(user_id: str, upload_id: str) -> dict:
    """合并所有分片到最终文件，返回与 save_upload_file 相同格式的结果。"""
    uid = validate_user_id(user_id)
    temp_dir = _get_chunk_temp_dir(uid, upload_id)
    if not temp_dir.is_dir():
        raise UploadError("上传会话不存在或已过期", 404)

    meta = _read_meta(temp_dir)
    if not meta:
        raise UploadError("上传会话元数据丢失", 500)

    total_chunks = meta["total_chunks"]
    safe_name = meta["filename"]
    session_id = meta.get("session_id", "")

    # 校验分片完整性
    for i in range(total_chunks):
        chunk_file = temp_dir / f"chunk_{i:06d}"
        if not chunk_file.is_file():
            raise UploadError(f"缺少分片 {i}，请重新上传", 400)

    # 合并分片
    if session_id and session_id.strip():
        target_dir = get_session_uploads_dir(uid, str(session_id))
        path_prefix = f"workspace/{session_id}/uploads"
    else:
        target_dir = get_uploads_dir(uid)
        path_prefix = "uploads"

    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / safe_name
    if dest.exists():
        stem = dest.stem
        suffix = dest.suffix
        counter = 1
        while dest.exists():
            dest = target_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    with open(dest, "wb") as out:
        for i in range(total_chunks):
            chunk_file = temp_dir / f"chunk_{i:06d}"
            out.write(chunk_file.read_bytes())

    actual_size = dest.stat().st_size
    if actual_size != meta["total_size"]:
        dest.unlink(missing_ok=True)
        raise UploadError(
            f"文件大小不匹配: 期望 {meta['total_size']} 字节，实际 {actual_size} 字节", 500
        )

    # 清理临时目录
    shutil.rmtree(temp_dir, ignore_errors=True)

    await refresh_workspace_usage(uid)

    return {
        "path": f"{path_prefix}/{dest.name}",
        "size_bytes": actual_size,
        "filename": dest.name,
    }


async def abort_chunked_upload(user_id: str, upload_id: str) -> None:
    """取消分片上传，清理临时文件。"""
    uid = validate_user_id(user_id)
    temp_dir = _get_chunk_temp_dir(uid, upload_id)
    if temp_dir.is_dir():
        shutil.rmtree(temp_dir, ignore_errors=True)
