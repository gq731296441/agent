#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
安全页面编辑模块 - 统一页面编辑入口

核心思路：
1. 所有页面编辑场景均通过本脚本处理
2. 用户输入Markdown时自动转换为HTML后再编辑
3. 导出时获取原始HTML，回写时直接使用HTML，避免格式丢失

适用场景：
- ✅ 所有页面编辑需求（统一入口）
- ✅ 用户提供Markdown内容（自动转HTML）
- ✅ 页面包含宏（PlantUML、表格、提醒等）
- ✅ 页面内容较多（避免模型截断）
- ✅ 需要精确控制格式的场合

使用方法：
【方式一】导出→编辑→回写（推荐用于复杂页面）
1) 导出页面（保留 HTML）
   python3 safe_page_edit.py --action export --url "<页面URL>" --output-dir ./my-page

2) 编辑导出的 HTML 文件（推荐使用 VSCode、Sublime Text 等编辑器）

3) 回写页面
   python3 safe_page_edit.py --action import --url "<页面URL>" --html-file ./my-page/page.html

【方式二】直接提供Markdown内容（自动转HTML后编辑）
   python3 safe_page_edit.py --action edit \
     --url "<页面URL>" \
     --content "# 标题\n这是Markdown内容"

【方式三】从本地Markdown文件读取（自动转HTML）
   python3 safe_page_edit.py --action edit \
     --url "<页面URL>" \
     --md-file ./page.md
"""

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# 让脚本可在任意工作目录下被执行
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

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


BASE_URL = ICENTER_API_BASE
FETCH_CONTENT_URL_TEMPLATE = f"{BASE_URL}/zte-rdcloud-space-content/content/{{page_id}}"
UPDATE_CONTENT_URL_TEMPLATE = (
    f"{BASE_URL}/zte-rdcloud-space-content/content/{{page_id}}/updateContent"
)
MARKDOWN2HTML_URL = f"{BASE_URL}/zte-rdcloud-space-wconvert/markdown2html"


def get_user_and_token() -> Tuple[str, str]:
    """返回 (user_id, token)，非 strict。"""
    return _get_user_and_token(strict=False)


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


def parse_space_page_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    """从页面 URL 中解析 spaceId/pageId。"""
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


# ---------------------------------------------------------------------------
# Markdown转HTML功能（复用studio_space_page_edit.py中的成熟实现）
# ---------------------------------------------------------------------------


def protect_plantuml_blocks(markdown_content: str) -> Tuple[str, List[str]]:
    """保护PlantUML fenced code块，避免markdown2html破坏换行。"""
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
    """将markdown2html输出中的占位符替换回正确的HTML结构。"""
    if not html_result:
        return html_result

    import html as _html

    plantuml_placeholder_start = "<!--PLANTUML_BLOCK_{}_START-->"
    plantuml_placeholder_end = "<!--PLANTUML_BLOCK_{}_END-->"

    t = html_result
    for idx, plantuml_code in enumerate(plantuml_blocks):
        escaped_code = _html.escape(plantuml_code or "")
        # 将字面量\n（反斜杠+n）替换为HTML实体&#92;n，避免iCenter后端误解析为真实换行
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
    """API不可用时的兜底：使用本地markdown库或最简转换。"""
    markdown_content = markdown_content or ""
    try:
        import markdown as _markdown_lib  # type: ignore

        return _markdown_lib.markdown(markdown_content, extensions=["extra", "nl2br"])
    except Exception:
        import html as _html

        escaped = _html.escape(markdown_content)
        return f"<div>{escaped.replace(chr(10), '<br>')}</div>"


def markdown_to_html(markdown_content: str, user_id: str, token: str) -> str:
    """
    调用iCenter markdown2html服务转换Markdown为HTML。

    说明：markdown2html可能会破坏PlantUML代码块的换行，所以这里做了保护/恢复。

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


