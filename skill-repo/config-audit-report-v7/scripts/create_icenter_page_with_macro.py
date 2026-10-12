#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=================================================================
config-audit-report - 创建 iCenter 页面并同步嵌入 HTML 宏报告链接
=================================================================
功能：
  在指定的 iCenter 空间/父页面下**新建一个页面**，并在**创建页面时同步**通过
  HTML 宏（HtmlIncludeMacro）嵌入报告链接（如配置审核报告的免登录分享链接）。

核心原理：
  iCenter 的"HTML 宏"本质上就是页面正文 HTML 中的一段宏标记：
    <div macro-key="HtmlIncludeMacro" m-url="<要嵌入的链接>" ...>
  因此"创建页面 + 嵌入 HTML 宏"可以**一步完成**：在调用创建页面接口时，
  直接把宏标记作为页面正文（contentBody）传入即可，无需先建页再二次回写。

  本脚本使用与 studio-space-manager 一致的创建页面接口：
    POST {BASE_URL}/zte-rd-icenter-contents/content?spaceId=&parentId=&environmentType=browser
  Body: {"parentId": ..., "spaceId": ..., "title": ..., "contentBody": <含宏标记的HTML>}

认证：工号/Token 从环境变量读取（coclaw_empno / X_Emp_No，coclaw_token / X_Auth_Value）。

使用方式：
  python3 create_icenter_page_with_macro.py \
      --space-id <spaceId> --parent-id <parentId> \
      --title "ZXUPN CSR V6.2_2026年7月配置审核报告" \
      --url "https://aip.zte.com.cn/cmtool/api/agenta/workspace/html-share/<报告分享ID>" \
      [--page-height 1600] [--page-width 1600]

