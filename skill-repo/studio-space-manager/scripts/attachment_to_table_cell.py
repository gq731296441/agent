#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
附件上传并挂载到表格单元格（集成版 - 原生上传调用）

工作流程：
1. 调用 studio_space_attachment.py 上传文件（不自动挂载到页面）
2. 获取上传成功的 attachment_html 列表
3. 读取页面 HTML，定位 block 表格
4. 在指定行列插入 attachment_html
5. 回写页面

参数示例：
python3 attachment_to_table_cell.py \
  --page-url "https://i.zte.com.cn/.../view" \
  --block-id "BQU4T2MY" \
  --upload-file "/path/a.pdf" --upload-file "/path/b.pdf" \
  --row-index 1 --col-index 0

环境配置：
- 通过环境变量 coclaw_env 控制环境（prod/test），默认为 prod
- 产线：https://icenterapi.zte.com.cn, https://i.zte.com.cn
- 测试：https://icenter-systest.test.zte.com.cn, https://icenter.uat.zte.com.cn
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from bs4 import BeautifulSoup

# 将脚本目录加入路径以便导入其他模块
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# 复用 system_param.py 中的环境配置和工具函数
from system_param import (
    ICENTER_ENV_PROD,
    ICENTER_ENV_TEST,
    ICENTER_ENV,
    get_icenter_frontend_url,
    build_icenter_page_url,
    parse_icenter_page_url,
)

# 复用 safe_page_edit.py 中的成熟实现
from safe_page_edit import (
    get_user_and_token,
    build_headers,
    fetch_page_html as safe_fetch_page_html,
)

def upload_via_cli(space_id: str, page_id: str, file_paths: list) -> list:
    """调用 studio_space_attachment.py 上传，返回 attachment_html 列表"""
    attachment_script = os.path.join(SCRIPT_DIR, "studio_space_attachment.py")
    cmd = ["python3", attachment_script, "--space-id", space_id, "--page-id", page_id, "--json"]
    for fp in file_paths:
        cmd.extend(["--upload-file", str(Path(fp).expanduser().resolve())])
    # 添加 --no-attach-to-page 避免自动挂载到底部
    cmd.append("--no-attach-to-page")

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        print(f"❌ 上传失败: {result.stderr}")
        sys.exit(1)

    try:
        data = json.loads(result.stdout)
    except Exception as e:
        print(f"❌ JSON 解析失败: {e}\n输出: {result.stdout}")
        sys.exit(1)

    if data.get("status") != "success":
        print(f"❌ 上传未成功: {data}")
        sys.exit(1)

    attachment_htmls = []
    for ur in data.get("upload_results", []):
        if ur.get("status") == "success":
            html = ur.get("attachment_html")
            if not html:
                print(f"❌ 缺少 attachment_html: {ur.get('file_name')}")
                sys.exit(1)
            attachment_htmls.append(html)
            print(f"✅ 上传成功: {ur.get('file_name')}")
        else:
            print(f"❌ 上传失败: {ur.get('message')}")
            sys.exit(1)

    return attachment_htmls


def fetch_page_html(space_id: str, page_id: str) -> str:
    """获取页面原始 HTML 内容（完全复用 safe_page_edit.py 的实现）。

    Args:
        space_id: 空间 ID
        page_id: 页面 ID

    Returns:
        页面 HTML 内容字符串

    Raises:
        RuntimeError: 当获取页面失败时抛出异常
    """
    user_id, token = get_user_and_token()

    # 直接调用 safe_page_edit.py 中的成熟实现
    result = safe_fetch_page_html(space_id, page_id, user_id, token)

    if result.get("status") != "success":
        raise RuntimeError(result.get("message", "获取页面失败"))

    html_content = result.get("html_content", "")
    if not html_content:
        raise RuntimeError("页面内容为空")

    return html_content


