"""技能逻辑 ID 与安装目录名映射。

market 包安装目录使用英文名（SKILL.md `name` / catalog.name_en），
使 agent 在 `ls /skills` 时看到的目录名与系统提示中的技能名一致；
内置技能与历史安装沿用 skill_id 作为目录名。

目录内 `.install-meta.json` 记录 package_id，用于从逻辑 ID 反查目录。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

INSTALL_META_FILENAME = ".install-meta.json"

# 目录名需满足 deepagents 技能名约束：小写字母/数字/单连字符
_SAFE_DIR_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def read_install_meta(skill_dir: Path) -> dict:
    """读取技能目录下的 .install-meta.json；缺失或损坏返回空 dict。"""
    meta_path = Path(skill_dir) / INSTALL_META_FILENAME
    if not meta_path.is_file():
        return {}
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def safe_dir_name(name: str, fallback: str) -> str:
    """将候选英文名规范为可用的目录名，非法时回退 fallback。"""
    candidate = (name or "").strip().lower()
    candidate = re.sub(r"[^a-z0-9-]+", "-", candidate).strip("-")
    candidate = re.sub(r"-{2,}", "-", candidate)
    if candidate and len(candidate) <= 64 and _SAFE_DIR_NAME_RE.match(candidate):
        return candidate
    fallback = (fallback or "").strip()
    return fallback or candidate


def install_dir_name(catalog: Optional[dict], skill_id: str) -> str:
    """根据 catalog 记录计算安装目录名。

    - 用户上传包（uskill-* / skill_type=user）：使用英文名（SKILL.md name），
      使 `ls /skills` 与技能清单一致，且 SKILL.md 中可稳定引用
      /mnt/skills/<英文名>。
    - 仓库同步技能（内置 / VM repo）：保持 skill_id 作为目录名。这类技能的
      目录名与 SKILL.md name 未必一致（如 config-audit-report-v7 →
      config-audit-report、employee-info-query → 中文名），改名会破坏既有
      安装、硬编码路径与前端选择，故维持原布局。
    """
    sid = (skill_id or "").strip()
    data = catalog or {}
    # 仅用户上传包（uskill-*）使用英文名目录；repo 迁移/官方包沿用 skill_id，
    # 保证升级为 market 包后安装目录不发生变化。
    if not sid.startswith("uskill-"):
        return sid

    name_en = (data.get("name_en") or "").strip()
    if not name_en:
        # 旧 catalog 可能只有 name
        name = (data.get("name") or "").strip()
        if name and name != sid:
            name_en = name
    return safe_dir_name(name_en, sid)


def find_installed_dir(root: Path, skill_id: str) -> Optional[Path]:
    """在 root 下查找 skill_id 对应的安装目录。

    优先按目录名直接命中（内置技能 / 历史布局），
    其次扫描各目录 .install-meta.json 的 package_id / skill_id。
    """
    sid = (skill_id or "").strip().strip("/\\")
    if not sid:
        return None
    root = Path(root)
    direct = root / sid
    if direct.is_dir():
        return direct
    if not root.is_dir():
        return None
    sid_lower = sid.lower()
    for child in root.iterdir():
        if not child.is_dir():
            continue
        meta = read_install_meta(child)
        meta_id = str(meta.get("package_id") or meta.get("skill_id") or "").strip()
        if meta_id and meta_id.lower() == sid_lower:
            return child
    return None


def resolve_dir_name(root: Path, skill_id: str) -> str:
    """返回 skill_id 在 root 下的实际目录名；未安装时回退 skill_id。"""
    found = find_installed_dir(root, skill_id)
    if found is not None:
        return found.name
    return (skill_id or "").strip().strip("/\\")


async def migrate_legacy_skill_dirs() -> dict:
    """启动迁移：把旧布局（目录名=skill_id）的市场包目录改名为英文名。

    幂等；目标目录已存在时跳过，避免覆盖。沙箱根 best-effort 同步改名。
    """
    from config import get_logger
    from db import deep_storage
    from db.database import db_manager
    from deep_agent_server.workspace import get_sandbox_skills_dir, get_skills_dir

    logger = get_logger(__name__)
    migrated = 0
    errors = 0
    try:
        pool = await db_manager.get_pool()
        rows = await pool.fetch("SELECT DISTINCT user_id FROM user_installed_skills")
    except Exception as e:
        logger.warning(f"[SkillDirMigrate] 读取用户列表失败: {e}")
        return {"migrated": 0, "errors": 0}

    for row in rows:
        user_id = row["user_id"]
        root = get_skills_dir(user_id)
        if not root.is_dir():
            continue
        for child in list(root.iterdir()):
            if not child.is_dir():
                continue
            meta = read_install_meta(child)
            skill_id = str(meta.get("package_id") or meta.get("skill_id") or "").strip()
            if not skill_id:
                continue
            try:
                catalog = await deep_storage.get_skill_catalog(skill_id)
                target_name = install_dir_name(catalog or meta, skill_id)
                if not target_name or target_name == child.name:
                    continue
                target = root / target_name
                if target.exists():
                    logger.warning(
                        f"[SkillDirMigrate] 目标目录已存在，跳过 name={skill_id} "
                        f"src={child} dst={target}"
                    )
                    continue
                child.rename(target)
                # 沙箱根同名目录同步改名（远程环境可能不可达，best effort）
                try:
                    sandbox_root = get_sandbox_skills_dir(user_id)
                    sandbox_old = sandbox_root / child.name
                    sandbox_new = sandbox_root / target_name
                    if sandbox_old.is_dir() and not sandbox_new.exists():
                        sandbox_old.rename(sandbox_new)
                except OSError:
                    pass
                try:
                    async with pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE user_installed_skills SET install_dir=$1 "
                            "WHERE user_id=$2 AND market_skill_id=$3",
                            str(target),
                            user_id,
                            skill_id,
                        )
                        await conn.execute(
                            "UPDATE skill_installations SET install_dir=$1 "
                            "WHERE user_id=$2 AND package_id=$3",
                            str(target),
                            user_id,
                            skill_id,
                        )
                except Exception as e:
                    logger.warning(f"[SkillDirMigrate] 更新 DB 失败 {skill_id}: {e}")
                migrated += 1
                logger.info(
                    f"[SkillDirMigrate] user={user_id} {child.name} -> {target_name}"
                )
            except Exception as e:
                errors += 1
                logger.warning(f"[SkillDirMigrate] 迁移失败 {child}: {e}")

    if migrated or errors:
        logger.info(
            f"[SkillDirMigrate] 完成: migrated={migrated} errors={errors}"
        )
    return {"migrated": migrated, "errors": errors}
