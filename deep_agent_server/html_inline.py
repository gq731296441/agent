"""HTML 依赖内联与资源引用改写。

- 将同目录的 CSS/JS 内联，实现单文件可独立渲染；
- 将本地资源引用（<img>/srcset/icon/style 内 url() 等）改写为可访问 URL
  （分享快照路径 / 预览下载 URL），沙箱虚拟路径（/workspace/...、/mnt/skills/...）
  在此处转换为后端虚拟路径。

对齐前端 deepApi.js 中的 inlineHtmlReferences() / workspacePath.js resolveSandboxAssetPath() 逻辑。
"""

import re
from typing import Callable, List, Optional, Tuple

from config import get_logger
from deep_agent_server.workspace_service import resolve_virtual_path

logger = get_logger(__name__)

MAX_INLINE_FILES = 20
MAX_INLINE_FILE_BYTES = 500 * 1024  # 单个依赖最大 500KB

# 外部 URL 特征（无需内联 / 改写）
_EXTERNAL_URL_RE = re.compile(
    r'^(?:https?:|data:|mailto:|tel:|ftp:|file:|blob:|#|//)',
    re.IGNORECASE,
)

# CSS url() 三种引号形式（双引号 / 单引号 / 裸值）
_CSS_URL_RE = re.compile(
    r'url\(\s*(?:"(?P<dq>[^"]*)"|\'(?P<sq>[^\']*)\'|(?P<bare>[^)\s]+))\s*\)',
    re.IGNORECASE,
)

# 会话 UUID 判定（workspace/{uuid}/... 视为已含 session 的完整路径）
_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)


def _is_external_url(url: str) -> bool:
    """判断 URL 是否为外部引用（不需要内联）。"""
    if not url:
        return True
    return bool(_EXTERNAL_URL_RE.match(url.strip()))


def _extract_session_id(html_virtual_path: str) -> str:
    """从 workspace/{sid}/... 中提取 session_id。"""
    parts = (html_virtual_path or '').strip('/').split('/')
    return parts[1] if len(parts) >= 2 else ''


def _resolve_local_vpath(dir_vpath: str, session_id: str, raw: str) -> Optional[str]:
    """沙箱路径 → 后端虚拟路径（与前端 resolveSandboxAssetPath 规则一致）。

    规则表:
      /mnt/skills/{skill}/...        → skills/{skill}/...
      /workspace/...                 → 第二段是 UUID 原样；否则补 session_id
      /uploads/ | /artifacts/ | /skills/ → 去前导 / 原样
      其他 /foo（沙箱根=会话工作区）  → workspace/{sid}/foo
      相对路径                        → 基于 dir_vpath 栈式归一化
    外部引用 / 无法识别 / .. 上溢      → None（调用方保留原 URL）
    """
    if not raw:
        return None
    raw = raw.strip()
    if _is_external_url(raw):
        return None

    if raw.startswith('/mnt/skills/'):
        return 'skills/' + raw[len('/mnt/skills/'):].lstrip('/')

    if raw.startswith('/'):
        path = raw.lstrip('/')
        if path.startswith('workspace/'):
            parts = path.split('/', 2)
            if len(parts) >= 2 and _UUID_RE.match(parts[1]):
                return path
            rest = parts[1] if len(parts) < 3 else parts[1] + '/' + parts[2]
            return f'workspace/{session_id}/{rest}' if session_id else None
        if path.startswith(('uploads/', 'artifacts/', 'skills/')):
            return path
        # 其他 /foo：沙箱根 = 会话工作区
        return f'workspace/{session_id}/{path}' if session_id else None

    # 相对路径：基于 HTML 所在虚拟目录归一化
    base = dir_vpath
    if not base.endswith('/'):
        base += '/'
    combined = base + raw
    parts = [p for p in combined.split('/') if p and p != '.']
    resolved = []
    for part in parts:
        if part == '..':
            if resolved:
                resolved.pop()
            else:
                return None  # 上溢
        else:
            resolved.append(part)
    if not resolved:
        return None
    vpath = '/'.join(resolved)
    # 归一化结果必须落在已知虚拟路径树内，否则保留原 URL
    if not vpath.startswith(('workspace/', 'skills/', 'uploads/', 'artifacts/')):
        return None
    return vpath


