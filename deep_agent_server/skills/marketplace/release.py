"""原子发布与崩溃恢复。"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any, Dict

from config import get_logger
from db.database import db_manager
from deep_agent_server.skills.marketplace.hash_payload import hash_skill_payload, write_meta
from deep_agent_server.skills.marketplace.paths import publishing_dir, release_dir
from deep_agent_server.skills.marketplace import storage as ms
from deep_agent_server.skills.marketplace.semver import compare_semver, is_strictly_greater

logger = get_logger(__name__)


def _prepare_release_sync(
    unpacked_src: Path, pub: Path, final: Path, expected: str
) -> str:
    """同步准备发布目录：copytree → 哈希复核 → 写 meta → rename。

    全部为阻塞 I/O（整目录遍历哈希），必须在 to_thread 中执行，避免阻塞事件循环。
    """
    if pub.exists():
        shutil.rmtree(pub, ignore_errors=True)
    pub.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(unpacked_src, pub)

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
    return actual


async def _publish_tx(
    pool,
    version_id: str,
    version: Dict[str, Any],
    review: Dict[str, Any],
    package_id: str,
    semver: str,
    final: Path,
    actual: str,
    reviewer_id: str,
    review_comment: str,
) -> None:
    """发布 DB 事务：审核决策 + 版本晋升 + 版本号占用 + current 指针 + 可见性。"""
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

            # 审核决策与发布同事务：仅当仍为 pending 时置为 approved，
            # 否则说明已被他人处理，中止发布。
            decided = await conn.execute(
                """
                UPDATE skill_reviews
                SET reviewer_id = $2, decision = 'approved',
                    comment = $3, decided_at = now()
                WHERE id = $1::uuid AND decision = 'pending'
                """,
                review["id"],
                reviewer_id,
                review_comment,
            )
            if not decided.endswith("1"):
                raise ValueError("审核记录已被处理，请刷新后重试")

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


async def atomic_publish(
    version_id: str,
    unpacked_src: Path,
    reviewer_id: str = "",
    review_comment: str = "",
) -> Dict[str, Any]:
    """
    先哈希 .publishing → rename → 复核 → DB 事务（含递增检查与终身占用）。

    review 决策在同一 DB 事务内完成，保证「审批通过」与「发布成功」原子：
    文件准备失败时事务从未开启，review 仍为 pending，可直接重试。
    """
    version = await ms.get_version(version_id)
    if not version:
        raise ValueError("version 不存在")
    if version["status"] != "REVIEW_PENDING":
        raise ValueError(f"version 状态不可发布: {version['status']}")

    review = await ms.get_pending_review_for_version(version_id)
    if not review:
        raise ValueError("无待审记录，禁止发布")

    package_id = version["package_id"]
    semver = version["version"]
    pub = publishing_dir(package_id, semver)
    final = release_dir(package_id, semver)

    expected = version.get("content_sha256") or await asyncio.to_thread(
        hash_skill_payload, unpacked_src
    )
    actual = await asyncio.to_thread(
        _prepare_release_sync, unpacked_src, pub, final, expected
    )

    pool = await db_manager.get_pool()
    # DB 事务（失败时清理已落盘的 release 目录，避免出现「有文件无记录」的孤儿）
    try:
        await _publish_tx(
            pool, version_id, version, review, package_id, semver,
            final, actual, reviewer_id, review_comment,
        )
    except Exception:
        logger.exception(
            "[SkillPublish] tx failed version={} pkg={} v={}, cleanup final",
            version_id, package_id, semver,
        )
        await asyncio.to_thread(shutil.rmtree, final, True)
        raise

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
    """启动恢复：清理 releases 下残留的 .publishing 临时目录。"""
    from deep_agent_server.skills.marketplace.paths import get_repo_root

    root = get_repo_root()
    if not root.is_dir():
        return 0
    cleaned = 0
    for package_dir in root.iterdir():
        if not package_dir.is_dir():
            continue
        releases = package_dir / "releases"
        if not releases.is_dir():
            continue
        for d in releases.iterdir():
            if d.name.endswith(".publishing"):
                await asyncio.to_thread(shutil.rmtree, d, True)
                cleaned += 1
    if cleaned:
        logger.info("[SkillRecover] cleaned orphaned publishing dirs={}", cleaned)
    return cleaned
