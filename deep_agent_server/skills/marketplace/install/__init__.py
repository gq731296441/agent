"""固定版本安装：.installing / .previous 两次 rename + 恢复。"""

from __future__ import annotations

import asyncio
import json
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from config import get_logger
from db.database import db_manager
from deep_agent_server.skills.dirs import install_dir_name
from deep_agent_server.skills.marketplace.hash_payload import hash_skill_payload
from deep_agent_server.skills.marketplace.paths import release_dir
from deep_agent_server.skills.marketplace import storage as ms
from deep_agent_server.workspace import ensure_user_dirs, get_skills_dir, sync_skill_to_sandbox_bind_path

logger = get_logger(__name__)


class InstallError(Exception):
    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code


@asynccontextmanager
async def _install_lock(user_id: str, package_id: str):
    """基于 PG advisory lock 的安装互斥（跨进程/多实例有效）。

    必须独占一条连接持有锁：若放进事务块，连接归还后锁会悬挂。
    """
    pool = await db_manager.get_pool()
    conn = await pool.acquire()
    key = f"skill-install:{user_id}:{package_id}"
    got = False
    try:
        got = await conn.fetchval("SELECT pg_try_advisory_lock(hashtext($1))", key)
        if not got:
            raise InstallError("该技能正在安装中，请稍候", 409)
        yield
    finally:
        if got:
            try:
                await conn.fetchval("SELECT pg_advisory_unlock(hashtext($1))", key)
            except Exception:
                logger.warning(
                    "[SkillInstall] advisory unlock failed user={} pkg={}",
                    user_id, package_id,
                )
        await pool.release(conn)


