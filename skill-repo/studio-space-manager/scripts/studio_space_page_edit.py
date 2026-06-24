#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
studio-space-manager - 编辑页面内容模块 (v3.0.0)

能力：
- 支持通过页面 URL 或本地文件（首行页面 URL）定位页面
- ⭐ **新增**: 直接读取本地 HTML 文件，无需 Markdown 转换
- 支持读取本地 Markdown 文件内容，转换为 HTML（向后兼容）
- 为 HTML 添加 data-ai 标识
- 走 MCP 同款页面更新接口（/zte-rdcloud-space-content/.../updateContent）回写正文

⚠️ **重要变更** (v3.0.0)：
- 推荐使用 HTML 直传模式，避免 Markdown 双向转换导致的格式丢失
- 对于包含宏、PlantUML或复杂格式的页面，请使用 --html-file 参数

推荐本地编辑流程：
【方式一】HTML直传模式（推荐，100%保真）
1) 用 `safe_page_edit.py --action export --url <URL>` 导出HTML
2) 编辑 HTML 文件
3) 用本脚本回写：`python3 studio_space_page_edit.py --html-file page.html`

【方式二】Markdown模式（简单文本页面）
1) 用 `space_page_tomd.py --url <URL>` 导出Markdown
2) 编辑 MD 文件
3) 用本脚本回写（会进行 Markdown→HTML转换）
"""

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List

import requests
from bs4 import BeautifulSoup

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 让脚本可在任意工作目录下被执行
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
BASE_DIR = os.path.dirname(SKILL_DIR)
for p in (SCRIPT_DIR, SKILL_DIR, BASE_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from system_param import (
    build_icenter_api_url,
    get_user_and_token as _get_user_and_token,
)

ICENTER_API_BASE = build_icenter_api_url("")  # 获取基础 URL
from _platform_utils import configure_stdio_utf8, get_platform_and_user_agent
from _ai_html_utils import html_add_ai_attr


BASE_URL = ICENTER_API_BASE
MARKDOWN2HTML_URL = f"{BASE_URL}/zte-rdcloud-space-wconvert/markdown2html"
FETCH_CONTENT_URL_TEMPLATE = f"{BASE_URL}/zte-rdcloud-space-content/content/{{page_id}}"
UPDATE_CONTENT_URL_TEMPLATE = (
    f"{BASE_URL}/zte-rdcloud-space-content/content/{{page_id}}/updateContent"
)
UPDATE_CHAPTER_URL = f"{BASE_URL}/zte-rdcloud-space-content/content/updateChapter"
QUERY_BLOCK_CONTENT_URL = f"{BASE_URL}/zte-rdcloud-space-block/block/content/query"
UPDATE_BLOCK_CONTENT_URL_TEMPLATE = f"{BASE_URL}/zte-rdcloud-space-content/block/content/{{page_id}}"


def get_user_and_token() -> Tuple[str, str]:
    """返回 (user_id, token)，非 strict（与其它脚本保持一致）。"""
    return _get_user_and_token(strict=False)


def _json_mode_log(message: str) -> None:
    """--json-output 时向 stderr 打一行日志，避免污染 stdout 纯 JSON。"""
    print(f"[studio_space_page_edit] {message}", file=sys.stderr)


def parse_markdown_export_file(content_file: str) -> Tuple[Optional[str], str]:
    """
    解析本地 md 文件：
    - 首行：页面 URL（可选）
    - 中间：空行
    - 其余：Markdown 正文
    """
    raw = Path(content_file).read_text(encoding="utf-8")
    raw = raw.lstrip("\ufeff")

    lines = raw.splitlines()
    if not lines:
        return None, ""

    first_line = (lines[0] or "").strip()
    page_url: Optional[str] = None
    content_start_idx = 1

    if first_line.startswith("http://") or first_line.startswith("https://"):
        page_url = first_line
        # 跳过 URL 后的空行
        while content_start_idx < len(lines) and (lines[content_start_idx] or "").strip() != "":
            content_start_idx += 1
        while content_start_idx < len(lines) and (lines[content_start_idx] or "").strip() == "":
            content_start_idx += 1
    else:
        page_url = None
        content_start_idx = 0

    markdown = "\n".join(lines[content_start_idx:]).strip()
    return page_url, markdown


def parse_html_export_file(content_file: str) -> Tuple[str, Optional[str]]:
    """
    解析本地 HTML 文件（v3.0.0新增）：
    - 支持从 safe_page_edit.py 导出的HTML文件
    - 或者包含页面URL注释的HTML文件
    - 直接返回完整HTML内容

    文件格式示例：
    <!-- Page URL: https://i.zte.com.cn/... -->
    <!DOCTYPE html>
    <html>...</html>
    """
    raw = Path(content_file).read_text(encoding="utf-8")
    raw = raw.lstrip("\ufeff")

    page_url: Optional[str] = None

    # 尝试从注释中解析URL
    url_match = re.search(r'<!--\s*Page\s+URL:\s*(https?://[^\s]+)\s*-->', raw, re.IGNORECASE)
    if url_match:
        page_url = url_match.group(1)
        # 移除URL注释
        raw = re.sub(r'<!--\s*Page\s+URL:[^\-]*-->\s*', '', raw, flags=re.IGNORECASE)

    # 如果首行是URL（兼容旧格式）
    lines = raw.splitlines()
    if lines and (lines[0].startswith("http://") or lines[0].startswith("https://")):
        page_url = lines[0].strip()
        # 跳过URL行和后续空行
        content_start_idx = 1
        while content_start_idx < len(lines) and not lines[content_start_idx].strip():
            content_start_idx += 1
        raw = "\n".join(lines[content_start_idx:])

    return raw.strip(), page_url


def parse_space_page_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    从页面 URL 中解析 spaceId/pageId。
    支持 shared/space/ispace 等常见格式。
    """
    if not url:
        return None, None
    patterns = [
        r"/shared/([a-f0-9]+)/wiki/page/([a-f0-9]+)/view",
        r"/space/([a-f0-9]+)/wiki/page/([a-f0-9]+)/view",
        r"/ispace/#/space/([a-f0-9]+)/wiki/page/([a-f0-9]+)/view",
    ]
    for pattern in patterns:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            return match.group(1), match.group(2)
    return None, None


