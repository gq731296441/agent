"""附件服务：将用户上传的文件列表转为 Agent 可消费的 SystemMessage。

策略：不将文件内容直接注入到用户消息中（用户可能上传大文件，超出上下文限制）。
改为在 SystemMessage 中列出附件文件路径和元数据，让 Agent 主动使用工具读取：

  - 文本/二进制文件 → 使用 read_file 工具
  - 图片文件 → 使用 read_image 工具

SystemMessage 不会推送到前端，用户看不到这些提示信息。
"""

import logging
from pathlib import Path

from deep_agent_server.workspace import get_uploads_dir, get_workspace_dir

logger = logging.getLogger(__name__)

# 常见图片扩展名（使用 read_image 工具）
IMAGE_EXTENSIONS: set[str] = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".ico", ".tiff", ".tif", ".svg",
}

# Office/二进制文档扩展名（无法直接用 read_file，需 execute 终端命令转换）
# 仅列出沙箱中实际已安装对应库的格式
OFFICE_EXTENSIONS: dict[str, str] = {
    ".docx": "python3 -c \"from docx import Document; doc=Document('{path}'); [print(p.text) for p in doc.paragraphs]\"",
    ".pptx": "python3 -c \"from pptx import Presentation; prs=Presentation('{path}'); [print(slide.shapes.title.text if slide.shapes.title else '') or [print(shape.text) for shape in slide.shapes if shape.has_text_frame] for slide in prs.slides]\"",
    ".xlsx": "python3 -c \"import openpyxl; wb=openpyxl.load_workbook('{path}'); [print(f'Sheet: {s}') or [print(row) for row in wb[s].values] for s in wb.sheetnames]\"",
    ".xls":  "python3 -c \"import pandas; df=pandas.read_excel('{path}'); print(df.to_string())\"",
    ".pdf":  "python3 -c \"import PyPDF2; r=PyPDF2.PdfReader('{path}'); [print(p.extract_text()) for p in r.pages]\"",
}

# 依赖 libreoffice 的格式（沙箱默认未安装，提示用户转换）
LIBREOFFICE_EXTENSIONS: set[str] = {
    ".wps", ".dps", ".et", ".doc", ".odt", ".ods", ".odp",
}

# 最多列出的附件数
MAX_ATTACHMENTS: int = 20


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


def build_attachment_message(user_id: str, attachments: list | None) -> str | None:
    """构建附件列表提示文本，作为 SystemMessage 注入到消息流。

    返回 None 表示无需注入（无附件或无有效文件）。

    返回的文本告诉 Agent 用户上传了哪些文件、文件大小、以及应该使用什么工具读取。
    此内容会以 SystemMessage 形式注入，不推送到前端。
    """
    if not attachments:
        return None

    lines: list[str] = []
    count = 0
    total_bytes = 0

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

        if _is_image(filename):
            tool = "read_image"
            hint = ""
        elif suffix in OFFICE_EXTENSIONS:
            tool = "execute"
            hint = OFFICE_EXTENSIONS[suffix].replace("{path}", f"/{vpath}")
        elif suffix in LIBREOFFICE_EXTENSIONS:
            tool = "read_file"
            hint = (
                f"WPS/旧版 Office 格式，沙箱默认未安装 libreoffice。"
                f"请告知用户将此文件转换为标准格式后重新上传"
                f"（.wps→.docx, .dps→.pptx, .et→.xlsx, .doc→.docx, .odt→.docx 等）"
            )
        else:
            tool = "read_file"
            hint = ""

        agent_path = _to_agent_path(str(vpath))
        line = f"- `{agent_path}` — {filename}, {_format_size(file_size)}, 使用 `{tool}`"
        if hint and tool == "execute":
            line += f"\n  示例命令: `{hint}`"
        elif hint:
            line += f"\n  ⚠️ {hint}"
        lines.append(line)

        count += 1

    if not lines:
        return None

    header = (
        f"用户上传了 {count} 个附件文件（共 {_format_size(total_bytes)}）。"
        f"请根据用户的问题判断需要读取哪些文件，使用对应工具获取内容后再回答。"
    )
    return header + "\n" + "\n".join(lines)


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f}MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.2f}GB"
