#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=================================================================
studio-space-attachment - 附件下载/上传模块 (v1.3.0)
=================================================================
iCenter 空间附件下载技能

功能：
- 从 iCenter 空间页面中识别并提取附件信息
- 获取附件下载所需的认证凭证
- 下载附件到本地或获取下载链接
- 上传本地附件到空间并生成页面附件链接 HTML

下载流程：
1. 从页面 HTML 中解析附件信息
2. 调用 UDM/config 获取认证参数 (accessKey, timestamp, verifyCode)
3. 同一页面仅请求一次「内容权限」接口，根据 bo.spacePageEdit 决定 docAuth/add 的 perType（可编辑为 E，否则为 R）
4. 对每个附件先调用 docAuth/add，再调用 docFile/downloadUrl 获取下载地址
5. 自动下载文件到本地；每个成功下载的文件默认按 readme 调用 filecryptor -d <路径> 尝试解密

关键API：
- UDM配置: GET {ADVANCED_API}/udm/config
- 内容权限: GET …/authority/businessType/content/（noRedirect=true、businessId=页面ID、spaceId；每页面一次，用于 spacePageEdit → perType）
- 添加授权: POST {UDM_GATEWAY}/docAuth/add
- 获取下载地址: GET {UDM_GATEWAY}/docFile/downloadUrl
- 在线预览: GET {UDM_GATEWAY}/docFile/onlineView
- 附件上传: POST {DOC_CONVERTER_API}/file/upload?spaceId={spaceId}&environmentType=browser

依赖：
- Python 3.6+
- requests

