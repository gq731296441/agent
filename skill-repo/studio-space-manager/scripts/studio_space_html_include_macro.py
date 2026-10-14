#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=================================================================
studio-space-html-include-macro - 上传本地 HTML 并以「HTML 包含宏」保存到页面 (v1.0.0)
=================================================================
iCenter 空间「HTML 包含宏（HtmlIncludeMacro）」技能

功能：
- 调用 HtmlIncludeMacro 专用上传接口上传本地 HTML 文件
  POST {SPACE_PLUGIN_API}/file/upload/html?docId={pageId}&spaceId={spaceId}&macroId={macroId}
  表单字段：htmlFile=@本地.html
- 接口返回渲染地址（src/url）与宏元信息，构造 HtmlIncludeMacro 宏 HTML 片段
  （与前端 2.0 空间 div.ze-macro 结构一致，macro-key="HtmlIncludeMacro"）
- 将宏片段追加/挂载到目标页面正文（整页末尾、章节末尾/开头），并自动回写页面

背景：
空间页面正文无法直接写入复杂样式 HTML（会被 xss 过滤破坏格式），官方推荐将
HTML 源码保存为 .html 文件后，通过「HTML 包含宏」上传并在页面中渲染展示。
本脚本把「上传 html → 生成宏 → 回写页面」三步自动化。

关键 API：
- HTML 上传: POST {SPACE_PLUGIN_API}/file/upload/html?docId={pageId}&spaceId={spaceId}&macroId={macroId}
- 读取页面: GET .../zte-rdcloud-space-content/content/{pageId}?dataType=HTML&withMacro=true
- 回写页面: POST .../zte-rdcloud-space-content/content/{pageId}/updateContent（MCP 同款）

约束：
- 单文件不超过 100MB（本地校验并提示）
- 单页面「HTML 包含宏」数量限制为 10 个（平台限制，脚本在追加前做计数提示）
- 鉴权仅从环境变量读取：coclaw_empno、coclaw_token
- macroId / blockId / fileKey 均由脚本自动生成，无需也无法手动指定

依赖：
- Python 3.6+
- requests

用法：
  # 上传本地 html 并追加到页面末尾（推荐）
  python3 studio_space_html_include_macro.py \
    --url "https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view" \
    --html-file /path/to/page.html

  # 指定空间/页面 + 挂载到指定章节
  python3 studio_space_html_include_macro.py \
    --space-id "{spaceId}" --page-id "{pageId}" \
    --html-file /path/to/page.html \
    --attach-chapter-name "1. 背景" --attach-mode append

