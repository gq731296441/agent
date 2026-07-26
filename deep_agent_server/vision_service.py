"""Vision API 服务 — nebulacoder-v8.0 多模态接口。

封装新 Vision API 的调用逻辑，被 file_parser.py（后端解析）和
builtin_tools/image_reader.py（Agent 工具）共用。

API:
  URL:     http://10.57.230.76:40082/v1/chat/completions
  Model:   nebulacoder-v8.0
  Auth:    纯工号（无 "Bearer " 前缀）
"""

import base64
from pathlib import Path

import httpx

from config import get_logger

logger = get_logger(__name__)

# 新 Vision API 配置
VISION_BASE_URL = "http://10.57.230.76:40082/v1/chat/completions"
VISION_MODEL = "nebulacoder-v8.0"
VISION_TIMEOUT = 120  # 秒

# 支持的图片格式
SUPPORTED_IMAGE_EXTENSIONS: set[str] = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp",
    ".webp", ".tiff", ".tif", ".svg", ".ico",
}

# 单张图片大小上限（20MB）
MAX_IMAGE_SIZE_BYTES = 20 * 1024 * 1024


def _get_mime_type(suffix: str) -> str:
    """根据扩展名返回 MIME 类型。"""
    mapping = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
        ".tiff": "image/tiff",
        ".tif": "image/tiff",
        ".svg": "image/svg+xml",
        ".ico": "image/x-icon",
    }
    return mapping.get(suffix.lower(), "image/jpeg")


async def analyze_image(
    image_path: Path,
    user_id: str,
    question: str = "请详细描述这张图片的内容",
    timeout: int = VISION_TIMEOUT,
) -> str:
    """使用新 Vision API 分析图片，返回文本描述。

    Args:
        image_path: 图片磁盘绝对路径
        user_id: 用户 8 位工号（用作 Authorization header）
        question: 对图片的提问
        timeout: 超时秒数

    Returns:
        描述文本。失败时返回错误字符串（而非抛异常），
        调用方（Agent 工具 / file_parser）可直接将返回值展示给 LLM。
    """
    # 1. 验证文件
    if not image_path.is_file():
        return f"错误：图片文件不存在 ({image_path.name})"

    suffix = image_path.suffix.lower()
    if suffix not in SUPPORTED_IMAGE_EXTENSIONS:
        return (
            f"不支持的图片格式: {suffix}。"
            f"支持的格式: {', '.join(sorted(SUPPORTED_IMAGE_EXTENSIONS))}"
        )

    # 2. 文件大小检查
    try:
        file_size = image_path.stat().st_size
        if file_size > MAX_IMAGE_SIZE_BYTES:
            return (
                f"图片过大（{file_size / 1024 / 1024:.1f}MB），"
                f"请压缩后重试（上限 20MB）"
            )
    except OSError as e:
        return f"无法读取图片信息: {e}"

    # 3. Base64 编码
    try:
        with open(image_path, "rb") as f:
            b64_data = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        return f"读取图片文件失败: {e}"

    mime = _get_mime_type(suffix)
    data_url = f"data:{mime};base64,{b64_data}"

    # 4. 调用 API
    headers = {
        "Authorization": str(user_id),  # 纯工号，无 "Bearer " 前缀
        "Content-Type": "application/json",
    }
    payload = {
        "model": VISION_MODEL,
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
        "temperature": 0.2,
        "think_mode": "normal",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(VISION_BASE_URL, headers=headers, json=payload)
            if response.status_code != 200:
                logger.error(
                    f"Vision API 错误 status={response.status_code} "
                    f"file={image_path.name}"
                )
                return (
                    f"图片解析失败: API 返回 {response.status_code}。"
                    f"请稍后重试。"
                )

            result = response.json()
            content = (
                result.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            if not content:
                return "图片解析完成，但未返回有效内容。"

            logger.info(
                f"Vision API 成功 file={image_path.name} "
                f"response_len={len(content)}"
            )
            return content

    except httpx.TimeoutException:
        return f"图片解析超时（{timeout}秒），请重试或使用更小的图片。"
    except Exception as e:
        logger.error(f"Vision API 调用失败 file={image_path.name} error={e}")
        return f"图片解析失败: {e}"
