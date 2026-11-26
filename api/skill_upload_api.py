"""Skill 上传 API：异步 submit + 查询/重试/取消。"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, File, Form, Header, Query, UploadFile
from pydantic import BaseModel, Field

from common.schemas import ApiResponse, ListApiResponse
from config import get_logger
from deep_agent_server.skills.marketplace import storage as ms
from deep_agent_server.skills.marketplace.publish_service import (
    PublishError,
    get_publish_service,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/skills", tags=["skills"])


def _with_scan_progress(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return row
    data = dict(row)
    meta = data.get("meta")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    if not isinstance(meta, dict):
        meta = {}
    data["scan_progress"] = meta.get("scan_progress")
    return data


@router.get("/my-skills", response_model=ListApiResponse)
async def list_my_skills(user_id: str = Query(...)):
    rows = await ms.list_submissions_for_user(user_id)
    return ListApiResponse.success(data=[_with_scan_progress(r) for r in rows])


@router.get("/submissions", response_model=ListApiResponse)
async def list_my_submissions(user_id: str = Query(...), limit: int = Query(50, ge=1, le=200)):
    rows = await ms.list_submissions_for_user(user_id, limit=limit)
    return ListApiResponse.success(data=[_with_scan_progress(r) for r in rows])


@router.get("/uploads", response_model=ListApiResponse)
async def list_my_uploads(user_id: str = Query(...), limit: int = Query(50, ge=1, le=200)):
    rows = await ms.list_uploads_for_user(user_id, limit=limit)
    data = []
    for row in rows:
        item = _with_scan_progress(row)
        st = item.get("status") or ""
        item["reviewed"] = st in ("AWAITING_REVIEW", "PROMOTED") or bool(item.get("version_id"))
        data.append(item)
    return ListApiResponse.success(data=data)


@router.get("/uploads/{package_id}", response_model=ApiResponse)
async def get_my_upload(package_id: str, user_id: str = Query(...)):
    pkg = await ms.get_package(package_id)
    if not pkg:
        return ApiResponse.error(msg="不存在", code=404)
    if pkg.get("owner_id") != user_id:
        from db import deep_storage

        if not await deep_storage.is_admin(user_id):
            return ApiResponse.error(msg="不存在", code=404)
    subs = await ms.list_submissions_for_package(package_id, limit=20)
    latest = _with_scan_progress(subs[0]) if subs else None
    findings = []
    if latest:
        findings = await ms.list_findings(latest["id"])
        latest["findings"] = findings
    versions = await ms.list_versions_for_package(package_id)
    install_count = await ms.count_package_installs(package_id)
    return ApiResponse.success(
        data={
            "package": pkg,
            "latest_submission": latest,
            "submissions": [_with_scan_progress(s) for s in subs],
            "versions": versions,
            "findings": findings,
            "install_count": install_count,
            "reviewed": any(
                (v.get("status") or "") in ("PUBLISHED", "REVIEW_PENDING", "QUARANTINED")
                for v in versions
            )
            or any(
                (s.get("status") or "") in ("AWAITING_REVIEW", "PROMOTED") for s in subs
            ),
        }
    )


@router.delete("/uploads/{package_id}", response_model=ApiResponse)
async def delete_my_upload(package_id: str, user_id: str = Query(...)):
    try:
        result = await get_publish_service().withdraw_package(package_id, user_id)
        logger.info(
            "[SkillAudit] action=withdraw user={} pkg={} installs={} hard_deleted={}",
            user_id,
            package_id,
            result.get("install_count"),
            result.get("hard_deleted"),
        )
        msg = "已删除" if result.get("hard_deleted") else "已删除并下架"
        return ApiResponse.success(data=result, msg=msg)
    except PublishError as e:
        logger.info(
            "[SkillAudit] action=withdraw user={} pkg={} rejected code={} msg={}",
            user_id, package_id, e.code, e.message,
        )
        return ApiResponse.error(msg=e.message, code=e.code)
    except Exception:
        logger.exception("[SkillWithdraw] failed user={} pkg={}", user_id, package_id)
        return ApiResponse.error(msg="操作失败，请稍后重试或联系管理员", code=500)


@router.get("/submissions/{submission_id}", response_model=ApiResponse)
async def get_submission(submission_id: str, user_id: str = Query(...)):
    sub = await ms.get_submission(submission_id)
    if not sub:
        return ApiResponse.error(msg="不存在", code=404)
    if sub["submitter_id"] != user_id:
        from db import deep_storage

        if not await deep_storage.is_admin(user_id):
            return ApiResponse.error(msg="不存在", code=404)
    findings = await ms.list_findings(submission_id)
    data = _with_scan_progress(dict(sub))
    data["findings"] = findings
    return ApiResponse.success(data=data)


@router.post("/upload", response_model=ApiResponse)
async def upload_skill(
    user_id: str = Query(...),
    file: UploadFile = File(...),
    version: str = Form(""),
    package_id: str = Form(""),
    requested_visibility: str = Form("public"),
    name_zh: str = Form(""),
    name_en: str = Form(""),
    description: str = Form(""),
    tags: str = Form(""),
    author: str = Form(""),
    idempotency_key_header: Optional[str] = Header(None, alias="Idempotency-Key"),
    idempotency_key: str = Form(""),
):
    """异步提交：返回 submission_id，不在请求内跑完扫描。"""
    key = (idempotency_key_header or idempotency_key or "").strip() or None
    try:
        # 流式读取并硬限
        from config.config import app_config

        max_bytes = int(getattr(app_config, "SKILL_UPLOAD_MAX_BYTES", 52428800))
        chunks = []
        total = 0
        while True:
            chunk = await file.read(1024 * 256)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                return ApiResponse.error(msg=f"ZIP 超过 {max_bytes // 1024 // 1024} MiB", code=400)
            chunks.append(chunk)
        zip_bytes = b"".join(chunks)
        if not zip_bytes:
            return ApiResponse.error(msg="空文件", code=400)

        svc = get_publish_service()
        # version 可不传：服务端从 ZIP 内 SKILL.md 读取；传了则做一致性校验
        result = await svc.submit(
            submitter_id=user_id,
            zip_bytes=zip_bytes,
            version=(version or "").strip() or None,
            package_id=(package_id or "").strip() or None,
            requested_visibility="public",
            idempotency_key=key,
            declared_meta={
                "name_zh": name_zh,
                "name_en": name_en,
                "description": description,
                "tags": tags,
                "author": author or user_id,
            },
        )
        logger.info(
            "[SkillAudit] action=upload user={} submission={} pkg={} version={} status={}",
            user_id,
            result.get("submission_id"),
            result.get("package_id"),
            result.get("declared_version"),
            result.get("status"),
        )
        return ApiResponse.success(data=result, msg="已提交")
    except PublishError as e:
        logger.info(
            "[SkillAudit] action=upload user={} rejected code={} msg={}",
            user_id, e.code, e.message,
        )
        # 版本冲突等结构化错误：附带 error_type/detail，供前端精准展示
        payload = e.to_payload() if (e.error_type or e.detail) else None
        return ApiResponse.error(msg=e.message, code=e.code, data=payload)
    except Exception:
        logger.exception("[SkillUpload] submit failed user={}", user_id)
        return ApiResponse.error(msg="上传失败，请稍后重试或联系管理员", code=500)


class RetryBody(BaseModel):
    """重试时可选修正的声明字段（复用已上传 ZIP，无需重新上传）。"""

    name_zh: Optional[str] = None
    name_en: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[Any] = None
    author: Optional[str] = None


@router.post("/submissions/{submission_id}/retry", response_model=ApiResponse)
async def retry_submission(
    submission_id: str,
    user_id: str = Query(...),
    body: Optional[RetryBody] = Body(None),
):
    try:
        declared = body.model_dump(exclude_none=True) if body else None
        result = await get_publish_service().retry_scan(
            submission_id, user_id, declared or None
        )
        logger.info(
            "[SkillAudit] action=retry user={} submission={} status={}",
            user_id, submission_id, result.get("status"),
        )
        return ApiResponse.success(data=result, msg="已重新入队")
    except PublishError as e:
        logger.info(
            "[SkillAudit] action=retry user={} submission={} rejected code={} msg={}",
            user_id, submission_id, e.code, e.message,
        )
        return ApiResponse.error(msg=e.message, code=e.code)
    except Exception:
        logger.exception("[SkillRetry] failed user={} submission={}", user_id, submission_id)
        return ApiResponse.error(msg="重试失败，请稍后重试或联系管理员", code=500)


@router.post("/submissions/{submission_id}/cancel", response_model=ApiResponse)
async def cancel_submission(submission_id: str, user_id: str = Query(...)):
    try:
        result = await get_publish_service().cancel(submission_id, user_id)
        logger.info(
            "[SkillAudit] action=cancel user={} submission={} status={}",
            user_id, submission_id, result.get("status"),
        )
        return ApiResponse.success(data=result, msg="已取消")
    except PublishError as e:
        logger.info(
            "[SkillAudit] action=cancel user={} submission={} rejected code={} msg={}",
            user_id, submission_id, e.code, e.message,
        )
        return ApiResponse.error(msg=e.message, code=e.code)
    except Exception:
        logger.exception("[SkillCancel] failed user={} submission={}", user_id, submission_id)
        return ApiResponse.error(msg="取消失败，请稍后重试或联系管理员", code=500)


# 旧 update/delete/export：update 改为提示走新 submit；保留 export 兼容
@router.put("/update/{skill_id}", response_model=ApiResponse)
async def update_skill_legacy(skill_id: str, user_id: str = Query(...)):
    return ApiResponse.error(
        msg="请使用 POST /skills/upload 并指定 package_id 与更高 SemVer 提交新版本",
        code=410,
    )


@router.delete("/delete/{skill_id}", response_model=ApiResponse)
async def delete_skill_legacy(skill_id: str, user_id: str = Query(...)):
    return ApiResponse.error(msg="删除请联系管理员隔离或下架", code=410)


@router.get("/{skill_id}/export")
async def export_skill(skill_id: str, user_id: str = Query("")):
    from urllib.parse import quote

    from fastapi import HTTPException
    from fastapi.responses import Response

    from deep_agent_server.skills.vm_repo import export_skill_as_zip

    try:
        zip_bytes, filename = export_skill_as_zip(skill_id)
        encoded = quote(filename)
        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{filename}"; '
                    f"filename*=UTF-8''{encoded}"
                ),
            },
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
