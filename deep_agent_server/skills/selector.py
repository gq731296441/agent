"""Skills 目录管理：内置技能安装、候选目录解析、过滤。

渐进式披露改由官方 SkillsMiddleware 处理（通过 create_deep_agent(skills=[...])）。
此模块保留目录管理、内置技能安装和过滤工具函数。
"""

from pathlib import Path
from typing import List, Optional

import logging
import shutil

from config.config import app_config
from deep_agent_server.skills.parser import SkillMeta, parse_skill_md
from deep_agent_server.workspace import get_skills_dir

logger = logging.getLogger(__name__)

# 内置默认技能：对所有用户默认可用，不入 catalog，不参与订阅/退订
BUILTIN_SKILL_IDS = {"vanilla-web-builder", "frontend-design", "pdf", "xlsx", "pptx", "docx"}

# 内置技能源目录路径（skill-repo 根目录）
BUILTIN_SKILL_REPO_ROOT = app_config.get_skill_repo_root()

def get_builtin_skill_dirs() -> List[str]:
    """返回内置技能的 skill-repo 源路径，仅包含实际存在的目录。"""
    root = Path(BUILTIN_SKILL_REPO_ROOT) if not isinstance(BUILTIN_SKILL_REPO_ROOT, Path) else BUILTIN_SKILL_REPO_ROOT
    dirs = []
    for sid in BUILTIN_SKILL_IDS:
        candidate = root / sid
        if candidate.is_dir() and (candidate / "SKILL.md").is_file():
            dirs.append(str(candidate))
    return dirs


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


def _install_builtin_skill(src: Path, dst: Path) -> Optional[str]:
    """不安全删除目标目录后从源复制，返回目标路径或 None。"""
    # 无条件删除旧的（symlink / broken symlink / 真实目录），避免 NFS symlink 残留
    try:
        if dst.is_symlink():
            dst.unlink(missing_ok=True)
        elif dst.is_dir():
            shutil.rmtree(str(dst), ignore_errors=True)
        elif dst.exists():
            dst.unlink(missing_ok=True)
    except OSError:
        pass

    try:
        shutil.copytree(str(src), str(dst))
    except OSError:
        return None
    return str(dst)


def _ensure_builtin_skill_in_user_dir(user_id: str, skill_id: str) -> Optional[str]:
    """将内置技能以副本方式安装到用户 skills 目录，使 /skills/ 路由可达。

    不能用 symlink：FilesystemBackend(virtual_mode=True) 的 _resolve_path 会验证
    最终路径在 root_dir 内，symlink 解析后的 skill-repo 路径在 root_dir 外会被拒绝。

    Returns:
        用户 skills 目录下的 skill 路径，或 None（skill-repo 源不存在时）。
    """
    repo_root = Path(BUILTIN_SKILL_REPO_ROOT) if not isinstance(BUILTIN_SKILL_REPO_ROOT, Path) else BUILTIN_SKILL_REPO_ROOT
    src = repo_root / skill_id
    if not src.is_dir() or not (src / "SKILL.md").is_file():
        return None

    dst = get_skills_dir(user_id) / skill_id
    dst.parent.mkdir(parents=True, exist_ok=True)

    # 如果 SKILL.md 可读且未过期则跳过
    try:
        if dst.is_dir() and (dst / "SKILL.md").is_file():
            if (dst / "SKILL.md").stat().st_mtime >= (src / "SKILL.md").stat().st_mtime:
                return str(dst)
    except OSError:
        pass

    # 目标不可用（broken symlink / NFS 残留 / 过期）→ 删除重建
    return _install_builtin_skill(src, dst)


def _ensure_builtin_skill_in_user_dir_for_agent(agent_id: str, skill_id: str) -> Optional[str]:
    """订阅智能体版本：将内置技能以副本方式安装到 agent skills 目录下。"""
    from deep_agent_server.workspace import get_agent_skills_dir

    repo_root = Path(BUILTIN_SKILL_REPO_ROOT) if not isinstance(BUILTIN_SKILL_REPO_ROOT, Path) else BUILTIN_SKILL_REPO_ROOT
    src = repo_root / skill_id
    if not src.is_dir() or not (src / "SKILL.md").is_file():
        return None

    dst = get_agent_skills_dir(agent_id) / skill_id
    dst.parent.mkdir(parents=True, exist_ok=True)

    try:
        if dst.is_dir() and (dst / "SKILL.md").is_file():
            if (dst / "SKILL.md").stat().st_mtime >= (src / "SKILL.md").stat().st_mtime:
                return str(dst)
    except OSError:
        pass

    return _install_builtin_skill(src, dst)


def ensure_builtin_skills_for_user(user_id: str) -> dict:
    """为单个用户安装/更新内置技能。返回 {skill_id: path}。"""
    result = {}
    for bid in BUILTIN_SKILL_IDS:
        d = _ensure_builtin_skill_in_user_dir(user_id, bid)
        if d:
            result[bid] = d
    return result


def ensure_builtin_skills_for_all_users() -> dict:
    """启动时批量安装：扫描所有用户目录，安装/更新内置技能。"""
    from pathlib import Path as _Path

    root = _Path(app_config.WORKSPACE_ROOT)
    if not root.is_dir():
        logger.warning(f"[BuiltinSkill] WORKSPACE_ROOT 不存在: {root}")
        return {"users": 0, "installed": 0}

    users = 0
    total_installed = 0
    for user_dir in sorted(root.iterdir()):
        if not user_dir.is_dir() or user_dir.name.startswith("."):
            continue
        uid = user_dir.name
        try:
            installed = ensure_builtin_skills_for_user(uid)
            if installed:
                users += 1
                total_installed += len(installed)
        except Exception as e:
            logger.warning(f"[BuiltinSkill] 用户 {uid} 安装失败: {e}")

    logger.info(
        f"[BuiltinSkill] 批量安装完成 users={users} installed={total_installed}"
    )
    return {"users": users, "installed": total_installed}


def resolve_skill_dirs(
    user_id: str,
    agent_id: Optional[str],
    bound_dirs: Optional[List[str]] = None,
) -> List[str]:
    """解析候选 skill 目录：内置技能（symlink 到用户目录） + 用户已安装技能。"""
    if bound_dirs is not None:
        return bound_dirs

    dirs: List[str] = []
    root = get_skills_dir(user_id)
    root.mkdir(parents=True, exist_ok=True)

    # 内置技能：确保在用户 skills 目录下存在
    for bid in BUILTIN_SKILL_IDS:
        d = _ensure_builtin_skill_in_user_dir(user_id, bid)
        if d:
            dirs.append(d)

    # 用户已安装技能
    if root.is_dir():
        for p in sorted(root.iterdir()):
            if p.is_dir() and (p / "SKILL.md").is_file():
                if str(p) not in dirs:
                    dirs.append(str(p))
    return dirs


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






