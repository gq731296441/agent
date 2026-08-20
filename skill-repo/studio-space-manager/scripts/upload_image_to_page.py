#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=================================================================
studio-space-manager - 图片上传并插入页面模块 (v2.0.0)
=================================================================
iCenter 空间管理技能 - 将本地图片上传到空间并插入到页面正文

功能：
- 使用 uploadImage 接口上传图片到 iCenter 文件服务
- 自动拼接图片可访问 URL
- 支持将图片插入到页面指定位置：页面末尾、指定章节、表格单元格
- 智能 HTML 插入：图片可追加到章节末尾或表格单元格内

依赖：
- Python 3.6+
- requests
- bs4 (用于表格单元格编辑)

作者：Co-Claw Team
创建：2026-06-29
版本：2.0.0（支持指定位置插入）
"""

import os
import re
import sys
import json
import argparse
import time
import platform
import mimetypes
from datetime import datetime
from typing import Optional, Dict, Any, Tuple

import requests

try:
    from PIL import Image
    _PIL_AVAILABLE = True
    # 禁用 DecompressionBomb 检查，以支持大尺寸图片上传
    Image.MAX_IMAGE_PIXELS = None
except ImportError:
    _PIL_AVAILABLE = False

from bs4 import BeautifulSoup

from system_param import (
    build_icenter_api_url,
    build_system_param,
    get_user_and_token as _get_user_and_token,
)
from _platform_utils import configure_stdio_utf8, get_platform_and_user_agent

# 尝试导入 studio_space_page_edit 的安全更新接口（走标准页面编辑流程）
try:
    from studio_space_page_edit import (
        update_chapter_content_via_mcp_api,
        update_table_cell_html,
        update_block_content_via_mcp_api,
        query_block_content,
    )
    _EDIT_AVAILABLE = True
except ImportError as e:
    _EDIT_AVAILABLE = False
    update_chapter_content_via_mcp_api = None
    update_table_cell_html = None
    update_block_content_via_mcp_api = None
    query_block_content = None

# 导入 safe_page_edit 的安全编辑功能（方案B：直接集成，避免子进程）
try:
    # safe_page_edit 位于同一目录
    from safe_page_edit import fetch_page_html, update_page_html
    _SAFE_EDIT_AVAILABLE = True
except ImportError:
    _SAFE_EDIT_AVAILABLE = False
    fetch_page_html = None
    update_page_html = None

# 确保跨平台路径处理
SYSTEM = platform.system()

def get_user_and_token() -> Tuple[str, str]:
    """返回 (user_id, token)，优先从环境变量获取（非 strict）。"""
    return _get_user_and_token(strict=False)

def get_auth_headers() -> Dict[str, str]:
    """获取基础认证请求头（复用现有附件上传逻辑）"""
    emp_no, token = get_user_and_token()
    if not emp_no or not token:
        raise ValueError(
            "未找到认证信息，请确保环境变量 coclaw_empno 和 coclaw_token 已设置"
        )

    if SYSTEM == "Windows":
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/138.0.0.0 Safari/537.36"
        )
        platform_str = '"Windows"'
    elif SYSTEM == "Darwin":
        ua = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/138.0.0.0 Safari/537.36"
        )
        platform_str = '"macOS"'
    else:
        ua = (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/138.0.0.0 Safari/537.36"
        )
        platform_str = '"Linux"'

    return {
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-US;q=0.7",
        "Connection": "keep-alive",
        "Content-Type": "multipart/form-data; boundary=----WebKitFormBoundaryOcxmaZFXB2vJsKjY",
        "Origin": "https://i.zte.com.cn",
        "Referer": "https://i.zte.com.cn/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "User-Agent": ua,
        "X-Auth-Value": token,
        "X-Emp-No": emp_no,
        "X-Lang-Id": "zh_CN",
        "X_Requested_With": "XMLHttpRequest",
        "sec-ch-ua": '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": platform_str,
        "sw8": "1-MGMyMjAwZGMtNDMzYy00ZjhkLTg4MDktODcxNDBlMzkyNGI5-YTE1Mjc4YTktNGY3NS00ZDhjLThlMDgtMmNmMGIzY2I0MmUy-0-enRlLWttLWljZW50ZXItd2ViY29udGVudA==-MS4wLjA=-aHR0cHM6Ly9pLnp0ZS5jb20uY24vaXNwYWNlL3BhZ2U=-aWNlbnRlcmFwaS56dGUuY29tLmNu",
    }

def _normalize_local_file_path(path: str) -> str:
    """规范化本地路径，跨平台支持"""
    raw = (path or "").strip()
    if not raw:
        return ""
    expanded = os.path.expandvars(os.path.expanduser(raw))
    return os.path.abspath(os.path.normpath(expanded))

def upload_image(space_id: str, local_file_path: str) -> Dict[str, Any]:
    """
    上传图片到 iCenter 文件服务

    Args:
        space_id: 空间 ID
        local_file_path: 本地图片路径

    Returns:
        dict: 上传结果，包含 status, url, file_name, file_size 等字段
    """
    result: Dict[str, Any] = {
        "status": "error",
        "message": "",
        "space_id": space_id,
        "file_name": "",
        "file_size": 0,
        "url": "",
    }

    file_path = _normalize_local_file_path(local_file_path)
    if not file_path:
        result["message"] = "上传失败：未提供图片路径"
        return result
    if not os.path.isfile(file_path):
        result["message"] = f"上传失败：图片不存在或不是普通文件 -> {file_path}"
        return result

    file_name = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)
    result["file_name"] = file_name
    result["file_size"] = file_size

    if file_size <= 0:
        result["message"] = "上传失败：图片大小为 0，不能上传空文件"
        return result
    # 图片大小限制：10MB
    MAX_IMAGE_SIZE = 10 * 1024 * 1024
    if file_size > MAX_IMAGE_SIZE:
        result["message"] = (
            f"上传失败：图片「{file_name}」大小为 {file_size/1024/1024:.2f}MB，"
            f"超过限制 10MB，请压缩后重试"
        )
        return result

    # 猜测 MIME 类型
    mime_type, _ = mimetypes.guess_type(file_name)
    if not mime_type or not mime_type.startswith("image/"):
        mime_type = "image/png"  # 默认

    # 获取图片尺寸（可选）
    width = None
    height = None
    if _PIL_AVAILABLE:
        try:
            with Image.open(file_path) as img:
                width, height = img.size
        except Exception:
            pass

    # 构建接口 URL
    upload_url = build_icenter_api_url(
        f"/zte-km-icenter-filetransfer/file/uploadImage?action=uploadimage&system=space-{space_id}&encode=utf-8"
    )

    headers = get_auth_headers()

    # 构建 multipart/form-data 请求体（按用户提供的 curl 格式）
    boundary = "----WebKitFormBoundaryOcxmaZFXB2vJsKjY"
    headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"

    # 构造 lastModifiedDate（类似 curl 中的格式）
    last_modified = time.strftime("%a %b %d %Y %H:%M:%S GMT+0800 (中国标准时间)")

    # 使用 requests 的 files 参数自动构建 multipart 请求
    # 注意：curl 中字段顺序很重要，但 requests 可能会重新排序；关键字段都存在即可
    files = {
        "id": (None, "WU_FILE_0"),
        "name": (None, file_name),
        "type": (None, mime_type),
        "lastModifiedDate": (None, last_modified),
        "size": (None, str(file_size)),
        "upfile": (file_name, open(file_path, "rb"), mime_type),
    }

    try:
        response = requests.post(
            upload_url,
            headers={k: v for k, v in headers.items() if k.lower() != "content-type"},
            files=files,
            timeout=300,
        )
    except requests.exceptions.RequestException as e:
        result["message"] = f"上传失败：请求异常 - {e}"
        return result
    except OSError as e:
        result["message"] = f"上传失败：读取本地文件异常 - {e}"
        return result
    finally:
        # 确保文件句柄关闭
        if "upfile" in files:
            files["upfile"][1].close()

    if response.status_code != 200:
        preview = (response.text or "").strip().replace("\n", " ")[:200]
        result["message"] = (
            f"上传失败：HTTP {response.status_code}"
            + (f"，响应: {preview}" if preview else "")
        )
        return result

    try:
        data = response.json()
    except ValueError:
        preview = (response.text or "").strip().replace("\n", " ")[:200]
        result["message"] = "上传失败：响应不是 JSON" + (f"，响应: {preview}" if preview else "")
        return result

    code_obj = data.get("code") or {}
    if code_obj.get("code") != "0000":
        msg = code_obj.get("msg") or code_obj.get("errMsg") or "上传失败"
        result["message"] = f"上传失败：{msg}（错误码: {code_obj.get('code')}）"
        return result

    bo = data.get("bo") or []
    if not isinstance(bo, list) or not bo:
        result["message"] = "上传失败：接口返回数据格式异常（缺少 bo 数组）"
        return result

    first = bo[0]
    path = str(first.get("path") or "").strip()
    if not path:
        result["message"] = "上传失败：接口返回缺少 path 字段"
        return result

    # 拼接完整 URL（无参基础 URL）
    base_url = f"https://icenterapi.zte.com.cn/{path}"
    result["status"] = "success"
    result["url"] = base_url
    result["width"] = width
    result["height"] = height
    result["message"] = "图片上传成功"
    return result

def parse_space_page_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    从 URL 中解析 spaceId 和 pageId

    Args:
        url: 页面 URL

    Returns:
        (space_id, page_id) 或 (None, None)
    """
    patterns = [
        r"/shared/([a-f0-9]+)/wiki/page/([a-f0-9]+)/view",
        r"/space/([a-f0-9]+)/wiki/page/([a-f0-9]+)/view",
        r"/ispace/#/space/([a-f0-9]+)/wiki/page/([a-f0-9]+)/view",
        r"#/shared/([a-f0-9]+)/wiki/page/([a-f0-9]+)/view",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1), match.group(2)
    return None, None

