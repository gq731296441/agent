"""Skill 市场写模型 DB 操作。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from db.database import db_manager


def _row(r) -> Optional[Dict[str, Any]]:
    if r is None:
        return None
    d = dict(r)
    for k, v in list(d.items()):
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
        elif isinstance(v, uuid4.__class__):
            d[k] = str(v)
    # uuid
    for k, v in list(d.items()):
        if type(v).__name__ == "UUID":
            d[k] = str(v)
    return d


async def ensure_marketplace_schema(conn) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS skill_packages (
            id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            origin TEXT NOT NULL DEFAULT 'user',
            default_subscribe BOOLEAN NOT NULL DEFAULT FALSE,
            visibility TEXT NOT NULL DEFAULT 'private',
            current_published_version_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS skill_submissions (
            id UUID PRIMARY KEY,
            package_id TEXT,
            declared_version TEXT NOT NULL,
            submitter_id TEXT NOT NULL,
            upload_sha256 TEXT NOT NULL,
            zip_uri TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'RECEIVED',
            failure_reason TEXT DEFAULT '',
            requested_visibility TEXT NOT NULL DEFAULT 'private',
            idempotency_key TEXT,
            lease_owner TEXT,
            lease_until TIMESTAMPTZ,
            attempt_count INT NOT NULL DEFAULT 0,
            version_id UUID,
            meta JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_skill_submissions_status
            ON skill_submissions(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_skill_submissions_submitter
            ON skill_submissions(submitter_id, created_at DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_skill_submissions_idem
            ON skill_submissions(submitter_id, idempotency_key)
            WHERE idempotency_key IS NOT NULL AND idempotency_key <> '';

        CREATE TABLE IF NOT EXISTS skill_versions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            package_id TEXT NOT NULL REFERENCES skill_packages(id),
            version TEXT NOT NULL,
            submission_id UUID,
            content_sha256 TEXT NOT NULL DEFAULT '',
            upload_sha256 TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'REVIEW_PENDING',
            artifact_state TEXT NOT NULL DEFAULT 'present',
            release_path TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            tags TEXT[] NOT NULL DEFAULT '{}',
            author TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS skill_versions_occupied_semver
            ON skill_versions (package_id, version)
            WHERE status IN ('REVIEW_PENDING', 'PUBLISHED', 'QUARANTINED');

        CREATE TABLE IF NOT EXISTS skill_version_numbers (
            package_id TEXT NOT NULL,
            version TEXT NOT NULL,
            PRIMARY KEY (package_id, version)
        );

        CREATE TABLE IF NOT EXISTS skill_build_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            submission_id UUID NOT NULL,
            attempt INT NOT NULL DEFAULT 1,
            stage TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'running',
            detail JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at TIMESTAMPTZ
        );

        CREATE TABLE IF NOT EXISTS skill_scan_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            submission_id UUID NOT NULL,
            attempt INT NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'running',
            report JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at TIMESTAMPTZ
        );

        CREATE TABLE IF NOT EXISTS skill_findings (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            submission_id UUID NOT NULL,
            scan_run_id UUID,
            scanner TEXT NOT NULL DEFAULT '',
            severity TEXT NOT NULL DEFAULT 'Info',
            rule_id TEXT NOT NULL DEFAULT '',
            file_path TEXT NOT NULL DEFAULT '',
            line_no INT NOT NULL DEFAULT 0,
            evidence_redacted TEXT NOT NULL DEFAULT '',
            message TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_skill_findings_submission
            ON skill_findings(submission_id);

        CREATE TABLE IF NOT EXISTS skill_reviews (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            version_id UUID NOT NULL,
            reviewer_id TEXT,
            decision TEXT NOT NULL DEFAULT 'pending',
            comment TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            decided_at TIMESTAMPTZ
        );

        CREATE TABLE IF NOT EXISTS skill_installations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id TEXT NOT NULL,
            package_id TEXT NOT NULL,
            version_id UUID,
            content_sha256 TEXT NOT NULL DEFAULT '',
            version TEXT NOT NULL DEFAULT '',
            install_dir TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'READY',
            installed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(user_id, package_id)
        );
        CREATE INDEX IF NOT EXISTS idx_skill_installations_user
            ON skill_installations(user_id);
        """
    )
    # FK current pointer (deferred)
    try:
        await conn.execute(
            """
            ALTER TABLE skill_packages
            ADD COLUMN IF NOT EXISTS delisted BOOLEAN NOT NULL DEFAULT FALSE;
            """
        )
        await conn.execute(
            """
            ALTER TABLE skill_versions
            ADD COLUMN IF NOT EXISTS name_zh TEXT NOT NULL DEFAULT '';
            ALTER TABLE skill_versions
            ADD COLUMN IF NOT EXISTS name_en TEXT NOT NULL DEFAULT '';
            """
        )
        await conn.execute(
            """
            ALTER TABLE skill_packages
            DROP CONSTRAINT IF EXISTS skill_packages_current_fk;
            ALTER TABLE skill_packages
            ADD CONSTRAINT skill_packages_current_fk
            FOREIGN KEY (current_published_version_id)
            REFERENCES skill_versions(id);
            """
        )
    except Exception:
        pass


