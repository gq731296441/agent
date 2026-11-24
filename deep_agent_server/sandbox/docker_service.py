"""Docker 实现的 SandboxService。

生命周期：每 (user_id, session_id) 一个 Docker 容器，支持 pause/resume 与 LRU 全站并发上限。
容器内 /workspace 挂载会话 workspace/{session_id}；勾选 skill 通过 ro bind mount 到
{CONTAINER_SKILL_MOUNT_PREFIX}/{{id}}/（独立于 workspace，见 skills/session_mount.py）。
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config.config import app_config
from config import get_logger
from db import deep_storage
from deep_agent_server.sandbox.admission import (
    SandboxAdmissionTimeout,
    sandbox_admission_queue,
)
from deep_agent_server.sandbox.exceptions import SandboxUserLimitExceeded
from deep_agent_server.sandbox.models import SandboxHandle, SandboxStatus
from deep_agent_server.sandbox.env import build_container_user_env, build_exec_user_env, shell_env_prefix
from deep_agent_server.sandbox.service import SandboxService
from deep_agent_server.skills.mount_paths import CONTAINER_SKILL_MOUNT_PREFIX
from deep_agent_server.skills.session_mount import purge_host_sandbox_skills_dir
from deep_agent_server.sandbox.docker_client import get_docker_client
from deep_agent_server.workspace import (
    ensure_session_dirs,
    ensure_session_workspace_permissions,
    get_sandbox_workspace_dir,
    resolve_skill_bind_host_path,
)

logger = get_logger(__name__)

_session_locks: Dict[str, asyncio.Lock] = {}
_session_locks_guard = asyncio.Lock()


async def _get_session_lock(user_id: str, session_id: str) -> asyncio.Lock:
    key = f"{user_id}:{session_id}"
    async with _session_locks_guard:
        if key not in _session_locks:
            _session_locks[key] = asyncio.Lock()
        return _session_locks[key]


class DockerSandboxService(SandboxService):
    """每 session 一个 Docker 容器，支持 pause/resume 与全站 LRU 并发上限。"""

    def __init__(self):
        self._docker = None

    def _get_docker(self):
        if self._docker is None:
            from deep_agent_server.sandbox import is_docker_sdk_available
            if not is_docker_sdk_available():
                raise RuntimeError(
                    "未安装 docker Python 包，请 pip install docker 或设置 SANDBOX_ENABLED=false"
                )
            self._docker = get_docker_client()
        return self._docker

    async def get_backend_factory(
        self,
        user_id: str,
        session_id: str,
        store=None,
        user_token: Optional[str] = None,
        *,
        agent_skills_root=None,
        agent_id: Optional[str] = None,
        selected_skill_ids: Optional[list[str]] = None,
    ):
        from deep_agent_server.backend_factory import build_sandbox_backend_factory

        handle = await self.ensure_running(user_id, session_id)
        return build_sandbox_backend_factory(
            user_id,
            session_id,
            handle.container_id,
            store=store,
            user_token=user_token,
            agent_skills_root=agent_skills_root,
            agent_id=agent_id,
            selected_skill_ids=selected_skill_ids,
        )

    async def ensure_with_skills(
        self,
        user_id: str,
        session_id: str,
        *,
        mounted_skill_ids: List[str],
        selection_key: str,
        force_recreate: bool = False,
        agent_id: Optional[str] = None,
    ) -> bool:
        """
        确保容器 bind mount 与 selection_key 一致。

        Returns:
            True 若新建/重建了容器；False 若复用已有容器。
        """
        lock = await _get_session_lock(user_id, session_id)
        async with lock:
            try:
                row = await self._get_db_session(user_id, session_id)
                stored_key = (row or {}).get("selection_key")

                if not force_recreate and stored_key == selection_key and row:
                    cid = row.get("container_id")
                    status = row.get("status") or "stopped"
                    # 复用前必须核对容器实际 bind 的 skill 集合：selection_key 只由技能
                    # id 集合决定，容器可能因历史 bind 失败/技能源当时不可用而缺项，
                    # 此时仅比对 key 会把"缺挂载"的容器一直复用下去。
                    if status == SandboxStatus.PAUSED.value and cid:
                        await asyncio.to_thread(self._unpause_sync, cid)
                        bound = await self.resolve_verified_mounted_skill_ids(
                            cid,
                            user_id=user_id,
                            mounted_skill_ids=mounted_skill_ids,
                            agent_id=agent_id,
                        )
                        if bound is not None:
                            await sandbox_admission_queue.acquire(user_id, session_id)
                            await self._update_status(user_id, session_id, SandboxStatus.RUNNING)
                            await self._touch(user_id, session_id, selection_key, bound)
                            await self._finalize_running_container(user_id, session_id, cid)
                            return False
                    elif cid and await self._container_running(cid):
                        bound = await self.resolve_verified_mounted_skill_ids(
                            cid,
                            user_id=user_id,
                            mounted_skill_ids=mounted_skill_ids,
                            agent_id=agent_id,
                        )
                        if bound is not None:
                            await sandbox_admission_queue.force_hold(user_id, session_id)
                            await self._touch(user_id, session_id, selection_key, bound)
                            await self._finalize_running_container(user_id, session_id, cid)
                            return False
                    elif status == SandboxStatus.STOPPED.value and cid:
                        await sandbox_admission_queue.acquire(user_id, session_id)
                        if await asyncio.to_thread(self._start_sync, cid):
                            bound = await self.resolve_verified_mounted_skill_ids(
                                cid,
                                user_id=user_id,
                                mounted_skill_ids=mounted_skill_ids,
                                agent_id=agent_id,
                            )
                            if bound is not None:
                                await self._touch(user_id, session_id, selection_key, bound)
                                await self._finalize_running_container(user_id, session_id, cid)
                                return False

                has_row = row is not None and bool(row.get("container_id"))
                if not has_row:
                    active = await deep_storage.count_running_sandboxes_for_user(user_id)
                    limit = app_config.SANDBOX_MAX_PER_USER
                    if active >= limit:
                        running = await deep_storage.list_running_session_ids_for_user(user_id)
                        raise SandboxUserLimitExceeded(
                            f"已达每用户沙箱上限 {limit}，请等待其他会话结束或停止后再试",
                            running_sessions=running,
                        )

                old_cid = row.get("container_id") if row else None
                await sandbox_admission_queue.acquire(user_id, session_id)
                try:
                    await asyncio.to_thread(self._destroy_named_container, user_id, session_id)
                    if old_cid:
                        await asyncio.to_thread(self._destroy_container_sync, old_cid)
                    await asyncio.to_thread(purge_host_sandbox_skills_dir, user_id, session_id)
                    await ensure_session_dirs(user_id, session_id)
                    cid, bound_skill_ids = await asyncio.to_thread(
                        self._create_container,
                        user_id,
                        session_id,
                        mounted_skill_ids or [],
                        agent_id,
                    )
                    ws_path = str(get_sandbox_workspace_dir(user_id, session_id))
                    await self._persist_container(
                        user_id,
                        session_id,
                        cid,
                        ws_path,
                        selection_key=selection_key,
                        mounted_skill_ids=bound_skill_ids,
                    )
                    await self._finalize_running_container(user_id, session_id, cid)
                except Exception:
                    await sandbox_admission_queue.release(session_id, user_id)
                    raise
                logger.info(
                    f"[Sandbox] recreated with skills user={user_id} session={session_id} "
                    f"skills={bound_skill_ids} key={selection_key[:12]}"
                )
                return True
            finally:
                from deep_agent_server.sandbox.status_notify import notify_sandbox_status_changed

                await notify_sandbox_status_changed(user_id)

    async def _finalize_running_container(
        self, user_id: str, session_id: str, container_id: str
    ) -> None:
        """宿主机 + 容器内统一工作区权限：/workspace 可写，.sandbox 只读。

        注意：不调用 ensure_session_dirs —— 调用方（ensure_with_skills/ensure_running）
        已在容器创建前确保目录存在，此处只需修正权限。"""
        await asyncio.to_thread(
            ensure_session_workspace_permissions, user_id, session_id
        )
        await asyncio.to_thread(
            self._fix_container_workspace_permissions, container_id, user_id
        )

    def _fix_container_workspace_permissions(
        self, container_id: str, user_id: str = ""
    ) -> None:
        """容器内 chmod：工作区可写；不 chmod bind mount 的 skill 目录。"""
        try:
            self._exec_in_container(
                container_id,
                "chmod a+rwx /workspace 2>/dev/null || true; "
                "if [ -f /workspace/.sandbox/.mount_manifest.json ]; then "
                "chmod 644 /workspace/.sandbox/.mount_manifest.json 2>/dev/null; "
                "elif [ -f /workspace/.sandbox/skills/.mount_manifest.json ]; then "
                "chmod 644 /workspace/.sandbox/skills/.mount_manifest.json 2>/dev/null; "
                "fi",
                user_id=user_id,
            )
        except Exception as e:
            logger.warning(
                f"[Sandbox] workspace 权限修正失败 container={container_id[:12]}: {e}"
            )

    async def ensure_running(
        self,
        user_id: str,
        session_id: str,
        *,
        mounted_skill_ids: Optional[List[str]] = None,
        selection_key: Optional[str] = None,
    ) -> SandboxHandle:
        """
        确保沙箱 running：复用已有容器 / unpause / 或新建。

        新建前检查每用户 SANDBOX_MAX_PER_USER；达上限且本 session 无容器时硬拒绝。
        全站达 SANDBOX_MAX_CONCURRENT 时 LRU pause 最久未活跃容器。
        """
        row = await self._get_db_session(user_id, session_id)
        if mounted_skill_ids is None and row:
            raw = row.get("mounted_skill_ids")
            if isinstance(raw, list):
                mounted_skill_ids = [str(x) for x in raw]
            elif isinstance(raw, str):
                try:
                    mounted_skill_ids = json.loads(raw)
                except json.JSONDecodeError:
                    mounted_skill_ids = []
            else:
                mounted_skill_ids = []
        if selection_key is None and row:
            selection_key = row.get("selection_key")

        lock = await _get_session_lock(user_id, session_id)
        async with lock:
            row = await self._get_db_session(user_id, session_id)
            if row and row.get("container_id"):
                cid = row["container_id"]
                status = row.get("status") or "stopped"
                if status == SandboxStatus.PAUSED.value:
                    await sandbox_admission_queue.acquire(user_id, session_id)
                    await asyncio.to_thread(self._unpause_sync, cid)
                    await self._update_status(user_id, session_id, SandboxStatus.RUNNING)
                    await self._touch(user_id, session_id)
                    await self._finalize_running_container(user_id, session_id, cid)
                    await deep_storage.insert_audit_log(
                        user_id, "sandbox_resumed", target=cid[:12], session_id=session_id
                    )
                    return SandboxHandle(
                        user_id=user_id,
                        session_id=session_id,
                        container_id=cid,
                        status=SandboxStatus.RUNNING,
                    )
                if await self._container_running(cid):
                    await sandbox_admission_queue.force_hold(user_id, session_id)
                    await self._touch(user_id, session_id)
                    await self._finalize_running_container(user_id, session_id, cid)
                    return SandboxHandle(
                        user_id=user_id,
                        session_id=session_id,
                        container_id=cid,
                        status=SandboxStatus.RUNNING,
                    )
                if status == SandboxStatus.STOPPED.value:
                    await sandbox_admission_queue.acquire(user_id, session_id)
                    if await asyncio.to_thread(self._start_sync, cid):
                        await self._touch(user_id, session_id)
                        await self._finalize_running_container(user_id, session_id, cid)
                        await deep_storage.insert_audit_log(
                            user_id, "sandbox_started", target=cid[:12], session_id=session_id
                        )
                        return SandboxHandle(
                            user_id=user_id,
                            session_id=session_id,
                            container_id=cid,
                            status=SandboxStatus.RUNNING,
                        )

            found = await asyncio.to_thread(
                self._find_container_by_name, user_id, session_id
            )
            if found:
                cid, docker_status = found
                container_ready = True
                if docker_status == "paused":
                    container_ready = await asyncio.to_thread(self._unpause_sync, cid)
                elif docker_status in ("exited", "created"):
                    container_ready = await asyncio.to_thread(self._start_sync, cid)
                if container_ready:
                    old_cid = row.get("container_id") if row else None
                    if old_cid and old_cid != cid:
                        await asyncio.to_thread(self._destroy_container_sync, old_cid)
                    ws_path = str(get_sandbox_workspace_dir(user_id, session_id))
                    await self._persist_container(user_id, session_id, cid, ws_path)
                    await self._touch(user_id, session_id)
                    await self._finalize_running_container(user_id, session_id, cid)
                    return SandboxHandle(
                        user_id=user_id,
                        session_id=session_id,
                        container_id=cid,
                        status=SandboxStatus.RUNNING,
                    )
                # 容器在但启不起来（已被外部删除/暂停等）：既不复用它，也不上报
                # running —— 落到下面的重建分支。此前 `_start_sync` 的返回值被丢弃，
                # "启动失败"会被当作 running 落库并返回给调用方。
                logger.warning(
                    f"[Sandbox] 已有容器无法启动，转重建 user={user_id} "
                    f"session={session_id} container={cid[:12]} status={docker_status}"
                )

            has_row = row is not None and bool(row.get("container_id"))
            if not has_row:
                active = await deep_storage.count_running_sandboxes_for_user(user_id)
                limit = app_config.SANDBOX_MAX_PER_USER
                if active >= limit:
                    running = await deep_storage.list_running_session_ids_for_user(user_id)
                    raise SandboxUserLimitExceeded(
                        f"已达每用户沙箱上限 {limit}，请等待其他会话结束或停止后再试",
                        running_sessions=running,
                    )

            old_cid = row.get("container_id") if row else None
            await sandbox_admission_queue.acquire(user_id, session_id)
            await ensure_session_dirs(user_id, session_id)
            cid, bound_skill_ids = await asyncio.to_thread(
                self._create_container,
                user_id,
                session_id,
                mounted_skill_ids or [],
            )
            if old_cid and old_cid != cid:
                await asyncio.to_thread(self._destroy_container_sync, old_cid)
            ws_path = str(get_sandbox_workspace_dir(user_id, session_id))
            await self._persist_container(
                user_id,
                session_id,
                cid,
                ws_path,
                selection_key=selection_key,
                mounted_skill_ids=bound_skill_ids,
            )
            await self._finalize_running_container(user_id, session_id, cid)
            return SandboxHandle(
                user_id=user_id,
                session_id=session_id,
                container_id=cid,
                status=SandboxStatus.RUNNING,
            )

    async def pause(self, user_id: str, session_id: str) -> bool:
        row = await self._get_db_session(user_id, session_id)
        if not row or not row.get("container_id"):
            return False
        cid = row["container_id"]
        try:
            await asyncio.to_thread(self._pause_sync, cid)
            await self._update_status(user_id, session_id, SandboxStatus.PAUSED)
            await sandbox_admission_queue.release(session_id, user_id)
            await deep_storage.insert_audit_log(
                user_id, "sandbox_paused", target=cid[:12], session_id=session_id
            )
            return True
        except Exception as e:
            logger.warning(f"[Sandbox] pause failed user={user_id} session={session_id}: {e}")
            return False

    async def resume(self, user_id: str, session_id: str) -> bool:
        row = await self._get_db_session(user_id, session_id)
        if not row or not row.get("container_id"):
            return False
        cid = row["container_id"]
        try:
            await sandbox_admission_queue.acquire(user_id, session_id)
            await asyncio.to_thread(self._unpause_sync, cid)
            await self._update_status(user_id, session_id, SandboxStatus.RUNNING)
            await self._touch(user_id, session_id)
            await deep_storage.insert_audit_log(
                user_id, "sandbox_resumed", target=cid[:12], session_id=session_id
            )
            return True
        except Exception as e:
            logger.warning(f"[Sandbox] resume failed user={user_id} session={session_id}: {e}")
            return False

    async def stop(self, user_id: str, session_id: str) -> None:
        row = await self._get_db_session(user_id, session_id)
        if not row:
            return
        cid = row.get("container_id")
        if cid:
            if app_config.SANDBOX_REMOVE_ON_STOP:
                await asyncio.to_thread(self._destroy_container_sync, cid)
            else:
                await asyncio.to_thread(self._stop_sync, cid)
                await self._set_stopped_ttl(user_id, session_id)
                await sandbox_admission_queue.release(session_id, user_id)
                return
        if cid and app_config.SANDBOX_REMOVE_ON_STOP:
            await deep_storage.clear_sandbox_session(user_id, session_id)
        elif row.get("container_id") or row.get("status") != SandboxStatus.STOPPED.value:
            await self._set_stopped_ttl(user_id, session_id)
        await sandbox_admission_queue.release(session_id, user_id)

    async def stop_all_for_user(self, user_id: str) -> None:
        """停止用户全部 session 沙箱。

        逐 session 隔离异常：某个 session 失败不得中断后续 session 的停止。
        否则首个失败之后的沙箱全部残留，DB 行留在 running 状态，用户随后
        会被误报"已达沙箱并发上限"却没有任何活容器。
        """
        rows = await deep_storage.list_sandbox_sessions_for_user(user_id)
        for row in rows:
            sid = row.get("session_id")
            if not sid:
                continue
            try:
                await self.stop(user_id, sid)
            except Exception as e:
                logger.warning(
                    f"[Sandbox] 停止会话沙箱失败 user={user_id} session={sid}: {e}"
                )

    async def release(self, user_id: str, session_id: str) -> bool:
        """用完即释：docker stop，status→stopped，写入 stopped TTL。

        返回 False = 未释放（无沙箱记录，或 docker stop 失败）。
        docker stop 失败时**不写** stopped TTL / 审计日志：否则 DB 与前端
        都会显示已停止，而容器其实还在跑（这正是"释放仍扣配额"的成因）。
        """
        row = await self._get_db_session(user_id, session_id)
        if not row or not row.get("container_id"):
            return False
        cid = row["container_id"]
        if row.get("status") == SandboxStatus.STOPPED.value:
            return True
        stopped = await asyncio.to_thread(self._stop_sync, cid)
        if not stopped:
            logger.warning(
                f"[Sandbox] release 未生效（容器未能停止）user={user_id} "
                f"session={session_id} container={cid[:12]}"
            )
            return False
        await asyncio.to_thread(purge_host_sandbox_skills_dir, user_id, session_id)
        await self._set_stopped_ttl(user_id, session_id)
        await sandbox_admission_queue.release(session_id, user_id)
        await deep_storage.insert_audit_log(
            user_id, "sandbox_released", target=cid[:12], session_id=session_id
        )
        logger.debug(
            f"[Sandbox] released user={user_id} session={session_id} container={cid[:12]}"
        )
        return True

    async def cleanup_expired(self, batch: int = 50) -> int:
        rows = await deep_storage.list_expired_sandbox_sessions(limit=batch)
        removed = 0
        for row in rows:
            uid = row["user_id"]
            sid = row["session_id"]
            cid = row.get("container_id")
            # 单行失败不得阻塞后续行：查询按 ttl_at ASC 排序，坏行会一直排在最前，
            # 缺少 per-row 隔离时整个批次（含紧随其后的孤儿清理）每轮都被它饿死，
            # 结果是容器只增不减。
            try:
                if cid:
                    await asyncio.to_thread(self._destroy_container_sync, cid)
                await asyncio.to_thread(purge_host_sandbox_skills_dir, uid, sid)
                await deep_storage.clear_sandbox_session(uid, sid)
                await deep_storage.insert_audit_log(
                    uid, "sandbox_expired", target=(cid or "")[:12], session_id=sid
                )
                removed += 1
            except Exception as e:
                logger.warning(
                    f"[Sandbox] 清理过期沙箱失败 user={uid} session={sid} "
                    f"container={(cid or '')[:12]}: {e}"
                )

        # 清理孤儿容器（Docker 中存在但 DB 无记录，且超过 1 小时）
        try:
            known_ids = set(await deep_storage.list_all_sandbox_container_ids())
            orphan_removed = await asyncio.to_thread(
                self._cleanup_orphan_containers, known_ids
            )
            removed += orphan_removed
        except Exception as e:
            logger.warning(f"[Sandbox] 孤儿容器清理失败: {e}")
        return removed

    def _cleanup_orphan_containers(self, known_container_ids: set) -> int:
        """清理 Docker 中存在但 DB 无记录的孤儿容器（已创建超过 1 小时）。

        `known_container_ids` 是 DB 中登记的全部容器 ID（由调用方在 async 侧查出）。
        判据必须是"DB 是否有记录"，**不能用容器自身的状态**：
          - 按状态判断会误删 DB 里合法的 stopped/paused 沙箱；
          - 原先 `if status == "running": continue` 又让"DB 行已删但容器仍在跑"
            这类真正需要回收的孤儿永远漏网。
        保留 1 小时年龄门槛，保护 `_create_container` 与 `_persist_container`
        之间尚未落库的窗口。
        """
        try:
            containers = self._get_docker().containers.list(
                all=True,
                filters={"name": "deep-agent-"},
            )
        except Exception as e:
            logger.warning(f"[Sandbox] 查询孤儿容器失败: {e}")
            return 0

        removed = 0
        orphan_max_age_seconds = 3600  # 1 小时

        for c in containers:
            if c.id in known_container_ids:
                continue  # DB 有记录 → 不是孤儿
            try:
                created_ts = c.attrs.get("Created", "")
                if created_ts:
                    created_dt = datetime.fromisoformat(created_ts.replace("Z", "+00:00"))
                    age = (datetime.now(timezone.utc) - created_dt).total_seconds()
                    if age < orphan_max_age_seconds:
                        continue
            except Exception as e:
                logger.debug(
                    f"[Sandbox] 容器 {c.name} Created 时间解析失败，按超龄处理: {e}"
                )
            try:
                c.remove(force=True)
                logger.info(
                    f"[Sandbox] 清理孤儿容器 {c.name} (status={c.status})"
                )
                removed += 1
            except Exception as e:
                logger.warning(f"[Sandbox] 清理孤儿容器失败 {c.name}: {e}")

        if removed:
            logger.info(f"[Sandbox] 本轮共清理 {removed} 个孤儿容器")
        return removed

    async def health_check(self, user_id: str, session_id: str) -> bool:
        row = await self._get_db_session(user_id, session_id)
        if not row or not row.get("container_id"):
            return False
        return await self._container_running(row["container_id"])

    async def pause_oldest_if_at_capacity(self) -> int:
        max_n = app_config.SANDBOX_MAX_CONCURRENT
        running_count = await deep_storage.count_running_sandboxes()
        if running_count < max_n:
            return 0
        to_pause = running_count - max_n + 1
        rows = await deep_storage.list_running_sandboxes_by_lru(limit=to_pause)
        paused = 0
        failed = 0
        for row in rows:
            uid = row["user_id"]
            sid = row["session_id"]
            if await self.pause(uid, sid):
                paused += 1
            else:
                failed += 1
        if failed:
            # 驱逐没做成，全站并发会实际越过 SANDBOX_MAX_CONCURRENT —— 此前这里
            # 静默忽略失败，调用方也无从知道"该驱逐但没驱逐成功"。
            logger.warning(
                f"[Sandbox] LRU 驱逐未完全生效：计划暂停 {to_pause} 个，"
                f"成功 {paused} 个，失败 {failed} 个（全站并发可能超限）"
            )
        return paused

    async def get_user_status(self, user_id: str) -> dict:
        sessions = await deep_storage.list_sandbox_sessions_for_user(user_id)
        running_count = sum(1 for s in sessions if s.get("status") == "running")
        max_per_user = app_config.SANDBOX_MAX_PER_USER
        return {
            "user_id": user_id,
            "max_per_user": max_per_user,
            "running_count": running_count,
            "available_slots": max(0, max_per_user - running_count),
            "sessions": [
                {
                    "session_id": s.get("session_id"),
                    "status": s.get("status"),
                    "ttl_at": s.get("ttl_at").isoformat() if s.get("ttl_at") else None,
                    "last_active_at": (
                        s.get("last_active_at").isoformat()
                        if s.get("last_active_at")
                        else None
                    ),
                }
                for s in sessions
            ],
        }

    async def _get_db_session(self, user_id: str, session_id: str):
        return await deep_storage.get_sandbox_session(user_id, session_id)

    async def _touch(
        self,
        user_id: str,
        session_id: str,
        selection_key: Optional[str] = None,
        mounted_skill_ids: Optional[List[str]] = None,
    ):
        ttl = datetime.now(timezone.utc) + app_config.get_sandbox_idle_ttl()
        from db.database import db_manager
        pool = await db_manager.get_pool()
        async with pool.acquire() as conn:
            if selection_key is not None:
                await conn.execute(
                    """
                    UPDATE deep_sandbox_sessions SET
                        last_active_at = CURRENT_TIMESTAMP,
                        ttl_at = $3,
                        status = 'running',
                        selection_key = $4,
                        mounted_skill_ids = $5::jsonb
                    WHERE user_id = $1 AND session_id = $2::uuid
                    """,
                    user_id,
                    session_id,
                    ttl,
                    selection_key,
                    json.dumps(mounted_skill_ids or []),
                )
            else:
                await conn.execute(
                    """
                    UPDATE deep_sandbox_sessions SET
                        last_active_at = CURRENT_TIMESTAMP, ttl_at = $3, status = 'running'
                    WHERE user_id = $1 AND session_id = $2::uuid
                    """,
                    user_id,
                    session_id,
                    ttl,
                )

    async def _set_stopped_ttl(self, user_id: str, session_id: str) -> None:
        ttl = datetime.now(timezone.utc) + app_config.get_sandbox_stopped_ttl()
        from db.database import db_manager
        pool = await db_manager.get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE deep_sandbox_sessions SET
                    status = 'stopped',
                    ttl_at = $3,
                    last_active_at = CURRENT_TIMESTAMP
                WHERE user_id = $1 AND session_id = $2::uuid
                """,
                user_id,
                session_id,
                ttl,
            )

    async def _update_status(
        self, user_id: str, session_id: str, status: SandboxStatus
    ):
        from db.database import db_manager
        pool = await db_manager.get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE deep_sandbox_sessions SET status = $3
                WHERE user_id = $1 AND session_id = $2::uuid
                """,
                user_id,
                session_id,
                status.value,
            )

    @staticmethod
    def _container_name(user_id: str, session_id: str) -> str:
        sid_short = str(session_id).replace("-", "")[:8]
        return f"deep-agent-{user_id}-{sid_short}"[:63]

    def _find_container_by_name(
        self, user_id: str, session_id: str
    ) -> Optional[tuple[str, str]]:
        name = self._container_name(user_id, session_id)
        try:
            c = self._get_docker().containers.get(name)
            if c.status in ("running", "paused", "exited", "created"):
                return c.id, c.status
        except Exception:
            return None
        return None

    def _resolve_skill_bind_volumes(
        self,
        user_id: str,
        mounted_skill_ids: Optional[List[str]],
        *,
        agent_id: Optional[str] = None,
        log: bool = True,
    ) -> Tuple[Dict[str, Dict[str, str]], List[str]]:
        """解析 skill 的 bind mount：返回 (volumes 片段, 实际挂载成功的 skill_id 列表)。

        源目录不存在/为空（resolve_skill_bind_host_path 返回 None）时该技能被跳过，
        只能出现在返回值里，调用方不得把请求列表原样回写 DB —— 否则 DB 记录与
        容器实际挂载不一致，后续 selection_key 比对会误判成"已挂载"而永不重建。
        """
        volumes: Dict[str, Dict[str, str]] = {}
        bound: List[str] = []
        for raw in mounted_skill_ids or []:
            sid = str(raw).strip().strip("/\\")
            if not sid or ".." in sid:
                continue
            host_skill = resolve_skill_bind_host_path(user_id, sid, agent_id=agent_id)
            if host_skill is None:
                if log:
                    logger.warning(
                        f"[Sandbox] skill 目录不存在或为空，跳过 mount: "
                        f"user={user_id} skill={sid}"
                    )
                continue
            bind_src = str(host_skill.resolve())
            destination = f"{CONTAINER_SKILL_MOUNT_PREFIX}/{sid}"
            if bind_src in volumes:
                # volumes 以源路径为 key，同一源只能挂一个 destination
                if log:
                    logger.warning(
                        f"[Sandbox] skill bind 源已被占用，跳过 mount: "
                        f"user={user_id} skill={sid} src={bind_src}"
                    )
                continue
            volumes[bind_src] = {"bind": destination, "mode": "ro"}
            bound.append(sid)
            if log:
                logger.info(
                    f"[Sandbox] skill volume bind user={user_id} skill={sid} "
                    f"src={bind_src} -> {destination}"
                )
        return volumes, bound

    def _build_container_run_kwargs(
        self,
        user_id: str,
        session_id: str,
        mounted_skill_ids: Optional[List[str]] = None,
        *,
        agent_id: Optional[str] = None,
    ) -> Tuple[dict, List[str]]:
        """返回 (containers.run 参数, 实际挂载成功的 skill_id 列表)。"""
        ws_host = str(get_sandbox_workspace_dir(user_id, session_id))
        os.makedirs(ws_host, exist_ok=True)

        volumes = {
            ws_host: {"bind": "/workspace", "mode": "rw"},
        }

        skill_volumes, bound_skill_ids = self._resolve_skill_bind_volumes(
            user_id, mounted_skill_ids, agent_id=agent_id
        )
        volumes.update(skill_volumes)

        pip_conf = app_config.SANDBOX_PIP_CONF_PATH
        if pip_conf is None:
            pip_conf = os.path.expanduser("~/.pip/pip.conf")
        if pip_conf and os.path.isfile(pip_conf):
            volumes[pip_conf] = {"bind": "/root/.config/pip/pip.conf", "mode": "ro"}

        environment: dict[str, str] = {}
        for key in (
            "http_proxy",
            "https_proxy",
            "no_proxy",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
        ):
            val = os.environ.get(key)
            if val:
                environment[key] = val

        environment.update(build_container_user_env(user_id))

        # 确保容器内使用 UTF-8 locale，避免中文文件名在 shell 输出中乱码
        environment.setdefault("LANG", "C.UTF-8")
        environment.setdefault("LC_ALL", "C.UTF-8")

        kwargs: dict = {
            "image": app_config.SANDBOX_IMAGE,
            "command": "sleep infinity",
            "detach": True,
            "mem_limit": app_config.SANDBOX_MEMORY_LIMIT,
            "pids_limit": int(getattr(app_config, "SANDBOX_PIDS_LIMIT", 512) or 0),
            "nano_cpus": int(app_config.SANDBOX_CPU_LIMIT * 1e9),
            "volumes": volumes,
            "working_dir": "/workspace",
            "name": self._container_name(user_id, session_id),
            "remove": False,
            # 内核 4.19 seccomp 过滤阻止 pthread_create，必须 unconfined
            "security_opt": ["seccomp=unconfined"],
        }
        if environment:
            kwargs["environment"] = environment

        if app_config.SANDBOX_HARDEN:
            kwargs["read_only"] = True
            kwargs["tmpfs"] = {
                "/tmp": "size=256m",
                "/root": "size=128m",
            }
            kwargs["cap_drop"] = ["ALL"]
            kwargs["security_opt"] = kwargs.get("security_opt", []) + ["no-new-privileges:true"]

        return kwargs, bound_skill_ids

    def _create_container(
        self,
        user_id: str,
        session_id: str,
        mounted_skill_ids: Optional[List[str]] = None,
        agent_id: Optional[str] = None,
    ) -> Tuple[str, List[str]]:
        """创建容器，返回 (container_id, 实际挂载成功的 skill_id 列表)。"""
        client = self._get_docker()
        name = self._container_name(user_id, session_id)

        try:
            existing = client.containers.get(name)
            existing.remove(force=True)
        except Exception:
            pass

        run_kwargs, bound_skill_ids = self._build_container_run_kwargs(
            user_id, session_id, mounted_skill_ids,
            agent_id=agent_id,
        )
        container = client.containers.run(**run_kwargs)
        requested = [
            s for s in (str(x).strip().strip("/\\") for x in (mounted_skill_ids or []))
            if s and ".." not in s
        ]
        if len(bound_skill_ids) != len(requested):
            logger.warning(
                f"[Sandbox] 部分 skill 未能 bind，容器内 /mnt/skills 将缺项: "
                f"user={user_id} session={session_id} "
                f"请求={requested} 实际={bound_skill_ids}"
            )
        return container.id, bound_skill_ids

    def _destroy_named_container(self, user_id: str, session_id: str) -> None:
        name = self._container_name(user_id, session_id)
        try:
            c = self._get_docker().containers.get(name)
            c.remove(force=True)
        except Exception:
            pass

    async def _persist_container(
        self,
        user_id: str,
        session_id: str,
        container_id: str,
        mounted_workspace_path: str,
        *,
        selection_key: Optional[str] = None,
        mounted_skill_ids: Optional[List[str]] = None,
    ):
        ttl = datetime.now(timezone.utc) + app_config.get_sandbox_idle_ttl()
        from db.database import db_manager
        pool = await db_manager.get_pool()
        skill_json = json.dumps(mounted_skill_ids or [])
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO deep_sandbox_sessions (
                    user_id, session_id, container_id, status, provider,
                    ttl_at, mounted_workspace_path, selection_key, mounted_skill_ids
                )
                VALUES ($1, $2::uuid, $3, 'running', 'docker', $4, $5, $6, $7::jsonb)
                ON CONFLICT (user_id, session_id) DO UPDATE SET
                    container_id = EXCLUDED.container_id,
                    status = 'running',
                    last_active_at = CURRENT_TIMESTAMP,
                    ttl_at = EXCLUDED.ttl_at,
                    mounted_workspace_path = EXCLUDED.mounted_workspace_path,
                    selection_key = EXCLUDED.selection_key,
                    mounted_skill_ids = EXCLUDED.mounted_skill_ids
                """,
                user_id,
                session_id,
                container_id,
                ttl,
                mounted_workspace_path,
                selection_key,
                skill_json,
            )
        await deep_storage.insert_audit_log(
            user_id, "sandbox_created", target=container_id[:12], session_id=session_id
        )

    async def _container_running(self, container_id: str) -> bool:
        try:
            return await asyncio.to_thread(self._check_running, container_id)
        except Exception as e:
            # 注意：这里把"查询失败"（daemon 故障 / ssh 隧道断开）与"容器未运行"
            # 归为同一结果 false，上层会因此走重建分支。记日志以便区分二者，
            # 否则 Docker 故障会被伪装成"容器不存在"。
            logger.warning(
                f"[Sandbox] 查询容器状态失败（非容器不存在）"
                f"container={container_id[:12]}: {e}"
            )
            return False

    def _check_running(self, container_id: str) -> bool:
        c = self._get_docker().containers.get(container_id)
        return c.status == "running"

    def _container_skill_mount_ids(self, container_id: str) -> Optional[set]:
        """读回容器实际挂载的 skill id 集合（/mnt/skills/* 的 Destination）。

        返回 None 表示查询失败（区别于"确实没有挂载"），调用方应视为不一致。
        """
        try:
            container = self._get_docker().containers.get(container_id)
            mounts = (container.attrs or {}).get("Mounts") or []
        except Exception as e:
            logger.warning(
                f"[Sandbox] 读取容器挂载失败 container={container_id[:12]}: {e}"
            )
            return None
        prefix = CONTAINER_SKILL_MOUNT_PREFIX + "/"
        mounted = set()
        for m in mounts:
            dest = str(m.get("Destination") or "")
            if not dest.startswith(prefix):
                continue
            sid = dest[len(prefix):].strip("/")
            if sid:
                mounted.add(sid)
        return mounted

    async def resolve_verified_mounted_skill_ids(
        self,
        container_id: str,
        *,
        user_id: str,
        mounted_skill_ids: Optional[List[str]],
        agent_id: Optional[str] = None,
    ) -> Optional[List[str]]:
        """校验容器 skill 挂载；一致时返回应持久化的实际挂载列表，不一致返回 None。

        期望集合取"当前能解析出 bind 源的技能"（与 _resolve_skill_bind_volumes 同一
        判定），因此源目录不可用的技能不会造成"重建后仍不一致"的重建抖动；而技能
        新增/卸载、bind 丢失会被识别为不一致，由调用方重建容器自愈。
        """
        _, expected = self._resolve_skill_bind_volumes(
            user_id, mounted_skill_ids, agent_id=agent_id, log=False
        )
        actual = await asyncio.to_thread(self._container_skill_mount_ids, container_id)
        if actual is None:
            return None
        if actual == set(expected):
            return expected
        logger.warning(
            f"[Sandbox] 容器 skill 挂载与当前选择不一致，需重建 "
            f"container={container_id[:12]} user={user_id} "
            f"实际={sorted(actual)} 期望={sorted(expected)}"
        )
        return None

    def _pause_sync(self, container_id: str) -> bool:
        """docker pause。返回是否成功——容器可能已被外部删除。"""
        try:
            c = self._get_docker().containers.get(container_id)
            if c.status == "running":
                c.pause()
            return True
        except Exception as e:
            logger.warning(f"[Sandbox] docker pause: {e}")
            return False

    def _unpause_sync(self, container_id: str) -> bool:
        """docker unpause。返回是否成功——容器可能已被外部删除。

        此前无异常保护，容器被外部删除时会在 ensure_running 里硬失败，
        绕过按名重建的兜底分支。
        """
        try:
            c = self._get_docker().containers.get(container_id)
            if c.status == "paused":
                c.unpause()
            return True
        except Exception as e:
            logger.warning(f"[Sandbox] docker unpause: {e}")
            return False

    def _start_sync(self, container_id: str) -> bool:
        try:
            c = self._get_docker().containers.get(container_id)
            if c.status == "running":
                return True
            if c.status == "paused":
                c.unpause()
                return True
            c.start()
            return True
        except Exception as e:
            logger.warning(f"[Sandbox] docker start: {e}")
            return False

    def _stop_sync(self, container_id: str) -> bool:
        """docker stop。返回是否成功——调用方（release）据此判断真实结果。"""
        try:
            c = self._get_docker().containers.get(container_id)
            c.stop(timeout=10)
            return True
        except Exception as e:
            logger.warning(f"[Sandbox] docker stop: {e}")
            return False

    def _destroy_container_sync(self, container_id: str) -> bool:
        """docker rm -f。返回是否成功。"""
        try:
            c = self._get_docker().containers.get(container_id)
            c.remove(force=True)
            return True
        except Exception as e:
            msg = str(e)
            # TTL / 外部已删掉容器后再 recreate 时常见，不算故障
            if "404" in msg or "No such container" in msg:
                logger.debug(f"[Sandbox] docker rm 跳过（容器已不存在）: {container_id[:12]}")
            else:
                logger.warning(f"[Sandbox] docker rm: {e}")
            return False

    def _exec_in_container(
        self,
        container_id: str,
        cmd: str,
        *,
        user_id: str = "",
        user_token: Optional[str] = None,
    ) -> str:
        c = self._get_docker().containers.get(container_id)
        exec_env = build_exec_user_env(user_id, user_token)
        prefix = shell_env_prefix(exec_env)
        kwargs: dict = {"workdir": "/workspace", "demux": False}
        if exec_env:
            kwargs["environment"] = exec_env
        cmd = prefix + cmd if prefix else cmd
        exit_code, output = c.exec_run(
            ["bash", "-lc", cmd],
            user="root",
            **kwargs,
        )
        if exit_code != 0:
            raise RuntimeError(output.decode("utf-8", errors="replace"))
        return output.decode("utf-8", errors="replace")


SandboxManager = DockerSandboxService
