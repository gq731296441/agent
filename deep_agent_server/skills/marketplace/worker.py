"""DB 任务轮询 Worker：SKIP LOCKED + 租约。"""

from __future__ import annotations

import asyncio
import logging
import socket
import shutil
from pathlib import Path
from typing import Optional
from uuid import uuid4

from deep_agent_server.skills.marketplace import storage as ms
from deep_agent_server.skills.marketplace.hash_payload import hash_skill_payload
from deep_agent_server.skills.marketplace.identity import normalize_declared
from deep_agent_server.skills.marketplace.paths import submission_build_dir
from config.config import app_config
from deep_agent_server.skills.marketplace.scan.adapter import (
    Finding,
    apply_scan_stage,
    new_scan_progress,
    run_security_scans,
)
from deep_agent_server.skills.marketplace.validate.structure import (
    StructureError,
    validate_skill_structure,
)
from deep_agent_server.skills.marketplace.validate.unpack import UnpackError, safe_unpack_zip

logger = logging.getLogger(__name__)

_worker_task: Optional[asyncio.Task] = None
_worker_id = f"{socket.gethostname()}-{uuid4().hex[:8]}"
_stop = asyncio.Event()


def _pkg_finding(rule_id: str, message: str, file: str = "SKILL.md", severity: str = "High") -> Finding:
    return Finding(
        scanner="package",
        severity=severity,
        rule_id=rule_id,
        file=file,
        line=0,
        evidence_redacted=message,
        message=message,
    )


def _findings_from_structure_error(err: StructureError) -> list:
    msg = (err.message or "").strip() or "结构校验失败"
    if "缺少 SKILL.md" in msg:
        return [_pkg_finding("STRUCT-001", msg, "SKILL.md")]
    if "frontmatter" in msg or "缺少 name" in msg:
        return [_pkg_finding("STRUCT-002", msg, "SKILL.md")]
    items = [_pkg_finding("STRUCT-003", msg, "SKILL.md")]
    for detail in err.details[:20]:
        items.append(_pkg_finding("STRUCT-003", str(detail), "SKILL.md"))
    return items


_IDENTITY_RULES = {
    "STRUCT-004": ("英文名称与 SKILL.md 一致", "high", "填写的英文名称须与 SKILL.md 的 name 完全一致"),
    "STRUCT-011": ("版本号与 SKILL.md 一致", "high", "填写的版本号须与 SKILL.md frontmatter 的 version 完全一致"),
    "DUP-001": ("英文名称不与市场已有技能重名", "high", "市场不允许两个同名 Skill"),
    "DUP-002": ("制品内容不重复", "high", "不能与已发布 Skill 内容完全相同"),
    "DUP-003": ("中文名称不与市场已有技能重名", "high", "市场不允许两个同名 Skill"),
}


def _identity_rule_items(findings: list, version_ok: bool) -> list:
    """身份校验阶段的规则项（s3canner 未运行时用于报告规则清单）。"""
    failed_ids = set()
    for f in findings or []:
        rid = getattr(f, "rule_id", None) if not isinstance(f, dict) else f.get("rule_id")
        if rid:
            failed_ids.add(str(rid))
    items = []
    for rid, (name, severity, desc) in _IDENTITY_RULES.items():
        passed = rid not in failed_ids
        if rid == "STRUCT-011" and not version_ok:
            passed = False
        items.append(
            {
                "id": rid,
                "name": name,
                "passed": passed,
                "category": "spec",
                "severity": severity,
                "description": desc,
            }
        )
    return items


def _scan_rules_payload(items: list) -> dict:
    total = len(items)
    passed = sum(1 for i in items if isinstance(i, dict) and i.get("passed"))
    return {"items": items, "total": total, "passed": passed, "failed": total - passed}


