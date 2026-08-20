"""SKILL.md frontmatter 解析。

SKILL.md 格式（YAML frontmatter）：
  ---
  name: xxx
  description: xxx
  version: "1.0.0"
  author: platform
  tags: [tag1, tag2]
  triggers: [keyword1, keyword2]
  always_on: false
  ---
  （正文 instructions）

兼容新旧两种格式：
  - 旧：扁平 key: value，triggers/tags 用 [a, b] 行内数组
  - 新：YAML 多行标量 (| / >-)、嵌套 metadata.version
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


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


def _parse_yaml_frontmatter(block: str) -> Dict[str, Any]:
    """YAML 解析 frontmatter，兼容旧格式（行内 json 数组）。"""
    try:
        fm = yaml.safe_load(block)
        if isinstance(fm, dict):
            return fm
    except yaml.YAMLError:
        pass
    # YAML 解析失败时回退到手工解析
    return _parse_legacy_frontmatter(block)


def _parse_legacy_frontmatter(block: str) -> Dict[str, Any]:
    """手工解析旧的 key: value 格式（triggers/tags 为 [a, b] 行内数组）。"""
    result: Dict[str, Any] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower()
        val = val.strip().strip('"').strip("'")
        if key in ("tags", "triggers"):
            if val.startswith("["):
                inner = val.strip("[]")
                result[key] = [
                    t.strip().strip('"').strip("'")
                    for t in inner.split(",")
                    if t.strip()
                ]
            else:
                result[key] = [val] if val else []
        elif key == "always_on":
            result[key] = val.lower() in ("true", "1", "yes")
        else:
            result[key] = val
    return result


def _to_str_list(value: Any) -> List[str]:
    """安全转为字符串列表。"""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        return [value] if value else []
    return [str(value)]


def _resolve_version(fm: Dict[str, Any]) -> str:
    """解析 version，兼容顶层和 metadata.version。"""
    v = fm.get("version")
    if v is not None:
        return str(v)
    meta = fm.get("metadata")
    if isinstance(meta, dict) and meta.get("version") is not None:
        return str(meta["version"])
    return "1.0.0"


def parse_skill_md(skill_dir: str) -> Optional[SkillMeta]:
    """解析 skill 目录下的 SKILL.md。"""
    path = Path(skill_dir) / "SKILL.md"
    if not path.is_file():
        return None

    # utf-8-sig 自动剥离 UTF-8 BOM（﻿），否则 ^--- 无法匹配 frontmatter
    text = path.read_text(encoding="utf-8-sig")
    m = _FRONTMATTER_RE.match(text)

    if not m:
        # 没有 frontmatter：目录名作为 name
        return SkillMeta(skill_dir=str(skill_dir), name=Path(skill_dir).name)

    fm = _parse_yaml_frontmatter(m.group(1))

    return SkillMeta(
        skill_dir=str(skill_dir),
        name=str(fm.get("name") or Path(skill_dir).name),
        description=str(fm.get("description") or ""),
        version=_resolve_version(fm),
        author=str(fm.get("author") or ""),
        tags=_to_str_list(fm.get("tags")),
        triggers=_to_str_list(fm.get("triggers")),
        always_on=fm.get("always_on") in (True, "true", "1", "yes"),
    )
