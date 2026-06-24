#!/usr/bin/env python3
"""
HTML / Markdown 内容转换（三合一：html_converter + html_text_extractor + md_to_html）。

对外接口：
  - html_to_markdown(html)   HTML → Markdown
  - html_to_text(html)      HTML → 纯文本（供总结/LLM）
  - markdown_to_html(content) Markdown → HTML
  - get_space_link_from_mail_html(html) 从邮件正文 HTML 解析 space_link（对外统一入口）
  - extract_space_link(html) 在 .mail-text.mail-sys-name 内取 iframe 的 src（首个 transfer 链接）
  - has_zhistory_row(html) 是否含 .zhistoryRow（转发/会话历史块）
"""

import re
from html.parser import HTMLParser
from typing import List, Optional

# ---------------------------------------------------------------------------
# 1. HTML → Markdown（原 html_converter）
# ---------------------------------------------------------------------------


class HTMLToMarkdown(HTMLParser):
    """Simple HTML to Markdown converter."""

    def __init__(self):
        super().__init__()
        self.markdown = []
        self.in_pre = False
        self.in_code = False
        self.in_a = False
        self.a_href = ""
        self.in_bold = False
        self.in_italic = False
        self.in_list = False
        self.list_type = None
        self.list_counter = 0
        self.skip_content = 0

    def handle_starttag(self, tag: str, attrs: List[tuple]):
        attrs_dict = dict(attrs)
        if tag in ("script", "style", "head"):
            self.skip_content += 1
            return
        if self.skip_content > 0:
            return
        if tag == "p":
            pass
        elif tag == "br":
            self.markdown.append("\n")
        elif tag == "hr":
            self.markdown.append("\n---\n")
        elif tag == "h1":
            self.markdown.append("\n# ")
        elif tag == "h2":
            self.markdown.append("\n## ")
        elif tag == "h3":
            self.markdown.append("\n### ")
        elif tag == "h4":
            self.markdown.append("\n#### ")
        elif tag == "h5":
            self.markdown.append("\n##### ")
        elif tag == "h6":
            self.markdown.append("\n###### ")
        elif tag == "strong" or tag == "b":
            self.markdown.append("**")
            self.in_bold = True
        elif tag == "em" or tag == "i":
            self.markdown.append("*")
            self.in_italic = True
        elif tag == "a":
            self.in_a = True
            self.a_href = attrs_dict.get("href", "")
            self.markdown.append("[")
        elif tag == "img":
            alt = attrs_dict.get("alt", "")
            src = attrs_dict.get("src", "")
            self.markdown.append(f"![{alt}]({src})")
        elif tag == "code":
            if not self.in_pre:
                self.markdown.append("`")
            self.in_code = True
        elif tag == "pre":
            self.markdown.append("\n```\n")
            self.in_pre = True
        elif tag == "ul":
            self.in_list = True
            self.list_type = "ul"
            self.markdown.append("\n")
        elif tag == "ol":
            self.in_list = True
            self.list_type = "ol"
            self.list_counter = 1
            self.markdown.append("\n")
        elif tag == "li":
            if self.list_type == "ul":
                self.markdown.append("- ")
            else:
                self.markdown.append(f"{self.list_counter}. ")
                self.list_counter += 1
        elif tag == "blockquote":
            self.markdown.append("\n> ")
        elif tag == "div":
            pass
        elif tag in ("table", "tr", "td", "th"):
            if tag in ("td", "th"):
                self.markdown.append("| ")

    def handle_endtag(self, tag: str):
        if tag in ("script", "style", "head"):
            self.skip_content -= 1
            return
        if self.skip_content > 0:
            return
        if tag == "p":
            self.markdown.append("\n\n")
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.markdown.append("\n")
        elif tag == "strong" or tag == "b":
            self.markdown.append("**")
            self.in_bold = False
        elif tag == "em" or tag == "i":
            self.markdown.append("*")
            self.in_italic = False
        elif tag == "a":
            self.markdown.append(f"]({self.a_href})")
            self.in_a = False
        elif tag == "code":
            if not self.in_pre:
                self.markdown.append("`")
            self.in_code = False
        elif tag == "pre":
            self.markdown.append("\n```\n")
            self.in_pre = False
        elif tag in ("ul", "ol"):
            self.in_list = False
            self.list_type = None
            self.markdown.append("\n")
        elif tag == "li":
            self.markdown.append("\n")
        elif tag == "blockquote":
            self.markdown.append("\n")
        elif tag == "tr":
            self.markdown.append(" |\n")
        elif tag == "table":
            self.markdown.append("\n")

    def handle_data(self, data: str):
        if self.skip_content > 0:
            return
        text = data.replace("\r", "")
        if not self.in_pre:
            text = re.sub(r"[ \t]+", " ", text)
        self.markdown.append(text)

    def get_markdown(self) -> str:
        result = "".join(self.markdown)
        result = re.sub(r"\n{4,}", "\n\n\n", result)
        return result.strip()