async def get_submission(submission_id: str) -> Optional[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM skill_submissions WHERE id = $1::uuid", submission_id
        )
        return _row(row)


async def list_submissions_for_user(user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM skill_submissions
            WHERE submitter_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            user_id,
            limit,
        )
        return [_row(r) for r in rows]


async def find_by_idempotency(submitter_id: str, key: str) -> Optional[Dict[str, Any]]:
    if not key:
        return None
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM skill_submissions
            WHERE submitter_id = $1 AND idempotency_key = $2
            ORDER BY created_at DESC LIMIT 1
            """,
            submitter_id,
            key,
        )
        return _row(row)


async def find_active_same_version(
    package_id: str, version: str
) -> Optional[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM skill_submissions
            WHERE package_id = $1 AND declared_version = $2
              AND status IN (
                'RECEIVED','QUEUED','VALIDATING','SCANNING','AWAITING_REVIEW'
              )
            ORDER BY created_at DESC LIMIT 1
            """,
            package_id,
            version,
        )
        return _row(row)


async def insert_submission(data: Dict[str, Any]) -> Dict[str, Any]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO skill_submissions (
                id, package_id, declared_version, submitter_id, upload_sha256,
                zip_uri, status, requested_visibility, idempotency_key, meta
            ) VALUES (
                $1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb
            ) RETURNING *
            """,
            data["id"],
            data.get("package_id"),
            data["declared_version"],
            data["submitter_id"],
            data["upload_sha256"],
            data.get("zip_uri", ""),
            data.get("status", "QUEUED"),
            data.get("requested_visibility", "private"),
            data.get("idempotency_key"),
            json.dumps(data.get("meta") or {}),
        )
        return _row(row)


async def merge_submission_meta(submission_id: str, patch: dict) -> None:
    if not patch:
        return
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE skill_submissions
            SET meta = COALESCE(meta, '{}'::jsonb) || $2::jsonb,
                updated_at = now()
            WHERE id = $1::uuid
            """,
            submission_id,
            json.dumps(patch),
        )


async def update_submission(submission_id: str, **fields) -> None:
    if not fields:
        return
    cols = []
    vals = []
    i = 1
    for k, v in fields.items():
        if k == "meta" and isinstance(v, dict):
            cols.append(f"{k} = ${i}::jsonb")
            vals.append(json.dumps(v))
        else:
            cols.append(f"{k} = ${i}")
            vals.append(v)
        i += 1
    cols.append(f"updated_at = now()")
    vals.append(submission_id)
    sql = f"UPDATE skill_submissions SET {', '.join(cols)} WHERE id = ${i}::uuid"
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(sql, *vals)


async def claim_next_submission(worker_id: str, lease_seconds: int = 120) -> Optional[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT id FROM skill_submissions
                WHERE status IN ('QUEUED', 'VALIDATING', 'SCANNING')
                  AND (lease_until IS NULL OR lease_until < now())
                ORDER BY created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            )
            if not row:
                return None
            sid = row["id"]
            await conn.execute(
                """
                UPDATE skill_submissions
                SET lease_owner = $2,
                    lease_until = now() + make_interval(secs => $3),
                    attempt_count = attempt_count + 1,
                    updated_at = now()
                WHERE id = $1
                """,
                sid,
                worker_id,
                int(lease_seconds),
            )
            full = await conn.fetchrow("SELECT * FROM skill_submissions WHERE id = $1", sid)
            return _row(full)