作者：Co-Claw Team
创建：2026-09-28
"""

import argparse
import json
import os
import re
import sys
import uuid
from typing import Any, Dict, List, Optional, Tuple

import requests
from urllib.parse import quote

# 脚本目录加入 Python 路径，便于复用同目录模块
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from system_param import (
    build_icenter_api_url,
    get_user_and_token as _get_user_and_token,
)
from _platform_utils import configure_stdio_utf8

# 复用 studio_space_attachment 的请求工具/URL 解析/路径规范化能力
try:
    from studio_space_attachment import (
        _request_json,
        parse_space_page_id_from_url as _parse_space_page_id_from_url,
        _normalize_local_file_path,
    )
except ImportError as _imp_err:  # pragma: no cover - 仅用于提升可读性
    raise SystemExit(f"无法导入 studio_space_attachment 模块：{_imp_err}")

# 复用页面读取（fetch_space_page）
try:
    from fetch_space_page import fetch_space_page as _fetch_space_page
except ImportError:
    _fetch_space_page = None


def get_auth_headers():
    """获取认证请求头"""
    emp_no, token = _get_user_and_token(strict=False)
    emp_no = (emp_no or "").strip()
    token = (token or "").strip()

    if not emp_no or not token:
        raise ValueError(
            "未找到认证信息，请确保环境变量 coclaw_empno 和 coclaw_token 已设置，如仍报错请重启 Co-Claw 服务后重试"
        )

    return {
        "Accept": "application/json, text/html, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Pragma": "no-cache",
        "User-Agent": "co-claw-skill",
        "X-Auth-Value": token,
        "X-Emp-No": emp_no,
        "X-Lang-Id": "zh_CN",
        "X-Tenant-Id": "ZTE",
    }


MAX_UPLOAD_SIZE_BYTES = 100 * 1024 * 1024
MAX_UPLOAD_SIZE_MB = 100
HTML_INCLUDE_MACRO_KEY = "HtmlIncludeMacro"
# 单页面 HtmlIncludeMacro 数量限制（平台约束，来自客服 FAQ）
MAX_HTML_INCLUDE_MACROS_PER_PAGE = 10

# HtmlIncludeMacro 专用上传接口（表单字段 htmlFile）
SPACE_PLUGIN_API = build_icenter_api_url("/zte-rdcloud-space-plugin")
SPACE_CONTENT_API = build_icenter_api_url("/zte-rdcloud-space-content")
# HtmlIncludeMacro 渲染地址前缀（NAS 直链，用于拼接 bo.urlPath）
HTML_INCLUDE_MACRO_BASE = "https://icenterapi.zte.com.cn/zte-rdcloud-space-plugin"


def get_user_and_token() -> Tuple[str, str]:
    """返回 (user_id, token)，优先从环境变量获取（非 strict）。"""
    return _get_user_and_token(strict=False)


def _json_mode_log(message: str) -> None:
    """--json 时向 stderr 打一行日志，避免污染 stdout 的纯 JSON。"""
    print(f"[studio_space_html_include_macro] {message}", file=sys.stderr)


def _new_macro_id() -> str:
    """生成宏 id（与前端类似，单次调用内唯一）。"""
    return uuid.uuid4().hex[:32]


def _new_block_id() -> str:
    """生成block id（与前端类似，单次调用内唯一）。"""
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# 1) 宏片段构造
# ---------------------------------------------------------------------------
def _escape_attr(value: str) -> str:
    """HTML 属性值转义（& < > "）。"""
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_html_include_macro_html(
        *,
        url: str,
        file_path: str = "",
        file_name: str = "",
        file_key: str = "",
        macro_id: Optional[str] = None,
        block_id: Optional[str] = None,

) -> str:
    """
    构造 HtmlIncludeMacro 宏 HTML 片段（与 iCenter 2.0 空间实际结构一致）。

    参考样例（前端保存后的真实宏结构）：
      <div tag-start="blockMacro" class="ze-macro" macro-key="HtmlIncludeMacro"
           enable-ai-create="n" enable-ai-check="n"
           macro-id="{macroId}" block-id="{blockId}"
           m-vn-id-7520-47="upload" m-file="C:\\fakepath\\xxx.html" m-mode="upload"
           m-file-name="HtmlIncludeMacro_{pageId}_{macroId}.html"
           m-page-height="1600" m-page-width="1600" selected="true"
           m-url="{nas_url}" m-file-key="{fileKey}">
        <div tag-start="block" class="ze-macro-body"><p tag-start="p"><br /></p></div>
      </div>

    参数说明：
    - macro-id：宏 ID（上传接口入参，也决定 m-file-name 后缀）
    - block-id：块 ID（前端 blockMacro 块）
    - m-url：NAS 渲染地址（上传接口返回）
    - m-file-key：NAS 文件 key（上传接口返回，可空）
    """
    mid = (macro_id or "").strip() or _new_macro_id()
    bid = (block_id or "").strip() or _new_block_id()

    attrs = [
        'tag-start="blockMacro"',
        'class="ze-macro"',
        f'macro-key="{HTML_INCLUDE_MACRO_KEY}"',
        'enable-ai-create="n"',
        'enable-ai-check="n"',
        f'macro-id="{_escape_attr(mid)}"',
        f'block-id="{_escape_attr(bid)}"',
        'm-vn-id-7520-47="upload"',
        'm-mode="upload"',
        'm-file="%s"' % _escape_attr(file_path),
        'm-file-key="%s"' % _escape_attr(file_key),
        'm-file-name="%s"' % _escape_attr(file_name),
        'm-page-height="1600"',
        'm-page-width="1600"',
        'selected="true"',
        'm-url="%s"' % _escape_attr(url or ""),
    ]

    return (
            '<div %s>'
            '<div tag-start="block" class="ze-macro-body"><p tag-start="p"><br /></p></div>'
            '</div>' % (" ".join(attrs))
    )


