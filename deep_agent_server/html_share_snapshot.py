"""HTML 分享快照：创建分享时将 HTML 引用的本地图片复制到会话工作区快照目录。

快照使分享链接与技能生命周期解耦：用户卸载 skill / 订阅智能体删除 skill 后，
已生成的分享链接图片仍可加载（快照文件在工作区，不受技能目录变化影响）。

快照目录: workspace/{sid}/.html-share-assets/{token}/
  - index.html        改写引用后的 HTML 快照（引用指向快照内的图片副本）
  - {md5(src)[:16]}.{ext}  图片副本（md5 命名防冲突、防路径穿越）
"""

import hashlib
import json
import mimetypes
import re
import shutil
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple
from urllib.parse import quote

from config import get_logger
from deep_agent_server.html_inline import (
    _extract_asset_refs,
    _extract_local_refs,
    _iter_css_urls,
    _resolve_local_vpath,
    _rewrite_asset_refs,
    _rewrite_css_urls,
    _script_tag_is_module,
)
from deep_agent_server.workspace import HTML_SHARE_SNAPSHOT_DIR, get_workspace_dir
from deep_agent_server.workspace_service import read_file_bytes, resolve_virtual_path

logger = get_logger(__name__)

# 别名（workspace_api 与既有测试从本模块引用）
SNAPSHOT_DIR_NAME = HTML_SHARE_SNAPSHOT_DIR
SNAPSHOT_INDEX_NAME = "index.html"

MAX_SNAPSHOT_ASSETS = 50        # 单次分享快照文件数上限
MAX_SNAPSHOT_BYTES = 30 * 1024 * 1024  # 单次分享快照总字节上限
MAX_ESM_IMPORT_DEPTH = 3        # module 脚本 import 依赖递归深度上限


@dataclass
class SnapshotBuildResult:
    """快照写入结果。

    missing: 构建时被 HTML/CSS/JS 引用、但源文件读不到的资源
             [(源虚拟路径, 文件签名), ...]。签名用于判断之后文件是否出现/变化，
             空串表示构建时文件不存在。
    """
    copied: int
    missing: List[Tuple[str, str]]


def source_signature(
    user_id: str,
    src_vpath: str,
    *,
    agent_id: Optional[str] = None,
) -> str:
    """源文件签名 size:mtime_ns；不存在/不可访问返回空串。"""
    try:
        abs_path, *_ = resolve_virtual_path(user_id, src_vpath, agent_id=agent_id)
        st = abs_path.stat()
    except (ValueError, OSError):
        return ""
    return f"{st.st_size}:{st.st_mtime_ns}"


def normalize_missing_assets(raw: Optional[str]) -> Optional[List[Tuple[str, str]]]:
    """DB 的 snapshot_missing_assets → [(vpath, 签名)]。

    返回 None 表示「状态未知」（旧数据未记录 / 字段损坏），调用方应重建一次。
    """
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    out: List[Tuple[str, str]] = []
    for item in data:
        if isinstance(item, (list, tuple)) and item:
            vpath = str(item[0] or "")
            sig = str(item[1]) if len(item) > 1 and item[1] is not None else ""
            if vpath:
                out.append((vpath, sig))
    return out


def missing_assets_changed(
    user_id: str,
    missing: Optional[List[Tuple[str, str]]],
    *,
    agent_id: Optional[str] = None,
) -> bool:
    """构建时缺失的依赖是否已出现/变化 —— 需要重建快照。

    仅当文件签名与构建时记录不同（含「当时不存在、现在存在」）才返回 True，
    避免文件持续缺失时每次访问都重建。
    """
    if not missing:
        return False
    for vpath, recorded_sig in missing:
        if source_signature(user_id, vpath, agent_id=agent_id) != recorded_sig:
            return True
    return False


# ESM 静态 import/export from 与动态 import() 的相对路径提取
_ESM_IMPORT_RE = re.compile(
    r"""(?:import|export)\s+(?:[^'";]*?\s+from\s+)?['"]([^'"]+)['"]"""
    r"""|import\(\s*['"]([^'"]+)['"]\s*\)""",
    re.MULTILINE,
)


