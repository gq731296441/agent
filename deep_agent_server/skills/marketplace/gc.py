"""builds TTL 与旧 releases GC、catalog 对账。"""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path

from config import get_logger
from config.config import app_config
from db.database import db_manager
from deep_agent_server.skills.marketplace.paths import get_builds_root
from deep_agent_server.skills.marketplace import storage as ms

logger = get_logger(__name__)


async def cleanup_expired_builds() -> int:
    """清理超期 build 目录；仍在流程中（待审等）的提交受保护，不会被误删。"""
    root = get_builds_root()
    if not root.is_dir():
        return 0
    ttl_days = int(getattr(app_config, "SKILL_BUILDS_TTL_DAYS", 14))
    cutoff = time.time() - ttl_days * 86400
    try:
        protected = set(await ms.list_active_submission_ids())
    except Exception:
        logger.exception("[SkillGC] load active submissions failed, skip cleanup")
        return 0
    removed = 0
    for d in root.iterdir():
        if not d.is_dir():
            continue
        if d.name in protected:
            continue
        try:
            if d.stat().st_mtime < cutoff:
                await asyncio.to_thread(shutil.rmtree, d, True)
                removed += 1
        except OSError:
            pass
    if removed:
        logger.info(f"[SkillGC] removed builds={removed}")
    return removed


async def gc_unreferenced_releases(keep_n: int = 5) -> int:
    """将无人引用且非 current 的旧 PUBLISHED 标 ARCHIVED 并 purge 文件。"""
    pool = await db_manager.get_pool()
    purged = 0
    async with pool.acquire() as conn:
        packages = await conn.fetch("SELECT id, current_published_version_id FROM skill_packages")
        for pkg in packages:
            pid = pkg["id"]
            current = pkg["current_published_version_id"]
            rows = await conn.fetch(
                """
                SELECT v.* FROM skill_versions v
                WHERE v.package_id = $1 AND v.status = 'PUBLISHED'
                  AND v.artifact_state = 'present'
                ORDER BY v.created_at DESC
                """,
                pid,
            )
            # 保留 current + 最近 keep_n + 仍被 installation 引用
            keep_ids = set()
            if current:
                keep_ids.add(str(current))
            for r in rows[:keep_n]:
                keep_ids.add(str(r["id"]))
            referenced = await conn.fetch(
                """
                SELECT DISTINCT version_id FROM skill_installations
                WHERE package_id = $1 AND version_id IS NOT NULL
                """,
                pid,
            )
            for r in referenced:
                keep_ids.add(str(r["version_id"]))

            for r in rows:
                vid = str(r["id"])
                if vid in keep_ids:
                    continue
                await conn.execute(
                    """
                    UPDATE skill_versions
                    SET status = 'ARCHIVED', artifact_state = 'purged', updated_at = now()
                    WHERE id = $1::uuid
                    """,
                    vid,
                )
                path = r.get("release_path") or ""
                if path:
                    p = Path(path)
                    if p.is_dir():
                        await asyncio.to_thread(shutil.rmtree, p, True)
                        purged += 1
    if purged:
        logger.info(f"[SkillGC] purged releases={purged}")
    return purged


async def reconcile_catalog() -> int:
    """补偿 catalog 投影：current 版本已 PUBLISHED 但 catalog 缺失/版本不一致的包重新投影。

    发布流程的 catalog 投影在 DB 事务之外，失败会留下「已发布但市场看不到」的包。
    """
    pool = await db_manager.get_pool()
    fixed = 0
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT p.id AS package_id, p.owner_id, p.origin, p.visibility,
                   p.current_published_version_id,
                   v.*
            FROM skill_packages p
            JOIN skill_versions v ON v.id = p.current_published_version_id
            LEFT JOIN skill_catalog c ON c.skill_id = p.id
            WHERE p.delisted = FALSE
              AND v.status = 'PUBLISHED'
              AND v.artifact_state = 'present'
              AND (c.skill_id IS NULL OR c.version IS DISTINCT FROM v.version)
            """
        )
    for r in rows:
        row = dict(r)
        package = {
            "owner_id": row.get("owner_id"),
            "origin": row.get("origin"),
            "visibility": row.get("visibility"),
        }
        version = {
            "package_id": row.get("package_id"),
            "version": row.get("version"),
            "release_path": row.get("release_path"),
            "name": row.get("name"),
            "name_zh": row.get("name_zh"),
            "name_en": row.get("name_en"),
            "description": row.get("description"),
            "tags": row.get("tags"),
            "author": row.get("author"),
        }
        try:
            await ms.project_catalog_from_version(version, package)
            fixed += 1
        except Exception:
            logger.exception(
                "[SkillGC] reconcile catalog failed pkg={}", row.get("package_id")
            )
    if fixed:
        logger.info(f"[SkillGC] reconciled catalog={fixed}")
    return fixed
