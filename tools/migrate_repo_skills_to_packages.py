"""把 repo 同步技能一次性迁移为市场包（package_id 沿用 skill_id）。

用途
----
测试/生产环境上线「Skill 上传」后，将 VM `.skill-repo/` 中已有的 repo 技能
转为 market 包，之后由作者/管理员通过上传流程维护。

执行内容（幂等，可重复运行）
1. 按 AUTHOR_MAP 修正各 SKILL.md 的 author（姓名+工号）；修正 employee-info-query 的 name
2. 调用 import_trusted 生成 skill_packages / skill_versions / releases/<version>/
3. 清理扁平目录内容，只保留 releases/（避免启动同步读扁平 SKILL.md 导致版本回退）

用法（在项目根目录、已激活虚拟环境）
    python tools/migrate_repo_skills_to_packages.py --dry-run     # 预览
    python tools/migrate_repo_skills_to_packages.py               # 执行
    python tools/migrate_repo_skills_to_packages.py --only pdm-query2report-skill
"""

import argparse
import asyncio
import re
import shutil
import sys
from pathlib import Path

# skill_id -> (package owner 工号, SKILL.md author)
# owner="platform" 表示仅 admin_users 中的管理员可更新
AUTHOR_MAP = {
    "config-audit-report-v7": "刘奕琳00294192",
    "employee-info-query": "platform",
    "imap-indicator-query": "吴俊琴00114732",
    "imap-report-generator": "刘奕琳00294192",
    "mrm-pending-document-cleanup": "platform",
    "oa-zmail-manage": "platform",
    "pdm-query2report-skill": "洪伟韬10306212",
    "report-quality-check": "陈郁锋10081544",
    "studio-rdcloud-workitem": "platform",
    "studio-space-manager": "platform",
    "workload-data-monitoring": "庄静文00351023",
}

# 额外修正：employee-info-query 的中文 SKILL.md name 不符合上传校验（kebab-case）
NAME_FIX = {
    "employee-info-query": "employee-info-query",
}


def owner_of(author: str) -> str:
    """从 author 提取 owner 工号；platform 原样返回。"""
    if author == "platform":
        return author
    m = re.search(r"(\d{6,})$", author)
    return m.group(1) if m else author


def fix_skill_md(skill_dir: Path, sid: str, author: str) -> str:
    """幂等修正 SKILL.md 的 author/name；返回动作描述。"""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return "no SKILL.md"
    raw = skill_md.read_bytes()
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    m = re.match(r"^(---\s*\n)(.*?)(\n---)", text, re.DOTALL)
    if not m:
        return "no frontmatter"

    fm = m.group(2)
    changed = []

    new_fm, n = re.subn(
        r"^author:.*$", f"author: {author}", fm, count=1, flags=re.MULTILINE
    )
    if n:
        if new_fm != fm:
            changed.append("author")
        fm = new_fm

    target_name = NAME_FIX.get(sid)
    if target_name:
        new_fm, n2 = re.subn(
            r"^name:.*$", f"name: {target_name}", fm, count=1, flags=re.MULTILINE
        )
        if n2:
            if new_fm != fm:
                changed.append("name")
            fm = new_fm

    if not changed and not had_bom:
        return "unchanged"

    new_text = m.group(1) + fm + m.group(3) + text[m.end():]
    skill_md.write_text(new_text, encoding="utf-8")  # 统一去 BOM
    return f"updated({','.join(changed)}{',bom' if had_bom else ''})"


async def migrate_one(sid: str, author: str, *, dry_run: bool, keep_flat: bool) -> str:
    from config.config import app_config
    from db import deep_storage
    from deep_agent_server.skills.marketplace import storage as ms
    from deep_agent_server.skills.marketplace.publish_service import (
        PublishError,
        get_publish_service,
    )

    vm_root = Path(app_config.get_vm_skill_repo_root())
    src = vm_root / sid
    if not src.is_dir():
        return f"SKIP {sid}: 源目录不存在"

    catalog = await deep_storage.get_skill_catalog(sid)
    if not catalog:
        return f"SKIP {sid}: catalog 不存在"
    version = str(catalog.get("version") or "1.0.0")

    fix = "flat-cleaned" if not (src / "SKILL.md").is_file() else fix_skill_md(src, sid, author)

    existing = await ms.get_version_by_package_semver(sid, version)
    if existing and existing.get("status") == "PUBLISHED":
        return f"SAME {sid}: v{version} 已发布，跳过（{fix}）"

    if dry_run:
        return f"DRY  {sid}: v{version} owner={owner_of(author)} author={author}（{fix}）"

    if not (src / "SKILL.md").is_file():
        return f"FAIL {sid}: 缺少 SKILL.md（扁平目录已清理但版本未发布，请人工检查）"

    try:
        result = await get_publish_service().import_trusted(
            source=src,
            package_id=sid,
            version=version,
            operator=owner_of(author),
            policy="trusted_import",
            reason="migrate repo skill to market package",
            visibility="public",
            origin="official",
        )
    except PublishError as e:
        return f"FAIL {sid}: {e.message}"

    if not keep_flat:
        for child in src.iterdir():
            if child.name == "releases":
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                try:
                    child.unlink()
                except OSError:
                    pass

    return (
        f"OK   {sid}: v{version} owner={owner_of(author)} "
        f"skipped={result.get('skipped')}（{fix}）"
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-flat", action="store_true")
    args = parser.parse_args()

    if args.only:
        if args.only not in AUTHOR_MAP:
            print(f"未知 skill_id: {args.only}")
            sys.exit(1)
        targets = [(args.only, AUTHOR_MAP[args.only])]
    else:
        targets = list(AUTHOR_MAP.items())

    for sid, author in targets:
        print(await migrate_one(sid, author, dry_run=args.dry_run, keep_flat=args.keep_flat), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
