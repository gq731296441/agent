#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
查询空间页面的子页面列表
支持从URL解析或直接传入space-id和content-id

用法：
    python3 get_page_children.py --url "https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view"
    python3 get_page_children.py --space-id <spaceId> --content-id <pageId>
    python3 get_page_children.py --space-id <spaceId> --content-id <pageId> --json-output

作者: Co-Claw Team
日期: 2026-03-09
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
# 确保既能导入根目录模块，也能导入当前 scripts 下的公共模块（如 system_param）
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, BASE_DIR)

from system_param import build_icenter_api_url, build_system_param, get_user_and_token
from _platform_utils import configure_stdio_utf8, get_platform_and_user_agent


def _json_mode_log(message: str) -> None:
    """--json-output 时向 stderr 打一行日志，避免污染 stdout 的纯 JSON。"""
    print(f"[get_page_children] {message}", file=sys.stderr)


token = ""
emp_no = ""

# 统一从 system_param 读取环境变量（保持原脚本结构：模块级变量 + 函数内校验）
_emp_no, _token = get_user_and_token(strict=False)
emp_no = (_emp_no or "").strip()
token = (_token or "").strip()


def parse_url(url):
    """从URL中解析spaceId和contentId"""
    # 匹配 /shared/{spaceId}/wiki/page/{pageId}/view 格式
    pattern = r"/shared/([a-f0-9]+)/wiki/page/([a-f0-9]+)/view"
    match = re.search(pattern, url)
    if match:
        return match.group(1), match.group(2)
    return None, None