def snapshot_root_vpath(session_id: str, token: str) -> str:
    """快照目录虚拟路径：workspace/{sid}/.html-share-assets/{token}"""
    return f"workspace/{session_id}/{SNAPSHOT_DIR_NAME}/{token}"


def _asset_dst_name(src_vpath: str) -> str:
    """快照文件命名：md5(原虚拟路径)[:16] + 原扩展名。"""
    digest = hashlib.md5(src_vpath.encode("utf-8")).hexdigest()[:16]
    name = src_vpath.rsplit("/", 1)[-1]
    ext = ""
    if "." in name:
        candidate = name.rsplit(".", 1)[-1].lower()
        if candidate and len(candidate) <= 6:
            ext = "." + candidate
    if not ext:
        guessed = mimetypes.guess_type(src_vpath)[0]
        ext = mimetypes.guess_extension(guessed or "") or ""
    return f"{digest}{ext}"


def build_html_share_snapshot(
    html_text: str,
    dir_vpath: str,
    session_id: str,
    snapshot_root: str,
    read_source: Callable[[str], Optional[bytes]],
) -> Tuple[str, List[Tuple[str, str, bytes]], List[str]]:
    """构建分享快照：解析图片引用 → 生成改写后 HTML 与待复制文件清单。

    Args:
        html_text: 原始 HTML 内容
        dir_vpath: HTML 所在虚拟目录（相对引用基准）
        session_id: 会话 ID
        snapshot_root: 快照目录虚拟路径（snapshot_root_vpath 的返回值）
        read_source: 读取源资源字节（不存在/越界返回 None），调用方注入

    Returns:
        (改写后 HTML, [(src_vpath, dst_vpath, bytes), ...], [缺失源虚拟路径, ...])
        改写后 HTML 中图片引用指向快照绝对虚拟路径（/workspace/{sid}/.html-share-assets/{token}/...）。
        超限/读取失败的引用保留原样（对应图片在分享页 404，其余不受影响）。
        缺失列表只包含「源文件读不到」的引用（不含超限跳过），
        供调用方记录状态：文件之后出现时重建快照。
    """
    src_vpaths = _extract_asset_refs(html_text, dir_vpath, session_id)
    mapping: dict = {}
    copies: List[Tuple[str, str, bytes]] = []
    total_bytes = 0
    missing_vpaths: List[str] = []
    missing_seen: set = set()

    def mark_missing(src: str) -> None:
        """记录源文件读取失败的引用（去重保序）。"""
        if src and src not in missing_seen:
            missing_seen.add(src)
            missing_vpaths.append(src)

    def add_copy(src: str, data: bytes) -> Optional[str]:
        """登记一个快照副本，返回快照绝对虚拟路径（超限返回 None）。"""
        nonlocal total_bytes
        if src in mapping:
            return mapping[src]
        if len(copies) >= MAX_SNAPSHOT_ASSETS:
            logger.warning(
                f"[HtmlShareSnapshot] 快照文件数超限 ({MAX_SNAPSHOT_ASSETS})，跳过: {src}"
            )
            return None
        if total_bytes + len(data) > MAX_SNAPSHOT_BYTES:
            logger.warning(
                f"[HtmlShareSnapshot] 快照字节数超限 ({MAX_SNAPSHOT_BYTES})，跳过: {src}"
            )
            return None
        dst = f"{snapshot_root}/{_asset_dst_name(src)}"
        mapping[src] = dst
        copies.append((src, dst, data))
        total_bytes += len(data)
        return dst

    # 1. HTML 级图片资源
    for src in src_vpaths:
        data = read_source(src)
        if data is not None:
            add_copy(src, data)
        else:
            mark_missing(src)

    # 2. 外部 CSS：内部 url() 图片一并快照，CSS 自身也复制为快照（url() 改写为快照绝对路径）
    css_mapping: dict = {}
    for tag_type, rel_path in _extract_local_refs(html_text):
        if tag_type != "stylesheet":
            continue
        src_css = _resolve_local_vpath(dir_vpath, session_id, rel_path)
        if not src_css:
            continue
        css_bytes = read_source(src_css)
        if css_bytes is None:
            mark_missing(src_css)
            continue
        css_dir = src_css[: src_css.rfind("/") + 1]

        def css_resolve(raw: str) -> Optional[str]:
            vpath = _resolve_local_vpath(css_dir, session_id, raw)
            if vpath is None:
                return None
            data = read_source(vpath)
            if data is None:
                mark_missing(vpath)
                return None
            dst = add_copy(vpath, data)
            return "/" + dst if dst else None

        # 复制 CSS 内的图片引用
        for content in _iter_css_urls(css_bytes.decode("utf-8", errors="replace")):
            css_resolve(content)

        # CSS 自身快照（内容中 url() 指向快照图片）
        dst_css = add_copy(
            src_css,
            _rewrite_css_urls(
                css_bytes.decode("utf-8", errors="replace"),
                css_resolve,
            ).encode("utf-8"),
        )
        if dst_css:
            # key 用 HTML 中的原始引用（rel_path），供后续 link href 改写匹配
            css_mapping[rel_path] = dst_css

    # 3. JS：复制到快照 + 改写 script src（与 CSS 同模式）
    token = snapshot_root.rsplit("/", 1)[-1]

    def _rewrite_module_tree(
        js_vpath: str,
        js_text: str,
        depth: int,
        rewritten: dict,
    ) -> str:
        """递归处理 module 脚本的静态 import 依赖：复制到快照 + 改写 import 路径。

        改写为相对 asset URL（{token}/asset?path=...，基于页面 URL 解析，
        兼容任意部署前缀）。外部（http/https/data/node:/blob:）与无法解析的保留原样。
        """
        if depth > MAX_ESM_IMPORT_DEPTH:
            return js_text
        js_dir = js_vpath[: js_vpath.rfind("/") + 1]

        def dep_resolve(raw: str) -> Optional[str]:
            if raw.startswith(("/", "http:", "https:", "data:", "node:", "blob:")):
                return None
            vpath = _resolve_local_vpath(js_dir, session_id, raw)
            if vpath is None:
                return None
            data = read_source(vpath)
            if data is None:
                mark_missing(vpath)
                return None
            if add_copy(vpath, data) is None:
                return None
            if vpath not in rewritten:
                rewritten[vpath] = _rewrite_module_tree(
                    vpath, data.decode("utf-8", errors="replace"), depth + 1, rewritten
                )
            return vpath

        def repl(m: re.Match) -> str:
            raw = m.group(1) or m.group(2)
            if not raw:
                return m.group(0)
            vpath = dep_resolve(raw)
            if vpath is None:
                return m.group(0)
            return m.group(0).replace(raw, f"{token}/asset?path={quote(vpath)}")

        return _ESM_IMPORT_RE.sub(repl, js_text)

    script_mapping: dict = {}
    for tag_type, rel_path in _extract_local_refs(html_text):
        if tag_type != "script":
            continue
        src_js = _resolve_local_vpath(dir_vpath, session_id, rel_path)
        if not src_js:
            continue
        js_bytes = read_source(src_js)
        if js_bytes is None:
            mark_missing(src_js)
            continue
        dst_js = add_copy(src_js, js_bytes)
        if dst_js:
            script_mapping[rel_path] = dst_js
            # module 脚本：递归处理静态 import 依赖（复制 + 改写为 asset 相对 URL）
            if _script_tag_is_module(html_text, rel_path):
                rewritten: dict = {}
                new_content = _rewrite_module_tree(
                    src_js, js_bytes.decode("utf-8", errors="replace"), 0, rewritten
                )
                for vp, content in rewritten.items():
                    for idx, (s, d, _b) in enumerate(copies):
                        if s == vp:
                            copies[idx] = (s, d, content.encode("utf-8"))
                            break
                if new_content != js_bytes.decode("utf-8", errors="replace"):
                    for idx, (s, d, _b) in enumerate(copies):
                        if s == src_js:
                            copies[idx] = (s, d, new_content.encode("utf-8"))
                            break

    # 4. 改写 HTML：图片/图标/style 引用 + stylesheet link + script src → 快照绝对虚拟路径
    def resolve(raw: str) -> Optional[str]:
        vpath = _resolve_local_vpath(dir_vpath, session_id, raw)
        if vpath is None:
            return None
        dst = mapping.get(vpath)
        return "/" + dst if dst else None

    new_html = _rewrite_asset_refs(html_text, resolve)

    # stylesheet link href → 快照 CSS 路径（快照 CSS 内 url() 已指向快照图片）
    for rel_path, dst_css in css_mapping.items():
        new_html = re.sub(
            r'(\bhref\s*=\s*["\'])' + re.escape(rel_path) + r'(["\'])',
            lambda _m: _m.group(1) + "/" + dst_css + _m.group(2),
            new_html,
            count=1,
            flags=re.IGNORECASE,
        )

    # script src → 快照 JS 路径（快照 JS 原样内容，view 时从快照内联/引用）
    for rel_path, dst_js in script_mapping.items():
        new_html = re.sub(
            r'(\bsrc\s*=\s*["\'])' + re.escape(rel_path) + r'(["\'])',
            lambda _m: _m.group(1) + "/" + dst_js + _m.group(2),
            new_html,
            count=1,
            flags=re.IGNORECASE,
        )

    return new_html, copies, missing_vpaths


