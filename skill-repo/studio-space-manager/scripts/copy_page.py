#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=================================================================
studio-space-manager - 复制空间页面模块 (v1.0.0)
=================================================================
iCenter 空间管理技能 - 复制页面到同级或指定目录

功能：
- 根据页面URL或页面ID，复制页面到同级目录或指定目标目录
- 支持解析URL提取spaceId和pageId作为入参
- 复制后可获得新页面ID，用于后续操作

跨平台支持：
- ✅ Linux (Ubuntu/CentOS/Debian)
- ✅ Windows 10/11
- ✅ macOS (通过 Python)

使用方式：
  # 从URL复制页面到同级目录
  python3 copy_page.py --url "https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view"

  # 复制到指定目标目录（通过URL指定目标父页面）
  python3 copy_page.py --url "https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view" --target-url "https://i.zte.com.cn/#/shared/{targetSpaceId}/wiki/page/{targetParentId}/view"

  # 直接指定参数
  python3 copy_page.py --space-id "源空间ID" --page-id "源页面ID" --target-space-id "目标空间ID" --target-node-id "目标父页面ID"

  # JSON格式输出（用于程序调用）
  python3 copy_page.py --space-id "xxx" --page-id "xxx" --json-output

依赖：
- Python 3.6+
- requests

作者：Co-Claw Team
创建：2026-03-12
更新：2026-03-12
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
# 既支持从技能根目录导入，也支持从当前 scripts 目录导入公共模块（如 system_param）
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, BASE_DIR)

from system_param import (
    build_icenter_api_url,
    build_system_param,
    get_user_and_token,
)
from _platform_utils import configure_stdio_utf8, get_platform_and_user_agent


def _json_mode_log(message: str) -> None:
    """--json-output 时向 stderr 打一行日志，避免污染 stdout 的纯 JSON。"""
    print(f"[copy_page] {message}", file=sys.stderr)


def get_direct_children_count(space_id: str, page_id: str) -> int:
    """
    通过复用 `get_page_children.py` 的 get_page_children 接口，
    获取某页面的「直接子页面」数量。
    """
    try:
        import get_page_children

        raw = get_page_children.get_page_children(space_id, page_id, json_output=True)
        if not raw:
            return 0
        if isinstance(raw, str):
            data = json.loads(raw)
        elif isinstance(raw, dict):
            data = raw
        else:
            return 0
        return int(data.get("children_count") or 0)
    except Exception:
        return 0


def normalize_title_replace(title_source: str, title_target: str):
    """
    normalize `copyWithSpace` 的标题替换参数。

    - title_source 为空 且 title_target 为空：视为不做替换
    - 仅传了其中一个：视为参数错误（必须成对出现）
    """
    title_source = (title_source or "").strip()
    title_target = (title_target or "").strip()
    if not title_source and not title_target:
        return "", ""
    if not title_source or not title_target:
        raise ValueError("标题替换参数必须同时提供 --title-source 和 --title-target")
    return title_source, title_target


def parse_space_page_url(url):
    """
    从URL中解析spaceId和pageId

    Args:
        url: 页面URL，支持两种格式：
            - https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view
            - https://i.zte.com.cn/index/ispace/#/space/{spaceId}/wiki/page/{pageId}/view

    Returns:
        Tuple[str, str]: (spaceId, pageId)
    """
    # 匹配 /shared/{spaceId}/wiki/page/{pageId}/view 格式
    pattern1 = r"/shared/([a-f0-9]+)/wiki/page/([a-f0-9]+)/view"
    match = re.search(pattern1, url)
    if match:
        return match.group(1), match.group(2)

    # 匹配 /space/{spaceId}/wiki/page/{pageId}/view 格式（带 index/ispace 前缀）
    pattern2 = r"/space/([a-f0-9]+)/wiki/page/([a-f0-9]+)/view"
    match = re.search(pattern2, url)
    if match:
        return match.group(1), match.group(2)

    return None, None


