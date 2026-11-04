"""上传声明元数据：中英文名、简介、标签、创建人；与 SKILL.md name 对齐。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

ALLOWED_TAGS = ("办公协作", "数据分析", "数据智能", "研发提效", "资产管理")

# Anthropic/Claude Skill name：小写字母开头，仅字母数字和连字符，最长 64。
# 须与 SKILL.md `name`、解包目录名一致（s3canner SPEC-008）。
NAME_EN_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
NAME_ZH_RE = re.compile(
    r"^[\u4e00-\u9fffA-Za-z0-9]([\u4e00-\u9fffA-Za-z0-9·\s\-]{0,38}[\u4e00-\u9fffA-Za-z0-9])?$"
)

_SPECIAL_CUT = re.compile(r"[^\u4e00-\u9fffA-Za-z0-9·\s\-]")


def parse_tags(raw: Any) -> List[str]:
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, str):
        items = re.split(r"[,，]+", raw)
    else:
        items = []
    out: List[str] = []
    seen = set()
    for item in items:
        tag = str(item or "").strip()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    return out


def is_valid_name_en(value: str) -> bool:
    text = (value or "").strip()
    return bool(text) and len(text) <= 64 and bool(NAME_EN_RE.fullmatch(text))


def is_valid_name_zh(value: str) -> bool:
    text = (value or "").strip()
    if len(text) < 2 or len(text) > 40:
        return False
    if _SPECIAL_CUT.search(text):
        return False
    return bool(NAME_ZH_RE.fullmatch(text))


def normalize_declared(
    *,
    name_zh: str = "",
    name_en: str = "",
    description: str = "",
    tags: Any = None,
    author: str = "",
    submitter_id: str = "",
) -> Dict[str, Any]:
    return {
        "name_zh": (name_zh or "").strip(),
        "name_en": (name_en or "").strip().lower(),
        "description": re.sub(r"\s+", " ", (description or "").strip()),
        "tags": parse_tags(tags),
        "author": (author or "").strip() or (submitter_id or "").strip(),
    }


def validate_declared(declared: Dict[str, Any]) -> Optional[str]:
    if not is_valid_name_zh(declared.get("name_zh") or ""):
        return "中文名称必填，2–40 字，仅中文/字母/数字及空格、间隔号、连字符"
    if not is_valid_name_en(declared.get("name_en") or ""):
        return "英文名称必填，须为 kebab-case（小写字母开头，仅字母、数字、连字符），最长 64 位，且与 SKILL.md 的 name 一致"
    desc = declared.get("description") or ""
    if not desc:
        return "请填写 Skill 简介"
    if len(desc) > 200:
        return "Skill 简介不能超过 200 字"
    tags = declared.get("tags") or []
    if not tags:
        return "请至少选择一个标签"
    bad = [t for t in tags if t not in ALLOWED_TAGS]
    if bad:
        return "标签只能从「办公协作、数据分析、数据智能、研发提效、资产管理」中选择"
    author = declared.get("author") or ""
    if not author:
        return "缺少创建人"
    if len(author) > 64:
        return "创建人过长"
    return None
