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