def get_page_parent_id(space_id, page_id, user_id, token):
    """
    获取页面的父页面ID

    Args:
        space_id: 空间ID
        page_id: 页面ID
        user_id: 用户ID
        token: 认证令牌

    Returns:
        str: 父页面ID，如果获取失败返回None
    """
    url = build_icenter_api_url(
        f"/zte-rd-icenter-contents/content/{page_id}?spaceId={space_id}&environmentType=browser"
    )

    headers = {
        "Accept": "application/json",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://i.zte.com.cn",
        "Referer": "https://i.zte.com.cn/",
        "User-Agent": get_platform_and_user_agent()[1],
        "X-Auth-Value": token,
        "X-Cookie-From-Cros": "Y",
        "X-Emp-No": user_id,
        "X-Lang-Id": "zh_CN",
        "X-Tenant-Id": "ZTE",
    }
    headers["apm_biz_key"] = json.dumps(
        {
            "host_ip": "i.zte.com.cn",
            "serviceName": "ICENTER_zte-km-icenter-ui",
            "X-Emp-No": user_id,
        }
    )

    try:
        import requests

        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        data = response.json()
        if data.get("code", {}).get("code") == "0000":
            bo = data.get("bo", {})
            # 父页面ID可能在parentId或parentContentId字段中
            return bo.get("parentId") or bo.get("parentContentId")
    except Exception:
        pass
    return None


def get_parentPageId(space_id, page_id, user_id, token):
    """
    对外接口：获取页面 parentPageId。

    Returns:
        dict: {
            "status": "success"|"error",
            "space_id": str,
            "page_id": str,
            "parent_page_id": str
        }
    """
    parent_page_id = get_page_parent_id(space_id, page_id, user_id, token)
    if parent_page_id:
        return {
            "status": "success",
            "space_id": space_id,
            "page_id": page_id,
            "parent_page_id": parent_page_id,
        }
    return {
        "status": "error",
        "message": "无法获取父页面ID，请确认页面存在且当前账号有访问权限",
        "space_id": space_id,
        "page_id": page_id,
        "parent_page_id": "",
    }


def get_space_main_page_id(space_id: str, user_id: str, token: str) -> str:
    """
    获取空间主页（MAINPAGE）的页面ID。
    接口：GET /zte-rdcloud-space-content/content/mainPageWithBody/
    """
    if not space_id or not user_id or not token:
        return ""

    timestamp = int(time.time() * 1000)
    system_param = build_system_param("space")
    url = build_icenter_api_url(
        "/zte-rdcloud-space-content/content/mainPageWithBody/"
        f"?t={timestamp}&environmentType=browser&system={system_param}&spaceId={space_id}"
    )
    platform_type, user_agent = get_platform_and_user_agent()
    headers = {
        "Accept": "application/json, text/plain",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://i.zte.com.cn",
        "Referer": "https://i.zte.com.cn/",
        "User-Agent": user_agent,
        "X-Auth-Value": token,
        "X-Cookie-From-Cros": "Y",
        "X-Emp-No": user_id,
        "X-Lang-Id": "zh_CN",
        "X-Tenant-Id": "ZTE",
    }
    headers["apm_biz_key"] = json.dumps(
        {
            "host_ip": "i.zte.com.cn",
            "serviceName": "ICENTER_zte-km-icenter-ui",
            "X-Emp-No": user_id,
        }
    )
    try:
        import requests

        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code", {}).get("code") == "0000":
            bo = data.get("bo") or {}
            return (bo.get("id") or "").strip()
    except Exception:
        return ""
    return ""