async def renew_lease(submission_id: str, worker_id: str, lease_seconds: int = 120) -> bool:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        r = await conn.execute(
            """
            UPDATE skill_submissions
            SET lease_until = now() + make_interval(secs => $3), updated_at = now()
            WHERE id = $1::uuid AND lease_owner = $2
            """,
            submission_id,
            worker_id,
            int(lease_seconds),
        )
        return r.endswith("1")


async def count_inflight(user_id: str) -> int:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        n = await conn.fetchval(
            """
            SELECT COUNT(*)::int FROM skill_submissions
            WHERE submitter_id = $1
              AND status IN ('RECEIVED','QUEUED','VALIDATING','SCANNING','AWAITING_REVIEW')
            """,
            user_id,
        )
        return int(n or 0)


async def count_uploads_last_hour(user_id: str) -> int:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        n = await conn.fetchval(
            """
            SELECT COUNT(*)::int FROM skill_submissions
            WHERE submitter_id = $1 AND created_at > now() - interval '1 hour'
            """,
            user_id,
        )
        return int(n or 0)


async def get_package(package_id: str) -> Optional[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        return _row(await conn.fetchrow("SELECT * FROM skill_packages WHERE id = $1", package_id))


async def upsert_package(
    package_id: str,
    owner_id: str,
    *,
    origin: str = "user",
    visibility: str = "private",
) -> Dict[str, Any]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO skill_packages (id, owner_id, origin, visibility)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (id) DO UPDATE SET updated_at = now()
            RETURNING *
            """,
            package_id,
            owner_id,
            origin,
            visibility,
        )
        return _row(row)


async def max_occupied_semver(package_id: str) -> Optional[str]:
    """最高已占用 / 终身占用版本号。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT version FROM skill_versions
            WHERE package_id = $1
              AND status IN ('REVIEW_PENDING','PUBLISHED','QUARANTINED')
            UNION
            SELECT version FROM skill_version_numbers WHERE package_id = $1
            """,
            package_id,
        )
    if not rows:
        return None
    from deep_agent_server.skills.marketplace.semver import compare_semver

    best = None
    for r in rows:
        v = r["version"]
        if best is None or compare_semver(v, best) > 0:
            best = v
    return best


async def is_version_occupied(package_id: str, version: str) -> bool:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        n = await conn.fetchval(
            """
            SELECT 1 FROM skill_versions
            WHERE package_id = $1 AND version = $2
              AND status IN ('REVIEW_PENDING','PUBLISHED','QUARANTINED')
            UNION
            SELECT 1 FROM skill_version_numbers
            WHERE package_id = $1 AND version = $2
            LIMIT 1
            """,
            package_id,
            version,
        )
        return n is not None


async def insert_version(data: Dict[str, Any]) -> Dict[str, Any]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO skill_versions (
                id, package_id, version, submission_id, content_sha256, upload_sha256,
                status, name, description, tags, author, name_zh, name_en
            ) VALUES (
                $1::uuid, $2, $3, $4::uuid, $5, $6, $7, $8, $9, $10, $11, $12, $13
            ) RETURNING *
            """,
            data.get("id") or str(uuid4()),
            data["package_id"],
            data["version"],
            data.get("submission_id"),
            data.get("content_sha256", ""),
            data.get("upload_sha256", ""),
            data.get("status", "REVIEW_PENDING"),
            data.get("name_zh") or data.get("name", ""),
            data.get("description", ""),
            data.get("tags") or [],
            data.get("author", ""),
            data.get("name_zh") or "",
            data.get("name_en") or "",
        )
        return _row(row)