def build_headers(user_id: str, token: str) -> Dict[str, str]:
    """构建统一请求头。"""
    user_agent = get_platform_and_user_agent()[1]
    return {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": user_agent,
        "X-Auth-Value": token,
        "X-Emp-No": user_id,
        "X-Tenant-Id": "ZTE",
        "X-Lang-Id": "zh_CN",
    }


def fetch_page_title(space_id: str, page_id: str, user_id: str, token: str) -> Dict[str, Any]:
    """通过内容查询接口读取页面标题。"""
    headers = build_headers(user_id, token)
    url = FETCH_CONTENT_URL_TEMPLATE.format(page_id=page_id)
    params = {
        "dataType": "HTML",
        "withMacro": "true",
        "spaceId": space_id,
    }
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return {"status": "error", "message": f"获取页面标题失败: {exc}"}

    code = (data.get("code") or {}).get("code")
    if code != "0000":
        return {
            "status": "error",
            "message": f"获取页面标题失败: {(data.get('code') or {}).get('msgId', '未知错误')}",
            "raw": data,
        }

    bo = data.get("bo") or {}
    return {"status": "success", "title": bo.get("title") or ""}


def markdown_to_html(markdown_content: str, user_id: str, token: str) -> str:
    """
    调用 iCenter markdown2html 服务转换 Markdown 为 HTML。

    说明：markdown2html 可能会破坏 PlantUML 代码块的换行，所以这里做了保护/恢复。

    增强功能:
    - 添加详细的日志记录，便于排查问题
    - 对转换结果进行验证，确保输出是有效的HTML
    - 提供更友好的错误信息
    """
    markdown_content = markdown_content or ""
    if not markdown_content.strip():
        logger.debug("Markdown内容为空，跳过转换")
        return ""

    logger.debug(f"开始转换Markdown内容，长度={len(markdown_content)}字符")

    headers = build_headers(user_id, token)

    # 保护PlantUML块
    protected_content, plantuml_blocks = protect_plantuml_blocks(markdown_content)
    if plantuml_blocks:
        logger.debug(f"检测到{len(plantuml_blocks)}个PlantUML代码块，已保护")

    try:
        logger.debug("调用后端markdown2html API...")
        resp = requests.post(
            f"{MARKDOWN2HTML_URL}?environmentType=browser",
            headers=headers,
            json={"content": protected_content},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        code = (data.get("code") or {}).get("code")

        if code != "0000":
            msg = (data.get("code") or {}).get("msgId", "未知错误")
            logger.warning(f"后端API返回错误码 {code}: {msg}，使用兜底方案")
            html_result = None
        else:
            html_result = data.get("bo")
            if html_result:
                logger.debug(f"后端API转换成功，HTML长度={len(html_result)}字符")
            else:
                logger.warning("后端API返回成功但bo为空，使用兜底方案")

    except requests.exceptions.Timeout:
        logger.error("后端API请求超时，使用兜底方案")
        html_result = None
    except requests.exceptions.RequestException as e:
        logger.error(f"后端API请求失败：{e}，使用兜底方案")
        html_result = None
    except Exception as e:
        logger.error(f"后端API响应解析失败：{e}，使用兜底方案")
        html_result = None

    if not html_result:
        logger.debug("使用本地fallback_markdown_to_html进行转换")
        html_result = fallback_markdown_to_html(markdown_content)

    # 恢复PlantUML块
    if plantuml_blocks:
        logger.debug("恢复PlantUML代码块")
        html_result = restore_plantuml_blocks(html_result, plantuml_blocks)

    # 验证转换结果
    if not html_result or not html_result.strip():
        logger.warning("Markdown转HTML结果为空，返回原始内容")
        return markdown_content

    # 简单验证是否是有效的HTML（至少包含标签）
    if not re.search(r'<[^>]+>', html_result):
        logger.warning("转换结果不包含HTML标签，可能是纯文本")

    return html_result


def protect_plantuml_blocks(markdown_content: str) -> Tuple[str, List[str]]:
    """保护 PlantUML fenced code 块，避免 markdown2html 破坏换行。"""
    plantuml_blocks: List[str] = []
    plantuml_placeholder_start = "<!--PLANTUML_BLOCK_{}_START-->"
    plantuml_placeholder_end = "<!--PLANTUML_BLOCK_{}_END-->"

    def _save(match: "re.Match[str]") -> str:
        idx = len(plantuml_blocks)
        plantuml_blocks.append(match.group(1) or "")
        return plantuml_placeholder_start.format(idx) + plantuml_placeholder_end.format(idx)

    plantuml_pattern = r"```\s*plantuml\s*(?:\r?\n)?(.*?)```"
    protected = re.sub(plantuml_pattern, _save, markdown_content, flags=re.DOTALL)
    return protected, plantuml_blocks


def restore_plantuml_blocks(html_result: str, plantuml_blocks: List[str]) -> str:
    """将 markdown2html 输出中的占位符替换回正确 HTML 结构。"""
    if not html_result:
        return html_result

    import html as _html

    plantuml_placeholder_start = "<!--PLANTUML_BLOCK_{}_START-->"
    plantuml_placeholder_end = "<!--PLANTUML_BLOCK_{}_END-->"

    t = html_result
    for idx, plantuml_code in enumerate(plantuml_blocks):
        escaped_code = _html.escape(plantuml_code or "")
        # 将字面量 \n（反斜杠+n）替换为 HTML 实体 &#92;n，避免 iCenter 后端误解析为真实换行
        escaped_code = escaped_code.replace("\\n", "&#92;n")
        plantuml_html = (
            f'<pre><code class="language-plantuml">{escaped_code}</code></pre>'
        )
        start_marker = plantuml_placeholder_start.format(idx)
        end_marker = plantuml_placeholder_end.format(idx)

        raw_pattern = re.escape(start_marker) + r".*?" + re.escape(end_marker)
        t = re.sub(raw_pattern, plantuml_html, t, flags=re.DOTALL)

        escaped_start_marker = _html.escape(start_marker)
        escaped_end_marker = _html.escape(end_marker)
        escaped_pattern = (
            re.escape(escaped_start_marker) + r".*?" + re.escape(escaped_end_marker)
        )
        t = re.sub(escaped_pattern, plantuml_html, t, flags=re.DOTALL)

    return t


def fallback_markdown_to_html(markdown_content: str) -> str:
    """API 不可用时的兜底：使用本地 markdown 库或最简转换。"""
    markdown_content = markdown_content or ""
    try:
        import markdown as _markdown_lib  # type: ignore

        return _markdown_lib.markdown(markdown_content, extensions=["extra", "nl2br"])
    except Exception:
        import html as _html

        escaped = _html.escape(markdown_content)
        return f"<div>{escaped.replace(chr(10), '<br>')}</div>"


def detect_content_type(content: str) -> str:
    """
    检测内容是 Markdown 还是 HTML。

    返回: 'html' 或 'markdown'

    检测规则:
    - 包含 < 和 > 且看起来像HTML标签 → HTML
    - 包含 # (标题)、** (粗体)、- (列表) 等Markdown特征 → Markdown
    - 都不明显 → 默认为 Markdown(保守策略)
    """
    if not content or not content.strip():
        return 'markdown'

    # HTML检测：查找常见的HTML标签
    html_patterns = [
        r'<[a-zA-Z][^>]*>',  # 任何HTML标签
        r'</[a-zA-Z]+>',     # 闭合标签
    ]

    has_html_tags = any(re.search(pattern, content) for pattern in html_patterns)

    # Markdown检测：查找常见Markdown语法
    md_patterns = [
        r'^#{1,6}\s+',       # 标题 (# H1)
        r'\*\*[^*]+\*\*',    # 粗体 (**text**)
        r'\*[^*]+\*',        # 斜体 (*text*)
        r'`[^`]+`',          # 行内代码
        r'^\s*[-*+]\s+',     # 无序列表
        r'^\s*\d+\.\s+',     # 有序列表
        r'\[([^\]]+)\]\([^)]+\)',  # 链接 [text](url)
        r'^>{1,}\s+',        # 引用
        r'^---+$',           # 水平线
    ]

    has_md_syntax = any(re.search(pattern, content, re.MULTILINE) for pattern in md_patterns)

    # 决策逻辑
    if has_html_tags and not has_md_syntax:
        return 'html'
    elif has_md_syntax and not has_html_tags:
        return 'markdown'
    elif has_html_tags and has_md_syntax:
        # 两者都有，可能是混合内容，优先当作HTML(因为用户可能手动写了HTML标签)
        # 但如果Markdown特征更明显，仍视为Markdown
        md_score = sum(1 for pattern in md_patterns if re.search(pattern, content, re.MULTILINE))
        html_score = len(re.findall(r'<[^>]+>', content))

        # 如果HTML标签数量明显多于Markdown特征，视为HTML
        if html_score > md_score * 2:
            return 'html'
        else:
            return 'markdown'
    else:
        # 都没有明显特征，检查是否包含大量纯文本
        # 如果是纯文本且没有特殊格式，视为Markdown(NL2BR会处理换行)
        return 'markdown'


def build_content_from_args(args: argparse.Namespace) -> Tuple[str, bool]:
    """
    返回 (content, content_is_html)。

    v3.0.0变更：
    - 优先支持 --html-file 参数（HTML直传模式）
    - --content-file 现在也支持直接读取HTML文件
    - --content-html 参数仍然可用，但现在默认就是HTML直传
    - ⭐ 新增：智能检测内容类型，避免误判

    优先级：
    1. --html-file > --content-file > --content
    2. 如果文件首行是URL，则自动解析并设置 --page-url
    3. 对于--content-file，先按扩展名判断，再用内容检测验证
    """
    # --html-file 参数支持（v3.0.0）
    if hasattr(args, 'html_file') and args.html_file:
        page_url_from_file, html_content = parse_html_export_file(args.html_file)
        if args.page_url is None and page_url_from_file is not None:
            args.page_url = page_url_from_file
        return html_content, True  # is_html=True

    if not args.content and not args.content_file:
        raise ValueError("缺少内容：请提供 --content、--content-file 或 --html-file")

    content = ""
    page_url_from_file: Optional[str] = None
    detected_as_html = False

    if args.content_file:
        file_path = Path(args.content_file)
        file_ext = file_path.suffix.lower()

        if file_ext in ['.html', '.htm']:
            # HTML文件，直接读取
            page_url_from_file, content = parse_html_export_file(args.content_file)
            detected_as_html = True
        elif file_ext in ['.md', '.markdown']:
            # MD文件，解析Markdown
            page_url_from_file, content = parse_markdown_export_file(args.content_file)
            detected_as_html = False
        else:
            # 未知扩展名，尝试读取并用内容检测
            raw_text = file_path.read_text(encoding='utf-8')
            # 尝试作为HTML解析
            try:
                page_url_from_file, content = parse_html_export_file(args.content_file)
                detected_type = detect_content_type(content)
                detected_as_html = (detected_type == 'html')
            except Exception:
                # 失败则回退到Markdown
                page_url_from_file, content = parse_markdown_export_file(args.content_file)
                detected_as_html = False

    if args.page_url is None and page_url_from_file is not None:
        args.page_url = page_url_from_file

    if args.content is not None and args.content != "":
        content = args.content
        # 对于--content参数，使用内容检测
        if not hasattr(args, 'content_html') or not args.content_html:
            detected_type = detect_content_type(content)
            detected_as_html = (detected_type == 'html')

    # 最终决策：用户显式指定 > 文件扩展名/内容检测 > 默认False
    if hasattr(args, 'html_file') and args.html_file:
        content_is_html = True
    elif hasattr(args, 'content_html') and args.content_html:
        # 用户明确说是HTML，相信用户
        content_is_html = True
    elif hasattr(args, 'content_file') and args.content_file:
        # 文件扩展名优先级高于内容检测
        file_ext = Path(args.content_file).suffix.lower()
        if file_ext in ['.html', '.htm']:
            content_is_html = True
        elif file_ext in ['.md', '.markdown']:
            content_is_html = False
        else:
            # 未知扩展名，使用检测结果
            content_is_html = detected_as_html
    else:
        # --content参数，使用检测结果
        content_is_html = detected_as_html

    return content, content_is_html


def parse_html_export_file(content_file: str) -> Tuple[Optional[str], str]:
    """
    解析本地 HTML 文件：
    - 首行：页面 URL（可选）
    - 其余：HTML正文

    这是 safe_page_edit.py export 动作的逆操作。
    """
    raw = Path(content_file).read_text(encoding="utf-8")
    raw = raw.lstrip("\ufeff")

    lines = raw.splitlines()
    if not lines:
        return None, ""

    first_line = (lines[0] or "").strip()
    page_url: Optional[str] = None
    content_start_idx = 0

    # 检查首行是否是URL
    if first_line.startswith("http://") or first_line.startswith("https://"):
        page_url = first_line
        # 跳过URL后的空行
        content_start_idx = 1
        while content_start_idx < len(lines) and (lines[content_start_idx] or "").strip() == "":
            content_start_idx += 1

    html_content = "\n".join(lines[content_start_idx:]).strip()
    return page_url, html_content


def parse_target_page(args: argparse.Namespace) -> Tuple[str, str]:
    """
    解析目标 (space_id, page_id)：
    - 优先：--page-url
    - 再：--space-id + --page-id
    """
    if args.page_url:
        sid, pid = parse_space_page_url(args.page_url)
        if sid and pid:
            return sid, pid
        raise ValueError(f"无法从 --page-url 解析 spaceId/pageId: {args.page_url}")

    if args.space_id and args.page_id:
        return args.space_id, args.page_id

    raise ValueError("请提供 --page-url，或同时提供 --space-id 和 --page-id")


def update_page_content_via_mcp_api(
    *,
    space_id: str,
    page_id: str,
    user_id: str,
    token: str,
    new_html_content: str,
    desired_title: str,
) -> Dict[str, Any]:
    """
    使用 MCP 同款 updateContent 接口更新页面内容。
    """
    headers = build_headers(user_id, token)
    url = UPDATE_CONTENT_URL_TEMPLATE.format(page_id=page_id)
    params = {
        "environmentType": "browser",
        "spaceId": space_id,
    }
    body = {
        "docType": "HTML",
        "title": desired_title,
        "text": new_html_content or "",
    }

    try:
        resp = requests.post(url, params=params, json=body, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return {
            "status": "error",
            "message": f"页面内容更新失败: {exc}",
        }

    code = (data.get("code") or {}).get("code")
    if code != "0000":
        return {
            "status": "error",
            "message": f"页面内容更新失败: {(data.get('code') or {}).get('msgId', '未知错误')}",
            "raw": data,
        }

    return {
        "status": "success",
        "message": "页面内容更新成功",
        "space_id": space_id,
        "page_id": page_id,
        "title": desired_title,
        "update_result": data.get("bo"),
    }


def update_chapter_content_via_mcp_api(
    *,
    space_id: str,
    page_id: str,
    user_id: str,
    token: str,
    chapter_name: str,
    chapter_html: str,
    position: int,
) -> Dict[str, Any]:
    """按章节标题更新章节内容（MCP 同款 updateChapter）。"""
    headers = build_headers(user_id, token)
    params = {"spaceId": space_id, "contentId": page_id}
    body = {
        "chapterName": chapter_name,
        "updateContent": chapter_html or "",
        "dataType": "HTML",
        "position": position,
    }
    try:
        resp = requests.post(
            UPDATE_CHAPTER_URL,
            params=params,
            json=body,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return {"status": "error", "message": f"章节更新失败: {exc}"}

    code = (data.get("code") or {}).get("code")
    if code != "0000":
        return {
            "status": "error",
            "message": f"章节更新失败: {(data.get('code') or {}).get('msgId', '未知错误')}",
            "raw": data,
        }
    return {
        "status": "success",
        "message": "章节内容更新成功",
        "space_id": space_id,
        "page_id": page_id,
        "chapter_name": chapter_name,
        "position": position,
        "update_result": data.get("bo"),
    }


def query_block_content(
    *,
    space_id: str,
    page_id: str,
    block_id: str,
    user_id: str,
    token: str,
) -> Dict[str, Any]:
    """查询 block 内容（仅 body）。"""
    headers = build_headers(user_id, token)
    body = {
        "spaceId": space_id,
        "pageId": page_id,
        "blockId": block_id,
        "onlyBody": True,
        "contentType": "HTML",
    }
    try:
        resp = requests.post(
            QUERY_BLOCK_CONTENT_URL,
            json=body,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return {"status": "error", "message": f"查询 block 内容失败: {exc}"}

    code = (data.get("code") or {}).get("code")
    if code != "0000":
        return {
            "status": "error",
            "message": f"查询 block 内容失败: {(data.get('code') or {}).get('msgId', '未知错误')}",
            "raw": data,
        }
    bo = data.get("bo") or {}
    return {"status": "success", "body": bo.get("body") or "", "raw": data}


def update_block_content_via_mcp_api(
    *,
    space_id: str,
    page_id: str,
    block_id: str,
    block_html: str,
    user_id: str,
    token: str,
) -> Dict[str, Any]:
    """更新指定 block 内容（MCP 同款 update_block_content）。"""
    headers = build_headers(user_id, token)
    url = UPDATE_BLOCK_CONTENT_URL_TEMPLATE.format(page_id=page_id)
    params = {"spaceId": space_id, "environmentType": "browser"}
    body = {
        "targetBlockId": block_id,
        "targetBlockTags": [],
        "targetBlockTitle": "",
        "block": {"body": block_html or ""},
    }
    try:
        resp = requests.put(url, params=params, json=body, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return {"status": "error", "message": f"更新 block 内容失败: {exc}"}

    code = (data.get("code") or {}).get("code")
    if code != "0000":
        return {
            "status": "error",
            "message": f"更新 block 内容失败: {(data.get('code') or {}).get('msgId', '未知错误')}",
            "raw": data,
        }
    return {
        "status": "success",
        "message": "表格单元格更新成功",
        "space_id": space_id,
        "page_id": page_id,
        "block_id": block_id,
        "update_result": data.get("bo"),
    }


def update_table_cell_html(
    block_html: str,
    row_index: int,
    col_index: int,
    new_cell_html: str,
    cell_value_is_html: bool,
) -> Dict[str, Any]:
    """在 block HTML 里更新指定表格单元格。"""
    soup = BeautifulSoup(block_html or "", "html.parser")
    table = soup.find("table")
    if table is None:
        return {"status": "error", "message": "目标 block 中未找到 table 元素"}

    rows = table.find_all("tr")
    if row_index < 0 or row_index >= len(rows):
        return {"status": "error", "message": f"行索引越界：row_index={row_index}, 总行数={len(rows)}"}

    cells = rows[row_index].find_all(["td", "th"])
    if col_index < 0 or col_index >= len(cells):
        return {"status": "error", "message": f"列索引越界：col_index={col_index}, 当前行单元格数={len(cells)}"}

    target_cell = cells[col_index]
    target_cell.clear()
    if cell_value_is_html:
        fragment = BeautifulSoup(new_cell_html or "", "html.parser")
        for node in fragment.contents:
            target_cell.append(node)
    else:
        target_cell.string = new_cell_html or ""

    return {"status": "success", "html": str(soup)}


def _json_or_text_error(message: str, json_output: bool) -> None:
    if json_output:
        _json_mode_log(f"错误: {message}")
        print(json.dumps({"status": "error", "message": message}, ensure_ascii=False))
        return
    print(f"❌ {message}")


def _build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="编辑指定 iCenter 页面：支持 HTML直传模式（v3.0.0推荐）和 Markdown 转换模式。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
⚠️ **重要提示** (v3.0.0)：
- 对于包含宏、PlantUML或复杂格式的页面，请使用 --html-file 参数
- Markdown 双向转换可能导致格式丢失

示例：
【HTML直传模式】（推荐，100%保真）
1) 导出页面HTML：
   python3 safe_page_edit.py --action export --url "<URL>" --output-dir ./my-page

2) 编辑 HTML 文件后回写：
   python3 studio_space_page_edit.py --html-file ./my-page/page.html

3) 或直接提供HTML内容：
   python3 studio_space_page_edit.py --page-url "<URL>" --content "<p>HTML</p>" --content-html

【Markdown模式】（简单文本页面）
1) 从本地 md 文件（首行页面URL）回写正文
   python3 studio_space_page_edit.py --content-file ./page.md

2) 直接更新（Markdown）
   python3 studio_space_page_edit.py --page-url "<页面URL>" --content "<markdown>"

