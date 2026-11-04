"""builds TTL 与旧 releases GC。"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from config.config import app_config
from db.database import db_manager
from deep_agent_server.skills.marketplace.paths import get_builds_root, get_repo_root

logger = logging.getLogger(__name__)


async def cleanup_expired_builds() -> int:
    root = get_builds_root()
    if not root.is_dir():
        return 0
    ttl_days = int(getattr(app_config, "SKILL_BUILDS_TTL_DAYS", 14))
    cutoff = time.time() - ttl_days * 86400
    removed = 0
    for d in root.iterdir():
        if not d.is_dir():
            continue
        try:
            if d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
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
                        shutil.rmtree(p, ignore_errors=True)
                        purged += 1
    if purged:
        logger.info(f"[SkillGC] purged releases={purged}")
    return purged
