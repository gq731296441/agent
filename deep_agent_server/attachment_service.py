"""附件服务：将用户上传的文件列表转为 Agent 可消费的消息内容。

策略：
  - skill 文档（pdf/docx/pptx/xlsx）：不走 markitdown，引导 Agent 使用对应 skill 处理
  - 纯文本 (Layer 1)：charset-normalizer 读文本，直接注入
  - 图片 (Layer 3)：vision_service 描述，直接注入
  - WPS/旧格式 (Layer 4)：提示用户转换

降级策略：
  - 图片未解析：提示使用 read_image
  - WPS/旧格式：提示用户转换格式
"""

import logging
from pathlib import Path

from config.config import app_config
from deep_agent_server.workspace import get_uploads_dir, get_workspace_dir

logger = logging.getLogger(__name__)

# 常见图片扩展名
IMAGE_EXTENSIONS: set[str] = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".ico", ".tiff", ".tif", ".svg",
}

# 文档 skill 支持的格式 — 不通过 markitdown 解析，由 Agent 使用 skill 自行处理
SKILL_DOCUMENT_EXTENSIONS: set[str] = {
    ".pdf", ".docx", ".pptx", ".xlsx",
}

# WPS/旧版 Office/邮件格式（沙箱未安装 libreoffice，无法解析）
LIBREOFFICE_EXTENSIONS: set[str] = {
    ".wps", ".dps", ".et", ".doc", ".ppt", ".msg", ".eml",
}

# 最多列出的附件数
MAX_ATTACHMENTS: int = 20

# 文档扩展名 → 对应 skill 名称
SKILL_MAP: dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
}


def resolve_attachment_path(user_id: str, virtual_path: str) -> Path:
    """解析附件虚拟路径为磁盘绝对路径。

    支持两种前缀：
      workspace/{session_id}/uploads/{file} → 会话级上传
      workspace/{session_id}/{sub}/{file}   → 工作区任意文件
      uploads/{file}                        → 用户级上传（兼容旧版）

    Raises:
        ValueError: 路径格式不合法时
    """
    vpath = virtual_path.lstrip("/").replace("\\", "/")
    if not vpath:
        raise ValueError("附件路径为空")

    if ".." in vpath.split("/"):
        raise ValueError(f"附件路径包含非法字符: {virtual_path}")

    if vpath.startswith("workspace/"):
        parts = vpath.split("/", 2)
        if len(parts) >= 3:
            session_id = parts[1]
            rel = parts[2]
            return get_workspace_dir(user_id, session_id) / rel
        raise ValueError(f"workspace 路径格式错误: {virtual_path}")

    if vpath.startswith("uploads/"):
        rel = vpath[len("uploads/"):]
        return get_uploads_dir(user_id) / rel

    raise ValueError(f"不支持的附件路径前缀: {virtual_path}")


def _to_agent_path(vpath: str) -> str:
    """将前端传来的显示路径转为 Agent 可用的虚拟路径。

    前端返回格式               →  Agent 虚拟路径（CompositeBackend 路由）
    ─────────────────────────────────────────────────────────────
    workspace/{sid}/uploads/f  →  /workspace/uploads/f
    workspace/{sid}/sub/f      →  /workspace/sub/f
    uploads/f                  →  /uploads/f
    """
    vp = vpath.lstrip("/").replace("\\", "/")
    if vp.startswith("workspace/"):
        parts = vp.split("/", 2)  # ["workspace", "{sid}", "uploads/f"]
        if len(parts) >= 3:
            return f"/workspace/{parts[2]}"
    return f"/{vp}"


def _is_image(filename: str) -> bool:
    return Path(filename).suffix.lower() in IMAGE_EXTENSIONS


def _is_libreoffice_format(filename: str) -> bool:
    return Path(filename).suffix.lower() in LIBREOFFICE_EXTENSIONS