def copy_page(
    space_id,
    page_id,
    target_space_id,
    target_node_id,
    user_id,
    token,
    level=0,
    title_replace_source="",
    title_replace_target="",
    json_output=False,
):
    """
    复制页面到目标目录

    Args:
        space_id: 源页面所在空间ID
        page_id: 源页面ID (nodeId)
        target_space_id: 目标空间ID (targetSpaceId)
        target_node_id: 目标父页面ID (targetNodeId)，复制到该页面下方；未指定时自动使用目标空间主页ID
        user_id: 用户ID（工号）
        token: 认证令牌
        level: 复制层级（0=仅复制当前页面；1=当前页及下所有层级子页面，即整棵子树）
        title_replace_source: 标题替换的原文本（copyWithSpace: request_body.source）
        title_replace_target: 标题替换的新文本（copyWithSpace: request_body.target）
        json_output: 是否JSON格式输出

    Returns:
        dict: 包含复制结果的字典
    """
    if not all([space_id, page_id, user_id, token]):
        error_result = {
            "status": "error",
            "message": "缺少必需参数：space_id、page_id、user_id 和 token",
        }
        if json_output:
            _json_mode_log(f"错误: {error_result['message']}")
            return json.dumps(error_result, ensure_ascii=False)
        print(f"❌ {error_result['message']}")
        return error_result

    # level=1 为整棵子树复制：先检查直接子页数量（阈值），避免系统堵塞
    if int(level) == 1:
        children_count = get_direct_children_count(space_id, page_id)
        if children_count > 100:
            msg = "子页面太多可能引起系统堵塞，不能执行。"
            error_result = {
                "status": "error",
                "message": msg,
                "children_count": children_count,
                "threshold": 100,
            }
            if json_output:
                _json_mode_log(
                    f"错误: {msg} children_count={children_count} threshold=100"
                )
                return json.dumps(error_result, ensure_ascii=False)
            print(f"❌ {msg}")
            return error_result
        if json_output:
            _json_mode_log(f"level=1 子页面数量={children_count}，未超过阈值 100，继续调用复制接口")

    # 如果目标空间未指定，使用源空间
    if not target_space_id:
        target_space_id = space_id

    # 如果目标节点未指定，使用目标空间主页ID作为目标父节点
    if not target_node_id:
        main_page_id = get_space_main_page_id(target_space_id, user_id, token)
        if not main_page_id:
            error_result = {
                "status": "error",
                "message": f"无法获取目标空间主页ID，请确认空间ID有效且当前账号有访问权限（spaceId: {target_space_id}）",
            }
            if json_output:
                _json_mode_log(f"错误: {error_result['message']}")
                return json.dumps(error_result, ensure_ascii=False)
            print(f"❌ {error_result['message']}")
            return error_result
        target_node_id = main_page_id

    # 构建请求URL
    timestamp = int(time.time() * 1000)
    system_param = build_system_param("space")
    url = build_icenter_api_url(
        f"/zte-rdcloud-space-content/content/{page_id}/copyWithSpace"
        f"?t={timestamp}"
        f"&spaceId={space_id}"
        f"&environmentType=browser"
        f"&system={system_param}"
    )

    # 构建请求头
    platform_type, user_agent = get_platform_and_user_agent()
    headers = {
        "Accept": "application/json, text/plain",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://i.zte.com.cn",
        "Referer": "https://i.zte.com.cn/",
        "User-Agent": user_agent,
        "X-Auth-Value": token,
        "X-Cookie-From-Cros": "Y",
        "X-Emp-No": user_id,
        "X-Lang-Id": "zh_CN",
        "X-Tenant-Id": "ZTE",
    }
    headers["apm_biz_key"] = json.dumps(
        {
            "host_ip": "i.zte.com.cn",
            "serviceName": "ICENTER_zte-km-icenter-ui",
            "X-Emp-No": user_id,
        }
    )

    # 构建请求体
    request_body = {
        "level": str(int(level)),
        "nodeId": page_id,
        "targetNodeId": target_node_id,
        "spaceId": space_id,
        "targetSpaceId": target_space_id,
        "source": title_replace_source or "",
        "target": title_replace_target or "",
    }

    if json_output:
        _json_mode_log(
            f"调用复制接口 copyWithSpace level={int(level)} "
            f"space_id={space_id} page_id={page_id} "
            f"target_space_id={target_space_id} target_parent_id={target_node_id}"
        )

    try:
        import requests

        response = requests.post(url, json=request_body, headers=headers, timeout=30)
        response.raise_for_status()

        data = response.json()

        # 检查返回码
        code_info = data.get("code", {})
        if code_info.get("code") == "0000" and "bo" in data:
            bo = data.get("bo", {})
            new_content_id = bo.get("contentId", "")
            new_root_id = bo.get("newTargetRootId", "")

            result = {
                "status": "success",
                "message": "页面复制成功",
                "new_page_id": new_content_id,
                "new_page_root_id": new_root_id,
                "source_space_id": space_id,
                "source_page_id": page_id,
                "target_space_id": target_space_id,
                "target_parent_id": target_node_id,
                "new_page_url": f"https://i.zte.com.cn/#/shared/{target_space_id}/wiki/page/{new_content_id}/view",
            }

            if json_output:
                _json_mode_log(
                    f"成功: 页面复制成功 new_page_id={new_content_id} "
                    f"target_space_id={target_space_id} target_parent_id={target_node_id}"
                )
                return json.dumps(result, ensure_ascii=False)

            # 格式化输出
            print(f"\n✅ 页面复制成功!")
            print(f"   新页面ID: {new_content_id}")
            print(f"   新页面链接: {result['new_page_url']}")
            print(f"   源空间: {space_id} → 目标空间: {target_space_id}")
            return result
        else:
            error_msg = code_info.get("msg", "未知错误")
            error_result = {
                "status": "error",
                "message": f"复制失败: {error_msg}",
                "code": code_info.get("code", ""),
            }
            if json_output:
                _json_mode_log(f"错误: {error_result['message']}")
                return json.dumps(error_result, ensure_ascii=False)
            print(f"❌ {error_result['message']}")
            return error_result

    except requests.exceptions.Timeout:
        error_result = {"status": "error", "message": "复制失败: 请求超时"}
        if json_output:
            _json_mode_log(f"错误: {error_result['message']}")
            return json.dumps(error_result, ensure_ascii=False)
        print(f"❌ {error_result['message']}")
        return error_result
    except requests.exceptions.RequestException as e:
        error_result = {"status": "error", "message": f"复制失败: 网络请求错误 - {str(e)}"}
        if json_output:
            _json_mode_log(f"错误: {error_result['message']}")
            return json.dumps(error_result, ensure_ascii=False)
        print(f"❌ {error_result['message']}")
        return error_result
    except json.JSONDecodeError:
        error_result = {"status": "error", "message": "复制失败: 返回数据解析错误"}
        if json_output:
            _json_mode_log(f"错误: {error_result['message']}")
            return json.dumps(error_result, ensure_ascii=False)
        print(f"❌ {error_result['message']}")
        return error_result


