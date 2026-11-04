"""L0 ZIP 安全解包（P1a 验收数字）。"""

from __future__ import annotations

import io
import os
import re
import shutil
import unicodedata
import zipfile
from pathlib import Path
from typing import Set

from config.config import app_config

ALLOWED_EXTENSIONS = {
    ".md", ".txt", ".json", ".yaml", ".yml", ".py", ".js", ".mjs", ".cjs",
    ".ts", ".css", ".html", ".xml", ".csv", ".svg", ".png", ".jpg", ".jpeg",
    ".gif", ".woff2", ".sh",
}

NESTED_ARCHIVE_EXT = {".zip", ".tar", ".tgz", ".gz", ".7z", ".rar"}

WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

SKIP_NAMES = {
    "__MACOSX",
    ".DS_Store",
    "Thumbs.db",
    # 版本控制 / 编辑器 / 打包工具的元文件：技能包里常见但对功能无作用，
    # 解包时静默跳过，避免因无扩展名命中白名单校验而拒绝整包。
    ".gitignore",
    ".gitattributes",
    ".editorconfig",
    ".skillignore",
    ".npmignore",
    ".dockerignore",
    ".prettierignore",
    ".eslintignore",
    ".stylelintignore",
}

CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


class UnpackError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _unwrap_single_skill_root(dest_dir: Path) -> None:
    """Windows 压缩整个文件夹时，ZIP 根是 folder/SKILL.md 而不是 SKILL.md。

    仅当解包根没有 SKILL.md、且恰好一个子目录内有 SKILL.md 时上提一层。
    """
    if (dest_dir / "SKILL.md").is_file():
        return
    children = [p for p in dest_dir.iterdir() if p.name not in SKIP_NAMES]
    dirs = [p for p in children if p.is_dir()]
    files = [p for p in children if p.is_file()]
    if files or len(dirs) != 1:
        return
    inner = dirs[0]
    if not (inner / "SKILL.md").is_file():
        return
    for item in list(inner.iterdir()):
        target = dest_dir / item.name
        if target.exists():
            raise UnpackError(f"剥壳冲突: {item.name}")
        item.rename(target)
    try:
        inner.rmdir()
    except OSError:
        shutil.rmtree(inner, ignore_errors=True)


def _normalize_entry(name: str) -> str:
    name = name.replace("\\", "/")
    name = unicodedata.normalize("NFC", name)
    while name.startswith("./"):
        name = name[2:]
    return name.lstrip("/")


def _is_dangerous_path(rel: str):
    if not rel:
        return "空路径"
    if CTRL_RE.search(rel):
        return "路径含控制字符"
    if rel.startswith("/") or rel.startswith("\\"):
        return "绝对路径"
    # Windows 盘符 / UNC
    if re.match(r"^[A-Za-z]:", rel) or rel.startswith("//") or rel.startswith("\\\\"):
        return "盘符或 UNC 路径"
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if ".." in parts:
        return "路径穿越"
    for p in parts:
        if p.endswith(" ") or p.endswith("."):
            return "段尾空格或点号"
        base = p.split(".")[0].upper() if "." in p else p.upper()
        if p.upper() in WIN_RESERVED or base in WIN_RESERVED:
            return f"Windows 保留名: {p}"
    if parts and parts[0] == "_meta":
        return "禁止用户包自带 _meta/"
    if ".git" in parts or "node_modules" in parts:
        return "禁止 .git/ 或 node_modules/"
    if len(parts) > 8:
        return "目录深度超过 8"
    return None


def _should_skip(rel: str) -> bool:
    parts = rel.split("/")
    if any(p in SKIP_NAMES for p in parts):
        return True
    name = parts[-1] if parts else ""
    if name in SKIP_NAMES:
        return True
    return False


def safe_unpack_zip(zip_path: Path, dest_dir: Path) -> None:
    """解包到 dest_dir；失败时删除 dest_dir。"""
    max_zip = int(getattr(app_config, "SKILL_UPLOAD_MAX_BYTES", 52428800))
    max_unpacked = int(getattr(app_config, "SKILL_UPLOAD_MAX_UNPACKED_BYTES", 209715200))
    max_ratio = float(getattr(app_config, "SKILL_UPLOAD_MAX_COMPRESSION_RATIO", 100))
    max_files = int(getattr(app_config, "SKILL_UPLOAD_MAX_FILES", 500))
    max_file = int(getattr(app_config, "SKILL_UPLOAD_MAX_FILE_BYTES", 33554432))

    if dest_dir.exists():
        shutil.rmtree(dest_dir, ignore_errors=True)
    dest_dir.mkdir(parents=True, exist_ok=True)

    zip_size = zip_path.stat().st_size
    if zip_size > max_zip:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise UnpackError(f"ZIP 超过 {max_zip // 1024 // 1024} MiB")

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            if zf.testzip() is not None:
                raise UnpackError("ZIP 损坏")
            # 加密检测
            for info in zf.infolist():
                if info.flag_bits & 0x1:
                    raise UnpackError("拒绝加密 ZIP")

            seen_casefold: Set[str] = set()
            written = 0
            entry_count = 0

            for info in zf.infolist():
                rel = _normalize_entry(info.filename)
                if not rel or rel.endswith("/"):
                    continue
                if _should_skip(rel):
                    continue
                entry_count += 1
                if entry_count > max_files:
                    raise UnpackError(f"条目数超过 {max_files}")

                reason = _is_dangerous_path(rel)
                if reason:
                    raise UnpackError(reason)

                lower = rel.lower()
                # .tar.gz
                if lower.endswith(".tar.gz") or lower.endswith(".tgz"):
                    raise UnpackError(f"拒绝嵌套归档: {rel}")
                ext = Path(rel).suffix.lower()
                if ext in NESTED_ARCHIVE_EXT:
                    raise UnpackError(f"拒绝嵌套归档: {rel}")
                if not ext or ext not in ALLOWED_EXTENSIONS:
                    raise UnpackError(f"未允许的文件类型: {rel}")

                key = unicodedata.normalize("NFC", rel).casefold()
                if key in seen_casefold:
                    raise UnpackError(f"路径冲突（大小写/NFC）: {rel}")
                seen_casefold.add(key)

                target = (dest_dir / rel).resolve()
                try:
                    target.relative_to(dest_dir.resolve())
                except ValueError as e:
                    raise UnpackError("路径穿越") from e

                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info, "r") as src, open(target, "wb") as dst:
                    while True:
                        chunk = src.read(1024 * 64)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > max_unpacked:
                            raise UnpackError("解压体积超过 200 MiB")
                        if written > max_zip * max_ratio:
                            raise UnpackError("压缩比超过 100，疑似 ZIP 炸弹")
                        if target.stat().st_size + len(chunk) > max_file if target.exists() else len(chunk) > max_file:
                            # track per-file
                            pass
                        dst.write(chunk)
                        if dst.tell() > max_file:
                            raise UnpackError(f"单文件超过 32 MiB: {rel}")

                try:
                    os.chmod(target, 0o644)
                except OSError:
                    pass

        _unwrap_single_skill_root(dest_dir)

        # 目录权限
        for d in dest_dir.rglob("*"):
            if d.is_dir():
                try:
                    os.chmod(d, 0o755)
                except OSError:
                    pass
    except UnpackError:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise
    except zipfile.BadZipFile as e:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise UnpackError("无效 ZIP") from e
    except Exception as e:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise UnpackError(f"解包失败: {e}") from e