def _get_format_label(suffix: str) -> str:
    """返回人类可读的格式标签。"""
    labels = {
        ".txt": "纯文本", ".md": "Markdown", ".csv": "CSV表格",
        ".json": "JSON", ".xml": "XML", ".yaml": "YAML",
        ".yml": "YAML", ".html": "HTML", ".htm": "HTML",
        ".log": "日志", ".py": "Python", ".js": "JavaScript",
        ".ts": "TypeScript", ".sql": "SQL", ".cfg": "配置文件",
        ".ini": "配置文件", ".toml": "TOML", ".env": "环境变量",
        ".rst": "reStructuredText", ".tex": "LaTeX",
        ".docx": "Word文档", ".pptx": "PPT演示", ".xlsx": "Excel表格",
        ".xls": "Excel表格(旧)", ".pdf": "PDF文档",
        ".epub": "电子书", ".rtf": "RTF文档",
        ".odt": "OpenDocument文本", ".ods": "OpenDocument表格",
        ".odp": "OpenDocument演示",
        ".jpg": "图片", ".jpeg": "图片", ".png": "图片",
        ".gif": "图片", ".bmp": "图片", ".webp": "图片",
        ".svg": "图片", ".ico": "图片", ".tiff": "图片",
    }
    return labels.get(suffix.lower(), "文件")


async def build_attachment_message(user_id: str, attachments: list | None) -> str | None:
    """构建附件上下文文本，注入到 HumanMessage。

    优先读取后端解析缓存 {file}.parsed.txt，直接注入文本内容。
    缓存未命中时对 Layer 1/2 文件按需触发解析（不阻塞消息处理），
    Layer 3/4 降级为工具提示。

    返回 None 表示无需注入（无附件或无有效文件）。
    """
    if not attachments:
        return None

    lines: list[str] = []
    count = 0
    total_bytes = 0
    has_parsed_content = False

    for vpath in attachments:
        if count >= MAX_ATTACHMENTS:
            lines.append(
                f"（共 {len(attachments)} 个附件，仅列出前 {MAX_ATTACHMENTS} 个）"
            )
            break

        try:
            disk_path = resolve_attachment_path(user_id, str(vpath))
        except ValueError:
            lines.append(f"- {vpath} （路径无效，已跳过）")
            continue

        if not disk_path.is_file():
            lines.append(f"- {vpath} （文件不存在，已跳过）")
            continue

        filename = disk_path.name
        try:
            file_size = disk_path.stat().st_size
        except OSError:
            lines.append(f"- {vpath} （无法读取，已跳过）")
            continue

        total_bytes += file_size
        suffix = disk_path.suffix.lower()

        # ── Skill 文档（pdf/docx/pptx/xlsx）：不解析，引导 Agent 使用 skill ──
        if suffix in SKILL_DOCUMENT_EXTENSIONS:
            skill_name = SKILL_MAP.get(suffix, "read_file")
            format_label = _get_format_label(suffix)
            agent_path = _to_agent_path(str(vpath))
            lines.append(
                f"用户上传了一个{format_label}文件：`{agent_path}` ({filename}, {_format_size(file_size)})，"
                f"请使用 **{skill_name} skill** 来读取和处理此文件的内容。"
            )
            count += 1
            continue

        # ── 图片：优先读取缓存，否则提示 ──
        if _is_image(filename):
            parsed_content = _read_parsed_cache(disk_path)
            if parsed_content is not None:
                lines.append(
                    f"\n--- {filename} (图片, {_format_size(file_size)}) ---\n"
                    f"{parsed_content}"
                )
                has_parsed_content = True
            else:
                agent_path = _to_agent_path(str(vpath))
                lines.append(
                    f"- `{agent_path}` — {filename}, {_format_size(file_size)}, "
                    f"图片尚未解析，可使用 `read_image` 工具查看"
                )
            count += 1
            continue

        # ── WPS/旧格式：提示转换 ──
        if _is_libreoffice_format(filename):
            lib_hints = {
                ".wps": "WPS 文字文档，请转换为 .docx 后重新上传",
                ".dps": "WPS 演示文档，请转换为 .pptx 后重新上传",
                ".et":  "WPS 表格文档，请转换为 .xlsx 后重新上传",
                ".doc": "旧版 Word 文档，请转换为 .docx 后重新上传",
                ".ppt": "旧版 PPT 文档，请转换为 .pptx 后重新上传",
                ".msg": "Outlook 邮件格式，请导出为 .txt 或 .pdf 后重新上传",
                ".eml": "邮件格式，请另存为 .txt 后重新上传",
            }
            hint = lib_hints.get(suffix, f"暂不支持 {suffix} 格式的自动解析")
            agent_path = _to_agent_path(str(vpath))
            lines.append(f"- `{agent_path}` — {filename}, ⚠️ {hint}")
            count += 1
            continue

        # ── 纯文本 / 其他：按需解析 ──
        parsed_content = _read_parsed_cache(disk_path)
        if parsed_content is not None:
            format_label = _get_format_label(suffix)
            lines.append(
                f"\n--- {filename} ({format_label}, {_format_size(file_size)}) ---\n"
                f"{parsed_content}"
            )
            has_parsed_content = True
        else:
            agent_path = _to_agent_path(str(vpath))
            lines.append(
                f"- `{agent_path}` — {filename}, {_format_size(file_size)}, "
                f"可使用 `read_file` 工具读取内容"
            )

        count += 1

    if not lines:
        return None

    # 根据附件类型使用不同的引导语
    has_skill_docs = any(
        Path(str(a)).suffix.lower() in SKILL_DOCUMENT_EXTENSIONS
        for a in (attachments or [])
    )
    if has_skill_docs and not has_parsed_content:
        header = (
            f"用户上传了 {count} 个附件（共 {_format_size(total_bytes)}）。\n"
            f"对于文档文件（PDF/Word/PPT/Excel），请直接使用对应的 skill 工具来读取内容"
            f"（如 `python scripts/xxx.py`），不要使用 `read_file` 工具。\n"
            f"对于其他文件类型，请根据需要使用对应工具获取内容。"
        )
    elif has_parsed_content:
        header = (
            f"用户上传了 {count} 个附件文件（共 {_format_size(total_bytes)}），"
            f"文件内容已自动解析如下。"
            f"请优先基于以下解析内容回答用户的问题，"
            f"仅当需要查看被截断的完整内容时才使用工具读取原文件。"
        )
    else:
        header = (
            f"用户上传了 {count} 个附件文件（共 {_format_size(total_bytes)}）。"
            f"请根据用户的问题判断需要读取哪些文件，使用对应工具获取内容后再回答。"
        )

    return header + "\n" + "\n".join(lines)


