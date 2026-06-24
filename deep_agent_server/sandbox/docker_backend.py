"""Docker 沙箱 Backend — 实现 execute via docker exec。

deepagents 要求 CompositeBackend.default 实现 SandboxBackendProtocol，
否则 execute 工具会报 Execution not available。

文件工具（read_file 等）必须走 routes 中的 /skills/、/workspace/；
若误落到 default（_DockerExecBackend），会 NotImplementedError。
"""

from pathlib import Path
from typing import Any, Optional

from deepagents.backends.composite import (
    CompositeBackend,
    _remap_file_info_path,
    _remap_grep_path,
    _route_for_path,
    _strip_route_from_pattern,
)
from deepagents.backends.protocol import (
    BackendProtocol,
    ExecuteResponse,
    GlobResult,
    GrepResult,
    LsResult,
    SandboxBackendProtocol,
)

from config import get_logger
from deep_agent_server.backend_factory import build_filesystem_routes
from deep_agent_server.sandbox.docker_client import get_docker_client
from deep_agent_server.sandbox.env import build_exec_user_env, shell_env_prefix

logger = get_logger(__name__)


class _DockerExecBackend(SandboxBackendProtocol):
    """仅负责容器内命令执行，作为 CompositeBackend 的 default。"""

    def __init__(self, user_id: str, container_id: str, user_token: Optional[str] = None):
        self.user_id = user_id
        self.container_id = container_id
        self.user_token = user_token or ""

    @property
    def id(self) -> str:
        return f"docker:{self.container_id[:12]}"

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        text, code = self._exec_sync(command, timeout or 120)
        return ExecuteResponse(output=text, exit_code=code, truncated=False)

    def _exec_sync(self, command: str, timeout: int) -> tuple[str, int]:
        client = get_docker_client()
        c = client.containers.get(self.container_id)
        exec_env = build_exec_user_env(self.user_id, self.user_token)
        prefix = shell_env_prefix(exec_env)
        kwargs: dict = {"workdir": "/workspace", "demux": False}
        if exec_env:
            kwargs["environment"] = exec_env
        cmd = prefix + command if prefix else command
        code, out = c.exec_run(
            ["bash", "-lc", cmd],
            user="root",
            **kwargs,
        )
        text = out.decode("utf-8", errors="replace") if out else ""
        return text, int(code)


