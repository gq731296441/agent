"""Skill VM 仓库管理：上传、导出、迁移、删除。

管理 {WORKSPACE_ROOT}/.skill-repo/ 下的 skill 目录。
项目 skill-repo/ 仅保留内置 skill（vanilla-web-builder, frontend-design）。
"""

import io
import logging
import os
import shutil
import zipfile
from pathlib import Path
from typing import List, Tuple
from uuid import uuid4

import yaml

from config.config import app_config
from db import deep_storage
from deep_agent_server.skills.parser import parse_skill_md
from deep_agent_server.skills.selector import BUILTIN_SKILL_IDS

logger = logging.getLogger(__name__)

SKILL_ID_PREFIX = "uskill-"


def generate_skill_id() -> str:
    """生成用户上传 skill 的唯一 ID：uskill-{uuid4前8位}。"""
    return f"{SKILL_ID_PREFIX}{uuid4().hex[:8]}"


def get_vm_repo_root() -> Path:
    """返回 VM skill 仓库根路径。"""
    return Path(app_config.get_vm_skill_repo_root())


def get_vm_skill_dir(skill_id: str) -> Path:
    """返回 VM 仓库中指定 skill 的目录路径。"""
    sid = (skill_id or "").strip().strip("/\\")
    if not sid or ".." in sid or "/" in sid or "\\" in sid:
        raise ValueError(f"非法 skill_id: {skill_id}")
    return get_vm_repo_root() / sid


def ensure_vm_repo_root() -> Path:
    """确保 VM skill 仓库根目录存在。"""
    root = get_vm_repo_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


# ── ZIP 校验 ───────────────────────────────────────────────────────


def _validate_zip_structure(zip_data: bytes) -> list[str]:
    """校验 ZIP 包结构：大小、文件数、路径安全、必须包含 SKILL.md。

    Returns:
        ZIP 内的文件路径列表。
    Raises:
        ValueError: 校验不通过时。
    """
    max_bytes = app_config.SKILL_UPLOAD_MAX_BYTES
    max_files = app_config.SKILL_UPLOAD_MAX_FILES

    if len(zip_data) > max_bytes:
        raise ValueError(
            f"ZIP 文件超过大小限制（{max_bytes // 1024 // 1024}MB）"
        )

    with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
        namelist = zf.namelist()
        if not namelist:
            raise ValueError("ZIP 包为空")
        if len(namelist) > max_files:
            raise ValueError(f"ZIP 文件数超过上限（{max_files}）")

        # 检查路径穿越
        for name in namelist:
            parts = name.replace("\\", "/").split("/")
            if any(p == ".." for p in parts):
                raise ValueError(f"ZIP 包含非法路径穿越: {name}")
            if name.startswith("/") or name.startswith(".."):
                raise ValueError(f"ZIP 包含非法路径: {name}")

        # 排除 macOS 垃圾文件
        clean_names = [
            n for n in namelist
            if not n.startswith("__MACOSX/")
            and not n.endswith(".DS_Store")
            and not n.startswith("._")
        ]

        # 必须包含 SKILL.md
        has_skill_md = any(n.rstrip("/") == "SKILL.md" or n.endswith("/SKILL.md") for n in clean_names)
        if not has_skill_md:
            raise ValueError("ZIP 包必须包含 SKILL.md 文件")

        return clean_names


def _resolve_zip_root_dir(namelist: List[str]) -> List[str]:
    """若 ZIP 内所有文件都在同一单层子目录下，自动剥离该层级。

    例如 my-skill/SKILL.md → 提取到 SKILL.md。
    """
    top_dirs = set()
    for name in namelist:
        parts = name.replace("\\", "/").split("/")
        if parts[0]:
            top_dirs.add(parts[0])

    if len(top_dirs) == 1:
        root_dir = top_dirs.pop() + "/"
        return [n[len(root_dir):] for n in namelist if n.startswith(root_dir) and n != root_dir]
    return namelist


# ── 解压与元数据 ───────────────────────────────────────────────────


def _extract_zip_to_dir(zip_data: bytes, target_dir: Path, file_list: List[str] | None = None) -> None:
    """解压 ZIP 到目标目录，过滤非法文件。"""
    target_dir.mkdir(parents=True, exist_ok=True)
    allowed = set(file_list) if file_list is not None else None

    with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
        for name in zf.namelist():
            if allowed is not None and name not in allowed:
                continue
            # 安全检查
            if name.startswith("__MACOSX/") or name.endswith(".DS_Store") or name.startswith("._"):
                continue
            zf.extract(name, target_dir)


