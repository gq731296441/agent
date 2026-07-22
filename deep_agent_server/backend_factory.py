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
from deep_agent_server.workspace import get_skills_dir, get_workspace_dir


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
        """检查路径是否属于白名单内的 skill 目录。"""
        if self._allowed_ids is None:
            return True
        # file_path 格式: /skills/installed/{skill_id}/SKILL.md 或 /skills/installed/{skill_id}
        parts = Path(file_path.lstrip("/")).parts
        # 跳过第一个 segment ("skills")，检查第二个 ("installed")，第三个是 skill_id
        if len(parts) >= 3:
            return parts[2] in self._allowed_ids
        # 根路径或 skills/installed/ 本身总是允许
        return True

    def ls(self, path: str) -> LsResult:
        return self._filter_entries(self._inner.ls(path))

    async def als(self, path: str) -> LsResult:
        return self._filter_entries(await self._inner.als(path))

    def _filter_entries(self, result: Any) -> LsResult:
        """对 ls/als 结果按 _allowed_ids 过滤：仅保留白名单内的目录条目。"""
        if self._allowed_ids is None:
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

    # 合并可见 skill：内置技能 + 用户勾选技能
    allowed = set(BUILTIN_SKILL_IDS)
    if selected_skill_ids:
        allowed.update(selected_skill_ids)

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

    routes = {
        "/skills/": skill_backend,
        "/workspace/": QuotaFilesystemBackend(
            FilesystemBackend(root_dir=workspace_root, virtual_mode=True),
            user_id=user_id,
            session_id=session_id,
            file_kind="workspace",
            route_prefix="workspace",
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