def _fallback_strip_tags_preserve_links(html: str) -> str:
    """解析失败时回退：保留 a 标签的 href，转为 [text](href) 格式后再剥离其余标签。"""
    def replace_a(m):
        href = (m.group(1) or "").strip()
        text = (m.group(2) or "").strip()
        if href:
            return f"[{text}]({href})"
        return text
    html = re.sub(
        r'<a\s+[^>]*href=["\']([^"\']*)["\'][^>]*>([^<]*)</a>',
        replace_a,
        html,
        flags=re.I | re.DOTALL,
    )
    return re.sub(r"<[^>]+>", "", html)


def html_to_markdown(html: str) -> str:
    """HTML → Markdown。a 标签会转换为 [text](href) 格式保留链接。无外部依赖。"""
    if not html:
        return ""
    converter = HTMLToMarkdown()
    try:
        # bs4 仅作为“可选增强”：可用时先做 DOM 级过滤，去掉签名/历史块/隐藏块，减少噪音；
        # 不可用或异常时保持原行为，确保无外部依赖也可运行。
        try:
            # Markdown 转换需保留原始邮件引用（zhistoryRow），供 view 展示侧自行解析/排版。
            html = _clean_html_by_bs4(html, remove_zhistory_row=False)
        except Exception:
            pass
        converter.feed(html)
        return converter.get_markdown()
    except Exception:
        return _fallback_strip_tags_preserve_links(html)


# ---------------------------------------------------------------------------
# 2. HTML → 纯文本（原 html_text_extractor）
# ---------------------------------------------------------------------------

_EXCLUDE_CLASSES = ("zMailSign", "zhistoryRow")
_DISPLAY_NONE_RE = re.compile(r"display\s*:\s*none", re.I)