def get_page_children(space_id, content_id, json_output=False):
    """
    获取页面的子页面列表

    Args:
        space_id: 空间ID
        content_id: 页面ID（父页面ID）
        json_output: 是否JSON格式输出（用于程序调用）

    Returns:
        子页面列表或JSON字符串
    """
    # 获取认证信息和平台信息

    if not emp_no or not token:
        error_msg = "无法获取用户账号和Token，请确保已设置 coclaw_empno 和 coclaw_token 环境变量，如仍报错请重启 Co-Claw 服务后重试"
        if json_output:
            _json_mode_log(f"错误: {error_msg}")
            return json.dumps({"error": error_msg}, ensure_ascii=False)
        print(f"❌ {error_msg}")
        return None

    # 获取平台信息和User-Agent
    platform_type, default_user_agent = get_platform_and_user_agent()

    # 构建请求URL
    timestamp = int(time.time() * 1000)
    system_param = build_system_param("space")
    url = (
        build_icenter_api_url("/zte-rd-icenter-contents/content/subtree/")
        f"?contentId={content_id}"
        f"&system={system_param}"
        f"&v={timestamp}"
        f"&environmentType=browser"
        f"&spaceId={space_id}"
        f"&hasDepth=false"
        f"&_={timestamp-230}"
    )

    # 构建请求头
    headers = {
        "Accept": "application/json",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://i.zte.com.cn",
        "Referer": "https://i.zte.com.cn/",
        "User-Agent": default_user_agent,
        "X-Auth-Value": token,
        "X-Cookie-From-Cros": "Y",
        "X-Emp-No": emp_no,
        "X-Lang-Id": "zh_CN",
        "X-Tenant-Id": "ZTE",
    }

    # 添加apm_biz_key头
    headers["apm_biz_key"] = json.dumps(
        {
            "host_ip": "i.zte.com.cn",
            "serviceName": "ICENTER_zte-km-icenter-ui",
            "X-Emp-No": emp_no,
        }
    )

    if json_output:
        _json_mode_log(
            f"调用 subtree 接口 space_id={space_id} parent_content_id={content_id}"
        )

    try:
        import requests

        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        data = response.json()

        # 检查返回状态
        if data.get("code", {}).get("code") != "0000":
            error_msg = f"查询失败: {data.get('code', {}).get('msgId', '未知错误')}"
            if json_output:
                _json_mode_log(f"错误: {error_msg}")
                return json.dumps({"error": error_msg}, ensure_ascii=False)
            print(f"❌ {error_msg}")
            return None

        # 提取子页面列表
        children = data.get("bo", [])

        if json_output:
            _json_mode_log(
                f"成功: 直接子页面数={len(children)} space_id={space_id} parent_content_id={content_id}"
            )
            return json.dumps(
                {
                    "space_id": space_id,
                    "parent_content_id": content_id,
                    "children_count": len(children),
                    "children": children,
                },
                ensure_ascii=False,
            )

        # 格式化输出
        if not children:
            print(f"\n📋 该页面暂无子页面")
            return children

        print(f"\n📋 子页面列表")
        print(f"空间ID: {space_id}")
        print(f"父页面ID: {content_id}")
        print(f"子页面数量: {len(children)}")
        print("-" * 80)

        for idx, child in enumerate(children, 1):
            title = child.get("title", "未命名")
            page_id = child.get("id", "")
            content_type = child.get("contentType", "")
            create_date = child.get("createDate", "")
            update_date = child.get("updateDate", "")
            create_no = child.get("createNo", "")
            is_leaf = child.get("isLeaf", True)

            # 构建页面链接
            page_url = (
                f"https://i.zte.com.cn/#/shared/{space_id}/wiki/page/{page_id}/view"
            )

            print(f"{idx}. {title}")
            print(f"   页面ID: {page_id}")
            print(f"   类型: {content_type}")
            print(f"   链接: {page_url}")
            print(f"   创建时间: {create_date}")
            print(f"   更新时间: {update_date}")
            print(f"   创建人: {create_no}")
            print(f"   是叶子节点: {'是' if is_leaf else '否'}")
            print("-" * 80)

        return children

    except requests.exceptions.Timeout:
        error_msg = "查询失败: 请求超时"
        if json_output:
            _json_mode_log(f"错误: {error_msg}")
            return json.dumps({"error": error_msg}, ensure_ascii=False)
        print(f"❌ {error_msg}")
        return None
    except requests.exceptions.RequestException as e:
        error_msg = f"查询失败: 网络请求错误 - {str(e)}"
        if json_output:
            _json_mode_log(f"错误: {error_msg}")
            return json.dumps({"error": error_msg}, ensure_ascii=False)
        print(f"❌ {error_msg}")
        return None
    except json.JSONDecodeError:
        error_msg = "查询失败: 返回数据解析错误"
        if json_output:
            _json_mode_log(f"错误: {error_msg}")
            return json.dumps({"error": error_msg}, ensure_ascii=False)
        print(f"❌ {error_msg}")
        return None


