"""Docker 沙箱 Backend — 实现 execute via docker exec。

deepagents 要求 CompositeBackend.default 实现 SandboxBackendProtocol，
否则 execute 工具会报 Execution not available。

文件工具（read_file 等）必须走 routes 中的 /skills/、/workspace/；
若误落到 default（_DockerExecBackend），会 NotImplementedError。

容器命令以宿主机用户 UID:GID 执行，避免 root 创建的文件在 bind mount
宿主机目录上产生 Permission denied（write_file 在宿主机以普通用户运行）。
"""

import os
from pathlib import Path
from typing import Any, Optional

# 宿主机进程 UID/GID，用于 docker exec 时以相同身份执行命令，
# 确保 bind mount 目录中创建的文件 owner 与 FilesystemBackend 一致。
_HOST_EXEC_UID = os.getuid()
_HOST_EXEC_GID = os.getgid()

# 宿主机/容器常见系统目录前缀。虚拟文件系统只提供 /workspace/、/skills/ 等
# 路由；对这些系统目录的文件操作一律拒绝并引导模型改用虚拟路径，避免模型
# 误以为在操作宿主机文件系统（如平台 skill 真实路径 /mnt/skills）。
_HOST_SYSTEM_DIRS = (
    "/etc", "/mnt", "/home", "/usr", "/var", "/tmp", "/root", "/opt",
    "/bin", "/sbin", "/lib", "/lib64", "/dev", "/proc", "/sys", "/boot",
    "/srv", "/run", "/data", "/app",
)


def _is_host_system_path(path: str) -> bool:
    """判断绝对路径是否为宿主机/容器系统目录（按路径段精确匹配）。"""
    if not path.startswith("/"):
        return False
    for prefix in _HOST_SYSTEM_DIRS:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False