作者：Co-Claw Team
创建：2026-03-16
更新：2026-03-16 (添加自动下载功能)
更新：2026-03-24 (先查内容权限再 addAuth，perType 随 spacePageEdit；每页仅一次权限请求)
更新：2026-03-24 (内容权限 URL 补充 noRedirect、businessId)
更新：2026-04-18 (下载成功后默认对所有附件尝试解密，不再按 perType 跳过)
更新：2026-04-19 (按 filecryptor 说明解析工具路径：环境变量、PATH、scripts/references 目录)
更新：2026-04-20 (新增附件上传能力，补充上传异常提示与 100M 限制校验)
更新：2026-06-10 (UAC 认证方式获取的下载链接为明文，无需解密)
"""

import os
import sys
import json
import re
import argparse
import time
import platform
import subprocess
import shutil
import mimetypes
import requests
from typing import Optional, Dict, List, Any, Tuple
from urllib.parse import quote
import html as html_lib

from system_param import (
    build_icenter_api_url,
    build_idrive_api_url,
    build_system_param,
    get_idrive_client_id,
    get_user_and_token as _get_user_and_token,
    get_uac_app_code_sec_key as _get_uac_app_code_sec_key,
)
from _platform_utils import configure_stdio_utf8

SYSTEM = platform.system()
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.dirname(SCRIPT_DIR)
REFERENCES_DIR = os.path.join(WORKSPACE_ROOT, "references")

# iCenter 前端埋点/识别用的客户端标识，避免在代码中散落硬编码
SPACE_ATTACHMENT_CLIENT_ID = "608078020840mk1rdko2rkzo98xmuojv"

# filecryptor：readme 要求 Windows 为 filecryptor_2025-10-13.exe -d <路径>（客户端 ≥7.2.0.38），
# Linux(nsdl) 为 chmod 755 后 ./filecryptor -d <路径>（客户端 ≥6.8.0.219）
FILECRYPTOR_ENV_KEYS = ("FILECRYPTOR_PATH", "ICENTER_FILECRYPTOR")


def get_user_and_token() -> Tuple[str, str]:
    """返回 (user_id, token)，优先从环境变量获取（非 strict）。"""
    return _get_user_and_token(strict=False)

def get_uac_app_code_sec_key() -> Tuple[str, str]:
    """返回 (uac_app_code, uac_sec_key)，优先从环境变量获取（非 strict）。"""
    return _get_uac_app_code_sec_key()

# 复用页面读取技能（fetch_space_page）
try:
    from fetch_space_page import fetch_space_page as fetch_space_page_skill
except ImportError:
    fetch_space_page_skill = None


# API 配置 - 使用正确的 API 路径
ADVANCED_API = build_icenter_api_url("/zte-rd-icenter-advanced")
ICENTER_AUTH_API = build_icenter_api_url("/zte-rd-icenter-auth")
UDM_GATEWAY = build_icenter_api_url(
    "/zte-rdcloud-space-gateway/inone/zte-km-cloududm-core"
)
DOC_CONVERTER_API = build_icenter_api_url("/zte-rd-icenter-docconverter")
SPACE_CONTENT_API = build_icenter_api_url("/zte-rdcloud-space-content")
MAX_UPLOAD_SIZE_BYTES = 100 * 1024 * 1024
MAX_UPLOAD_SIZE_MB = 100


def _get_user_agent_and_platform() -> Tuple[str, str]:
    """
    根据当前操作系统返回合适的 User-Agent 和 sec-ch-ua-platform，支持 Linux / Windows / macOS。
    """
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
    return ua, platform_str


def get_auth_headers() -> Dict[str, str]:
    """
    获取基础认证请求头

    Returns:
        包含认证信息的请求头字典
    """
    emp_no, token = get_user_and_token()

    if not emp_no or not token:
        raise ValueError(
            "未找到认证信息，请确保环境变量 coclaw_empno 和 coclaw_token 已设置，" "如仍报错请重启 Co-Claw 服务后重试"
        )

    user_agent, platform_str = _get_user_agent_and_platform()

    return {
        "Accept": "application/json, text/plain",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-US;q=0.7",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Pragma": "no-cache",
        "User-Agent": user_agent,
        "X-Auth-Value": token,
        "X-Cookie-From-Cros": "Y",
        "X-Emp-No": emp_no,
        "X-Lang-Id": "zh_CN",
        "X-Tenant-Id": "ZTE",
        "appcode": get_idrive_client_id(),
        "sec-ch-ua": '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": platform_str,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
    }


def _request_json(
    method: str,
    url: str,
    headers: Dict[str, str],
    timeout: int = 30,
    json_payload: Optional[Dict[str, Any]] = None,
    action: str = "请求",
) -> Optional[Dict[str, Any]]:
    """统一发起 HTTP 请求并解析 JSON，失败时返回 None。"""
    try:
        if method.upper() == "POST":
            response = requests.post(
                url, headers=headers, json=json_payload, timeout=timeout
            )
        else:
            response = requests.get(url, headers=headers, timeout=timeout)

        if response.status_code != 200:
            body_preview = (response.text or "").strip().replace("\n", " ")[:200]
            print(
                f"{action}失败: HTTP {response.status_code}"
                + (f", 响应: {body_preview}" if body_preview else "")
            )
            return None

        try:
            return response.json()
        except ValueError:
            body_preview = (response.text or "").strip().replace("\n", " ")[:200]
            print(
                f"{action}失败: 响应不是 JSON"
                + (f", 响应: {body_preview}" if body_preview else "")
            )
            return None
    except requests.exceptions.RequestException as e:
        print(f"{action}失败: {e}")
        return None


def parse_attachments_from_html(html_content: str) -> List[Dict[str, str]]:
    """
    从页面 HTML 内容中解析附件信息

    当前支持的附件链接格式：
    1. HTML 格式: <a href="udm-doc-xxx" fileName="文件名.ext" groupKey="WIKI...">（属性顺序任意）
    2. 当 fileName 缺失时，回退取 <a> 标签内文本作为文件名

    Args:
        html_content: 页面的 HTML 内容

    Returns:
        附件信息列表，每个附件包含：名称、URL、doc_id、groupKey（与 docAuth/add 一致）等
    """
    attachments = []

    # 处理 HTML 实体，避免中文名称被转成乱码/实体
    try:
        import html as _html_module
    except ImportError:  # 理论上不会发生，只是兜底
        _html_module = None

    if not html_content:
        return attachments

    # 同一 <a> 上属性顺序不固定，须整段匹配后再从属性串解析 fileName / groupKey（groupKey 为空时 docAuth 常返回 0042「对象不存在」）
    anchor_re = re.compile(
        r'<a\b([^>]*\bhref=["\']udm-doc-(\d+)["\'][^>]*)>',
        re.I,
    )
    for m in anchor_re.finditer(html_content):
        attrs, doc_id = m.group(1), m.group(2)
        doc_id = doc_id.strip()
        if not doc_id or any(a.get("doc_id") == doc_id for a in attachments):
            continue

        fn_m = re.search(r'\bfileName=["\']([^"\']+)["\']', attrs, re.I)
        file_name = fn_m.group(1).strip() if fn_m else ""
        if _html_module is not None and file_name:
            file_name = _html_module.unescape(file_name)

        if not file_name:
            end = m.end()
            close = html_content.find("</a>", end)
            if close != -1:
                inner = re.sub(r"<[^>]+>", "", html_content[end:close]).strip()
                file_name = inner
                if _html_module is not None and file_name:
                    file_name = _html_module.unescape(file_name)

        if not file_name:
            continue

        gk_m = re.search(r'\bgroupKey=["\']([^"\']+)["\']', attrs, re.I)
        group_key = gk_m.group(1).strip() if gk_m else ""

        attachment = {
            "name": file_name,
            "url": f"udm-doc-{doc_id}",
            "doc_id": doc_id,
            "type": "udm-doc",
            "groupKey": group_key,
        }
        ext_match = re.search(r"\.([a-zA-Z0-9]+)$", file_name)
        if ext_match:
            attachment["ext"] = ext_match.group(1)
        attachments.append(attachment)

    return attachments


def _filter_attachments_by_name_contains(
    attachments: List[Dict[str, str]], name_contains: Optional[str]
) -> List[Dict[str, str]]:
    """
    按附件名称做模糊包含过滤（大小写不敏感）。
    - name_contains 为空则返回原列表
    - 仅匹配附件的 name 字段
    """
    key = (name_contains or "").strip()
    if not key:
        return attachments
    key_lower = key.lower()
    return [a for a in attachments if key_lower in (a.get("name") or "").lower()]


def get_udm_config(space_id: str) -> Optional[Dict[str, str]]:
    """
    获取 UDM 配置文件

    调用 GET {ADVANCED_API}/udm/config?t=timestamp&spaceId={spaceId}&environmentType=browser&system=space-{xxx}

    Returns:
        包含认证配置的字典 (accessKey, timestamp, verifyCode)，失败返回 None
    """
    url = (
        f"{ADVANCED_API}/udm/config"
        f"?t={int(time.time() * 1000)}"
        f"&spaceId={space_id}"
        f"&environmentType=browser"
        f"&system={build_system_param('space')}"
    )

    data = _request_json(
        "GET", url, headers=get_auth_headers(), timeout=30, action="获取 UDM 配置"
    )
    if data and data.get("code", {}).get("code") == "0000":
        bo = data.get("bo", {})
        return {
            "accessKey": bo.get("accessKey", ""),
            "timestamp": bo.get("timestamp", ""),
            "verifyCode": bo.get("verifyCode", ""),
        }

    return None


def fetch_page_content_authority(
    space_id: str, business_id: str
) -> Optional[Dict[str, Any]]:
    """
    查询空间内容业务权限（同一 wiki 页面流程内只应调用一次）。

    GET …/authority/businessType/content/
    与前端一致：t、environmentType=browser、system、noRedirect=true、businessId（页面/内容 ID）、spaceId。

    Args:
        space_id: 空间 ID
        business_id: 当前 wiki 页面对应的 businessId（即 page_id）

    Returns:
        成功时返回 bo 字典（含 spacePageEdit 等）；失败返回 None
    """
    if not (space_id and business_id):
        return None
    url = (
        f"{ICENTER_AUTH_API}/authority/businessType/content/"
        f"?t={int(time.time() * 1000)}"
        f"&environmentType=browser"
        f"&system={build_system_param('space')}"
        f"&noRedirect=true"
        f"&businessId={quote(business_id, safe='')}"
        f"&spaceId={quote(space_id, safe='')}"
    )
    data = _request_json(
        "GET", url, headers=get_auth_headers(), timeout=30, action="获取内容权限"
    )
    if data and data.get("code", {}).get("code") == "0000":
        return data.get("bo") or {}
    return None


def per_type_from_space_page_edit(authority_bo: Optional[Dict[str, Any]]) -> str:
    """spacePageEdit 为 True 时使用编辑权限 E，否则只读 R。权限接口失败时默认 R。"""
    if authority_bo and authority_bo.get("spacePageEdit") is True:
        return "E"
    return "R"


def add_doc_auth(        
    space_id: str,
    group_key: str,
    doc_id: str,
    content_id: str,
    business_path: str,
    udm_config: Dict[str, str],
    per_type: str = "R"
) -> bool:
    """
    添加文档访问权限

    调用 POST {UDM_GATEWAY}/docAuth/add 添加权限

    Args:
        space_id: 空间ID
        group_key: 文件组Key (从附件元素中获取)
        doc_id: 文档ID
        udm_config: UDM 配置信息
        per_type: 权限类型，E 编辑 / R 只读（由内容权限 spacePageEdit 决定）

    Returns:
        是否添加成功
    """
    url = (
        f"{UDM_GATEWAY}/docAuth/add"
        f"?t={int(time.time() * 1000)}"
        f"&spaceId={space_id}"
        f"&environmentType=browser"
        f"&system={build_system_param('space')}"
    )

    headers = get_auth_headers().copy()
    headers["Content-Type"] = "application/json;charset=UTF-8"
    headers["X-Origin-ServiceName"] = "WIKI"
    headers["X-Access-Key"] = udm_config.get("accessKey", "")
    headers["X-Timestamp"] = udm_config.get("timestamp", "")
    headers["X-Verify-Code"] = udm_config.get("verifyCode", "")

    emp_no, _ = get_user_and_token()
    payload = {
        "groupKey": group_key,
        "filePers": [
            {"perType": per_type, "perObjId": [{"objType": "U", "objID": emp_no}]}
        ],
        "businessPath": business_path,
        "businessId": content_id
    }

    data = _request_json(
        "POST",
        url,
        headers=headers,
        json_payload=payload,
        timeout=30,
        action="添加文档权限",
    )
    if not data:
        return False
    ok = data.get("code", {}).get("code") == "0000"
    return ok


def get_download_url(
    space_id: str, file_id: str, udm_config: Dict[str, str] = None
) -> Tuple[Optional[str], str]:
    """
    获取文件下载 URL

    优先使用 UAC 认证方式（当 uac_app_code 环境变量存在时），否则使用 UDM Gateway 认证方式。

    Args:
        space_id: 空间 ID
        file_id: 文件 ID
        udm_config: UDM 配置信息（两种认证方式都需要）

    Returns:
        (下载 URL, 认证方式) 元组，认证方式为 'uac' 或 'udm'，URL 失败返回 (None, '')
    """
    # 检查是否配置了 UAC 认证
    uac_app_code, uac_sec_key = get_uac_app_code_sec_key()

    if uac_app_code and uac_sec_key:
        # 使用新的系统接口（UAC 认证方式）
        url = _get_download_url_with_uac(space_id, file_id, udm_config)
        if url:
            return (url, "uac")

    # 使用传统的 UDM Gateway 认证方式
    url = _get_download_url_with_udm(space_id, file_id, udm_config)
    if url:
        return (url, "udm")

    return (None, "")


def _get_download_url_with_uac(
    space_id: str, file_id: str, udm_config: Dict[str, str]
) -> Optional[str]:
    """
    使用 UAC 认证方式获取下载 URL（新系统接口）

    调用 GET {base_url}/docFile/system/downloadUrl?fileId={fileId}&userId={userId}

    Args:
        space_id: 空间 ID
        file_id: 文件 ID
        udm_config: UDM 配置信息（包含 accessKey, timestamp, verifyCode）

    Returns:
        下载 URL，失败返回 None
    """
    user_id, token = get_user_and_token()
    uac_app_code, uac_sec_key = get_uac_app_code_sec_key()

    if not user_id or not uac_app_code or not uac_sec_key:
        print("UAC 认证参数不完整，无法使用新系统接口")
        return None

    if not udm_config:
        print("缺少 UDM 配置参数，无法使用新系统接口")
        return None

    # 构建新系统接口 URL
    base_url = build_idrive_api_url("/zte-km-cloududm-core")
    url = (
        f"{base_url}/docFile/system/downloadurl"
        f"?t={int(time.time() * 1000)}"
        f"&spaceId={space_id}"
        f"&environmentType=browser"
        f"&system={build_system_param('space')}"
        f"&fileId={file_id}"
        f"&userId={user_id}"
    )

    # 构建请求头：保留所有原有请求头，新增 UAC 认证参数
    headers = get_auth_headers().copy()
    headers["X-Origin-ServiceName"] = "WIKI"
    headers["X-Access-Key"] = udm_config.get("accessKey", "")
    headers["X-Timestamp"] = udm_config.get("timestamp", "")
    headers["X-Verify-Code"] = udm_config.get("verifyCode", "")
    headers["appcode"] = uac_app_code
    headers["secretkey"] = uac_sec_key

    data = _request_json("GET", url, headers=headers, timeout=30, action="获取下载 URL(UAC)")
    if data and data.get("code", {}).get("code") == "0000":
        download_url = data.get("bo", {}).get("downloadUrl", "")
        if download_url:
            return download_url

    return None

def _get_download_url_with_udm(
    space_id: str, file_id: str, udm_config: Dict[str, str]
) -> Optional[str]:
    """
    使用 UDM Gateway 认证方式获取下载 URL（传统接口）

    调用 GET {UDM_GATEWAY}/docFile/downloadUrl?t=timestamp&spaceId={spaceId}&environmentType=browser&system=space-xxx&fileId={fileId}

    Args:
        space_id: 空间 ID
        file_id: 文件 ID
        udm_config: UDM 配置信息

    Returns:
        下载 URL，失败返回 None
    """
    url = (
        f"{UDM_GATEWAY}/docFile/downloadUrl"
        f"?t={int(time.time() * 1000)}"
        f"&spaceId={space_id}"
        f"&environmentType=browser"
        f"&system={build_system_param('space')}"
        f"&fileId={file_id}"
    )

    headers = get_auth_headers().copy()
    headers["X-Origin-ServiceName"] = "WIKI"
    headers["X-Access-Key"] = udm_config.get("accessKey", "")
    headers["X-Timestamp"] = udm_config.get("timestamp", "")
    headers["X-Verify-Code"] = udm_config.get("verifyCode", "")

    data = _request_json("GET", url, headers=headers, timeout=30, action="获取下载 URL")
    if data and data.get("code", {}).get("code") == "0000":
        return data.get("bo", {}).get("downloadUrl", "")

    return None



def get_online_view_url(
    space_id: str, file_id: str, udm_config: Dict[str, str]
) -> Optional[str]:
    """
    获取文件在线预览URL

    调用 GET {UDM_GATEWAY}/docFile/onlineView?t=timestamp&spaceId={spaceId}&environmentType=browser&system=space-xxx&fileId={fileId}

    Args:
        space_id: 空间ID
        file_id: 文件ID
        udm_config: UDM 配置信息

    Returns:
        预览URL，失败返回 None
    """
    url = (
        f"{UDM_GATEWAY}/docFile/onlineView"
        f"?t={int(time.time() * 1000)}"
        f"&spaceId={space_id}"
        f"&environmentType=browser"
        f"&system={build_system_param('space')}"
        f"&fileId={file_id}"
    )

    headers = get_auth_headers().copy()
    headers["X-Origin-ServiceName"] = "WIKI"
    headers["X-Access-Key"] = udm_config.get("accessKey", "")
    headers["X-Timestamp"] = udm_config.get("timestamp", "")
    headers["X-Verify-Code"] = udm_config.get("verifyCode", "")

    data = _request_json("GET", url, headers=headers, timeout=30, action="获取预览URL")
    if data and data.get("code", {}).get("code") == "0000":
        return data.get("bo", {}).get("onlineViewUrl", "")

    return None


def _build_upload_error_message(code_obj: Dict[str, Any]) -> str:
    """将接口 code 对象转换为便于用户理解的错误文案。"""
    code = (code_obj or {}).get("code") or ""
    err_msg = (code_obj or {}).get("errMsg") or ""
    msg = (code_obj or {}).get("msg") or ""
    sub_code = (code_obj or {}).get("subErrCode") or ""
    raw = err_msg or msg or sub_code or "上传失败，请稍后重试"
    raw_lower = str(raw).lower()

    if "100m" in raw_lower or "size" in raw_lower or "too large" in raw_lower:
        return f"上传失败：单个附件不能超过 {MAX_UPLOAD_SIZE_MB}MB"
    if "auth" in raw_lower or "authorize" in raw_lower or "permission" in raw_lower:
        return (
            "上传失败：当前账号未开通接口授权。"
            "请联系管理员申请附件上传接口权限。"
        )
    if code:
        return f"上传失败：{raw}（错误码: {code}）"
    return f"上传失败：{raw}"


def _build_attachment_anchor_html(
    page_id: str,
    file_id: str,
    group_key: str,
    file_name: str,
    file_ext_name: str,
    file_size: int,
) -> str:
    """根据上传返回字段生成页面附件链接 HTML。"""
    safe_page_id = (page_id or "").strip()
    safe_file_id = (file_id or "").strip()
    safe_group_key = (group_key or "").strip()
    safe_name = (file_name or "").strip() or "unnamed_file"
    safe_ext = (file_ext_name or "").strip()
    safe_size = str(file_size or 0)
    page_attr = f' pageId="{safe_page_id}"' if safe_page_id else ""
    return (
        f'<a fileSize="{safe_size}" groupKey="{safe_group_key}" fileName="{safe_name}" '
        f'href="udm-doc-{safe_file_id}" target="_blank" ext="{safe_ext}"{page_attr} '
        f'_href="udm-doc-{safe_file_id}">{safe_name}</a>'
    )


def _normalize_local_file_path(path: str) -> str:
    """
    规范化本地路径，兼容 Windows/Linux：
    - 展开环境变量与 ~
    - 转为绝对路径
    - 统一分隔符风格（按当前系统）
    """
    raw = (path or "").strip()
    if not raw:
        return ""
    expanded = os.path.expandvars(os.path.expanduser(raw))
    return os.path.abspath(os.path.normpath(expanded))


def upload_attachment_file(space_id: str, page_id: str, local_file_path: str) -> Dict[str, Any]:
    """
    上传单个本地附件到空间。
    - 接口限制：单文件 <= 100MB
    """
    result: Dict[str, Any] = {
        "status": "error",
        "message": "",
        "space_id": space_id,
        "page_id": page_id or "",
        "local_file_path": local_file_path,
        "file_name": "",
        "file_size": 0,
        "file_id": "",
        "group_key": "",
        "attachment_html": "",
    }
    file_path = _normalize_local_file_path(local_file_path)
    if not file_path:
        result["message"] = "上传失败：未提供本地文件路径"
        return result
    result["local_file_path"] = file_path
    if not os.path.isfile(file_path):
        result["message"] = f"上传失败：文件不存在或不是普通文件 -> {file_path}"
        return result

    file_name = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)
    result["file_name"] = file_name
    result["file_size"] = file_size

    if file_size <= 0:
        result["message"] = "上传失败：文件大小为 0，不能上传空文件"
        return result
    if file_size > MAX_UPLOAD_SIZE_BYTES:
        result["message"] = (
            f"上传失败：文件「{file_name}」大小为 {file_size / 1024 / 1024:.2f}MB，"
            f"超过接口限制 {MAX_UPLOAD_SIZE_MB}MB"
        )
        return result

    url = (
        f"{DOC_CONVERTER_API}/file/upload"
        f"?spaceId={quote(space_id, safe='')}"
        f"&environmentType=browser"
    )
    headers = get_auth_headers().copy()
    headers.pop("Content-Type", None)
    # requests 在 multipart/form-data 中可正确发送 UTF-8 文件名；此处仅补充 content-type 识别。
    content_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"

    try:
        with open(file_path, "rb") as f:
            response = requests.post(
                url,
                headers=headers,
                files={"file": (file_name, f, content_type)},
                timeout=300,
            )
    except requests.exceptions.RequestException as e:
        result["message"] = f"上传失败：请求异常 - {e}"
        return result
    except OSError as e:
        result["message"] = f"上传失败：读取本地文件异常 - {e}"
        return result

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
        result["message"] = _build_upload_error_message(code_obj)
        return result

    bo = data.get("bo") or {}
    files = bo.get("files") or []
    first = files[0] if isinstance(files, list) and files else {}
    file_id = str(first.get("id") or "").strip()
    group_key = str(bo.get("groupKey") or first.get("groupKey") or "").strip()
    file_name_resp = str(first.get("fileName") or file_name).strip() or file_name
    file_ext_name = str(first.get("fileExtName") or "").strip()
    file_size_resp = int(first.get("fileSize") or file_size)

    if not file_id or not group_key:
        result["message"] = "上传失败：接口返回缺少 fileId 或 groupKey，无法生成附件链接"
        return result

    result["status"] = "success"
    result["message"] = "上传成功"
    result["file_id"] = file_id
    result["group_key"] = group_key
    result["file_name"] = file_name_resp
    result["file_size"] = file_size_resp
    result["attachment_html"] = _build_attachment_anchor_html(
        page_id=page_id or "",
        file_id=file_id,
        group_key=group_key,
        file_name=file_name_resp,
        file_ext_name=file_ext_name,
        file_size=file_size_resp,
    )
    return result


def upload_attachments(space_id: str, page_id: str, file_paths: List[str]) -> Dict[str, Any]:
    """批量上传附件。"""
    result: Dict[str, Any] = {
        "status": "success",
        "message": "上传完成",
        "space_id": space_id,
        "page_id": page_id or "",
        "upload_results": [],
        "success_count": 0,
        "fail_count": 0,
    }
    for path in file_paths:
        item = upload_attachment_file(space_id=space_id, page_id=page_id, local_file_path=path)
        result["upload_results"].append(item)
        if item.get("status") == "success":
            result["success_count"] += 1
        else:
            result["fail_count"] += 1

    if result["fail_count"] > 0 and result["success_count"] == 0:
        result["status"] = "error"
        result["message"] = "所有附件上传失败"
    elif result["fail_count"] > 0:
        result["status"] = "partial_success"
        result["message"] = "部分附件上传失败"
    return result


def append_uploaded_attachments_to_page(
    space_id: str,
    page_id: str,
    upload_results: List[Dict[str, Any]],
    chapter_name: str = "",
    chapter_position: int = 1,
    attach_mode: str = "append",
) -> Dict[str, Any]:
    """
    将上传成功后的附件 HTML 写回页面正文。
    仅处理 upload_results 中 status=success 且含 attachment_html 的条目。
    """
    action_result: Dict[str, Any] = {
        "status": "error",
        "message": "",
        "space_id": space_id,
        "page_id": page_id,
        "appended_count": 0,
        "skipped_count": 0,
    }
    if not page_id:
        action_result["message"] = "回写失败：缺少 pageId"
        return action_result
    if fetch_space_page_skill is None:
        action_result["message"] = "回写失败：页面读取技能(fetch_space_page)不可用"
        return action_result

    anchors: List[str] = []
    for item in upload_results or []:
        if item.get("status") != "success":
            continue
        anchor = (item.get("attachment_html") or "").strip()
        if anchor:
            anchors.append(anchor)
    if not anchors:
        action_result["status"] = "success"
        action_result["message"] = "没有可回写页面的附件 HTML"
        return action_result

    user_id, token = get_user_and_token()
    page_data = fetch_space_page_skill(space_id, page_id, user_id, token)
    if not isinstance(page_data, dict) or page_data.get("status") != "success":
        msg = ""
        if isinstance(page_data, dict):
            msg = page_data.get("message", "")
        action_result["message"] = f"回写失败：读取页面失败{f' - {msg}' if msg else ''}"
        return action_result

    html_content = (page_data.get("html_content") or "").strip()
    page_title = (page_data.get("title") or "").strip()
    if not html_content:
        action_result["message"] = "回写失败：页面正文为空，无法追加附件 HTML"
        return action_result

    append_segments: List[str] = []
    skipped = 0
    for anchor in anchors:
        m = re.search(r'href=["\'](udm-doc-\d+)["\']', anchor, re.I)
        doc_ref = m.group(1) if m else ""
        if doc_ref and doc_ref in html_content:
            skipped += 1
            continue
        append_segments.append(anchor)

    if not append_segments:
        action_result["status"] = "success"
        action_result["message"] = "页面中已存在这些附件链接，无需重复回写"
        action_result["skipped_count"] = skipped
        return action_result

    chapter_name = (chapter_name or "").strip()
    mode = (attach_mode or "append").strip().lower()
    if mode not in ("append", "prepend"):
        mode = "append"

    anchors_html = "<br/>".join(append_segments)
    insert_html = f'<div data-attachment-mount="true">{anchors_html}</div>'

    if chapter_name:
        heading_re = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1>", re.I | re.S)
        normalized_target = re.sub(r"\s+", " ", chapter_name).strip()
        heading_hits: List[Dict[str, Any]] = []
        for m in heading_re.finditer(html_content):
            level = int(m.group(1))
            heading_inner = m.group(2) or ""
            plain = re.sub(r"<[^>]+>", "", heading_inner)
            plain = html_lib.unescape(plain)
            plain = re.sub(r"\s+", " ", plain).strip()
            heading_hits.append(
                {
                    "level": level,
                    "text": plain,
                    "start": m.start(),
                    "end": m.end(),
                }
            )
        matched = [h for h in heading_hits if h["text"] == normalized_target]
        pos = chapter_position if isinstance(chapter_position, int) else 1
        if pos < 1:
            pos = 1
        if len(matched) < pos:
            action_result["message"] = (
                f"回写失败：未找到第 {pos} 个章节「{chapter_name}」"
            )
            return action_result
        target_heading = matched[pos - 1]
        target_level = int(target_heading["level"])
        if mode == "prepend":
            insert_at = int(target_heading["end"])
        else:
            next_same_or_higher = None
            target_idx = heading_hits.index(target_heading)
            for h in heading_hits[target_idx + 1 :]:
                h_level = int(h["level"])
                if h_level <= target_level:
                    next_same_or_higher = h
                    break
            if next_same_or_higher is not None:
                insert_at = int(next_same_or_higher["start"])
            else:
                insert_at = len(html_content)
        new_html = (
            html_content[:insert_at]
            + "<br/>"
            + insert_html
            + "<br/>"
            + html_content[insert_at:]
        )
    else:
        if mode == "prepend":
            new_html = insert_html + "<br/>" + html_content
        else:
            new_html = html_content + "<br/>" + insert_html
    update_url = (
        f"{SPACE_CONTENT_API}/content/{quote(page_id, safe='')}/updateContent"
        f"?environmentType=browser&spaceId={quote(space_id, safe='')}"
    )
    headers = get_auth_headers().copy()
    headers["Content-Type"] = "application/json;charset=UTF-8"
    payload = {
        "docType": "HTML",
        "title": page_title,
        "text": new_html,
    }
    data = _request_json(
        "POST",
        update_url,
        headers=headers,
        timeout=30,
        json_payload=payload,
        action="回写页面附件",
    )
    if not data:
        action_result["message"] = "回写失败：更新页面接口无有效响应"
        return action_result
    code = (data.get("code") or {}).get("code")
    if code != "0000":
        code_obj = data.get("code") or {}
        err = code_obj.get("errMsg") or code_obj.get("msg") or "更新页面失败"
        action_result["message"] = f"回写失败：{err}（错误码: {code or 'unknown'}）"
        return action_result

    action_result["status"] = "success"
    action_result["appended_count"] = len(append_segments)
    action_result["skipped_count"] = skipped
    mount_desc = "页面正文"
    if chapter_name:
        mount_desc = f"章节「{chapter_name}」"
    action_result["message"] = (
        f"已回写 {len(append_segments)} 个附件链接到{mount_desc}"
        + (f"，模式: {mode}" if chapter_name else "")
        + (f"，跳过 {skipped} 个已存在链接" if skipped else "")
    )
    return action_result


def _sanitize_filename(file_name: str) -> str:
    """
    规范化文件名，确保在 Linux / Windows / macOS 下均可用。
    - 去除首尾空白
    - 替换 Windows 非法字符 <>:\"/\\|?* 为下划线
    - 避免空字符串
    """
    name = (file_name or "").strip() or "downloaded_file"
    name = re.sub(r'[<>:"/\\\\|?*]', "_", name)
    return name or "downloaded_file"


def _get_default_download_dir() -> str:
    """
    获取默认下载目录，支持 Linux / Windows / macOS。
    优先使用用户 Home 目录下的 Downloads。
    """
    home = os.path.expanduser("~")
    if not home or home == "~":
        return os.getcwd()
    return os.path.join(home, "Downloads")


def download_file(
    download_url: str, save_path: str, file_name: str = None
) -> Dict[str, Any]:
    """
    下载文件到本地

    Args:
        download_url: 下载URL
        save_path: 保存目录路径
        file_name: 文件名（可选，从URL中提取）你们自己

    Returns:
        下载结果字典
    """
    result = {
        "status": "pending",
        "message": "下载中...",
        "file_path": None,
        "file_size": 0,
    }

    try:
        # 如果未指定文件名，从URL中提取
        if not file_name:
            file_name = download_url.split("/")[-1]

        file_name = _sanitize_filename(file_name)

        # 确保保存目录存在
        os.makedirs(save_path, exist_ok=True)

        # 完整保存路径
        file_path = os.path.join(save_path, file_name)
        result["file_path"] = file_path

        # 下载文件
        print(f"开始下载: {file_name} -> {file_path}")

        response = requests.get(download_url, timeout=300, stream=True)

        if response.status_code == 200:
            total_size = int(response.headers.get("Content-Length", 0))
            downloaded_size = 0

            with open(file_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded_size += len(chunk)

                        # 显示进度（每1MB）
                        if downloaded_size % (1024 * 1024) < 8192:
                            percent = (
                                (downloaded_size / total_size * 100)
                                if total_size > 0
                                else 0
                            )
                            print(
                                f"  进度: {downloaded_size / 1024 / 1024:.1f} MB / {total_size / 1024 / 1024:.1f} MB ({percent:.1f}%)"
                            )

            result["status"] = "success"
            result["message"] = f"下载成功 ({downloaded_size / 1024 / 1024:.2f} MB)"
            result["file_size"] = downloaded_size
            print(f"✅ 下载完成: {file_name} ({downloaded_size / 1024 / 1024:.2f} MB)")
        else:
            result["status"] = "error"
            result["message"] = f"下载失败: HTTP {response.status_code}"

    except requests.exceptions.Timeout:
        result["status"] = "error"
        result["message"] = "下载超时"
    except requests.exceptions.RequestException as e:
        result["status"] = "error"
        result["message"] = f"下载失败: {str(e)}"
    except IOError as e:
        result["status"] = "error"
        result["message"] = f"文件写入失败: {str(e)}"
    except Exception as e:
        result["status"] = "error"
        result["message"] = f"未知错误: {str(e)}"

    return result


def _filecryptor_fixed_candidates() -> List[str]:
    """按 readme 列出的文件名，在 references / 脚本目录下查找。"""
    names: Tuple[str, ...]
    if SYSTEM == "Windows":
        names = ("filecryptor_2025-10-13.exe", "filecryptor.exe")
    else:
        names = ("filecryptor",)
    out: List[str] = []
    for base in (REFERENCES_DIR, SCRIPT_DIR):
        for n in names:
            out.append(os.path.join(base, n))
    return out


def resolve_filecryptor_executable() -> Optional[str]:
    """
    解析 filecryptor 可执行文件绝对路径。
    优先级：FILECRYPTOR_PATH / ICENTER_FILECRYPTOR → 固定目录候选 → PATH 中的 filecryptor。
    """
    for key in FILECRYPTOR_ENV_KEYS:
        raw = (os.environ.get(key) or "").strip()
        if not raw:
            continue
        if os.path.isfile(raw):
            return os.path.abspath(raw)
        if os.path.isdir(raw):
            if SYSTEM == "Windows":
                for n in ("filecryptor_2025-10-13.exe", "filecryptor.exe"):
                    p = os.path.join(raw, n)
                    if os.path.isfile(p):
                        return os.path.abspath(p)
            else:
                p = os.path.join(raw, "filecryptor")
                if os.path.isfile(p):
                    return os.path.abspath(p)

    for c in _filecryptor_fixed_candidates():
        if os.path.isfile(c):
            return os.path.abspath(c)

    if SYSTEM != "Windows":
        which = shutil.which("filecryptor")
        if which:
            return os.path.abspath(which)

    return None


def _parse_filecryptor_json_output(stdout: str, stderr: str) -> Optional[Dict[str, Any]]:
    """
    filecryptor 在「JSON Result:」后输出 JSON；管道运行时该段常在 stderr，交互终端可能在 stdout。
    顶层 status 可能与单项操作不一致，必须以 operations 为准。
    """
    combined = "\n".join((stdout or "", stderr or ""))
    if not combined.strip():
        return None
    marker = "JSON Result:"
    for chunk in (stderr or "", stdout or "", combined):
        if marker in chunk:
            tail = chunk.split(marker, 1)[-1].strip()
            try:
                return json.loads(tail)
            except ValueError:
                pass
    return None


def _decrypt_result_from_filecryptor_json(
    parsed: Dict[str, Any],
) -> Optional[Tuple[str, str]]:
    """
    根据 filecryptor 的 JSON 输出得到 (result_status, message)。
    result_status: success | error | skipped
    若无法从 JSON 判断则返回 None，由调用方回退到仅看进程退出码。
    """
    ops = parsed.get("operations")
    if not isinstance(ops, list) or not ops:
        return None
    messages: List[str] = []
    worst: str = "success"
    for op in ops:
        if not isinstance(op, dict):
            continue
        msg = (op.get("message") or "").strip()
        st = op.get("status")
        messages.append(msg)
        # 实测失败时 status 为 2，且 message 含 Decryption failed
        if st == 2 or "Decryption failed" in msg:
            if "Not an encrypted file" in msg:
                worst = "skipped"
            else:
                worst = "error"
        elif st not in (0, None) and worst == "success":
            worst = "error"
    if worst == "success":
        return ("success", "解密成功")
    if worst == "skipped":
        return (
            "skipped",
            "本地文件不是 ZTE 加密格式（常见原因：UDM 下载接口已下发明文，或附件本身未加密）。"
            f" filecryptor: {messages[0] if messages else ''}",
        )
    return ("error", messages[0] if messages else "解密失败")


def decrypt_attachment(local_file_path: str) -> Dict[str, Any]:
    """
    使用 filecryptor 对本地附件解密（readme：filecryptor -d <文件或文件夹路径>）。
    工具需自备：Windows 为 filecryptor_2025-10-13.exe，Linux 为 filecryptor（可先 chmod 755）。
    """
    result: Dict[str, Any] = {
        "status": "skipped",
        "message": "",
        "command": "",
    }
    file_path = (local_file_path or "").strip()
    if not file_path:
        result["message"] = "未提供附件路径，跳过解密"
        return result
    if not os.path.exists(file_path):
        result["message"] = f"附件不存在，跳过解密: {file_path}"
        return result

    cli_path = resolve_filecryptor_executable()
    if not cli_path:
        result["message"] = (
            "未找到 filecryptor。请将可执行文件放在本脚本同目录或 references/ "
            "(Windows: filecryptor_2025-10-13.exe；Linux: filecryptor)，"
            "或设置环境变量 FILECRYPTOR_PATH / ICENTER_FILECRYPTOR，"
            "或将 filecryptor 加入 PATH。版本要求见 readme（Windows ≥7.2.0.38，nsdl ≥6.8.0.219）。"
        )
        return result

    abs_target = os.path.abspath(file_path)
    cmd = [cli_path, "-d", abs_target]

    try:
        if SYSTEM != "Windows":
            try:
                os.chmod(cli_path, 0o755)
            except OSError:
                pass

        print(f"尝试解密: {abs_target}\n  命令: {' '.join(cmd)}")

        # universal_newlines：兼容 Python 3.6（3.7+ 的 text=True 在 3.6 上会报错）
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            check=False,
        )
        result["command"] = " ".join(cmd)
        output = proc.stdout or ""
        err = (proc.stderr or "").strip()

        parsed = _parse_filecryptor_json_output(output, err)
        if parsed is not None:
            verdict = _decrypt_result_from_filecryptor_json(parsed)
            if verdict is not None:
                result["status"], result["message"] = verdict
                return result

        output_stripped = (output or "").strip()
        err_stripped = (err or "").strip()
        if proc.returncode == 0:
            result["status"] = "success"
            result["message"] = output_stripped or err_stripped or "解密成功"
        else:
            result["status"] = "error"
            result["message"] = (
                err_stripped
                or output_stripped
                or f"解密失败，退出码: {proc.returncode}"
            )
    except Exception as e:
        result["status"] = "error"
        result["message"] = f"执行解密失败: {e}"

    return result


def format_result_as_markdown(result: Dict[str, Any]) -> str:
    """
    将结果格式化为 Markdown 格式

    Args:
        result: 下载/上传结果字典

    Returns:
        Markdown 格式的结果字符串
    """
    lines = []

    has_upload = bool(result.get("upload_results"))
    action_name = "上传" if has_upload else "下载"

    if result.get("status") == "success":
        lines.append(f"## ✅ {action_name}成功\n")
    elif result.get("status") == "partial_success":
        lines.append(f"## ⚠️ {action_name}部分成功\n")
    elif result.get("status") == "error":
        lines.append(f"## ❌ {action_name}失败\n")
    else:
        lines.append(f"## 📄 {action_name}结果\n")

    # 页面信息
    if result.get("page_title"):
        lines.append(f"**页面**: {result.get('page_title')}\n")

    # 过滤条件
    if result.get("name_contains"):
        lines.append(f"**附件筛选**: 名称包含「{result.get('name_contains')}」\n")

    # 附件列表
    if result.get("attachments"):
        lines.append("\n### 📎 附件列表\n")
        lines.append("| 文件名 | 类型 | 状态 | 操作 |\n")
        lines.append("|--------|------|------|------")

        for att in result.get("attachments", []):
            name = att.get("name", "未知文件")
            ext = att.get("ext", "").upper()
            doc_id = att.get("doc_id", "")
            att_type = att.get("type", "")

            lines.append(f"| {name} | {ext} | 识别成功 | - |")

        lines.append("")

    # 下载结果
    if result.get("download_results"):
        lines.append("\n### ⬇️ 下载结果\n")

        for r in result.get("download_results", []):
            name = r.get("name", "未知文件")
            status = r.get("status", "")
            message = r.get("message", "")
            download_url = r.get("download_url", "")
            # preview_url = r.get("preview_url", "")

            if status == "success":
                status_icon = "✅"
                # f"[下载]({download_url})" if download_url else "无下载链接"
            else:
                status_icon = "❌"
                # action = f"[预览]({preview_url})" if preview_url else message

            lines.append(f"- {status_icon} **{name}**\n")
            lines.append(f"  - 状态: {message}\n")
            if download_url:
                lines.append(f"  - 下载: [{download_url}]({download_url})\n")

            local_dl = r.get("local_download") or {}
            if local_dl:
                fp = local_dl.get("file_path") or ""
                if fp:
                    lines.append(f"  - 本地路径: `{fp}`\n")
                lm = local_dl.get("message") or ""
                if lm:
                    lines.append(f"  - 下载详情: {lm}\n")

            dec = r.get("decrypt_result") or {}
            if dec:
                ds = dec.get("status", "")
                if ds == "success":
                    dicon = "✅"
                elif ds == "skipped":
                    dicon = "⏭️"
                else:
                    dicon = "❌"
                lines.append(
                    f"  - 解密 {dicon}: {dec.get('message', '')}\n"
                )
                cmd = (dec.get("command") or "").strip()
                if cmd:
                    lines.append(f"  - 解密命令: `{cmd}`\n")

        lines.append("")

    # 上传结果
    if result.get("upload_results"):
        lines.append("\n### ⬆️ 上传结果\n")
        for r in result.get("upload_results", []):
            name = r.get("file_name") or os.path.basename(r.get("local_file_path", "") or "未知文件")
            status = r.get("status")
            status_icon = "✅" if status == "success" else "❌"
            lines.append(f"- {status_icon} **{name}**\n")
            lines.append(f"  - 状态: {r.get('message', '')}\n")
            lines.append(f"  - 本地路径: `{r.get('local_file_path', '')}`\n")
            file_size = r.get("file_size") or 0
            if file_size > 0:
                lines.append(f"  - 文件大小: {file_size / 1024 / 1024:.2f} MB\n")
            if r.get("file_id"):
                lines.append(f"  - fileId: `{r.get('file_id')}`\n")
            if r.get("group_key"):
                lines.append(f"  - groupKey: `{r.get('group_key')}`\n")
            if r.get("attachment_html"):
                lines.append("  - 附件HTML:\n")
                lines.append(f"    `{r.get('attachment_html')}`\n")
        lines.append("")
    attach_res = result.get("attach_to_page") or {}
    if attach_res:
        status_icon = "✅" if attach_res.get("status") == "success" else "❌"
        lines.append("\n### 🔗 页面挂载结果\n")
        lines.append(f"- {status_icon} {attach_res.get('message', '')}\n")
        if attach_res.get("appended_count") is not None:
            lines.append(f"- 新增挂载: {int(attach_res.get('appended_count') or 0)}\n")
        if attach_res.get("skipped_count") is not None:
            lines.append(f"- 跳过重复: {int(attach_res.get('skipped_count') or 0)}\n")
        lines.append("")

    # 消息
    if result.get("message"):
        lines.append(f"\n**{result.get('message')}**\n")

    # 附件统计信息
    total = result.get("total_attachments")
    processed = result.get("processed_attachments")
    if total is not None and processed is not None:
        extra = ""
        if result.get("truncated"):
            extra = "（已达到最大处理数量上限）"
        lines.append(f"\n共识别附件 {total} 个，本次处理 {processed} 个{extra}。\n")

    if result.get("upload_results") is not None:
        succ = int(result.get("success_count") or 0)
        fail = int(result.get("fail_count") or 0)
        lines.append(f"\n上传统计：成功 {succ} 个，失败 {fail} 个。\n")

    return "\n".join(lines)


def parse_space_page_id_from_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    从 iCenter 空间页面 URL 中解析 spaceId 和 pageId

    支持以下 URL 格式：
    - https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view
    - https://i.zte.com.cn/#/space/{spaceId}/wiki/page/{pageId}/view
    - https://i.zte.com.cn/#/modules/docManage/view?docId=xxx (单文档模式)
    - i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view (无协议)
    - ./shared/{spaceId}/wiki/page/{pageId}/view (相对路径)

    Args:
        url: 页面 URL

    Returns:
        (space_id, page_id) 元组
    """
    if not url:
        return None, None

    # 移除协议前缀（如果有）
    url = url.strip()

    # 查找 # 符号位置，处理 hash 路由
    hash_pos = url.find("#")
    if hash_pos != -1:
        url = url[hash_pos + 1 :]

    # 移除开头的 /
    url = url.lstrip("/")

    # 定义路径模式正则
    patterns = [
        # 模式1: shared/{spaceId}/wiki/page/{pageId}/view
        r"^shared/([a-f0-9]{32})/wiki/page/([a-f0-9]{32})/view",
        # 模式2: space/{spaceId}/wiki/page/{pageId}/view
        r"^space/([a-f0-9]{32})/wiki/page/([a-f0-9]{32})/view",
        # 模式3: modules/docManage/view?docId=xxx (单文档模式)
        r"^modules/docManage/view\?docId=([a-f0-9]{32})",
        # 模式4: 工作台路径格式
        r"^worktable/([a-f0-9]{32})/wiki/page/([a-f0-9]{32})",
    ]

    for pattern in patterns:
        match = re.match(pattern, url)
        if match:
            groups = match.groups()
            if len(groups) == 2:
                return groups[0], groups[1]
            elif len(groups) == 1:
                # 单文档模式，返回 space_id=None, doc_id=groups[0]
                return None, groups[0]

    return None, None


