"""Vision API 服务 — Qwen3-VL 多模态接口。

封装多模态图片识别调用，被 file_parser.py（后端解析）和
builtin_tools/image_reader.py（Agent 工具）共用。

默认 API（可在 .env 中覆盖）:
  URL:     https://maas-apigateway.dt.zte.com.cn/model/qwen3-vl-32b-instruct/v1/chat/completions
  Model:   Qwen3-VL-32B-Instruct
  Auth:    Bearer <VISION_API_KEY>

兼容说明：
  - 配置了 VISION_API_KEY 时使用 `Authorization: Bearer <key>`；
  - 未配置时回退为旧 nebulacoder 的“纯工号”认证（需同时把
    VISION_API_URL / VISION_MODEL 指回旧接口），否则会认证失败。
"""

import base64
from pathlib import Path

import httpx

from config import get_logger
from config.config import app_config

logger = get_logger(__name__)

# 支持的图片格式
SUPPORTED_IMAGE_EXTENSIONS: set[str] = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp",
    ".webp", ".tiff", ".tif", ".svg", ".ico",
}

# 单张图片大小上限（20MB）
MAX_IMAGE_SIZE_BYTES = 20 * 1024 * 1024

# 错误响应 body 日志截断长度
_ERROR_BODY_LOG_LIMIT = 500


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


def _extract_content(result: dict) -> str:
    """从 OpenAI 兼容响应中提取文本内容。

    兼容 content 为字符串或分段列表（多模态模型常见）两种格式。
    """
    try:
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    except (AttributeError, IndexError, TypeError):
        return ""

    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))
        return "\n".join(parts).strip()
    return str(content).strip() if content else ""


async def analyze_image(
    image_path: Path,
    user_id: str,
    question: str = "请详细描述这张图片的内容",
    timeout: int | None = None,
) -> str:
    """使用多模态模型分析图片，返回文本描述。

    Args:
        image_path: 图片磁盘绝对路径
        user_id: 用户 8 位工号（仅在未配置 API Key、回退旧接口时用作认证）
        question: 对图片的提问
        timeout: 超时秒数（默认读取 VISION_TIMEOUT）

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

    # 4. 认证头：优先 Bearer API Key；未配置时兼容旧版纯工号
    api_url = (app_config.VISION_API_URL or "").strip()
    model = (app_config.VISION_MODEL or "").strip()
    api_key = (app_config.VISION_API_KEY or "").strip()
    if not api_url or not model:
        return "错误：未配置 VISION_API_URL / VISION_MODEL，无法调用图片识别服务"

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        headers["Authorization"] = str(user_id)
        logger.warning(
            "VISION_API_KEY 未配置，使用纯工号认证；"
            "仅当 VISION_API_URL 指向旧 nebulacoder 接口时可用"
        )

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": question},
                ],
            }
        ],
        "stream": False,
    }

    # 5. 调用 API
    eff_timeout = timeout or app_config.VISION_TIMEOUT
    try:
        # trust_env=False：绕过系统 http(s)_proxy / all_proxy（部分环境配置了
        # httpx 不支持的 socks 代理；该接口是内网直连域名，no_proxy 已覆盖）
        async with httpx.AsyncClient(timeout=eff_timeout, trust_env=False) as client:
            response = await client.post(api_url, headers=headers, json=payload)
            if response.status_code != 200:
                body = (response.text or "")[:_ERROR_BODY_LOG_LIMIT]
                logger.error(
                    f"Vision API 错误 status={response.status_code} "
                    f"file={image_path.name} body={body}"
                )
                return (
                    f"图片解析失败: API 返回 {response.status_code}。"
                    f"请稍后重试。"
                )

            try:
                result = response.json()
            except ValueError:
                logger.error(
                    f"Vision API 返回非 JSON file={image_path.name} "
                    f"body={(response.text or '')[:_ERROR_BODY_LOG_LIMIT]}"
                )
                return "图片解析失败: API 返回非 JSON 内容。"

            content = _extract_content(result)
            if not content:
                return "图片解析完成，但未返回有效内容。"

            logger.info(
                f"Vision API 成功 file={image_path.name} "
                f"model={model} response_len={len(content)}"
            )
            return content

    except httpx.TimeoutException:
        return f"图片解析超时（{eff_timeout}秒），请重试或使用更小的图片。"
    except Exception as e:
        logger.error(f"Vision API 调用失败 file={image_path.name} error={e}")
        return f"图片解析失败: {e}"