def fetch_page_content(space_id: str, page_id: str) -> Dict[str, Any]:
    """
    获取页面内容（用于后续更新）

    Returns:
        dict: {status, title, html_content, parent_id, apool, attribs, doc_type, ...}
    """
    emp_no, token = get_user_and_token()
    if not emp_no or not token:
        return {"status": "error", "message": "认证信息缺失"}

    system_param = build_system_param("space")
    t_ms = int(time.time() * 1000)
    url = build_icenter_api_url(
        f"/zte-rdcloud-space-external/content/{page_id}"
        f"?t={t_ms}&spaceId={space_id}&environmentType=browser&system={system_param}"
    )

    platform_type, user_agent = _get_platform_and_user_agent()
    headers = {
        "Accept": "application/json, text/plain",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://i.zte.com.cn",
        "Referer": "https://i.zte.com.cn/",
        "User-Agent": user_agent,
        "X-Auth-Value": token,
        "X-Emp-No": emp_no,
        "X-Tenant-Id": "ZTE",
        "X-Lang-Id": "zh_CN",
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()

        code_info = data.get("code", {})
        if code_info.get("code") not in ("0000", None):
            return {"status": "error", "message": code_info.get("msg", "获取内容失败")}

        bo = data.get("bo", data)
        return {
            "status": "success",
            "title": bo.get("title") or "",
            "html_content": bo.get("contentBody") or "",
            "parent_id": bo.get("parentId") or "",
            "apool": bo.get("apool"),
            "attribs": bo.get("attribs"),
            "doc_type": bo.get("docType") or bo.get("contentType") or "ARTICLE",
            "revision": bo.get("revision"),
            "is_leaf": bo.get("isLeaf"),
            "sort_no": bo.get("sortNo"),
            "disable_new_macro_id": bo.get("disableNewMacroId") or bo.get("disable_new_macro_id"),
            # 新增：章节列表与block列表（用于定位）
            "chapters": bo.get("chapters") or [],
            "blocks": bo.get("blocks") or [],
        }
    except Exception as e:
        return {"status": "error", "message": f"请求失败: {str(e)}"}

def update_page_content(
    space_id: str,
    page_id: str,
    title: str,
    html_content: str,
    parent_id: str = "",
    doc_type: str = "ARTICLE",
    **kwargs,
) -> Dict[str, Any]:
    """
    使用 coredata updateContent 接口更新页面内容

    Args:
        space_id: 空间 ID
        page_id: 页面 ID
        title: 页面标题
        html_content: HTML 格式的页面正文
        parent_id: 父页面 ID
        doc_type: 文档类型

    Returns:
        dict: 更新结果
    """
    emp_no, token = get_user_and_token()
    if not emp_no or not token:
        return {"status": "error", "message": "认证信息缺失"}

    system_param = build_system_param("space")
    url = build_icenter_api_url(
        f"/zte-rdcloud-space-external/content/{page_id}/updateContent"
        f"?spaceId={space_id}&system={system_param}"
    )

    headers = {
        "Content-Type": "application/json",
        "X-Emp-No": emp_no,
        "X-Auth-value": token,
        "X-Lang-Id": "zh_CN",
        "X-Tenant-Id": "ZTE",
    }

    body = {
        "id": page_id,
        "bizId": space_id,
        "docType": "HTML",  # 必须为 HTML，否则 text 字段会被当作纯文本处理
        "parentId": parent_id or "",
        "title": title or "",
        "revision": kwargs.get("revision"),
        "text": html_content,
        "attribs": kwargs.get("attribs") or "",
        "apool": kwargs.get("apool"),
        "isLeaf": kwargs.get("is_leaf"),
        "sortNo": kwargs.get("sort_no"),
        "disableNewMacroId": kwargs.get("disable_new_macro_id"),
    }

    try:
        response = requests.post(url, headers=headers, json=body, timeout=30)
        response.raise_for_status()
        data = response.json()
        if data.get("code", {}).get("code") == "0000":
            return {"status": "success", "message": "页面更新成功", "data": data.get("bo")}
        else:
            return {"status": "error", "message": data.get("code", {}).get("msg", "更新失败")}
    except Exception as e:
        return {"status": "error", "message": f"请求失败: {str(e)}"}


def update_chapter_content(
    space_id: str,
    page_id: str,
    chapter_name: str,
    chapter_html: str,
) -> Dict[str, Any]:
    """
    在指定章节内追加图片 HTML 内容（使用完整页面保存流程）

    注意：必须使用 update_page_html 进行完整页面保存，否则章节更新不会生效
    """
    emp_no, token = get_user_and_token()
    if not emp_no or not token:
        return {"status": "error", "message": "认证信息缺失"}

    # 获取页面原始 HTML（使用 safe_page_edit 的 fetch_page_html）
    if not _SAFE_EDIT_AVAILABLE or fetch_page_html is None:
        # 降级方案：使用原有的 fetch_page_content
        page_result = fetch_page_content(space_id, page_id)
        if page_result.get("status") != "success":
            return page_result
        existing_html = page_result.get("html_content") or ""
        title = page_result.get("title") or ""
    else:
        # 优先使用 safe_page_edit 的接口（更安全，保留格式）
        page_result = fetch_page_html(space_id, page_id, emp_no, token)
        if page_result.get("status") != "success":
            # 降级到 fetch_page_content
            page_result2 = fetch_page_content(space_id, page_id)
            if page_result2.get("status") != "success":
                return page_result2
            existing_html = page_result2.get("html_content") or ""
            title = page_result2.get("title") or ""
        else:
            existing_html = page_result.get("html_content") or ""
            title = page_result.get("title") or ""

    if not existing_html:
        return {"status": "error", "message": "页面内容为空"}

    soup = BeautifulSoup(existing_html, "html.parser")
    heading = None
    for tag in soup.find_all(['h1','h2','h3','h4','h5','h6']):
        if tag.get_text(strip=True) == chapter_name:
            heading = tag
            break
    if not heading:
        return {"status": "error", "message": f"未找到章节标题: {chapter_name}"}

    # 收集该章节下所有内容节点（直到下一个同级或更高级标题）
    chapter_nodes = []
    for sibling in heading.next_siblings:
        if getattr(sibling, 'name', None) and sibling.name.startswith('h'):
            try:
                if int(sibling.name[1]) <= int(heading.name[1]):
                    break
            except:
                pass
        chapter_nodes.append(sibling)

    # 构建新的章节 HTML（原内容 + 新图片）
    old_chapter_html = ''.join(str(node) for node in chapter_nodes)
    new_chapter_html = old_chapter_html
    if chapter_html:
        if old_chapter_html and not old_chapter_html.endswith('\n'):
            new_chapter_html += '\n'
        new_chapter_html += chapter_html

    # 替换原章节内容为新内容
    # 策略：将 heading 和 chapter_nodes 作为一个整体替换
    old_section_html = str(heading) + old_chapter_html

    # 构建完整的页面 HTML（替换章节内容）
    full_page_html = existing_html.replace(old_section_html, str(heading) + new_chapter_html)

    # 验证替换是否成功
    if full_page_html == existing_html:
        # 替换失败，尝试另一种方法：直接操作 soup 对象
        # 找到 heading 后面的所有节点并删除
        nodes_to_remove = list(chapter_nodes)
        for node in reversed(nodes_to_remove):
            if hasattr(node, 'decompose'):
                node.decompose()

        # 在 heading 后面插入新内容
        from bs4 import BeautifulSoup as BS
        new_soup = BS(full_page_html, "html.parser")
        # 重新查找 heading
        for h_tag in new_soup.find_all(['h1','h2','h3','h4','h5','h6']):
            if h_tag.get_text(strip=True) == chapter_name:
                # 在该标题后插入新内容
                for img_html in chapter_html.split('\n'):
                    if img_html.strip():
                        img_soup = BS(img_html, "html.parser")
                        for child in img_soup.children:
                            h_tag.insert_after(child)
                full_page_html = str(new_soup)
                break

    # 使用 safe_page_edit.update_page_html 进行完整页面保存（关键！）
    if _SAFE_EDIT_AVAILABLE and update_page_html is not None:
        return update_page_html(
            space_id=space_id,
            page_id=page_id,
            user_id=emp_no,
            token=token,
            html_content=full_page_html,
            title=title if title else None,
        )
    else:
        # 降级方案：使用原有的 update_page_content
        return update_page_content(
            space_id=space_id,
            page_id=page_id,
            title=title or "",
            html_content=full_page_html,
            doc_type="HTML",
        )

def update_table_cell_content(
    space_id: str,
    page_id: str,
    block_id: str,
    row_index: int,
    col_index: int,
    cell_html: str,
) -> Dict[str, Any]:
    """
    在表格单元格内追加 HTML 内容（使用完整页面保存流程）

    Args:
        space_id: 空间 ID
        page_id: 页面 ID
        block_id: block ID
        row_index: 行索引（0基）
        col_index: 列索引（0基）
        cell_html: 要追加到单元格的 HTML 内容

    Returns:
        dict: 更新结果
    """
    emp_no, token = get_user_and_token()
    if not emp_no or not token:
        return {"status": "error", "message": "认证信息缺失"}

    # 获取整个页面的 HTML（用于后续完整保存）
    if not _SAFE_EDIT_AVAILABLE or fetch_page_html is None:
        # 降级方案：使用原有的 fetch_page_content
        page_result = fetch_page_content(space_id, page_id)
        if page_result.get("status") != "success":
            return page_result
        existing_html = page_result.get("html_content") or ""
        title = page_result.get("title") or ""
    else:
        # 优先使用 safe_page_edit 的接口
        page_result = fetch_page_html(space_id, page_id, emp_no, token)
        if page_result.get("status") != "success":
            # 降级到 fetch_page_content
            page_result2 = fetch_page_content(space_id, page_id)
            if page_result2.get("status") != "success":
                return page_result2
            existing_html = page_result2.get("html_content") or ""
            title = page_result2.get("title") or ""
        else:
            existing_html = page_result.get("html_content") or ""
            title = page_result.get("title") or ""

    if not existing_html:
        return {"status": "error", "message": "页面内容为空"}

    # 查询 block 内容（获取当前表格 HTML）
    block_result = query_block_content(
        space_id=space_id,
        page_id=page_id,
        block_id=block_id,
        user_id=emp_no,
        token=token,
    )
    if block_result.get("status") != "success":
        return block_result

    block_html = block_result.get("body") or ""

    if not _EDIT_AVAILABLE or update_table_cell_html is None:
        return {"status": "error", "message": "缺少 studio_space_page_edit 模块支持，无法使用表格单元格插入功能。"}

    # 使用复用函数修改表格单元格 HTML
    cell_update = update_table_cell_html(
        block_html=block_html,
        row_index=row_index,
        col_index=col_index,
        new_cell_html=cell_html,
        cell_value_is_html=True,
    )
    if cell_update.get("status") != "success":
        return cell_update

    new_block_html = cell_update.get("html", "")

    # 在整个页面 HTML 中替换这个 block 的内容
    # 策略：查找包含该 block_id 的 div 并替换其内容
    soup = BeautifulSoup(existing_html, "html.parser")

    # iCenter 的 block 通常有 data-block-id 属性
    block_div = soup.find('div', attrs={'data-block-id': block_id})
    if not block_div:
        # 尝试其他方式查找（可能是 id 属性）
        block_div = soup.find('div', attrs={'id': block_id})

    if block_div:
        # 替换 block 内容
        new_soup = BeautifulSoup(new_block_html, "html.parser")
        # 清空原 block 内容
        block_div.clear()
        # 插入新内容
        for child in new_soup.children:
            block_div.append(child.copy())
        full_page_html = str(soup)
    else:
        # 找不到 block，尝试直接字符串替换
        # 注意：这种方法不太可靠，但作为降级方案
        full_page_html = existing_html.replace(block_html, new_block_html)
        if full_page_html == existing_html:
            return {"status": "error", "message": f"未找到 block {block_id}，无法更新表格单元格"}

    # 使用 safe_page_edit.update_page_html 进行完整页面保存（关键！）
    if _SAFE_EDIT_AVAILABLE and update_page_html is not None:
        return update_page_html(
            space_id=space_id,
            page_id=page_id,
            user_id=emp_no,
            token=token,
            html_content=full_page_html,
            title=title if title else None,
        )
    else:
        # 降级方案：使用原有的 update_page_content
        return update_page_content(
            space_id=space_id,
            page_id=page_id,
            title=title or "",
            html_content=full_page_html,
            doc_type="HTML",
        )

def query_block_content(
    *,
    space_id: str,
    page_id: str,
    block_id: str,
    user_id: str,
    token: str,
) -> Dict[str, Any]:
    """查询 block 内容（仅 body）。"""
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": get_platform_and_user_agent()[1],
        "X-Auth-Value": token,
        "X-Emp-No": user_id,
        "X-Tenant-Id": "ZTE",
        "X-Lang-Id": "zh_CN",
    }
    body = {
        "spaceId": space_id,
        "pageId": page_id,
        "blockId": block_id,
        "onlyBody": True,
        "contentType": "HTML",
    }
    url = build_icenter_api_url(
        "/zte-rdcloud-space-block/block/content/query"
    )
    try:
        resp = requests.post(url, json=body, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return {"status": "error", "message": f"查询 block 内容失败: {exc}"}

    code = (data.get("code") or {}).get("code")
    if code != "0000":
        return {
            "status": "error",
            "message": f"查询 block 内容失败: {(data.get('code') or {}).get('msgId', '未知错误')}",
            "raw": data,
        }
    bo = data.get("bo") or {}
    return {"status": "success", "body": bo.get("body") or "", "raw": data}


def update_block_content_via_mcp_api(
    *,
    space_id: str,
    page_id: str,
    block_id: str,
    block_html: str,
    user_id: str,
    token: str,
) -> Dict[str, Any]:
    """更新指定 block 内容（MCP 同款 update_block_content）。"""
    headers = {
        "Content-Type": "application/json",
        "X-Emp-No": user_id,
        "X-Auth-value": token,
        "X-Lang-Id": "zh_CN",
        "X-Tenant-Id": "ZTE",
    }
    url = build_icenter_api_url(
        f"/zte-rdcloud-space-content/block/content/{page_id}"
    )
    params = {"spaceId": space_id, "environmentType": "browser"}
    body = {
        "targetBlockId": block_id,
        "targetBlockTags": [],
        "targetBlockTitle": "",
        "block": {"body": block_html or ""},
    }
    try:
        resp = requests.put(url, params=params, json=body, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return {"status": "error", "message": f"更新 block 内容失败: {exc}"}

    code = (data.get("code") or {}).get("code")
    if code != "0000":
        return {
            "status": "error",
            "message": f"更新 block 内容失败: {(data.get('code') or {}).get('msgId', '未知错误')}",
            "raw": data,
        }
    return {
        "status": "success",
        "message": "表格单元格更新成功",
        "space_id": space_id,
        "page_id": page_id,
        "block_id": block_id,
        "update_result": data.get("bo"),
    }


def markdown_to_html(markdown_content: str) -> Optional[str]:
    """调用 iCenter 的 markdown2html 接口"""
    emp_no, token = get_user_and_token()
    if not emp_no or not token:
        return None

    markdown_url = build_icenter_api_url("/zte-rdcloud-space-wconvert/markdown2html")
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "X-Auth-Value": token,
        "X-Emp-No": emp_no,
        "X-Tenant-Id": "ZTE",
        "X-Lang-Id": "zh_CN",
    }

    try:
        response = requests.post(
            f"{markdown_url}?environmentType=browser",
            headers=headers,
            json={"content": markdown_content},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code", {}).get("code") == "0000":
            return data.get("bo")
    except Exception:
        pass
    return None

def _normalize_newlines_to_br(text: str) -> str:
    """简单换行归一化"""
    if not text:
        return text
    text = text.replace("\r\n", "<br>").replace("\n", "<br>").replace("\r", "<br>")
    return text

def markdown_to_html_fallback(markdown_content: str) -> Optional[str]:
    """本地降级方案：使用简单转换"""
    if not markdown_content.strip():
        return None
    try:
        # 尝试导入 markdown 库
        import markdown
        html = markdown.markdown(markdown_content, extensions=["extra", "nl2br"])
        return html
    except ImportError:
        # 极简降级：仅处理换行和粗体
        lines = markdown_content.splitlines()
        html_lines = []
        for line in lines:
            if line.startswith("# "):
                html_lines.append(f"<h1>{line[2:]}</h1>")
            elif line.startswith("## "):
                html_lines.append(f"<h2>{line[3:]}</h2>")
            elif line.startswith("### "):
                html_lines.append(f"<h3>{line[4:]}</h3>")
            elif line.startswith("- "):
                html_lines.append(f"<li>{line[2:]}</li>")
            else:
                html_lines.append(f"<p>{_normalize_newlines_to_br(line)}</p>")
        return "\n".join(html_lines)

def attach_image_to_page(
    space_id: str,
    page_id: str,
    image_url: str,
    alt_text: str = "",
    json_output: bool = False,
) -> Dict[str, Any]:
    """
    将图片插入到页面正文末尾

    Args:
        space_id: 空间 ID
        page_id: 页面 ID
        image_url: 图片 URL（已上传）
        alt_text: 图片说明（可选）
        json_output: 是否返回 JSON

    Returns:
        dict: 操作结果
    """
    # 1. 读取当前页面内容
    page_data = fetch_page_content(space_id, page_id)
    if page_data.get("status") != "success":
        msg = page_data.get("message") or "读取页面失败"
        if json_output:
            print(json.dumps({"status": "error", "message": msg}, ensure_ascii=False))
        return {"status": "error", "message": msg}

    title = page_data.get("title") or ""
    parent_id = page_data.get("parent_id") or ""
    existing_html = page_data.get("html_content") or ""
    apool = page_data.get("apool")
    attribs = page_data.get("attribs")
    doc_type = page_data.get("doc_type") or "ARTICLE"
    revision = page_data.get("revision")
    is_leaf = page_data.get("is_leaf")
    sort_no = page_data.get("sort_no")
    disable_new_macro_id = page_data.get("disable_new_macro_id")

    # 2. 在现有内容后追加 Markdown 图片语法
    # 注意：existing_html 是 HTML 格式，我们需要先把它当作 Markdown 处理吗？
    # 实际上 fetch_page_content 返回的 contentBody 已经是 HTML 了！
    # 所以我们可以直接构造新的 HTML 内容：在末尾追加 <img> 标签
    image_markdown = f"\n\n![{alt_text}]({image_url})\n"
    # 由于我们无法简单地将 Markdown 插入 HTML，这里采用两个策略：
    # 方案 A：将现有 HTML 转回 Markdown（不可靠）
    # 方案 B：直接将图片 HTML 追加到正文末尾（简单有效）
    image_html = f'<p><img src="{image_url}" alt="{alt_text}" /></p>\n'
    new_html = existing_html.rstrip() + "\n" + image_html

    # 3. 调用 updateContent 接口更新页面
    update_result = update_page_content(
        space_id=space_id,
        page_id=page_id,
        title=title,
        html_content=new_html,
        parent_id=parent_id,
        doc_type=doc_type,
        revision=revision,
        apool=apool,
        attribs=attribs,
        is_leaf=is_leaf,
        sort_no=sort_no,
        disable_new_macro_id=disable_new_macro_id,
    )

    return update_result

def _get_platform_and_user_agent() -> Tuple[str, str]:
    """获取平台类型和 User-Agent"""
    if SYSTEM == "Windows":
        return "windows", (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/138.0.0.0 Safari/537.36"
        )
    elif SYSTEM == "Darwin":
        return "macos", (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/138.0.0.0 Safari/537.36"
        )
    else:
        return "linux", (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/138.0.0.0 Safari/537.36"
        )

def build_system_param(prefix: str = "space") -> str:
    """生成 system 参数（与现有脚本一致）"""
    beijing_tz = time.timezone + 8 * 3600  # 简化处理
    beijing_now = datetime.now()
    seed = f"{beijing_now.year}{beijing_now.month:02d}{beijing_now.day:02d}"
    # 使用简单的哈希
    hash_val = 0
    for c in seed:
        hash_val = (31 * hash_val + ord(c)) & 0xFFFFFFFF
    if hash_val >= 0x80000000:
        hash_val -= 0x100000000
    return f"{prefix}-{hash_val}"

def main():
    configure_stdio_utf8()

    parser = argparse.ArgumentParser(
        description="将本地图片上传到 iCenter 空间并插入到页面正文（支持指定位置）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 上传图片并追加到页面末尾（默认行为，向后兼容）
  python3 upload_image_to_page.py --space-id "空间ID" --page-id "页面ID" --image-path "/path/to/image.png"

  # 批量上传多张图片追加到页面末尾
  python3 upload_image_to_page.py \
    --space-id "空间ID" --page-id "页面ID" \
    --image-path "/path/to/image1.png" \
    --image-path "/path/to/image2.png" \
    --alt-prefix "图"

  # 使用页面 URL 定位
  python3 upload_image_to_page.py \
    --page-url "https://i.zte.com.cn/index/ispace/#/space/<spaceId>/wiki/page/<pageId>/view" \
    --image-path "/path/to/image.png" --alt "示意图"

  # 🆕 将图片插入到指定章节内（在章节末尾追加）
  python3 upload_image_to_page.py \
    --page-url "<页面URL>" \
    --insert-mode chapter --chapter-name "1. 背景" \
    --image-path "/path/to/diagram.png" --alt "架构图"

  # 🆕 将图片插入到表格指定单元格（在单元格内追加）
  python3 upload_image_to_page.py \
    --page-url "<页面URL>" \
    --insert-mode table-cell \
    --block-id "BQU4T2MY" --row-index 1 --col-index 0 \
    --image-path "/path/to/chart.png" --alt "数据图表"

  # 🆕 仅上传图片，不插入页面
  python3 upload_image_to_page.py --space-id "空间ID" --image-path "/path/to/image.png" --no-attach --json

  # JSON 格式输出（用于程序调用）
  python3 upload_image_to_page.py --space-id "空间ID" --page-id "页面ID" --image-path "/path/to/image.png" --json
        """,
    )

    parser.add_argument("--space-id", help="空间 ID（可与 --page-id 配合使用）")
    parser.add_argument("--page-id", help="页面 ID（与 --space-id 二选一组合）")
    parser.add_argument("--page-url", help="页面完整 URL（可替代 --space-id + --page-id）")
    parser.add_argument(
        "--image-path",
        action="append",
        required=True,
        help="本地图片文件路径（可重复指定以批量上传）",
    )
    parser.add_argument(
        "--alt-prefix",
        default="",
        help="批量上传时，图片说明文字的前缀（文件名会自动添加）",
    )
    parser.add_argument("--alt", default="", help="图片说明文字（单张模式使用，批量时可选）")
    parser.add_argument("--no-attach", action="store_true", help="仅上传图片，不插入到页面")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON 格式输出")
    # 🆕 插入位置控制参数
    parser.add_argument(
        "--insert-mode",
        choices=["append", "chapter", "table-cell"],
        default="append",
        help="图片插入位置模式：append（页面末尾，默认）、chapter（指定章节内）、table-cell（表格单元格内）",
    )
    parser.add_argument(
        "--chapter-name",
        type=str,
        default=None,
        help="章节标题（当 --insert-mode=chapter 时必填）",
    )
    parser.add_argument(
        "--block-id",
        type=str,
        default=None,
        help="目标 block ID（当 --insert-mode=table-cell 时必填）",
    )
    parser.add_argument(
        "--row-index",
        type=int,
        default=None,
        help="表格行索引，从 0 开始（table-cell 模式必填）",
    )
    parser.add_argument(
        "--col-index",
        type=int,
        default=None,
        help="表格列索引，从 0 开始（table-cell 模式必填）",
    )

    args = parser.parse_args()

    # 参数校验：定位信息
    space_id = (args.space_id or "").strip()
    page_id = (args.page_id or "").strip()
    page_url = (args.page_url or "").strip()
    if page_url:
        sid, pid = parse_space_page_url(page_url)
        if not sid or not pid:
            msg = "无法从 URL 解析出 spaceId 和 pageId，请检查 URL 格式"
            if args.json_output:
                print(json.dumps({"status": "error", "message": msg}, ensure_ascii=False))
            else:
                print(f"❌ {msg}")
            sys.exit(1)
        space_id, page_id = sid, pid
    else:
        if not space_id:
            msg = "请提供 --space-id（和 --page-id）或 --page-url"
            if args.json_output:
                print(json.dumps({"status": "error", "message": msg}, ensure_ascii=False))
            else:
                print(f"❌ {msg}")
            sys.exit(1)

    # 如果用户只提供了 space-id 但没提供 page-id，且不是 --no-attach 模式，则需要 page_id
    if not page_id and not args.no_attach:
        msg = "缺少 --page-id（或使用 --page-url），无法确定目标页面"
        if args.json_output:
            print(json.dumps({"status": "error", "message": msg}, ensure_ascii=False))
        else:
            print(f"❌ {msg}")
        sys.exit(1)

    # 处理图片路径列表
    image_paths = args.image_path if isinstance(args.image_path, list) else [args.image_path]

    # 1. 批量上传图片
    uploaded = []  # 列表，每项: {path, url, file_name, file_size}
    failed = []    # 列表，每项: {path, error}

    for img_path in image_paths:
        result = upload_image(space_id, img_path)
        if result.get("status") == "success" and result.get("url"):
            base_url = result["url"]
            w = result.get("width")
            h = result.get("height")
            if w is not None and h is not None:
                full_url = f"{base_url}?w={w}&h={h}"
            else:
                full_url = base_url
            uploaded.append({
                "path": img_path,
                "url": full_url,
                "file_name": result.get("file_name", ""),
                "file_size": result.get("file_size", 0),
            })
        else:
            failed.append({"path": img_path, "error": result.get("message", "上传失败")})

    # 如果有任何上传失败，给出提示（但不完全退出）
    if failed:
        if not args.json_output:
            print(f"⚠️  有 {len(failed)} 张图片上传失败：")
            for f in failed:
                print(f"   - {f['path']}: {f['error']}")
        else:
            # JSON 模式下我们仍然收集失败信息，最后统一输出
            pass

    if not uploaded:
        msg = "所有图片均上传失败"
        if args.json_output:
            print(json.dumps({"status": "error", "message": msg, "failed": failed}, ensure_ascii=False))
        else:
            print(f"❌ {msg}")
        sys.exit(1)

    # 如果用户只想要上传，不插入页面
    if args.no_attach:
        if args.json_output:
            print(
                json.dumps(
                    {
                        "status": "success",
                        "message": f"成功上传 {len(uploaded)} 张图片（共 {len(image_paths)} 张）",
                        "uploaded": [{"file_name": u["file_name"], "url": u["url"], "file_size": u["file_size"]} for u in uploaded],
                        "failed": failed,
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(f"✅ 上传完成：成功 {len(uploaded)} 张，失败 {len(failed)} 张")
            for u in uploaded:
                print(f"\n📄 文件名: {u['file_name']}")
                print(f"  大小: {u['file_size']} 字节")
                print(f"  链接: {u['url']}")
        sys.exit(0)

    # 2. 批量插入图片到页面（根据插入模式分发）
    if not page_id:
        msg = "内部错误：未找到 page_id"
        if args.json_output:
            print(json.dumps({"status": "error", "message": msg}, ensure_ascii=False))
        else:
            print(f"❌ {msg}")
        sys.exit(1)

    insert_mode = args.insert_mode

    # 校验插入模式所需参数
    if insert_mode == "chapter":
        if not args.chapter_name:
            msg = "--insert-mode=chapter 时需要提供 --chapter-name"
            if args.json_output:
                print(json.dumps({"status": "error", "message": msg}, ensure_ascii=False))
            else:
                print(f"❌ {msg}")
            sys.exit(1)
    elif insert_mode == "table-cell":
        if not all([args.block_id, args.row_index is not None, args.col_index is not None]):
            msg = "--insert-mode=table-cell 时需要提供 --block-id、--row-index、--col-index"
            if args.json_output:
                print(json.dumps({"status": "error", "message": msg}, ensure_ascii=False))
            else:
                print(f"❌ {msg}")
            sys.exit(1)

    # 生成所有图片的 HTML 片段
    image_html_blocks = []
    for i, img in enumerate(uploaded):
        # 确定 alt 文本
        alt_text = ""
        file_name = img["file_name"]
        if args.alt_prefix:
            alt_text = f"{args.alt_prefix}{file_name}"
        elif args.alt:
            # 如果是多张图片且提供了 --alt，给每张图片加序号
            if len(uploaded) > 1:
                alt_text = f"{args.alt} {i+1}"
            else:
                alt_text = args.alt
        else:
            alt_text = file_name  # 使用文件名作为 alt
        image_html_blocks.append(f'<p><img src="{img["url"]}" alt="{alt_text}" /></p>')

    appended_images_html = "\n".join(image_html_blocks)

    # 根据插入模式执行
    if insert_mode == "append":
        # 使用 safe_page_edit 的安全编辑接口（方案B）
        if not _SAFE_EDIT_AVAILABLE:
            # 回退到原有逻辑（基于updateContent）
            page_result = fetch_page_content(space_id, page_id)
            if page_result.get("status") != "success":
                msg = page_result.get("message") or "读取页面失败"
                if args.json_output:
                    print(json.dumps({"status": "error", "message": msg, "uploaded": uploaded, "failed": failed}, ensure_ascii=False))
                else:
                    print(f"❌ {msg}")
                    print(f"   已上传 {len(uploaded)} 张图片，但未插入页面")
                sys.exit(1)

            title = page_result.get("title") or ""
            parent_id = page_result.get("parent_id") or ""
            existing_html = page_result.get("html_content") or ""
            # 追加到页面末尾
            new_html = existing_html.rstrip() + "\n" + appended_images_html + "\n"

            update_result = update_page_content(
                space_id=space_id,
                page_id=page_id,
                title=title,
                html_content=new_html,
                parent_id=parent_id,
                doc_type=page_result.get("doc_type") or "ARTICLE",
                revision=page_result.get("revision"),
                apool=page_result.get("apool"),
                attribs=page_result.get("attribs"),
                is_leaf=page_result.get("is_leaf"),
                sort_no=page_result.get("sort_no"),
                disable_new_macro_id=page_result.get("disable_new_macro_id"),
            )
        else:
            # 使用 safe_page_edit 接口（更安全，保留格式）
            emp_no, token = get_user_and_token()
            if not emp_no or not token:
                msg = "无法获取认证信息，请检查环境变量 coclaw_empno 和 coclaw_token"
                if args.json_output:
                    print(json.dumps({"status": "error", "message": msg, "uploaded": uploaded, "failed": failed}, ensure_ascii=False))
                else:
                    print(f"❌ {msg}")
                sys.exit(1)

            # 获取当前页面HTML
            page_result = fetch_page_html(space_id, page_id, emp_no, token)
            if page_result.get("status") != "success":
                msg = page_result.get("message") or "读取页面失败"
                if args.json_output:
                    print(json.dumps({"status": "error", "message": msg, "uploaded": uploaded, "failed": failed}, ensure_ascii=False))
                else:
                    print(f"❌ {msg}")
                    print(f"   已上传 {len(uploaded)} 张图片，但未插入页面")
                # 尝试回退到原有方法
                print("⚠️  尝试回退到原有更新方法...")
                page_result2 = fetch_page_content(space_id, page_id)
                if page_result2.get("status") == "success":
                    existing_html = page_result2.get("html_content") or ""
                    new_html = existing_html.rstrip() + "\n" + appended_images_html + "\n"
                    update_result = update_page_content(
                        space_id=space_id,
                        page_id=page_id,
                        title=page_result2.get("title") or "",
                        html_content=new_html,
                        parent_id=page_result2.get("parent_id") or "",
                        doc_type=page_result2.get("doc_type") or "ARTICLE",
                        revision=page_result2.get("revision"),
                        apool=page_result2.get("apool"),
                        attribs=page_result2.get("attribs"),
                        is_leaf=page_result2.get("is_leaf"),
                        sort_no=page_result2.get("sort_no"),
                        disable_new_macro_id=page_result2.get("disable_new_macro_id"),
                    )
                else:
                    sys.exit(1)
            else:
                existing_html = page_result.get("html_content") or ""
                new_html = existing_html.rstrip() + "\n" + appended_images_html + "\n"
                # safe_page_edit.update_page_html 不需要多余的附加字段
                update_result = update_page_html(
                    space_id=space_id,
                    page_id=page_id,
                    user_id=emp_no,
                    token=token,
                    html_content=new_html,
                    title=page_result.get("title")  # 可选，保持原标题
                )

    elif insert_mode == "chapter":
        # 章节模式：更新指定章节，在章节末尾追加图片（使用完整页面保存流程）
        update_result = update_chapter_content(
            space_id=space_id,
            page_id=page_id,
            chapter_name=args.chapter_name,
            chapter_html=appended_images_html,
        )
    elif insert_mode == "table-cell":
        # 表格单元格模式：在指定单元格内追加图片（多张图片会依次追加）
        # 每次只处理一张图片，因为表格单元格通常只放一个
        if len(uploaded) > 1:
            # 多张图片时，只使用第一张，其余给出警告
            if not args.json_output:
                print(f"⚠️  表格单元格模式每次只插入一张图片，将使用第一张：{uploaded[0]['file_name']}")
        img = uploaded[0]
        img_url = img['url']
        alt_text = args.alt or img['file_name']
        cell_img_html = "<p><img src=\"{}\" alt=\"{}\" /></p>\n".format(img_url, alt_text)

        update_result = update_table_cell_content(
            space_id=space_id,
            page_id=page_id,
            block_id=args.block_id,
            row_index=args.row_index,
            col_index=args.col_index,
            cell_html=cell_img_html,
        )
    else:
        msg = f"未知的插入模式: {insert_mode}"
        if args.json_output:
            print(json.dumps({"status": "error", "message": msg}, ensure_ascii=False))
        else:
            print(f"❌ {msg}")
        sys.exit(1)

    if update_result.get("status") != "success":
        msg = update_result.get("message") or "页面更新失败"
        if args.json_output:
            print(
                json.dumps(
                    {"status": "error", "message": msg, "uploaded": uploaded, "failed": failed},
                    ensure_ascii=False,
                )
            )
        else:
            print(f"❌ {msg}")
            print(f"   已上传 {len(uploaded)} 张图片，但未插入页面")
        sys.exit(1)

    # 统一结果输出
    if update_result.get("status") != "success":
        msg = update_result.get("message") or "图片插入失败"
        if args.json_output:
            print(
                json.dumps(
                    {"status": "error", "message": msg, "uploaded": uploaded, "failed": failed},
                    ensure_ascii=False,
                )
            )
        else:
            print(f"❌ {msg}")
            print(f"   已上传 {len(uploaded)} 张图片，但插入页面失败")
        sys.exit(1)

    # 成功输出
    if args.json_output:
        output = {
            "status": "success",
            "message": f"成功将 {len(uploaded)} 张图片插入页面（失败 {len(failed)} 张）",
            "uploaded": [{"file_name": u["file_name"], "url": u["url"], "file_size": u["file_size"]} for u in uploaded],
            "failed": failed,
            "insert_mode": insert_mode,
            "page_id": page_id,
        }
        if insert_mode == "chapter":
            output["chapter_name"] = args.chapter_name
        elif insert_mode == "table-cell":
            output.update({
                "block_id": args.block_id,
                "row_index": args.row_index,
                "col_index": args.col_index,
            })
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        mode_desc = {
            "append": "页面末尾",
            "chapter": f"章节「{args.chapter_name}」内",
            "table-cell": f"表格单元格(block={args.block_id}, row={args.row_index}, col={args.col_index})",
        }[insert_mode]
        print(f"✅ 成功将 {len(uploaded)} 张图片插入到{mode_desc}（失败 {len(failed)} 张）")
        for u in uploaded:
            print(f"\n📄 文件名: {u['file_name']}")
            print(f"  大小: {u['file_size']} 字节")
            print(f"  链接: {u['url']}")
        if failed:
            print(f"\n⚠️  部分图片上传失败：")
            for f in failed:
                print(f"   - {f['path']}: {f['error']}")

if __name__ == "__main__":
    main()