def format_result_as_text(result):
    """
    将结果格式化为文本格式

    Args:
        result: 结果字典或JSON字符串

    Returns:
        str: 格式化的文本
    """
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return result

    if result.get("status") == "success":
        output = []
        output.append("=" * 60)
        output.append("📋 页面复制结果")
        output.append("=" * 60)
        output.append(f"状态: ✅ 成功")
        output.append(f"新页面ID: {result.get('new_page_id', '')}")
        output.append(f"新页面链接: {result.get('new_page_url', '')}")
        output.append(f"源页面ID: {result.get('source_page_id', '')}")
        output.append(f"源空间ID: {result.get('source_space_id', '')}")
        output.append(f"目标空间ID: {result.get('target_space_id', '')}")
        output.append(f"目标父页面ID: {result.get('target_parent_id', '')}")
        output.append("=" * 60)
        return "\n".join(output)
    else:
        output = []
        output.append("=" * 60)
        output.append("❌ 页面复制失败")
        output.append("=" * 60)
        output.append(f"错误信息: {result.get('message', '未知错误')}")
        if result.get("code"):
            output.append(f"错误代码: {result.get('code')}")
        output.append("=" * 60)
        return "\n".join(output)


def main():
    parser = argparse.ArgumentParser(
        description="复制 iCenter 空间页面到同级目录或指定目录",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 复制页面到同级目录
  python3 copy_page.py --url "https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view"
  
  # 复制到指定目标目录（使用URL指定目标父页面）
  python3 copy_page.py --url "https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view" \\
      --target-url "https://i.zte.com.cn/#/shared/{targetSpaceId}/wiki/page/{targetParentId}/view"
  
  # 直接指定参数
  python3 copy_page.py --space-id "xxx" --page-id "xxx" --target-space-id "yyy" --target-node-id "zzz"

  # 指定复制层级（0=仅当前页；1=当前页及下所有层级子页面，整棵子树）
  python3 copy_page.py --space-id "xxx" --page-id "xxx" --level 1
  
  # 复制时替换标题文本：将标题中匹配的“源文本”替换为“新文本”
  python3 copy_page.py --space-id "源空间ID" --page-id "源页面ID" \
    --target-space-id "目标空间ID" --target-node-id "目标父页面ID" \
    --title-source "源文本" --title-target "新文本"
  
  # JSON格式输出（用于程序调用）
  python3 copy_page.py --space-id "xxx" --page-id "xxx" --json-output
        """,
    )

    # 源页面参数（支持URL或直接参数）
    parser.add_argument(
        "--url",
        type=str,
        help="源页面URL，格式: https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view",
    )
    parser.add_argument("--space-id", type=str, help="源页面所在空间ID（与--url二选一）")
    parser.add_argument("--page-id", type=str, help="源页面ID（与--url二选一）")

    # 目标页面参数
    parser.add_argument(
        "--target-url",
        type=str,
        help="目标父页面URL（可选），如果指定则从中解析targetSpaceId和targetNodeId",
    )
    parser.add_argument("--target-space-id", type=str, help="目标空间ID（可选），默认使用源空间ID")
    parser.add_argument(
        "--target-node-id", type=str, help="目标父页面ID（可选），不传则自动使用目标空间主页ID作为目标父页面ID"
    )
    parser.add_argument(
        "--get-parent-page-id",
        action="store_true",
        help="仅获取源页面的父页面ID（get_parentPageId接口），不执行复制",
    )

    # 输出格式
    parser.add_argument("--json-output", action="store_true", help="JSON格式输出（用于程序调用）")
    parser.add_argument(
        "--level",
        type=int,
        default=0,
        choices=[0, 1],
        help="复制层级：0=仅复制当前页面；1=当前页及下所有层级子页面即整棵子树（默认0）",
    )

    parser.add_argument(
        "--title-source",
        type=str,
        default=None,
        help="复制页面时：将标题中匹配的原文本替换为 --title-target（可选，需与 --title-target 配对）",
    )
    parser.add_argument(
        "--title-target",
        type=str,
        default=None,
        help="复制页面时：标题替换后的新文本（可选，需与 --title-source 配对）",
    )

    args = parser.parse_args()

    # 获取认证信息
    user_id, token = get_user_and_token(json_output=args.json_output)

    # 解析源页面参数
    if args.url:
        space_id, page_id = parse_space_page_url(args.url)
        if not space_id or not page_id:
            error_msg = "无法从URL解析spaceId和pageId"
            if args.json_output:
                _json_mode_log(f"错误: {error_msg}")
                print(
                    json.dumps(
                        {"status": "error", "message": error_msg}, ensure_ascii=False
                    )
                )
            else:
                print(f"❌ {error_msg}")
            sys.exit(1)
    elif args.space_id and args.page_id:
        space_id = args.space_id
        page_id = args.page_id
    else:
        if args.json_output:
            _json_mode_log("错误: 请提供 --url 或 --space-id + --page-id 参数")
            print(
                json.dumps(
                    {
                        "status": "error",
                        "message": "请提供 --url 或 --space-id + --page-id 参数",
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print("❌ 请提供 --url 或 --space-id + --page-id 参数")
        sys.exit(1)

    # 解析目标参数
    target_space_id = args.target_space_id
    target_node_id = args.target_node_id

    if args.target_url:
        ts, tn = parse_space_page_url(args.target_url)
        if ts:
            target_space_id = ts
        if tn:
            target_node_id = tn

    # 标题替换参数校验与归一化
    try:
        title_replace_source, title_replace_target = normalize_title_replace(
            title_source=args.title_source,
            title_target=args.title_target,
        )
    except ValueError as e:
        error_msg = str(e)
        if args.json_output:
            _json_mode_log(f"错误: {error_msg}")
            print(
                json.dumps(
                    {"status": "error", "message": error_msg}, ensure_ascii=False
                )
            )
        else:
            print(f"❌ {error_msg}")
        sys.exit(1)

    # 仅查询父页面ID接口
    if args.get_parent_page_id:
        result = get_parentPageId(
            space_id=space_id,
            page_id=page_id,
            user_id=user_id,
            token=token,
        )
        if args.json_output:
            print(json.dumps(result, ensure_ascii=False))
        else:
            if result.get("status") == "success":
                print(f"✅ parentPageId: {result.get('parent_page_id', '')}")
            else:
                print(f"❌ {result.get('message', '获取父页面ID失败')}")
        return

    # 执行复制
    result = copy_page(
        space_id=space_id,
        page_id=page_id,
        target_space_id=target_space_id,
        target_node_id=target_node_id,
        user_id=user_id,
        token=token,
        level=args.level,
        title_replace_source=title_replace_source,
        title_replace_target=title_replace_target,
        json_output=args.json_output,
    )

    if args.json_output:
        # copy_page 在 json_output 模式下返回 JSON 字符串；stdout 仅输出 JSON，日志在 stderr
        if isinstance(result, str):
            print(result)
        else:
            print(json.dumps(result, ensure_ascii=False))
    elif isinstance(result, dict):
        print(format_result_as_text(result))


if __name__ == "__main__":
    configure_stdio_utf8()
    main()