def _iter_css_urls(css_text: str) -> List[str]:
    """提取 CSS 文本中所有 url() 内容（含三种引号形式）。"""
    urls = []
    for m in _CSS_URL_RE.finditer(css_text):
        content = m.group('dq') or m.group('sq') or m.group('bare') or ''
        content = content.strip()
        if content:
            urls.append(content)
    return urls


def _rewrite_css_urls(css_text: str, resolve: Callable[[str], Optional[str]]) -> str:
    """改写 CSS 文本中的 url() 引用。

    resolve: raw 路径 -> 最终 URL；返回 None 时保留原样。
    """
    def repl(m: re.Match) -> str:
        content = m.group('dq') or m.group('sq') or m.group('bare') or ''
        content = content.strip()
        if not content or content.startswith('#') or _is_external_url(content):
            return m.group(0)
        new_url = resolve(content)
        if new_url is None:
            return m.group(0)
        safe = new_url.replace('"', '%22')
        return f'url("{safe}")'

    return _CSS_URL_RE.sub(repl, css_text)


def _rewrite_srcset(srcset_value: str, resolve: Callable[[str], Optional[str]]) -> str:
    """改写 srcset 属性：逗号切候选，每候选首个空白分隔 token 为 URL。"""
    out = []
    for cand in srcset_value.split(','):
        cand = cand.strip()
        if not cand:
            continue
        tokens = cand.split(None, 1)
        url = tokens[0]
        new_url = resolve(url)
        if new_url is None:
            out.append(cand)
        else:
            out.append(new_url + (' ' + tokens[1] if len(tokens) > 1 else ''))
    return ', '.join(out)


def _script_tag_is_module(html_text: str, rel_path: str) -> bool:
    """判断 <script src="rel_path"> 是否为 type="module"（module 脚本不可内联）。"""
    m = re.search(
        r'<script\b[^>]*\bsrc\s*=\s*["\']' + re.escape(rel_path) + r'["\'][^>]*>',
        html_text,
        re.IGNORECASE,
    )
    if not m:
        return False
    tag = m.group(0)
    tm = re.search(r'\btype\s*=\s*["\']([^"\']+)["\']', tag, re.IGNORECASE)
    return bool(tm and "module" in tm.group(1).lower())


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


def _rewrite_link_tag(tag_text: str, href: str, resolve: Callable[[str], Optional[str]]) -> str:
    """改写 <link> 中非 stylesheet 的本地资源（icon/apple-touch-icon/manifest）。"""
    rel_match = re.search(r'\brel\s*=\s*["\']([^"\']+)["\']', tag_text, re.IGNORECASE)
    rel_value = (rel_match.group(1) or '').lower() if rel_match else ''
    rel_tokens = rel_value.split()
    is_icon = any(t in rel_tokens for t in ('icon', 'apple-touch-icon', 'manifest'))
    if not is_icon:
        return tag_text
    new_url = resolve(href)
    if new_url is None:
        return tag_text
    return re.sub(
        r'\bhref\s*=\s*["\']([^"\']+)["\']',
        lambda m: f'href="{new_url.replace(chr(34), "%22")}"',
        tag_text,
        count=1,
        flags=re.IGNORECASE,
    )


