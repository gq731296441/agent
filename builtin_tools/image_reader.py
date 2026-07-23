"""内置工具：图片阅读（通过 nebulacoder-vl-v7.0 vision API）。

用于读取用户上传的图片文件（.jpg/.png/.gif 等），
将图片以 base64 编码发送给 nebulacoder vision 模型进行理解和描述。

API: http://nebulacoder.dev.zte.com.cn:40081/v1/chat/completions
Model: nebulacoder-vl-v7.0
Auth: Bearer {工号}

参考 online_search.py 的工厂模式。
"""

import base64
from pathlib import Path

import requests
from langchain.tools import tool
from pydantic import BaseModel, Field

from config import get_logger
from deep_agent_server.workspace import get_uploads_dir, get_workspace_dir

logger = get_logger(__name__)

# Vision API 配置
_IMAGE_READER_BASE_URL = "http://nebulacoder.dev.zte.com.cn:40081/v1/chat/completions"
_IMAGE_READER_MODEL = "nebulacoder-vl-v7.0"

# 支持的图片格式
_SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}


class _ReadImageInput(BaseModel):
    file_path: str = Field(
        description=(
            "要读取的图片文件路径（虚拟路径），必须以 / 开头。"
            "例如：'/workspace/abc123/uploads/screenshot.png' 或 '/uploads/photo.jpg'。"
        ),
    )
    question: str = Field(
        default="请详细描述这张图片的内容",
        description="对图片的提问，例如：'这张图片里有什么'、'请提取图片中的文字'。",
    )


def _resolve_physical_path(user_id: str, virtual_path: str, session_id: str = "") -> Path:
    """将 Agent 虚拟路径解析为磁盘绝对路径。

    与 CompositeBackend 路由对齐：
      /workspace/{rel}  →  {user_id}/workspace/{session_id}/{rel}
      /uploads/{rel}    →  {user_id}/uploads/{rel}
    """
    vpath = virtual_path.lstrip("/").replace("\\", "/")
    if not vpath:
        raise ValueError("文件路径为空")
    if ".." in vpath.split("/"):
        raise ValueError("非法路径")

    if vpath.startswith("workspace/"):
        rel = vpath[len("workspace/"):]
        if session_id:
            return get_workspace_dir(user_id, session_id) / rel
        raise ValueError("无法解析 workspace 路径：缺少 session_id")
    if vpath.startswith("uploads/"):
        rel = vpath[len("uploads/"):]
        return get_uploads_dir(user_id) / rel

    raise ValueError(f"不支持的路径前缀: {virtual_path}")


def _get_mime_type(suffix: str) -> str:
    mapping = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
    }
    return mapping.get(suffix.lower(), "image/jpeg")


def create_read_image(user_id: str, user_token: str = "", session_id: str = ""):
    """工厂函数：创建绑定了用户身份和会话上下文的 read_image 工具。"""
    _current_user_id = user_id
    _current_session_id = session_id

    @tool("read_image", args_schema=_ReadImageInput)
    def read_image(file_path: str, question: str = "请详细描述这张图片的内容") -> str:
        """读取图片文件并返回图片内容的文字描述。

        使用 vision 模型来理解和描述图片内容。适用于：
        - 查看用户上传的截图、照片、图表
        - 提取图片中的文字（OCR）
        - 理解流程图、架构图、UI 设计图等

        注意：
        - 仅支持 .jpg/.jpeg/.png/.gif/.bmp/.webp 格式
        - 图片会以 base64 编码发送，单张不宜过大（建议 < 10MB）
        - 这是一个内置工具，无需 MCP 配置，始终可用
        """
        if not file_path or not file_path.strip():
            return "错误：file_path 不能为空，请提供有效的图片路径"

        vpath = file_path.strip()
        logger.info(
            f"read_image 被调用 user_id={_current_user_id} path={vpath} question={question[:80]}"
        )

        # 解析路径
        try:
            disk_path = _resolve_physical_path(_current_user_id, vpath, _current_session_id)
        except ValueError as e:
            return f"图片路径无效: {e}"

        if not disk_path.is_file():
            return f"图片文件不存在: {vpath}"

        suffix = disk_path.suffix.lower()
        if suffix not in _SUPPORTED_EXTENSIONS:
            return (
                f"不支持的图片格式: {suffix}。"
                f"支持的格式: {', '.join(sorted(_SUPPORTED_EXTENSIONS))}"
            )

        # 检查文件大小
        try:
            file_size = disk_path.stat().st_size
            if file_size > 20 * 1024 * 1024:  # 20MB
                return f"图片过大（{file_size / 1024 / 1024:.1f}MB），请压缩后重试（上限 20MB）"
        except OSError as e:
            return f"无法读取图片: {e}"

        # 读取并编码
        try:
            with open(disk_path, "rb") as f:
                base64_image = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            return f"读取图片文件失败: {e}"

        mime = _get_mime_type(suffix)
        data_url = f"data:{mime};base64,{base64_image}"

        # 调用 Vision API
        headers = {
            "Authorization": f"Bearer {_current_user_id}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": _IMAGE_READER_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": question},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "stream": False,
        }

        try:
            response = requests.post(
                _IMAGE_READER_BASE_URL,
                headers=headers,
                json=payload,
                timeout=120,
            )
            if response.status_code != 200:
                logger.error(
                    f"read_image API 错误 user_id={_current_user_id} "
                    f"status={response.status_code} path={vpath}"
                )
                return (
                    f"图片阅读失败: API 返回 {response.status_code}。"
                    f"请稍后重试或使用其他方式查看图片。"
                )

            result = response.json()
            content = (
                result.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            if not content:
                return "图片阅读完成，但未返回有效内容。请尝试换一种提问方式或简化问题。"

            logger.info(
                f"read_image 成功 user_id={_current_user_id} "
                f"path={vpath} response_len={len(content)}"
            )
            return content

        except requests.Timeout:
            return "图片阅读超时（120秒），请重试或使用更小的图片。"
        except Exception as e:
            logger.error(
                f"read_image 调用失败 user_id={_current_user_id} path={vpath} error={e}"
            )
            return f"图片阅读失败: {e}"

    return read_image
