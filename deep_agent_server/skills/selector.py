"""Skills KeywordTrigger 按需匹配。

Skill 加载分两阶段：
  1. exposed_metas — 所有候选 skill 的 name/description 写入 system prompt
  2. trigger_metas — 用户消息关键词匹配后实际激活的 skill（推送 SKILL_LOADED）
"""

import hashlib
import json
from pathlib import Path
from typing import List, Optional

from deep_agent_server.skills.mount_paths import (
    CONTAINER_SKILL_MOUNT_PREFIX,
    container_skill_execute_path,
)
from deep_agent_server.skills.parser import SkillMeta, parse_skill_md
from deep_agent_server.workspace import get_skills_dir


class SkillSelector:
    """
    根据用户消息匹配应加载的 skill 目录。

    always_on skill 始终加载；其余 skill 需 triggers 关键词命中 user_message。
    """

    @staticmethod
    def load_metas(skill_dirs: List[str]) -> List[SkillMeta]:
        """从多个 skill 目录解析 SKILL.md frontmatter。"""
        metas = []
        for d in skill_dirs:
            meta = parse_skill_md(d)
            if meta:
                metas.append(meta)
        return metas

    @classmethod
    def match(
        cls,
        user_message: Optional[str],
        skill_dirs: List[str],
        *,
        always_on_only: bool = False,
    ) -> List[str]:
        """
        返回应传给 create_deep_agent(skills=...) 的目录列表。

        - always_on_only=True：仅返回 always_on skills（warmup 阶段）
        - 无 user_message 且非 always_on_only：仅 always_on
        """
        metas = cls.load_metas(skill_dirs)
        if always_on_only:
            return [m.skill_dir for m in metas if m.always_on]

        msg = (user_message or "").lower()
        matched = []
        for m in metas:
            if m.always_on:
                matched.append(m.skill_dir)
                continue
            sid = m.skill_id.lower()
            name = (m.name or "").lower()
            # 用户消息提到 skill 目录名或 frontmatter name 时也视为激活
            if msg and sid and sid in msg:
                matched.append(m.skill_dir)
                continue
            if msg and name and len(name) >= 2 and name in msg:
                matched.append(m.skill_dir)
                continue
            if not m.triggers:
                continue
            if msg and any(t.lower() in msg for t in m.triggers if t):
                matched.append(m.skill_dir)
        return matched

    @staticmethod
    def match_hash(matched_dirs: List[str]) -> str:
        """生成 skill 选择指纹，用于 agent 实例缓存 key。"""
        payload = sorted(matched_dirs)
        return hashlib.md5(json.dumps(payload).encode()).hexdigest()[:12]


def filter_skill_dirs_by_ids(
    candidate_dirs: List[str],
    skill_ids: Optional[List[str]],
) -> List[str]:
    """
    按 capabilities.skills 过滤候选目录。

    - skill_ids is None：不过滤（兼容旧客户端）
    - skill_ids == []：空池，不加载任何 skill
    - 否则：仅保留 market_skill_id / 目录名匹配的 skill
    """
    if skill_ids is None:
        return candidate_dirs
    allowed = {str(s).strip().lower() for s in skill_ids if str(s).strip()}
    if not allowed:
        return []
    filtered: List[str] = []
    for d in candidate_dirs:
        name = Path(d).name.lower()
        if name in allowed:
            filtered.append(d)
    return filtered


def resolve_skill_dirs(
    user_id: str,
    agent_id: Optional[str],
    bound_dirs: Optional[List[str]] = None,
) -> List[str]:
    """解析候选 skill 目录：优先 agent 绑定，否则扫描用户 skills/。"""
    if bound_dirs is not None:
        return bound_dirs
    root = get_skills_dir(user_id)
    if not root.is_dir():
        return []
    return [
        str(p)
        for p in sorted(root.iterdir())
        if p.is_dir() and (p / "SKILL.md").is_file()
    ]


def resolve_selected_pool_dirs(
    candidate_dirs: List[str],
    selected_skill_ids: Optional[List[str]] = None,
) -> List[str]:
    """capabilities.skills 过滤后的候选物理目录。"""
    if selected_skill_ids is None:
        return list(candidate_dirs)
    return filter_skill_dirs_by_ids(candidate_dirs, selected_skill_ids)


