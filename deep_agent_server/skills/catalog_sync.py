"""从 skill-repo 扫描并同步 skill_catalog 表。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List

from config.config import app_config
from config import get_logger
from db import deep_storage
from deep_agent_server.skills.parser import parse_skill_md
from deep_agent_server.skills.selector import BUILTIN_SKILL_IDS

logger = get_logger(__name__)


@dataclass
class SkillCatalogEntry:
    skill_id: str
    name: str
    description: str
    version: str
    tags: List[str]
    author: str
    repo_rel_path: str


def scan_skill_repo(repo_root: str | Path | None = None) -> List[SkillCatalogEntry]:
    """遍历 repo 子目录，要求存在 SKILL.md。"""
    root = Path(repo_root or app_config.get_skill_repo_root())
    logger.info(f"[SkillSync] 扫描 skill-repo: {root}")
    if not root.is_dir():
        logger.error(
            f"[SkillSync] repo 目录不存在: {root} —— "
            "请检查: 1) skill-repo/ 是否打进 Docker 镜像或挂载; "
            "2) 环境变量 SKILL_REPO_ROOT 是否配置正确; "
            "3) K8s ConfigMap/Volume 是否已创建"
        )
        return []

    entries: List[SkillCatalogEntry] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        skill_id = child.name
        if skill_id.startswith(".") or ".." in skill_id:
            continue
        # 内置默认技能不入 catalog（不参与订阅/退订）
        if skill_id in BUILTIN_SKILL_IDS:
            logger.debug(f"[SkillSync] 跳过内置技能: {skill_id}")
            continue

        skill_dir = child
        repo_rel = skill_id
        # marketplace 布局：releases/{semver}/SKILL.md
        releases = child / "releases"
        if releases.is_dir() and not (child / "SKILL.md").is_file():
            from deep_agent_server.skills.marketplace.semver import compare_semver

            candidates = [
                d
                for d in releases.iterdir()
                if d.is_dir()
                and not d.name.endswith(".publishing")
                and (d / "SKILL.md").is_file()
            ]
            if not candidates:
                continue
            best = candidates[0]
            for c in candidates[1:]:
                if compare_semver(c.name, best.name) > 0:
                    best = c
            skill_dir = best
            repo_rel = f"{skill_id}/releases/{best.name}"

        if not (skill_dir / "SKILL.md").is_file():
            continue
        meta = parse_skill_md(str(skill_dir))
        if not meta:
            continue
        # releases 布局以目录名为发行号优先（API declared_version）
        if skill_dir.parent.name == "releases":
            version = skill_dir.name
        else:
            version = meta.version or "1.0.0"
        entries.append(
            SkillCatalogEntry(
                skill_id=skill_id,
                name=meta.name or skill_id,
                description=meta.description or "",
                version=version,
                tags=list(meta.tags or []),
                author=meta.author or "",
                repo_rel_path=repo_rel,
            )
        )
    return entries


async def sync_catalog_from_repo(repo_root: str | Path | None = None) -> dict:
    """扫描 repo 并 upsert skill_catalog；缺失目录标记 archived。

    默认从 VM .skill-repo/ 扫描（非内置 skill）。
    """
    root = repo_root or app_config.get_vm_skill_repo_root()
    entries = scan_skill_repo(root)
    if not entries:
        logger.warning(
            "[SkillSync] 扫描结果为空，跳过同步 —— "
            "如确认 skill-repo 中有 SKILL.md 文件，请检查路径权限和目录结构"
        )
        return {"synced": 0, "deleted": 0, "skill_ids": []}

    delisted_ids = set()
    market_package_ids: set = set()
    try:
        from deep_agent_server.skills.marketplace import storage as ms

        delisted_ids = set(await ms.list_delisted_package_ids())
        market_package_ids = set(await ms.list_package_ids())
    except Exception:
        logger.exception("[SkillSync] 读取市场包状态失败，继续同步")

    present_ids: List[str] = []
    for entry in entries:
        present_ids.append(entry.skill_id)
        # 用户上传包由审核发布写入 catalog，repo 同步不得覆盖中文名/标签
        if str(entry.skill_id).startswith("uskill-"):
            continue
        # 已转为市场包的 repo 技能：catalog 由发布流程维护，
        # 避免仓库扁平目录覆盖版本/名称（迁移后即使扁平目录仍在也安全）
        if entry.skill_id in market_package_ids:
            continue
        if entry.skill_id in delisted_ids:
            await deep_storage.set_skill_catalog_status(entry.skill_id, "archived")
            continue
        await deep_storage.upsert_skill_catalog(
            entry.skill_id,
            entry.name,
            entry.description,
            entry.version,
            entry.tags,
            entry.author,
            entry.repo_rel_path,
            status="published",
            name_en=entry.name or entry.skill_id,
        )
    deleted = await deep_storage.delete_missing_skills(present_ids)
    logger.info(
        f"[SkillSync] synced={len(entries)} deleted={deleted} repo={root}"
    )
    return {"synced": len(entries), "deleted": deleted, "skill_ids": present_ids}


async def sync_catalog_from_vm_repo() -> dict:
    """从 VM .skill-repo/ 同步到 catalog（上传后增量同步时用）。"""
    from deep_agent_server.skills.vm_repo import sync_catalog_from_vm_repo as _vm_sync

    return await _vm_sync()
