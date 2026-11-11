"""PublishService：submit / import_trusted / quarantine。"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from config.config import app_config
from deep_agent_server.skills.marketplace import storage as ms
from deep_agent_server.skills.marketplace.hash_payload import hash_skill_payload
from deep_agent_server.skills.marketplace.paths import (
    get_builds_root,
    new_package_id,
    new_submission_id,
    release_dir,
    submission_build_dir,
)
from deep_agent_server.skills.marketplace.semver import is_strictly_greater, is_valid_semver
from deep_agent_server.skills.marketplace.validate.structure import (
    StructureError,
    validate_skill_structure,
)
from deep_agent_server.skills.marketplace.validate.unpack import UnpackError, safe_unpack_zip
from deep_agent_server.skills.marketplace.scan.adapter import new_scan_progress
from deep_agent_server.skills.marketplace.identity import (
    normalize_declared,
    validate_declared,
)

logger = logging.getLogger(__name__)


def _submission_meta(sub: Dict[str, Any]) -> Dict[str, Any]:
    """读取 submission.meta（jsonb 可能以 str 返回）。"""
    meta = (sub or {}).get("meta")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except ValueError:
            meta = {}
    return meta if isinstance(meta, dict) else {}


async def _purge_withdrawn_repo(package_id: str, pkg: Optional[Dict[str, Any]] = None) -> bool:
    """下架后删除 VM .skill-repo 中该包目录。不碰平台官方 skill-repo。"""
    pid = (package_id or "").strip()
    if not pid:
        return False
    origin = (pkg or {}).get("origin") or "user"
    if origin == "official" or not pid.startswith("uskill-"):
        return False
    try:
        from deep_agent_server.skills.vm_repo import delete_skill_from_repo

        removed = await delete_skill_from_repo(pid)
        if removed:
            logger.info("[SkillWithdraw] 已删除仓库目录 package=%s", pid)
        return bool(removed)
    except Exception:
        logger.exception("[SkillWithdraw] 删除仓库目录失败 package=%s", pid)
        return False


class PublishError(Exception):
    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code


class PublishService:
    async def submit(
        self,
        *,
        submitter_id: str,
        zip_bytes: bytes,
        version: str,
        package_id: Optional[str] = None,
        requested_visibility: str = "public",
        idempotency_key: Optional[str] = None,
        declared_meta: Optional[dict] = None,
    ) -> Dict[str, Any]:
        if not submitter_id:
            raise PublishError("缺少 submitter_id", 400)
        if not is_valid_semver(version):
            raise PublishError("version 必须是 MAJOR.MINOR.PATCH", 400)
        requested_visibility = "public"
        if requested_visibility not in ("private", "public"):
            raise PublishError("requested_visibility 无效", 400)

        max_bytes = int(getattr(app_config, "SKILL_UPLOAD_MAX_BYTES", 52428800))
        if len(zip_bytes) > max_bytes:
            raise PublishError(f"ZIP 超过 {max_bytes // 1024 // 1024} MiB", 400)

        inflight = await ms.count_inflight(submitter_id)
        if inflight >= int(getattr(app_config, "SKILL_UPLOAD_MAX_INFLIGHT", 2)):
            raise PublishError("进行中提交过多", 429)
        hourly = await ms.count_uploads_last_hour(submitter_id)
        if hourly >= int(getattr(app_config, "SKILL_UPLOAD_RATE_PER_HOUR", 10)):
            raise PublishError("上传速率超限", 429)

        upload_sha = hashlib.sha256(zip_bytes).hexdigest()

        if idempotency_key:
            existing = await ms.find_by_idempotency(submitter_id, idempotency_key)
            if existing:
                if (
                    existing.get("declared_version") == version
                    and existing.get("upload_sha256") == upload_sha
                    and (not package_id or existing.get("package_id") == package_id)
                ):
                    return {"submission_id": existing["id"], "status": existing["status"]}
                raise PublishError("Idempotency-Key 与制品/版本冲突", 409)

        # 解析 / 创建 package
        if package_id:
            pkg = await ms.get_package(package_id)
            if not pkg:
                # 兼容旧 catalog：允许首次把 catalog skill 升为 package
                from db import deep_storage

                cat = await deep_storage.get_skill_catalog(package_id)
                if not cat:
                    raise PublishError("package 不存在", 404)
                owner = cat.get("creator_user_id") or ""
                if owner and owner != submitter_id:
                    if not await deep_storage.is_admin(submitter_id):
                        raise PublishError("无权更新该包", 403)
                await ms.upsert_package(package_id, owner or submitter_id)
                pkg = await ms.get_package(package_id)
            elif pkg["owner_id"] != submitter_id:
                from db import deep_storage

                if not await deep_storage.is_admin(submitter_id):
                    raise PublishError("无权更新该包", 403)
        else:
            package_id = new_package_id()
            await ms.upsert_package(package_id, submitter_id, visibility="public")

        # 递增检查
        max_v = await ms.max_occupied_semver(package_id)
        if max_v and not is_strictly_greater(version, max_v):
            # 未占用则可重用：若占用则拒绝
            if await ms.is_version_occupied(package_id, version):
                raise PublishError(
                    f"版本号必须高于已占用最高版本 {max_v}，且 {version} 已被占用",
                    400,
                )
            if not is_strictly_greater(version, max_v):
                raise PublishError(f"版本号必须严格高于 {max_v}", 400)

        # 处理中冲突
        active = await ms.find_active_same_version(package_id, version)
        if active:
            if active.get("upload_sha256") == upload_sha:
                return {"submission_id": active["id"], "status": active["status"]}
            raise PublishError("同版本另有进行中的不同制品提交", 409)

        # occupied + different zip
        if await ms.is_version_occupied(package_id, version):
            raise PublishError("该版本已被占用，不能用不同制品覆盖", 409)

        sid = new_submission_id()
        build = submission_build_dir(sid)
        build.mkdir(parents=True, exist_ok=True)
        zip_path = build / "upload.zip"
        zip_path.write_bytes(zip_bytes)

        # builds 配额粗检
        builds_root = get_builds_root()
        # 略：按用户统计可后续加强

        declared = normalize_declared(
            name_zh=(declared_meta or {}).get("name_zh") or "",
            name_en=(declared_meta or {}).get("name_en") or "",
            description=(declared_meta or {}).get("description") or "",
            tags=(declared_meta or {}).get("tags"),
            author=(declared_meta or {}).get("author") or "",
            submitter_id=submitter_id,
        )
        err = validate_declared(declared)
        if err:
            raise PublishError(err, 400)
        if package_id:
            existing_id = await ms.get_package_declared_identity(package_id)
            locked_en = ((existing_id or {}).get("name_en") or "").strip()
            if locked_en and declared["name_en"] != locked_en.lower():
                raise PublishError(f"该包英文名称已锁定为 {locked_en}，不能修改", 400)

        scan_progress = new_scan_progress()
        meta = dict(declared_meta or {})
        meta.update(declared)
        meta["scan_progress"] = scan_progress
        row = await ms.insert_submission(
            {
                "id": sid,
                "package_id": package_id,
                "declared_version": version,
                "submitter_id": submitter_id,
                "upload_sha256": upload_sha,
                "zip_uri": str(zip_path),
                "status": "QUEUED",
                "requested_visibility": requested_visibility,
                "idempotency_key": idempotency_key,
                "meta": meta,
            }
        )
        return {
            "submission_id": row["id"],
            "package_id": package_id,
            "status": row["status"],
            "declared_version": version,
            "scan_progress": copy.deepcopy(scan_progress),
        }

    async def retry_scan(
        self,
        submission_id: str,
        user_id: str,
        declared_meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """重试失败的提交，复用已上传的 ZIP。

        declared_meta 允许修正名称/描述/标签/作者等声明字段（例如英文名称与
        SKILL.md name 不一致导致的结构校验失败），无需用户重新上传文件。
        """
        sub = await ms.get_submission(submission_id)
        if not sub:
            raise PublishError("提交不存在", 404)
        if sub["submitter_id"] != user_id:
            from db import deep_storage

            if not await deep_storage.is_admin(user_id):
                raise PublishError("无权操作", 403)

        status = sub.get("status") or ""
        if status not in ("FAILED", "REJECTED"):
            raise PublishError("仅失败或被拒绝的提交可重试", 400)

        # 已上传的 ZIP 仍在才可复用；被清理时要求用户重新上传
        build = submission_build_dir(submission_id)
        zip_path = Path(sub.get("zip_uri") or (build / "upload.zip"))
        if not zip_path.is_file():
            raise PublishError("上传文件已清理，请重新上传 ZIP", 410)

        # 允许在重试时修正声明字段
        if declared_meta:
            meta = _submission_meta(sub)
            declared = normalize_declared(
                name_zh=declared_meta.get("name_zh")
                if declared_meta.get("name_zh") is not None
                else (meta.get("name_zh") or ""),
                name_en=declared_meta.get("name_en")
                if declared_meta.get("name_en") is not None
                else (meta.get("name_en") or ""),
                description=declared_meta.get("description")
                if declared_meta.get("description") is not None
                else (meta.get("description") or ""),
                tags=declared_meta.get("tags")
                if declared_meta.get("tags") is not None
                else meta.get("tags"),
                author=declared_meta.get("author")
                if declared_meta.get("author") is not None
                else (meta.get("author") or ""),
                submitter_id=sub.get("submitter_id") or "",
            )
            err = validate_declared(declared)
            if err:
                raise PublishError(err, 400)
            await ms.merge_submission_meta(submission_id, declared)

        # 重试复用同一 submission id：清空上一次失败留下的 findings，
        # 否则审核页/详情页会继续展示旧问题（如 STRUCT-004），与实际结果不符
        try:
            await ms.delete_findings(submission_id)
        except Exception:
            logger.exception("清理旧 findings 失败 submission=%s", submission_id)

        scan_progress = new_scan_progress()
        await ms.update_submission(
            submission_id,
            status="QUEUED",
            failure_reason="",
            lease_owner=None,
            lease_until=None,
        )
        await ms.merge_submission_meta(
            submission_id,
            {"scan_progress": scan_progress, "scan_rules": {}},
        )
        return {
            "submission_id": submission_id,
            "status": "QUEUED",
            "scan_progress": copy.deepcopy(scan_progress),
        }

    async def cancel(self, submission_id: str, user_id: str) -> Dict[str, Any]:
        sub = await ms.get_submission(submission_id)
        if not sub:
            raise PublishError("提交不存在", 404)
        if sub["submitter_id"] != user_id:
            raise PublishError("无权操作", 403)
        if sub["status"] not in ("AWAITING_REVIEW", "QUEUED", "VALIDATING", "SCANNING"):
            raise PublishError("当前状态不可取消", 400)
        if sub.get("version_id"):
            ver = await ms.get_version(sub["version_id"])
            if ver and ver["status"] == "REVIEW_PENDING":
                await ms.update_version(sub["version_id"], status="CANCELLED")
        await ms.update_submission(submission_id, status="CANCELLED")
        return {"submission_id": submission_id, "status": "CANCELLED"}

    async def withdraw_package(self, package_id: str, user_id: str) -> Dict[str, Any]:
        """作者删除/下架：已安装副本强制禁用，禁止再调用。"""
        pkg = await ms.get_package(package_id)
        if not pkg:
            raise PublishError("包不存在", 404)
        if pkg.get("owner_id") != user_id:
            from db import deep_storage

            if not await deep_storage.is_admin(user_id):
                raise PublishError("无权操作", 403)
        if pkg.get("delisted"):
            await _purge_withdrawn_repo(package_id, pkg)
            return {"package_id": package_id, "delisted": True, "install_count": 0}

        versions = await ms.list_versions_for_package(package_id)
        reviewed = any(
            (v.get("status") or "") in ("PUBLISHED", "REVIEW_PENDING", "QUARANTINED")
            for v in versions
        )
        for ver in versions:
            st = ver.get("status") or ""
            fields: Dict[str, Any] = {"artifact_state": "purged"}
            if st == "PUBLISHED":
                fields["status"] = "QUARANTINED"
            elif st == "REVIEW_PENDING":
                fields["status"] = "CANCELLED"
            await ms.update_version(ver["id"], **fields)
        await ms.set_current_published(package_id, None)
        await ms.clear_catalog_if_no_current(package_id)
        await ms.cancel_inflight_submissions(package_id)
        await ms.set_package_delisted(package_id, True)
        await _purge_withdrawn_repo(package_id, pkg)
        install_count = await ms.count_package_installs(package_id)
        logger.info(
            "[SkillWithdraw] package=%s by=%s reviewed=%s installs=%s",
            package_id,
            user_id,
            reviewed,
            install_count,
        )
        return {
            "package_id": package_id,
            "delisted": True,
            "reviewed": reviewed,
            "install_count": install_count,
        }

    async def quarantine(self, version_id: str, admin_user_id: str) -> Dict[str, Any]:
        from deep_agent_server.skills.admin_service import assert_admin

        await assert_admin(admin_user_id)
        ver = await ms.get_version(version_id)
        if not ver or ver["status"] != "PUBLISHED":
            raise PublishError("仅 PUBLISHED 可隔离", 400)
        package_id = ver["package_id"]
        await ms.update_version(version_id, status="QUARANTINED")
        pkg = await ms.get_package(package_id)
        if pkg and str(pkg.get("current_published_version_id")) == str(version_id):
            fallback = await ms.get_highest_other_published(package_id, version_id)
            await ms.set_current_published(
                package_id, fallback["id"] if fallback else None
            )
            if fallback:
                await ms.project_catalog_from_version(fallback, await ms.get_package(package_id))
            else:
                await ms.clear_catalog_if_no_current(package_id)
        return {"version_id": version_id, "status": "QUARANTINED"}

    async def import_trusted(
        self,
        *,
        source: Path,
        package_id: str,
        version: str,
        operator: str,
        policy: str = "trusted_import",
        reason: str = "",
        visibility: str = "public",
        origin: str = "official",
    ) -> Dict[str, Any]:
        if policy not in ("trusted_import", "emergency_import"):
            raise PublishError("无效 policy", 400)
        if policy == "emergency_import" and not reason:
            raise PublishError("emergency_import 需要 reason", 400)
        if not is_valid_semver(version):
            raise PublishError("version 非法", 400)

        # 准备 ZIP 或目录
        build_id = new_submission_id()
        build = submission_build_dir(build_id)
        unpacked = build / "unpacked"
        build.mkdir(parents=True, exist_ok=True)

        if source.is_file() and source.suffix.lower() == ".zip":
            shutil.copy2(source, build / "upload.zip")
            try:
                safe_unpack_zip(build / "upload.zip", unpacked)
            except UnpackError as e:
                raise PublishError(f"L0 失败: {e.message}", 400) from e
        elif source.is_dir():
            if unpacked.exists():
                shutil.rmtree(unpacked)
            shutil.copytree(source, unpacked)
        else:
            raise PublishError("source 必须是 ZIP 或目录", 400)

        # 结构校验不可跳过
        try:
            meta = validate_skill_structure(unpacked)
        except StructureError as e:
            raise PublishError(f"结构校验失败: {e.message}", 400) from e

        content_sha = hash_skill_payload(unpacked)
        # 同哈希已 published → 跳过
        existing = await ms.get_version_by_package_semver(package_id, version)
        if existing and existing.get("status") == "PUBLISHED" and existing.get("content_sha256") == content_sha:
            return {"skipped": True, "package_id": package_id, "version": version, "content_sha256": content_sha}

        await ms.upsert_package(package_id, operator, origin=origin, visibility=visibility)
        # 直接发布路径（跳过扫描/人审）
        from deep_agent_server.skills.marketplace.release import atomic_publish

        # 造 submission + version + approved review
        sid = build_id
        zip_uri = str(build / "upload.zip") if (build / "upload.zip").exists() else str(source)
        upload_sha = (
            hashlib.sha256((build / "upload.zip").read_bytes()).hexdigest()
            if (build / "upload.zip").exists()
            else content_sha
        )
        await ms.insert_submission(
            {
                "id": sid,
                "package_id": package_id,
                "declared_version": version,
                "submitter_id": operator,
                "upload_sha256": upload_sha,
                "zip_uri": zip_uri,
                "status": "AWAITING_REVIEW",
                "requested_visibility": visibility,
                "meta": {"policy": policy, "reason": reason, **meta},
            }
        )
        vid = str(uuid4())
        await ms.insert_version(
            {
                "id": vid,
                "package_id": package_id,
                "version": version,
                "submission_id": sid,
                "content_sha256": content_sha,
                "upload_sha256": upload_sha,
                "status": "REVIEW_PENDING",
                "name": meta.get("name", package_id),
                "name_en": meta.get("name", ""),
                "name_zh": "",
                "description": meta.get("description", ""),
                "tags": meta.get("tags") or [],
                "author": meta.get("author") or operator,
            }
        )
        await ms.update_submission(sid, version_id=vid)
        rid = await ms.create_pending_review(vid)
        await ms.decide_review(rid, operator, "approved", f"import:{policy}")
        published = await atomic_publish(vid, unpacked)
        return {
            "skipped": False,
            "package_id": package_id,
            "version": version,
            "version_id": published["id"],
            "content_sha256": content_sha,
            "policy": policy,
        }


_service: Optional[PublishService] = None


def get_publish_service() -> PublishService:
    global _service
    if _service is None:
        _service = PublishService()
    return _service