async def get_version(version_id: str) -> Optional[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        return _row(
            await conn.fetchrow("SELECT * FROM skill_versions WHERE id = $1::uuid", version_id)
        )


async def get_version_by_package_semver(package_id: str, version: str) -> Optional[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        return _row(
            await conn.fetchrow(
                """
                SELECT * FROM skill_versions
                WHERE package_id = $1 AND version = $2
                ORDER BY created_at DESC LIMIT 1
                """,
                package_id,
                version,
            )
        )


async def update_version(version_id: str, **fields) -> None:
    if not fields:
        return
    cols, vals, i = [], [], 1
    for k, v in fields.items():
        cols.append(f"{k} = ${i}")
        vals.append(v)
        i += 1
    cols.append("updated_at = now()")
    vals.append(version_id)
    sql = f"UPDATE skill_versions SET {', '.join(cols)} WHERE id = ${i}::uuid"
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(sql, *vals)


async def insert_findings(submission_id: str, scan_run_id: Optional[str], findings: list) -> None:
    if not findings:
        return
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        for f in findings:
            scanner = getattr(f, "scanner", None) if not isinstance(f, dict) else f.get("scanner")
            severity = getattr(f, "severity", None) if not isinstance(f, dict) else f.get("severity")
            rule_id = getattr(f, "rule_id", None) if not isinstance(f, dict) else f.get("rule_id")
            file_path = getattr(f, "file", None) if not isinstance(f, dict) else (f.get("file") or f.get("file_path"))
            line_no = getattr(f, "line", None) if not isinstance(f, dict) else f.get("line")
            evidence = (
                getattr(f, "evidence_redacted", None)
                if not isinstance(f, dict)
                else f.get("evidence_redacted")
            )
            message = getattr(f, "message", None) if not isinstance(f, dict) else f.get("message")
            await conn.execute(
                """
                INSERT INTO skill_findings (
                    submission_id, scan_run_id, scanner, severity, rule_id,
                    file_path, line_no, evidence_redacted, message
                ) VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9)
                """,
                submission_id,
                scan_run_id,
                scanner or "",
                severity or "",
                rule_id or "",
                file_path or "",
                int(line_no or 0),
                (evidence or "")[:500],
                (message or "")[:500],
            )


async def list_findings(submission_id: str) -> List[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM skill_findings WHERE submission_id = $1::uuid ORDER BY created_at",
            submission_id,
        )
        return [_row(r) for r in rows]


async def create_scan_run(submission_id: str, attempt: int = 1) -> str:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rid = await conn.fetchval(
            """
            INSERT INTO skill_scan_runs (submission_id, attempt, status)
            VALUES ($1::uuid, $2, 'running') RETURNING id
            """,
            submission_id,
            attempt,
        )
        return str(rid)


async def finish_scan_run(scan_run_id: str, status: str, report: dict) -> None:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE skill_scan_runs
            SET status = $2, report = $3::jsonb, finished_at = now()
            WHERE id = $1::uuid
            """,
            scan_run_id,
            status,
            json.dumps(report or {}),
        )


async def create_pending_review(version_id: str) -> str:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rid = await conn.fetchval(
            """
            INSERT INTO skill_reviews (version_id, decision)
            VALUES ($1::uuid, 'pending') RETURNING id
            """,
            version_id,
        )
        return str(rid)


async def get_pending_review_for_version(version_id: str) -> Optional[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        return _row(
            await conn.fetchrow(
                """
                SELECT * FROM skill_reviews
                WHERE version_id = $1::uuid AND decision = 'pending'
                ORDER BY created_at DESC LIMIT 1
                """,
                version_id,
            )
        )


async def decide_review(review_id: str, reviewer_id: str, decision: str, comment: str = "") -> None:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE skill_reviews
            SET reviewer_id = $2, decision = $3, comment = $4, decided_at = now()
            WHERE id = $1::uuid
            """,
            review_id,
            reviewer_id,
            decision,
            comment,
        )


async def list_pending_version_reviews(page: int = 1, page_size: int = 20) -> Tuple[List[Dict], int]:
    pool = await db_manager.get_pool()
    offset = (page - 1) * page_size
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            """
            SELECT COUNT(*)::int FROM skill_reviews r
            JOIN skill_versions v ON v.id = r.version_id
            WHERE r.decision = 'pending' AND v.status = 'REVIEW_PENDING'
            """
        )
        rows = await conn.fetch(
            """
            SELECT r.*, v.package_id, v.version, v.name, v.description, v.submission_id,
                   v.content_sha256, p.owner_id, p.visibility
            FROM skill_reviews r
            JOIN skill_versions v ON v.id = r.version_id
            JOIN skill_packages p ON p.id = v.package_id
            WHERE r.decision = 'pending' AND v.status = 'REVIEW_PENDING'
            ORDER BY r.created_at ASC
            LIMIT $1 OFFSET $2
            """,
            page_size,
            offset,
        )
        return [_row(r) for r in rows], int(total or 0)