def _rewrite_asset_refs(
    html_text: str,
    resolve: Callable[[str], Optional[str]],
) -> str:
    """改写 HTML 中的本地资源引用（img/srcset/icon/style 内 url()）。

    仅处理可解析为虚拟路径的本地引用；外部/无法识别保留原样。
    全部用 lambda 返回 replacement，避免 re 对转义序列二次解释。
    """
    # 1. style 属性内 url()
    html_text = re.sub(
        r'(\bstyle\s*=\s*["\'])([^"\']*)(["\'])',
        lambda m: m.group(1) + _rewrite_css_urls(m.group(2), resolve) + m.group(3),
        html_text,
        flags=re.IGNORECASE,
    )
    # 2. <style> 块内 url()
    html_text = re.sub(
        r'(<style\b[^>]*>)(.*?)(</style\s*>)',
        lambda m: m.group(1) + _rewrite_css_urls(m.group(2), resolve) + m.group(3),
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    # 3. <img src="...">
    html_text = re.sub(
        r'(<img\b[^>]*?\bsrc\s*=\s*["\'])([^"\']+)(["\'])',
        lambda m: m.group(1) + (resolve(m.group(2)) or m.group(2)) + m.group(3),
        html_text,
        flags=re.IGNORECASE,
    )
    # 4. <img srcset="...">
    html_text = re.sub(
        r'(<img\b[^>]*?\bsrcset\s*=\s*["\'])([^"\']*)(["\'])',
        lambda m: m.group(1) + _rewrite_srcset(m.group(2), resolve) + m.group(3),
        html_text,
        flags=re.IGNORECASE,
    )
    # 5. <link> 非 stylesheet 本地资源（icon / manifest）
    html_text = re.sub(
        r'<link\b[^>]*\bhref\s*=\s*["\']([^"\']+)["\'][^>]*>',
        lambda m: _rewrite_link_tag(m.group(0), m.group(1), resolve),
        html_text,
        flags=re.IGNORECASE,
    )
    return html_text


def _extract_asset_refs(
    html_text: str,
    dir_vpath: str,
    session_id: str,
) -> List[str]:
    """提取 HTML 中本地图片资源引用（解析为虚拟路径后返回，去重）。

    供分享快照扫描使用：img src / srcset / icon link / style 属性与 <style> 块内 url()。
    """
    vpaths: List[str] = []

    def resolve(raw: str) -> Optional[str]:
        vpath = _resolve_local_vpath(dir_vpath, session_id, raw)
        if vpath is not None:
            vpaths.append(vpath)
        return vpath

    # img src / srcset
    for m in re.finditer(r'<img\b[^>]*?\bsrc\s*=\s*["\']([^"\']+)["\']', html_text, re.IGNORECASE):
        resolve(m.group(1))
    for m in re.finditer(r'<img\b[^>]*?\bsrcset\s*=\s*["\']([^"\']+)["\']', html_text, re.IGNORECASE):
        for cand in m.group(1).split(','):
            tokens = cand.strip().split(None, 1)
            if tokens:
                resolve(tokens[0])
    # link icon / manifest
    for m in re.finditer(
        r'<link\b[^>]*\bhref\s*=\s*["\']([^"\']+)["\'][^>]*>',
        html_text,
        re.IGNORECASE,
    ):
        tag_text = m.group(0)
        rel_match = re.search(r'\brel\s*=\s*["\']([^"\']+)["\']', tag_text, re.IGNORECASE)
        rel_value = (rel_match.group(1) or '').lower() if rel_match else ''
        if any(t in rel_value.split() for t in ('icon', 'apple-touch-icon', 'manifest')):
            resolve(m.group(1))
    # style 属性 + <style> 块内 url()
    for m in re.finditer(r'\bstyle\s*=\s*["\']([^"\']*)["\']', html_text, re.IGNORECASE):
        for content in _iter_css_urls(m.group(1)):
            resolve(content)
    for m in re.finditer(r'<style\b[^>]*>(.*?)</style\s*>', html_text, re.IGNORECASE | re.DOTALL):
        for content in _iter_css_urls(m.group(1)):
            resolve(content)

    # 去重（保持顺序）
    seen = set()
    unique: List[str] = []
    for vpath in vpaths:
        if vpath not in seen:
            seen.add(vpath)
            unique.append(vpath)
    return unique


def inline_html_references(
    user_id: str,
    html_virtual_path: str,
    *,
    agent_id: Optional[str] = None,
    asset_url_fn: Optional[Callable[[str], str]] = None,
) -> str:
    """读取工作区 HTML 文件，将本地资源引用改写、JS/CSS 内联后返回完整 HTML。

    Args:
        user_id: 用户 ID
        html_virtual_path: HTML 文件虚拟路径，如 workspace/{sid}/subdir/index.html
        agent_id: 订阅智能体会话上下文（skills 资源解析用）
        asset_url_fn: 虚拟路径 -> 最终可访问 URL 的回调（分享=快照路径 URL，
            预览=download URL）。为 None 时仅内联 JS/CSS，不改写图片资源。

    Returns:
        处理后的完整 HTML 字符串
    """
    # 1. 读取 HTML 文件
    abs_path, _, _, _ = resolve_virtual_path(user_id, html_virtual_path, agent_id=agent_id)
    if not abs_path.is_file():
        raise FileNotFoundError(html_virtual_path)

    html_bytes = abs_path.read_bytes()
    html_text = html_bytes.decode('utf-8')

    # 2. HTML 所在目录（虚拟路径）与 session_id
    dir_vpath = html_virtual_path[:html_virtual_path.rfind('/') + 1]
    session_id = _extract_session_id(html_virtual_path)

    def make_resolve(base_dir: str) -> Callable[[str], Optional[str]]:
        def resolve(raw: str) -> Optional[str]:
            vpath = _resolve_local_vpath(base_dir, session_id, raw)
            if vpath is None or asset_url_fn is None:
                return None
            return asset_url_fn(vpath)
        return resolve

    # 3. 资源引用改写（图片等，仅当调用方提供 asset_url_fn）
    if asset_url_fn is not None:
        html_text = _rewrite_asset_refs(html_text, make_resolve(dir_vpath))

    # 4. 提取本地 JS/CSS 引用
    refs = _extract_local_refs(html_text)

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

    # 5. 读取并内联每个依赖
    inlined_count = 0
    for tag_type, rel_path in unique_refs:
        dep_vpath = _resolve_local_vpath(dir_vpath, session_id, rel_path)
        if dep_vpath is None:
            logger.warning(f"[HtmlInline] 依赖路径无法解析: {rel_path} path={html_virtual_path}")
            continue
        try:
            dep_abs, _, _, _ = resolve_virtual_path(user_id, dep_vpath, agent_id=agent_id)
            if not dep_abs.is_file():
                logger.warning(f"[HtmlInline] 依赖不存在: {dep_vpath}")
                continue

            dep_bytes = dep_abs.read_bytes()
            if len(dep_bytes) > MAX_INLINE_FILE_BYTES:
                dep_bytes = dep_bytes[:MAX_INLINE_FILE_BYTES]
            dep_content = dep_bytes.decode('utf-8', errors='replace')

            if tag_type == 'script':
                # module 脚本不内联（import 相对解析/top-level await 语义会破坏），
                # 改写 src 为可访问 URL（分享=asset 路由，预览=download URL）
                if asset_url_fn is not None and _script_tag_is_module(html_text, rel_path):
                    html_text = re.sub(
                        r'(\bsrc\s*=\s*["\'])' + re.escape(rel_path) + r'(["\'])',
                        lambda _m: _m.group(1) + asset_url_fn(dep_vpath) + _m.group(2),
                        html_text,
                        count=1,
                        flags=re.IGNORECASE,
                    )
                    continue
                # <script src="x.js"> → <script>content</script>
                # 注意：必须用 lambda 返回 replacement 字符串，
                # 否则 re.sub 会对 replacement 中的 \n 等转义序列进行二次解释导致 JS/CSS 内容损坏
                html_text = re.sub(
                    r'<script\b[^>]*\bsrc\s*=\s*["\']' + re.escape(rel_path) + r'["\'][^>]*>\s*</script\s*>',
                    lambda _m: '<script>' + dep_content + '</script>',
                    html_text,
                    count=1,
                    flags=re.IGNORECASE,
                )
                inlined_count += 1
            elif tag_type == 'stylesheet':
                # 外部 CSS 内联前，按其自身目录改写内部 url()（CSS 内相对路径以 CSS 文件为基准）
                if asset_url_fn is not None:
                    css_dir = dep_vpath[:dep_vpath.rfind('/') + 1]
                    dep_content = _rewrite_css_urls(dep_content, make_resolve(css_dir))
                # <link href="x.css" ...> → <style>content</style>
                html_text = re.sub(
                    r'<link\b[^>]*\bhref\s*=\s*["\']' + re.escape(rel_path) + r'["\'][^>]*>',
                    lambda _m: '<style>' + dep_content + '</style>',
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