def download_attachments_from_page(
    space_id: str, page_id: str, args=None
) -> Dict[str, Any]:
    """
    从页面下载所有附件

    Args:
        space_id: 空间ID
        page_id: 页面ID
        args: 命令行参数（包含 download 和 output_dir）

    Returns:
        下载结果字典
    """
    result = {
        "space_id": space_id,
        "page_id": page_id,
        "status": "success",
        "message": "处理完成",
        "attachments": [],
        "download_results": [],
    }

    # 1. 获取页面内容（复用 fetch_space_page 技能）
    if fetch_space_page_skill is None:
        result["status"] = "error"
        result["message"] = "页面读取技能(fetch_space_page)不可用，无法获取页面内容"
        return result

    user_id, token = get_user_and_token()
    page_data = fetch_space_page_skill(space_id, page_id, user_id, token)
    if not page_data or page_data.get("status") != "success":
        result["status"] = "error"
        result["message"] = (
            page_data.get("message", "获取页面内容失败")
            if isinstance(page_data, dict)
            else "获取页面内容失败"
        )
        return result

    # 获取页面标题
    result["page_title"] = page_data.get("title", "未知页面")

    # 2. 解析附件
    html_content = page_data.get("html_content", "") or ""
    attachments = parse_attachments_from_html(html_content)

    if not attachments:
        result["status"] = "success"
        result["message"] = "页面中未找到附件"
        result["attachments"] = []
        return result

    # 2.1 可选：按名称模糊匹配过滤
    name_contains = getattr(args, "name_contains", None) if args else None
    if name_contains:
        filtered = _filter_attachments_by_name_contains(attachments, name_contains)
        result["name_contains"] = str(name_contains)
        if not filtered:
            result["status"] = "success"
            result["message"] = f"页面中未找到名称包含「{name_contains}」的附件"
            result["attachments"] = []
            return result
        attachments = filtered

    result["total_attachments"] = len(attachments)

    # 2.2 可选：限制最大处理数量，避免页面附件过多导致请求时间过长
    max_count = getattr(args, "max_attachments", None) if args else None
    if isinstance(max_count, int) and max_count > 0 and len(attachments) > max_count:
        attachments = attachments[:max_count]
        result["truncated"] = True
        result["processed_attachments"] = len(attachments)
    else:
        result["truncated"] = False
        result["processed_attachments"] = len(attachments)

    result["attachments"] = attachments

    # 3. 获取 UDM 配置
    udm_config = get_udm_config(space_id)
    if not udm_config:
        result["status"] = "error"
        result["message"] = "获取 UDM 认证配置失败"
        for att in attachments:
            result["download_results"].append(
                {
                    "doc_id": att.get("doc_id", ""),
                    "status": "error",
                    "message": "获取 UDM 认证配置失败",
                    "download_url": None,
                    # "preview_url": None,
                    "name": att.get("name", ""),
                    "ext": att.get("ext", ""),
                }
            )
        return result

    # 4. 同一页面仅请求一次内容权限，用于后续每个附件的 docAuth perType
    authority_bo = fetch_page_content_authority(space_id, page_id)
    per_type = per_type_from_space_page_edit(authority_bo)
    content_id = page_id
    business_path = page_data.get("parent_path", "") or ""
    # 5. 逐个处理附件：先 addAuth，再取 downloadUrl
    for idx, att in enumerate(attachments, 1):
        doc_id = att.get("doc_id", "")
        if not doc_id:
            continue

        # 获取 groupKey（如果有的话）
        group_key = att.get("groupKey", "")

        auth_ok = add_doc_auth(
            space_id, group_key, doc_id, content_id, business_path, udm_config, per_type
        )
        if not auth_ok:
            download_result = {
                "doc_id": doc_id,
                "status": "error",
                "message": "添加文档权限失败，已跳过获取下载链接",
                "download_url": None,
                # "preview_url": None,
                "name": att.get("name", ""),
                "ext": att.get("ext", ""),
            }
            result["download_results"].append(download_result)
            continue

        download_url, auth_method = get_download_url(space_id, doc_id, udm_config)

        download_result = {
            "doc_id": doc_id,
            "status": "success" if download_url else "error",
            "message": "获取下载链接成功" if download_url else "获取下载链接失败",
            "download_url": download_url,
            "auth_method": auth_method,  # 'uac' 或 'udm'
            # "preview_url": preview_url,
            "name": att.get("name", ""),
            "ext": att.get("ext", ""),
        }

        # 如果指定了下载选项且有下载链接，则下载文件
        if download_url and args and getattr(args, "download", False):
            save_path = getattr(args, "output_dir", ".")
            if save_path == ".":
                save_path = _get_default_download_dir()
            file_name = att.get("name", f"attachment_{doc_id}")
            download_result["local_download"] = download_file(
                download_url, save_path, file_name
            )
            local_dl = download_result.get("local_download") or {}
            should_decrypt = (
                local_dl.get("status") == "success"
                and local_dl.get("file_path")
                and auth_method != "uac"
                and not (args and getattr(args, "no_decrypt", False))
            )
            if should_decrypt:
                download_result["decrypt_result"] = decrypt_attachment(
                    local_dl["file_path"]
                )

        result["download_results"].append(download_result)

    # 检查是否所有附件都下载失败
    all_failed = all(r["status"] == "error" for r in result["download_results"])
    if all_failed:
        result["status"] = "error"
        result["message"] = "所有附件下载失败"

    return result