def _read_parsed_cache(file_path: Path) -> str | None:
    """读取后端解析缓存（如果启用且缓存有效）。

    Returns:
        解析后的文本内容，或 None（缓存不存在/过期/解析功能关闭）。
    """
    if not app_config.FILE_PARSE_ENABLED:
        return None

    from deep_agent_server.file_parser import read_cached_content

    return read_cached_content(file_path)


def _detect_file_layer(suffix: str) -> int:
    """通过扩展名判断文件层级（1-4），避免重复导入 file_parser 的开销。"""
    from deep_agent_server.file_parser import (
        LAYER_1_EXTENSIONS,
        LAYER_2_EXTENSIONS,
        LAYER_3_EXTENSIONS,
        LAYER_4_EXTENSIONS,
    )
    if suffix in LAYER_1_EXTENSIONS:
        return 1
    if suffix in LAYER_2_EXTENSIONS:
        return 2
    if suffix in LAYER_3_EXTENSIONS:
        return 3
    if suffix in LAYER_4_EXTENSIONS:
        return 4
    return 0


async def _parse_on_demand(file_path: Path, user_id: str, layer: int) -> str | None:
    """对 Layer 1/2 文件按需触发解析（缓存未命中时）。

    Layer 1 (纯文本): run_in_executor 解析，毫秒级
    Layer 2 (Office): run_in_executor 解析，1~3 秒
    解析成功写缓存，后续消息直接命中。

    Returns:
        解析后的文本内容，失败时返回 None。
    """
    from deep_agent_server.file_parser import parse_file_async

    result = await parse_file_async(file_path, user_id=user_id)
    if result.content and not result.error:
        return result.content
    return None


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f}MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.2f}GB"