def detect_content_type(content: str) -> str:
    """
    检测内容是Markdown还是HTML。

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
        r'^#{1,6}\s+',       # 标题
        r'\*\*[^*]+\*\*',    # 粗体
        r'\*[^*]+\*',        # 斜体
        r'`[^`]+`',          # 行内代码
        r'^\s*[-*+]\s+',     # 无序列表
        r'^\s*\d+\.\s+',     # 有序列表
        r'\[([^\]]+)\]\([^)]+\)',  # 链接
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
        # 两者都有，计算特征分数
        md_score = sum(1 for pattern in md_patterns if re.search(pattern, content, re.MULTILINE))
        html_score = len(re.findall(r'<[^>]+>', content))

        # 如果HTML标签数量明显多于Markdown特征，视为HTML
        if html_score > md_score * 2:
            return 'html'
        else:
            return 'markdown'
    else:
        # 都没有明显特征，默认Markdown
        return 'markdown'


def parse_markdown_or_html_file(content_file: str) -> Tuple[str, bool]:
    """
    解析本地文件，返回 (content, is_html)。

    Args:
        content_file: 文件路径

    Returns:
        Tuple[str, bool]: (内容，是否是HTML)
    """
    file_path = Path(content_file)
    if not file_path.exists():
        raise FileNotFoundError(f"文件不存在：{content_file}")

    raw = file_path.read_text(encoding='utf-8').lstrip("\ufeff")

    # 检查文件扩展名
    file_ext = file_path.suffix.lower()
    if file_ext in ['.html', '.htm']:
        # HTML文件，可能需要移除URL注释行
        lines = raw.splitlines()
        if lines and lines[0].startswith('<!-- Page URL:'):
            raw = '\n'.join(lines[1:])
        return raw.strip(), True
    elif file_ext in ['.md', '.markdown']:
        # Markdown文件，可能需要移除URL行
        lines = raw.splitlines()
        if lines and (lines[0].startswith('http://') or lines[0].startswith('https://')):
            raw = '\n'.join(lines[1:])
        return raw.strip(), False
    else:
        # 未知扩展名，使用内容检测
        content = raw.strip()
        detected_type = detect_content_type(content)
        return content, (detected_type == 'html')


def fetch_page_html(space_id: str, page_id: str, user_id: str, token: str) -> Dict[str, Any]:
    """获取页面原始 HTML 内容。"""
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
        return {"status": "error", "message": f"获取页面失败：{exc}"}

    code = (data.get("code") or {}).get("code")
    if code != "0000":
        return {
            "status": "error",
            "message": f"获取页面失败：{(data.get('code') or {}).get('msgId', '未知错误')}",
            "raw": data,
        }

    bo = data.get("bo") or {}
    return {
        "status": "success",
        "title": bo.get("title") or "",
        "html_content": bo.get("contentBody") or "",
        "space_id": space_id,
        "page_id": page_id,
    }


def update_page_html(
    space_id: str,
    page_id: str,
    user_id: str,
    token: str,
    html_content: str,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """更新页面 HTML 内容（不经过 Markdown 转换）。"""
    headers = build_headers(user_id, token)
    url = UPDATE_CONTENT_URL_TEMPLATE.format(page_id=page_id)
    params = {
        "environmentType": "browser",
        "spaceId": space_id,
    }
    body = {
        "docType": "HTML",
        "title": title or "",
        "text": html_content or "",
    }

    try:
        resp = requests.post(url, params=params, json=body, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return {"status": "error", "message": f"更新页面失败：{exc}"}

    code = (data.get("code") or {}).get("code")
    if code != "0000":
        return {
            "status": "error",
            "message": f"更新页面失败：{(data.get('code') or {}).get('msgId', '未知错误')}",
            "raw": data,
        }

    return {
        "status": "success",
        "message": "页面已更新（HTML 直传模式，无格式损失）",
        "space_id": space_id,
        "page_id": page_id,
        "title": title or "",
    }


def sanitize_filename(name: str) -> str:
    """将标题转换为安全文件名。"""
    safe = re.sub(r'[\\/:*?"<>|]', "_", name or "")
    safe = re.sub(r"\s+", "_", safe).strip("_. ")
    return safe[:100] if safe else "untitled"


def export_action(args: argparse.Namespace) -> Dict[str, Any]:
    """导出页面原始 HTML。"""
    if not args.url:
        return {"status": "error", "message": "请提供 --url 参数"}

    parsed = parse_space_page_url(args.url)
    if not parsed:
        return {"status": "error", "message": f"无法从 URL 解析：{args.url}"}

    space_id, page_id = parsed
    user_id, token = get_user_and_token()

    if not user_id or not token:
        return {"status": "error", "message": "无法获取认证信息"}

    result = fetch_page_html(space_id, page_id, user_id, token)
    if result.get("status") != "success":
        return result

    # 保存为 HTML 文件
    output_dir = Path(args.output_dir) if args.output_dir else Path(".")
    output_dir.mkdir(parents=True, exist_ok=True)

    html_file = output_dir / f"{sanitize_filename(result['title'])}.html"
    metadata_file = output_dir / "metadata.json"

    # 写入 HTML 文件
    html_file.write_text(result["html_content"], encoding="utf-8")

    # 写入元数据
    metadata = {
        "page_url": args.url,
        "space_id": space_id,
        "page_id": page_id,
        "title": result["title"],
        "exported_at": __import__("datetime").datetime.now().isoformat(),
    }
    metadata_file.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "status": "success",
        "message": f"页面已导出为 HTML：{html_file}",
        "html_file": str(html_file),
        "metadata_file": str(metadata_file),
        "title": result["title"],
    }


def import_action(args: argparse.Namespace) -> Dict[str, Any]:
    """导入 HTML 并更新页面。"""
    if not args.url:
        return {"status": "error", "message": "请提供 --url 参数"}
    if not args.html_file:
        return {"status": "error", "message": "请提供 --html-file 参数"}

    parsed = parse_space_page_url(args.url)
    if not parsed:
        return {"status": "error", "message": f"无法从 URL 解析：{args.url}"}

    space_id, page_id = parsed
    user_id, token = get_user_and_token()

    if not user_id or not token:
        return {"status": "error", "message": "无法获取认证信息"}

    # 读取 HTML 文件
    html_file = Path(args.html_file)
    if not html_file.exists():
        return {"status": "error", "message": f"HTML 文件不存在：{html_file}"}

    html_content = html_file.read_text(encoding="utf-8")

    # 可选：从 metadata.json 读取标题
    metadata_file = html_file.parent / "metadata.json"
    title = None
    if metadata_file.exists():
        try:
            metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
            title = metadata.get("title")
        except Exception:
            pass

    # 如果提供了 --title 参数，优先使用
    if hasattr(args, "title") and args.title:
        title = args.title

    result = update_page_html(space_id, page_id, user_id, token, html_content, title)
    return result


def edit_action(args: argparse.Namespace) -> Dict[str, Any]:
    """
    编辑页面：支持用户提供Markdown内容或MD文件，自动转换为HTML后更新页面。

    这是v3.0新增的统一编辑入口，所有用户主动提供的文本内容都会经过此流程:
    1. 检测内容类型（Markdown或HTML）
    2. 如果是Markdown，转换为HTML
    3. 直接调用update_page_html更新页面
    """
    if not args.url:
        return {"status": "error", "message": "请提供 --url 参数"}

    # 检查是否提供了内容
    has_content = (hasattr(args, 'content') and args.content) or \
                  (hasattr(args, 'md_file') and args.md_file) or \
                  (hasattr(args, 'html_file') and args.html_file)

    if not has_content:
        return {"status": "error", "message": "请提供 --content、--md-file 或 --html-file 参数"}

    parsed = parse_space_page_url(args.url)
    if not parsed:
        return {"status": "error", "message": f"无法从 URL 解析：{args.url}"}

    space_id, page_id = parsed
    user_id, token = get_user_and_token()

    if not user_id or not token:
        return {"status": "error", "message": "无法获取认证信息"}

    # 获取HTML内容和标题
    html_content = ""
    title = getattr(args, 'title', None)

    # 情况1: 用户提供HTML文件
    if hasattr(args, 'html_file') and args.html_file:
        html_file = Path(args.html_file)
        if not html_file.exists():
            return {"status": "error", "message": f"HTML文件不存在：{html_file}"}
        html_content = html_file.read_text(encoding="utf-8")
        logger.info(f"从HTML文件读取内容：{args.html_file}")

    # 情况2: 用户提供Markdown文件
    elif hasattr(args, 'md_file') and args.md_file:
        md_file = Path(args.md_file)
        if not md_file.exists():
            return {"status": "error", "message": f"Markdown文件不存在：{md_file}"}

        logger.info(f"从Markdown文件读取内容：{args.md_file}")
        md_content, _ = parse_markdown_or_html_file(args.md_file)

        # 转换为HTML
        logger.debug("正在将Markdown转换为HTML...")
        html_content = markdown_to_html(md_content, user_id, token)
        logger.info(f"Markdown转换完成，HTML长度={len(html_content)}字符")

    # 情况3: 用户直接提供Markdown/HTML内容字符串
    elif hasattr(args, 'content') and args.content:
        content = args.content
        detected_type = detect_content_type(content)
        logger.info(f"检测到内容类型为：{detected_type}")

        if detected_type == 'markdown':
            logger.debug("正在将Markdown转换为HTML...")
            html_content = markdown_to_html(content, user_id, token)
            logger.info(f"Markdown转换完成，HTML长度={len(html_content)}字符")
        else:
            logger.info("内容为HTML格式，直接使用")
            html_content = content

    # 获取页面标题（如果未提供）
    if not title:
        title_result = fetch_page_title(space_id, page_id, user_id, token)
        if title_result.get("status") == "success":
            title = title_result.get("title")
        else:
            logger.warning(f"获取页面标题失败：{title_result.get('message')}")
            title = ""

    # 更新页面
    logger.info(f"正在更新页面：space_id={space_id}, page_id={page_id}")
    result = update_page_html(space_id, page_id, user_id, token, html_content, title)

    if result.get("status") == "success":
        logger.info("页面更新成功")
    else:
        logger.error(f"页面更新失败：{result.get('message')}")

    return result


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
        return {"status": "error", "message": f"获取页面标题失败：{exc}"}

    code = (data.get("code") or {}).get("code")
    if code != "0000":
        return {
            "status": "error",
            "message": f"获取页面标题失败：{(data.get('code') or {}).get('msgId', '未知错误')}",
            "raw": data,
        }

    bo = data.get("bo") or {}
    return {"status": "success", "title": bo.get("title") or ""}


def main() -> None:
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(
        description="安全页面编辑 - 统一页面编辑入口（支持Markdown自动转HTML）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
【方式一】导出→编辑→回写（推荐用于复杂页面或批量编辑）
1) 导出页面原始 HTML
   python3 safe_page_edit.py --action export --url "<页面URL>" --output-dir ./my-page

2) 使用任意 HTML 编辑器编辑文件（推荐 VSCode、Sublime Text）

3) 回写页面
   python3 safe_page_edit.py --action import --url "<页面URL>" --html-file ./my-page/page.html

【方式二】直接提供Markdown/HTML内容（快速编辑）
   python3 safe_page_edit.py --action edit \\
     --url "<页面URL>" \\
     --content "# 标题\\n这是Markdown内容"

【方式三】从本地Markdown文件读取（自动转HTML）
   python3 safe_page_edit.py --action edit \\
     --url "<页面URL>" \\
     --md-file ./page.md

【方式四】从本地HTML文件读取（直传模式）
   python3 safe_page_edit.py --action edit \\
     --url "<页面URL>" \\
     --html-file ./page.html

优势：
- ✅ 统一编辑入口，所有场景都通过本脚本处理
- ✅ Markdown自动转换为HTML，无需手动指定
- ✅ PlantUML保护机制，确保图表格式不丢失
- ✅ 100%保留原始格式（HTML直传模式）
- ✅ 详细的日志记录，便于排查问题
""",
    )
    parser.add_argument(
        "--action",
        choices=["export", "import", "edit"],
        required=True,
        help="操作类型：export(导出)、import(导入)、edit(编辑)",
    )
    parser.add_argument("--url", type=str, help="页面 URL")
    parser.add_argument("--output-dir", type=str, help="导出目录（export 时使用）")
    parser.add_argument("--html-file", type=str, help="HTML文件路径（import/edit时使用）")
    parser.add_argument("--md-file", type=str, help="Markdown文件路径（edit时使用）")
    parser.add_argument("--content", type=str, help="页面内容：Markdown或HTML（edit时使用）")
    parser.add_argument("--title", type=str, default=None, help="页面标题（可选）")
    parser.add_argument("--json-output", action="store_true", help="JSON 格式输出")

    args = parser.parse_args()
    json_output = bool(getattr(args, "json_output", False))

    if args.action == "export":
        result = export_action(args)
    elif args.action == "import":
        result = import_action(args)
    elif args.action == "edit":
        result = edit_action(args)
    else:
        result = {"status": "error", "message": f"未知操作：{args.action}"}

    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result.get("status") == "success":
            print(f"✅ {result.get('message', '操作成功')}")
            if result.get("html_file"):
                print(f"   HTML 文件：{result['html_file']}")
            if result.get("metadata_file"):
                print(f"   元数据文件：{result['metadata_file']}")
            if result.get("title"):
                print(f"   页面标题：{result['title']}")
        else:
            print(f"❌ {result.get('message', '操作失败')}")


if __name__ == "__main__":
    main()