async def _fail_validation(
    sid: str,
    prev_meta: dict,
    scan_progress: dict,
    reason: str,
    findings: list,
    attempt: int = 1,
    scan_rules: Optional[dict] = None,
) -> None:
    n = len(findings) or 1
    scan_progress = apply_scan_stage(scan_progress, "validate", "done", findings=n)
    scan_run_id = None
    try:
        scan_run_id = await ms.create_scan_run(sid, attempt)
        await ms.finish_scan_run(
            scan_run_id,
            "failed",
            {"reason": reason, "scan_progress": scan_progress},
        )
    except Exception as e:
        logger.warning("[SkillWorker] create validation scan_run failed: %s", e)
    await ms.insert_findings(sid, scan_run_id, findings)
    meta = {**prev_meta, "scan_progress": scan_progress}
    if scan_rules is not None:
        meta["scan_rules"] = scan_rules
    await ms.update_submission(
        sid,
        status="FAILED",
        failure_reason=reason[:500],
        meta=meta,
    )


def _submission_meta(sub: dict) -> dict:
    prev_meta = sub.get("meta") or {}
    if isinstance(prev_meta, str):
        import json as _json

        try:
            prev_meta = _json.loads(prev_meta)
        except Exception:
            prev_meta = {}
    return prev_meta if isinstance(prev_meta, dict) else {}


