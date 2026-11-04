"""平台 SKILL.md / 引用文件结构校验。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from deep_agent_server.skills.parser import parse_skill_md

_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_CODE_FENCE_REF = re.compile(r"(?:scripts|references|assets)/[A-Za-z0-9_./\-]+")


class StructureError(Exception):
    def __init__(self, message: str, details: Optional[List[str]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or []


def validate_skill_structure(unpacked: Path) -> dict:
    skill_md = unpacked / "SKILL.md"
    if not skill_md.is_file():
        raise StructureError("缺少 SKILL.md")

    meta = parse_skill_md(str(unpacked))
    if not meta or not (meta.name or "").strip():
        raise StructureError("SKILL.md frontmatter 缺少 name")

    errors: List[str] = []
    text = skill_md.read_text(encoding="utf-8", errors="replace")

    # markdown 链接到相对文件
    for _label, href in _LINK_RE.findall(text):
        href = href.strip()
        if not href or href.startswith(("http://", "https://", "mailto:", "#")):
            continue
        href = href.split("#", 1)[0].split("?", 1)[0]
        if not href:
            continue
        target = (unpacked / href).resolve()
        try:
            target.relative_to(unpacked.resolve())
        except ValueError:
            errors.append(f"引用越界: {href}")
            continue
        if not target.exists():
            errors.append(f"引用文件不存在: {href}")

    # 常见 scripts/references 路径提示
    for m in _CODE_FENCE_REF.findall(text):
        p = unpacked / m
        if not p.exists():
            # 仅警告级：正文可能举例；必需引用已由链接检查覆盖
            pass

    if errors:
        raise StructureError("结构校验失败", errors)

    return {
        "name": meta.name,
        "description": meta.description or "",
        "tags": list(meta.tags or []),
        "author": meta.author or "",
        "skill_md_version": meta.version or "",
    }
