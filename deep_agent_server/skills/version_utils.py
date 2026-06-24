"""Skill 版本比较工具。"""

from __future__ import annotations

from packaging.version import InvalidVersion, Version


def version_is_newer(latest: str, installed: str) -> bool:
    """catalog 版本是否高于用户已装版本。"""
    latest = (latest or "0").strip()
    installed = (installed or "0").strip()
    if latest == installed:
        return False
    try:
        return Version(latest) > Version(installed)
    except InvalidVersion:
        return latest != installed


def versions_equal(a: str, b: str) -> bool:
    a = (a or "0").strip()
    b = (b or "0").strip()
    if a == b:
        return True
    try:
        return Version(a) == Version(b)
    except InvalidVersion:
        return False