def _install_files_sync(
    src: Path,
    installing: Path,
    final: Path,
    previous: Path,
    expected: str,
    meta_file: dict,
) -> str:
    """同步执行安装文件操作：copytree → 哈希校验 → 写 meta → 两次 rename。"""
    if installing.exists():
        shutil.rmtree(installing, ignore_errors=True)
    shutil.copytree(src, installing, ignore=shutil.ignore_patterns("_meta"))
    meta_dir = installing / "_meta"
    if meta_dir.exists():
        shutil.rmtree(meta_dir, ignore_errors=True)

    actual = hash_skill_payload(installing)
    if expected and actual != expected:
        shutil.rmtree(installing, ignore_errors=True)
        raise InstallError("安装哈希不匹配", 500)

    (installing / ".install-meta.json").write_text(
        json.dumps(meta_file, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if previous.exists():
        shutil.rmtree(previous, ignore_errors=True)
    if final.exists():
        final.rename(previous)
    installing.rename(final)
    if previous.exists():
        shutil.rmtree(previous, ignore_errors=True)
    return actual


async def install_pinned_version(
    user_id: str,
    package_id: str,
    *,
    version_id: Optional[str] = None,
) -> dict:
    pkg = await ms.get_package(package_id)
    from db import deep_storage

    catalog = await deep_storage.get_skill_catalog(package_id)

    # 解析目标 version
    if version_id:
        ver = await ms.get_version(version_id)
    elif pkg and pkg.get("current_published_version_id"):
        ver = await ms.get_version(str(pkg["current_published_version_id"]))
    else:
        # 兼容旧 catalog-only 包：回退旧 installer 路径由调用方处理
        ver = None

    if pkg and pkg.get("delisted"):
        raise InstallError("该 Skill 已下架，无法安装", 410)

    if ver:
        if ver["status"] != "PUBLISHED" or ver.get("artifact_state") == "purged":
            raise InstallError("版本不可安装", 404)
        if pkg and pkg.get("visibility") == "private" and pkg.get("owner_id") != user_id:
            if not await deep_storage.is_admin(user_id):
                raise InstallError("私人包不可安装", 403)

        src = Path(ver.get("release_path") or "")
        if not src.is_dir():
            src = release_dir(package_id, ver["version"])
        if not src.is_dir() or not (src / "SKILL.md").is_file():
            raise InstallError("发布目录缺失", 404)

        expected = ver.get("content_sha256") or ""
        ensure_user_dirs(user_id)
        skills_root = get_skills_dir(user_id)
        # 安装目录使用英文名（SKILL.md name），与 agent 技能清单保持一致
        dir_name = install_dir_name(catalog or ver, package_id)
        final = skills_root / dir_name
        installing = skills_root / f"{dir_name}.installing"
        previous = skills_root / f"{dir_name}.previous"

        # 同一用户对同一包并发安装会互相覆盖 .installing，用 advisory lock 串行化。
        async with _install_lock(user_id, package_id):
            return await _do_install(
                user_id, package_id, ver, catalog, src, final, installing, previous, expected
            )

    # 无 marketplace version：旧路径
    from deep_agent_server.skills.installer import install_skill_for_user

    return await install_skill_for_user(user_id, package_id, overwrite=True)


async def _do_install(
    user_id: str,
    package_id: str,
    ver: dict,
    catalog: Optional[dict],
    src: Path,
    final: Path,
    installing: Path,
    previous: Path,
    expected: str,
) -> dict:
    """在持有安装锁的前提下执行安装（原 install_pinned_version 主体）。"""
    from db import deep_storage

    skills_root = get_skills_dir(user_id)
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO skill_installations (user_id, package_id, status)
                VALUES ($1, $2, 'INSTALLING')
                ON CONFLICT (user_id, package_id) DO UPDATE
                SET status = 'INSTALLING'
                """,
                user_id,
                package_id,
            )

        try:
            meta_src = catalog or ver or {}
            meta_file = {
                "package_id": package_id,
                "skill_id": package_id,
                "name_en": (meta_src.get("name_en") or "").strip(),
                "name_zh": (meta_src.get("name_zh") or "").strip(),
                "version_id": ver["id"],
                "version": ver["version"],
                "content_sha256": expected,
            }
            actual = await asyncio.to_thread(
                _install_files_sync, src, installing, final, previous, expected, meta_file
            )

            # 清理旧布局目录（目录名=package_id，迁移遗留）
            legacy = skills_root / package_id
            if legacy != final and legacy.is_dir():
                from deep_agent_server.skills.dirs import read_install_meta

                if str(read_install_meta(legacy).get("package_id") or "") == package_id:
                    await asyncio.to_thread(shutil.rmtree, legacy, True)
                    logger.info(
                        "[SkillInstall] removed legacy dir user={} pkg={} dir={}",
                        user_id,
                        package_id,
                        legacy,
                    )

            sync_skill_to_sandbox_bind_path(user_id, package_id, force=True)

            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE skill_installations
                    SET version_id = $3::uuid, content_sha256 = $4, version = $5,
                        install_dir = $6, status = 'READY', installed_at = now()
                    WHERE user_id = $1 AND package_id = $2
                    """,
                    user_id,
                    package_id,
                    ver["id"],
                    actual,
                    ver["version"],
                    str(final),
                )
            # 同步旧表
            await deep_storage.install_skill_record(
                user_id,
                package_id,
                ver.get("name") or package_id,
                str(final),
                version=ver["version"],
            )
            return {
                "package_id": package_id,
                "version": ver["version"],
                "version_id": ver["id"],
                "content_sha256": actual,
                "status": "READY",
            }
        except InstallError:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE skill_installations SET status = 'INSTALL_ERROR'
                    WHERE user_id = $1 AND package_id = $2
                    """,
                    user_id,
                    package_id,
                )
            # 恢复 previous
            if previous.exists() and not final.exists():
                previous.rename(final)
            raise
        except Exception as e:
            if previous.exists() and not final.exists():
                previous.rename(final)
            raise InstallError(str(e), 500) from e


async def recover_install_dirs(user_id: str, package_id: str) -> None:
    from db import deep_storage

    catalog = await deep_storage.get_skill_catalog(package_id)
    candidates = [install_dir_name(catalog or {}, package_id), package_id]
    skills_root = get_skills_dir(user_id)
    for dir_name in dict.fromkeys(candidates):
        final = skills_root / dir_name
        installing = skills_root / f"{dir_name}.installing"
        previous = skills_root / f"{dir_name}.previous"
        if not (final.exists() or installing.exists() or previous.exists()):
            continue

        if previous.exists() and not final.exists():
            previous.rename(final)
            return
        if final.exists() and installing.exists():
            shutil.rmtree(installing, ignore_errors=True)
            return
        if previous.exists() and installing.exists():
            # 优先恢复旧版
            if not final.exists():
                previous.rename(final)
            shutil.rmtree(installing, ignore_errors=True)
        return