async def add_lifelong_number(package_id: str, version: str) -> None:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO skill_version_numbers (package_id, version)
            VALUES ($1, $2) ON CONFLICT DO NOTHING
            """,
            package_id,
            version,
        )


async def set_current_published(package_id: str, version_id: Optional[str]) -> None:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE skill_packages
            SET current_published_version_id = $2::uuid, updated_at = now()
            WHERE id = $1
            """,
            package_id,
            version_id,
        )


async def get_highest_other_published(package_id: str, exclude_version_id: str) -> Optional[Dict]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM skill_versions
            WHERE package_id = $1 AND id <> $2::uuid
              AND status = 'PUBLISHED' AND artifact_state = 'present'
            """,
            package_id,
            exclude_version_id,
        )
    if not rows:
        return None
    from deep_agent_server.skills.marketplace.semver import compare_semver

    best = None
    for r in rows:
        d = _row(r)
        if best is None or compare_semver(d["version"], best["version"]) > 0:
            best = d
    return best


async def project_catalog_from_version(version: Dict[str, Any], package: Dict[str, Any]) -> None:
    """将已发布版本投影到 skill_catalog。"""
    from db import deep_storage

    status = "published" if package.get("visibility") == "public" else "draft"
    repo_rel = version.get("release_path") or f"{version['package_id']}/releases/{version['version']}"
    display_name = (
        (version.get("name_zh") or "").strip()
        or (version.get("name") or "").strip()
        or version["package_id"]
    )
    await deep_storage.upsert_skill_catalog(
        skill_id=version["package_id"],
        name=display_name,
        description=version.get("description") or "",
        version=version["version"],
        tags=list(version.get("tags") or []),
        author=version.get("author") or package.get("owner_id") or "",
        status=status,
        repo_rel_path=repo_rel,
        name_zh=(version.get("name_zh") or "").strip(),
        name_en=(version.get("name_en") or "").strip(),
    )
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE skill_catalog
            SET creator_user_id = $2,
                skill_type = $3,
                source_path = $4,
                updated_at = CURRENT_TIMESTAMP
            WHERE skill_id = $1
            """,
            version["package_id"],
            package.get("owner_id") or "",
            "official" if package.get("origin") == "official" else "user",
            repo_rel,
        )


async def clear_catalog_if_no_current(package_id: str) -> None:
    from db import deep_storage

    pkg = await get_package(package_id)
    if pkg and not pkg.get("current_published_version_id"):
        await deep_storage.set_skill_catalog_status(package_id, "archived")


async def set_package_delisted(package_id: str, delisted: bool = True) -> None:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE skill_packages
            SET delisted = $2, updated_at = now()
            WHERE id = $1
            """,
            package_id,
            bool(delisted),
        )


async def list_delisted_package_ids() -> List[str]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM skill_packages WHERE delisted = TRUE"
        )
        return [str(r["id"]) for r in rows]


async def list_package_ids() -> List[str]:
    """所有已转为市场包的 skill_id（catalog 由发布流程维护，repo 同步不应覆盖）。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id FROM skill_packages")
        return [str(r["id"]) for r in rows]


async def list_versions_for_package(package_id: str) -> List[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM skill_versions
            WHERE package_id = $1
            ORDER BY created_at DESC
            """,
            package_id,
        )
        return [_row(r) for r in rows]


async def list_submissions_for_package(
    package_id: str, limit: int = 20
) -> List[Dict[str, Any]]:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM skill_submissions
            WHERE package_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            package_id,
            limit,
        )
        return [_row(r) for r in rows]


async def cancel_inflight_submissions(package_id: str) -> None:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE skill_submissions
            SET status = 'CANCELLED', updated_at = now()
            WHERE package_id = $1
              AND status IN ('RECEIVED','QUEUED','VALIDATING','SCANNING','AWAITING_REVIEW')
            """,
            package_id,
        )


