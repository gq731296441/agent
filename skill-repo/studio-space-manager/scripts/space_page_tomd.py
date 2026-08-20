#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将 iCenter 空间页面转换为 Markdown 并保存到本地文件。

能力：
- 支持 URL 直转，或通过 spaceId + pageId 转换
- 使用页面标题作为文件名（自动清理非法字符）
- 保存文件首行写入页面完整链接，便于后续回写页面时识别
"""

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests

from _platform_utils import configure_stdio_utf8, get_data_dir
from system_param import ICENTER_API_BASE, get_user_and_token as _get_user_and_token

BASE_API = ICENTER_API_BASE


def get_user_and_token() -> Tuple[str, str]:
    """读取工号和 token。"""
    return _get_user_and_token(strict=False)


def build_headers(emp_no: str, token: str) -> Dict[str, str]:
    """构建统一请求头。"""
    return {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "X-Auth-Value": token,
        "X-Emp-No": emp_no,
        "X-Device": "web",
        "X-Lang-Id": "zh_CN",
    }


def parse_page_link(url: str) -> Optional[Tuple[str, str]]:
    """从页面 URL 中解析 space_id/page_id。"""
    if not url:
        return None
    part = url.strip().split("#", 1)[-1] if "#" in url else url.strip()
    pattern = r"(?:shared|space)/([a-f0-9]{32})/wiki/page/([a-f0-9]{32})/view"
    match = re.search(pattern, part, re.IGNORECASE)
    if match:
        return match.group(1), match.group(2)
    return None


def build_page_url(space_id: str, page_id: str) -> str:
    """拼接页面访问链接。"""
    return f"https://i.zte.com.cn/index/ispace/#/space/{space_id}/wiki/page/{page_id}/view?useNewPath=true"


def fetch_page_content(space_id: str, page_id: str, headers: Dict[str, str]) -> Dict[str, Any]:
    """获取页面 title + HTML 正文。"""
    url = f"{BASE_API}/zte-rd-icenter-contents/content/{page_id}?spaceId={space_id}&environmentType=browser"
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as exc:
        return {"status": "error", "message": f"请求页面内容失败: {exc}"}
    except ValueError:
        return {"status": "error", "message": "页面内容接口返回非 JSON"}

    code_info = data.get("code", {})
    if code_info.get("code") != "0000" or "bo" not in data:
        return {
            "status": "error",
            "message": f"获取页面内容失败: {code_info.get('msg', '未知错误')}",
            "code": code_info.get("code", ""),
        }
    bo = data.get("bo", {})
    return {
        "status": "success",
        "title": bo.get("title", "") or "",
        "html_content": bo.get("contentBody", "") or "",
    }


def html_to_markdown(html_content: str, headers: Dict[str, str]) -> Dict[str, Any]:
    """调用接口将 HTML 转换为 Markdown。"""
    if not html_content:
        return {"status": "error", "message": "页面 HTML 内容为空"}
    url = f"{BASE_API}/zte-rdcloud-space-wconvert/html2markdown?environmentType=browser"
    try:
        resp = requests.post(url, headers=headers, json={"content": html_content}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as exc:
        return {"status": "error", "message": f"HTML 转 Markdown 请求失败: {exc}"}
    except ValueError:
        return {"status": "error", "message": "HTML 转 Markdown 接口返回非 JSON"}

    code_info = data.get("code", {})
    if code_info.get("code") != "0000":
        return {
            "status": "error",
            "message": f"HTML 转 Markdown 失败: {code_info.get('msg', '未知错误')}",
            "code": code_info.get("code", ""),
        }
    return {"status": "success", "markdown_content": data.get("bo", "") or ""}


def postprocess_markdown(md_content: str) -> str:
    """轻量后处理，修复常见转义问题。"""
    return (
        md_content.replace("\\[", "[")
        .replace("\\]", "]")
        .replace("\\_", "_")
        .replace("\\>", ">")
        .replace("\\-", "-")
    )


def sanitize_filename(name: str) -> str:
    """将标题转换为安全文件名。"""
    safe = re.sub(r'[\\/:*?"<>|]', "_", name or "")
    safe = re.sub(r"\s+", "_", safe).strip("_. ")
    return safe[:100] if safe else "untitled"


def resolve_output_dir(output_dir: Optional[str]) -> Path:
    """确定输出目录。"""
    if output_dir:
        result_dir = Path(output_dir)
    else:
        base = Path(get_data_dir())
        result_dir = base / "page_tomd" / datetime.now().strftime("%Y%m%d_%H%M%S")
    result_dir.mkdir(parents=True, exist_ok=True)
    return result_dir


def save_markdown_file(page_url: str, title: str, markdown_content: str, output_dir: Path) -> Path:
    """按“链接 + 正文”格式保存为本地 md。"""
    file_path = output_dir / f"{sanitize_filename(title)}.md"
    final_content = f"{page_url}\n\n{markdown_content}".strip() + "\n"
    file_path.write_text(final_content, encoding="utf-8")
    return file_path


def run_convert(space_id: str, page_id: str, headers: Dict[str, str], output_dir: Path) -> Dict[str, Any]:
    """执行页面转换并保存。"""
    content_result = fetch_page_content(space_id, page_id, headers)
    if content_result.get("status") != "success":
        return content_result

    convert_result = html_to_markdown(content_result.get("html_content", ""), headers)
    if convert_result.get("status") != "success":
        return convert_result

    page_url = build_page_url(space_id, page_id)
    title = content_result.get("title", "") or "untitled"
    markdown = postprocess_markdown(convert_result.get("markdown_content", ""))
    file_path = save_markdown_file(page_url, title, markdown, output_dir)
    return {
        "status": "success",
        "message": "页面已转换并保存为 Markdown",
        "title": title,
        "space_id": space_id,
        "page_id": page_id,
        "page_url": page_url,
        "file_path": str(file_path),
    }


def main() -> None:
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(
        description="iCenter 页面转 Markdown 并保存到本地文件",
        epilog="提示: 工号和 Token 从环境变量 coclaw_empno、coclaw_token 获取",
    )
    parser.add_argument("--url", help="页面 URL（与 --space-id/--page-id 二选一）")
    parser.add_argument("--space-id", dest="space_id", help="空间 ID")
    parser.add_argument("--page-id", dest="page_id", help="页面 ID")
    parser.add_argument("--output-dir", dest="output_dir", help="输出目录（可选）")
    parser.add_argument("--json-output", action="store_true", help="输出 JSON 结果")
    args = parser.parse_args()

    user_id, token = get_user_and_token()
    if not user_id or not token:
        result = {
            "status": "error",
            "message": "缺少认证信息，请设置环境变量 coclaw_empno 和 coclaw_token",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json_output else result["message"])
        raise SystemExit(1)

    space_id = args.space_id
    page_id = args.page_id
    if args.url:
        parsed = parse_page_link(args.url)
        if not parsed:
            result = {"status": "error", "message": f"无法从 URL 解析 spaceId/pageId: {args.url}"}
            print(json.dumps(result, ensure_ascii=False, indent=2) if args.json_output else result["message"])
            raise SystemExit(1)
        space_id, page_id = parsed

    if not space_id or not page_id:
        result = {
            "status": "error",
            "message": "参数不足：请提供 --url，或同时提供 --space-id 和 --page-id",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json_output else result["message"])
        raise SystemExit(1)

    headers = build_headers(user_id, token)
    output_dir = resolve_output_dir(args.output_dir)
    result = run_convert(space_id, page_id, headers, output_dir)

    if args.json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result.get("status") == "success":
            print(f"# 页面转 Markdown 成功\n\n- 标题: {result['title']}\n- 链接: {result['page_url']}\n- 文件: {result['file_path']}")
        else:
            print(f"# 页面转 Markdown 失败\n\n- 原因: {result.get('message', '未知错误')}")
    raise SystemExit(0 if result.get("status") == "success" else 1)


if __name__ == "__main__":
    main()
