"""Deep Agent CompositeBackend 构建。

Deep Agent 使用虚拟路径路由文件操作：
  /skills/   → 用户 skill 目录（勾选由 build_permissions 过滤）
  /workspace/→ session 工作区
  /memories/ → LangGraph Store 持久化（可选）
"""

from pathlib import Path
from typing import Any, Optional

from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend, StoreBackend
from deepagents.middleware.filesystem import FilesystemPermission

from config.config import app_config
from deep_agent_server.quota_backend import QuotaFilesystemBackend
from deep_agent_server.workspace import get_skills_dir, get_workspace_dir


def build_filesystem_routes(
    user_id: str,
    session_id: str,
    store=None,
    *,
    skills_root: Optional[Path] = None,
    agent_skills_root: Optional[Path] = None,
) -> dict:
    """CompositeBackend 的文件路由（/skills、/workspace、可选 /memories）。

    agent_skills_root 不为 None 时 /skills/ 路由到订阅智能体专属目录，
    跳过 QuotaFilesystemBackend（agent skill 不计入用户配额）。
    """
    if agent_skills_root is not None:
        resolved_skills_root = str(agent_skills_root)
    elif skills_root is not None:
        resolved_skills_root = str(skills_root)
    else:
        resolved_skills_root = str(get_skills_dir(user_id))

    workspace_root = str(get_workspace_dir(user_id, session_id))

    if agent_skills_root is not None:
        # 订阅智能体：/skills/ 不计入配额
        skills_route = FilesystemBackend(
            root_dir=resolved_skills_root, virtual_mode=True
        )
    else:
        skills_route = QuotaFilesystemBackend(
            FilesystemBackend(root_dir=resolved_skills_root, virtual_mode=True),
            user_id=user_id,
            file_kind="skill",
            route_prefix="skills",
        )

    routes = {
        "/skills/": skills_route,
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
        )

    return _factory


def build_backend_factory(
    user_id: str,
    session_id: str,
    store=None,
    *,
    agent_skills_root: Optional[Path] = None,
):
    """返回 create_deep_agent 可用的 backend 工厂（lambda rt）。"""

    def _factory(rt):
        return CompositeBackend(
            default=StateBackend(),
            routes=build_filesystem_routes(
                user_id, session_id, store=store,
                agent_skills_root=agent_skills_root,
            ),
        )

    return _factory


def build_permissions(allowed_skill_ids: Optional[list] = None) -> list:
    """构建文件权限。allowed_skill_ids 非 None 时仅暴露选中的 /skills/{id}/（只读）。

    注意：deepagents 权限为 first-match；具体 deny 必须排在宽泛 allow 之前。
    """
    spill_glob = f"/workspace/{(app_config.TOOL_SPILL_VIRTUAL_DIR or '.tool-cache').strip('/')}/**"
    perms = [
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
        FilesystemPermission(
            operations=["write", "edit"],
            paths=[spill_glob],
            mode="deny",
        ),
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
    ]
    if allowed_skill_ids is None:
        perms.append(
            FilesystemPermission(
                operations=["read"],
                paths=["/skills/**"],
                mode="allow",
            )
        )
    else:
        for sid in allowed_skill_ids:
            sid = str(sid).strip()
            if sid:
                # Allow the directory itself (for ls listing) AND its contents
                perms.append(
                    FilesystemPermission(
                        operations=["read"],
                        paths=[
                            f"/skills/{sid}",
                            f"/skills/{sid}/",
                            f"/skills/{sid}/**",
                        ],
                        mode="allow",
                    )
                )
        perms.append(
            FilesystemPermission(
                operations=["read"],
                paths=["/skills/**"],
                mode="deny",
            )
        )
    perms.append(
        FilesystemPermission(
            operations=["write", "edit"],
            paths=["/skills/**"],
            mode="deny",
        )
    )
    perms.append(
        FilesystemPermission(
            operations=["read", "write"],
            paths=[
                # 禁止读写敏感路径（与 build_permissions deny 规则互补）
                "/workspace/**/.env",
                "/workspace/**/.env.*",
                "/skills/**/.env",
                "/workspace/**/credentials*",
                "/skills/**/credentials*",
                "/workspace/**/secrets/**",
                "/skills/**/secrets/**",
            ],
            mode="deny",
        )
    )
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
