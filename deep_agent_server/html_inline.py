"""HTML 依赖内联：将同目录的 CSS/JS 嵌入 HTML，实现单文件可独立渲染。

对齐前端 deepApi.js 中的 inlineHtmlReferences() 逻辑。
"""

import re
from typing import List, Tuple

from config import get_logger
from deep_agent_server.workspace_service import resolve_virtual_path

logger = get_logger(__name__)

MAX_INLINE_FILES = 20
MAX_INLINE_FILE_BYTES = 500 * 1024  # 单个依赖最大 500KB

# 外部 URL 特征（无需内联）
_EXTERNAL_URL_RE = re.compile(
    r'^(?:https?:|data:|mailto:|tel:|ftp:|file:|blob:|#|//)',
    re.IGNORECASE,
)


def _is_external_url(url: str) -> bool:
    """判断 URL 是否为外部引用（不需要内联）。"""
    if not url:
        return True
    return bool(_EXTERNAL_URL_RE.match(url.strip()))


def _resolve_relative_path(base_dir: str, rel: str) -> str:
    """基于 HTML 所在目录解析相对路径。

    例: _resolve_relative_path('workspace/sess123/sub/', '../css/style.css')
        → 'workspace/sess123/css/style.css'
    """
    if not base_dir.endswith('/'):
        base_dir += '/'

    # 绝对路径（相对于工作区根）
    if rel.startswith('/'):
        parts = base_dir.split('/')
        root = parts[0] if len(parts) > 1 and parts[0] else ''
        return root + '/' + rel.lstrip('/')

    combined = base_dir + rel
    parts = [p for p in combined.split('/') if p and p != '.']
    resolved = []
    for part in parts:
        if part == '..':
            if resolved:
                resolved.pop()
        else:
            resolved.append(part)
    return '/'.join(resolved)


def _extract_local_refs(html_text: str) -> List[Tuple[str, str]]:
    """从 HTML 中提取本地引用的 JS/CSS 文件。

    Returns:
        [(tag_type, relative_path), ...]  tag_type: 'script' | 'stylesheet'
    """
    refs = []

    # <script src="...">
    for m in re.finditer(r'<script\b[^>]*\bsrc\s*=\s*["\']([^"\']+)["\']', html_text, re.IGNORECASE):
        src = m.group(1)
        if not _is_external_url(src):
            refs.append(('script', src))

    # <link rel="stylesheet" href="..."> 或 .css 文件
    for m in re.finditer(
        r'<link\b[^>]*\bhref\s*=\s*["\']([^"\']+)["\'][^>]*>',
        html_text,
        re.IGNORECASE,
    ):
        href = m.group(1)
        if _is_external_url(href):
            continue
        tag_text = m.group(0)
        rel_match = re.search(r'\brel\s*=\s*["\']([^"\']+)["\']', tag_text, re.IGNORECASE)
        rel_value = (rel_match.group(1) or '').lower() if rel_match else ''
        is_stylesheet = 'stylesheet' in rel_value.split()
        is_css = href.lower().endswith('.css')
        if is_stylesheet or is_css:
            refs.append(('stylesheet', href))

    return refs


def inline_html_references(
    user_id: str,
    html_virtual_path: str,
) -> str:
    """读取工作区 HTML 文件，将本地引用的 JS/CSS 内联后返回完整 HTML。

    Args:
        user_id: 用户 ID
        html_virtual_path: HTML 文件虚拟路径，如 workspace/{sid}/subdir/index.html

    Returns:
        内联后的完整 HTML 字符串
    """
    # 1. 读取 HTML 文件
    abs_path, _, _, _ = resolve_virtual_path(user_id, html_virtual_path)
    if not abs_path.is_file():
        raise FileNotFoundError(html_virtual_path)

    html_bytes = abs_path.read_bytes()
    html_text = html_bytes.decode('utf-8')

    # 2. HTML 所在目录（虚拟路径）
    dir_vpath = html_virtual_path[:html_virtual_path.rfind('/') + 1]

    # 3. 提取本地引用
    refs = _extract_local_refs(html_text)
    if not refs:
        return html_text

    # 去重，保留首次出现位置
    seen = set()
    unique_refs = []
    for tag_type, rel_path in refs:
        if rel_path not in seen:
            seen.add(rel_path)
            unique_refs.append((tag_type, rel_path))

    if len(unique_refs) > MAX_INLINE_FILES:
        logger.warning(
            f"[HtmlInline] 依赖过多 ({len(unique_refs)})，仅内联前 {MAX_INLINE_FILES} 个 "
            f"path={html_virtual_path}"
        )
        unique_refs = unique_refs[:MAX_INLINE_FILES]

    # 4. 读取并内联每个依赖
    inlined_count = 0
    for tag_type, rel_path in unique_refs:
        dep_vpath = _resolve_relative_path(dir_vpath, rel_path)
        try:
            dep_abs, _, _, _ = resolve_virtual_path(user_id, dep_vpath)
            if not dep_abs.is_file():
                logger.warning(f"[HtmlInline] 依赖不存在: {dep_vpath}")
                continue

            dep_bytes = dep_abs.read_bytes()
            if len(dep_bytes) > MAX_INLINE_FILE_BYTES:
                dep_bytes = dep_bytes[:MAX_INLINE_FILE_BYTES]
            dep_content = dep_bytes.decode('utf-8', errors='replace')

            if tag_type == 'script':
                # <script src="x.js"> → <script>content</script>
                html_text = re.sub(
                    r'<script\b[^>]*\bsrc\s*=\s*["\']' + re.escape(rel_path) + r'["\'][^>]*>\s*</script\s*>',
                    '<script>' + dep_content + '</script>',
                    html_text,
                    count=1,
                    flags=re.IGNORECASE,
                )
                inlined_count += 1
            elif tag_type == 'stylesheet':
                # <link href="x.css" ...> → <style>content</style>
                html_text = re.sub(
                    r'<link\b[^>]*\bhref\s*=\s*["\']' + re.escape(rel_path) + r'["\'][^>]*>',
                    '<style>' + dep_content + '</style>',
                    html_text,
                    count=1,
                    flags=re.IGNORECASE,
                )
                inlined_count += 1

        except ValueError as e:
            logger.warning(f"[HtmlInline] 路径解析失败 dep={dep_vpath}: {e}")
            continue
        except Exception as e:
            logger.warning(f"[HtmlInline] 读取依赖失败 dep={dep_vpath}: {e}")
            continue

    if inlined_count > 0:
        logger.info(
            f"[HtmlInline] 内联完成: {inlined_count} 个文件 "
            f"path={html_virtual_path}"
        )

    return html_text
