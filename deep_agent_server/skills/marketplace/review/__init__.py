"""人审：approve → atomic_publish；reject → REJECTED。"""

from __future__ import annotations

from pathlib import Path

from deep_agent_server.skills.marketplace import storage as ms
from deep_agent_server.skills.marketplace.paths import submission_build_dir
from deep_agent_server.skills.marketplace.publish_service import PublishError
from deep_agent_server.skills.marketplace.release import atomic_publish


async def approve_version(version_id: str, reviewer_id: str, comment: str = "") -> dict:
    from deep_agent_server.skills.admin_service import assert_admin

    await assert_admin(reviewer_id)
    ver = await ms.get_version(version_id)
    if not ver or ver["status"] != "REVIEW_PENDING":
        raise PublishError("version 不可审核", 400)
    review = await ms.get_pending_review_for_version(version_id)
    if not review:
        raise PublishError("无待审记录", 400)
    await ms.decide_review(review["id"], reviewer_id, "approved", comment)

    sub_id = ver.get("submission_id")
    unpacked = submission_build_dir(sub_id) / "unpacked"
    if not unpacked.is_dir():
        raise PublishError("构建产物缺失，无法发布", 500)
    published = await atomic_publish(version_id, unpacked)
    return {"version_id": version_id, "status": published["status"], "package_id": ver["package_id"]}


async def reject_version(version_id: str, reviewer_id: str, comment: str = "") -> dict:
    from deep_agent_server.skills.admin_service import assert_admin

    await assert_admin(reviewer_id)
    ver = await ms.get_version(version_id)
    if not ver or ver["status"] != "REVIEW_PENDING":
        raise PublishError("version 不可拒绝", 400)
    review = await ms.get_pending_review_for_version(version_id)
    if review:
        await ms.decide_review(review["id"], reviewer_id, "rejected", comment)
    await ms.update_version(version_id, status="REJECTED")
    if ver.get("submission_id"):
        await ms.update_submission(ver["submission_id"], status="REJECTED")
    return {"version_id": version_id, "status": "REJECTED"}
