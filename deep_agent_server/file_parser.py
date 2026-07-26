"""文件解析模块 — markitdown + 分层兜底方案。

架构：
  Layer 1 (纯文本, 零依赖)  → charset-normalizer 检测编码 → read_text()
  Layer 2 (Office/PDF)     → markitdown 统一转换
  Layer 3 (图片)             → vision_service.analyze_image()
  Layer 4 (WPS/旧格式)       → 返回"请转换格式"提示

缓存策略：
  解析结果存为 {file}.parsed.txt，与原始文件同目录。
  缓存有效性：mtime(parsed) >= mtime(original)。

截断策略：
  超过 FILE_PARSE_MAX_CHARS 的文本会被截断，
  保留头部 + 尾部 + 提示 Agent 用 read_file 获取完整内容。
"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from config.config import app_config

logger = logging.getLogger(__name__)

# ── 分层定义 ──

LAYER_1_EXTENSIONS: set[str] = {
    ".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml",
    ".html", ".htm", ".log", ".py", ".js", ".ts", ".sql",
    ".cfg", ".ini", ".toml", ".env", ".rst", ".tex",
}

LAYER_2_EXTENSIONS: set[str] = {
    ".docx", ".pptx", ".xlsx", ".xls", ".pdf",
    ".epub", ".rtf", ".odt", ".ods", ".odp",
}

LAYER_3_EXTENSIONS: set[str] = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp",
    ".webp", ".tiff", ".tif", ".svg", ".ico",
}

LAYER_4_EXTENSIONS: set[str] = {
    ".wps", ".dps", ".et", ".doc", ".ppt",
    ".msg", ".eml",
}

LAYER_4_HINTS: dict[str, str] = {
    ".wps": "WPS 文字文档，请转换为 .docx 后重新上传",
    ".dps": "WPS 演示文档，请转换为 .pptx 后重新上传",
    ".et":  "WPS 表格文档，请转换为 .xlsx 后重新上传",
    ".doc": "旧版 Word 文档，请转换为 .docx 后重新上传",
    ".ppt": "旧版 PPT 文档，请转换为 .pptx 后重新上传",
    ".msg": "Outlook 邮件格式，请导出为 .txt 或 .pdf 后重新上传",
    ".eml": "邮件格式，请另存为 .txt 后重新上传",
}

# 缓存后缀
CACHE_SUFFIX = ".parsed.txt"


# ── 数据类型 ──

@dataclass
class ParseResult:
    """文件解析结果。"""
    content: str
    truncated: bool = False
    format: str = "unknown"       # text / office / image / wps / cached / error
    error: Optional[str] = None
    layer: int = 0                # 1-4，0=未知


# ── 公共 API ──

def detect_format(file_path: Path) -> int:
    """通过文件扩展名判定所属层级。

    Returns:
        1=纯文本, 2=Office/PDF, 3=图片, 4=WPS/旧格式, 0=未知
    """
    suffix = file_path.suffix.lower()
    if suffix in LAYER_1_EXTENSIONS:
        return 1
    if suffix in LAYER_2_EXTENSIONS:
        return 2
    if suffix in LAYER_3_EXTENSIONS:
        return 3
    if suffix in LAYER_4_EXTENSIONS:
        return 4
    return 0


def is_parsed(file_path: Path) -> bool:
    """检查解析缓存是否存在且未过期。"""
    cache = _get_cache_path(file_path)
    if not cache.is_file():
        return False
    try:
        return cache.stat().st_mtime >= file_path.stat().st_mtime
    except OSError:
        return False


def read_cached_content(file_path: Path) -> Optional[str]:
    """读取缓存的解析内容。

    Returns:
        解析内容字符串，缓存缺失/过期/读取失败时返回 None。
    """
    if not is_parsed(file_path):
        return None
    cache = _get_cache_path(file_path)
    try:
        return cache.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"读解析缓存失败 {cache}: {e}")
        return None


async def parse_file_async(
    file_path: Path,
    user_id: str = "",
    question: str = "请详细描述这张图片的内容",
) -> ParseResult:
    """异步解析文件入口。

    - Layer 1/2: CPU 密集 → run_in_executor(线程池)
    - Layer 3:   网络 IO → 直接异步
    - Layer 4:   同步返回提示

    解析成功后会写缓存 {file}.parsed.txt。
    """
    # 先检查缓存
    cached = read_cached_content(file_path)
    if cached is not None:
        return ParseResult(
            content=cached, format="cached",
            layer=detect_format(file_path),
        )

    layer = detect_format(file_path)

    if layer == 1:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _parse_layer1, file_path)
    elif layer == 2:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, _parse_layer2, file_path,
        )
    elif layer == 3:
        result = await _parse_layer3_async(file_path, user_id, question)
    elif layer == 4:
        result = _parse_layer4(file_path)
    else:
        result = ParseResult(
            content="", error="不支持的文件格式", layer=0,
        )

    # 解析成功时写缓存（即使被截断也可以缓存）
    if result.content and layer in (1, 2, 3):
        _write_cache(file_path, result.content)

    return result


# ── 内部：缓存 ──

def _get_cache_path(file_path: Path) -> Path:
    """返回 {file}.parsed.txt"""
    return file_path.with_suffix(file_path.suffix + CACHE_SUFFIX)


def _write_cache(file_path: Path, content: str) -> None:
    """写解析缓存文件。"""
    cache = _get_cache_path(file_path)
    try:
        cache.write_text(content, encoding="utf-8")
    except OSError as e:
        logger.warning(f"写解析缓存失败 {cache}: {e}")


# ── 内部：截断 ──

def _truncate_content(text: str) -> tuple[str, bool]:
    """按配置截断超长文本。

    Returns:
        (处理后的文本, 是否被截断)
    """
    max_chars = app_config.FILE_PARSE_MAX_CHARS
    head_chars = app_config.FILE_PARSE_TRUNCATE_HEAD
    tail_chars = app_config.FILE_PARSE_TRUNCATE_TAIL

    if len(text) <= max_chars:
        return text, False

    head = text[:head_chars]
    tail = text[-tail_chars:]
    truncated = (
        f"{head}\n\n"
        f"[内容较长（共 {len(text)} 字符），已截断。"
        f"如需完整内容，请使用 read_file 工具读取原文件]\n\n"
        f"{tail}"
    )
    return truncated, True


# ── 内部：Layer 1 — 纯文本 ──

def _parse_layer1(file_path: Path) -> ParseResult:
    """Layer 1: 纯文本文件。

    使用 charset-normalizer 自动检测编码后读取。
    """
    try:
        import charset_normalizer

        raw = file_path.read_bytes()
        detected = charset_normalizer.from_bytes(raw)
        text = str(detected.best())
        content, truncated = _truncate_content(text)
        return ParseResult(
            content=content, truncated=truncated,
            format="text", layer=1,
        )
    except Exception as e:
        # 降级：尝试直接 UTF-8 读取
        try:
            text = file_path.read_text(encoding="utf-8")
            content, truncated = _truncate_content(text)
            return ParseResult(
                content=content, truncated=truncated,
                format="text", layer=1,
            )
        except Exception:
            return ParseResult(
                content="", error=f"文本解析失败: {e}",
                format="text", layer=1,
            )


# ── 内部：Layer 2 — Office/PDF ──

def _parse_layer2(file_path: Path) -> ParseResult:
    """Layer 2: Office/PDF 文档。

    使用 markitdown 统一转换为 markdown 文本。
    """
    try:
        from markitdown import MarkItDown
        from markitdown._exceptions import UnsupportedFormatException
    except ImportError:
        return ParseResult(
            content="", error="markitdown 库未安装，无法解析 Office/PDF 文档",
            format="office", layer=2,
        )

    try:
        md = MarkItDown()
        result = md.convert(str(file_path))
        text = result.text_content

        if not text or not text.strip():
            return ParseResult(
                content="", error="文档未包含可提取的文本内容",
                format="office", layer=2,
            )

        content, truncated = _truncate_content(text)
        return ParseResult(
            content=content, truncated=truncated,
            format="office", layer=2,
        )
    except UnsupportedFormatException:
        # markitdown 不支持此格式 → 回退到 Layer4 提示
        return _parse_layer4(file_path)
    except Exception as e:
        # 区分错误类型，给出更精准的提示
        error_msg = str(e)
        suffix = file_path.suffix.lower()

        if "BadZipFile" in error_msg or "not a zip file" in error_msg:
            # docx/pptx/xlsx 本质是 ZIP 包，损坏或格式名不匹配时触发
            if suffix in (".docx", ".pptx", ".xlsx"):
                return ParseResult(
                    content="",
                    error=(
                        f"文件已损坏或不是真正的 {suffix} 文件"
                        f"（{suffix} 格式实际是一个 ZIP 压缩包，"
                        f"当前文件无法作为 ZIP 打开）。请确认文件来源。"
                    ),
                    format="office", layer=2,
                )
            return ParseResult(
                content="",
                error=f"文件无法解析，可能是文件已损坏: {error_msg}",
                format="office", layer=2,
            )

        if "MissingDependencyException" in error_msg:
            return ParseResult(
                content="",
                error=f"解析器缺少必要组件: {error_msg}。请安装 markitdown[all]。",
                format="office", layer=2,
            )

        return ParseResult(
            content="", error=f"文档解析失败: {error_msg}",
            format="office", layer=2,
        )


# ── 内部：Layer 3 — 图片 ──

async def _parse_layer3_async(
    file_path: Path, user_id: str, question: str,
) -> ParseResult:
    """Layer 3: 图片文件。

    调用 vision_service.analyze_image() 获取图片描述。
    图片描述通常很短，不做截断处理。
    """
    from deep_agent_server.vision_service import analyze_image

    timeout = app_config.FILE_PARSE_IMAGE_TIMEOUT
    try:
        text = await analyze_image(
            file_path, user_id=user_id, question=question,
            timeout=timeout,
        )
        if text.startswith("错误") or text.startswith("图片") and "失败" in text:
            return ParseResult(
                content="", error=text,
                format="image", layer=3,
            )
        return ParseResult(
            content=text, truncated=False,
            format="image", layer=3,
        )
    except Exception as e:
        return ParseResult(
            content="", error=f"图片解析失败: {e}",
            format="image", layer=3,
        )


# ── 内部：Layer 4 — WPS/旧格式 ──

def _parse_layer4(file_path: Path) -> ParseResult:
    """Layer 4: WPS/旧版 Office/邮件格式。

    不解析，返回格式转换提示。
    """
    suffix = file_path.suffix.lower()
    hint = LAYER_4_HINTS.get(suffix, f"暂不支持 {suffix} 格式的文件自动解析")
    return ParseResult(
        content=hint, format="wps", layer=4, error=hint,
    )
