"""HTML 分享链接实时注入中间件 (HtmlShareLinkMiddleware)

拦截 write_file / edit_file / execute 的工具结果：若本次调用写出了完整 HTML
文件，则同步创建（或复用）分享链接与资源快照，并把链接追加到工具结果文本中。

为什么需要：
    分享 token 原先只在 adapter 消费工具事件时异步创建（fire-and-forget），
    Agent 当轮看不到链接，只能等下一轮 run 由 runner 回灌 —— 用户拿到最终
    回复时往往还没有链接。本中间件在工具返回前完成创建，链接作为工具结果的
    一部分进入上下文，Agent 本轮即可在最终答案中告知用户。

与既有链路的关系：
    - 共享 wrapper 的 share_links 缓存与锁：中间件先创建 → adapter 的
      _schedule_html_share 命中缓存直接返回，不重复建 token
    - 创建走 html_share_service.ensure_html_share（含快照预建 + 内容指纹落库），
      因此「源文件改名/删除后链接仍可访问」不再依赖首次访问的懒重建

失败隔离：分享创建的任何异常都被捕获并降级为「无链接」，
绝不影响工具调用本身的返回。
"""

import asyncio
import time
from typing import Any, Dict, List, Optional, Set

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from config import get_logger

logger = get_logger(__name__)

# 触发分享检查的工具（execute 用于脚本/转换工具生成 HTML 的场景）
_SHARE_TOOLS = frozenset({"write_file", "edit_file", "execute"})

_HTML_EXTS = ("html", "htm")

# 追加到工具结果末尾的提示（引导 Agent 在最终回复中告知用户）
_NOTICE_HEADER = (
    "\n\n---\n"
    "📎 分享链接已生成（免登录，可直接在浏览器打开，支持 iframe 嵌入）：\n"
)


def _is_html_path(path: str) -> bool:
    return (path or "").rsplit(".", 1)[-1].lower() in _HTML_EXTS


def _tool_messages_of(result: Any) -> List[ToolMessage]:
    """从工具调用返回中取出 ToolMessage（兼容 ToolMessage 与 Command 两种形态）。"""
    if isinstance(result, ToolMessage):
        return [result]
    update = getattr(result, "update", None)
    if isinstance(update, dict):
        messages = update.get("messages")
        if isinstance(messages, list):
            return [m for m in messages if isinstance(m, ToolMessage)]
    return []


def _append_text_to_result(result: Any, text: str) -> Any:
    """把提示文本追加到工具结果（ToolMessage 或 Command 内的 ToolMessage）。"""
    if not text:
        return result

    def _append(msg: ToolMessage) -> ToolMessage:
        content = msg.content
        if isinstance(content, str):
            new_content: Any = content + text
        elif isinstance(content, list):
            new_content = list(content) + [{"type": "text", "text": text}]
        else:
            new_content = f"{content}{text}"
        try:
            return msg.model_copy(update={"content": new_content})
        except Exception:
            return msg

    if isinstance(result, ToolMessage):
        return _append(result)

    update = getattr(result, "update", None)
    if isinstance(update, dict):
        messages = update.get("messages")
        if isinstance(messages, list):
            new_messages = [
                _append(m) if isinstance(m, ToolMessage) else m for m in messages
            ]
            try:
                return result.model_copy(
                    update={"update": {**update, "messages": new_messages}}
                )
            except Exception:
                update["messages"] = new_messages  # 兜底：原地替换
    return result


def _is_tool_success(messages: List[ToolMessage]) -> bool:
    """工具是否成功（deepagents 写文件工具以 status 标记；无 status 时按文本判定）。"""
    for msg in messages:
        status = getattr(msg, "status", None)
        if status == "success":
            return True
        if status == "error":
            continue
        content = msg.content if isinstance(msg.content, str) else str(msg.content or "")
        if not any(
            tok in content.lower()
            for tok in ("error", "failed", "denied", "permission", "cannot", "unable")
        ):
            return True
    return False


