"""本轮 conversation 内 Agent 写入的工作区文件（卡片展示 / WS 推送）。"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import get_logger

logger = get_logger(__name__)

# 图片类不在卡片中展示（用户要求排除）
_IMAGE_EXTS = frozenset(
    {"jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "ico", "tif", "tiff"}
)

# 与沙箱 requirements + 前端预览能力对齐（非图片）
_CARD_EXTS = frozenset(
    {
        # 文档
        "html", "htm", "md", "markdown", "pdf", "docx", "txt",
        # 表格
        "xls", "xlsx", "csv",
        # 演示
        "pptx",
        # 代码 / 配置 / 数据
        "json", "js", "ts", "jsx", "tsx", "vue", "css", "scss", "less",
        "xml", "yaml", "yml", "py", "sh", "bash", "sql", "log",
        "java", "go", "rs", "c", "cpp", "h", "hpp", "php", "rb", "swift", "kt",
        "toml", "ini", "env.example",
    }
)

_WRITE_TOOLS = frozenset({"write_file", "edit_file"})

_PATH_IN_RESULT_RE = re.compile(r"(/workspace/[^\s'\"<>]+\.\w+)")

_BLOCKED_PATH_PARTS = (
    "/.tool-cache/",
    "/.spill/",
    "/.sandbox/",
)


def file_extension(path: str) -> str:
    name = (path or "").split("/")[-1]
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()


def is_card_eligible_agent_path(agent_path: str) -> bool:
    """是否可作为会话文件卡片展示（排除图片与内部路径）。"""
    p = (agent_path or "").replace("\\", "/").strip()
    if not p:
        return False
    norm = f"/{p.lstrip('/')}"
    for part in _BLOCKED_PATH_PARTS:
        if part in norm:
            return False
    ext = file_extension(p)
    if not ext or ext in _IMAGE_EXTS:
        return False
    return ext in _CARD_EXTS


def agent_path_to_virtual(session_id: str, agent_path: str) -> Optional[str]:
    """Agent 路径 /workspace/foo.md → workspace/{session_id}/foo.md

    当路径中已有 3 段但第二段不是 session_id 时（如 workspace/工作区/foo），
    自动注入 session_id 到正确位置。
    """
    p = (agent_path or "").replace("\\", "/").strip()
    if p.startswith("/"):
        p = p[1:]
    if p.startswith("workspace/"):
        parts = p.split("/")
        if len(parts) >= 3:
            # parts[1] 可能是 session_id，也可能是普通目录名（如 LLM 创建的「工作区」）
            if session_id and parts[1] != session_id:
                return f"workspace/{session_id}/{'/'.join(parts[1:])}"
            return p
        if session_id and len(parts) >= 2:
            return f"workspace/{session_id}/{'/'.join(parts[1:])}"
        return None
    if session_id:
        return f"workspace/{session_id}/{p}"
    return None


def extract_path_from_result(result_text: str) -> Optional[str]:
    """从工具返回文本中提取 /workspace/... 路径（args 缺失时的兜底）。"""
    match = _PATH_IN_RESULT_RE.search(result_text or "")
    return match.group(1) if match else None


def infer_fs_tool_name(args: Optional[dict]) -> Optional[str]:
    """从工具参数推断内置文件工具名（ToolMessage.name 缺失时的兜底）。"""
    if not args:
        return None
    file_path = (args.get("file_path") or args.get("path") or "").strip()
    if not file_path:
        return None
    if "old_string" in args:
        return "edit_file"
    if "content" in args:
        return "write_file"
    if "limit" in args or "offset" in args:
        return "read_file"
    return None


def resolve_write_tool_name(tool_name: str, args: Optional[dict]) -> Optional[str]:
    """解析 write/edit 登记用的工具名。"""
    name = (tool_name or "").strip()
    if name in _WRITE_TOOLS:
        return name
    inferred = infer_fs_tool_name(args)
    if inferred in _WRITE_TOOLS:
        return inferred
    return None


_WORKSPACE_CN = "工作区"


def _normalize_workspace_path(path: str) -> str:
    """将中文「工作区」前缀规范化为 /workspace/，兼容 Agent LLM 输出。

    「工作区」可能是 workspace 的同义词，也可能是 workspace 内的真实子目录。
    因此只翻译前缀为英文，保留「工作区」作为合法路径段，不删除任何路径组件。
    """
    p = path.replace("\\", "/").strip()
    # /工作区/foo → /workspace/工作区/foo
    if p.startswith(f"/{_WORKSPACE_CN}/"):
        return f"/workspace/{_WORKSPACE_CN}/{p[len(f'/{_WORKSPACE_CN}/'):]}"
    # /工作区 (无后续路径) → /workspace/工作区
    if p.startswith(f"/{_WORKSPACE_CN}"):
        return f"/workspace/{_WORKSPACE_CN}"
    # 工作区/foo → workspace/工作区/foo
    if p.startswith(f"{_WORKSPACE_CN}/"):
        return f"workspace/{_WORKSPACE_CN}/{p[len(f'{_WORKSPACE_CN}/'):]}"
    # 工作区 (无后续路径) → workspace/工作区
    if p == _WORKSPACE_CN:
        return f"workspace/{_WORKSPACE_CN}"
    return p


def resolve_agent_file_path(
    *,
    tool_name: str,
    args: Optional[dict],
    workspace_path: Optional[str],
    session_id: str,
    result_text: str = "",
) -> Optional[str]:
    if resolve_write_tool_name(tool_name, args) is None:
        return None
    agent_path = ""
    if args:
        agent_path = (args.get("file_path") or args.get("path") or "").strip()
    # 规范化：Agent LLM 可能将 "workspace" 写成中文 "工作区"
    if agent_path:
        agent_path = _normalize_workspace_path(agent_path)
    if not agent_path and workspace_path:
        wp = workspace_path.replace("\\", "/").strip()
        if wp.startswith("workspace/") and session_id:
            prefix = f"workspace/{session_id}/"
            if wp.startswith(prefix):
                agent_path = f"/workspace/{wp[len(prefix):]}"
        elif wp.startswith("/workspace/"):
            agent_path = wp
    if not agent_path:
        agent_path = extract_path_from_result(result_text) or ""
    if not agent_path or not is_card_eligible_agent_path(agent_path):
        return None
    return agent_path


def _mime_for_name(name: str) -> str:
    guessed, _ = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"


def file_exists(user_id: str, virtual_path: str) -> bool:
    try:
        from deep_agent_server.workspace_service import resolve_virtual_path

        abs_path, _, _, _ = resolve_virtual_path(user_id, virtual_path)
        return abs_path.is_file()
    except Exception:
        return False


def build_file_entry(
    *,
    user_id: str,
    session_id: str,
    conversation_id: str,
    agent_path: str,
    tool_call_id: str = "",
    tool_name: str = "",
) -> Optional[Dict[str, Any]]:
    virtual_path = agent_path_to_virtual(session_id, agent_path)
    if not virtual_path:
        return None
    name = virtual_path.split("/")[-1]
    exists = file_exists(user_id, virtual_path)
    return {
        "agent_path": agent_path if agent_path.startswith("/") else f"/{agent_path.lstrip('/')}",
        "virtual_path": virtual_path,
        "name": name,
        "mime_type": _mime_for_name(name),
        "exists": exists,
        "previewable": exists,
        "tool_call_id": tool_call_id or None,
        "tool_name": tool_name or None,
        "conversation_id": conversation_id,
    }


def is_write_success(result_text: str) -> bool:
    text = (result_text or "").strip().lower()
    if not text:
        return False
    fail_tokens = (
        "error",
        "failed",
        "failure",
        "denied",
        "not found",
        "permission",
        "quota",
        "cannot",
        "unable",
    )
    return not any(tok in text for tok in fail_tokens)


def build_conversation_files_payload(
    *,
    user_id: str,
    session_id: str,
    conversation_id: str,
    run_id: str,
    recorded: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    files: List[Dict[str, Any]] = []
    for item in recorded.values():
        agent_path = item.get("agent_path") or ""
        entry = build_file_entry(
            user_id=user_id,
            session_id=session_id,
            conversation_id=conversation_id,
            agent_path=agent_path,
            tool_call_id=item.get("tool_call_id") or "",
            tool_name=item.get("tool_name") or "",
        )
        if entry:
            files.append(entry)
    return {
        "agent_mode": "deep",
        "run_id": run_id,
        "conversation_id": conversation_id,
        "files": files,
    }