# ---------------------------------------------------------------------------
# 2) 上传 html + 获取可渲染 URL
# ---------------------------------------------------------------------------
def _count_existing_html_include_macros(html_content: str) -> int:
    """统计页面 HTML 中已存在的 HtmlIncludeMacro 数量（用于平台 10 个上限提示）。

    兼容两种宏结构：
    - 2.0：<div class="ze-macro" macro-key="HtmlIncludeMacro" ...>
    - 1.0：<img data-macro-key="HtmlIncludeMacro" ...>
    用 (?:data-)?macro-key 一次匹配，避免同一段被重复计数。
    """
    if not html_content:
        return 0
    return len(
        re.findall(r'(?:data-)?macro-key=["\']HtmlIncludeMacro["\']', html_content)
    )


def upload_html_file(
        space_id: str, page_id: str, local_file_path: str
) -> Dict[str, Any]:
    """
    上传本地 html 文件到空间，使用 HtmlIncludeMacro 专用上传接口，并生成宏片段。

    接口：POST {SPACE_PLUGIN_API}/file/upload/html
        ?docId={page_id}&spaceId={space_id}&macroId={macro_id}
    表单字段：htmlFile=@本地.html（multipart/form-data）

    流程：
    1. 本地校验（存在性、大小 <=100MB）
    2. 调用 file/upload/html 上传，返回渲染地址
    3. 构造 HtmlIncludeMacro 宏片段

    macroId / blockId 全部由脚本自动生成，无需也无法手动指定。
    """
    # macroId/blockId 自动生成
    resolved_macro_id = _new_macro_id()
    resolved_block_id = _new_block_id()
    result: Dict[str, Any] = {
        "status": "error",
        "message": "",
        "space_id": space_id,
        "page_id": page_id or "",
        "local_file_path": local_file_path,
        "file_name": "",
        "file_key": "",
        "block_id": resolved_block_id,
        "url": "",
        "macro_id": resolved_macro_id,
        "macro_html": "",
        "raw_upload": None,
    }

    file_path = _normalize_local_file_path(local_file_path)
    if not file_path:
        result["message"] = "上传失败：未提供本地文件路径"
        return result
    result["local_file_path"] = file_path
    if not os.path.isfile(file_path):
        result["message"] = f"上传失败：文件不存在或不是普通文件 -> {file_path}"
        return result

    file_name = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)
    result["file_name"] = file_name

    if file_size <= 0:
        result["message"] = "上传失败：文件大小为 0，不能上传空文件"
        return result
    if file_size > MAX_UPLOAD_SIZE_BYTES:
        result["message"] = (
            f"上传失败：文件「{file_name}」大小为 {file_size / 1024 / 1024:.2f}MB，"
            f"超过接口限制 {MAX_UPLOAD_SIZE_MB}MB"
        )
        return result

    # 1) 调用 HtmlIncludeMacro 专用上传接口
    if not page_id:
        result["message"] = "上传失败：缺少 pageId（docId 参数必需）"
        return result
    mid = result["macro_id"]
    url = (
        f"{SPACE_PLUGIN_API}/file/upload/html"
        f"?docId={quote(page_id, safe='')}"
        f"&spaceId={quote(space_id, safe='')}"
        f"&macroId={quote(mid, safe='')}"
    )
    headers = get_auth_headers().copy()
    headers.pop("Content-Type", None)  # multipart 由 requests 自动生成 boundary

    try:
        with open(file_path, "rb") as f:
            resp = requests.post(
                url,
                headers=headers,
                files={"htmlFile": (file_name, f, "text/html")},
                timeout=300,
            )
    except requests.exceptions.RequestException as e:
        result["message"] = f"上传失败：请求异常 - {e}"
        return result
    except OSError as e:
        result["message"] = f"上传失败：读取本地文件异常 - {e}"
        return result

    if resp.status_code != 200:
        preview = (resp.text or "").strip().replace("\n", " ")[:200]
        result["message"] = (
                f"上传失败：HTTP {resp.status_code}"
                + (f"，响应: {preview}" if preview else "")
        )
        return result

    try:
        data = resp.json()
    except ValueError:
        preview = (resp.text or "").strip().replace("\n", " ")[:200]
        result["message"] = (
                "上传失败：响应不是 JSON" + (f"，响应: {preview}" if preview else "")
        )
        return result

    result["raw_upload"] = data
    code_obj = data.get("code") or {}
    if code_obj.get("code") != "0000":
        err = (
                code_obj.get("errMsg")
                or code_obj.get("msg")
                or code_obj.get("msgId")
                or "上传失败"
        )
        result["message"] = f"上传失败：{err}（错误码: {code_obj.get('code')}）"
        return result

    bo = data.get("bo") or {}
    # 渲染地址：优先 base + bo.urlPath（HtmlIncludeMacro 的 NAS 直链约定）
    url_path = str(bo.get("urlPath") or "").strip()
    if url_path:
        if not url_path.startswith("/"):
            url_path = "/" + url_path
        render_url = HTML_INCLUDE_MACRO_BASE + url_path
    else:
        render_url = ""
    if not render_url and isinstance(bo, dict):
        # 兜底：接口直接返回完整地址时使用
        for k, v in bo.items():
            if isinstance(v, str) and v.startswith("http"):
                render_url = v
                break
    if not render_url:
        result["message"] = (
            "上传失败：接口返回缺少可渲染地址（bo.urlPath 或 bo.url/src/htmlUrl/mUrl），"
            f"原始返回: {json.dumps(data, ensure_ascii=False)[:300]}"
        )
        return result
    result["url"] = render_url
    result["file_name"] = (
        str(bo.get("nasFileName") or "").strip()
    )
    result["file_key"] = (
        str(bo.get("udmFileKey") or "").strip()
    )
    result["block_id"] = str(bo.get("blockId") or bo.get("block_id") or "").strip()

    # 2) 构造宏片段（macroId 使用自动生成的 id）
    emp_no, _ = get_user_and_token()
    macro_html = build_html_include_macro_html(
        url=result["url"],
        file_path=result.get("local_file_path", ""),
        file_name=result.get("file_name", ""),
        file_key=result.get("file_key", ""),
        macro_id=result.get("macro_id") or None,
        block_id=result.get("block_id") or None,
    )
    result["macro_html"] = macro_html
    result["status"] = "success"
    result["message"] = "上传成功并生成 HtmlIncludeMacro 宏"
    return result


