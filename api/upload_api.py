"""用户文件上传 API：上传到会话级或用户级 uploads/。"""

from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from common.schemas import ApiResponse
from deep_agent_server.upload_service import UploadError, save_upload_file

router = APIRouter(prefix="/workspace", tags=["workspace"])


@router.post("/upload", response_model=ApiResponse)
async def upload_file(
    user_id: str = Query(...),
    file: UploadFile = File(...),
    session_id: Optional[str] = Query(None, description="会话ID，传入时保存到会话工作区 uploads/"),
):
    """上传文件到用户 uploads 目录。

    - 传入 session_id：文件保存到 workspace/{session_id}/uploads/{filename}（会话级）
    - 不传 session_id：文件保存到 uploads/{filename}（用户级，向后兼容）

    上传计入个人配额。
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