class HtmlShareLinkMiddleware(AgentMiddleware):
    """HTML 分享链接实时注入中间件。

    配置参数（均由 factory 按会话注入）：
        user_id / session_id: 会话标识（分享 token 归属与工作区定位）
        share_links: virtual_path → share_url 的共享缓存（与 DeepAgentWrapper 同一 dict）
        share_locks: f"{user_id}:{virtual_path}" → asyncio.Lock（串行化查重+插入）
        announced: 已注入过链接的 virtual_path 集合（与 wrapper 共享，防跨轮重复注入）
        enabled: 总开关（HTML_SHARE_AUTO_INJECT_ENABLED）
    """

    def __init__(
        self,
        *,
        user_id: str,
        session_id: str,
        share_links: Dict[str, str],
        share_locks: dict,
        announced: Optional[Set[str]] = None,
        enabled: bool = True,
    ):
        super().__init__()
        self._user_id = user_id
        self._session_id = session_id
        self._share_links = share_links
        self._share_locks = share_locks
        self._announced = announced if announced is not None else set()
        self._enabled = enabled

    # ── 主钩子 ────────────────────────────────────────────────────────────

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        """执行工具 → 若产出完整 HTML 则创建分享链接并注入结果文本。"""
        started_at = time.time()
        result = await handler(request)
        if not self._enabled:
            return result

        try:
            call = getattr(request, "tool_call", None) or {}
            tool_name = (call.get("name") or "").strip()
            if tool_name not in _SHARE_TOOLS:
                return result

            messages = _tool_messages_of(result)
            if not messages or not _is_tool_success(messages):
                return result

            args = call.get("args") or {}
            if isinstance(args, str):
                args = {}

            vpaths = await self._candidate_vpaths(
                tool_name, args, messages, since_ts=started_at
            )
            if not vpaths:
                return result

            links = await self._ensure_links(vpaths)
            if links:
                return _append_text_to_result(result, self._build_notice(links))
        except Exception as e:
            logger.warning(f"[HtmlShareLink] 注入失败 tool={request}: {e}")
        return result

    # ── 候选路径收集 ──────────────────────────────────────────────────────

    async def _candidate_vpaths(
        self,
        tool_name: str,
        args: dict,
        messages: List[ToolMessage],
        *,
        since_ts: float,
    ) -> List[str]:
        """解析本次工具调用可能写出的 HTML 虚拟路径（去重，仅 .html/.htm）。"""
        from deep_agent_server.conversation_files import (
            agent_path_to_virtual,
            find_recent_document_files,
            resolve_agent_file_path,
            resolve_execute_output_tool,
        )

        result_text = " ".join(
            m.content if isinstance(m.content, str) else str(m.content or "")
            for m in messages
        )

        if tool_name == "execute":
            # 批量操作（解压/下载/依赖安装等）不产出用户文档，直接跳过
            if resolve_execute_output_tool(args) is None:
                return []
        else:
            try:
                agent_path = resolve_agent_file_path(
                    tool_name=tool_name,
                    args=args,
                    workspace_path=None,
                    session_id=self._session_id,
                    result_text=result_text,
                )
            except Exception:
                agent_path = ""

            if agent_path:
                vpath = agent_path_to_virtual(self._session_id, agent_path)
                return [vpath] if vpath and _is_html_path(vpath) else []
            return []

        # execute：命令内显式输出路径优先
        candidates: List[str] = []
        try:
            agent_path = resolve_agent_file_path(
                tool_name="execute",
                args=args,
                workspace_path=None,
                session_id=self._session_id,
                result_text=result_text,
            )
        except Exception:
            agent_path = ""
        if agent_path and _is_html_path(agent_path):
            vpath = agent_path_to_virtual(self._session_id, agent_path)
            if vpath:
                candidates.append(vpath)

        # execute 兜底：扫描本次工具调用期间写入的文档文件（脚本内部写文件场景）
        if not candidates:
            try:
                for ap in find_recent_document_files(
                    self._user_id, self._session_id, since_ts=since_ts
                ):
                    if not _is_html_path(ap):
                        continue
                    vpath = agent_path_to_virtual(self._session_id, ap)
                    if vpath:
                        candidates.append(vpath)
            except Exception as e:
                logger.debug(f"[HtmlShareLink] execute 文档扫描失败: {e}")

        # 去重保序
        seen = set()
        unique = []
        for vpath in candidates:
            if vpath not in seen:
                seen.add(vpath)
                unique.append(vpath)
        return unique

    # ── 链接创建 ──────────────────────────────────────────────────────────

    async def _ensure_links(self, vpaths: List[str]) -> Dict[str, str]:
        """为每个候选路径确保分享链接存在（命中缓存则不重复创建）。"""
        from deep_agent_server.html_share_service import ensure_html_share

        links: Dict[str, str] = {}
        for vpath in vpaths:
            cached = self._share_links.get(vpath)
            if cached:
                links[vpath] = cached
                continue

            lock_key = f"{self._user_id}:{vpath}"
            lock = self._share_locks.setdefault(lock_key, asyncio.Lock())
            async with lock:
                # 锁内复检：并发工具调用可能已建好
                cached = self._share_links.get(vpath)
                if cached:
                    links[vpath] = cached
                    continue
                url = await ensure_html_share(
                    user_id=self._user_id,
                    session_id=self._session_id,
                    vpath=vpath,
                )
                if url:
                    self._share_links[vpath] = url
                    self._announced.add(vpath)
                    links[vpath] = url
                    logger.info(
                        f"[HtmlShareLink] 已注入分享链接 path={vpath} url={url}"
                    )
        return links

    @staticmethod
    def _build_notice(links: Dict[str, str]) -> str:
        lines = []
        for vpath, url in links.items():
            filename = vpath.rsplit("/", 1)[-1]
            lines.append(f"- {filename}（路径 `{vpath}`）：{url}")
        return (
            _NOTICE_HEADER
            + "\n".join(lines)
            + "\n\n请在最终回复中将上述链接告知用户（用户可免登录直接打开）。"
        )