# ---------------------------------------------------------------------------
# 3) 回写页面
# ---------------------------------------------------------------------------
def _append_macro_to_page(
        space_id: str,
        page_id: str,
        macro_html: str,
        *,
        chapter_name: str = "",
        chapter_position: int = 1,
        attach_mode: str = "append",
        skip_existing: bool = True,
) -> Dict[str, Any]:
    """
    将宏片段追加/挂载到目标页面正文。
    - 未指定章节：整页正文末尾/开头
    - 指定章节：章节末尾(append)/章节开头(prepend)
    """
    action_result: Dict[str, Any] = {
        "status": "error",
        "message": "",
        "space_id": space_id,
        "page_id": page_id,
        "appended": False,
        "skipped_existing": False,
    }
    if not page_id:
        action_result["message"] = "回写失败：缺少 pageId"
        return action_result
    if _fetch_space_page is None:
        action_result["message"] = "回写失败：页面读取技能(fetch_space_page)不可用"
        return action_result

    user_id, token = get_user_and_token()
    page_data = _fetch_space_page(space_id, page_id, user_id, token)
    if not isinstance(page_data, dict) or page_data.get("status") != "success":
        msg = ""
        if isinstance(page_data, dict):
            msg = page_data.get("message", "")
        action_result["message"] = f"回写失败：读取页面失败{f' - {msg}' if msg else ''}"
        return action_result

    html_content = (page_data.get("html_content") or "").strip()
    page_title = (page_data.get("title") or "").strip()
    if not html_content:
        action_result["message"] = "回写失败：页面正文为空，无法追加宏"
        return action_result

    # 平台限制：单页 HtmlIncludeMacro 不超过 10 个
    existing_count = _count_existing_html_include_macros(html_content)
    if existing_count >= MAX_HTML_INCLUDE_MACROS_PER_PAGE:
        action_result["message"] = (
            f"回写失败：页面已有 {existing_count} 个 HTML 包含宏，"
            f"已达到平台上限 {MAX_HTML_INCLUDE_MACROS_PER_PAGE} 个"
        )
        return action_result

    macro_id_match = re.search(r'id=["\']([A-Za-z0-9]+)["\']', macro_html)
    macro_ref = macro_id_match.group(1) if macro_id_match else ""
    if skip_existing and macro_ref and macro_ref in html_content:
        action_result["status"] = "success"
        action_result["message"] = "页面中已存在该 HTML 包含宏，无需重复回写"
        action_result["skipped_existing"] = True
        return action_result

    insert_fragment = f'<div data-html-include-macro="true">{macro_html}</div>'

    chapter_name = (chapter_name or "").strip()
    mode = (attach_mode or "append").strip().lower()
    if mode not in ("append", "prepend"):
        mode = "append"

    if chapter_name:
        heading_re = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1>", re.I | re.S)
        normalized_target = re.sub(r"\s+", " ", chapter_name).strip()
        heading_hits: List[Dict[str, Any]] = []
        for m in heading_re.finditer(html_content):
            level = int(m.group(1))
            heading_inner = m.group(2) or ""
            plain = re.sub(r"<[^>]+>", "", heading_inner)
            import html as _html

            plain = _html.unescape(plain)
            plain = re.sub(r"\s+", " ", plain).strip()
            heading_hits.append(
                {"level": level, "text": plain, "start": m.start(), "end": m.end()}
            )
        matched = [h for h in heading_hits if h["text"] == normalized_target]
        pos = chapter_position if isinstance(chapter_position, int) else 1
        if pos < 1:
            pos = 1
        if len(matched) < pos:
            action_result["message"] = f"回写失败：未找到第 {pos} 个章节「{chapter_name}」"
            return action_result
        target_heading = matched[pos - 1]
        target_level = int(target_heading["level"])
        if mode == "prepend":
            insert_at = int(target_heading["end"])
        else:
            next_same_or_higher = None
            target_idx = heading_hits.index(target_heading)
            for h in heading_hits[target_idx + 1:]:
                if int(h["level"]) <= target_level:
                    next_same_or_higher = h
                    break
            insert_at = (
                int(next_same_or_higher["start"])
                if next_same_or_higher is not None
                else len(html_content)
            )
        new_html = (
                html_content[:insert_at]
                + insert_fragment
                + html_content[insert_at:]
        )
    else:
        if mode == "prepend":
            new_html = insert_fragment + html_content
        else:
            new_html = html_content + insert_fragment

    update_url = (
        f"{SPACE_CONTENT_API}/content/{quote(page_id, safe='')}/updateContent"
        f"?environmentType=browser&spaceId={quote(space_id, safe='')}"
    )
    headers = get_auth_headers().copy()
    headers["Content-Type"] = "application/json;charset=UTF-8"
    payload = {
        "docType": "HTML",
        "title": page_title,
        "text": new_html,
    }
    data = _request_json(
        "POST",
        update_url,
        headers=headers,
        timeout=30,
        json_payload=payload,
        action="回写页面宏",
    )
    if not data:
        action_result["message"] = "回写失败：更新页面接口无有效响应"
        return action_result
    code = (data.get("code") or {}).get("code")
    if code != "0000":
        code_obj = data.get("code") or {}
        err = code_obj.get("errMsg") or code_obj.get("msg") or "更新页面失败"
        action_result["message"] = f"回写失败：{err}（错误码: {code or 'unknown'}）"
        return action_result

    action_result["status"] = "success"
    action_result["appended"] = True
    mount_desc = "页面正文"
    if chapter_name:
        mount_desc = f"章节「{chapter_name}」"
    action_result["message"] = (
            f"已回写 HtmlIncludeMacro 到{mount_desc}"
            + (f"，模式: {mode}" if chapter_name else "")
    )
    return action_result


