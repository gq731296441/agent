"""Deep Agent 工作区路径与目录管理。

目录结构（相对于 WORKSPACE_ROOT）：
  {user_id}/skills/          — 用户 skill 安装目录
  {user_id}/workspace/{session_id}/ — 会话工作区
  {user_id}/uploads/         — 用户上传
  {user_id}/artifacts/       — 超长工具结果等 artifact 存储
"""

import hashlib
import os
import re
import shutil
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from config.config import app_config
from config import get_logger

logger = get_logger(__name__)


def _resolve_skill_dir_name(root: Path, skill_id: str) -> str:
    """延迟导入：workspace 被 skills 包反向依赖，顶层导入会循环。"""
    from deep_agent_server.skills.dirs import resolve_dir_name

    return resolve_dir_name(root, skill_id)

_USER_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_@.+~-]+$")
_AGENT_ID_PATTERN = re.compile(r"^[a-fA-F0-9-]+$")

# HTML 分享快照目录名（分享功能保留字，用户工作区内的该目录不占配额、不在文件列表展示）
HTML_SHARE_SNAPSHOT_DIR = ".html-share-assets"


def validate_user_id(user_id: str) -> str:
    """校验 user_id 格式，防止路径穿越（user_id 作为目录名）。"""
    uid = (user_id or "").strip()
    if not uid or not _USER_ID_PATTERN.match(uid):
        raise ValueError(f"非法 user_id: {user_id}")
    return uid


def validate_agent_id(agent_id: str) -> str:
    """校验 agent_id 格式（UUID），防止路径穿越。"""
    aid = (agent_id or "").strip()
    if not aid or not _AGENT_ID_PATTERN.match(aid):
        raise ValueError(f"非法 agent_id: {agent_id}")
    return aid


def get_user_root(user_id: str) -> Path:
    uid = validate_user_id(user_id)
    return Path(app_config.get_workspace_root()) / uid


def get_skills_dir(user_id: str) -> Path:
    return get_user_root(user_id) / "skills"


def get_workspace_dir(user_id: str, session_id: str) -> Path:
    return get_user_root(user_id) / "workspace" / str(session_id)


def resolve_vm_workspace_dir_for_environment(environment: Optional[str] = None) -> Optional[str]:
    """虚机 NFS export 目录名：agent-dev | agent-test | agent-production。"""
    env = (environment or app_config.ENVIRONMENT or "").lower().strip()
    return {
        "dev": "agent-dev",
        "development": "agent-dev",
        "test": "agent-test",
        "production": "agent-production",
        "prod": "agent-production",
    }.get(env)


def get_sandbox_workspace_root() -> str:
    """远程 docker bind mount 使用的根路径（虚机视角）。

    Agent 侧 NFS 统一挂到 WORKSPACE_ROOT=/data/cmtool/agent；
    虚机上实际目录为 agent-dev|agent-test|agent-production。
    配置远程 SANDBOX_DOCKER_HOST 时，按 ENVIRONMENT 自动映射到对应虚机目录。
    """
    override = (app_config.SANDBOX_WORKSPACE_ROOT or "").strip()
    if override:
        return override.rstrip("/\\")

    docker_host = (app_config.SANDBOX_DOCKER_HOST or "").strip()
    if docker_host:
        vm_dir = resolve_vm_workspace_dir_for_environment()
        if vm_dir:
            ws = app_config.WORKSPACE_ROOT.rstrip("/\\").replace("\\", "/")
            if ws.endswith("/agent"):
                return ws.rsplit("/", 1)[0] + "/" + vm_dir
            return f"/data/cmtool/{vm_dir}"

    return app_config.get_workspace_root()


def get_sandbox_user_root(user_id: str) -> Path:
    uid = validate_user_id(user_id)
    return Path(get_sandbox_workspace_root()) / uid


def get_sandbox_workspace_dir(user_id: str, session_id: str) -> Path:
    return get_sandbox_user_root(user_id) / "workspace" / str(session_id)


def get_sandbox_skills_dir(user_id: str) -> Path:
    return get_sandbox_user_root(user_id) / "skills"


# ==================== Agent 专属目录（订阅智能体） ====================

def get_agent_root(agent_id: str) -> Path:
    """订阅智能体专属根目录：{WORKSPACE_ROOT}/.agents/{agent_id}/。"""
    aid = validate_agent_id(agent_id)
    return Path(app_config.get_workspace_root()) / ".agents" / aid


