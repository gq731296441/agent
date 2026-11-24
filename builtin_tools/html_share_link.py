"""内置工具：获取或创建工作区 HTML 的免登录分享链接。

分享 token 存在数据库，不会写进 HTML 文件。Agent 需要把已有报告嵌到其他系统时，
调用本工具即可：已有链接直接返回，没有则创建（含资源快照）后返回。
"""

from __future__ import annotations

import json
import re
from typing import List

from langchain.tools import tool
from pydantic import BaseModel, Field

from config import get_logger

logger = get_logger(__name__)

_MAX_PATHS = 20
_HTML_EXTS = (".html", ".htm")


class _HtmlShareLinkInput(BaseModel):
    """get_html_share_link 的输入。"""

    paths: str = Field(
        default="",
        description=(
            "一个或多个工作区 HTML 路径，用换行或逗号分隔。"
            "例如：/workspace/reports/a.html\\n/workspace/reports/b.html。"
            "留空则只列出当前会话在数据库里已有的分享链接，不会新建。"
        ),
    )


def split_html_paths(raw: str) -> List[str]:
    """把模型传入的路径串拆成列表。支持换行、逗号，以及 JSON 数组字符串。"""
    text = (raw or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            data = None
        if isinstance(data, list):
            return [str(item).strip() for item in data if str(item).strip()]
    parts = re.split(r"[\n,;]+", text)
    return [part.strip().strip("'\"") for part in parts if part.strip()]


def to_share_vpath(session_id: str, raw: str) -> str:
    """Agent 路径（/workspace/a.html 或 reports/a.html）→ workspace/{session}/...。"""
    from deep_agent_server.conversation_files import agent_path_to_virtual

    path = (raw or "").strip().strip("'\"")
    if path.startswith("file://"):
        path = path[len("file://"):]
    vpath = agent_path_to_virtual(session_id, path) or ""
    return vpath


def create_get_html_share_link(user_id: str, session_id: str = ""):
    """工厂：绑定当前用户与会话，返回 get_html_share_link 工具。"""
    _user_id = user_id
    _session_id = session_id or ""

    @tool("get_html_share_link", args_schema=_HtmlShareLinkInput)
    async def get_html_share_link(paths: str = "") -> str:
        """获取工作区 HTML 的免登录分享链接。已有则从数据库复用，没有则创建。

        在需要把 HTML 嵌到其他系统、或工具结果里没有「分享链接已生成」时调用。
        传入文件路径（可多个）。不传路径时，只列出当前会话已经生成过的链接。
        不要在工作区搜索 html-share，不要自己编造 token。
        """
        if not _user_id or not _session_id:
            return "无法获取分享链接：当前会话缺少用户或会话标识。"

        requested = split_html_paths(paths)
        if not requested:
            return await _list_existing(_user_id, _session_id)

        if len(requested) > _MAX_PATHS:
            skipped = len(requested) - _MAX_PATHS
            requested = requested[:_MAX_PATHS]
        else:
            skipped = 0

        lines = ["HTML 分享链接（已有则复用，没有则新建）："]
        ok = 0
        for raw in requested:
            line, created = await _ensure_one(_user_id, _session_id, raw)
            lines.append(line)
            if created:
                ok += 1
        if skipped:
            lines.append(f"其余 {skipped} 个路径未处理，请再调用一次。")
        lines.append(f"成功 {ok} 个。把上面的完整 URL 用于发布或回复用户，不要改写 token。")
        return "\n".join(lines)

    return get_html_share_link


async def _list_existing(user_id: str, session_id: str) -> str:
    from db.html_share_store import list_share_tokens
    from deep_agent_server.html_share_service import share_url_for_token

    try:
        records = await list_share_tokens(user_id, session_id)
    except Exception as e:
        logger.warning(f"[HtmlShareTool] 列出现有链接失败: {e}")
        return "读取已有分享链接失败，请改为传入 HTML 文件路径重试。"

    lines = []
    for record in records:
        if not record.get("valid", True):
            continue
        file_path = (record.get("file_path") or "").strip()
        token = (record.get("token") or "").strip()
        if not file_path or not token or not file_path.lower().endswith(_HTML_EXTS):
            continue
        name = file_path.rsplit("/", 1)[-1]
        lines.append(f"- {name}\n  路径: {file_path}\n  分享链接: {share_url_for_token(token)}")
    if not lines:
        return (
            "当前会话还没有 HTML 分享链接。"
            "请传入工作区 HTML 路径（如 /workspace/reports/报告.html），工具会创建后返回。"
        )
    return "当前会话已有的 HTML 分享链接：\n" + "\n".join(lines)


async def _ensure_one(user_id: str, session_id: str, raw: str) -> tuple:
    """返回 (展示行, 是否成功)。"""
    from deep_agent_server.html_share_service import ensure_html_share, is_complete_html
    from deep_agent_server.workspace_service import resolve_virtual_path

    vpath = to_share_vpath(session_id, raw)
    name = (raw or vpath or "").rsplit("/", 1)[-1] or raw
    if not vpath or ".." in vpath.split("/"):
        return f"- {name}: 路径无效（{raw}）", False
    if not vpath.lower().endswith(_HTML_EXTS):
        return f"- {name}: 不是 HTML 文件", False

    try:
        abs_path, _rel, _sid, file_kind = resolve_virtual_path(user_id, vpath)
    except Exception as e:
        logger.info(f"[HtmlShareTool] 路径解析失败 path={raw}: {e}")
        return f"- {name}: 路径无效（{raw}）", False

    if file_kind != "workspace" or not abs_path.is_file():
        return f"- {name}: 文件不存在（{raw}）", False

    try:
        content = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.warning(f"[HtmlShareTool] 读取失败 path={vpath}: {e}")
        return f"- {name}: 读取失败", False

    if not is_complete_html(content):
        return f"- {name}: 还不是完整 HTML（需要 </body> 或 </html>），暂不能分享", False

    url = await ensure_html_share(
        user_id=user_id,
        session_id=session_id,
        vpath=vpath,
    )
    if not url:
        return f"- {name}: 分享链接创建失败，请稍后重试", False
    logger.info(f"[HtmlShareTool] path={vpath} url={url}")
    return f"- {name}\n  路径: {raw.strip()}\n  分享链接: {url}", True