# ---------------------------------------------------------------------------
# 4) 结果格式化 / URL 解析
# ---------------------------------------------------------------------------
def parse_space_page_id_from_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    """从页面 URL 解析 spaceId/pageId（复用附件脚本，同时兼容 rewrite/upload 的格式）。"""
    sid, pid = _parse_space_page_id_from_url(url)
    if sid and pid:
        return sid, pid
    # 兜底：兼容 /index/ispace/#/space/ 路径
    patterns = [
        r"/space/([^/]+)/wiki/page/([^/]+)/view",
        r"/shared/([^/]+)/wiki/page/([^/]+)/view",
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1), m.group(2)
    return None, None


def format_result_as_markdown(result: Dict[str, Any]) -> str:
    """将结果格式化为 Markdown。"""
    lines: List[str] = []
    if result.get("status") == "success":
        lines.append("## ✅ HTML 包含宏上传成功\n")
    elif result.get("status") == "partial_success":
        lines.append("## ⚠️ HTML 包含宏部分成功\n")
    else:
        lines.append("## ❌ HTML 包含宏上传失败\n")

    if result.get("space_id") or result.get("page_id"):
        lines.append(f"**空间ID**: `{result.get('space_id', '')}`")
        lines.append(f"**页面ID**: `{result.get('page_id', '')}`\n")

    if result.get("file_name"):
        lines.append(f"**HTML文件**: `{result.get('file_name')}`")
    if result.get("url"):
        lines.append(f"**HTML渲染地址**: {result.get('url')}")
    if result.get("macro_id"):
        lines.append(f"**宏ID**: `{result.get('macro_id')}`")
    if result.get("file_id"):
        lines.append(f"**fileId**: `{result.get('file_id')}`")
    if result.get("macro_html"):
        lines.append("\n### 🔗 HtmlIncludeMacro 宏片段\n")
        lines.append("```html")
        lines.append(result.get("macro_html"))
        lines.append("```\n")

    attach = result.get("attach_to_page") or {}
    if attach:
        status_icon = "✅" if attach.get("status") == "success" else "❌"
        lines.append("\n### 📄 页面挂载结果")
        lines.append(f"- {status_icon} {attach.get('message', '')}")
        if attach.get("skipped_existing"):
            lines.append("- 已存在相同宏，跳过回写")

    if result.get("message"):
        lines.append(f"\n**{result.get('message')}**\n")
    return "\n".join(lines)