def get_all_descendants(
    space_id, content_id, json_output=False, depth=0, max_depth=None
):
    """
    递归获取页面的所有子孙页面（构建完整的页面树）

    Args:
        space_id: 空间ID
        content_id: 页面ID（根页面ID）
        json_output: 是否JSON格式输出
        depth: 当前递归深度
        max_depth: 最大递归深度（None表示无限制）

    Returns:
        页面树结构列表或JSON字符串
    """
    # 检查最大深度限制
    if max_depth is not None and depth >= max_depth:
        return []

    # 构建请求头
    platform_type, default_user_agent = get_platform_and_user_agent()
    timestamp = int(time.time() * 1000)

    headers = {
        "Accept": "application/json",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://i.zte.com.cn",
        "Referer": "https://i.zte.com.cn/",
        "User-Agent": default_user_agent,
        "X-Auth-Value": token,
        "X-Cookie-From-Cros": "Y",
        "X-Emp-No": emp_no,
        "X-Lang-Id": "zh_CN",
        "X-Tenant-Id": "ZTE",
    }

    headers["apm_biz_key"] = json.dumps(
        {
            "host_ip": "i.zte.com.cn",
            "serviceName": "ICENTER_zte-km-icenter-ui",
            "X-Emp-No": emp_no,
        }
    )

    # 构建请求URL
    system_param = build_system_param("space")
    url = (
        build_icenter_api_url("/zte-rd-icenter-contents/content/subtree/")
        f"?contentId={content_id}"
        f"&system={system_param}"
        f"&v={timestamp}"
        f"&environmentType=browser"
        f"&spaceId={space_id}"
        f"&hasDepth=false"
        f"&_={timestamp-230}"
    )

    if json_output and depth == 0:
        _json_mode_log(
            f"递归查询页面树 space_id={space_id} root_content_id={content_id} "
            f"max_depth={max_depth}"
        )

    try:
        import requests

        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        data = response.json()

        # 检查返回状态
        if data.get("code", {}).get("code") != "0000":
            err = data.get("code", {}).get("msgId", "未知错误")
            if json_output:
                _json_mode_log(
                    f"错误: 子树接口返回非成功 content_id={content_id} depth={depth} msg={err}"
                )
            return []

        # 提取子页面列表
        children = data.get("bo", [])

        # 为每个子页面递归获取其子孙
        result_tree = []
        for child in children:
            page_id = child.get("id")
            is_leaf = child.get("isLeaf", True)

            # 构建节点信息
            node = {
                "id": page_id,
                "title": child.get("title", "未命名"),
                "contentType": child.get("contentType", ""),
                "createDate": child.get("createDate", ""),
                "updateDate": child.get("updateDate", ""),
                "createNo": child.get("createNo", ""),
                "isLeaf": is_leaf,
                "url": f"https://i.zte.com.cn/#/shared/{space_id}/wiki/page/{page_id}/view",
                "depth": depth + 1,
                "children": [],
            }

            # 如果不是叶子节点，递归获取子页面
            if not is_leaf and max_depth is not None and depth + 1 < max_depth:
                node["children"] = get_all_descendants(
                    space_id, page_id, json_output, depth + 1, max_depth
                )
            elif not is_leaf and max_depth is None:
                node["children"] = get_all_descendants(
                    space_id, page_id, json_output, depth + 1, max_depth
                )

            result_tree.append(node)

        return result_tree

    except requests.exceptions.Timeout:
        if json_output:
            _json_mode_log(f"错误: 子树请求超时 content_id={content_id} depth={depth}")
        return []
    except requests.exceptions.RequestException as e:
        if json_output:
            _json_mode_log(f"错误: 子树网络异常 content_id={content_id} depth={depth} {e}")
        return []
    except json.JSONDecodeError:
        if json_output:
            _json_mode_log(f"错误: 子树响应 JSON 解析失败 content_id={content_id} depth={depth}")
        return []


def print_tree_structure(tree, indent=0, prefix=""):
    """打印树形结构"""
    for idx, node in enumerate(tree):
        is_last = idx == len(tree) - 1

        # 构建连接符
        connector = "└── " if is_last else "├── "
        leaf_marker = " 🍃" if node.get("isLeaf") else ""

        # 打印当前节点
        print(f"{prefix}{connector}{node['title']}{leaf_marker}")
        print(f"{prefix}{'    ' if is_last else '│   '}ID: {node['id']}")

        # 递归打印子节点
        if node.get("children"):
            new_prefix = prefix + ("    " if is_last else "│   ")
            print_tree_structure(node["children"], indent + 1, new_prefix)


