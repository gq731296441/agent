"""Deep Agent CompositeBackend 构建。

Deep Agent 使用虚拟路径路由文件操作：
  /skills/builtin/   → 内置技能（skill-repo/，只读，所有用户共享）
  /skills/installed/ → 用户安装技能（{user}/skills/，由 SkillFilterBackend 按 selected_skill_ids 过滤）
  /workspace/        → session 工作区
  /memories/         → LangGraph Store 持久化（可选）
"""

from pathlib import Path
from typing import Any, Optional

from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend, StoreBackend
from deepagents.backends.protocol import (
    BackendProtocol,
    FileDownloadResponse,
    LsResult,
)
from deepagents.middleware.filesystem import FilesystemPermission

from config.config import app_config
from deep_agent_server.quota_backend import QuotaFilesystemBackend
from deep_agent_server.workspace import get_skills_dir, get_uploads_dir, get_workspace_dir


# ── Skill 过滤 Backend ───────────────────────────────────────────────────────
class SkillFilterBackend:
    """包装 Backend，限制 ls / download_files 仅返回白名单内的 skill 目录。

    供 SkillsMiddleware 使用：SkillsMiddleware 通过 backend.ls(source_path)
    发现 skill 目录，此包装器确保仅 selected_skill_ids 中的 skill 可见。
    """

    def __init__(
        self,
        inner: Any,  # FilesystemBackend 或 QuotaFilesystemBackend
        *,
        allowed_ids: Optional[set[str]] = None,
    ):
        self._inner = inner
        # None 表示不过滤（所有 skill 可见）
        self._allowed_ids = allowed_ids

    def _is_allowed(self, file_path: str) -> bool:
        """检查路径是否属于白名单内的 skill 目录。

        注意：CompositeBackend 路由转发时已剥离前缀（/skills/ 路由），
        子 backend 收到的路径形如 /{skill_id}/SKILL.md，skill_id 是首段；
        兼容旧版 /installed/{skill_id}/... 结构。
        """
        if self._allowed_ids is None:
            return True
        parts = Path(file_path.lstrip("/")).parts
        if not parts:
            return True
        skill_id = parts[1] if len(parts) >= 2 and parts[0] == "installed" else parts[0]
        return skill_id in self._allowed_ids

    def _is_skill_root_level(self, path: str) -> bool:
        """判断 ls 路径是否为技能根层级（需按白名单过滤技能目录）。

        CompositeBackend 剥离路由前缀后传入：根层级为 "/"；
        兼容直连时的 "/skills/"（虚拟路由前缀）。更深的技能内部路径
        （/skills/{skill_id}/...）不参与过滤，否则技能内文件/子目录
        会被 is_dir+白名单 条件误过滤导致 ls 返回空。
        """
        rel = path.strip("/")
        return rel == "" or rel == "skills"

    def ls(self, path: str) -> LsResult:
        return self._filter_entries(self._inner.ls(path), path)

    async def als(self, path: str) -> LsResult:
        return self._filter_entries(await self._inner.als(path), path)

    def _filter_entries(self, result: Any, path: str) -> LsResult:
        """对 ls/als 结果按 _allowed_ids 过滤：仅技能根层级保留白名单目录。

        技能内部（子目录/文件）原样返回，避免 ls /skills/{skill_id}/ 被
        is_dir + 白名单条件误过滤成空列表。
        """
        if self._allowed_ids is None or not self._is_skill_root_level(path):
            return result if isinstance(result, LsResult) else LsResult(entries=result)
        entries = result.entries if isinstance(result, LsResult) else result
        if entries:
            entries = [
                e for e in entries
                if e.get("is_dir") and Path(e["path"]).name in self._allowed_ids
            ]
            if isinstance(result, LsResult):
                result.entries = entries
                return result
            return LsResult(entries=entries)
        return result if isinstance(result, LsResult) else LsResult(entries=entries or [])

    def download_files(self, file_paths: list[str]) -> list[FileDownloadResponse]:
        if self._allowed_ids is not None:
            file_paths = [p for p in file_paths if self._is_allowed(p)]
        return self._inner.download_files(file_paths)

    async def adownload_files(self, file_paths: list[str]) -> list[FileDownloadResponse]:
        if self._allowed_ids is not None:
            file_paths = [p for p in file_paths if self._is_allowed(p)]
        return await self._inner.adownload_files(file_paths)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


