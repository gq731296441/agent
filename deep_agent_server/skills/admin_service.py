"""Skill 管理员服务：审批流程管理。

- 用户上传 skill 后状态为 draft
- 管理员审核通过 → published
- 管理员审核拒绝 → 保留 draft
- 删除/更新操作也需审批
"""

import logging

from db import deep_storage
from deep_agent_server.skills.vm_repo import delete_skill_from_repo

logger = logging.getLogger(__name__)


class AdminServiceError(Exception):
    """管理员操作异常。"""
    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.code = code


async def check_admin(user_id: str) -> bool:
    """检查用户是否为管理员。"""
    return await deep_storage.is_admin(user_id)


async def assert_admin(user_id: str) -> None:
    """断言用户是管理员，否则抛出异常。"""
    if not user_id or not await deep_storage.is_admin(user_id):
        raise AdminServiceError("需要管理员权限", 403)


async def submit_skill_for_review(
    skill_id: str,
    action_type: str,
    requested_by: str,
    *,
    old_version: str = "",
    new_version: str = "",
    detail: dict | None = None,
) -> str:
    """为 skill 操作提交审批请求。

    Args:
        skill_id: skill 标识
        action_type: 'publish' | 'update' | 'delete'
        requested_by: 发起操作的用户
        old_version: update 时的旧版本
        new_version: update 时的新版本
        detail: 额外信息

    Returns:
        action_id 审批记录 ID
    """
    catalog = await deep_storage.get_skill_catalog(skill_id)
    if not catalog:
        raise AdminServiceError("Skill 不存在", 404)

    action_id = await deep_storage.create_pending_action(
        skill_id=skill_id,
        action_type=action_type,
        requested_by=requested_by,
        old_version=old_version or str(catalog.get("version") or ""),
        new_version=new_version or "",
        detail=detail or {},
    )
    logger.info(
        f"[AdminService] 提交审批 skill={skill_id} action={action_type} "
        f"user={requested_by} id={action_id}"
    )
    return action_id


async def approve_publish(action_id: str, reviewer: str) -> dict:
    """审批通过 skill 发布：状态 draft → published。"""
    await assert_admin(reviewer)

    action = await deep_storage.get_pending_action(action_id)
    if not action:
        raise AdminServiceError("审批记录不存在", 404)
    if action["status"] != "pending":
        raise AdminServiceError("该审批已处理", 400)

    skill_id = action["skill_id"]
    catalog = await deep_storage.get_skill_catalog(skill_id)
    if not catalog:
        raise AdminServiceError("Skill 不存在", 404)

    if action["action_type"] == "publish":
        await deep_storage.set_skill_catalog_status(skill_id, "published")
    elif action["action_type"] == "update":
        # 更新操作：保持 published 状态即可（文件已在上传时更新）
        await deep_storage.set_skill_catalog_status(skill_id, "published")
    elif action["action_type"] == "delete":
        await delete_skill_from_repo(skill_id)
        await deep_storage.delete_skill_catalog_full(skill_id)

    await deep_storage.approve_action(action_id, reviewer)
    logger.info(
        f"[AdminService] 审批通过 skill={skill_id} action={action['action_type']} "
        f"reviewer={reviewer}"
    )
    return {"skill_id": skill_id, "action": action["action_type"], "status": "approved"}


async def reject_action(action_id: str, reviewer: str, comment: str = "") -> dict:
    """审批拒绝。"""
    await assert_admin(reviewer)

    action = await deep_storage.get_pending_action(action_id)
    if not action:
        raise AdminServiceError("审批记录不存在", 404)
    if action["status"] != "pending":
        raise AdminServiceError("该审批已处理", 400)

    await deep_storage.reject_action(action_id, reviewer, comment)
    logger.info(
        f"[AdminService] 审批拒绝 skill={action['skill_id']} "
        f"action={action['action_type']} reviewer={reviewer}"
    )
    return {"skill_id": action["skill_id"], "action": action["action_type"], "status": "rejected"}


async def list_pending_reviews(
    page: int = 1,
    page_size: int = 20,
    status: str = "pending",
) -> tuple:
    """分页查询待审批列表。"""
    return await deep_storage.list_pending_actions(
        page=page, page_size=page_size, status=status
    )