async def write_share_snapshot(
    *,
    user_id: str,
    html_text: str,
    vpath: str,
    session_id: str,
    token: str,
    agent_id: Optional[str] = None,
) -> SnapshotBuildResult:
    """构建并写入分享快照（先清旧快照再写）。

    分享创建（前端手动 / Agent 写入后自动）与访问时懒重建共用此入口，
    保证快照内容与生命周期管理只有一份实现。

    Returns:
        SnapshotBuildResult(copied=复制的资源数, missing=构建时读不到的源文件签名)

    Args:
        html_text: 源 HTML 当前内容
        vpath: 源文件虚拟路径（资源相对引用的解析基准）
        token: 分享 token（决定快照目录 workspace/{sid}/.html-share-assets/{token}/）
        agent_id: 订阅智能体会话上下文（skills 资源解析用）
    """
    dir_vpath = vpath[: vpath.rfind("/") + 1]
    snapshot_root = snapshot_root_vpath(session_id, token)

    def read_source(src_vpath: str) -> Optional[bytes]:
        try:
            data, _ = read_file_bytes(user_id, src_vpath, agent_id=agent_id)
            return data
        except (FileNotFoundError, ValueError):
            logger.warning(f"[HtmlShareSnapshot] 快照源读取失败 src={src_vpath}")
            return None

    new_html, copies, missing_vpaths = build_html_share_snapshot(
        html_text=html_text,
        dir_vpath=dir_vpath,
        session_id=session_id,
        snapshot_root=snapshot_root,
        read_source=read_source,
    )

    # 写盘：先清旧快照（token 复用/内容更新时重建），再写入新快照
    snap_dir = get_workspace_dir(user_id, session_id) / SNAPSHOT_DIR_NAME / token
    if snap_dir.exists():
        shutil.rmtree(snap_dir, ignore_errors=True)
    snap_dir.mkdir(parents=True, exist_ok=True)

    for _, dst_vpath, data in copies:
        dst_abs, *_ = resolve_virtual_path(user_id, dst_vpath)
        dst_abs.parent.mkdir(parents=True, exist_ok=True)
        dst_abs.write_bytes(data)

    index_abs, *_ = resolve_virtual_path(
        user_id, f"{snapshot_root}/{SNAPSHOT_INDEX_NAME}"
    )
    index_abs.write_text(new_html, encoding="utf-8")

    missing = [
        (vp, source_signature(user_id, vp, agent_id=agent_id))
        for vp in missing_vpaths
    ]
    logger.info(
        f"[HtmlShareSnapshot] 快照写入完成 token={token} path={vpath} "
        f"copied={len(copies)} missing={len(missing)}"
    )
    return SnapshotBuildResult(copied=len(copies), missing=missing)