# ── 路由构建 ──────────────────────────────────────────────────────────────────


def build_filesystem_routes(
    user_id: str,
    session_id: str,
    store=None,
    *,
    skills_root: Optional[Path] = None,
    agent_skills_root: Optional[Path] = None,
    selected_skill_ids: Optional[list[str]] = None,
) -> dict:
    """CompositeBackend 的文件路由。

    - /skills/ → 统一 skill 目录，内置技能 + 用户安装技能合并
      - 内置技能通过 ensure_builtin_skills_for_user 复制到用户目录
      - SkillFilterBackend 按 BUILTIN_SKILL_IDS ∪ selected_skill_ids 过滤可见性
    - /workspace/ → session 工作区
    """

    from deep_agent_server.skills.selector import BUILTIN_SKILL_IDS

    # 合并可见 skill：内置技能 + 用户勾选技能。
    # selected_skill_ids=None（未指定勾选）时不过滤，显示全部候选技能——
    # 与沙箱挂载语义一致（ensure_session_skill_mount 对 None 挂载全部候选），
    # 避免 /skills/ 与 /mnt/skills/ 可见技能不一致。
    if selected_skill_ids is None:
        allowed = None
    else:
        allowed = set(BUILTIN_SKILL_IDS) | set(selected_skill_ids)

    if agent_skills_root is not None:
        skill_backend = SkillFilterBackend(
            FilesystemBackend(root_dir=str(agent_skills_root), virtual_mode=True),
            allowed_ids=allowed,
        )
    else:
        skill_backend = SkillFilterBackend(
            QuotaFilesystemBackend(
                FilesystemBackend(root_dir=str(get_skills_dir(user_id)), virtual_mode=True),
                user_id=user_id,
                file_kind="skill",
                route_prefix="skills",
            ),
            allowed_ids=allowed,
        )

    workspace_root = str(get_workspace_dir(user_id, session_id))
    uploads_root = str(get_uploads_dir(user_id))

    routes = {
        "/skills/": skill_backend,
        "/workspace/": QuotaFilesystemBackend(
            FilesystemBackend(root_dir=workspace_root, virtual_mode=True),
            user_id=user_id,
            session_id=session_id,
            file_kind="workspace",
            route_prefix="workspace",
        ),
        "/uploads/": QuotaFilesystemBackend(
            FilesystemBackend(root_dir=uploads_root, virtual_mode=True),
            user_id=user_id,
            file_kind="upload",
            route_prefix="uploads",
        ),
    }
    if store is not None:
        routes["/memories/"] = StoreBackend(
            namespace=lambda _rt: (user_id, session_id, "filesystem"),
            store=store,
        )
    return routes


def build_sandbox_backend_factory(
    user_id: str,
    session_id: str,
    container_id: str,
    store=None,
    user_token: Optional[str] = None,
    *,
    agent_skills_root: Optional[Path] = None,
    agent_id: Optional[str] = None,
    selected_skill_ids: Optional[list[str]] = None,
):
    """返回 deepagents BackendFactory：每次 resolve 时构建带 /skills、/workspace 路由的沙箱 backend。"""

    def _factory(_rt):
        from deep_agent_server.sandbox.docker_backend import DockerSandboxBackend

        skills_root = get_skills_dir(user_id)
        return DockerSandboxBackend(
            user_id=user_id,
            container_id=container_id,
            session_id=session_id,
            store=store,
            user_token=user_token,
            skills_root=skills_root,
            agent_skills_root=agent_skills_root,
            agent_id=agent_id,
            selected_skill_ids=selected_skill_ids,
        )

    return _factory