3) 仅更新章节内容
   python3 studio_space_page_edit.py --page-url "<页面URL>" --mode chapter --chapter-name "1. 背景" --content "## 1. 背景\n更新内容"

5) 更新表格单元格
  python3 studio_space_page_edit.py --page-url "<页面URL>" --mode table-cell --block-id "<blockId>" --row-index 1 --col-index 2 --cell-value "新值"
""",
    )


def main() -> None:
    configure_stdio_utf8()
    parser = _build_parser()
    parser.add_argument("--page-url", type=str, default=None, help="目标页面 URL（与 --space-id/--page-id 二选一）")
    parser.add_argument("--space-id", type=str, default=None, help="目标空间 ID（与 --page-url 二选一）")
    parser.add_argument("--page-id", type=str, default=None, help="目标页面 ID（与 --page-url 二选一）")
    parser.add_argument(
        "--mode",
        type=str,
        choices=["page", "chapter", "table-cell"],
        default="page",
        help="更新模式：page(整页)、chapter(章节)、table-cell(表格单元格)",
    )
    parser.add_argument("--title", type=str, default=None, help="可选：更新后的页面标题，不传则沿用原标题")
    parser.add_argument("--content", type=str, default=None, help="页面内容：Markdown 或 HTML（见 --content-html）")
    parser.add_argument("--content-file", type=str, default=None, help="本地文件（支持.md/.html，首行可以是页面URL）")
    parser.add_argument("--html-file", type=str, default=None, help="⭐ v3.0.0新增: 本地HTML文件（推荐用于复杂格式页面）")
    parser.add_argument("--content-html", action="store_true", help="--content/--content-file 为 HTML，跳过 Markdown->HTML 转换")
    parser.add_argument("--chapter-name", type=str, default=None, help="章节名称（mode=chapter 时必填）")
    parser.add_argument("--position", type=int, default=1, help="同名章节位置（mode=chapter 时可选，默认1）")
    parser.add_argument("--block-id", type=str, default=None, help="block ID（mode=table-cell 时必填）")
    parser.add_argument("--row-index", type=int, default=None, help="行索引（0开始，mode=table-cell 时必填）")
    parser.add_argument("--col-index", type=int, default=None, help="列索引（0开始，mode=table-cell 时必填）")
    parser.add_argument("--cell-value", type=str, default=None, help="新单元格值（mode=table-cell 时必填）")
    parser.add_argument("--cell-value-html", action="store_true", help="单元格值按 HTML 解析（mode=table-cell）")
    parser.add_argument("--json-output", action="store_true", help="JSON 格式输出")
    args = parser.parse_args()
    json_output = bool(args.json_output)

    try:
        user_id, token = get_user_and_token()
    except SystemExit:
        if json_output:
            print(json.dumps({"status": "error", "message": "无法获取认证信息"}, ensure_ascii=False))
        else:
            print("❌ 无法获取认证信息")
        return

    if not user_id or not token:
        _json_or_text_error(
            "无法获取用户账号和Token，请确保已设置 coclaw_empno 和 coclaw_token 环境变量",
            json_output,
        )
        return

    space_id, page_id = parse_target_page(args)
    mode = args.mode

    desired_title = ""
    if mode == "page":
        markdown_or_html, content_is_html = build_content_from_args(args)

    if mode == "page" and not args.title:
        if json_output:
            _json_mode_log(f"获取页面标题：space_id={space_id} page_id={page_id}")
        page_result = fetch_page_title(space_id, page_id, user_id, token)
        if page_result.get("status") != "success":
            _json_or_text_error(page_result.get("message", "获取页面内容失败"), json_output)
            return
        desired_title = page_result.get("title") or ""
    elif mode == "page":
        desired_title = args.title.strip()

    if mode == "page":
        if content_is_html:
            new_html = markdown_or_html
        else:
            new_html = markdown_to_html(markdown_or_html, user_id, token)
        new_html = html_add_ai_attr(new_html)
        if json_output:
            _json_mode_log("按整页模式回写...")
        result = update_page_content_via_mcp_api(
            space_id=space_id,
            page_id=page_id,
            user_id=user_id,
            token=token,
            new_html_content=new_html,
            desired_title=desired_title,
        )
    elif mode == "chapter":
        if not args.chapter_name:
            _json_or_text_error("mode=chapter 时必须提供 --chapter-name", json_output)
            return
        chapter_markdown_or_html, content_is_html = build_content_from_args(args)
        if content_is_html:
            chapter_html = chapter_markdown_or_html
        else:
            chapter_html = markdown_to_html(chapter_markdown_or_html, user_id, token)
        chapter_html = html_add_ai_attr(chapter_html)
        if json_output:
            _json_mode_log("按章节模式回写...")
        result = update_chapter_content_via_mcp_api(
            space_id=space_id,
            page_id=page_id,
            user_id=user_id,
            token=token,
            chapter_name=args.chapter_name.strip(),
            chapter_html=chapter_html,
            position=max(1, args.position),
        )
    else:
        if not args.block_id:
            _json_or_text_error("mode=table-cell 时必须提供 --block-id", json_output)
            return
        if args.row_index is None or args.col_index is None:
            _json_or_text_error("mode=table-cell 时必须提供 --row-index 与 --col-index", json_output)
            return
        if args.cell_value is None:
            _json_or_text_error("mode=table-cell 时必须提供 --cell-value", json_output)
            return
        if json_output:
            _json_mode_log("查询目标表格 block...")
        block_result = query_block_content(
            space_id=space_id,
            page_id=page_id,
            block_id=args.block_id,
            user_id=user_id,
            token=token,
        )
        if block_result.get("status") != "success":
            _json_or_text_error(block_result.get("message", "查询 block 内容失败"), json_output)
            return
        changed = update_table_cell_html(
            block_html=block_result.get("body") or "",
            row_index=args.row_index,
            col_index=args.col_index,
            new_cell_html=args.cell_value,
            cell_value_is_html=bool(args.cell_value_html),
        )
        if changed.get("status") != "success":
            _json_or_text_error(changed.get("message", "更新单元格失败"), json_output)
            return
        changed_html = html_add_ai_attr(changed.get("html") or "")
        if json_output:
            _json_mode_log("回写目标 block...")
        result = update_block_content_via_mcp_api(
            space_id=space_id,
            page_id=page_id,
            block_id=args.block_id,
            block_html=changed_html,
            user_id=user_id,
            token=token,
        )

    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result.get("status") == "success":
            print(f"✅ {result.get('message', '页面内容更新成功')}")
            print(f"   空间ID: {space_id}")
            print(f"   页面ID: {page_id}")
            print(f"   标题: {desired_title}")
        else:
            print(f"❌ {result.get('message', '页面内容更新失败')}")


if __name__ == "__main__":
    main()

