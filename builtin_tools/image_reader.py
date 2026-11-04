"""内置工具：图片阅读（通过 Qwen3-VL 多模态 API）。

用于读取用户上传的图片文件（.jpg/.png/.gif 等），
将图片以 base64 编码发送给多模态模型进行理解和描述。

API: https://maas-apigateway.dt.zte.com.cn/model/qwen3-vl-32b-instruct/v1/chat/completions
Model: Qwen3-VL-32B-Instruct
Auth: Bearer <VISION_API_KEY>（在 .env 中配置）

依赖: deep_agent_server.vision_service.analyze_image()
"""

from pathlib import Path

from langchain.tools import tool
from pydantic import BaseModel, Field

from config import get_logger
from deep_agent_server.vision_service import SUPPORTED_IMAGE_EXTENSIONS, MAX_IMAGE_SIZE_BYTES
from deep_agent_server.workspace import get_uploads_dir, get_workspace_dir

logger = get_logger(__name__)


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


def create_read_image(user_id: str, user_token: str = "", session_id: str = ""):
    """工厂函数：创建绑定了用户身份和会话上下文的 read_image 工具。

    使用 Qwen3-VL-32B-Instruct 多模态 API，
    底层委托给 deep_agent_server.vision_service.analyze_image()。
    """
    _current_user_id = user_id
    _current_session_id = session_id

    @tool("read_image", args_schema=_ReadImageInput)
    async def read_image(
        file_path: str,
        question: str = "请详细描述这张图片的内容",
    ) -> str:
        """读取图片文件并返回图片内容的文字描述。

        使用 vision 模型来理解和描述图片内容。适用于：
        - 查看用户上传的截图、照片、图表
        - 提取图片中的文字（OCR）
        - 理解流程图、架构图、UI 设计图等

        注意：
        - 支持 .jpg/.jpeg/.png/.gif/.bmp/.webp/.tiff/.tif/.svg/.ico 格式
        - 图片会以 base64 编码发送，单张不宜过大（建议 < 10MB）
        - 这是一个内置工具，无需 MCP 配置，始终可用
        """
        if not file_path or not file_path.strip():
            return "错误：file_path 不能为空，请提供有效的图片路径"

        vpath = file_path.strip()
        logger.info(
            f"read_image 被调用 user_id={_current_user_id} "
            f"path={vpath} question={question[:80]}"
        )

        # 解析路径
        try:
            disk_path = _resolve_physical_path(
                _current_user_id, vpath, _current_session_id,
            )
        except ValueError as e:
            return f"图片路径无效: {e}"

        if not disk_path.is_file():
            return f"图片文件不存在: {vpath}"

        # 格式校验
        suffix = disk_path.suffix.lower()
        if suffix not in SUPPORTED_IMAGE_EXTENSIONS:
            return (
                f"不支持的图片格式: {suffix}。"
                f"支持的格式: {', '.join(sorted(SUPPORTED_IMAGE_EXTENSIONS))}"
            )

        # 大小校验
        try:
            file_size = disk_path.stat().st_size
            if file_size > MAX_IMAGE_SIZE_BYTES:
                return (
                    f"图片过大（{file_size / 1024 / 1024:.1f}MB），"
                    f"请压缩后重试（上限 20MB）"
                )
        except OSError as e:
            return f"无法读取图片: {e}"

        # 委托给共享 vision_service
        from deep_agent_server.vision_service import analyze_image

        return await analyze_image(
            disk_path,
            user_id=_current_user_id,
            question=question,
        )

    return read_image