def _ensure_skill_md(
    target_dir: Path,
    name: str,
    description: str,
    version: str,
    tags: List[str],
    author: str,
) -> None:
    """确保目标目录存在有效的 SKILL.md（更新 frontmatter，保留正文）。"""
    skill_md_path = target_dir / "SKILL.md"
    existing_body = ""

    if skill_md_path.is_file():
        try:
            text = skill_md_path.read_text(encoding="utf-8")
            # 提取 frontmatter 之后的正文
            parts = text.split("---", 2)
            if len(parts) >= 3:
                existing_body = parts[2].strip()
        except Exception:
            pass

    frontmatter = {
        "name": name,
        "description": description,
        "version": version,
        "author": author,
        "tags": tags,
    }
    fm_str = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True).strip()
    content = f"---\n{fm_str}\n---\n\n{existing_body}\n"
    skill_md_path.write_text(content, encoding="utf-8")


# ── 公开操作 ───────────────────────────────────────────────────────


async def upload_skill_zip(
    user_id: str,
    zip_data: bytes,
    name: str,
    description: str = "",
    version: str = "1.0.0",
    tags: List[str] | None = None,
    author: str = "",
    *,
    existing_skill_id: str | None = None,
) -> dict:
    """上传/更新 skill ZIP 包到 VM 仓库。

    1. 校验 ZIP 安全性
    2. 生成/复用 skill_id
    3. 解压到 .skill-repo/{skill_id}/
    4. 更新 SKILL.md frontmatter
    5. upsert skill_catalog（status=draft）
    """
    tag_list = list(tags or [])

    # 1. 校验
    file_list = _validate_zip_structure(zip_data)
    file_list = _resolve_zip_root_dir(file_list)

    # 2. skill_id
    if existing_skill_id:
        catalog = await deep_storage.get_skill_catalog(existing_skill_id)
        if not catalog:
            raise ValueError(f"Skill 不存在: {existing_skill_id}")
        if catalog.get("creator_user_id") and catalog["creator_user_id"] != user_id:
            raise ValueError("无权更新该 Skill")
        skill_id = existing_skill_id
    else:
        skill_id = generate_skill_id()

    # 3. 解压
    ensure_vm_repo_root()
    target_dir = get_vm_skill_dir(skill_id)
    if target_dir.exists():
        shutil.rmtree(target_dir)
    _extract_zip_to_dir(zip_data, target_dir, file_list)

    # 若 ZIP 内单层目录已被剥离，移动文件
    children = list(target_dir.iterdir())
    if len(children) == 1 and children[0].is_dir():
        inner_dir = children[0]
        # 将内层文件移到上层
        for item in inner_dir.iterdir():
            shutil.move(str(item), str(target_dir / item.name))
        shutil.rmtree(inner_dir)

    # 4. 更新 SKILL.md
    _ensure_skill_md(target_dir, name, description, version, tag_list, author)

    # 5. UPSERT catalog
    await deep_storage.upsert_skill_catalog_with_owner(
        skill_id=skill_id,
        name=name,
        description=description,
        version=version,
        tags=tag_list,
        author=author or user_id,
        repo_rel_path=skill_id,
        creator_user_id=user_id,
        skill_type="user",
        source_path=skill_id,
        file_size_bytes=len(zip_data),
        status="draft",
    )

    logger.info(
        f"[VMRepo] {'更新' if existing_skill_id else '上传'} skill "
        f"user={user_id} skill={skill_id} name={name} v={version}"
    )
    return {
        "skill_id": skill_id,
        "name": name,
        "version": version,
        "status": "draft",
    }


def export_skill_as_zip(skill_id: str) -> Tuple[bytes, str]:
    """将 VM 仓库中的 skill 导出为 ZIP 字节流。

    Returns:
        (zip_bytes, filename)
    """
    skill_dir = get_vm_skill_dir(skill_id)
    if not skill_dir.is_dir():
        raise FileNotFoundError(f"Skill 目录不存在: {skill_id}")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(skill_dir):
            for name in files:
                fp = Path(root) / name
                if name.startswith("._") or name == ".DS_Store":
                    continue
                arcname = fp.relative_to(skill_dir).as_posix()
                zf.write(fp, arcname)
    buf.seek(0)
    return buf.getvalue(), f"{skill_id}.zip"


