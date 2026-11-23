"""严格 SemVer MAJOR.MINOR.PATCH 校验与整数三元组比较。"""

from __future__ import annotations

import re
from typing import Optional, Tuple

SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def parse_semver(version: str) -> Optional[Tuple[int, int, int]]:
    v = (version or "").strip()
    m = SEMVER_RE.match(v)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def is_valid_semver(version: str) -> bool:
    return parse_semver(version) is not None


def compare_semver(a: str, b: str) -> int:
    """返回 -1 / 0 / 1。非法版本视为最低。"""
    pa, pb = parse_semver(a), parse_semver(b)
    if pa is None and pb is None:
        return 0
    if pa is None:
        return -1
    if pb is None:
        return 1
    if pa < pb:
        return -1
    if pa > pb:
        return 1
    return 0


def is_strictly_greater(candidate: str, baseline: str) -> bool:
    return compare_semver(candidate, baseline) > 0


def suggest_next_minor(version: str) -> Optional[str]:
    """给出一个「严格大于 baseline」的推荐版本号（MINOR + 1，PATCH 归零）。

    仅用于报错提示，帮用户知道该把 SKILL.md 的 version 改成什么。
    baseline 非法时返回 None。
    """
    parsed = parse_semver(version)
    if parsed is None:
        return None
    major, minor, _patch = parsed
    return f"{major}.{minor + 1}.0"
