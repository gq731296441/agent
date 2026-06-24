"""Skill 在容器内的 execute 挂载路径（独立于 /workspace bind mount）。"""

CONTAINER_SKILL_MOUNT_PREFIX = "/mnt/skills"


def container_skill_execute_path(skill_id: str) -> str:
    sid = str(skill_id).strip().strip("/\\")
    return f"{CONTAINER_SKILL_MOUNT_PREFIX}/{sid}/"