if __name__ == "__main__":
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(
        description="从 iCenter 空间页面下载/上传附件",
        epilog="示例:\n"
        "  python studio_space_attachment.py --url 'https://i.zte.com.cn/#/shared/xxx/wiki/page/yyy/view'\n"
        "  python studio_space_attachment.py --url '...' --download --output-dir ./downloads\n"
        "  python studio_space_attachment.py --space-id xxx --page-id yyy --upload-file /tmp/a.docx\n"
        "  python studio_space_attachment.py --space-id xxx --upload-file /tmp/a.docx --upload-file /tmp/b.zip\n"
        "  python studio_space_attachment.py --url '...' --upload-file /tmp/a.pdf\n"
        "  python studio_space_attachment.py --url '...' --upload-file /tmp/a.pdf --no-attach-to-page\n"
        "  python studio_space_attachment.py --url '...' --upload-file /tmp/a.pdf --attach-chapter-name '1. 背景' --attach-position 1 --attach-mode append\n"
        "  python studio_space_attachment.py --space-id xxx --page-id yyy\n"
        "提示: 上传接口单个文件不超过 100MB；超过会在本地校验并直接提示。\n"
        "提示: 用户ID和Token从环境变量 coclaw_empno 和 coclaw_token 获取",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # URL参数（推荐使用）
    parser.add_argument(
        "-u",
        "--url",
        dest="url",
        help="iCenter页面URL（自动解析spaceId和pageId），如: https://i.zte.com.cn/#/shared/xxx/wiki/page/yyy/view",
    )

    # 页面参数
    parser.add_argument("--space-id", dest="space_id", help="空间ID（与 page-id 配合使用）")
    parser.add_argument("--page-id", dest="page_id", help="页面ID（与 space-id 配合使用）")

    # 输出格式
    parser.add_argument(
        "--json", dest="json_output", action="store_true", help="JSON 格式输出"
    )

    # 下载选项
    parser.add_argument(
        "-d",
        "--download",
        dest="download",
        action="store_true",
        help="自动下载附件到本地（未指定 --output-dir 时默认使用 ~/Downloads）",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        dest="output_dir",
        default=".",
        help="下载保存目录（默认: 当前目录，可使用 ~/Downloads）",
    )
    parser.add_argument(
        "--no-decrypt",
        dest="no_decrypt",
        action="store_true",
        help="下载成功后不调用 filecryptor 解密（默认会尝试解密）",
    )

    # 附件过滤选项
    parser.add_argument(
        "--name-contains",
        dest="name_contains",
        default="",
        help="仅处理名称中包含该关键字的附件（模糊匹配，大小写不敏感）",
    )

    parser.add_argument(
        "--max-attachments",
        dest="max_attachments",
        type=int,
        default=0,
        help="最多处理的附件数量（默认 0 表示不限制；用于页面包含数百个附件时控制请求规模）",
    )
    parser.add_argument(
        "--upload-file",
        dest="upload_files",
        action="append",
        default=[],
        help="上传本地附件（可重复传多个，例如 --upload-file a.docx --upload-file b.zip）",
    )
    parser.add_argument(
        "--no-attach-to-page",
        dest="no_attach_to_page",
        action="store_true",
        help="上传成功后不自动挂载到页面正文（默认自动挂载）",
    )
    parser.add_argument(
        "--attach-chapter-name",
        dest="attach_chapter_name",
        default="",
        help="挂载到指定章节标题（不传则挂载到整页正文）",
    )
    parser.add_argument(
        "--attach-position",
        dest="attach_position",
        type=int,
        default=1,
        help="同名章节位置（从1开始，默认1）",
    )
    parser.add_argument(
        "--attach-mode",
        dest="attach_mode",
        choices=["append", "prepend"],
        default="append",
        help="挂载模式：append(章节末尾/页面末尾) 或 prepend(章节开头/页面开头)",
    )

    args = parser.parse_args()
    upload_mode = bool(args.upload_files)

    def _resolve_space_page_from_args() -> Tuple[Optional[str], Optional[str]]:
        if args.url:
            return parse_space_page_id_from_url(args.url)
        return args.space_id, args.page_id

    # 上传模式：必须提供 spaceId，pageId 可选（用于生成附件 HTML 的 pageId 属性）
    if upload_mode:
        space_id, page_id = _resolve_space_page_from_args()
        if not space_id:
            parser.print_help()
            print("\n错误: 上传附件时请提供 --space-id，或提供可解析出 spaceId 的 --url")
            sys.exit(1)
        attach_to_page = not bool(getattr(args, "no_attach_to_page", False))
        if attach_to_page and not page_id:
            parser.print_help()
            print("\n错误: 默认会自动挂载到页面正文，请提供页面 URL 或 --page-id；若仅上传不挂载可加 --no-attach-to-page")
            sys.exit(1)
        result = upload_attachments(space_id=space_id, page_id=page_id or "", file_paths=args.upload_files)
        if attach_to_page and page_id:
            result["attach_to_page"] = append_uploaded_attachments_to_page(
                space_id=space_id,
                page_id=page_id,
                upload_results=result.get("upload_results", []),
                chapter_name=args.attach_chapter_name,
                chapter_position=max(1, int(args.attach_position or 1)),
                attach_mode=args.attach_mode,
            )
        if args.json_output:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(format_result_as_markdown(result))
    elif args.url:
        space_id, page_id = _resolve_space_page_from_args()
        if space_id and page_id:
            print(f"解析成功: spaceId={space_id[:16]}..., pageId={page_id[:16]}...")
            result = download_attachments_from_page(space_id, page_id, args)
            if args.json_output:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(format_result_as_markdown(result))
        else:
            print("\n错误: 无法从URL中解析 spaceId 和 pageId（当前版本不支持单文档下载）")
            print(f"URL格式示例: https://i.zte.com.cn/#/shared/{'x'*32}/wiki/page/{'x'*32}/view")
            sys.exit(1)
    elif args.space_id and args.page_id:
        # 从页面下载所有附件
        result = download_attachments_from_page(args.space_id, args.page_id, args)
        if args.json_output:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(format_result_as_markdown(result))
    else:
        parser.print_help()
        print("\n错误: 下载请提供 --url 或 --space-id + --page-id；上传请提供 --upload-file 和 --space-id")
        sys.exit(1)