def effective_selected_skill_ids(
    candidate_dirs: List[str],
    selected_skill_ids: Optional[List[str]],
) -> Optional[List[str]]:
    """将前端 capabilities.skills 过滤为已安装 skill id；未安装 id 丢弃。"""
    if selected_skill_ids is None:
        return None
    return sorted(
        Path(d).name for d in filter_skill_dirs_by_ids(candidate_dirs, selected_skill_ids)
    )


def build_skills_system_prompt(metas: List[SkillMeta], *, sandbox_mode: bool = False) -> str:
    """渐进式披露：仅 name/description 进 system prompt，正文按需 read_file。"""
    if not metas:
        return ""
    lines = [
        "",
        "## Skills System",
        "",
        "You have access to the following skills. Read full instructions only when needed.",
        "",
        "**Available Skills:**",
        "",
    ]
    for m in metas:
        sid = Path(m.skill_dir).name
        desc = m.description or "(no description)"
        lines.append(f"- **{m.name or sid}**: {desc}")
        lines.append(f"  -> Read `/skills/{sid}/SKILL.md` for full instructions (use limit=1000)")
        if sandbox_mode:
            lines.append(
                f"  -> Run scripts via execute at `{container_skill_execute_path(sid)}` (container only)"
            )
    lines.extend(
        [
            "",
            "**How to Use Skills (Progressive Disclosure):**",
            "1. Match the user task to a skill description above",
            "2. `read_file` / `ls` / `glob` on `/skills/{{id}}/...` to read skill files",
            "3. Follow SKILL.md workflow; keep using `/skills/` for all file-tool reads",
        ]
    )
    if sandbox_mode:
        lines.extend(
            [
                "",
                "**Sandbox skill paths (important):**",
                "- **File tools** (`read_file`, `ls`, `glob`, `write_file`): use `/skills/{{id}}/...` only.",
                "  These read from the agent host; they do NOT run inside the sandbox container.",
                f"- **execute** (shell/python in container): use `{CONTAINER_SKILL_MOUNT_PREFIX}/{{id}}/...` only.",
                "  Example: `python /mnt/skills/my_skill/helper.py` or `bash /mnt/skills/my_skill/run.sh`.",
                "- **Never** use `/skills/...` inside an `execute` command — the container cannot see that path.",
                f"- **Never** use `{CONTAINER_SKILL_MOUNT_PREFIX}/...` with file tools — use `/skills/` instead.",
                "- `/workspace/` is the session working directory (outputs, user files); not for installed skills.",
                "- Do NOT use `/workspace/.sandbox/skills/` — skill content is not stored there.",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def dirs_to_skill_sources(skill_dirs: List[str]) -> List[str]:
    """deepagents SkillsMiddleware 源路径：扫描 /skills/ 下所有子目录。"""
    return ["/skills/"] if skill_dirs else []


def resolve_exposed_skill_sources(
    candidate_dirs: List[str],
    selected_skill_ids: Optional[List[str]] = None,
) -> List[str]:
    """兼容旧调用：返回 SkillsMiddleware 源路径（单根 /skills/）。"""
    pool = resolve_selected_pool_dirs(candidate_dirs, selected_skill_ids)
    return dirs_to_skill_sources(pool)


def match_skills(
    user_id: str,
    agent_id: Optional[str],
    user_message: Optional[str],
    bound_dirs: Optional[List[str]] = None,
    *,
    always_on_only: bool = False,
    selected_skill_ids: Optional[List[str]] = None,
) -> tuple[List[str], List[SkillMeta]]:
    """便捷函数：解析目录 + 匹配，返回 (matched_dirs, all_metas)。"""
    dirs = resolve_skill_dirs(user_id, agent_id, bound_dirs)
    dirs = filter_skill_dirs_by_ids(dirs, selected_skill_ids)
    metas = SkillSelector.load_metas(dirs)
    matched = SkillSelector.match(
        user_message, dirs, always_on_only=always_on_only
    )
    loaded_metas = [m for m in metas if m.skill_dir in matched]
    return matched, loaded_metas
