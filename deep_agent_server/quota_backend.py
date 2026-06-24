"""带配额检查的 FilesystemBackend 包装。

代理 deepagents FilesystemBackend 的 write/edit 操作，
写入前校验 WORKSPACE_QUOTA_BYTES，成功后异步刷新 DB 用量统计并登记文件索引。
"""

import asyncio
import mimetypes
from pathlib import Path
from typing import Any, Optional

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import EditResult, WriteResult

from config import get_logger
from deep_agent_server.quota_service import (
    check_additional_bytes,
    check_edit_delta,
    schedule_refresh_usage,
    utf8_len,
)

logger = get_logger(__name__)


def _mime_for_path(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def schedule_file_index(
    user_id: str,
    session_id: Optional[str],
    rel_path: str,
    abs_path: Path,
    *,
    file_kind: str = "workspace",
    conversation_id: Optional[str] = None,
) -> None:
    """write/edit 成功后异步 upsert deep_workspace_files 索引。"""

    async def _run() -> None:
        try:
            from deep_agent_server.workspace import compute_sha256
            from db import deep_storage

            if not abs_path.is_file():
                return
            await deep_storage.upsert_workspace_file_index(
                user_id,
                session_id,
                rel_path,
                abs_path.stat().st_size,
                compute_sha256(abs_path),
                file_kind=file_kind,
                mime_type=_mime_for_path(abs_path),
                storage_path=str(abs_path),
                conversation_id=conversation_id,
            )
        except Exception as e:
            logger.warning(f"[Quota] file index failed user={user_id} path={rel_path}: {e}")

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run())
    except RuntimeError:
        asyncio.run(_run())


class QuotaFilesystemBackend:
    """在 write / edit 前校验 WORKSPACE_QUOTA_BYTES，成功后刷新 DB 用量。"""

    def __init__(
        self,
        inner: FilesystemBackend,
        user_id: str,
        *,
        session_id: Optional[str] = None,
        file_kind: str = "workspace",
        route_prefix: str = "",
        conversation_id: Optional[str] = None,
    ):
        self._inner = inner
        self._user_id = user_id
        self._session_id = session_id
        self._file_kind = file_kind
        self._route_prefix = (route_prefix or "").strip("/")
        self._root_dir = Path(getattr(inner, "root_dir", "") or "")
        self._conversation_id = conversation_id

    def _virtual_rel_path(self, file_path: str) -> str:
        p = (file_path or "").replace("\\", "/").lstrip("/")
        if self._route_prefix:
            pref = self._route_prefix
            if p.startswith(pref + "/"):
                p = p[len(pref) + 1 :]
            elif p == pref:
                p = ""
        return p

    def _disk_path(self, file_path: str) -> Optional[Path]:
        if not self._root_dir:
            return None
        rel = self._virtual_rel_path(file_path)
        if not rel:
            return None
        return self._root_dir / rel

    def _after_write_success(self, file_path: str) -> None:
        schedule_refresh_usage(self._user_id)
        disk = self._disk_path(file_path)
        if disk is None:
            return
        schedule_file_index(
            self._user_id,
            self._session_id,
            self._virtual_rel_path(file_path),
            disk,
            file_kind=self._file_kind,
            conversation_id=self._conversation_id,
        )

    def write(self, file_path: str, content: str) -> WriteResult:
        err = check_additional_bytes(self._user_id, utf8_len(content))
        if err:
            return WriteResult(error=err)
        result = self._inner.write(file_path, content)
        if not result.error:
            self._after_write_success(file_path)
        return result

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        read_res = self._inner.read(file_path)
        if not read_res.error and read_res.file_data is not None:
            err = check_edit_delta(
                self._user_id,
                current_content=read_res.file_data.content,
                old_string=old_string,
                new_string=new_string,
                replace_all=replace_all,
            )
            if err:
                return EditResult(error=err)
        result = self._inner.edit(file_path, old_string, new_string, replace_all)
        if not result.error:
            self._after_write_success(file_path)
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
