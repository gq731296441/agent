#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=================================================================
studio-space-manager - 管理员设置模块 (v2.8.0)
=================================================================
企业级 iCenter 空间管理技能 - 管理员查询模块

通过对话交互查询和展示 iCenter 空间的管理员信息。

跨平台支持：
- ✅ Linux (Ubuntu/CentOS/Debian)
- ✅ Windows 10/11
- ✅ macOS (通过 Python)

使用方式：
  # Linux / macOS
  python3 space_admin_setting.py <空间名称关键词>

  # Windows
  python space_admin_setting.py <空间名称关键词>

依赖：
- Python 3.6+
- requests

作者：Co-Claw Team
创建：2026-02-26
更新：2026-03-07
"""

import os
import sys
import json
import requests
import re
import platform
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Tuple

from system_param import (
    build_icenter_api_url,
    build_system_param,
    get_user_and_token,
)

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
        """_platform_utils 不可用时占位，避免入口报错。"""
        return


# API 配置
API_URL = build_icenter_api_url("/zte-km-icenter-space/spacemember/loadSpaceAdmins")
SPACE_SEARCH_API = build_icenter_api_url(
    "/zte-icenter-search-bff/zte-icenter-search-api/search"
)


def get_auth_headers():
    """获取认证请求头"""
    emp_no, token = get_user_and_token(strict=False)
    emp_no = (emp_no or "").strip()
    token = (token or "").strip()

    if not emp_no or not token:
        raise ValueError(
            "未找到认证信息，请确保环境变量 coclaw_empno 和 coclaw_token 已设置，如仍报错请重启 Co-Claw 服务后重试"
        )

    return {
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


def get_hash_code_by_string(s: str) -> int:
    """与前端 getHashCodeByString 一致：对字符串做 31 倍累加哈希，结果为 32 位有符号整数。"""
    hash_val = 0
    if not s:
        return hash_val
    for c in s:
        hash_val = (31 * hash_val + ord(c)) & 0xFFFFFFFF
    if hash_val >= 0x80000000:
        hash_val -= 0x100000000
    return hash_val


def clean_html_tags(text: str) -> str:
    """清理 HTML 标签（复用 studio-space-search 技能的实现）"""
    if not text:
        return ""
    text = text.replace('<em class="search-highlight">', "").replace("</em>", "")
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def search_spaces(keyword: str) -> list:
    """
    搜索 iCenter 空间（复用 studio-space-search 技能的搜索逻辑）

    Args:
        keyword: 搜索关键词

    Returns:
        list: 空间列表，每个空间包含 id, name, url
    """
    emp_no, token = get_user_and_token(strict=False)
    emp_no = (emp_no or "").strip()
    token = (token or "").strip()

    if not emp_no or not token:
        raise ValueError(
            "未找到认证信息，请确保环境变量 coclaw_empno 和 coclaw_token 已设置，如仍报错请重启 Co-Claw 服务后重试"
        )

    headers = {
        "X-Auth-Value": token,
        "X-Emp-No": emp_no,
        "X-Lang-Id": "zh_CN",
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
    }

    request_body = {
        "keyword": keyword,
        "app": "space",
        "searchFilters": {},
        "page": 1,
        "size": 10,
        "correctKeyword": True,
        "keywordCorrected": False,
        "domain": "universal",
        "isDeepThinking": True,
    }

    try:
        response = requests.post(
            SPACE_SEARCH_API, headers=headers, json=request_body, timeout=30
        )
        response.raise_for_status()

        result = response.json()

        # 检查返回状态
        code_info = result.get("code", {})
        if code_info.get("code") != "0000":
            error_msg = code_info.get("msg", "未知错误")
            raise Exception(f"搜索空间失败: {error_msg}")

        # 提取空间列表
        rows = result.get("bo", {}).get("rows", [])
        spaces = []

        for row in rows:
            title = clean_html_tags(row.get("title", ""))
            space_id = row.get("extraFields", {}).get("spaceId", "")

            spaces.append(
                {
                    "id": space_id,
                    "name": title,
                    "url": f"https://i.zte.com.cn/index/ispace/#/space/{space_id}",
                }
            )

        return spaces

    except requests.exceptions.RequestException as e:
        raise Exception(f"请求失败: {str(e)}")
    except json.JSONDecodeError as e:
        raise Exception(f"JSON 解析错误: {str(e)}")
    except Exception as e:
        raise Exception(f"搜索空间失败: {str(e)}")


def get_space_admins(space_id: str) -> list:
    """
    获取指定空间的管理员列表

    Args:
        space_id: 空间ID

    Returns:
        list: 管理员列表，每个管理员包含 id 和 name
    """
    emp_no, _token = get_user_and_token(strict=False)
    emp_no = (emp_no or "").strip()

    # 构建 GET 请求参数
    params = {
        "employeeShortId": emp_no,
        "spaceId": space_id,
        "all": "true",
        "environmentType": "browser",
        "system": build_system_param("space"),
    }

    headers = get_auth_headers()

    try:
        response = requests.get(API_URL, params=params, headers=headers, timeout=30)
        response.raise_for_status()

        result = response.json()

        # 检查返回状态
        code_info = result.get("code", {})
        if code_info.get("code") != "0000":
            raise Exception(f"API 返回错误: {code_info.get('msg', '未知错误')}")

        # 提取管理员列表
        admins = result.get("bo", [])

        # 过滤 isAdmin=1 的管理员
        admin_list = [admin for admin in admins if admin.get("isAdmin") == 1]

        # 格式化管理员信息（只保留 id 和 name）
        formatted_admins = []
        for admin in admin_list:
            formatted_admins.append(
                {"id": admin.get("id", ""), "name": admin.get("name", "")}
            )

        return formatted_admins

    except requests.exceptions.RequestException as e:
        raise Exception(f"请求失败: {str(e)}")
    except json.JSONDecodeError as e:
        raise Exception(f"JSON 解析错误: {str(e)}")
    except Exception as e:
        raise Exception(f"获取管理员列表失败: {str(e)}")


def format_space_table(spaces: list) -> str:
    """
    格式化空间列表为表格形式

    Args:
        spaces: 空间列表

    Returns:
        str: 格式化的表格字符串
    """
    if not spaces:
        return "未找到相关空间，可能是空间不存在或当前账号无访问权限，请确认空间名称或权限后重试。"

    headers = ["编号", "空间名称", "空间地址"]
    rows = []

    for i, space in enumerate(spaces, 1):
        rows.append([str(i), space.get("name", ""), space.get("url", "")])

    # 计算每列最大宽度
    col_widths = []
    for col_idx in range(len(headers)):
        max_width = len(headers[col_idx])
        for row in rows:
            cell_length = len(str(row[col_idx]))
            if cell_length > max_width:
                max_width = cell_length
        col_widths.append(max_width + 2)

    # 构建表格
    separator_start = "┌"
    for width in col_widths:
        separator_start += "─" * width + "┬"
    separator_start = separator_start[:-1] + "┐"

    header_line = "│"
    for i, header in enumerate(headers):
        header_line += f" {header.ljust(col_widths[i] - 1)} │"

    separator = "├"
    for width in col_widths:
        separator += "─" * width + "┤"

    separator_end = "└"
    for width in col_widths:
        separator_end += "─" * width + "┴"
    separator_end = separator_end[:-1] + "┘"

    data_lines = []
    for row in rows:
        line = "│"
        for i, cell in enumerate(row):
            line += f" {str(cell).ljust(col_widths[i] - 1)} │"
        data_lines.append(line)

    table = f"{separator_start}\n{header_line}\n{separator}\n"
    for line in data_lines:
        table += f"{line}\n"
    table += separator_end

    return table


def format_admin_table(admins: list) -> str:
    """
    格式化管理员列表为表格形式（仅显示三列：编号、管理员工号、管理员姓名）

    Args:
        admins: 管理员列表

    Returns:
        str: 格式化的表格字符串
    """
    if not admins:
        return "该空间暂无管理员"

    headers = ["编号", "管理员工号", "管理员姓名"]
    rows = []

    for i, admin in enumerate(admins, 1):
        rows.append([str(i), admin.get("id", ""), admin.get("name", "")])

    # 计算每列最大宽度
    col_widths = []
    for col_idx in range(len(headers)):
        max_width = len(headers[col_idx])
        for row in rows:
            cell_length = len(str(row[col_idx]))
            if cell_length > max_width:
                max_width = cell_length
        col_widths.append(max_width + 2)

    # 构建表格
    separator_start = "┌"
    for width in col_widths:
        separator_start += "─" * width + "┬"
    separator_start = separator_start[:-1] + "┐"

    header_line = "│"
    for i, header in enumerate(headers):
        header_line += f" {header.ljust(col_widths[i] - 1)} │"

    separator = "├"
    for width in col_widths:
        separator += "─" * width + "┤"

    separator_end = "└"
    for width in col_widths:
        separator_end += "─" * width + "┴"
    separator_end = separator_end[:-1] + "┘"

    data_lines = []
    for row in rows:
        line = "│"
        for i, cell in enumerate(row):
            line += f" {str(cell).ljust(col_widths[i] - 1)} │"
        data_lines.append(line)

    table = f"{separator_start}\n{header_line}\n{separator}\n"
    for line in data_lines:
        table += f"{line}\n"
    table += separator_end

    return table


def print_step(message: str, emoji: str = "🔧"):
    """打印步骤信息"""
    print(f"\n{emoji} {message}")


def main():
    """主函数"""
    configure_stdio_utf8()
    if len(sys.argv) < 2:
        print("=" * 60)
        print("空间管理员设置技能")
        print("=" * 60)
        print("\n用法: python space_admin_setting.py <空间搜索关键词>")
        print("\n示例:")
        print("  python space_admin_setting.py 测试空间")
        print("  python space_admin_setting.py 项目管理")
        print("\n功能:")
        print("  1. 搜索 iCenter 空间")
        print("  2. 以表格形式展示搜索结果")
        print("  3. 用户选择目标空间")
        print("  4. 获取并展示该空间的管理员列表")
        sys.exit(1)

    if sys.argv[1] in ("--help", "-h"):
        print("=" * 60)
        print("空间管理员设置技能")
        print("=" * 60)
        print("\n用法: python space_admin_setting.py <空间搜索关键词>")
        print("\n示例:")
        print("  python space_admin_setting.py 测试空间")
        print("  python space_admin_setting.py 项目管理")
        print("\n功能:")
        print("  1. 搜索 iCenter 空间")
        print("  2. 以表格形式展示搜索结果")
        print("  3. 用户选择目标空间")
        print("  4. 获取并展示该空间的管理员列表")
        sys.exit(0)

    keyword = sys.argv[1]

    try:
        # Step 1: 搜索空间
        print_step(f"正在搜索空间: 「{keyword}」")
        spaces = search_spaces(keyword)

        if not spaces:
            print(f"\n❌ 未找到包含「{keyword}」的空间，可能是空间不存在或当前账号无访问权限，请确认空间名称或权限后重试。")
            sys.exit(0)

        # Step 2: 展示空间列表
        print(f"\n找到 {len(spaces)} 个匹配的空间：")
        print(format_space_table(spaces))

        # Step 3: 获取用户选择
        print(f"\n📋 请输入编号选择空间（1-{len(spaces)}）：")
        while True:
            try:
                choice = input().strip()
                if not choice:
                    continue

                idx = int(choice)
                if 1 <= idx <= len(spaces):
                    selected_space = spaces[idx - 1]
                    break
                else:
                    print(f"⚠️ 请输入 1-{len(spaces)} 之间的数字：")
            except ValueError:
                print("⚠️ 请输入有效的数字：")

        # Step 4: 获取并展示管理员列表
        print_step(f"正在获取「{selected_space['name']}」的管理员信息...")
        admins = get_space_admins(selected_space["id"])

        if not admins:
            print(f"\n⚠️ 空间「{selected_space['name']}」暂无管理员")
            sys.exit(0)

        print(f"\n✅ 空间「{selected_space['name']}」的管理员列表（共 {len(admins)} 位）：")
        print(format_admin_table(admins))

        # 输出 JSON 格式结果（供其他程序解析）
        print(f"\n📊 JSON 格式结果：")
        print(
            json.dumps(
                {
                    "spaceId": selected_space["id"],
                    "spaceName": selected_space["name"],
                    "spaceUrl": selected_space["url"],
                    "adminCount": len(admins),
                    "admins": admins,
                },
                ensure_ascii=False,
                indent=2,
            )
        )

        sys.exit(0)

    except ValueError as e:
        print(f"\n❌ 配置错误: {str(e)}")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"\n❌ 网络请求错误: {str(e)}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"\n❌ JSON 解析错误: {str(e)}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 错误: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