async def delete_skill_from_repo(skill_id: str, user_id: str = "") -> bool:
    """删除 VM 仓库中的 skill 目录。可选校验所有权。"""
    if user_id:
        catalog = await deep_storage.get_skill_catalog(skill_id)
        if catalog and catalog.get("creator_user_id") and catalog["creator_user_id"] != user_id:
            raise ValueError("无权删除该 Skill")

    skill_dir = get_vm_skill_dir(skill_id)
    if not skill_dir.exists():
        return False

    shutil.rmtree(skill_dir, ignore_errors=True)
    logger.info(f"[VMRepo] 已删除 skill 目录: {skill_id}")
    return True


def scan_vm_skill_repo() -> List[dict]:
    """扫描 VM .skill-repo/ 返回 SkillCatalogEntry-like 列表。

    与 catalog_sync.SkillCatalogEntry 格式兼容。
    """
    root = get_vm_repo_root()
    if not root.is_dir():
        return []

    entries = []
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
        entries.append({
            "skill_id": skill_id,
            "name": meta.name or skill_id,
            "description": meta.description or "",
            "version": meta.version or "1.0.0",
            "tags": list(meta.tags or []),
            "author": meta.author or "",
            "repo_rel_path": skill_id,
        })
    return entries


async def sync_catalog_from_vm_repo() -> dict:
    """扫描 VM .skill-repo/ 并 upsert 到 skill_catalog 表。

    仅处理 VM repo 中的 skill，不处理项目 skill-repo/ 中的内置 skill。
    """
    entries = scan_vm_skill_repo()
    if not entries:
        logger.warning("[VMRepo] 扫描结果为空，跳过同步")
        return {"synced": 0, "skill_ids": []}

    present_ids = []
    for entry in entries:
        skill_id = entry["skill_id"]
        present_ids.append(skill_id)

        # 保留已有的 creator 信息（避免覆盖用户创建者）
        existing = await deep_storage.get_skill_catalog(skill_id)
        creator = str(existing.get("creator_user_id") or "") if existing else ""
        orig_source_path = str(existing.get("source_path") or skill_id) if existing else skill_id
        file_size = int(existing.get("file_size_bytes") or 0) if existing else 0
        prev_status: str | None = existing.get("status") if existing else None

        await deep_storage.upsert_skill_catalog_with_owner(
            skill_id=skill_id,
            name=entry["name"],
            description=entry["description"],
            version=entry["version"],
            tags=entry["tags"],
            author=entry["author"],
            repo_rel_path=entry["repo_rel_path"],
            creator_user_id=creator,
            skill_type="platform" if not creator else "user",
            source_path=orig_source_path,
            file_size_bytes=file_size,
            status="published" if not existing or prev_status != "draft" else "draft",
        )

    logger.info(f"[VMRepo] sync 完成: synced={len(entries)}")
    return {"synced": len(entries), "skill_ids": present_ids}


async def migrate_non_builtin_skills() -> dict:
    """首次部署：将项目 skill-repo/ 中非内置 skill 迁移到 VM .skill-repo/。

    幂等：已存在于 VM repo 的 skill 跳过。
    """
    from config.config import app_config as cfg

    project_repo = Path(cfg.get_skill_repo_root())
    if not project_repo.is_dir():
        logger.warning(f"[VMMigrate] 项目 skill-repo 不存在: {project_repo}")
        return {"migrated": 0, "skipped": 0, "errors": 0}

    vm_repo = ensure_vm_repo_root()
    migrated = 0
    skipped = 0
    errors = 0

    for child in sorted(project_repo.iterdir()):
        if not child.is_dir():
            continue
        skill_id = child.name
        if skill_id.startswith(".") or ".." in skill_id:
            continue
        if skill_id in BUILTIN_SKILL_IDS:
            continue
        if not (child / "SKILL.md").is_file():
            continue

        vm_target = vm_repo / skill_id
        if vm_target.is_dir() and (vm_target / "SKILL.md").is_file():
            skipped += 1
            continue

        try:
            if vm_target.exists():
                shutil.rmtree(vm_target)
            shutil.copytree(child, vm_target)
            migrated += 1
            logger.info(f"[VMMigrate] 已迁移: {skill_id}")
        except Exception as e:
            errors += 1
            logger.error(f"[VMMigrate] 迁移失败 {skill_id}: {e}")

    logger.info(f"[VMMigrate] 完成: migrated={migrated} skipped={skipped} errors={errors}")
    return {"migrated": migrated, "skipped": skipped, "errors": errors}
