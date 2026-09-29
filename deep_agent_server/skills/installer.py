"""Skill 订阅安装：从 skill-repo 拷贝到用户工作区。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import List

from config import get_logger
from config.config import app_config
from db import deep_storage
from deep_agent_server.skills.version_utils import versions_equal
from deep_agent_server.workspace import ensure_user_dirs, get_skills_dir, remove_skill_installation, sync_skill_to_sandbox_bind_path

logger = get_logger(__name__)


class SkillInstallError(Exception):
    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.code = code


def get_repo_skill_dir(skill_id: str) -> Path:
    """获取 skill 源目录路径。

    优先从 VM .skill-repo/ 查找（用户上传 + 迁移的 skill），
    若不存在则回退到项目 skill-repo/（仅内置 skill）。
    """
    sid = (skill_id or "").strip().strip("/\\")
    if not sid or ".." in sid or "/" in sid or "\\" in sid:
        raise SkillInstallError(f"非法 skill_id: {skill_id}", 400)

    # 优先 VM repo
    vm_path = Path(app_config.get_vm_skill_repo_root()) / sid
    if vm_path.is_dir() and (vm_path / "SKILL.md").is_file():
        return vm_path

    # 回退项目 repo（内置 skill）
    project_path = Path(app_config.get_skill_repo_root()) / sid
    if project_path.is_dir() and (project_path / "SKILL.md").is_file():
        return project_path

    raise SkillInstallError(f"Skill 源目录不存在: {sid}", 404)


def _copy_skill_tree(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)


async def install_skill_for_user(
    user_id: str,
    skill_id: str,
    *,
    overwrite: bool = False,
) -> dict:
    """从 catalog + repo 安装到用户 skills/ 目录。"""
    sid = (skill_id or "").strip()
    catalog = await deep_storage.get_skill_catalog(sid)
    if not catalog or catalog.get("status") != "published":
        raise SkillInstallError("Skill 不存在或未发布", 404)

    existing = await deep_storage.get_installed_skill(user_id, sid)
    if existing and not overwrite:
        raise SkillInstallError("已订阅该 Skill，请先取消或使用更新", 409)

    repo_dir = get_repo_skill_dir(sid)
    ensure_user_dirs(user_id)
    target = get_skills_dir(user_id) / sid
    _copy_skill_tree(repo_dir, target)
    sync_skill_to_sandbox_bind_path(user_id, sid, force=True)

    version = catalog.get("version") or "1.0.0"
    record_id = await deep_storage.install_skill_record(
        user_id,
        sid,
        catalog.get("name") or sid,
        str(target),
        version,
    )
    await deep_storage.increment_skill_install_count(sid)
    await deep_storage.insert_audit_log(
        user_id,
        "skill_subscribe" if not overwrite else "skill_upgrade",
        target=sid,
        detail={"install_dir": str(target), "version": version},
    )
    from deep_agent_server.quota_service import refresh_workspace_usage
    from deep_agent_server.workspace_service import index_skill_tree

    await index_skill_tree(user_id, sid)
    await refresh_workspace_usage(user_id)
    logger.info(
        f"[SkillInstall] user={user_id} skill={sid} version={version} overwrite={overwrite}"
    )
    return {
        "installed_skill_id": record_id,
        "skill_id": sid,
        "version": version,
        "install_dir": str(target),
    }


async def upgrade_skill_for_user(user_id: str, skill_id: str) -> dict:
    """若 catalog 版本更新则覆盖安装。"""
    sid = (skill_id or "").strip()
    existing = await deep_storage.get_installed_skill(user_id, sid)
    if not existing:
        raise SkillInstallError("未订阅该 Skill", 404)

    catalog = await deep_storage.get_skill_catalog(sid)
    if not catalog or catalog.get("status") != "published":
        raise SkillInstallError("Skill 已从市场下架", 404)

    latest = catalog.get("version") or "1.0.0"
    installed = existing.get("version") or "1.0.0"
    if versions_equal(latest, installed):
        return {
            "skill_id": sid,
            "upgraded": False,
            "version": installed,
            "message": "已是最新版本",
        }

    result = await install_skill_for_user(user_id, sid, overwrite=True)
    result["upgraded"] = True
    result["previous_version"] = installed
    return result


async def uninstall_skill_for_user(user_id: str, skill_id: str) -> None:
    sid = (skill_id or "").strip()
    if await deep_storage.is_skill_bound_to_agents(user_id, sid):
        raise SkillInstallError("该 Skill 已被订阅智能体绑定，请先解绑", 400)

    had = await deep_storage.get_installed_skill(user_id, sid)
    user_skill_path = get_skills_dir(user_id) / sid
    had_dir = user_skill_path.exists()
    try:
        remove_skill_installation(user_id, sid)
    except ValueError as e:
        raise SkillInstallError(str(e), 400) from e

    ok = await deep_storage.delete_installed_skill(user_id, sid)
    if not ok and not had and not had_dir:
        raise SkillInstallError("未订阅该 Skill", 404)

    from deep_agent_server.quota_service import refresh_workspace_usage

    await refresh_workspace_usage(user_id)
    await deep_storage.insert_audit_log(user_id, "skill_unsubscribe", target=sid)


def sync_skills_to_agent(agent_id: str, market_skill_ids: List[str]) -> None:
    """将 skill-repo 中的 skill 全量覆盖到订阅智能体专属目录。

    每次调用时清空旧目录后重建，确保与绑定列表完全一致。
    文件从 skill-repo/ 拷贝，不依赖创建者的 user_installed_skills。
    """
    from deep_agent_server.workspace import get_agent_skills_dir

    sid_list = [s.strip() for s in market_skill_ids if s and s.strip()]
    agent_skills = get_agent_skills_dir(agent_id)

    # 清空旧目录后重建
    if agent_skills.exists():
        shutil.rmtree(agent_skills)
    agent_skills.mkdir(parents=True, exist_ok=True)

    synced = 0
    for sid in sid_list:
        try:
            repo_dir = get_repo_skill_dir(sid)
        except SkillInstallError:
            logger.warning(
                f"[SkillInstall] sync_skills_to_agent 跳过: skill_id={sid} "
                f"在 skill-repo 中不存在"
            )
            continue
        target = agent_skills / sid
        shutil.copytree(repo_dir, target)
        synced += 1
        logger.info(
            f"[SkillInstall] synced skill to agent agent_id={agent_id} "
            f"skill={sid} src={repo_dir} dst={target}"
        )

    logger.info(
        f"[SkillInstall] sync_skills_to_agent done agent_id={agent_id} "
        f"synced={synced} total_bound={len(sid_list)}"
    )