def build_backend_factory(
    user_id: str,
    session_id: str,
    store=None,
    *,
    agent_skills_root: Optional[Path] = None,
    selected_skill_ids: Optional[list[str]] = None,
):
    """返回 create_deep_agent 可用的 backend 工厂（lambda rt）。"""

    def _factory(rt):
        return CompositeBackend(
            default=StateBackend(),
            routes=build_filesystem_routes(
                user_id, session_id, store=store,
                agent_skills_root=agent_skills_root,
                selected_skill_ids=selected_skill_ids,
            ),
        )

    return _factory


def build_permissions() -> list:
    """构建文件权限。skill 目录只读，workspace 可读写。

    注意：deepagents 权限为 first-match；具体 deny 必须排在宽泛 allow 之前。
    """
    spill_glob = f"/workspace/{(app_config.TOOL_SPILL_VIRTUAL_DIR or '.tool-cache').strip('/')}/**"
    perms = [
        # ── 沙箱目录禁止写入 ──
        FilesystemPermission(
            operations=["write", "edit"],
            paths=["/workspace/.sandbox/**"],
            mode="deny",
        ),
        FilesystemPermission(
            operations=["read"],
            paths=[
                "/workspace/.sandbox/skills",
                "/workspace/.sandbox/skills/**",
            ],
            mode="deny",
        ),
        # ── spill 目录禁止写入 ──
        FilesystemPermission(
            operations=["write", "edit"],
            paths=[spill_glob],
            mode="deny",
        ),
        # ── 沙箱 mount manifest 可读 ──
        FilesystemPermission(
            operations=["read"],
            paths=[
                "/workspace/.sandbox/.mount_manifest.json",
                "/workspace/.sandbox/skills/.mount_manifest.json",
            ],
            mode="allow",
        ),
        FilesystemPermission(
            operations=["read"],
            paths=["/workspace/.sandbox/**"],
            mode="allow",
        ),
        # ── workspace 读写 ──
        FilesystemPermission(
            operations=["read", "write"],
            paths=["/workspace/**"],
            mode="allow",
        ),
        FilesystemPermission(
            operations=["read"],
            paths=[spill_glob],
            mode="allow",
        ),
        # ── Skills：只读 ──
        FilesystemPermission(
            operations=["read"],
            paths=["/skills/", "/skills/**"],
            mode="allow",
        ),
        FilesystemPermission(
            operations=["write", "edit"],
            paths=["/skills/**"],
            mode="deny",
        ),
        # ── Uploads：只读 ──
        FilesystemPermission(
            operations=["read"],
            paths=["/uploads/", "/uploads/**"],
            mode="allow",
        ),
        FilesystemPermission(
            operations=["write", "edit"],
            paths=["/uploads/**"],
            mode="deny",
        ),
        # ── 敏感文件禁止 ──
        FilesystemPermission(
            operations=["read", "write"],
            paths=[
                "/workspace/**/.env",
                "/workspace/**/.env.*",
                "/skills/**/.env",
                "/workspace/**/credentials*",
                "/skills/**/credentials*",
                "/workspace/**/secrets/**",
                "/skills/**/secrets/**",
            ],
            mode="deny",
        ),
    ]
    return perms


def build_interrupt_on(agent_config: dict) -> dict:
    """从 agent config 合并 HITL 策略，默认 write/edit/execute 需审批。

    HITL_ENABLED=False 时返回 {}，表示不中断任何工具调用。
    """
    if not app_config.HITL_ENABLED:
        return {}
    policy = (agent_config or {}).get("hitl_policy") or {}
    default = {
        "write_file": True,
        "edit_file": True,
        "execute": True,
    }
    default.update(policy)
    return default
