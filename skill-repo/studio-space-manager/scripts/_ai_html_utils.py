"""
HTML工具模块

提供HTML处理相关的工具函数，包括添加AI标识等功能。

``beautifulsoup4`` 缺失时：先通过 ``_pip_retry.ensure_import`` 用当前解释器
``pip install beautifulsoup4`` 并重试导入；仍失败则返回原 HTML，不阻断保存流程。
"""

from __future__ import annotations

import logging

from _pip_retry import ensure_import

logger = logging.getLogger(__name__)

# 需要添加AI标识的标签
TAG_P = "p"
TAG_H = {"h1", "h2", "h3", "h4", "h5", "h6"}


def _import_bs4():
    from bs4 import BeautifulSoup, Tag

    return BeautifulSoup, Tag


def html_add_ai_attr(html: str, ai_app_id: str = "studio-space-manager") -> str:
    """
    为HTML内容添加AI标识属性（简化版本，参考Java实现）

    优先保证可用：依赖缺失时会尝试自动安装后重试；最终仍不可用时返回原始 HTML。
    """
    if not html or not html.strip():
        return html

    bs4_pair = ensure_import(
        _import_bs4,
        "beautifulsoup4",
        invalidate_prefixes=("bs4",),
    )
    if bs4_pair is None:
        logger.warning(
            "beautifulsoup4(bs4) 不可用（自动安装后仍失败），已跳过 data-ai 属性注入；"
            "页面仍可保存。"
        )
        return html

    BeautifulSoup, Tag = bs4_pair

    def text_add_ai_attr(element: Tag, app_id: str) -> None:
        if not isinstance(element, Tag):
            return

        tag_name = element.name.lower()

        if element.get("class-name") == "plantuml-source":
            return

        if tag_name == TAG_P or tag_name in TAG_H:
            element["data-ai"] = app_id

        if element.children:
            for child in element.children:
                if isinstance(child, Tag):
                    text_add_ai_attr(child, app_id)

    try:
        soup = BeautifulSoup(html, "html.parser")
        body = soup.body

        if body is None:
            body = soup

        for child in body.children:
            if isinstance(child, Tag):
                text_add_ai_attr(child, ai_app_id)

        if soup.body:
            return soup.body.decode_contents()
        if soup.html:
            return soup.html.decode_contents()
        return str(soup)

    except Exception as error:
        logger.error("添加AI标识失败: %s", error)
        return html