def get_agent_skills_dir(agent_id: str) -> Path:
    """订阅智能体专属 skills 目录：{WORKSPACE_ROOT}/.agents/{agent_id}/skills/。"""
    return get_agent_root(agent_id) / "skills"


def get_sandbox_agent_skills_dir(agent_id: str) -> Path:
    """沙箱视角的 agent 专属 skills 目录。"""
    aid = validate_agent_id(agent_id)
    return Path(get_sandbox_workspace_root()) / ".agents" / aid / "skills"


def remove_agent_directory(agent_id: str) -> None:
    """删除订阅智能体专属目录（硬删除时调用）。"""
    root = get_agent_root(agent_id)
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
        logger.info(f"[Workspace] 已删除 agent 专属目录 {root}")
    else:
        logger.debug(f"[Workspace] agent 目录不存在，跳过清理: {root}")


def _sync_agent_skill_to_sandbox(agent_id: str, skill_id: str) -> bool:
    """将 agent skill 从 WORKSPACE_ROOT 同步到 SANDBOX_WORKSPACE_ROOT。

    远程 Docker 场景下，bind mount 源必须在 VM 本地可访问的 SANDBOX 路径上。
    若 WORKSPACE 路径（NFS 客户端）与 SANDBOX 路径（NFS 服务端 export）分离，
    需要将文件拷贝到 SANDBOX 侧。
    """
    sid = (skill_id or "").strip().strip("/\\")
    if not sid:
        return False

    agent_root = get_agent_skills_dir(agent_id)
    dir_name = _resolve_skill_dir_name(agent_root, sid)
    ws_skill = agent_root / dir_name
    sandbox_skill = get_sandbox_agent_skills_dir(agent_id) / dir_name

    if not ws_skill.is_dir():
        return False
    try:
        if ws_skill.resolve() == sandbox_skill.resolve():
            return False
    except OSError:
        pass
    if not _workspace_roots_differ():
        return False
    if (app_config.SANDBOX_DOCKER_HOST or "").strip():
        return _sync_skill_tree_to_remote_host(ws_skill, sandbox_skill)

    sandbox_skill.parent.mkdir(parents=True, exist_ok=True)
    if _skill_tree_has_files(sandbox_skill):
        try:
            if sandbox_skill.stat().st_mtime >= ws_skill.stat().st_mtime:
                return False
        except OSError:
            pass

    if sandbox_skill.exists():
        shutil.rmtree(sandbox_skill, ignore_errors=True)
    shutil.copytree(ws_skill, sandbox_skill)
    logger.info(
        f"[Workspace] synced agent skill to sandbox bind path "
        f"agent={agent_id} skill={sid} dst={sandbox_skill}"
    )
    return True


def _workspace_roots_differ() -> bool:
    ws = Path(app_config.get_workspace_root()).resolve()
    sb = Path(get_sandbox_workspace_root()).resolve()
    return ws != sb


def _skill_tree_has_files(skill_dir: Path) -> bool:
    if not skill_dir.is_dir():
        return False
    for _root, _dirs, files in os.walk(skill_dir):
        if files:
            return True
    return False


def _sync_skill_tree_to_remote_host(source: Path, destination: Path, *, force: bool = False) -> bool:
    """确认 NFS 导出的技能目录已可供远程 dockerd 使用。

    Agent 与 VM 使用同一个 NFS export，只是挂载点不同；这里不应通过
    SSH/SFTP 以 sandbox 身份再次写入 VM 路径。
    """
    docker_host = (app_config.SANDBOX_DOCKER_HOST or "").strip()
    parsed = urlparse(docker_host)
    if parsed.scheme != "ssh" or not source.is_dir():
        return False

    try:
        # 两个挂载点均来自同一 NFS export；source 有文件即表示 VM 侧
        # 的 destination 也可见。不要以 sandbox SSH 用户重复写目录。
        if _skill_tree_has_files(source):
            return True
        return False
    except Exception as exc:
        logger.warning(
            f"[Workspace] remote skill visibility check failed src={source} dst={destination}: {exc}"
        )
        return False


def _skill_bind_path_candidates(user_id: str, skill_id: str) -> list:
    """Docker bind 候选宿主机路径（SANDBOX 根优先，其次 WORKSPACE 根）。"""
    sid = (skill_id or "").strip().strip("/\\")
    uid = validate_user_id(user_id)
    dir_name = _resolve_skill_dir_name(get_skills_dir(uid), sid)
    seen: set = set()
    out: list = []
    for root in (get_sandbox_workspace_root(), app_config.get_workspace_root()):
        r = str(root or "").strip().rstrip("/\\")
        if not r or r in seen:
            continue
        seen.add(r)
        out.append(Path(r) / uid / "skills" / dir_name)
    return out