def modify_table_cells(html: str, block_id: str, attachments: list, start_row: int, start_col: int) -> str:
    """在指定 block 表格中插入附件链接"""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", {"block-id": block_id})
    if not table:
        raise ValueError(f"未找到 block-id 为 {block_id} 的表格")

    rows = table.find_all("tr")
    if start_row >= len(rows):
        raise ValueError(f"行索引 {start_row} 超出总行数 {len(rows)}")

    target_row = rows[start_row]
    cells = target_row.find_all(["td", "th"])

    for i, attach_html in enumerate(attachments):
        col_idx = start_col + i
        if col_idx >= len(cells):
            print(f"⚠️  列索引 {col_idx} 超出当前行单元格数 {len(cells)}，停止填充")
            break
        cell = cells[col_idx]
        cell.clear()
        frag = BeautifulSoup(attach_html, "html.parser")
        for node in frag.contents:
            cell.append(node)
        print(f"  更新单元格: 行 {start_row}, 列 {col_idx}")

    return str(soup)


def rewrite_page(page_url: str, html_content: str):
    """调用 studio_space_page_edit.py 回写页面"""
    edit_script = os.path.join(SCRIPT_DIR, "studio_space_page_edit.py")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, encoding="utf-8") as tmp:
        tmp.write(html_content)
        tmp_path = tmp.name

    cmd = ["python3", edit_script, "--page-url", page_url, "--html-file", tmp_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    try:
        os.unlink(tmp_path)
    except:
        pass

    if result.returncode != 0:
        print(f"❌ 回写失败: {result.stderr}")
        sys.exit(1)

    print(result.stdout)
    if "成功" in result.stdout or "✅" in result.stdout:
        print("✅ 页面更新完成")
    else:
        print("⚠️  页面可能已更新，请检查输出")


def main():
    parser = argparse.ArgumentParser(description="附件上传并挂载到表格单元格（调用原生技能）")
    parser.add_argument("--space-id", help="空间 ID")
    parser.add_argument("--page-id", help="页面 ID")
    parser.add_argument("--page-url", help="页面完整 URL（与 --space-id/--page-id 二选一）")
    parser.add_argument("--block-id", required=True, help="表格 block ID")
    parser.add_argument("--upload-file", action="append", required=True, help="要上传的本地文件（可重复）")
    parser.add_argument("--row-index", type=int, required=True, help="目标行索引（从0开始）")
    parser.add_argument("--col-index", type=int, required=True, help="起始列索引（从0开始）")
    parser.add_argument("--env", choices=["prod", "test"], help="指定环境（覆盖环境变量 coclaw_env）")
    args = parser.parse_args()

    # 确定使用的环境
    target_env = args.env if args.env else ICENTER_ENV

    # 定位页面
    if not (args.page_url or (args.space_id and args.page_id)):
        print("❌ 必须提供 --page-url 或同时提供 --space-id 和 --page-id")
        sys.exit(1)

    space_id = args.space_id
    page_id = args.page_id
    if args.page_url:
        space_id, page_id = parse_icenter_page_url(args.page_url)
        if not space_id or not page_id:
            print("❌ 无法从 URL 解析 space_id/page_id")
            frontend_url = get_icenter_frontend_url(target_env)
            print(f"URL 格式示例：{frontend_url}/index/ispace/#/space/{{space_id}}/wiki/page/{{page_id}}/view")
            sys.exit(1)

    print(f"🌍 当前环境：{'产线' if target_env == 'prod' else '测试'} ({target_env})")
    print(f"📍 目标页面: space_id={space_id}, page_id={page_id}")
    print(f"📍 表格 block_id={args.block_id}, 位置=行{args.row_index}, 列{args.col_index}")

    # 1. 上传附件（调用原生技能）
    print(f"\n📤 上传附件（调用 studio_space_attachment.py）...")
    attachment_htmls = upload_via_cli(space_id, page_id, args.upload_file)

    # 2. 获取页面 HTML（复用 safe_page_edit.py 的 API 调用方式）
    print("\n📄 获取页面 HTML...")
    page_html = fetch_page_html(space_id, page_id, target_env)

    # 3. 修改表格
    print("\n✏️  修改表格单元格...")
    modified_html = modify_table_cells(page_html, args.block_id, attachment_htmls, args.row_index, args.col_index)

    # 4. 回写页面
    print("\n📝 回写页面...")
    page_url = args.page_url or build_page_url(space_id, page_id, target_env)
    rewrite_page(page_url, modified_html)


if __name__ == "__main__":
    main()