async def count_package_installs(package_id: str) -> int:
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        n = await conn.fetchval(
            "SELECT COUNT(*)::int FROM user_installed_skills WHERE market_skill_id = $1",
            package_id,
        )
        return int(n or 0)


async def package_display_names(package_ids: List[str]) -> List[str]:
    if not package_ids:
        return []
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (v.package_id) v.package_id, v.name
            FROM skill_versions v
            WHERE v.package_id = ANY($1::text[])
            ORDER BY v.package_id, v.created_at DESC
            """,
            package_ids,
        )
    names = {str(r["package_id"]): (r["name"] or r["package_id"]) for r in rows}
    return [names.get(pid, pid) for pid in package_ids]


async def find_english_name_conflict(
    name_en: str, exclude_package_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """其它包已占用同一英文 name（含内置 catalog.skill_id / SKILL.md name）。"""
    key = (name_en or "").strip().lower()
    if not key:
        return None
    exclude = (exclude_package_id or "").strip() or None
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT c.skill_id AS package_id,
                   COALESCE(NULLIF(c.name_zh, ''), c.name, c.skill_id) AS display_name,
                   'catalog' AS source
            FROM skill_catalog c
            LEFT JOIN skill_packages p ON p.id = c.skill_id
            WHERE c.status IN ('published', 'draft')
              AND COALESCE(p.delisted, FALSE) = FALSE
              AND ($2::text IS NULL OR c.skill_id <> $2)
              AND (
                lower(c.skill_id) = $1
                OR lower(COALESCE(NULLIF(c.name_en, ''), '')) = $1
                OR (COALESCE(c.name_en, '') = '' AND lower(c.name) = $1)
              )
            LIMIT 1
            """,
            key,
            exclude,
        )
        if row:
            return _row(row)
        row = await conn.fetchrow(
            """
            SELECT v.package_id,
                   COALESCE(NULLIF(v.name_zh, ''), v.name, v.package_id) AS display_name,
                   'version' AS source
            FROM skill_versions v
            LEFT JOIN skill_packages p ON p.id = v.package_id
            WHERE v.status IN ('REVIEW_PENDING', 'PUBLISHED', 'QUARANTINED')
              AND COALESCE(p.delisted, FALSE) = FALSE
              AND ($2::text IS NULL OR v.package_id <> $2)
              AND lower(COALESCE(NULLIF(v.name_en, ''), v.name)) = $1
            LIMIT 1
            """,
            key,
            exclude,
        )
        return _row(row)


async def find_chinese_name_conflict(
    name_zh: str, exclude_package_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    key = (name_zh or "").strip()
    if not key:
        return None
    exclude = (exclude_package_id or "").strip() or None
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT c.skill_id AS package_id,
                   COALESCE(NULLIF(c.name_zh, ''), c.name) AS display_name,
                   'catalog' AS source
            FROM skill_catalog c
            LEFT JOIN skill_packages p ON p.id = c.skill_id
            WHERE c.status IN ('published', 'draft')
              AND COALESCE(p.delisted, FALSE) = FALSE
              AND ($2::text IS NULL OR c.skill_id <> $2)
              AND (
                c.name_zh = $1
                OR (COALESCE(c.name_zh, '') = '' AND c.name = $1)
              )
            LIMIT 1
            """,
            key,
            exclude,
        )
        if row:
            return _row(row)
        row = await conn.fetchrow(
            """
            SELECT v.package_id,
                   COALESCE(NULLIF(v.name_zh, ''), v.name) AS display_name,
                   'version' AS source
            FROM skill_versions v
            LEFT JOIN skill_packages p ON p.id = v.package_id
            WHERE v.status IN ('REVIEW_PENDING', 'PUBLISHED', 'QUARANTINED')
              AND COALESCE(p.delisted, FALSE) = FALSE
              AND ($2::text IS NULL OR v.package_id <> $2)
              AND COALESCE(NULLIF(v.name_zh, ''), v.name) = $1
            LIMIT 1
            """,
            key,
            exclude,
        )
        return _row(row)


async def find_inflight_identity_conflict(
    *,
    name_en: str,
    name_zh: str,
    exclude_package_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """进行中的提交已占用同一中/英文名。"""
    key_en = (name_en or "").strip().lower()
    key_zh = (name_zh or "").strip()
    if not key_en and not key_zh:
        return None
    exclude = (exclude_package_id or "").strip() or None
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT s.package_id,
                   COALESCE(NULLIF(s.meta->>'name_zh', ''), s.meta->>'name_en', s.package_id) AS display_name,
                   CASE
                     WHEN $1 <> '' AND lower(COALESCE(s.meta->>'name_en', '')) = $1 THEN 'en'
                     ELSE 'zh'
                   END AS kind
            FROM skill_submissions s
            LEFT JOIN skill_packages p ON p.id = s.package_id
            WHERE s.status IN (
                'RECEIVED','QUEUED','VALIDATING','SCANNING','AWAITING_REVIEW'
              )
              AND COALESCE(p.delisted, FALSE) = FALSE
              AND ($3::text IS NULL OR s.package_id <> $3)
              AND (
                ($1 <> '' AND lower(COALESCE(s.meta->>'name_en', '')) = $1)
                OR ($2 <> '' AND COALESCE(s.meta->>'name_zh', '') = $2)
              )
            ORDER BY s.created_at
            LIMIT 1
            """,
            key_en,
            key_zh,
            exclude,
        )
        return _row(row)


