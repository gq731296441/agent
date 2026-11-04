"""Skill 市场路径：builds / releases。"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from config.config import app_config


def get_builds_root() -> Path:
    return Path(app_config.get_workspace_root()) / ".skill-builds"


def get_repo_root() -> Path:
    return Path(app_config.get_vm_skill_repo_root())


def submission_build_dir(submission_id: str) -> Path:
    return get_builds_root() / submission_id


def package_releases_dir(package_id: str) -> Path:
    return get_repo_root() / package_id / "releases"


def release_dir(package_id: str, version: str) -> Path:
    return package_releases_dir(package_id) / version


def publishing_dir(package_id: str, version: str) -> Path:
    return package_releases_dir(package_id) / f"{version}.publishing"


def new_package_id() -> str:
    """用户新建：uskill- + 32 hex。"""
    return f"uskill-{uuid4().hex}"


def new_submission_id() -> str:
    return str(uuid4())