def main() -> None:
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(
        description="上传本地 HTML 文件并以「HTML 包含宏(HtmlIncludeMacro)」保存到 iCenter 空间页面",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 上传本地 html 并追加到页面末尾（推荐）
  python3 studio_space_html_include_macro.py \
    --url "https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view" \
    --html-file /path/to/page.html

  # 指定 space-id/page-id，挂载到指定章节
  python3 studio_space_html_include_macro.py \
    --space-id "{spaceId}" --page-id "{pageId}" \
    --html-file /path/to/page.html \
    --attach-chapter-name "1. 背景" --attach-mode append

提示: 上传接口为 {SPACE_PLUGIN_API}/file/upload/html，单文件不超过 100MB；
      单页 HtmlIncludeMacro 上限 10 个。
提示: macroId / blockId 均由脚本自动生成，无需也无法手动指定。
提示: 用户ID和Token从环境变量 coclaw_empno 和 coclaw_token 获取。
        """,
    )

    parser.add_argument(
        "-u", "--url", dest="url",  help="iCenter 页面 URL（自动解析 spaceId/pageId）"
    )
    parser.add_argument("--space-id", dest="space_id", help="空间 ID")
    parser.add_argument("--page-id", dest="page_id", help="页面 ID")
    parser.add_argument(
        "--html-file", dest="html_file", required=True, help="本地 HTML 文件路径"
    )
    parser.add_argument(
        "--attach-chapter-name",
        dest="attach_chapter_name",
        default="",
        help="挂载到指定章节标题（不传则挂载到整页正文末尾）",
    )
    parser.add_argument(
        "--attach-position",
        dest="attach_position",
        type=int,
        default=1,
        help="同名章节位置（从 1 开始，默认 1）",
    )
    parser.add_argument(
        "--attach-mode",
        dest="attach_mode",
        choices=["append", "prepend"],
        default="append",
        help="挂载模式：append(末尾) 或 prepend(开头)",
    )
    parser.add_argument(
        "--json", dest="json_output", action="store_true", help="JSON 格式输出"
    )

    args = parser.parse_args()

    def _resolve_space_page() -> Tuple[Optional[str], Optional[str]]:
        if args.url:
            return parse_space_page_id_from_url(args.url)
        return args.space_id, args.page_id

    space_id, page_id = _resolve_space_page()
    if not space_id:
        parser.print_help()
        print("\n错误: 请提供 --space-id，或提供可解析出 spaceId 的 --url")
        sys.exit(1)
    if not page_id:
        parser.print_help()
        print("\n错误: 请提供页面 URL 或 --page-id（需要回写页面）")
        sys.exit(1)

    result = upload_html_file(
        space_id=space_id,
        page_id=page_id,
        local_file_path=args.html_file,
    )

    if result.get("status") == "success":
        result["attach_to_page"] = _append_macro_to_page(
            space_id=space_id,
            page_id=page_id,
            macro_html=result.get("macro_html", ""),
            chapter_name=args.attach_chapter_name,
            chapter_position=max(1, int(args.attach_position or 1)),
            attach_mode=args.attach_mode,
        )

    if args.json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_result_as_markdown(result))


if __name__ == "__main__":
    main()
