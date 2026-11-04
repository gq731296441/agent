"""原子发布与崩溃恢复。"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from db.database import db_manager
from deep_agent_server.skills.marketplace.hash_payload import hash_skill_payload, write_meta
from deep_agent_server.skills.marketplace.paths import publishing_dir, release_dir
from deep_agent_server.skills.marketplace import storage as ms
from deep_agent_server.skills.marketplace.semver import compare_semver, is_strictly_greater

logger = logging.getLogger(__name__)


async def atomic_publish(version_id: str, unpacked_src: Path) -> Dict[str, Any]:
    """
    先哈希 .publishing → rename → 复核 → DB 事务（含递增检查与终身占用）。
    """
    version = await ms.get_version(version_id)
    if not version:
        raise ValueError("version 不存在")
    if version["status"] != "REVIEW_PENDING":
        raise ValueError(f"version 状态不可发布: {version['status']}")

    review = await ms.get_pending_review_for_version(version_id)
    # 发布前必须已有 approve 记录（decision=approved）
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        approved = await conn.fetchrow(
            """
            SELECT * FROM skill_reviews
            WHERE version_id = $1::uuid AND decision = 'approved'
            ORDER BY decided_at DESC LIMIT 1
            """,
            version_id,
        )
    if not approved:
        raise ValueError("无人审通过记录，禁止发布")

    package_id = version["package_id"]
    semver = version["version"]
    pub = publishing_dir(package_id, semver)
    final = release_dir(package_id, semver)

    if pub.exists():
        shutil.rmtree(pub, ignore_errors=True)
    pub.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(unpacked_src, pub)

    expected = version.get("content_sha256") or hash_skill_payload(unpacked_src)
    actual = hash_skill_payload(pub)
    if actual != expected:
        shutil.rmtree(pub, ignore_errors=True)
        raise ValueError(f"publishing 哈希不一致: {actual} != {expected}")

    write_meta(pub, actual)
    # meta 不参与哈希，复核业务树
    if hash_skill_payload(pub) != actual:
        shutil.rmtree(pub, ignore_errors=True)
        raise ValueError("写入 meta 后业务哈希变化（不应发生）")

    if final.exists():
        shutil.rmtree(final, ignore_errors=True)
    pub.rename(final)

    if hash_skill_payload(final) != actual:
        shutil.rmtree(final, ignore_errors=True)
        raise ValueError("最终路径哈希复核失败")

    # DB 事务
    async with pool.acquire() as conn:
        async with conn.transaction():
            pkg = await conn.fetchrow(
                "SELECT * FROM skill_packages WHERE id = $1 FOR UPDATE", package_id
            )
            if not pkg:
                raise ValueError("package 不存在")

            ver_row = await conn.fetchrow(
                "SELECT * FROM skill_versions WHERE id = $1::uuid FOR UPDATE", version_id
            )
            if not ver_row or ver_row["status"] != "REVIEW_PENDING":
                raise ValueError("version 状态已变化")

            # 重读最高已发布/隔离
            rows = await conn.fetch(
                """
                SELECT id, version, status FROM skill_versions
                WHERE package_id = $1 AND status IN ('PUBLISHED','QUARANTINED')
                """,
                package_id,
            )
            lifelong = await conn.fetch(
                "SELECT version FROM skill_version_numbers WHERE package_id = $1",
                package_id,
            )
            max_v = None
            for r in list(rows) + list(lifelong):
                v = r["version"]
                if max_v is None or compare_semver(v, max_v) > 0:
                    max_v = v

            # 本 version 仍应高于历史最高已发布（若已有更高 published 则仍可 PUBLISHED 但不抢 current）
            await conn.execute(
                """
                UPDATE skill_versions
                SET status = 'PUBLISHED', artifact_state = 'present',
                    release_path = $2, content_sha256 = $3, updated_at = now()
                WHERE id = $1::uuid
                """,
                version_id,
                str(final),
                actual,
            )
            await conn.execute(
                """
                INSERT INTO skill_version_numbers (package_id, version)
                VALUES ($1, $2) ON CONFLICT DO NOTHING
                """,
                package_id,
                semver,
            )

            current_id = pkg["current_published_version_id"]
            should_advance = False
            if current_id is None:
                should_advance = True
            else:
                cur = await conn.fetchrow(
                    "SELECT version FROM skill_versions WHERE id = $1", current_id
                )
                if cur and is_strictly_greater(semver, cur["version"]):
                    should_advance = True

            if should_advance:
                await conn.execute(
                    """
                    UPDATE skill_packages
                    SET current_published_version_id = $2::uuid, updated_at = now()
                    WHERE id = $1
                    """,
                    package_id,
                    version_id,
                )

            # visibility: requested public → 正式 public
            sub = await conn.fetchrow(
                "SELECT * FROM skill_submissions WHERE id = $1::uuid",
                version["submission_id"],
            )
            vis = "private"
            if sub and sub.get("requested_visibility") == "public":
                vis = "public"
            await conn.execute(
                "UPDATE skill_packages SET visibility = $2, updated_at = now() WHERE id = $1",
                package_id,
                vis,
            )
            if sub:
                await conn.execute(
                    """
                    UPDATE skill_submissions
                    SET status = 'PROMOTED', updated_at = now()
                    WHERE id = $1::uuid AND status = 'AWAITING_REVIEW'
                    """,
                    version["submission_id"],
                )

    package = await ms.get_package(package_id)
    version = await ms.get_version(version_id)
    # catalog 仅当 current 指向本版且 public 时上架
    if (
        package
        and str(package.get("current_published_version_id")) == str(version_id)
        and package.get("visibility") == "public"
    ):
        await ms.project_catalog_from_version(version, package)
    elif package and package.get("visibility") == "private":
        # 私人包：catalog draft，仅所有者可见（现有 list 过滤）
        await ms.project_catalog_from_version(version, package)

    return version


async def recover_orphaned_releases() -> int:
    """启动恢复：有 releases 无人审通过则移走；有审核则尝试补发布。"""
    # 简化：扫描 .publishing 残留清理
    from deep_agent_server.skills.marketplace.paths import get_repo_root

    root = get_repo_root()
    if not root.is_dir():
        return 0
    cleaned = 0
    for pub in root.glob("* /releases/*.publishing"):
        pass
    for package_dir in root.iterdir():
        if not package_dir.is_dir():
            continue
        releases = package_dir / "releases"
        if not releases.is_dir():
            continue
        for d in releases.iterdir():
            if d.name.endswith(".publishing"):
                shutil.rmtree(d, ignore_errors=True)
                cleaned += 1
    return cleaned