作者：Co-Claw Team
创建：2026-09-16
"""

import argparse
import json
import os
import sys
from typing import Any, Dict, Optional, Tuple

import requests

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
ICENTER_API_BASE = "https://icenterapi.zte.com.cn"
CONTENT_CREATE_URL = f"{ICENTER_API_BASE}/zte-rd-icenter-contents/content"

AUTH_ERROR_MSG = "个人信息认证失败，请登录后再试。"

# 默认宏参数（与 icenter-html-embed 技能保持一致）
DEFAULT_MACRO_ID = "4dc0073d28e811f1919745a30f529340"
DEFAULT_SOURCE_MACRO_ID = "01kmmh84s13nz7tw07n2jh8afsx1p44t"
DEFAULT_BLOCK_ID = "zJbjpFph"
DEFAULT_PAGE_HEIGHT = "1600"
DEFAULT_PAGE_WIDTH = "1600"


def _get_user() -> Tuple[str, str]:
    """从环境变量获取工号和 Token。"""
    emp_no = (os.environ.get("coclaw_empno") or os.environ.get("X_Emp_No") or "").strip()
    token = (os.environ.get("coclaw_token") or os.environ.get("X_Auth_Value") or "").strip()
    return emp_no, token


def build_headers(user_id: str, token: str) -> Dict[str, str]:
    """构建 iCenter 统一请求头。"""
    return {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "X-Auth-Value": token,
        "X-Emp-No": user_id,
        "X-Tenant-Id": "ZTE",
        "X-Lang-Id": "zh_CN",
    }


def build_embed_html(
    embed_url: str,
    *,
    page_height: str = DEFAULT_PAGE_HEIGHT,
    page_width: str = DEFAULT_PAGE_WIDTH,
    macro_id: str = DEFAULT_MACRO_ID,
    source_macro_id: str = DEFAULT_SOURCE_MACRO_ID,
    block_id: str = DEFAULT_BLOCK_ID,
) -> str:
    """
    构建嵌入外部网页的 HtmlIncludeMacro HTML 宏标记。

    Args:
        embed_url: 要嵌入的网页链接（URL 模式，如报告免登录分享链接）
        page_height: 嵌入页面高度（px）
        page_width: 嵌入页面宽度（px）
        macro_id / source_macro_id / block_id: 宏标识（一般用默认值即可）

    Returns:
        完整的 HTML 宏标记字符串
    """
    return f'''<div ze-root="zeRoot">
<div m-mode="url" macro-id="{macro_id}" block-id="{block_id}" m-url="{embed_url}" tag-start="blockMacro" class="ze-macro" enable-ai-create="n" enable-ai-check="n" macro-key="HtmlIncludeMacro" selected="true" m-page-height="{page_height}" m-page-width="{page_width}" source-macro-id="{source_macro_id}">
<div tag-start="block" class="ze-macro-body">
<p tag-start="p"><br /></p>
</div>
</div>
<p tag-start="p"><br /></p>
</div>'''


def create_page_with_macro(
    *,
    space_id: str,
    parent_id: str,
    title: str,
    embed_url: str,
    page_height: str = DEFAULT_PAGE_HEIGHT,
    page_width: str = DEFAULT_PAGE_WIDTH,
) -> Dict[str, Any]:
    """
    在指定空间/父页面下创建页面，并在创建时同步嵌入 HTML 宏报告链接。

    Args:
        space_id: 空间 ID
        parent_id: 父页面 ID（新页面创建在该父页面下）
        title: 新页面标题
        embed_url: 要嵌入的报告链接（URL 模式）
        page_height / page_width: 嵌入页面尺寸

    Returns:
        成功: {"status": "success", "page_link", "page_id", ...}
        失败: {"status": "error", "message"}
    """
    user_id, token = _get_user()
    if not user_id or not token:
        return {"status": "error", "message": AUTH_ERROR_MSG}

    # 构造含 HTML 宏标记的页面正文
    macro_html = build_embed_html(embed_url, page_height=page_height, page_width=page_width)

    headers = build_headers(user_id, token)
    url = f"{CONTENT_CREATE_URL}?spaceId={space_id}&parentId={parent_id}&environmentType=browser"
    body = {
        "parentId": parent_id,
        "spaceId": space_id,
        "title": (title or "").strip() or "",
        "contentBody": macro_html,
    }

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return {"status": "error", "message": f"创建页面失败: {exc}"}

    code = (data.get("code") or {}).get("code")
    if code != "0000":
        return {
            "status": "error",
            "message": f"创建页面失败: {(data.get('code') or {}).get('msgId', '未知错误')}",
            "raw": data,
        }

    bo = data.get("bo") or {}
    page_id = bo.get("id") or bo.get("contentId") or ""

    return {
        "status": "success",
        "message": "页面创建成功，HTML 宏报告链接已同步嵌入",
        "page_link": f"https://i.zte.com.cn/index/ispace/#/space/{space_id}/wiki/page/{page_id}/view?useNewPath=true",
        "space_id": space_id,
        "parent_id": parent_id,
        "page_id": page_id,
        "title": (title or "").strip(),
        "embed_url": embed_url,
        "mode": "create_with_macro",
    }


def main():
    parser = argparse.ArgumentParser(
        description="创建 iCenter 页面并同步嵌入 HTML 宏报告链接"
    )
    parser.add_argument("--space-id", required=True, help="空间 ID")
    parser.add_argument("--parent-id", required=True, help="父页面 ID（新页面创建在该父页面下）")
    parser.add_argument("--title", required=True, help="新页面标题（建议与报告名称一致）")
    parser.add_argument("--url", required=True, help="要嵌入的报告链接（必须以 http:// 或 https:// 开头）")
    parser.add_argument("--page-height", default=DEFAULT_PAGE_HEIGHT, help="嵌入页面高度（px），默认1600")
    parser.add_argument("--page-width", default=DEFAULT_PAGE_WIDTH, help="嵌入页面宽度（px），默认1600")

    args = parser.parse_args()

    # URL 格式验证
    if not args.url.startswith(("http://", "https://")):
        print(json.dumps({
            "status": "error",
            "message": "URL 必须以 http:// 或 https:// 开头"
        }, ensure_ascii=False))
        sys.exit(1)

    result = create_page_with_macro(
        space_id=args.space_id,
        parent_id=args.parent_id,
        title=args.title,
        embed_url=args.url,
        page_height=args.page_height,
        page_width=args.page_width,
    )

    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result.get("status") == "success" else 1)


if __name__ == "__main__":
    main()