async def process_submission(sub: dict) -> None:
    sid = sub["id"]
    build = submission_build_dir(sid)
    zip_path = Path(sub.get("zip_uri") or (build / "upload.zip"))
    unpacked = build / "unpacked"
    reports = build / "reports"

    try:
        per_scan = int(getattr(app_config, "SKILL_SCAN_TIMEOUT_SECONDS", 120) or 120)
        scan_lease = per_scan + 60
        await ms.renew_lease(sid, _worker_id, lease_seconds=scan_lease)
        prev_meta = _submission_meta(sub)
        scan_progress = prev_meta.get("scan_progress")
        if not isinstance(scan_progress, dict):
            scan_progress = new_scan_progress()
        scan_progress = apply_scan_stage(scan_progress, "validate", "running")
        await ms.update_submission(
            sid,
            status="VALIDATING",
            meta={**prev_meta, "scan_progress": scan_progress},
        )

        attempt = int(sub.get("attempt_count") or 1)
        if not zip_path.is_file():
            await _fail_validation(
                sid,
                prev_meta,
                scan_progress,
                "ZIP_MISSING",
                [_pkg_finding("PKG-001", "未找到上传的 ZIP 文件", "upload.zip")],
                attempt=attempt,
            )
            return

        try:
            await asyncio.to_thread(safe_unpack_zip, zip_path, unpacked)
        except UnpackError as e:
            await _fail_validation(
                sid,
                prev_meta,
                scan_progress,
                e.message[:500],
                [_pkg_finding("PKG-002", e.message, "upload.zip")],
                attempt=attempt,
            )
            return

        try:
            meta = await asyncio.to_thread(validate_skill_structure, unpacked)
        except StructureError as e:
            detail = e.message
            if e.details:
                detail = detail + "; " + "; ".join(e.details[:10])
            await _fail_validation(
                sid,
                prev_meta,
                scan_progress,
                detail[:500],
                _findings_from_structure_error(e),
                attempt=attempt,
            )
            if unpacked.exists():
                shutil.rmtree(unpacked, ignore_errors=True)
            return

        declared = normalize_declared(
            name_zh=prev_meta.get("name_zh") or "",
            name_en=prev_meta.get("name_en") or "",
            description=prev_meta.get("description") or "",
            tags=prev_meta.get("tags"),
            author=prev_meta.get("author") or "",
            submitter_id=sub.get("submitter_id") or "",
        )
        package_id = sub["package_id"]
        version = sub["declared_version"]
        md_name = (meta.get("name") or "").strip()
        identity_findings = []
        if declared["name_en"] != md_name:
            identity_findings.append(
                _pkg_finding(
                    "STRUCT-004",
                    f"填写的英文名称「{declared['name_en']}」与 SKILL.md 的 name「{md_name}」不一致，须保持相同",
                    "SKILL.md",
                )
            )
        # 版本号必须与 SKILL.md frontmatter 的 version 一致
        md_version = (meta.get("skill_md_version") or "").strip()
        declared_version = str(version or "").strip()
        version_ok = declared_version == md_version
        if not version_ok:
            identity_findings.append(
                _pkg_finding(
                    "STRUCT-011",
                    f"填写的版本号「{declared_version or '（未填写）'}」"
                    f"与 SKILL.md 的 version「{md_version or '（缺失）'}」不一致，须保持相同",
                    "SKILL.md",
                )
            )
        content_sha = hash_skill_payload(unpacked)
        dup_content = await ms.find_content_duplicate(
            content_sha, exclude_package_id=package_id, exclude_version=version
        )
        if dup_content:
            identity_findings.append(
                _pkg_finding(
                    "DUP-002",
                    "制品内容与已有 Skill {pid} v{ver} 完全相同，不能重复提交".format(
                        pid=dup_content.get("package_id") or "",
                        ver=dup_content.get("version") or "",
                    ),
                    "upload.zip",
                )
            )
        dup_en = await ms.find_english_name_conflict(declared["name_en"], package_id)
        if dup_en:
            identity_findings.append(
                _pkg_finding(
                    "DUP-001",
                    "英文名称「{en}」已被「{dn}」占用，市场上不能出现两个同名 Skill".format(
                        en=declared["name_en"],
                        dn=dup_en.get("display_name") or dup_en.get("package_id") or "",
                    ),
                    "SKILL.md",
                )
            )
        dup_zh = await ms.find_chinese_name_conflict(declared["name_zh"], package_id)
        if dup_zh:
            identity_findings.append(
                _pkg_finding(
                    "DUP-003",
                    "中文名称「{zh}」与市场上「{dn}」重复".format(
                        zh=declared["name_zh"],
                        dn=dup_zh.get("display_name") or dup_zh.get("package_id") or "",
                    ),
                    "SKILL.md",
                )
            )
        if not dup_en and not dup_zh:
            inflight = await ms.find_inflight_identity_conflict(
                name_en=declared["name_en"],
                name_zh=declared["name_zh"],
                exclude_package_id=package_id,
            )
            if inflight:
                kind = inflight.get("kind") or "en"
                rule_id = "DUP-001" if kind == "en" else "DUP-003"
                label = "英文名称" if kind == "en" else "中文名称"
                identity_findings.append(
                    _pkg_finding(
                        rule_id,
                        "{label}与进行中的提交「{dn}」重复，不能同时上架同名 Skill".format(
                            label=label,
                            dn=inflight.get("display_name") or inflight.get("package_id") or "",
                        ),
                        "SKILL.md",
                    )
                )
        if identity_findings:
            reason = "DUPLICATE" if any(
                (f.rule_id or "").startswith("DUP") for f in identity_findings
            ) else identity_findings[0].message
            identity_rules = _scan_rules_payload(
                _identity_rule_items(identity_findings, version_ok)
            )
            await _fail_validation(
                sid,
                prev_meta,
                scan_progress,
                reason[:500],
                identity_findings,
                attempt=attempt,
                scan_rules=identity_rules,
            )
            if unpacked.exists():
                shutil.rmtree(unpacked, ignore_errors=True)
            return

        prev_meta = {**prev_meta, **meta, **declared, "content_sha256": content_sha}
        scan_progress = apply_scan_stage(scan_progress, "prepare", "running")
        await ms.update_submission(
            sid,
            status="SCANNING",
            meta={**prev_meta, "scan_progress": scan_progress},
        )
        await ms.renew_lease(sid, _worker_id, lease_seconds=scan_lease)

        async def _on_scan_progress(payload: dict) -> None:
            await ms.merge_submission_meta(sid, {"scan_progress": payload})

        scan_run_id = await ms.create_scan_run(sid, attempt)
        outcome = await run_security_scans(
            unpacked, reports, on_progress=_on_scan_progress
        )
        await ms.finish_scan_run(
            scan_run_id,
            "failed" if outcome.unavailable else ("blocked" if outcome.blocked else "ok"),
            outcome.details,
        )
        await ms.insert_findings(sid, scan_run_id, outcome.findings)
        final_progress = (outcome.details or {}).get("scan_progress")
        if isinstance(final_progress, dict):
            await ms.merge_submission_meta(sid, {"scan_progress": final_progress})
        # 完整规则检查结果（含通过项），供前端展示扫描覆盖面；
        # 追加身份校验规则（版本一致性），与 s3canner 规则同屏展示
        scan_rules = (outcome.details or {}).get("scan_rules")
        if isinstance(scan_rules, dict) and isinstance(scan_rules.get("items"), list):
            items = scan_rules["items"]
            if not any(isinstance(i, dict) and i.get("id") == "STRUCT-011" for i in items):
                items.append(
                    {
                        "id": "STRUCT-011",
                        "name": "版本号与 SKILL.md 一致",
                        "passed": True,
                        "category": "spec",
                        "severity": "high",
                        "description": "填写的版本号须与 SKILL.md frontmatter 的 version 完全一致",
                    }
                )
            scan_rules.update(_scan_rules_payload(items))
            await ms.merge_submission_meta(sid, {"scan_rules": scan_rules})

        if outcome.unavailable:
            await ms.update_submission(
                sid, status="FAILED", failure_reason="SCAN_UNAVAILABLE"
            )
            return

        if outcome.blocked:
            await ms.update_submission(
                sid, status="BLOCKED", failure_reason="SECURITY_BLOCKED"
            )
            return

        # 晋升 version
        content_sha = prev_meta.get("content_sha256") or hash_skill_payload(unpacked)
        package_id = sub["package_id"]
        version = sub["declared_version"]

        if await ms.is_version_occupied(package_id, version):
            await ms.update_submission(
                sid, status="FAILED", failure_reason="VERSION_OCCUPIED"
            )
            return

        from uuid import uuid4

        vid = str(uuid4())
        try:
            await ms.insert_version(
                {
                    "id": vid,
                    "package_id": package_id,
                    "version": version,
                    "submission_id": sid,
                    "content_sha256": content_sha,
                    "upload_sha256": sub.get("upload_sha256", ""),
                    "status": "REVIEW_PENDING",
                    "name": prev_meta.get("name_zh") or meta.get("name", package_id),
                    "name_zh": prev_meta.get("name_zh") or "",
                    "name_en": prev_meta.get("name_en") or meta.get("name", ""),
                    "description": prev_meta.get("description") or meta.get("description", ""),
                    "tags": prev_meta.get("tags") or meta.get("tags") or [],
                    "author": prev_meta.get("author") or sub.get("submitter_id", ""),
                }
            )
        except Exception as e:
            logger.warning(f"insert version failed: {e}")
            await ms.update_submission(
                sid, status="FAILED", failure_reason="VERSION_OCCUPIED"
            )
            return

        await ms.create_pending_review(vid)
        await ms.update_submission(
            sid, status="AWAITING_REVIEW", version_id=vid
        )
        logger.info(f"[SkillWorker] submission={sid} → AWAITING_REVIEW version={vid}")

    except Exception as e:
        logger.exception(f"[SkillWorker] submission={sid} error: {e}")
        await ms.update_submission(
            sid, status="FAILED", failure_reason=f"WORKER_ERROR:{e}"[:500]
        )


async def _loop(poll_interval: float = 2.0) -> None:
    logger.info(f"[SkillWorker] started id={_worker_id}")
    while not _stop.is_set():
        try:
            sub = await ms.claim_next_submission(_worker_id)
            if sub:
                await process_submission(sub)
            else:
                try:
                    await asyncio.wait_for(_stop.wait(), timeout=poll_interval)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"[SkillWorker] loop error: {e}")
            await asyncio.sleep(poll_interval)
    logger.info("[SkillWorker] stopped")


async def start_marketplace_worker() -> None:
    global _worker_task
    _stop.clear()
    if _worker_task and not _worker_task.done():
        return
    _worker_task = asyncio.create_task(_loop())


async def stop_marketplace_worker() -> None:
    global _worker_task
    _stop.set()
    if _worker_task:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
        _worker_task = None