def main():
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(description="查询空间页面的子页面列表")
    parser.add_argument(
        "--url",
        type=str,
        help="页面URL，格式: https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view",
    )
    parser.add_argument("--space-id", type=str, help="空间ID")
    parser.add_argument("--content-id", type=str, help="页面ID（父页面ID）")
    parser.add_argument("--json-output", action="store_true", help="JSON格式输出（用于程序调用）")
    parser.add_argument(
        "--recursive",
        "-r",
        action="store_true",
        default=True,
        help="递归获取所有子孙页面（构建完整页面树）",
    )
    parser.add_argument("--max-depth", type=int, default=None, help="最大递归深度（默认无限制）")

    args = parser.parse_args()

    if not emp_no or not token:
        error_msg = (
            "无法获取用户账号和Token，请确保已设置 coclaw_empno 和 coclaw_token 环境变量，"
            "如仍报错请重启 Co-Claw 服务后重试"
        )
        if args.json_output:
            _json_mode_log(f"错误: {error_msg}")
            print(json.dumps({"error": error_msg}, ensure_ascii=False))
        else:
            print(f"❌ {error_msg}")
        sys.exit(1)

    # 解析URL或使用直接传入的参数
    if args.url:
        space_id, content_id = parse_url(args.url)
        if not space_id or not content_id:
            error_msg = "无法从URL解析spaceId和contentId"
            if args.json_output:
                _json_mode_log(f"错误: {error_msg}")
                print(json.dumps({"error": error_msg}, ensure_ascii=False))
            else:
                print(f"❌ {error_msg}")
            sys.exit(1)
    elif args.space_id and args.content_id:
        space_id = args.space_id
        content_id = args.content_id
    else:
        if args.json_output:
            _json_mode_log("错误: 请提供 --url 或 --space-id + --content-id 参数")
            print(
                json.dumps(
                    {"error": "请提供 --url 或 --space-id + --content-id 参数"},
                    ensure_ascii=False,
                )
            )
        else:
            print("❌ 请提供 --url 或 --space-id + --content-id 参数")
        sys.exit(1)

    # 递归模式：获取所有子孙页面
    if args.recursive:
        result_tree = get_all_descendants(
            space_id, content_id, args.json_output, max_depth=args.max_depth
        )

        if args.json_output:
            _json_mode_log(
                f"成功: 页面树已构建 顶层子节点数={len(result_tree)} " f"root_content_id={content_id}"
            )
            print(
                json.dumps(
                    {
                        "space_id": space_id,
                        "root_content_id": content_id,
                        "max_depth": args.max_depth,
                        "tree": result_tree,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(f"\n🌳 完整页面树结构")
            print(f"空间ID: {space_id}")
            print(f"根页面ID: {content_id}")
            print(f"最大深度: {args.max_depth if args.max_depth else '无限制'}")
            print("-" * 80)

            # 获取根页面信息作为树顶
            root_url = build_icenter_api_url(
                f"/zte-rd-icenter-contents/content/{content_id}?spaceId={space_id}&environmentType=browser"
            )
            platform_type, default_user_agent = get_platform_and_user_agent()
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json;charset=UTF-8",
                "User-Agent": default_user_agent,
                "X-Auth-Value": token,
                "X-Emp-No": emp_no,
                "X-Tenant-Id": "ZTE",
            }

            try:
                import requests

                resp = requests.get(root_url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    root_data = resp.json()
                    if root_data.get("code", {}).get("code") == "0000":
                        root_info = root_data.get("bo", {})
                        print(f"📄 {root_info.get('title', '未命名')} (根页面)")
                        print()
            except:
                print(f"📄 根页面 (ID: {content_id})")
                print()

            # 打印树形结构
            print_tree_structure(result_tree)
            print("-" * 80)

            # 统计信息
            def count_nodes(tree):
                count = 0
                for node in tree:
                    count += 1
                    if node.get("children"):
                        count += count_nodes(node["children"])
                return count

            def count_leaves(tree):
                count = 0
                for node in tree:
                    if node.get("isLeaf"):
                        count += 1
                    if node.get("children"):
                        count += count_leaves(node["children"])
                return count

            total_nodes = count_nodes(result_tree)
            total_leaves = count_leaves(result_tree)

            print(f"\n📊 统计信息:")
            print(f"  总节点数: {total_nodes}")
            print(f"  叶子节点数: {total_leaves}")
            print(f"  非叶子节点数: {total_nodes - total_leaves}")
    else:
        # 普通模式：只获取直接子页面
        result = get_page_children(space_id, content_id, args.json_output)

        if args.json_output:
            print(result)


if __name__ == "__main__":
    main()
