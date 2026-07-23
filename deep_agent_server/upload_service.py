"""用户文件上传服务：上传到会话级或用户级 uploads/，含配额检查。"""

from pathlib import Path

from config.config import app_config
from deep_agent_server.quota_service import (
    check_additional_bytes,
    refresh_workspace_usage,
)
from deep_agent_server.workspace import (
    get_session_uploads_dir,
    get_uploads_dir,
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
