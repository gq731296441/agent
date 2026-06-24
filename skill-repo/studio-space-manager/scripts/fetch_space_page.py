#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=================================================================
studio-space-manager - 获取空间页面模块 (v3.0.0)
=================================================================
iCenter 空间管理技能 - 获取空间页面内容

功能：
- 从 ZTE iCenter 生产环境获取空间页面内容
- 返回原始 HTML 内容（不再转换为 Markdown）

⚠️ **重要变更** (v3.0.0)：
- 移除了 HTML→Markdown 转换步骤
- 直接返回原始 HTML，用于 safe_page_edit.py 安全编辑
- 这样可完全保留宏结构、PlantUML 等特殊格式

返回字段说明：
- html_content: 原始 HTML 内容，推荐用于编辑（100% 保真）
- title: 页面标题
- space_id/page_id: 空间和页面标识

跨平台支持：
- ✅ Linux (Ubuntu/CentOS/Debian)
- ✅ Windows 10/11
- ✅ macOS (通过 Python)

使用方式：
  # 独立运行
  python3 fetch_space_page.py --space-id "空间 ID" --page-id "页面 ID"

  # 或使用 safe_page_edit.py（推荐）
  python3 safe_page_edit.py --action export --url "<URL>" --output-dir ./my-page

依赖：
- Python 3.6+
- requests
- beautifulsoup4（可选，用于 data-ai 注入）

