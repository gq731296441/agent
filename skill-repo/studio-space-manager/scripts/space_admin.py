#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=================================================================
studio-space-manager - 空间管理员模块 (v2.8.0)
=================================================================
企业级 iCenter 空间管理技能

通过 iCenter API 获取指定空间的所有管理员信息。

跨平台支持：
- ✅ Linux (Ubuntu/CentOS/Debian)
- ✅ Windows 10/11
- ✅ macOS (通过 Python)

使用方式：
  # Linux / macOS
  python3 space_admin.py <space_id>

  # Windows
  python space_admin.py <space_id>

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
import platform
import requests
from datetime import datetime
from typing import Tuple

from system_param import build_icenter_api_url, build_system_param, get_user_and_token

# 导入跨平台工具模块（如果存在）
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
    # 降级兼容：不使用跨平台功能
    SYSTEM, SYSTEM_RELEASE, IS_WINDOWS = (
        platform.system(),
        platform.release(),
        platform.system() == "Windows",
    )

    def configure_stdio_utf8() -> None:
        return


# API 配置
API_URL = build_icenter_api_url("/zte-km-icenter-space/spacemember/loadSpaceAdmins")


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


def get_space_admins(space_id):
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


def format_admin_table(admins):
    """
    格式化管理员列表为表格形式（仅显示三列：编号、管理员工号、管理员姓名）

    Args:
        admins: 管理员列表

    Returns:
        str: 格式化的表格字符串
    """
    if not admins:
        return "该空间暂无管理员"

    # 简化表格：只显示三列
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
        col_widths.append(max_width + 2)  # 两侧留空格

    # 构建表格
    # 表头
    header_line = "│"
    for i, header in enumerate(headers):
        header_line += f" {header.ljust(col_widths[i] - 1)} │"

    # 分隔线
    separator = "├"
    for width in col_widths:
        separator += "─" * width + "┤"

    separator_start = "┌"
    for width in col_widths:
        separator_start += "─" * width + "┬"
    separator_start = separator_start[:-1] + "┐"

    separator_end = "└"
    for width in col_widths:
        separator_end += "─" * width + "┴"
    separator_end = separator_end[:-1] + "┘"

    # 数据行
    data_lines = []
    for row in rows:
        line = "│"
        for i, cell in enumerate(row):
            line += f" {str(cell).ljust(col_widths[i] - 1)} │"
        data_lines.append(line)

    # 组装完整表格
    table = f"{separator_start}\n{header_line}\n{separator}\n"
    for line in data_lines:
        table += f"{line}\n"
    table += separator_end

    return table


def main():
    """主函数"""
    configure_stdio_utf8()
    if len(sys.argv) < 2:
        print("用法: python space_admin.py <space_id>")
        print("示例: python space_admin.py 1920485432")
        sys.exit(1)

    space_id = sys.argv[1]

    try:
        admins = get_space_admins(space_id)

        if not admins:
            print(f"空间 {space_id} 暂无管理员")
            sys.exit(0)

        # 输出详细JSON（供其他程序解析）
        print(f"找到 {len(admins)} 位管理员")
        print("\n详细信息（JSON格式）:")
        print(json.dumps(admins, ensure_ascii=False, indent=2))

        # 输出表格
        print("\n管理员列表:")
        print(format_admin_table(admins))

        sys.exit(0)

    except Exception as e:
        print(f"错误: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