class DockerSandboxBackend(CompositeBackend):
    """文件走 /skills、/workspace 路由（与无沙箱一致），execute 走 default。"""

    def __init__(
        self,
        user_id: str,
        container_id: str,
        session_id: str,
        store: Any = None,
        user_token: Optional[str] = None,
        skills_root: Optional[Path] = None,
    ):
        self.user_id = user_id
        self.container_id = container_id
        self.session_id = session_id
        self._exec_backend = _DockerExecBackend(user_id, container_id, user_token=user_token)
        routes = build_filesystem_routes(
            user_id, session_id, store=store, skills_root=skills_root
        )
        super().__init__(
            default=self._exec_backend,
            routes=routes,
        )
        logger.debug(
            f"[SandboxBackend] user={user_id} session={session_id} "
            f"routes={list(routes.keys())} container={container_id[:12]}"
        )

    def _list_virtual_roots(self) -> LsResult:
        """ls("/")：仅列出 /skills、/workspace 等虚拟路由，不调用 exec default。"""
        entries = [
            {
                "path": route_prefix,
                "is_dir": True,
                "size": 0,
                "modified_at": "",
            }
            for route_prefix, _backend in self.sorted_routes
        ]
        entries.sort(key=lambda x: x.get("path", ""))
        return LsResult(entries=entries)

    def _resolve_fs_backend(self, path: str) -> tuple[BackendProtocol, str] | None:
        """解析到可读写 backend；若仅 exec default 则返回 None。"""
        normalized = path if path.startswith("/") else f"/{path}"
        backend, stripped = self._get_backend_and_key(normalized)
        if backend is self._exec_backend:
            return None
        return backend, stripped

    def ls(self, path: str) -> LsResult:
        if path == "/":
            return self._list_virtual_roots()
        resolved = self._resolve_fs_backend(path)
        if resolved is None:
            return LsResult(entries=[], error=f"Unsupported path for ls: {path}")
        backend, stripped = resolved
        return self._coerce_ls_result(backend.ls(stripped))

    async def als(self, path: str) -> LsResult:
        if path == "/":
            return self._list_virtual_roots()
        resolved = self._resolve_fs_backend(path)
        if resolved is None:
            return LsResult(entries=[], error=f"Unsupported path for ls: {path}")
        backend, stripped = resolved
        return self._coerce_ls_result(await backend.als(stripped))

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        if path is not None and path != "/":
            backend, backend_path, route_prefix = _route_for_path(
                default=self._exec_backend,
                sorted_routes=self.sorted_routes,
                path=path,
            )
            if route_prefix is not None:
                grep_result = self._coerce_grep_result(
                    backend.grep(pattern, backend_path, glob)
                )
                if grep_result.error:
                    return grep_result
                return GrepResult(
                    matches=[
                        _remap_grep_path(m, route_prefix)
                        for m in (grep_result.matches or [])
                    ]
                )
            resolved = self._resolve_fs_backend(path)
            if resolved is not None:
                backend, stripped = resolved
                return self._coerce_grep_result(backend.grep(pattern, stripped, glob))
            return GrepResult(error=f"Unsupported path for grep: {path}")

        all_matches = []
        for route_prefix, backend in self.sorted_routes:
            grep_result = self._coerce_grep_result(backend.grep(pattern, "/", glob))
            if grep_result.error:
                return grep_result
            all_matches.extend(
                _remap_grep_path(m, route_prefix) for m in (grep_result.matches or [])
            )
        return GrepResult(matches=all_matches)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        if path is not None and path != "/":
            backend, backend_path, route_prefix = _route_for_path(
                default=self._exec_backend,
                sorted_routes=self.sorted_routes,
                path=path,
            )
            if route_prefix is not None:
                grep_result = self._coerce_grep_result(
                    await backend.agrep(pattern, backend_path, glob)
                )
                if grep_result.error:
                    return grep_result
                return GrepResult(
                    matches=[
                        _remap_grep_path(m, route_prefix)
                        for m in (grep_result.matches or [])
                    ]
                )
            resolved = self._resolve_fs_backend(path)
            if resolved is not None:
                backend, stripped = resolved
                return self._coerce_grep_result(
                    await backend.agrep(pattern, stripped, glob)
                )
            return GrepResult(error=f"Unsupported path for grep: {path}")

        all_matches = []
        for route_prefix, backend in self.sorted_routes:
            grep_result = self._coerce_grep_result(
                await backend.agrep(pattern, "/", glob)
            )
            if grep_result.error:
                return grep_result
            all_matches.extend(
                _remap_grep_path(m, route_prefix) for m in (grep_result.matches or [])
            )
        return GrepResult(matches=all_matches)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        if path is not None:
            backend, backend_path, route_prefix = _route_for_path(
                default=self._exec_backend,
                sorted_routes=self.sorted_routes,
                path=path,
            )
            if route_prefix is not None:
                glob_result = backend.glob(pattern, backend_path)
                matches = (
                    glob_result.matches
                    if isinstance(glob_result, GlobResult)
                    else glob_result
                )
                if isinstance(glob_result, GlobResult) and glob_result.error:
                    return glob_result
                return GlobResult(
                    matches=[
                        _remap_file_info_path(fi, route_prefix)
                        for fi in (matches or [])
                    ]
                )
            resolved = self._resolve_fs_backend(path)
            if resolved is not None:
                backend, stripped = resolved
                glob_result = backend.glob(pattern, stripped)
                if isinstance(glob_result, GlobResult):
                    return glob_result
                return GlobResult(matches=glob_result or [])
            return GlobResult(matches=[], error=f"Unsupported path for glob: {path}")

        results = []
        for route_prefix, backend in self.routes.items():
            route_pattern = _strip_route_from_pattern(pattern, route_prefix)
            sub_result = backend.glob(route_pattern, "/")
            sub_matches = (
                sub_result.matches if isinstance(sub_result, GlobResult) else sub_result
            )
            results.extend(
                _remap_file_info_path(fi, route_prefix) for fi in (sub_matches or [])
            )
        results.sort(key=lambda x: x.get("path", ""))
        return GlobResult(matches=results)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        if path is not None:
            backend, backend_path, route_prefix = _route_for_path(
                default=self._exec_backend,
                sorted_routes=self.sorted_routes,
                path=path,
            )
            if route_prefix is not None:
                glob_result = await backend.aglob(pattern, backend_path)
                matches = (
                    glob_result.matches
                    if isinstance(glob_result, GlobResult)
                    else glob_result
                )
                if isinstance(glob_result, GlobResult) and glob_result.error:
                    return glob_result
                return GlobResult(
                    matches=[
                        _remap_file_info_path(fi, route_prefix)
                        for fi in (matches or [])
                    ]
                )
            resolved = self._resolve_fs_backend(path)
            if resolved is not None:
                backend, stripped = resolved
                glob_result = await backend.aglob(pattern, stripped)
                if isinstance(glob_result, GlobResult):
                    return glob_result
                return GlobResult(matches=glob_result or [])
            return GlobResult(matches=[], error=f"Unsupported path for glob: {path}")

        results = []
        for route_prefix, backend in self.routes.items():
            route_pattern = _strip_route_from_pattern(pattern, route_prefix)
            sub_result = await backend.aglob(route_pattern, "/")
            sub_matches = (
                sub_result.matches if isinstance(sub_result, GlobResult) else sub_result
            )
            results.extend(
                _remap_file_info_path(fi, route_prefix) for fi in (sub_matches or [])
            )
        results.sort(key=lambda x: x.get("path", ""))
        return GlobResult(matches=results)

    def _get_backend_and_key(self, key: str) -> tuple[BackendProtocol, str]:
        backend, stripped_key = super()._get_backend_and_key(key)
        if backend is not self._exec_backend:
            return backend, stripped_key

        # 防御：/skills、/workspace 路径绝不能落到仅支持 execute 的 default
        for route_prefix, route_backend in self.sorted_routes:
            prefix_no_slash = route_prefix.rstrip("/")
            if key == prefix_no_slash:
                return route_backend, "/"
            normalized = route_prefix if route_prefix.endswith("/") else f"{route_prefix}/"
            if key.startswith(normalized):
                suffix = key[len(normalized):]
                backend_path = f"/{suffix}" if suffix else "/"
                logger.warning(
                    f"[SandboxBackend] 路由兜底: {key!r} -> {route_prefix!r} "
                    f"(请确认 routes 已正确注册)"
                )
                return route_backend, backend_path

        if key.startswith(("/skills", "/workspace", "/memories")):
            logger.error(
                f"[SandboxBackend] 文件路径 {key!r} 未匹配任何 route，"
                f"当前 routes={list(self.routes.keys())}"
            )
            return backend, stripped_key

        # 裸虚拟路径（如 /plan.md）默认写入 session 工作区，避免落到 exec default
        ws_backend = self.routes.get("/workspace/")
        if ws_backend is not None and key.startswith("/"):
            backend_path = key if key.startswith("/") else f"/{key}"
            logger.info(
                f"[SandboxBackend] 工作区兜底: {key!r} -> /workspace/ (backend_path={backend_path!r})"
            )
            return ws_backend, backend_path

        return backend, stripped_key
