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
    """渐进式披露：仅 name/description 进 system prompt，正文按需 read_file。

    当 metas 为空时，告知 agent 当前无可用 skill 以及错误含义，
    避免 agent 对未启用的 skill 反复尝试访问。
    """
    # ============================================================
    # EN backup (English version preserved below as comments)
    # ============================================================
    if not metas:
        lines = [
            "",
            "## Skills 技能系统",
            "",
            "当前会话没有启用任何技能。",
            "",
            "**重要提示 — 已关闭技能的错误含义：**",
            "- 用户可以开启和关闭技能，技能关闭后无法访问"
            "- 访问 `/skills/<名称>/...` 出现 `Error: permission denied` → 该技能「<名称>」**未被启用**。",
            "  立即停止尝试 — 任何替代路径都不会生效。",
            "- 在 `execute` 中访问 `/mnt/skills/<名称>/...` 出现 `No such file or directory` →",
            "  该技能「<名称>」**未被挂载**（未被启用）。",
            "- `ls /skills/` 出现 `permission denied` → 当前会话没有启用任何技能。",
            "",
            "**你应该怎么做：**",
            "- 如果用户问到某个技能，告知用户该技能当前未启用，需要在技能选择器中手动开启。",
            "- **不要**尝试 `/workspace/.sandbox/skills/` 或用文件工具访问 `/mnt/skills/`。",
            "- **不要**试图用 `execute` 绕过文件系统权限限制。",
            "- 直接告诉用户：该技能已被关闭，请在**能力市场**中启用后重试。",
        ]
        # EN: "No skills are currently enabled for this session."
        # EN: "Error: permission denied on /skills/<name>/... → the skill <name> is not enabled."
        # EN: "No such file or directory for /mnt/skills/<name>/... inside execute → not mounted."
        # EN: "permission denied on ls /skills/ → no skills are enabled."
        # EN: "Do NOT try workarounds — tell the user to enable the skill and retry."
        return "\n".join(lines) + "\n"

    lines = [
        "",
        "## Skills 技能系统",
        "",
        "你拥有以下可用技能，仅在需要时读取完整说明。",
        "",
        "**可用技能：**",
        "",
    ]
    # EN: "You have access to the following skills. Read full instructions only when needed."
    # EN: "**Available Skills:**"
    for m in metas:
        sid = Path(m.skill_dir).name
        desc = m.description or "（无描述）"
        lines.append(f"- **{m.name or sid}**: {desc}")
        lines.append(f"  -> 使用 `read_file` 读取 `/skills/{sid}/SKILL.md` 获取完整说明（建议 limit=1000）")
        # EN: "-> Read `/skills/{sid}/SKILL.md` for full instructions (use limit=1000)"
        if sandbox_mode:
            lines.append(
                f"  -> 通过 `execute` 在容器中运行脚本：`{container_skill_execute_path(sid)}`"
            )
            # EN: "-> Run scripts via execute at `{container_skill_execute_path(sid)}` (container only)"
    lines.extend(
        [
            "",
            "**如何使用技能（渐进式披露）：**",
            "1. 将用户任务与上方技能描述进行匹配",
            "2. 使用 `read_file` / `ls` / `glob` 读取 `/skills/{{id}}/...` 下的技能文件",
            "3. 按照 SKILL.md 中的工作流程执行；所有文件工具读取均使用 `/skills/` 路径",
        ]
    )
    # EN: "**How to Use Skills (Progressive Disclosure):**"
    # EN: "1. Match the user task to a skill description above"
    # EN: "2. read_file / ls / glob on /skills/{{id}}/... to read skill files"
    # EN: "3. Follow SKILL.md workflow; keep using /skills/ for all file-tool reads"
    if sandbox_mode:
        lines.extend(
            [
                "",
                "**沙箱技能路径（重要）：**",
                f"- **文件工具**（`read_file`、`ls`、`glob`、`write_file`）：仅使用 `/skills/{{{{id}}}}/...`。",
                "  这些操作在宿主机上执行，不在沙箱容器内运行。",
                f"- **execute**（容器内 shell/python）：仅使用 `{CONTAINER_SKILL_MOUNT_PREFIX}/{{{{id}}}}/...`。",
                f"  例如：`python /mnt/skills/my_skill/helper.py` 或 `bash /mnt/skills/my_skill/run.sh`。",
                "- **绝对不要**在 `execute` 命令中使用 `/skills/...` — 容器内看不到该路径。",
                f"- **绝对不要**用文件工具访问 `{CONTAINER_SKILL_MOUNT_PREFIX}/...` — 请使用 `/skills/`。",
                "- `/workspace/` 是会话工作目录（存放输出和用户文件），不是技能安装目录。",
                "- 不要使用 `/workspace/.sandbox/skills/` — 技能内容不存储在那里。",
            ]
        )
        # EN: "**Sandbox skill paths (important):**"
        # EN: "File tools: use /skills/{{id}}/... only. These read from the agent host."
        # EN: "execute: use /mnt/skills/{{id}}/... only. Example: python /mnt/skills/.../helper.py"
        # EN: "Never use /skills/... inside an execute command."
        # EN: "Never use /mnt/skills/... with file tools."
        # EN: "/workspace/ is the session working directory; not for installed skills."
        # EN: "Do NOT use /workspace/.sandbox/skills/"

    # 已关闭 skill 的识别指引
    lines.extend(
        [
            "",
            "**已关闭技能的识别：**",
            "- 未在上方列出的技能均处于**已关闭**状态（用户未启用）。",
            "- 访问 `/skills/<名称>/...` 出现 `Error: permission denied` → 该技能已关闭，立即停止尝试。",
            "- 在 execute 中访问 `/mnt/skills/<名称>/` 出现 `No such file or directory` → 未挂载（已关闭）。",
            "- **不要**尝试任何绕过方式 — 告知用户去**能力市场**启用该技能即可。",
        ]
    )
    # EN: "**Disabled Skill Recognition:**"
    # EN: "Skills NOT listed above are disabled (not enabled by the user)."
    # EN: "Error: permission denied on /skills/<name>/... -> <name> is disabled. Stop trying."
    # EN: "No such file or directory for /mnt/skills/<name>/ in execute -> not mounted (disabled)."
    # EN: "Do NOT try workarounds — ask the user to enable the skill instead."

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
