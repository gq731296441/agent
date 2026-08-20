"""
HTML工具模块

提供HTML处理相关的工具函数，包括添加AI标识等功能。

``beautifulsoup4`` 缺失时：先通过 ``_pip_retry.ensure_import`` 用当前解释器
``pip install beautifulsoup4`` 并重试导入；仍失败则返回原 HTML，不阻断保存流程。
"""

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
    为HTML内容添加AI标识属性（迭代版本，避免大文档递归溢出）

    策略：将原始 HTML 包装在一个临时容器 `<div id='__ai_wrapper__'>` 中，再解析。
    这可以避免当 HTML 中内嵌完整 HTML 文档时，BeautifulSoup 错将其当作根节点。
    处理完后返回容器的内部 HTML（`decode_contents()`），即注入后的原始内容。

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

    try:
        # 使用包装器避免内嵌文档干扰结构
        wrapper_html = f"<div id='__ai_wrapper__'>{html}</div>"
        soup = BeautifulSoup(wrapper_html, "html.parser")
        wrapper = soup.find('div', id='__ai_wrapper__')
        if wrapper is None:
            logger.warning("包装器未找到，回退直接返回原始 HTML")
            return html

        # 迭代遍历 wrapper 的直接子元素
        stack = []
        for child in list(wrapper.children):
            if isinstance(child, Tag):
                stack.append(child)

        while stack:
            elem = stack.pop()
            # 跳过特定类
            if elem.get("class-name") == "plantuml-source":
                continue

            tag_name = (elem.name or "").lower()
            if tag_name == TAG_P or tag_name in TAG_H:
                elem["data-ai"] = ai_app_id

            # 将当前元素的子元素入栈
            for child in list(elem.children):
                if isinstance(child, Tag):
                    stack.append(child)

        # 返回包装器的内部 HTML
        return wrapper.decode_contents()

    except Exception as error:
        logger.error("添加AI标识失败: %s", error)
        return html