from deepagents.backends.composite import (
    CompositeBackend,
    _remap_file_info_path,
    _remap_grep_path,
    _route_for_path,
    _strip_route_from_pattern,
)
from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    ExecuteResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
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
        try:
            c = client.containers.get(self.container_id)
        except Exception as e:
            return f"沙箱容器不可用: {e}", 1
        exec_env = build_exec_user_env(self.user_id, self.user_token)
        prefix = shell_env_prefix(exec_env)
        kwargs: dict = {"workdir": "/workspace", "demux": False}
        if exec_env:
            kwargs["environment"] = exec_env
        cmd = prefix + command if prefix else command
        try:
            code, out = c.exec_run(
                ["bash", "-lc", cmd],
                user=f"{_HOST_EXEC_UID}:{_HOST_EXEC_GID}",
                **kwargs,
            )
        except Exception as e:
            # 容器可能已被 TTL 清理或异常退出，返回友好错误让模型感知
            msg = str(e) or repr(e)
            if "not running" in msg:
                return "沙箱容器已停止（可能因空闲超时被回收），请重试或开启新会话", 1
            return f"沙箱执行失败: {msg}", 1
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
        agent_skills_root: Optional[Path] = None,
        agent_id: Optional[str] = None,
        selected_skill_ids: Optional[list[str]] = None,
    ):
        self.user_id = user_id
        self.container_id = container_id
        self.session_id = session_id
        self.agent_id = agent_id
        self._exec_backend = _DockerExecBackend(user_id, container_id, user_token=user_token)
        routes = build_filesystem_routes(
            user_id, session_id, store=store, skills_root=skills_root,
            agent_skills_root=agent_skills_root,
            selected_skill_ids=selected_skill_ids,
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

    def _fs_unsupported_error(self, op: str, path: str) -> str:
        """路径落到仅支持 execute 的 default 时的友好错误。

        返回错误文本而非抛 NotImplementedError —— 工具层会把 result.error
        作为 ToolMessage 返回给 LLM，让模型理解并改用 /workspace/ 等虚拟路径
        继续执行，而不是整个 run 冒泡失败。
        """
        return (
            f"文件操作 {op} 目标 {path!r} 落到了不支持读写的 backend"
            "（沙箱 default 仅支持 execute 命令）。请改用虚拟路径 "
            "/workspace/... 或 /skills/... 进行文件读写，不要直接操作容器根路径。"
        )

    def _is_virtual_route_path(self, path: str) -> bool:
        """判断路径是否属于虚拟路由（/workspace/、/skills/、/uploads/ 等）。

        非虚拟路由的绝对路径（如 /mnt/skills、/etc、/home/...）是宿主机风格
        路径，在沙箱虚拟文件系统中不存在 —— 需要向模型提示二者区别。
        """
        for route_prefix, _backend in self.sorted_routes:
            prefix = route_prefix.rstrip("/")
            if path == prefix or path.startswith(prefix + "/"):
                return True
        return False

    def _append_fs_guidance(self, path: str, error: str) -> str:
        """宿主机风格路径的错误追加虚拟文件系统引导提示。

        让模型意识到沙箱内是虚拟文件系统（只有 /workspace/、/skills/ 等虚拟
        路由），宿主机路径（/mnt/...、/etc/... 等）不存在，应改用虚拟路径。
        虚拟路由内的错误（如文件确实不存在）原样返回，不追加。
        """
        if self._is_virtual_route_path(path):
            return error
        return (
            f"{error}（沙箱内为虚拟文件系统：宿主机路径 {path!r} 不存在、不可访问。"
            "文件操作请使用 /workspace/... 或 /skills/... 等虚拟路径，"
            "不要操作 /mnt、/etc、/home 等宿主机目录）"
        )

    def _fs_error_with_guidance(self, op: str, path: str) -> str:
        """路径无法解析时的错误：组合不支持提示 + 虚拟文件系统引导。

        宿主机系统目录（/etc、/mnt 等）路径会命中 _fs_unsupported_error，
        再叠加 _append_fs_guidance 让模型意识到虚拟 FS 与宿主机 FS 的区别。
        """
        return self._append_fs_guidance(path, self._fs_unsupported_error(op, path))

    def read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        resolved = self._resolve_fs_backend(file_path)
        if resolved is None:
            return ReadResult(error=self._fs_error_with_guidance("read", file_path))
        backend, stripped = resolved
        result = backend.read(stripped, offset=offset, limit=limit)
        if result.error:
            result.error = self._append_fs_guidance(file_path, result.error)
        return result

    async def aread(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        resolved = self._resolve_fs_backend(file_path)
        if resolved is None:
            return ReadResult(error=self._fs_error_with_guidance("read", file_path))
        backend, stripped = resolved
        result = await backend.aread(stripped, offset=offset, limit=limit)
        if result.error:
            result.error = self._append_fs_guidance(file_path, result.error)
        return result

    def write(self, file_path: str, content: str) -> WriteResult:
        resolved = self._resolve_fs_backend(file_path)
        if resolved is None:
            return WriteResult(error=self._fs_error_with_guidance("write", file_path))
        backend, stripped = resolved
        result = backend.write(stripped, content)
        if result.error:
            result.error = self._append_fs_guidance(file_path, result.error)
        return result

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        resolved = self._resolve_fs_backend(file_path)
        if resolved is None:
            return WriteResult(error=self._fs_error_with_guidance("write", file_path))
        backend, stripped = resolved
        result = await backend.awrite(stripped, content)
        if result.error:
            result.error = self._append_fs_guidance(file_path, result.error)
        return result

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        resolved = self._resolve_fs_backend(file_path)
        if resolved is None:
            return EditResult(error=self._fs_error_with_guidance("edit", file_path))
        backend, stripped = resolved
        result = backend.edit(stripped, old_string, new_string, replace_all=replace_all)
        if result.error:
            result.error = self._append_fs_guidance(file_path, result.error)
        return result

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        resolved = self._resolve_fs_backend(file_path)
        if resolved is None:
            return EditResult(error=self._fs_error_with_guidance("edit", file_path))
        backend, stripped = resolved
        result = await backend.aedit(stripped, old_string, new_string, replace_all=replace_all)
        if result.error:
            result.error = self._append_fs_guidance(file_path, result.error)
        return result

    def _ls_with_remap(
        self,
        backend: BackendProtocol,
        backend_path: str,
        route_prefix: str | None,
    ) -> LsResult:
        """执行 ls 并做路径前缀重映射。

        与 grep/glob 系列一致，不调用 super().ls()：CompositeBackend.ls 对非路由
        路径会直接打到 default（exec backend）抛 NotImplementedError，而本项目
        _get_backend_and_key 有工作区兜底，两者路由结果不一致。
        """
        ls_result = self._coerce_ls_result(backend.ls(backend_path))
        if ls_result.error:
            return ls_result
        if route_prefix is not None:
            # SkillsMiddleware 依赖重映射后的完整路径（如 /skills/builtin/{id}/SKILL.md）
            # 来构建 download_files 参数
            return LsResult(
                entries=[_remap_file_info_path(fi, route_prefix) for fi in (ls_result.entries or [])]
            )
        return ls_result

    def _ls_result_with_guidance(self, result: LsResult, path: str) -> LsResult:
        """ls/als 出口：对宿主机风格路径的错误追加虚拟文件系统引导。"""
        if result.error:
            result.error = self._append_fs_guidance(path, result.error)
        return result

    def ls(self, path: str) -> LsResult:
        if path == "/":
            return self._list_virtual_roots()
        backend, backend_path, route_prefix = _route_for_path(
            default=self._exec_backend,
            sorted_routes=self.sorted_routes,
            path=path,
        )
        if route_prefix is not None:
            return self._ls_result_with_guidance(
                self._ls_with_remap(backend, backend_path, route_prefix), path
            )
        resolved = self._resolve_fs_backend(path)
        if resolved is None:
            return self._ls_result_with_guidance(
                LsResult(entries=[], error=f"Unsupported path for ls: {path}"), path
            )
        fs_backend, stripped = resolved
        return self._ls_result_with_guidance(
            self._ls_with_remap(fs_backend, stripped, None), path
        )

    async def als(self, path: str) -> LsResult:
        if path == "/":
            return self._list_virtual_roots()
        backend, backend_path, route_prefix = _route_for_path(
            default=self._exec_backend,
            sorted_routes=self.sorted_routes,
            path=path,
        )
        if route_prefix is not None:
            ls_result = self._coerce_ls_result(await backend.als(backend_path))
            if ls_result.error:
                return self._ls_result_with_guidance(ls_result, path)
            return LsResult(
                entries=[_remap_file_info_path(fi, route_prefix) for fi in (ls_result.entries or [])]
            )
        resolved = self._resolve_fs_backend(path)
        if resolved is None:
            return self._ls_result_with_guidance(
                LsResult(entries=[], error=f"Unsupported path for ls: {path}"), path
            )
        fs_backend, stripped = resolved
        return self._ls_result_with_guidance(
            self._coerce_ls_result(await fs_backend.als(stripped)), path
        )

    def _grep_result_with_guidance(self, result: GrepResult, path: str) -> GrepResult:
        """grep 出口：对宿主机风格路径的错误追加虚拟文件系统引导。"""
        if result.error:
            result.error = self._append_fs_guidance(path, result.error)
        return result

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
                    return self._grep_result_with_guidance(grep_result, path)
                return GrepResult(
                    matches=[
                        _remap_grep_path(m, route_prefix)
                        for m in (grep_result.matches or [])
                    ]
                )
            resolved = self._resolve_fs_backend(path)
            if resolved is not None:
                backend, stripped = resolved
                return self._grep_result_with_guidance(
                    self._coerce_grep_result(backend.grep(pattern, stripped, glob)), path
                )
            return self._grep_result_with_guidance(
                GrepResult(error=f"Unsupported path for grep: {path}"), path
            )

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
                    return self._grep_result_with_guidance(grep_result, path)
                return GrepResult(
                    matches=[
                        _remap_grep_path(m, route_prefix)
                        for m in (grep_result.matches or [])
                    ]
                )
            resolved = self._resolve_fs_backend(path)
            if resolved is not None:
                backend, stripped = resolved
                return self._grep_result_with_guidance(
                    self._coerce_grep_result(
                        await backend.agrep(pattern, stripped, glob)
                    ),
                    path,
                )
            return self._grep_result_with_guidance(
                GrepResult(error=f"Unsupported path for grep: {path}"), path
            )

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

    def _glob_result_with_guidance(self, result: GlobResult, path: str) -> GlobResult:
        """glob 出口：对宿主机风格路径的错误追加虚拟文件系统引导。"""
        if result.error:
            result.error = self._append_fs_guidance(path, result.error)
        return result

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
                    return self._glob_result_with_guidance(glob_result, path)
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
                    return self._glob_result_with_guidance(glob_result, path)
                return GlobResult(matches=glob_result or [])
            return self._glob_result_with_guidance(
                GlobResult(matches=[], error=f"Unsupported path for glob: {path}"), path
            )

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
                    return self._glob_result_with_guidance(glob_result, path)
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
                    return self._glob_result_with_guidance(glob_result, path)
                return GlobResult(matches=glob_result or [])
            return self._glob_result_with_guidance(
                GlobResult(matches=[], error=f"Unsupported path for glob: {path}"), path
            )

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

        # 宿主机/容器系统目录前缀：不兜底到工作区（否则模型会误以为写到了宿主机
        # /etc、/mnt 等真实目录），保持落到 exec backend，由 read/write 等覆盖层
        # 返回"不支持 + 请用虚拟路径"的引导错误
        if _is_host_system_path(key):
            logger.info(
                f"[SandboxBackend] 系统目录路径 {key!r} 不兜底到工作区，"
                "由上层返回引导错误"
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