def _clean_html_by_bs4(html: str, remove_zhistory_row: bool = True) -> str:
    """
    对 HTML 做 DOM 级清洗（bs4 可用时）。

    - 删除 script/style 节点
    - 删除签名块：class 命中 zMailSign 的节点及其子树
    - 可选删除原始邮件引用块：remove_zhistory_row=True 时删除 class 命中 zhistoryRow 的节点及其子树
    - 删除 style 命中 display:none 的节点及其子树

    返回清洗后的 HTML 字符串；若 bs4 不可用应由调用方捕获异常并回退。
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    exclude_classes = ("zMailSign", "zhistoryRow") if remove_zhistory_row else ("zMailSign",)
    # 过滤掉 class 为 _EXCLUDE_CLASSES 的节点及其子节点
    # 正序迭代，跳过已脱离树的节点（父节点被 decompose 后子节点不再在 soup 中）
    for el in list(soup.find_all(True)):
        if el not in soup.descendants:
            continue
        classes = el.get("class") or []
        if isinstance(classes, str):
            classes = classes.split()
        if any(c in exclude_classes for c in classes):
            el.decompose()
    # 过滤掉 style 中含有 display:none 的隐藏节点及其子节点
    # 正序迭代，跳过已脱离树的节点（父节点被 decompose 后子节点不再在 soup 中）
    for el in list(soup.find_all(True)):
        if el not in soup.descendants:
            continue
        style = el.get("style")
        if style and _DISPLAY_NONE_RE.search(str(style)):
            el.decompose()
    return str(soup)


def _has_exclude_class(attrs) -> bool:
    for name, value in attrs:
        if name == "class" and value:
            classes = value.split() if isinstance(value, str) else (value or [])
            if any(c in _EXCLUDE_CLASSES for c in classes):
                return True
    return False


def _has_hidden_style(attrs) -> bool:
    for name, value in attrs:
        if name == "style" and value and _DISPLAY_NONE_RE.search(str(value)):
            return True
    return False


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []
        self._skip = False
        self._exclude_stack = []

    def handle_starttag(self, tag, attrs):
        if self._exclude_stack:
            self._exclude_stack.append(tag)
            return
        if _has_exclude_class(attrs) or _has_hidden_style(attrs):
            self._exclude_stack.append(tag)
            return
        if tag in ("script", "style"):
            self._skip = True
        elif tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if self._exclude_stack and self._exclude_stack[-1] == tag:
            self._exclude_stack.pop()
        if self._exclude_stack:
            return
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data):
        if self._exclude_stack or self._skip:
            return
        self._parts.append(data)

    def get_text(self) -> str:
        return _normalize_newlines("".join(self._parts)).strip()


def _normalize_newlines(text: str) -> str:
    """将连续 3 个及以上换行压成 2 个。"""
    return re.sub(r"\n{3,}", "\n\n", text)


def _extract_by_bs4(html: str) -> str:
    from bs4 import BeautifulSoup

    # 纯文本提取用于总结/LLM：应去掉原始邮件引用（zhistoryRow），避免历史链路噪音。
    clean_html = _clean_html_by_bs4(html, remove_zhistory_row=True)
    soup = BeautifulSoup(clean_html, "html.parser")
    return _normalize_newlines(soup.get_text(separator="\n", strip=True))


def _extract_by_parser(html: str) -> str:
    extractor = _HTMLTextExtractor()
    extractor.feed(html)
    return extractor.get_text()


def _normalize_for_llm(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def html_to_text(html: str) -> str:
    """HTML → 纯文本，供总结/LLM。优先 BeautifulSoup，回退 HTMLParser。"""
    if not html:
        return ""
    try:
        text = _extract_by_bs4(html)
        if text:
            return _normalize_for_llm(text)
        return _normalize_for_llm(_extract_by_parser(html))
    except Exception:
        return _normalize_for_llm(_extract_by_parser(html))


def has_zhistory_row(html: str) -> bool:
    """
    邮件 HTML 中是否存在 .zhistoryRow（常见于带转发链/历史会话的正文）。

    存在时不应再提取 space_link，避免把历史块里的系统链接当作当前正文来源。
    """
    if not html:
        return False
    try:
        from bs4 import BeautifulSoup

        return BeautifulSoup(html, "html.parser").select_one(".zhistoryRow") is not None
    except Exception:
        return "zhistoryRow" in html


def extract_space_link(html: str) -> str:
    """
    从给定的 HTML 文本中查找同时具有 mail-text 与 mail-sys-name 的节点，
    若找到则在其子节点中查找 iframe，返回首个 src 为 iCenter transfer 链接的 src；否则返回空字符串。
    """
    transfer_prefix = "https://icenterapi.zte.com.cn/zte-km-icenter-web/static/transfer.html"
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one(".mail-text.mail-sys-name")
        if el is None:
            return ""
        for iframe in el.find_all("iframe"):
            src = (iframe.get("src") or "").strip()
            if src.startswith(transfer_prefix):
                return src
        return ""
    except Exception:
        return ""


def get_space_link_from_mail_html(html: str) -> str:
    """
    从邮件正文 HTML 得到用于拉取 iCenter 空间内容的 space_link。

    含 .zhistoryRow（转发/会话历史）时不返回链接，避免误用历史块中的系统链接；
    否则从 .mail-text.mail-sys-name 内 iframe 的 src 提取 transfer.html 链接。
    """
    if not html:
        return ""
    if has_zhistory_row(html):
        return ""
    return extract_space_link(html)


# ---------------------------------------------------------------------------
# 3. Markdown → HTML（原 md_to_html）
# ---------------------------------------------------------------------------

# 懒加载缓存，避免重复定义扩展类（使用 list 以便在函数内可写，无需 global）
_table_style_extension_cache = []


def _make_table_style_extension():
    import markdown
    from markdown.extensions import Extension
    from markdown.treeprocessors import Treeprocessor

    class TableStyleTreeprocessor(Treeprocessor):
        def run(self, root):
            for table in root.iter("table"):
                table.set("style", "border-collapse:collapse; margin-bottom:10px;")
                for th in table.iter("th"):
                    th.set(
                        "style",
                        "padding: 5px 18px 5px 10px;  background: #eee;border:1px solid #bbb;word-break:break-all",
                    )
                for td in table.iter("td"):
                    td.set("style", "padding: 5px 10px;border:1px solid #bbb;word-break:break-all")
            return root

    class TableStyleExtension(Extension):
        def extendMarkdown(self, md):
            md.treeprocessors.register(TableStyleTreeprocessor(md), "table_style", 10)

    return TableStyleExtension


def markdown_to_html(content):
    """
    将 Markdown 正文转为 HTML（单行，无换行符）。
    已启用扩展：nl2br、tables、TableStyleExtension、sane_lists、footnotes。
    支持字面量 \\n、\\r\\n、\\r 转为真实换行，便于命令行传入正文时正确换行。
    """
    text = content or ""
    text = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
    text = text.strip()
    if not text:
        return ""
    import markdown

    if not _table_style_extension_cache:
        _table_style_extension_cache.append(_make_table_style_extension())
    extensions = [
        "nl2br",
        "tables",
        _table_style_extension_cache[0](),
        "sane_lists",
        "footnotes",
    ]
    html_out = markdown.markdown(text, extensions=extensions)
    return html_out.replace("\r\n", "").replace("\n", "")