def sync_skill_to_sandbox_bind_path(user_id: str, skill_id: str, *, force: bool = False) -> bool:
    """当 WORKSPACE_ROOT 与 SANDBOX_WORKSPACE_ROOT 分离时，将 skill 同步到 docker bind 路径。

    read_file 使用 get_skills_dir（WORKSPACE_ROOT）；容器 execute bind 使用 get_sandbox_skills_dir。
    远程沙箱常见 WORKSPACE_ROOT=/data/cmtool/agent、SANDBOX_WORKSPACE_ROOT=/data/cmtool/agent-test，
    若仅前者有文件会导致 /mnt/skills/{id} 为空。

    Returns:
        True 若执行了拷贝或新建了 sandbox 侧目录。
    """
    sid = (skill_id or "").strip().strip("/\\")
    if not sid or ".." in sid or "/" in sid or "\\" in sid:
        return False

    skills_root = get_skills_dir(user_id)
    dir_name = _resolve_skill_dir_name(skills_root, sid)
    ws_skill = skills_root / dir_name
    sandbox_skill = get_sandbox_skills_dir(user_id) / dir_name
    if not ws_skill.is_dir():
        return False

    try:
        if ws_skill.resolve() == sandbox_skill.resolve():
            return False
    except OSError:
        pass

    if not _workspace_roots_differ():
        return False

    if (app_config.SANDBOX_DOCKER_HOST or "").strip():
        return _sync_skill_tree_to_remote_host(ws_skill, sandbox_skill, force=force)

    sandbox_skill.parent.mkdir(parents=True, exist_ok=True)
    if _skill_tree_has_files(sandbox_skill):
        try:
            if sandbox_skill.stat().st_mtime >= ws_skill.stat().st_mtime:
                return False
        except OSError:
            pass

    if sandbox_skill.exists():
        shutil.rmtree(sandbox_skill, ignore_errors=True)
    shutil.copytree(ws_skill, sandbox_skill)
    logger.info(
        f"[Workspace] synced skill to sandbox bind path user={user_id} skill={sid} "
        f"dst={sandbox_skill}"
    )
    return True


def sync_skills_to_sandbox_bind_paths(user_id: str, skill_ids: list) -> bool:
    """批量同步 skill 到 SANDBOX_WORKSPACE_ROOT；任一拷贝则返回 True。"""
    changed = False
    for sid in skill_ids or []:
        if sync_skill_to_sandbox_bind_path(user_id, str(sid)):
            changed = True
    return changed


def sync_builtin_skill_to_sandbox_bind_path(user_id: str, skill_id: str) -> bool:
    """远程 dockerd 时将内置技能从 skill-repo 同步到 SANDBOX_WORKSPACE_ROOT。

    内置技能源在 Agent 进程本机（skill-repo 项目目录），不在 NFS export 上，
    VM 的 dockerd 无法 stat 该路径 —— 直接 bind 会得到空文件夹。
    因此远程沙箱下必须显式拷贝到虚机可见的 sandbox skills 目录。

    Returns:
        True 若执行了拷贝。
    """
    from deep_agent_server.skills.selector import BUILTIN_SKILL_IDS

    sid = (skill_id or "").strip().strip("/\\")
    if not sid or ".." in sid or "/" in sid or "\\" in sid:
        return False
    if sid not in BUILTIN_SKILL_IDS:
        return False

    src = Path(app_config.get_skill_repo_root()) / sid
    if not _skill_tree_has_files(src):
        return False

    dst = get_sandbox_skills_dir(user_id) / sid
    if _skill_tree_has_files(dst):
        try:
            if dst.stat().st_mtime >= src.stat().st_mtime:
                return False
        except OSError:
            pass

    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)
    logger.info(
        f"[Workspace] synced builtin skill to sandbox bind path "
        f"user={user_id} skill={sid} dst={dst}"
    )
    return True


