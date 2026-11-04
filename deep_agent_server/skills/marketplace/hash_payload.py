"""content_sha256 = hash_skill_payload(root)：仅哈希 Skill 业务文件。"""

from __future__ import annotations

import hashlib
import unicodedata
from pathlib import Path
from typing import Iterable, List, Tuple

EXCLUDED_PREFIXES = ("_meta/",)
EXCLUDED_NAMES = {".install-meta.json"}
EXCLUDED_DIR_SUFFIXES = (".installing", ".previous")


def _normalize_rel(path: str) -> str:
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return unicodedata.normalize("NFC", p)


def _should_exclude(rel: str) -> bool:
    if not rel or rel.endswith("/"):
        return True
    name = rel.rsplit("/", 1)[-1]
    if name in EXCLUDED_NAMES:
        return True
    if any(rel == p.rstrip("/") or rel.startswith(p) for p in EXCLUDED_PREFIXES):
        return True
    parts = rel.split("/")
    for part in parts[:-1]:
        if part.endswith(EXCLUDED_DIR_SUFFIXES) or part in (".installing", ".previous"):
            return True
        if part.endswith(".installing") or part.endswith(".previous"):
            return True
    return False


def iter_skill_files(root: Path) -> List[Tuple[str, Path]]:
    root = root.resolve()
    out: List[Tuple[str, Path]] = []
    if not root.is_dir():
        return out
    for fp in root.rglob("*"):
        if not fp.is_file():
            continue
        rel = _normalize_rel(str(fp.relative_to(root)))
        if _should_exclude(rel):
            continue
        out.append((rel, fp))
    out.sort(key=lambda x: x[0].encode("utf-8"))
    return out


def build_manifest_lines(root: Path) -> List[str]:
    lines: List[str] = []
    for rel, fp in iter_skill_files(root):
        data = fp.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        lines.append(f"{rel}\t{len(data)}\t{digest}\n")
    return lines


def hash_skill_payload(root: Path) -> str:
    lines = build_manifest_lines(root)
    blob = "".join(lines).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def write_meta(root: Path, content_sha256: str) -> None:
    meta = root / "_meta"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "content_sha256").write_text(content_sha256 + "\n", encoding="utf-8")
    import json

    entries = []
    for line in build_manifest_lines(root):
        path, size, digest = line.rstrip("\n").split("\t")
        entries.append({"path": path, "size": int(size), "sha256": digest})
    (meta / "manifest.json").write_text(
        json.dumps({"content_sha256": content_sha256, "files": entries}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
