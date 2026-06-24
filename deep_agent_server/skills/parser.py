"""SKILL.md frontmatter 解析。

SKILL.md 格式：
  ---
  name: xxx
  description: xxx
  always_on: true
  triggers: [keyword1, keyword2]
  ---
  （正文 instructions）
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class SkillMeta:
    """单个 skill 的元数据（来自 SKILL.md frontmatter）。"""
    skill_dir: str
    name: str = ""
    description: str = ""
    version: str = "1.0.0"
    tags: List[str] = field(default_factory=list)
    author: str = ""
    triggers: List[str] = field(default_factory=list)
    always_on: bool = False

    @property
    def skill_id(self) -> str:
        return Path(self.skill_dir).name


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


def parse_skill_md(skill_dir: str) -> Optional[SkillMeta]:
    """解析 skill 目录下的 SKILL.md。"""
    path = Path(skill_dir) / "SKILL.md"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    meta = SkillMeta(skill_dir=str(skill_dir))
    m = _FRONTMATTER_RE.match(text)
    if not m:
        meta.name = path.parent.name
        return meta
    block = m.group(1)
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower()
        val = val.strip().strip('"').strip("'")
        if key == "name":
            meta.name = val
        elif key == "description":
            meta.description = val
        elif key == "version":
            meta.version = val or "1.0.0"
        elif key == "author":
            meta.author = val
        elif key == "tags":
            if val.startswith("["):
                inner = val.strip("[]")
                meta.tags = [
                    t.strip().strip('"').strip("'")
                    for t in inner.split(",")
                    if t.strip()
                ]
            else:
                meta.tags = [val] if val else []
        elif key == "always_on":
            meta.always_on = val.lower() in ("true", "1", "yes")
        elif key == "triggers":
            if val.startswith("["):
                inner = val.strip("[]")
                meta.triggers = [
                    t.strip().strip('"').strip("'")
                    for t in inner.split(",")
                    if t.strip()
                ]
            else:
                meta.triggers = [val] if val else []
    if not meta.name:
        meta.name = Path(skill_dir).name
    return meta
