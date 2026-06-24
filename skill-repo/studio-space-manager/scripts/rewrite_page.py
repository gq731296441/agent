#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=================================================================
studio-space-manager - 更新空间页面标题模块 (v1.1.0)
=================================================================
iCenter 空间管理技能 - 更新指定页面的标题（保留原内容）

功能：
- 根据用户提供页面URL或页面ID，读取页面内容
- 修改页面标题为用户指定的新标题
- 使用 coredata updateContent 落库（避免丢失原内容）

跨平台支持：
- ✅ Linux (Ubuntu/CentOS/Debian)
- ✅ Windows 10/11
- ✅ macOS (通过 Python)
"""

import os
import sys
import json
import time
import re
import argparse

# 添加技能根目录到Python路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
BASE_DIR = os.path.dirname(SKILL_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, SKILL_DIR)
sys.path.insert(0, BASE_DIR)

from system_param import (
    build_icenter_api_url,
    build_system_param,
    get_user_and_token as _get_user_and_token,
)
from _platform_utils import configure_stdio_utf8, get_platform_and_user_agent


def _json_mode_log(message: str) -> None:
    """--json-output 时向 stderr 打一行日志，避免污染 stdout 的纯 JSON。"""
    print(f"[create_page_from_referPage] {message}", file=sys.stderr)


def get_user_and_token():
    """返回 (user_id, token)，优先从环境变量获取（非 strict）。"""
    return _get_user_and_token(strict=False)


def parse_space_page_url(url):
    """
    从URL中解析spaceId和pageId

    Args:
        url: 页面URL，支持多种格式

    Returns:
        Tuple[str, str]: (spaceId, pageId)
    """
    patterns = [
        r"/shared/([a-f0-9]+)/wiki/page/([a-f0-9]+)/view",
        r"/space/([a-f0-9]+)/wiki/page/([a-f0-9]+)/view",
        r"/ispace/#/space/([a-f0-9]+)/wiki/page/([a-f0-9]+)/view",
    ]

    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1), match.group(2)

    return None, None


def fetch_space_content_v2(space_id, page_id, user_id, token):
    """
    获取空间2.0页面内容（v2）

    Returns:
        dict: {status, title, apool, attribs, text, html_content, parent_id} 或 {status, message}
    """
    if not all([space_id, page_id, user_id, token]):
        return {
            "status": "error",
            "message": "缺少必需参数：space_id、page_id、user_id 和 token",
        }

    # system 参数需要与其它接口保持一致（由 system_param 统一生成）
    system_param = build_system_param("space")
    t_ms = int(time.time() * 1000)
    url = build_icenter_api_url(
        "/zte-rdcloud-space-external/content/"
        f"{page_id}?t={t_ms}&spaceId={space_id}&environmentType=browser&system={system_param}"
    )

    platform_type, user_agent = get_platform_and_user_agent()
    headers = {
        "Accept": "application/json, text/plain",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://i.zte.com.cn",
        "Referer": "https://i.zte.com.cn/",
        "User-Agent": user_agent,
        "X-Auth-Value": token,
        "X-Emp-No": user_id,
        "X-Tenant-Id": "ZTE",
        "X-Lang-Id": "zh_CN",
    }

    try:
        import requests  # type: ignore

        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()

        code_info = data.get("code", {})
        if code_info and code_info.get("code") not in ("0000", None):
            return {
                "status": "error",
                "message": f"获取内容失败: {code_info.get('msg', '未知错误')}",
            }

        bo = data.get("bo", data)
        # v2 接口返回的“页面内容”本质是一套结构化数据（文档示例为 bo.apool/numToAttrib）。
        # 这里不做 HTML 转换，直接取原始结构交给更新接口处理。
        apool = bo.get("apool") if isinstance(bo, dict) else None
        attribs = bo.get("attribs") if isinstance(bo, dict) else None
        # 将 bo.contentBody 作为最终“文本/内容”字段返回给调用方
        text = ""
        if isinstance(bo, dict):
            text = bo.get("contentBody")
        title = ""
        if isinstance(bo, dict):
            title = bo.get("title") or bo.get("_title") or ""
        if not title and isinstance(apool, dict):
            num_to_attrib = apool.get("numToAttrib")
            if isinstance(num_to_attrib, dict):
                for _, pair_list in num_to_attrib.items():
                    if (
                        isinstance(pair_list, list)
                        and len(pair_list) == 2
                        and pair_list[0] == "_title"
                    ):
                        title = pair_list[1] or ""
                        break
        parent_id = bo.get("parentId") or bo.get("parentContentId") or ""
        revision = bo.get("revision") if isinstance(bo, dict) else None
        doc_type = ""
        if isinstance(bo, dict):
            doc_type = (
                bo.get("docType") or bo.get("contentType") or bo.get("doc_type") or ""
            )
        if not doc_type:
            doc_type = "ARTICLE"
        is_leaf = bo.get("isLeaf") if isinstance(bo, dict) else None
        sort_no = bo.get("sortNo") if isinstance(bo, dict) else None
        disable_new_macro_id = None
        if isinstance(bo, dict):
            disable_new_macro_id = (
                bo.get("disableNewMacroId")
                if "disableNewMacroId" in bo
                else bo.get("disable_new_macro_id")
            )

        return {
            "status": "success",
            "title": title,
            "apool": apool,
            "attribs": attribs,
            "text": text,
            "parent_id": parent_id,
            "revision": revision,
            "doc_type": doc_type,
            "is_leaf": is_leaf,
            "sort_no": sort_no,
            "disable_new_macro_id": disable_new_macro_id,
        }
    except Exception as e:
        return {"status": "error", "message": f"请求失败: {str(e)}"}


def _update_title_via_coredata(
    page_id,
    space_id,
    user_id,
    token,
    desired_title,
    parent_id,
    json_output=False,
    content_text=None,
    content_attribs=None,
    content_apool=None,
    doc_type=None,
    revision=None,
    is_leaf=None,
    sort_no=None,
    disable_new_macro_id=None,
):
    """
    使用 coredata 的 updateContent 直接落库页面 title。
    desired_title 为用户指定标题时传入用户值；否则传入源页面标题。
    """
    # 如果调用方未显式传入内容字段，则回退从“目标页”抓取上下文再更新。
    final_title = desired_title or ""

    resolved_parent_id = parent_id or ""

    # curl 示例里 updateContent 需要较完整的字段。
    body = {
        "id": page_id,
        "bizId": space_id,
        "docType": doc_type,
        "parentId": resolved_parent_id,
        "title": final_title,
        "revision": revision,
        "text": content_text or "",
        "attribs": content_attribs or "",
        "apool": content_apool,
        "isLeaf": is_leaf,
        "sortNo": sort_no,
        "disableNewMacroId": disable_new_macro_id,
    }

    headers = {
        "Content-Type": "application/json",
        "X-Emp-No": user_id,
        "X-Auth-value": token,
        "X-Lang-Id": "zh_CN",
        "X-Tenant-Id": "ZTE",
    }
    system_param = build_system_param("space")
    url = build_icenter_api_url(
        f"/zte-rdcloud-space-external/content/{page_id}/updateContent?spaceId={space_id}&system={system_param}"
    )
    try:
        import requests  # type: ignore

        if json_output:
            _json_mode_log(
                f"调用 coredata updateContent page_id={page_id} title={final_title!r}"
            )
        resp = requests.post(url, headers=headers, json=body, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code", {}).get("code") == "0000":
            return {
                "status": "success",
                "title": final_title,
                "title_update_result": data.get("bo"),
            }

        return {
            "status": "error",
            "message": data.get("code", {}).get("msg", "coredata updateContent 失败"),
        }
    except Exception as e:
        return {"status": "error", "message": f"coredata updateContent 请求失败: {str(e)}"}


def parse_title_from_command(command):
    """
    从用户指令中解析新页面名称

    Args:
        command: 用户指令字符串

    Returns:
        str 或 None: 解析出的页面名称
    """
    patterns = [
        r"命名为[：:]\s*(.+?)(?:\s|$)",
        r"名字叫\s*(.+?)(?:\s|$)",
        r"名称为[：:]\s*(.+?)(?:\s|$)",
        r"叫\s*(.+?)(?:\s|$)",
        r"标题[为是：:]\s*(.+?)(?:\s|$)",
    ]

    for pattern in patterns:
        match = re.search(pattern, command)
        if match:
            title = match.group(1).strip()
            title = re.sub(r"[。.，,；;]$", "", title)
            if title:
                return title

    return None


def format_result_as_text(result):
    """将结果格式化为文本"""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return result

    if result.get("status") == "success":
        lines = [
            "=" * 60,
            "📋 基于模板创建页面结果",
            "=" * 60,
            f"状态: ✅ 成功",
            f"新页面标题: {result.get('title', '')}",
            f"新页面ID: {result.get('new_page_id', '')}",
            f"新页面链接: {result.get('new_page_url', '')}",
            f"目标空间ID: {result.get('template_space_id', '')}",
            f"父页面ID: {result.get('parent_id', '')}",
            "=" * 60,
        ]
        return "\n".join(lines)
    else:
        lines = [
            "=" * 60,
            "❌ 页面操作失败",
            "=" * 60,
            f"错误信息: {result.get('message', '未知错误')}",
            "=" * 60,
        ]
        return "\n".join(lines)


def _json_or_text_error(message: str, json_output: bool) -> None:
    """输出错误并退出，避免 main 里重复的 if/else。"""
    if json_output:
        _json_mode_log(f"错误: {message}")
        print(json.dumps({"status": "error", "message": message}, ensure_ascii=False))
    else:
        print(f"❌ {message}")
    sys.exit(1)


def _build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器。抽取到函数后让 main 更线性。"""
    parser = argparse.ArgumentParser(
        description="更新指定空间页面的标题",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 更新页面标题（推荐：用页面URL）
  python3 rewrite_page.py --page-url "https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view" --title "新标题"

  # 更新页面标题（也可直接传 spaceId + pageId）
  python3 rewrite_page.py --space-id "{spaceId}" --page-id "{pageId}" --title "新标题"

  # JSON格式输出（用于程序调用）
  python3 rewrite_page.py --page-url "..." --title "新标题" --json-output
        """,
    )

    parser.add_argument(
        "--page-url",
        type=str,
        help="待更新页面URL，格式: https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view",
    )
    parser.add_argument(
        "--space-id",
        type=str,
        help="待更新页面所在空间ID（与--page-url二选一）",
    )
    parser.add_argument(
        "--page-id",
        type=str,
        help="待更新页面ID（与--page-url二选一）",
    )

    parser.add_argument(
        "--title",
        type=str,
        required=True,
        help="新页面标题",
    )

    parser.add_argument(
        "--json-output",
        action="store_true",
        help="JSON格式输出（用于程序调用）",
    )
    return parser


def main():
    configure_stdio_utf8()
    args = _build_parser().parse_args()
    json_output = bool(args.json_output)

    # 1) 鉴权
    user_id, token = get_user_and_token()
    if not user_id or not token:
        _json_or_text_error(
            "无法获取用户账号和Token，请确保已设置 coclaw_empno 和 coclaw_token 环境变量，如仍报错请重启 Co-Claw 服务后重试",
            json_output,
        )

    # 2) 解析“待更新页面”（用户指定那一页）
    page_url = getattr(args, "page_url", None)
    space_id = getattr(args, "space_id", None)
    page_id = getattr(args, "page_id", None)

    if page_url:
        parsed_space_id, parsed_page_id = parse_space_page_url(page_url)
        if not parsed_space_id or not parsed_page_id:
            _json_or_text_error("无法从URL解析spaceId和pageId", json_output)
        space_id, page_id = parsed_space_id, parsed_page_id

    if not space_id or not page_id:
        _json_or_text_error("请提供 --page-url 或 --space-id + --page-id", json_output)

    desired_title = args.title

    if json_output:
        _json_mode_log(f"目标页面 space_id={space_id} page_id={page_id} 开始拉取页面内容...")
    else:
        print(f"\n📄 目标页面")
        print(f"   空间ID: {space_id}")
        print(f"   页面ID: {page_id}")
        print(f"\n✏️  更新标题为：{desired_title}")
        print(f"\n⏳ 正在获取页面内容...")

    # 3) 获取内容后：仅更新标题（保留原内容）
    page_result = fetch_space_content_v2(space_id, page_id, user_id, token)
    if page_result.get("status") != "success":
        _json_or_text_error(page_result.get("message", "获取页面内容失败"), json_output)

    html_content = page_result.get("text", "")
    parent_id = page_result.get("parent_id", "") or ""
    doc_type = page_result.get("doc_type") or "ARTICLE"

    title_update_result = _update_title_via_coredata(
        page_id=page_id,
        space_id=space_id,
        user_id=user_id,
        token=token,
        desired_title=desired_title,
        parent_id=parent_id,
        json_output=json_output,
        content_text=html_content,
        content_attribs=page_result.get("attribs"),
        content_apool=page_result.get("apool"),
        doc_type=doc_type,
        revision=page_result.get("revision") or 1,
        is_leaf=page_result.get("is_leaf"),
        sort_no=page_result.get("sort_no") or 0,
        disable_new_macro_id=page_result.get("disable_new_macro_id") or False,
    )

    if title_update_result.get("status") != "success":
        final = {
            "status": "error",
            "message": title_update_result.get("message", "coredata 更新失败"),
            "page_id": page_id,
            "space_id": space_id,
        }
        if json_output:
            print(json.dumps(final, ensure_ascii=False))
        else:
            print(f"❌ {final['message']}")
        return

    final = {
        "status": "success",
        "message": "页面标题更新成功",
        "page_id": page_id,
        "space_id": space_id,
        "title": desired_title,
        "title_update_result": title_update_result,
    }

    if json_output:
        print(json.dumps(final, ensure_ascii=False))
    else:
        print(
            f"✅ 页面标题更新成功\n   空间ID: {space_id}\n   页面ID: {page_id}\n   新标题: {desired_title}"
        )


if __name__ == "__main__":
    main()