async def find_content_duplicate(
    content_sha256: str,
    *,
    exclude_package_id: Optional[str] = None,
    exclude_version: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    sha = (content_sha256 or "").strip()
    if not sha:
        return None
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT v.package_id, v.version, v.name, v.name_zh, v.name_en
            FROM skill_versions v
            LEFT JOIN skill_packages p ON p.id = v.package_id
            WHERE v.content_sha256 = $1
              AND v.status IN ('REVIEW_PENDING', 'PUBLISHED', 'QUARANTINED')
              AND COALESCE(p.delisted, FALSE) = FALSE
              AND NOT (
                v.package_id = COALESCE($2, '')
                AND v.version = COALESCE($3, '')
              )
            ORDER BY v.created_at DESC
            LIMIT 1
            """,
            sha,
            (exclude_package_id or "").strip() or None,
            (exclude_version or "").strip() or None,
        )
        return _row(row)


async def get_package_declared_identity(package_id: str) -> Optional[Dict[str, Any]]:
    """已有包的中英文名（最新占用版本优先，其次 catalog）。"""
    if not package_id:
        return None
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT v.name_zh, v.name_en, v.name, v.description, v.tags, v.author
            FROM skill_versions v
            WHERE v.package_id = $1
              AND v.status IN ('REVIEW_PENDING', 'PUBLISHED', 'QUARANTINED')
            ORDER BY v.created_at DESC
            LIMIT 1
            """,
            package_id,
        )
        if row:
            return _row(row)
        row = await conn.fetchrow(
            """
            SELECT name_zh, name_en, name, description, tags, author
            FROM skill_catalog
            WHERE skill_id = $1
            """,
            package_id,
        )
        return _row(row)


async def list_uploads_for_user(user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """按 package 聚合：一张卡片对应一个上传包（含已下架）。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM (
                SELECT DISTINCT ON (s.package_id)
                    s.*,
                    COALESCE(p.delisted, FALSE) AS delisted,
                    p.visibility AS package_visibility,
                    COALESCE(
                        NULLIF(s.meta->>'name_zh', ''),
                        NULLIF(v.name_zh, ''),
                        NULLIF(s.meta->>'name', ''),
                        v.name,
                        s.package_id
                    ) AS skill_name,
                    (
                        SELECT COUNT(*)::int
                        FROM user_installed_skills uis
                        WHERE uis.market_skill_id = s.package_id
                    ) AS install_count
                FROM skill_submissions s
                LEFT JOIN skill_packages p ON p.id = s.package_id
                LEFT JOIN skill_versions v ON v.id = s.version_id
                WHERE s.submitter_id = $1
                  AND s.package_id IS NOT NULL
                ORDER BY s.package_id, s.created_at DESC
            ) t
            ORDER BY t.created_at DESC
            LIMIT $2
            """,
            user_id,
            limit,
        )
        return [_row(r) for r in rows]