作者：Co-Claw Team
创建：2026-03-05
更新：2026-06-10 (v3.0.0 - 移除 Markdown 转换)
"""

import requests
import json
import argparse
import os
import platform
import tempfile
from typing import Optional, Tuple

# 统一的 user_id/token 环境变量读取逻辑
from system_param import (
    build_icenter_api_url,
    get_user_and_token as _get_user_and_token,
)

# 导入跨平台工具模块
try:
    from _platform_utils import (
        detect_platform,
        get_log_dir,
        get_data_dir,
        SYSTEM,
        SYSTEM_RELEASE,
        IS_WINDOWS,
        configure_stdio_utf8,
    )
except ImportError:
    SYSTEM, SYSTEM_RELEASE, IS_WINDOWS = (
        platform.system(),
        platform.release(),
        platform.system() == "Windows",
    )

    def configure_stdio_utf8():
        import io
        import sys as _sys

        for stream_name in ("stdout", "stderr"):
            stream = getattr(_sys, stream_name, None)
            if stream is None or not hasattr(stream, "buffer"):
                continue
            enc = getattr(stream, "encoding", None) or ""
            if enc.lower() in ("utf-8", "utf8"):
                continue
            try:
                if hasattr(stream, "reconfigure"):
                    stream.reconfigure(encoding="utf-8", errors="replace")
                else:
                    setattr(
                        _sys,
                        stream_name,
                        io.TextIOWrapper(
                            stream.buffer,
                            encoding="utf-8",
                            errors="replace",
                            line_buffering=getattr(stream, "line_buffering", True),
                        ),
                    )
            except (AttributeError, OSError, ValueError, io.UnsupportedOperation):
                continue


def fetch_space_content(space_id, page_id, user_id, token):
    """
    从生产环境获取空间页面内容

    Args:
        space_id: 空间 ID
        page_id: 页面 ID
        user_id: 用户 ID（工号）
        token: 认证令牌

    Returns:
        dict: 包含 HTML 内容和标题的字典，失败返回 None
    """
    if not space_id or not page_id or not user_id or not token:
        return {"status": "error", "message": "缺少必需参数：space_id、page_id、user_id 和 token"}

    url = build_icenter_api_url(
        f"/zte-rd-icenter-contents/content/{page_id}"
        f"?spaceId={space_id}&environmentType=browser&withMacro=true"
    )

    headers = {
        "X-Tenant-Id": "ZTE",
        "X-Auth-Value": token,
        "X-Emp-No": user_id,
        "Content-Type": "application/json;charset=UTF-8",
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        data = response.json()

        # 检查返回码
        code_info = data.get("code", {})
        if code_info.get("code") == "0000" and "bo" in data:
            bo = data.get("bo", {})
            html_content = bo.get("contentBody", "")
            title = bo.get("title", "")

            return {"status": "success", "html_content": html_content, "title": title}
        else:
            error_msg = code_info.get("msg", "未知错误")
            return {
                "status": "error",
                "message": f"获取内容失败：{error_msg}",
                "code": code_info.get("code", ""),
            }

    except requests.exceptions.RequestException as e:
        return {"status": "error", "message": f"请求失败：{str(e)}"}
    except Exception as e:
        return {"status": "error", "message": f"处理失败：{str(e)}"}


def fetch_space_page(space_id, page_id, user_id, token):
    """
    获取空间页面原始 HTML 内容（不再进行 Markdown 转换）

    ⚠️ **重要变更** (v3.0.0)：
    - 本函数现在仅返回原始 HTML 内容，移除了 HTML→Markdown 转换步骤
    - 编辑时请使用 safe_page_edit.py 直接操作 HTML，避免格式丢失
    - 这样可完全保留宏结构、PlantUML 等特殊格式

    推荐使用流程：
    1. 调用本函数或使用 --action export 导出 HTML
    2. 使用 HTML 编辑器编辑
    3. 使用 safe_page_edit.py --action import 回写

    Args:
        space_id: 空间 ID
        page_id: 页面 ID
        user_id: 用户 ID（工号）
        token: 认证令牌

    Returns:
        dict: 包含状态、标题、html_content 的字典
            - status: success/error
            - title: 页面标题
            - html_content: 原始 HTML 内容（推荐用于编辑）
            - space_id: 空间 ID
            - page_id: 页面 ID
    """
    # 获取 HTML 内容（不做任何转换）
    content_result = fetch_space_content(space_id, page_id, user_id, token)

    if content_result.get("status") != "success":
        return content_result

    html_content = content_result.get("html_content", "")
    title = content_result.get("title", "")

    if not html_content:
        return {"status": "error", "message": "获取的内容为空"}

    return {
        "status": "success",
        "title": title,
        "html_content": html_content,  # ⭐ 原始 HTML，用于安全编辑（无格式损失）
        "space_id": space_id,
        "page_id": page_id,
    }


def format_result(result):
    """
    将结果格式化为可读文本（显示 HTML 内容摘要）

    Args:
        result: 结果字典

    Returns:
        str: 可读文本
    """
    if result.get("status") == "success":
        title = result.get("title", "无标题")
        html_content = result.get("html_content", "")
        space_id = result.get("space_id", "")
        page_id = result.get("page_id", "")

        # 生成 HTML 内容的简短摘要（前 500 字符）
        html_preview = html_content[:500] + "..." if len(html_content) > 500 else html_content
        # 去掉 HTML 标签，只显示文本预览
        import re
        text_preview = re.sub(r'<[^>]+>', '', html_preview).strip()
        if text_preview:
            text_preview = text_preview[:200] + "..." if len(text_preview) > 200 else text_preview

        output = f"# {title}\n\n"
        if space_id or page_id:
            output += "## 页面信息\n\n"
            if space_id:
                output += f"- **空间 ID**: `{space_id}`\n"
            if page_id:
                output += f"- **页面 ID**: `{page_id}`\n"
            output += "\n"

        output += "## HTML 内容\n\n"
        output += f"HTML 长度：{len(html_content)} 字符\n\n"
        if text_preview:
            output += f"**内容预览**:\n{text_preview}\n\n"
        output += "> 💡 **提示**: 完整 HTML 内容可通过编程方式获取，或使用 safe_page_edit.py 导出为文件\n"
        return output
    else:
        error_msg = result.get("message", "未知错误")
        output = f"# 错误\n\n"
        output += f"❌ {error_msg}\n"
        if result.get("code"):
            output += f"\n**错误代码**: `{result.get('code')}`\n"
        return output


def get_user_and_token() -> tuple:
    """返回 (user_id, token)，优先从环境变量获取（非 strict）。"""
    return _get_user_and_token(strict=False)


if __name__ == "__main__":
    configure_stdio_utf8()

    parser = argparse.ArgumentParser(
        description="读取 ZTE iCenter 生产环境空间页面（返回原始 HTML）",
        epilog="提示：用户 ID 和 Token 从环境变量 coclaw_empno 和 coclaw_token 获取\n\n"
               "推荐使用 safe_page_edit.py 进行页面编辑：\n"
               "  python3 safe_page_edit.py --action export --url '<URL>' --output-dir ./my-page",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--space-id", dest="space_id", required=True, help="空间 ID（必需）")
    parser.add_argument("--page-id", dest="page_id", required=True, help="页面 ID（必需）")
    parser.add_argument("--json-output", action="store_true", help="输出 JSON 格式结果")

    args = parser.parse_args()

    # 从环境变量获取工号和 token
    user_id, token = get_user_and_token()

    if not user_id or not token:
        error_result = {
            "status": "error",
            "message": "缺少必需参数：user_id 和 token。请设置环境变量 coclaw_empno 和 coclaw_token",
        }
        print(json.dumps(error_result, ensure_ascii=False, indent=2) if args.json_output
              else f"❌ {error_result['message']}")
        exit(1)

    result = fetch_space_page(args.space_id, args.page_id, user_id, token)

    if args.json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_result(result))

    exit(0 if result.get("status") == "success" else 1)


# =============================================================================
# 跨平台支持 - 平台检测
# =============================================================================


def detect_platform() -> Tuple[str, str, bool]:
    """
    检测当前操作系统平台。
    """
    system = platform.system()
    release = platform.release()
    is_windows = system == "Windows"
    return system, release, is_windows


def get_log_dir() -> str:
    """
    获取日志目录，支持跨平台运行。
    """
    system, _, is_windows = detect_platform()

    if is_windows or os.name != "posix":
        temp_base = tempfile.gettempdir()
        log_dir = os.path.join(temp_base, "studio-space-manager", "log")
    else:
        log_dir = os.path.join(
            os.path.expanduser("~"),
            ".openclaw",
            "workspace",
            "skills",
            "studio-space-manager",
            "log",
        )

    try:
        os.makedirs(log_dir, exist_ok=True)
        if not is_windows and os.name == "posix":
            os.chmod(log_dir, 0o700)
    except Exception:
        temp_base = tempfile.gettempdir()
        log_dir = os.path.join(temp_base, "studio-space-manager", "log")
        os.makedirs(log_dir, exist_ok=True)

    return log_dir


# 初始化平台信息
SYSTEM, SYSTEM_RELEASE, IS_WINDOWS = detect_platform()
