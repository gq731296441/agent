#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=================================================================
studio-space-manager - 页面保存模块 (v2.8.0)
=================================================================
iCenter 空间页面保存技能

将用户内容保存到指定空间目录并返回页面链接。

支持多轮交互：
1. 列出保存位置来源：上次保存位置、个人空间、最近使用的空间、我管理的空间
2. 用户选择空间后确认保存目录（空间根目录或指定父级）
3. 创建页面并返回链接

跨平台支持：
- ✅ Linux (Ubuntu/CentOS/Debian)
- ✅ Windows 10/11
- ✅ macOS (通过 Python)

使用方式：
  # Linux / macOS
  python3 studio_space_page_save.py --action list_options
  python3 studio_space_page_save.py --action create --space-id xxx --parent-id yyy --title "标题" --content "内容"

  # Windows
  python studio_space_page_save.py --action list_options

依赖：
- Python 3.6+
- requests
- beautifulsoup4（用于 data-ai 注入；缺失时自动 pip install 后重试，仍失败则跳过注入）
- markdown（本地 Markdown 回退；缺失时自动 pip install 后重试，仍失败则仅用接口转换）

作者：Co-Claw Team
创建：2026-03-05
更新：2026-03-07
"""

import argparse
import json
import os
import platform
import re
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests

from system_param import ICENTER_API_BASE, get_user_and_token

# 导入跨平台工具模块
try:
    from _platform_utils import (
        configure_stdio_utf8,
        detect_platform,
        get_log_dir,
        get_data_dir,
        SYSTEM,
        SYSTEM_RELEASE,
        IS_WINDOWS,
    )
except ImportError:
    SYSTEM, SYSTEM_RELEASE, IS_WINDOWS = (
        platform.system(),
        platform.release(),
        platform.system() == "Windows",
    )

    def configure_stdio_utf8() -> None:
        return

try:
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

# 确保可与本脚本同目录的辅助模块互相导入（无论从何处启动）
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from _pip_retry import ensure_import


def _import_markdown():
    import markdown

    return markdown


_markdown_lib = ensure_import(
    _import_markdown,
    "markdown",
    invalidate_prefixes=("markdown",),
)

from _ai_html_utils import html_add_ai_attr

# ---------------------------------------------------------------------------
# 接口地址（与前端 curl 一致）
# ---------------------------------------------------------------------------
BASE_URL = ICENTER_API_BASE
SAVE_PATH_URL = f"{BASE_URL}/zte-rd-icenter-advanced/savePath"
PERSONAL_SPACE_URL = f"{BASE_URL}/zte-km-icenter-space/space/personalSpace"
RECENT_SPACES_URL = f"{BASE_URL}/zte-km-icenter-space/spaceLog/loadLatestSpaceOutside"
MANAGED_SPACES_URL = f"{BASE_URL}/zte-km-icenter-space/space/searchMySpaceWithPage"
MAIN_PAGE_URL = f"{BASE_URL}/zte-rdcloud-space-content/content/mainPageWithBody/"
CONTENT_CREATE_URL = f"{BASE_URL}/zte-rd-icenter-contents/content"
MARKDOWN2HTML_URL = f"{BASE_URL}/zte-rdcloud-space-wconvert/markdown2html"
# 按空间名称查找空间（POST，body 含 keyword）
SEARCH_SPACE_URL = f"{BASE_URL}/zte-km-icenter-space/space/searchMySpace"
# 在指定空间内搜索页面（POST，body 含 keyword、searchFilters.space）
SEARCH_PAGE_URL = f"{BASE_URL}/zte-icenter-search-bff/zte-icenter-search-api/search"
# 获取空间管理员列表（用于过滤「仅当前用户为管理员」的空间）
LOAD_SPACE_ADMINS_URL = f"{BASE_URL}/zte-km-icenter-space/spacemember/loadSpaceAdmins"


AUTH_ERROR_MSG = "个人信息认证失败，请登录后再试。"


def _get_hash_code_by_string(s: str) -> int:
    """与前端 getHashCodeByString 一致：对字符串做 31 倍累加哈希，结果为 32 位有符号整数。"""
    hash_val = 0
    if not s:
        return hash_val
    for c in s:
        hash_val = (31 * hash_val + ord(c)) & 0xFFFFFFFF
    if hash_val >= 0x80000000:
        hash_val -= 0x100000000
    return hash_val


def _get_beijing_date() -> str:
    """与前端 getBeijingDate() 一致：返回当前北京时间的 YYYYMMDD 字符串（UTC+8）。"""
    beijing_tz = timezone(timedelta(hours=8))
    beijing_now = datetime.now(beijing_tz)
    return f"{beijing_now.year}{beijing_now.month:02d}{beijing_now.day:02d}"


def _build_system_param(prefix: str = "space") -> str:
    """生成 system 参数：prefix + '-' + getHashCodeByString(get_beijing_date())，与 space_at_me 一致。"""
    seed = _get_beijing_date()
    return f"{prefix}-{_get_hash_code_by_string(seed)}"


def _get_user() -> Tuple[str, str]:
    """从环境变量获取工号和 Token。"""
    emp_no, token = get_user_and_token(strict=False)
    emp_no = (emp_no or "").strip()
    token = (token or "").strip()
    return emp_no, token


def _build_headers() -> Tuple[Optional[Dict[str, str]], Optional[str], Optional[str]]:
    """
    组装请求头：由 _get_user 从环境变量获取工号与 Token。
    成功返回 (headers, emp_no, token)；若为空则返回 (None, None, AUTH_ERROR_MSG)。
    """
    emp_no, token = _get_user()
    if not emp_no or not token:
        return None, None, AUTH_ERROR_MSG
    headers = {
        "Accept": "application/json, text/plain, */*",
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
    return headers, emp_no, token


# ---------------------------------------------------------------------------
# 查询：保存位置、个人空间、最近使用、我管理的空间
# ---------------------------------------------------------------------------
def fetch_save_path(space_from: str = "DOC_SPACE") -> Optional[Dict]:
    """查询用户上次保存位置。返回 bo：spaceId, parentId, parentPath, parentTitle, spaceFrom 等。"""
    headers, emp_no, token = _build_headers()
    if headers is None:
        return None
    try:
        params = {"spaceFrom": space_from, "empNo": emp_no}
        headers["x-emp-no"] = emp_no
        headers["x-auth-value"] = token
        headers["x-tenant-id"] = "ZTE"
        resp = requests.get(SAVE_PATH_URL, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code", {}).get("code") == "0000" and "bo" in data:
            return data["bo"]
    except Exception:
        pass
    return None


def fetch_personal_space() -> Optional[Dict]:
    """查询用户个人空间。返回 bo：id, chName, enName, ..."""
    headers, _, _ = _build_headers()
    if headers is None:
        return None
    try:
        headers = dict(headers)
        headers["X-Tenant-Id"] = "ZTE"
        resp = requests.get(PERSONAL_SPACE_URL, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code", {}).get("code") == "0000" and "bo" in data:
            return data["bo"]
    except Exception:
        pass
    return None


def fetch_recent_spaces() -> List[Dict]:
    """查询最近使用的空间列表。返回 bo 数组：spaceId, chName, enName, url, icon, spaceFrom, certified。"""
    headers, _, _ = _build_headers()
    if headers is None:
        return []
    try:
        headers = dict(headers)
        headers["X-Tenant-Id"] = "ZTE"
        resp = requests.get(RECENT_SPACES_URL, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code", {}).get("code") == "0000":
            bo = data.get("bo")
            if isinstance(bo, list):
                return bo
    except Exception:
        pass
    return []


def fetch_managed_spaces(start: int = 0, count: int = 30) -> List[Dict]:
    """查询我管理的空间列表。role=1 表示管理员。返回 bo.rows。"""
    headers, _, _ = _build_headers()
    if headers is None:
        return []
    try:
        import time

        t = int(time.time() * 1000)
        params = {
            "role": "1",
            "start": start,
            "count": count,
            "t": t,
            "spaceId": "undefined",
            "environmentType": "browser",
            "system": _build_system_param("space"),
        }
        headers = dict(headers)
        headers["X-Cookie-From-Cros"] = "Y"
        resp = requests.get(
            MANAGED_SPACES_URL, headers=headers, params=params, timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code", {}).get("code") == "0000":
            bo = data.get("bo", {})
            rows = bo.get("rows") if isinstance(bo, dict) else []
            if isinstance(rows, list):
                return rows
    except Exception:
        pass
    return []


def get_space_admins(space_id: str) -> List[Dict]:
    """
    获取空间管理员列表（与 space_workreport 中 loadSpaceAdmins 一致）。
    返回 isAdmin=1 的管理员列表，每项含 employeeShortId, name, isAdmin 等。
    """
    if not (space_id or "").strip():
        return []
    headers, emp_no, _ = _build_headers()
    if headers is None:
        return []
    try:
        params = {
            "employeeShortId": emp_no,
            "spaceId": space_id,
            "all": "true",
            "environmentType": "browser",
            "system": _build_system_param("space"),
        }
        headers = dict(headers)
        resp = requests.get(
            LOAD_SPACE_ADMINS_URL, headers=headers, params=params, timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code", {}).get("code") == "0000" and "bo" in data:
            bo = data.get("bo", [])
            if isinstance(bo, list):
                return [a for a in bo if a.get("isAdmin") == 1]
    except Exception:
        pass
    return []


def _is_only_current_user_admin(space_id: str) -> bool:
    """判断该空间是否只有一个管理员且该管理员为当前用户。"""
    if not (space_id or "").strip():
        return False
    headers, emp_no, _ = _build_headers()
    if not emp_no:
        return False
    admins = get_space_admins(space_id)
    return (
        len(admins) == 1 and (admins[0].get("employeeShortId") or "").strip() == emp_no
    )


def get_space_homepage_id(space_id: str) -> Optional[str]:
    """获取空间主页（根目录）ID。"""
    headers, _, _ = _build_headers()
    if headers is None:
        return None
    try:
        params = {
            "t": int(__import__("time").time() * 1000),
            "environmentType": "browser",
            "system": _build_system_param("space"),
            "spaceId": space_id,
        }
        headers = dict(headers)
        headers["X-Cookie-From-Cros"] = "Y"
        resp = requests.get(MAIN_PAGE_URL, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code", {}).get("code") == "0000" and data.get("bo"):
            return (data["bo"].get("id") or "").strip() or None
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# 按空间名称查找空间、在空间内查找页面（用于「保存到空间A的B页面下」）
# ---------------------------------------------------------------------------
def fetch_spaces_by_keyword(keyword: str, role: str = "1") -> List[Dict]:
    """按关键字搜索「我的空间」，POST searchMySpace。返回 bo 数组，每项含 id, chName, enName。"""
    if not (keyword or "").strip():
        return []
    headers, emp_no, _ = _build_headers()
    if headers is None:
        return []
    try:
        params = {"environmentType": "browser", "system": _build_system_param("space")}
        headers = dict(headers)
        headers["Content-Type"] = "application/json;charset=UTF-8"
        headers["X-Cookie-From-Cros"] = "Y"
        body = {
            "employeeShortId": emp_no,
            "keyword": (keyword or "").strip(),
            "role": role,
            "scCodes": [],
        }
        resp = requests.post(
            SEARCH_SPACE_URL, headers=headers, params=params, json=body, timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code", {}).get("code") == "0000":
            bo = data.get("bo")
            if isinstance(bo, list):
                return bo
    except Exception:
        pass
    return []


def _strip_html_title(raw: str) -> str:
    """去掉标题中的 HTML 标签（如 <em class=\"search-highlight\">...</em>），得到纯文本。"""
    if not raw:
        return ""
    return re.sub(r"<[^>]+>", "", raw).replace("&nbsp;", " ").strip()


