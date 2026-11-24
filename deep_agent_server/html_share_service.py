"""HTML 分享链接统一创建入口。

供这些调用：
  - builtin_tools/html_share_link.py     —— Agent 主动获取或创建分享链接
  - deep_agent_server/adapter.py         —— Agent 写完 HTML 后自动创建（跨轮次兜底）
  - deep_agent_server/middleware/html_share_link.py —— 工具结果内实时注入链接
  - api/workspace_api.py                 —— 前端手动点「分享」（走自己的流程，
                                            但共用 write_share_snapshot）

设计要点：
  - 幂等：同一文件复用同一 token（四级复用链），快照内容未变化时不重复构建
  - 快照随创建即写：分享链接不再依赖「首次访问」才建快照，
    源文件被改名/删除后链接依然可访问（修复未访问链接的失效缺口）
  - 失败不抛出：调用方（工具中间件 / 后台任务）不应因分享失败而中断
"""

import asyncio
import hashlib
from typing import Optional

from config import get_logger
from config.config import app_config
from db.html_share_store import (
    create_or_reuse_share_token,
    get_share_token,
    update_share_source,
)
from deep_agent_server.html_share_snapshot import (
    SNAPSHOT_DIR_NAME,
    SNAPSHOT_INDEX_NAME,
    missing_assets_changed,
    normalize_missing_assets,
    write_share_snapshot,
)
from deep_agent_server.workspace_service import resolve_virtual_path

logger = get_logger(__name__)

# 快照构建超时（秒）：超时则放弃快照，仍返回可用链接（访问时懒重建兜底）
SNAPSHOT_BUILD_TIMEOUT = 20.0


def share_url_for_token(token: str) -> str:
    """token → 对外可访问的完整分享 URL。"""
    public_base = (app_config.AGENTA_PUBLIC_BASE_URL or "").rstrip("/")
    return f"{public_base}/workspace/html-share/{token}"


def is_complete_html(content: str) -> bool:
    """是否为完整 HTML（含闭合标签）—— 避免骨架阶段误建分享。"""
    return "</body>" in content or "</html>" in content


async def ensure_html_share(
    *,
    user_id: str,
    session_id: str,
    vpath: str,
    build_snapshot: bool = True,
) -> str:
    """确保 HTML 文件有可用的分享链接（含资源快照），返回完整 URL。

    幂等：重复调用复用同一 token；仅当快照缺失或源内容变化时才重建快照。
    失败时返回空串（调用方按「无链接」处理，不影响主流程）。
    """
    try:
        abs_path, _rel, _sid, file_kind = resolve_virtual_path(user_id, vpath)
        if file_kind != "workspace" or not abs_path.is_file():
            return ""

        content = abs_path.read_text(encoding="utf-8", errors="replace")
        if not is_complete_html(content):
            return ""

        sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        token, _reused, _reason = await create_or_reuse_share_token(
            user_id=user_id,
            session_id=session_id,
            file_path=vpath,
            sha256=sha,
            expires_in_hours=None,
        )

        if build_snapshot:
            await _ensure_snapshot(
                user_id=user_id,
                session_id=session_id,
                vpath=vpath,
                token=token,
                content=content,
                sha=sha,
            )

        return share_url_for_token(token)
    except Exception as e:
        logger.warning(f"[HtmlShare] ensure_html_share 失败 path={vpath}: {e}")
        return ""


async def _ensure_snapshot(
    *,
    user_id: str,
    session_id: str,
    vpath: str,
    token: str,
    content: str,
    sha: str,
) -> None:
    """快照缺失或源内容变化时重建（带超时保护，失败降级不抛）。"""
    try:
        record = await get_share_token(token)
        last_sha = record.get("last_source_sha256") if record else None
        snapshot_vpath = (
            f"workspace/{session_id}/{SNAPSHOT_DIR_NAME}/{token}/{SNAPSHOT_INDEX_NAME}"
        )
        snap_abs, *_ = resolve_virtual_path(user_id, snapshot_vpath)

        agent_id = None
        try:
            from db import deep_storage

            agent_id = await deep_storage.get_session_agent_id(session_id)
        except Exception:
            pass

        # 快照新鲜度 = HTML 内容指纹 + 缺失资源状态：
        #   - missing is None：旧数据/状态未知 → 重建一次（自愈历史上
        #     「HTML 先写完、CSS/JS 尚未落盘」时生成的快照）
        #   - 缺失资源签名变化（文件后来出现/被替换）→ 重建
        missing = normalize_missing_assets(
            record.get("snapshot_missing_assets") if record else None
        )
        if (
            last_sha == sha
            and snap_abs.is_file()
            and missing is not None
            and not missing_assets_changed(user_id, missing, agent_id=agent_id)
        ):
            return  # 快照已是最新，跳过（高频写入时避免重复复制资源）

        async with asyncio.timeout(SNAPSHOT_BUILD_TIMEOUT):
            result = await write_share_snapshot(
                user_id=user_id,
                html_text=content,
                vpath=vpath,
                session_id=session_id,
                token=token,
                agent_id=agent_id,
            )
            await update_share_source(
                token, vpath, sha, missing_assets=result.missing
            )
    except asyncio.TimeoutError:
        logger.warning(
            f"[HtmlShare] 快照构建超时（{SNAPSHOT_BUILD_TIMEOUT}s）token={token} "
            f"path={vpath}，链接仍可用，访问时懒重建"
        )
    except Exception as e:
        logger.warning(f"[HtmlShare] 快照构建失败 token={token} path={vpath}: {e}")


async def resolve_share_target(user_id: str, vpath: str) -> Optional[str]:
    """校验虚拟路径可作为分享源（存在 + 完整 HTML），返回绝对路径字符串。

    供中间件在「决定是否注入链接」前做廉价预检。
    """
    try:
        abs_path, _rel, _sid, file_kind = resolve_virtual_path(user_id, vpath)
    except ValueError:
        return None
    if file_kind != "workspace" or not abs_path.is_file():
        return None
    return str(abs_path)
