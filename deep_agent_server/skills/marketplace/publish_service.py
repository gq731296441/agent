"""PublishService：submit / import_trusted / quarantine。"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from config import get_logger
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
from deep_agent_server.skills.marketplace.semver import (
    is_strictly_greater,
    is_valid_semver,
    suggest_next_minor,
)
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

logger = get_logger(__name__)


def _read_skill_md_version(zip_bytes: bytes) -> Optional[str]:
    """从 ZIP 内 SKILL.md 的 frontmatter 读取 version。

    版本号以 SKILL.md 为唯一来源，提交时即落库，避免用户填写值与包内不一致。
    ZIP 尚未解包，这里只做最小解包（临时目录），复用 safe_unpack_zip 的全部安全
    限制；任何失败都返回 None，交由 worker 的完整校验给出精确报错。
    """
    from deep_agent_server.skills.marketplace.validate.unpack import (
        UnpackError,
        safe_unpack_zip,
    )
    from deep_agent_server.skills.parser import read_skill_md_version

    tmp = Path(tempfile.mkdtemp(prefix="skill-md-"))
    try:
        zip_path = tmp / "upload.zip"
        zip_path.write_bytes(zip_bytes)
        target = tmp / "unpacked"
        safe_unpack_zip(zip_path, target)
        return read_skill_md_version(str(target))
    except UnpackError:
        return None
    except Exception:
        logger.exception("[SkillSubmit] 读取 SKILL.md 版本失败")
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
            logger.info("[SkillWithdraw] 已删除仓库目录 package={}", pid)
        return bool(removed)
    except Exception:
        logger.exception("[SkillWithdraw] 删除仓库目录失败 package={}", pid)
        return False


class PublishError(Exception):
    def __init__(
        self,
        message: str,
        code: int = 400,
        *,
        error_type: str = "",
        detail: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        # error_type：机器可读的错误分类（如 version_conflict），供前端做差异化展示
        # detail：结构化补充信息（如当前最高版本、推荐版本），供前端精准提示
        self.error_type = error_type
        self.detail = detail or {}

    def to_payload(self) -> Dict[str, Any]:
        """转成 API 响应 data，前端可直接读取 error_type / detail。"""
        return {"error_type": self.error_type, "detail": self.detail}


class PublishService:
    async def submit(
        self,
        *,
        submitter_id: str,
        zip_bytes: bytes,
        version: Optional[str] = None,
        package_id: Optional[str] = None,
        requested_visibility: str = "public",
        idempotency_key: Optional[str] = None,
        declared_meta: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """提交新包/新版本。

        版本号以 SKILL.md 为唯一来源：调用方可以不传 version，服务端在落盘后
        从 ZIP 内的 SKILL.md frontmatter 读取；若调用方传了 version 且与包内
        不一致，直接拒绝，避免出现「填 1.0.0、包内 1.1.1」的错配。
        """
        if not submitter_id:
            raise PublishError("缺少 submitter_id", 400)
        requested_visibility = "public"

        max_bytes = int(getattr(app_config, "SKILL_UPLOAD_MAX_BYTES", 52428800))
        if len(zip_bytes) > max_bytes:
            raise PublishError(f"ZIP 超过 {max_bytes // 1024 // 1024} MiB", 400)

        inflight = await ms.count_inflight(submitter_id)
        if inflight >= int(getattr(app_config, "SKILL_UPLOAD_MAX_INFLIGHT", 2)):
            raise PublishError("进行中提交过多", 429)
        # 待审数量独立限流（AWAITING_REVIEW 不计入 inflight，避免用户被卡死）
        pending = await ms.count_pending_review(submitter_id)
        if pending >= int(getattr(app_config, "SKILL_MAX_PENDING_REVIEW", 5)):
            raise PublishError("待审核的提交过多，请等待审核完成后再上传", 429)
        hourly = await ms.count_uploads_last_hour(submitter_id)
        if hourly >= int(getattr(app_config, "SKILL_UPLOAD_RATE_PER_HOUR", 10)):
            raise PublishError("上传速率超限", 429)

        upload_sha = hashlib.sha256(zip_bytes).hexdigest()

        # 版本号以 SKILL.md 为准：从包内读取，客户端传入值仅用于一致性校验
        md_version = await asyncio.to_thread(_read_skill_md_version, zip_bytes)
        declared_version = (version or "").strip()
        if declared_version and md_version and declared_version != md_version:
            raise PublishError(
                f"版本号须与 SKILL.md 的 version「{md_version}」一致，请勿手工填写",
                400,
            )
        version = md_version or declared_version
        if not version:
            raise PublishError("无法从 SKILL.md 读取 version，请检查 frontmatter", 400)
        if not is_valid_semver(version):
            raise PublishError(
                f"SKILL.md 的 version「{version}」不是 MAJOR.MINOR.PATCH 格式", 400
            )

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

        # 递增检查：版本号来自 ZIP 内 SKILL.md，用户看不到输入框，报错必须讲清
        # 「版本从哪来 / 当前最高是多少 / 应该改成什么」，否则用户无从下手。
        max_v = await ms.max_occupied_semver(package_id)
        if max_v and not is_strictly_greater(version, max_v):
            suggested = suggest_next_minor(max_v) or ""
            tip = f"新版本号需严格大于当前最高版本 {max_v}"
            if suggested:
                tip += f"（例如 {suggested}）"
            tip += "，请修改 ZIP 内 SKILL.md 的 version 后重新上传"
            occupied = await ms.is_version_occupied(package_id, version)
            head = (
                f"版本 {version} 已被占用，"
                if occupied
                else f"版本 {version} 低于当前最高版本 {max_v}，"
            )
            raise PublishError(
                head + tip,
                400,
                error_type="version_conflict",
                detail={
                    "declared_version": version,
                    "max_version": max_v,
                    "suggested_version": suggested,
                    "occupied": occupied,
                },
            )

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
        from asyncpg.exceptions import UniqueViolationError

        try:
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
        except UniqueViolationError:
            # 幂等键并发竞态：另一请求已先插入同键记录，返回其结果。
            existing = (
                await ms.find_by_idempotency(submitter_id, idempotency_key)
                if idempotency_key
                else None
            )
            if existing:
                return {
                    "submission_id": existing["id"],
                    "package_id": existing.get("package_id"),
                    "status": existing.get("status"),
                    "declared_version": existing.get("declared_version"),
                    "scan_progress": copy.deepcopy(scan_progress),
                }
            raise PublishError("提交冲突，请重试", 409)
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

        # 版本号以 SKILL.md 为唯一来源：历史提交可能因手工填写而落库了错配值，
        # 重试时按当前 ZIP 重新读取并覆盖，避免旧值继续触发不一致校验。
        try:
            zip_bytes = await asyncio.to_thread(zip_path.read_bytes)
            md_version = await asyncio.to_thread(_read_skill_md_version, zip_bytes)
        except Exception:
            logger.exception("读取 SKILL.md 版本失败 submission={}", submission_id)
            md_version = None
        if md_version:
            current = str(sub.get("declared_version") or "").strip()
            if md_version != current:
                if not is_valid_semver(md_version):
                    raise PublishError(
                        f"SKILL.md 的 version「{md_version}」不是 MAJOR.MINOR.PATCH 格式",
                        400,
                    )
                await ms.update_submission(submission_id, declared_version=md_version)
                sub["declared_version"] = md_version

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
            logger.exception("清理旧 findings 失败 submission={}", submission_id)

        scan_progress = new_scan_progress()
        # 重试视为新的处理周期：重置 attempt_count，避免被 MAX_ATTEMPTS 立刻终结。
        await ms.update_submission(
            submission_id,
            expected_status={"FAILED", "REJECTED"},
            status="QUEUED",
            failure_reason="",
            lease_owner=None,
            lease_until=None,
            attempt_count=0,
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
        # 状态守卫：仅当仍处于可取消状态时才置 CANCELLED，防止与 worker 竞态。
        ok = await ms.update_submission(
            submission_id,
            expected_status={"AWAITING_REVIEW", "QUEUED", "VALIDATING", "SCANNING"},
            status="CANCELLED",
        )
        if not ok:
            raise PublishError("提交状态已变化，无法取消", 409)
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
            # 已下架：再次删除 = 物理清理 DB，避免「我的上传」里幽灵卡片
            await _purge_withdrawn_repo(package_id, pkg)
            purged = await ms.hard_delete_package(package_id)
            logger.info(
                "[SkillWithdraw] hard-delete delisted package={} by={} result={}",
                package_id,
                user_id,
                purged,
            )
            return {
                "package_id": package_id,
                "delisted": True,
                "hard_deleted": True,
                "install_count": 0,
            }

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
            "[SkillWithdraw] package={} by={} reviewed={} installs={}",
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
        # 先建 pending review，再由 atomic_publish 在同一事务内决定并发布
        await ms.create_pending_review(vid)
        published = await atomic_publish(vid, unpacked, operator, f"import:{policy}")
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