def resolve_skill_bind_host_path(
    user_id: str, skill_id: str, *, agent_id: Optional[str] = None
) -> Optional[Path]:
    """解析 Docker bind mount 应使用的宿主机 skill 目录（须含文件）。

    远程沙箱时 Agent 进程往往只能访问 WORKSPACE_ROOT（如 NFS 挂到 /data/cmtool/agent），
    而 dockerd 在虚机上 bind 的是 SANDBOX_WORKSPACE_ROOT（如 /data/cmtool/agent-test）。
    二者为同一 NFS export 时，Agent 侧可能 stat 不到 agent-test 路径，但仍应 bind 虚机上的
    sandbox 路径。若 sandbox 路径在虚机上为空而 agent 路径有文件，则 fallback bind agent 路径。

    若传入 agent_id，优先检查 .agents/{agent_id}/skills/{skill_id} 下的文件。

    内置技能优先从 skill-repo/ 绑定（只读，所有用户共享）。
    """
    from deep_agent_server.skills.selector import BUILTIN_SKILL_IDS

    sid = (skill_id or "").strip().strip("/\\")
    if not sid or ".." in sid or "/" in sid or "\\" in sid:
        return None

    # 订阅智能体：文件工具（/skills/ 路由到 agent_skills_root）与容器 bind
    # 统一走 .agents/{agent_id}/skills/（同源目录），内置技能也不例外——
    # 内置技能在订阅会话中已由 _ensure_builtin_skill_in_user_dir_for_agent
    # 安装到 .agents 侧，此处必须跳过 skill-repo 分支，否则 bind 与文件工具
    # 会落到不同目录（skill-repo / 用户目录），造成沙箱与 /skills/ 不一致。
    if agent_id:
        agent_root = get_agent_skills_dir(agent_id)
        sid = _resolve_skill_dir_name(agent_root, sid)
        agent_skill = agent_root / sid
        sandbox_agent_skill = get_sandbox_agent_skills_dir(agent_id) / sid

        # 远程 dockerd：直接信任 NFS 对齐，返回 SANDBOX 路径
        # （Agent 进程 stat 不到 VM 上的 SANDBOX 路径，但 NFS 保证数据一致）
        if (app_config.SANDBOX_DOCKER_HOST or "").strip() and _workspace_roots_differ():
            _sync_agent_skill_to_sandbox(agent_id, sid)
            # 同源拓扑（虚机 agent-test ↔ Agent 机 agent 同一 NFS 数据）下
            # 以 Agent 侧 .agents 是否有内容为据：缺失时跳过 bind，
            # 与文件工具（同样读不到）保持一致
            if not _skill_tree_has_files(agent_skill):
                logger.warning(
                    f"[Workspace] agent skill 目录不存在或为空，跳过 bind: "
                    f"agent={agent_id} skill={sid} path={agent_skill}"
                )
                return None
            logger.info(
                f"[Workspace] skill docker bind (agent sandbox remote): {sandbox_agent_skill}"
            )
            return sandbox_agent_skill

        if _skill_tree_has_files(agent_skill):
            logger.info(f"[Workspace] skill docker bind (agent): {agent_skill}")
            return agent_skill.resolve()
        if _skill_tree_has_files(sandbox_agent_skill):
            logger.info(f"[Workspace] skill docker bind (agent sandbox): {sandbox_agent_skill}")
            return sandbox_agent_skill.resolve()

        # .agents 侧缺失：与文件工具保持一致（文件工具同样读不到该技能），
        # 不 fallback 用户目录，避免沙箱可用而 /skills/ 为空的不一致
        logger.warning(
            f"[Workspace] agent skill 目录不存在或为空，跳过 bind: "
            f"agent={agent_id} skill={sid} path={agent_skill}"
        )
        return None

    # 内置技能（default 智能体）：直接从 skill-repo 绑定（只读，不经过用户目录）
    if sid in BUILTIN_SKILL_IDS:
        from config.config import app_config as _cfg
        repo_root = _cfg.get_skill_repo_root()
        builtin_path = Path(repo_root) / sid
        if not _skill_tree_has_files(builtin_path):
            logger.warning(
                f"[Workspace] builtin skill 源目录不可用, skill={sid} path={builtin_path}"
            )
            return None
        # 远程 dockerd：skill-repo 在 Agent 进程本机，VM 不可见，直接 bind 会得到
        # 空文件夹。须同步到 SANDBOX_WORKSPACE_ROOT（虚机可见）后 bind sandbox 路径。
        if (app_config.SANDBOX_DOCKER_HOST or "").strip() and _workspace_roots_differ():
            sync_builtin_skill_to_sandbox_bind_path(user_id, sid)
            sandbox_builtin = get_sandbox_skills_dir(user_id) / sid
            logger.info(
                f"[Workspace] skill docker bind (builtin sandbox): {sandbox_builtin}"
            )
            return sandbox_builtin
        logger.info(f"[Workspace] skill docker bind (builtin): {builtin_path}")
        return builtin_path.resolve()

    skills_root = get_skills_dir(user_id)
    sid = _resolve_skill_dir_name(skills_root, sid)
    ws_skill = skills_root / sid
    sandbox_skill = get_sandbox_skills_dir(user_id) / sid

    # 双向一致性自愈：SANDBOX 侧有文件而 WORKSPACE 侧为空（卸载残留 / NFS
    # 漂移等导致的两侧不一致）时，反向同步回 WORKSPACE，使 /skills/（文件工具）
    # 与 /mnt/skills/（容器 bind）保持一致；远程 dockerd 下不可达则跳过。
    if _workspace_roots_differ() and _skill_tree_has_files(sandbox_skill) \
            and not _skill_tree_has_files(ws_skill):
        try:
            if ws_skill.exists():
                shutil.rmtree(ws_skill, ignore_errors=True)
            ws_skill.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(sandbox_skill, ws_skill)
            logger.info(
                f"[Workspace] synced skill back from sandbox bind path "
                f"user={user_id} skill={sid} dst={ws_skill}"
            )
        except OSError:
            logger.warning(
                f"[Workspace] sandbox skill 不可达，跳过反向同步: "
                f"user={user_id} skill={sid}"
            )

    if not ((app_config.SANDBOX_DOCKER_HOST or "").strip() and _workspace_roots_differ()):
        sync_skill_to_sandbox_bind_path(user_id, sid)

    # 远程 dockerd 时 Agent 进程 stat 不到虚机上的 SANDBOX 路径，
    # 直接信任 NFS 对齐，优先使用 SANDBOX 路径作为 Docker bind mount 源
    if (app_config.SANDBOX_DOCKER_HOST or "").strip() and _workspace_roots_differ():
        if not sync_skill_to_sandbox_bind_path(user_id, sid):
            logger.error(
                f"[Workspace] remote skill unavailable, skip bind: "
                f"user={user_id} skill={sid} path={sandbox_skill}"
            )
            return None
        logger.info(f"[Workspace] remote docker skill bind sandbox path: {sandbox_skill}")
        return sandbox_skill

    candidates = _skill_bind_path_candidates(user_id, sid)
    for path in candidates:
        if _skill_tree_has_files(path):
            logger.info(f"[Workspace] skill docker bind: {path}")
            return path.resolve()

    ws_skill = get_skills_dir(user_id) / sid

    if _skill_tree_has_files(ws_skill):
        if _workspace_roots_differ():
            if (app_config.SANDBOX_DOCKER_HOST or "").strip():
                logger.info(
                    f"[Workspace] remote docker skill bind VM path: {sandbox_skill}"
                )
                return sandbox_skill
            logger.warning(
                f"[Workspace] SANDBOX_WORKSPACE_ROOT 下 skill 为空，fallback bind "
                f"WORKSPACE_ROOT 路径（请确认虚机上该路径对 dockerd 可见）: "
                f"user={user_id} skill={sid} path={ws_skill}"
            )
            return ws_skill.resolve()
        return ws_skill.resolve()

    if _skill_tree_has_files(sandbox_skill):
        return sandbox_skill.resolve()

    if ws_skill.is_dir() and _workspace_roots_differ():
        logger.info(
            f"[Workspace] skill bind 假定 NFS 对齐，使用 SANDBOX 路径: {sandbox_skill}"
        )
        return sandbox_skill

    logger.warning(
        f"[Workspace] 无可用 skill bind 路径 user={user_id} skill={sid} "
        f"candidates={[str(p) for p in candidates]}"
    )
    return None


