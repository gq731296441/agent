"""用户工作区配额：基于磁盘实际占用 + WORKSPACE_QUOTA_BYTES。

配额统计覆盖用户根目录下全部子目录（workspace/skills/artifacts/uploads），
写入前同步检查，写入后异步刷新 DB 中的 workspace_bytes_used。
"""

import asyncio
from typing import Optional

from config.config import app_config
from config import get_logger
from deep_agent_server.workspace import dir_size_bytes, get_user_root

logger = get_logger(__name__)


class QuotaExceededError(Exception):
    """工作区写入超过配额。"""


def get_workspace_limit() -> int:
    return int(app_config.WORKSPACE_QUOTA_BYTES)


def measure_workspace_bytes(user_id: str) -> int:
    """统计用户根目录下所有文件占用（含 workspace/skills/artifacts/uploads）。"""
    return dir_size_bytes(get_user_root(user_id))


def quota_error_message(user_id: str, used: int, additional: int) -> str:
    limit = get_workspace_limit()
    return (
        f"工作区配额不足：已用 {used} 字节，本次约需 {additional} 字节，"
        f"上限 {limit} 字节（WORKSPACE_QUOTA_BYTES）。"
        f"请删除旧文件或调大配额。"
    )


def check_additional_bytes(user_id: str, additional_bytes: int) -> Optional[str]:
    """同步检查是否可再写入 additional_bytes；超限返回错误文案，否则 None。"""
    if additional_bytes <= 0:
        return None
    used = measure_workspace_bytes(user_id)
    if used + additional_bytes > get_workspace_limit():
        return quota_error_message(user_id, used, additional_bytes)
    return None


def utf8_len(text: str) -> int:
    return len((text or "").encode("utf-8"))


def check_edit_delta(
    user_id: str,
    *,
    current_content: str,
    old_string: str,
    new_string: str,
    replace_all: bool,
) -> Optional[str]:
    content = current_content or ""
    count = content.count(old_string)
    if count == 0:
        return None
    if replace_all:
        new_content = content.replace(old_string, new_string)
    else:
        if count != 1:
            return None
        new_content = content.replace(old_string, new_string, 1)
    delta = utf8_len(new_content) - utf8_len(content)
    return check_additional_bytes(user_id, delta)


async def refresh_workspace_usage(user_id: str) -> int:
    """将磁盘实际占用同步到 user_resource_quotas.workspace_bytes_used。"""
    from db import deep_storage

    used = measure_workspace_bytes(user_id)
    await deep_storage.ensure_user_quota(user_id)
    await deep_storage.update_quota_used(user_id, used)
    return used


def schedule_refresh_usage(user_id: str) -> None:
    """在 sync 写入完成后异步刷新 DB 统计。"""

    async def _run() -> None:
        try:
            await refresh_workspace_usage(user_id)
        except Exception as e:
            logger.warning(f"[Quota] refresh failed user={user_id}: {e}")

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run())
    except RuntimeError:
        asyncio.run(_run())