def fetch_pages_in_space(
    space_id: str,
    keyword: str,
    page: int = 1,
    size: int = 15,
) -> List[Dict[str, str]]:
    """在指定空间内按关键字搜索页面。返回列表，每项含 id（contentId）、title_plain（去 HTML 的标题）。"""
    if not space_id or not (keyword or "").strip():
        return []
    headers, _, _ = _build_headers()
    if headers is None:
        return []
    try:
        headers = dict(headers)
        headers["Content-Type"] = "application/json"
        headers["X-Device"] = "web"
        headers["X-Tenant-Id"] = "31003"
        body = {
            "keyword": (keyword or "").strip(),
            "app": "page_v2",
            "searchFilters": {"space": {"value": [space_id], "type": "space"}},
            "page": page,
            "size": size,
            "correctKeyword": True,
            "keywordCorrected": False,
            "domain": "space",
            "isDeepThinking": True,
            "searchSort": "DEFAULT",
        }
        resp = requests.post(SEARCH_PAGE_URL, headers=headers, json=body, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code", {}).get("code") != "0000":
            return []
        bo = data.get("bo") or {}
        rows = bo.get("rows") if isinstance(bo, dict) else []
        if not isinstance(rows, list):
            return []
        out = []
        for row in rows:
            cid = (row.get("id") or "").strip()
            title_raw = row.get("title") or ""
            title_plain = _strip_html_title(title_raw)
            if cid:
                out.append(
                    {"id": cid, "title_plain": title_plain, "title_raw": title_raw}
                )
        return out
    except Exception:
        pass
    return []


def markdown_to_html(markdown_content: str) -> Optional[str]:
    """将 Markdown 转为 HTML（调用 iCenter 转换接口）。

    注意：iCenter 的 markdown2html 接口会破坏 PlantUML 代码块的换行符，
    因此需要先保护 PlantUML 代码块，转换后再恢复。
    """
    if not (markdown_content or "").strip():
        return None
    headers, emp_no, token = _build_headers()
    if headers is None:
        return None

    # 步骤1：提取 PlantUML 代码块，用占位符替换，避免被 markdown2html 接口破坏换行
    plantuml_blocks: List[str] = []
    # 使用 HTML 注释作为占位符，不会被 markdown 解析
    plantuml_placeholder_start = "<!--PLANTUML_BLOCK_{}_START-->"
    plantuml_placeholder_end = "<!--PLANTUML_BLOCK_{}_END-->"

    def _save_plantuml_block(match: "re.Match") -> str:
        idx = len(plantuml_blocks)
        plantuml_blocks.append(match.group(1))
        return plantuml_placeholder_start.format(idx) + plantuml_placeholder_end.format(
            idx
        )

    # 匹配 PlantUML fenced code：
    # - 支持 ```plantuml ... ```
    # - 支持 ``` plantuml ... ```（允许 plantuml 前有空格）
    # - 允许 plantuml 后可选换行（有些 Markdown 产物可能同一行开始代码）
    plantuml_pattern = r"```\s*plantuml\s*(?:\r?\n)?(.*?)```"
    protected_content = re.sub(
        plantuml_pattern, _save_plantuml_block, markdown_content, flags=re.DOTALL
    )

    # 步骤2：调用 markdown2html 接口
    try:
        headers = dict(headers)
        headers["Content-Type"] = "application/json;charset=UTF-8"
        resp = requests.post(
            f"{MARKDOWN2HTML_URL}?environmentType=browser",
            headers=headers,
            json={"content": protected_content},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code", {}).get("code") != "0000" or "bo" not in data:
            return None
        html_result = data.get("bo") or ""
    except Exception:
        return None

    # 步骤3：将占位符替换为正确格式的 PlantUML HTML 块
    if plantuml_blocks:
        import html as html_module

        for idx, plantuml_code in enumerate(plantuml_blocks):
            escaped_code = html_module.escape(plantuml_code)
            # 将字面量 \n（反斜杠+n）替换为 HTML 实体 &#92;n，避免 iCenter 后端误解析为真实换行
            escaped_code = escaped_code.replace("\\n", "&#92;n")
            # 使用 <pre><code> 包裹，保留换行并便于前端渲染
            plantuml_html = (
                f'<pre><code class="language-plantuml">{escaped_code}</code></pre>'
            )
            start_marker = plantuml_placeholder_start.format(idx)
            end_marker = plantuml_placeholder_end.format(idx)
            # 占位符在 markdown2html 结果中可能被 <p> 等标签包裹，这里用正则宽松匹配
            raw_pattern = re.escape(start_marker) + r".*?" + re.escape(end_marker)
            html_result = re.sub(
                raw_pattern, plantuml_html, html_result, flags=re.DOTALL
            )

            # 若 markdown2html 将 HTML 注释转义成实体（例如 &lt;!-- ... --&gt;），则再尝试匹配转义后的占位符
            escaped_start_marker = html_module.escape(start_marker)
            escaped_end_marker = html_module.escape(end_marker)
            escaped_pattern = (
                re.escape(escaped_start_marker) + r".*?" + re.escape(escaped_end_marker)
            )
            html_result = re.sub(
                escaped_pattern, plantuml_html, html_result, flags=re.DOTALL
            )

    return html_result or None


def _markdown_to_html_fallback(markdown_content: str) -> Optional[str]:
    """接口不可用时，用本地 markdown 库将 Markdown 转为 HTML。"""
    if not (markdown_content or "").strip() or _markdown_lib is None:
        return None
    try:
        return _markdown_lib.markdown(markdown_content, extensions=["extra", "nl2br"])
    except Exception:
        return None


def _normalize_newlines_to_br(text: str) -> str:
    """将真实换行符与字面量 \\n/\\r\\n/\\r 均转为 <br>，避免页面上显示 \\n 符号。

    注意：跳过 <pre><code> 块内的内容，保护代码块中的换行符不被转换。
    """
    if not text:
        return text

    # 步骤1：提取所有 <pre><code>...</code></pre> 块，用占位符替换
    code_blocks: List[str] = []
    placeholder_template = "<!--CODE_BLOCK_{}-->"

    def _save_code_block(match: "re.Match") -> str:
        idx = len(code_blocks)
        code_blocks.append(match.group(0))
        return placeholder_template.format(idx)

    code_pattern = r"<pre>\s*<code[^>]*>.*?</code>\s*</pre>"
    t = re.sub(code_pattern, _save_code_block, text, flags=re.DOTALL)

    # 步骤2：对剩余内容进行换行符转换
    # 先处理字面量形式的 \n \r\n \r（反斜杠+n 等），再处理真实换行符
    t = t.replace("\\r\\n", "<br>").replace("\\n", "<br>").replace("\\r", "<br>")
    t = t.replace("\r\n", "<br>").replace("\n", "<br>").replace("\r", "<br>")

    # 步骤3：将占位符替换回原来的代码块
    for idx, code_block in enumerate(code_blocks):
        t = t.replace(placeholder_template.format(idx), code_block)

    return t


def create_space_page(
    space_id: str,
    parent_id: str,
    title: str,
    content: str,
    content_is_html: bool = False,
    force_html_convert: bool = False,
) -> Optional[Dict]:
    """
    在指定空间、指定父节点下创建页面。content 默认按 Markdown 转 HTML；若 content_is_html=True 则直接使用。
    若 force_html_convert=True，则强制将所有内容转换为 HTML（不直接使用原始 HTML）。

    返回接口 bo（含 id）。
    """
    headers, _, _ = _build_headers()
    if headers is None:
        return None

    # 强制转换模式：先尝试将内容作为 Markdown 转为 HTML
    if force_html_convert:
        html = markdown_to_html(content)
        if not html:
            html = _markdown_to_html_fallback(content)
        if not html:
            html = f"<div>{_normalize_newlines_to_br(content or '')}</div>"
    elif content_is_html:
        # 原始 HTML 模式：直接使用，但需要进行换行符规范化
        html = content
    else:
        # 默认 Markdown 转 HTML
        html = markdown_to_html(content)
        if not html:
            html = _markdown_to_html_fallback(content)
        if not html:
            # 仅在完全无法转换为 HTML 时，才按纯文本将换行规范为 <br>
            html = f"<div>{_normalize_newlines_to_br(content or '')}</div>"

    # 保存前为 p/h1-h6 添加 data-ai，便于平台统计 AI 生成率（co-claw 产出页面）
    html = html_add_ai_attr(html)

    url = f"{CONTENT_CREATE_URL}?spaceId={space_id}&parentId={parent_id}&environmentType=browser"
    headers = dict(headers)
    headers["Content-Type"] = "application/json"
    body = {
        "parentId": parent_id,
        "spaceId": space_id,
        "title": (title or "").strip() or "",
        "contentBody": html,
    }
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code", {}).get("code") == "0000":
            return data
    except Exception:
        pass
    return None


def build_page_link(space_id: str, content_id: str) -> str:
    # 按最新规范使用 index/ispace 路径
    return f"https://i.zte.com.cn/index/ispace/#/space/{space_id}/wiki/page/{content_id}/view?useNewPath=true"


# ---------------------------------------------------------------------------
# 解析「保存到空间A的B页面下」：按空间名查 spaceId，按页面名查 contentId 作为父级
# ---------------------------------------------------------------------------
def _build_resolved_paths(
    space_name: str,
    page_name: Optional[str],
) -> Tuple[List[Dict[str, str]], Optional[str]]:
    """
    根据空间名称和可选页面名称，解析出所有可能的 (spaceId, spaceName, parentId, parentTitle)。
    返回 (paths, error_message)。若 error_message 非空则 paths 为空。
    """
    space_name = (space_name or "").strip()
    if not space_name:
        return [], "请提供空间名称。"

    spaces = fetch_spaces_by_keyword(space_name)
    exact_spaces = [s for s in spaces if (s.get("chName") or "").strip() == space_name]
    if not exact_spaces:
        return [], f"未找到名称完全一致的空间「{space_name}」，可能是空间不存在或当前账号无访问权限，请确认空间名称或权限后重试。"

    page_name = (page_name or "").strip() if page_name else None
    paths = []  # 每项: spaceId, spaceName, parentId, parentTitle

    if not page_name:
        # 保存到空间根目录
        for s in exact_spaces:
            sid = (s.get("id") or "").strip()
            sname = (s.get("chName") or s.get("enName") or "").strip()
            if not sid:
                continue
            parent_id = get_space_homepage_id(sid)
            if not parent_id:
                continue
            paths.append(
                {
                    "spaceId": sid,
                    "spaceName": sname,
                    "parentId": parent_id,
                    "parentTitle": "根目录",
                }
            )
    else:
        # 保存到指定页面下：在每个名称完全一致的空间内搜索标题完全一致的页面
        for s in exact_spaces:
            sid = (s.get("id") or "").strip()
            sname = (s.get("chName") or s.get("enName") or "").strip()
            if not sid:
                continue
            pages = fetch_pages_in_space(sid, page_name, page=1, size=50)
            exact_pages = [
                p for p in pages if (p.get("title_plain") or "").strip() == page_name
            ]
            for p in exact_pages:
                pid = (p.get("id") or "").strip()
                if pid:
                    paths.append(
                        {
                            "spaceId": sid,
                            "spaceName": sname,
                            "parentId": pid,
                            "parentTitle": (p.get("title_plain") or "").strip()
                            or page_name,
                        }
                    )
        if not paths and exact_spaces:
            return (
                [],
                f"在空间「{space_name}」下未找到标题完全一致的页面「{page_name}」，可能是页面已被删除或当前账号无访问权限，请确认页面是否存在或权限是否足够。",
            )

    return paths, None


def run_resolve_target(
    space_name: str,
    page_name: Optional[str] = None,
    index: Optional[int] = None,
) -> Dict[str, Any]:
    """
    用户指定「保存到空间A的B页面下」时：
    1）按空间名查 spaceId（名称完全一致；多个则再按页面名筛或列出）
    2）若指定了页面名则查该空间下标题完全一致的页面 contentId 作为 parentId
    3）唯一则返回 confirm_location；多个则返回 choose_resolved_target 供用户按序号选，再传 index 确认
    """
    headers, _, _ = _build_headers()
    if headers is None:
        return {"step": "error", "message": AUTH_ERROR_MSG, "success": False}
    paths, err = _build_resolved_paths(space_name, page_name)
    if err:
        return {"step": "error", "message": err, "success": False}

    if index is not None:
        if index < 0 or index >= len(paths):
            return {
                "step": "error",
                "message": f"序号无效，请选择 1 到 {len(paths)} 之间的数字。",
                "success": False,
            }
        p = paths[index]
        return {
            "step": "confirm_location",
            "message": f"将保存到：空间「{p['spaceName']}」下的「{p['parentTitle']}」。请确认后提供页面标题和内容，我将在此目录下创建页面并返回链接。",
            "spaceId": p["spaceId"],
            "parentId": p["parentId"],
            "parentTitle": p["parentTitle"],
            "spaceName": p["spaceName"],
            "next_action": "create",
            "success": True,
        }

    if len(paths) == 1:
        p = paths[0]
        return {
            "step": "confirm_location",
            "message": f"将保存到：空间「{p['spaceName']}」下的「{p['parentTitle']}」。请确认后提供页面标题和内容，我将在此目录下创建页面并返回链接。",
            "spaceId": p["spaceId"],
            "parentId": p["parentId"],
            "parentTitle": p["parentTitle"],
            "spaceName": p["spaceName"],
            "next_action": "create",
            "success": True,
        }

    # 多个候选，列出供用户按序号选择
    lines = []
    for i, p in enumerate(paths, 1):
        lines.append(f"{i}. 空间「{p['spaceName']}」下的「{p['parentTitle']}」")
    message = "存在多个匹配的保存位置，请根据序号选择：\n" + "\n".join(lines)
    return {
        "step": "choose_resolved_target",
        "message": message,
        "paths": paths,
        "success": True,
    }


# ---------------------------------------------------------------------------
# 多轮交互：list_options / choose_space / create
# ---------------------------------------------------------------------------
def run_list_options() -> Dict[str, Any]:
    """拉取四类数据并返回第一步选项，供代理展示给用户。"""
    headers, _, _ = _build_headers()
    if headers is None:
        return {"step": "error", "message": AUTH_ERROR_MSG, "success": False}
    save_path = fetch_save_path()
    personal = fetch_personal_space()
    recent = fetch_recent_spaces()
    managed = fetch_managed_spaces()
    # 仅保留「只有一个管理员且管理员是当前用户」的空间（参考 space_workreport）
    recent = [s for s in recent if _is_only_current_user_admin(s.get("spaceId") or "")]
    managed = [s for s in managed if _is_only_current_user_admin(s.get("id") or "")]

    options = []
    if save_path:
        sp_id = save_path.get("spaceId") or ""
        parent_id = save_path.get("parentId") or ""
        parent_title = (save_path.get("parentTitle") or "根目录").strip()
        options.append(
            {
                "type": "save_path",
                "label": "使用上次保存位置",
                "description": f"空间目录：{parent_title}",
                "spaceId": sp_id,
                "parentId": parent_id,
                "parentTitle": parent_title,
            }
        )
    if personal:
        pid = personal.get("id") or ""
        ch_name = (personal.get("chName") or "我的空间").strip()
        options.append(
            {
                "type": "personal",
                "label": "我的个人空间",
                "description": ch_name,
                "spaceId": pid,
            }
        )
    if recent:
        options.append(
            {
                "type": "recent",
                "label": "从最近使用的空间中选择",
                "spaces": [
                    {
                        "spaceId": s.get("spaceId"),
                        "chName": (s.get("chName") or s.get("enName") or "").strip(),
                        "index": i,
                    }
                    for i, s in enumerate(recent)
                ],
            }
        )
    if managed:
        options.append(
            {
                "type": "managed",
                "label": "从我管理的空间中选择",
                "spaces": [
                    {
                        "spaceId": s.get("id"),
                        "chName": (s.get("chName") or s.get("enName") or "").strip(),
                        "index": i,
                    }
                    for i, s in enumerate(managed)
                ],
            }
        )

    if not options:
        return {
            "step": "error",
            "message": "暂无可用的保存位置（保存位置、个人空间、最近使用、我管理的空间均未获取到）。",
            "success": False,
        }

    message = "请选择要将内容保存到哪个位置：\n"
    for i, opt in enumerate(options, 1):
        if opt["type"] == "save_path":
            message += f"{i}. 【使用上次保存位置】 {opt.get('description', '')}\n"
        elif opt["type"] == "personal":
            message += f"{i}. 【我的个人空间】 {opt.get('description', '')}\n"
        elif opt["type"] == "recent":
            message += f"{i}. 【最近使用的空间】 请回复「最近第 N 个」或空间名称（N 从 1 开始）\n"
        elif opt["type"] == "managed":
            message += f"{i}. 【我管理的空间】 请回复「管理第 N 个」或空间名称（N 从 1 开始）\n"

    return {
        "step": "choose_source",
        "message": message.strip(),
        "options": options,
        "success": True,
    }


def run_choose_space(
    source: str,
    index: Optional[int] = None,
) -> Dict[str, Any]:
    """根据用户选择（save_path / personal / recent / managed）解析出 spaceId、parentId、parentTitle，返回确认步骤。"""
    headers, _, _ = _build_headers()
    if headers is None:
        return {"step": "error", "message": AUTH_ERROR_MSG, "success": False}
    space_id = None
    parent_id = None
    parent_title = None
    space_name = None

    if source == "save_path":
        save_path = fetch_save_path()
        if not save_path:
            return {"step": "error", "message": "无法获取上次保存位置。", "success": False}
        space_id = save_path.get("spaceId") or ""
        parent_id = save_path.get("parentId") or ""
        parent_title = (save_path.get("parentTitle") or "根目录").strip()
        space_name = parent_title  # 简化展示

    elif source == "personal":
        personal = fetch_personal_space()
        if not personal:
            return {"step": "error", "message": "无法获取个人空间。", "success": False}
        space_id = personal.get("id") or ""
        space_name = (personal.get("chName") or "我的空间").strip()
        parent_id = get_space_homepage_id(space_id)
        if not parent_id:
            return {"step": "error", "message": "无法获取个人空间根目录。", "success": False}
        parent_title = "根目录"

    elif source == "recent":
        recent = fetch_recent_spaces()
        recent = [
            s for s in recent if _is_only_current_user_admin(s.get("spaceId") or "")
        ]
        if index is None or index < 0 or index >= len(recent):
            return {
                "step": "choose_source",
                "message": "请指定最近使用的空间序号（从 1 开始），例如：最近第 1 个。",
                "options": [
                    {
                        "type": "recent",
                        "spaces": [
                            {
                                "spaceId": s.get("spaceId"),
                                "chName": (
                                    s.get("chName") or s.get("enName") or ""
                                ).strip(),
                                "index": i,
                            }
                            for i, s in enumerate(recent)
                        ],
                    }
                ],
                "success": True,
            }
        s = recent[index]
        space_id = s.get("spaceId") or ""
        space_name = (s.get("chName") or s.get("enName") or "").strip()
        parent_id = get_space_homepage_id(space_id)
        if not parent_id:
            return {
                "step": "error",
                "message": f"无法获取空间「{space_name}」的根目录。",
                "success": False,
            }
        parent_title = "根目录"

    elif source == "managed":
        managed = fetch_managed_spaces()
        managed = [s for s in managed if _is_only_current_user_admin(s.get("id") or "")]
        if index is None or index < 0 or index >= len(managed):
            return {
                "step": "choose_source",
                "message": "请指定我管理的空间序号（从 1 开始），例如：管理第 1 个。",
                "options": [
                    {
                        "type": "managed",
                        "spaces": [
                            {
                                "spaceId": s.get("id"),
                                "chName": (
                                    s.get("chName") or s.get("enName") or ""
                                ).strip(),
                                "index": i,
                            }
                            for i, s in enumerate(managed)
                        ],
                    }
                ],
                "success": True,
            }
        s = managed[index]
        space_id = s.get("id") or ""
        space_name = (s.get("chName") or s.get("enName") or "").strip()
        parent_id = get_space_homepage_id(space_id)
        if not parent_id:
            return {
                "step": "error",
                "message": f"无法获取空间「{space_name}」的根目录。",
                "success": False,
            }
        parent_title = "根目录"

    else:
        return {"step": "error", "message": f"未知来源: {source}", "success": False}

    return {
        "step": "confirm_location",
        "message": f"将保存到：空间「{space_name}」下的「{parent_title}」。请确认后提供页面标题和内容，我将在此目录下创建页面并返回链接。",
        "spaceId": space_id,
        "parentId": parent_id,
        "parentTitle": parent_title,
        "spaceName": space_name,
        "next_action": "create",
        "success": True,
    }


def run_create(
    space_id: str,
    parent_id: str,
    title: str,
    content: str,
    content_is_html: bool = False,
    force_html_convert: bool = False,
) -> Dict[str, Any]:
    """
    在指定 spaceId、parentId 下创建页面，返回页面链接。

    参数:
        space_id: 空间 ID
        parent_id: 父页面 ID
        title: 页面标题
        content: 页面内容
        content_is_html: 内容是否为 HTML 格式
        force_html_convert: 强制将内容转换为 HTML（不直接使用原始 HTML）
    """
    headers, _, _ = _build_headers()
    if headers is None:
        return {"step": "error", "message": AUTH_ERROR_MSG, "success": False}
    if not space_id or not parent_id:
        return {"step": "error", "message": "缺少 spaceId 或 parentId。", "success": False}
    title = (title or "").strip() or ""
    result = create_space_page(
        space_id,
        parent_id,
        title,
        content or "",
        content_is_html=content_is_html,
        force_html_convert=force_html_convert,
    )
    if not result or not result.get("bo"):
        return {"step": "error", "message": "创建页面失败，请检查权限与参数。", "success": False}
    content_id = (result.get("bo") or {}).get("id") or ""
    if not content_id:
        return {"step": "error", "message": "创建成功但未返回页面 ID。", "success": False}
    page_link = build_page_link(space_id, content_id)
    return {
        "step": "created",
        "message": "页面已创建。",
        "page_link": page_link,
        "contentId": content_id,
        "spaceId": space_id,
        "title": title,
        "success": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="将内容保存到指定空间目录并返回页面链接（多轮交互）")
    parser.add_argument(
        "--action",
        choices=["list_options", "choose_space", "resolve_target", "create"],
        required=True,
        help="当前步骤；resolve_target 用于「保存到空间A的B页面下」",
    )
    parser.add_argument(
        "--source",
        choices=["save_path", "personal", "recent", "managed"],
        help="选择空间来源（choose_space 时必填）",
    )
    parser.add_argument(
        "--index",
        type=int,
        default=None,
        help="列表序号（0 起）；choose_space 为最近/管理；resolve_target 为多选时的序号",
    )
    parser.add_argument(
        "--space-name", type=str, default="", help="resolve_target：空间名称（必填）"
    )
    parser.add_argument(
        "--page-name", type=str, default="", help="resolve_target：父页面名称（可选，不填则保存到空间根目录）"
    )
    parser.add_argument("--space-id", type=str, default="", help="空间 ID（创建/更新页面时使用）")
    parser.add_argument("--parent-id", type=str, default="", help="创建页面时的父节点 ID")
    parser.add_argument("--title", type=str, default="", help="页面标题")
    parser.add_argument(
        "--content", type=str, default="", help="页面内容（Markdown 或 HTML，见 --content-html）"
    )
    parser.add_argument(
        "--content-file", type=str, default="", help="从文件读取页面内容（避免命令行特殊字符解析问题）"
    )
    parser.add_argument(
        "--content-html", action="store_true", help="--content 为 HTML，不转 Markdown"
    )
    parser.add_argument(
        "--force-html-convert", action="store_true", help="强制将内容转换为 HTML（不直接使用原始 HTML）"
    )
    args = parser.parse_args()

    headers, _, err_msg = _build_headers()
    if headers is None:
        out = {"step": "error", "message": err_msg or AUTH_ERROR_MSG, "success": False}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        sys.exit(1)

    if args.action == "list_options":
        out = run_list_options()
    elif args.action == "choose_space":
        if not args.source:
            out = {
                "step": "error",
                "message": "请指定 --source（save_path / personal / recent / managed）",
                "success": False,
            }
        else:
            out = run_choose_space(args.source, args.index)
    elif args.action == "resolve_target":
        space_name = (args.space_name or "").strip()
        if not space_name:
            out = {
                "step": "error",
                "message": "请指定 --space-name（空间名称）。",
                "success": False,
            }
        else:
            page_name = (args.page_name or "").strip() or None
            out = run_resolve_target(space_name, page_name, args.index)
    elif args.action == "create":
        # 处理从文件读取内容
        content = args.content
        if args.content_file:
            try:
                with open(args.content_file, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception as e:
                out = {"step": "error", "message": f"读取内容文件失败: {e}", "success": False}
                print(json.dumps(out, ensure_ascii=False, indent=2))
                sys.exit(1)
        out = run_create(
            args.space_id,
            args.parent_id,
            args.title,
            content,
            content_is_html=args.content_html,
            force_html_convert=args.force_html_convert,
        )
    else:
        out = {
            "step": "error",
            "message": f"未知 action: {args.action}",
            "success": False,
        }

    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    configure_stdio_utf8()
    main()