def get_session_skills_mount_dir(user_id: str, session_id: str) -> Path:
    """容器内 execute 路径对应宿主机占位：workspace/{session_id}/.sandbox/skills/。

    宿主机不 intentionally 维护 skill 文件；Windows 上 Docker 嵌套 bind mount 可能临时生成空目录。
    """
    return get_workspace_dir(user_id, session_id) / ".sandbox" / "skills"


def get_uploads_dir(user_id: str) -> Path:
    return get_user_root(user_id) / "uploads"


def get_session_uploads_dir(user_id: str, session_id: str) -> Path:
    """会话级上传目录：{user_id}/workspace/{session_id}/uploads/"""
    return get_workspace_dir(user_id, session_id) / "uploads"


def get_vm_skill_repo_dir() -> Path:
    """返回 VM skill 仓库根路径（非用户目录，不计入配额）。"""
    return Path(app_config.get_vm_skill_repo_root())


def get_artifacts_dir(user_id: str) -> Path:
    return get_user_root(user_id) / "artifacts"


def ensure_user_dirs(user_id: str) -> Path:
    """确保用户根目录及 skills/uploads 存在。"""
    root = get_user_root(user_id)
    for sub in ("skills", "uploads", "artifacts"):
        _ensure_dir_writable(root / sub)
    return root


