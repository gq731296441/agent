"""从 skill-repo 扫描并同步 skill_catalog 表。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List

from config.config import app_config
from config import get_logger
from db import deep_storage
from deep_agent_server.skills.parser import parse_skill_md

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
    if not root.is_dir():
        logger.warning(f"[SkillSync] repo 目录不存在: {root}")
        return []

    entries: List[SkillCatalogEntry] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        skill_id = child.name
        if skill_id.startswith(".") or ".." in skill_id:
            continue
        if not (child / "SKILL.md").is_file():
            continue
        meta = parse_skill_md(str(child))
        if not meta:
            continue
        entries.append(
            SkillCatalogEntry(
                skill_id=skill_id,
                name=meta.name or skill_id,
                description=meta.description or "",
                version=meta.version or "1.0.0",
                tags=list(meta.tags or []),
                author=meta.author or "",
                repo_rel_path=skill_id,
            )
        )
    return entries


async def sync_catalog_from_repo(repo_root: str | Path | None = None) -> dict:
    """扫描 repo 并 upsert skill_catalog；缺失目录标记 archived。"""
    entries = scan_skill_repo(repo_root)
    present_ids: List[str] = []
    for entry in entries:
        present_ids.append(entry.skill_id)
        await deep_storage.upsert_skill_catalog(
            entry.skill_id,
            entry.name,
            entry.description,
            entry.version,
            entry.tags,
            entry.author,
            entry.repo_rel_path,
            status="published",
        )
    archived = await deep_storage.archive_missing_skills(present_ids)
    logger.info(
        f"[SkillSync] synced={len(entries)} archived={archived} repo={repo_root or app_config.get_skill_repo_root()}"
    )
    return {"synced": len(entries), "archived": archived, "skill_ids": present_ids}