def _ensure_dir_writable(path: Path, mode: int = 0o777) -> None:
    """创建目录并尽量设为容器内 root 可写（缓解 Windows bind mount 775 问题）。"""
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, mode)
    except OSError as e:
        logger.debug(f"[Workspace] chmod {path} -> {oct(mode)} skipped: {e}")


def get_sandbox_dir(user_id: str, session_id: str) -> Path:
    return get_workspace_dir(user_id, session_id) / ".sandbox"


def ensure_session_workspace_permissions(user_id: str, session_id: str) -> Path:
    """会话工作区根目录可写；manifest 在 .sandbox/，skill 内容由 Docker bind mount 进容器。"""
    ws = get_workspace_dir(user_id, session_id)
    _ensure_dir_writable(ws, 0o777)
    _ensure_dir_writable(get_sandbox_dir(user_id, session_id), 0o755)
    return ws


async def ensure_session_dirs(user_id: str, session_id: str) -> Path:
    """创建 session 工作区目录（新建 session 时调用）。"""
    ensure_user_dirs(user_id)
    ws = ensure_session_workspace_permissions(user_id, session_id)
    logger.info(f"[Workspace] 已创建工作区 {ws}")
    return ws


def safe_join(base: Path, rel_path: str) -> Path:
    """防止路径穿越。"""
    rel = (rel_path or "").lstrip("/").replace("\\", "/")
    target = (base / rel).resolve()
    base_resolved = base.resolve()
    if not str(target).startswith(str(base_resolved)):
        raise ValueError(f"路径越界: {rel_path}")
    return target


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def dir_size_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for root, dirs, files in os.walk(path):
        # HTML 分享快照目录不占用户配额（含任一层级同名目录；该目录名为分享功能保留字，
        # 与 .sandbox 同级取舍——用户自建同名目录同样豁免，见 html_share_snapshot 模块注释）
        dirs[:] = [d for d in dirs if d != HTML_SHARE_SNAPSHOT_DIR]
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def remove_session_workspace(user_id: str, session_id: str) -> None:
    ws = get_workspace_dir(user_id, session_id)
    if ws.exists():
        shutil.rmtree(ws, ignore_errors=True)
        logger.info(f"[Workspace] 已删除 session 工作区 {ws}")


def remove_skill_installation(user_id: str, skill_id: str) -> None:
    """卸载 skill：删除用户级目录 {user}/skills/{id}/（WORKSPACE 与 SANDBOX 双侧）。

    只删单侧会导致 /skills/（文件工具）与 /mnt/skills/（容器 bind）不一致：
    卸载后 SANDBOX 侧残留目录会被 resolve_skill_bind_host_path 探测到并继续
    bind，造成沙箱内技能仍可用、宿主机侧已空的假象。
    """
    uid = validate_user_id(user_id)
    sid = (skill_id or "").strip().strip("/\\")
    if not sid or ".." in sid or "/" in sid or "\\" in sid:
        raise ValueError(f"非法 skill_id: {skill_id}")

    skills_root = get_skills_dir(uid)
    dir_name = _resolve_skill_dir_name(skills_root, sid)
    user_skill = skills_root / dir_name
    if user_skill.exists():
        shutil.rmtree(user_skill, ignore_errors=True)
        logger.info(f"[Workspace] 已删除用户 skill 目录 {user_skill}")

    # SANDBOX 侧（远程 dockerd 时 Agent 进程可能不可达，best effort）
    try:
        sandbox_skill = get_sandbox_skills_dir(uid) / dir_name
        if _workspace_roots_differ() and sandbox_skill.exists():
            shutil.rmtree(sandbox_skill, ignore_errors=True)
            logger.info(f"[Workspace] 已删除 sandbox skill 目录 {sandbox_skill}")
    except OSError:
        logger.warning(
            f"[Workspace] sandbox skill 目录不可达，跳过删除: "
            f"user={uid} skill={sid}"
        )


def remove_user_workspace(user_id: str) -> None:
    root = get_user_root(user_id)
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
        logger.info(f"[Workspace] 已删除用户工作区 {root}")
