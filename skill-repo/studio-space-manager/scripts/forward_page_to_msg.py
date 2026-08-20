#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=================================================================
studio-space-manager - 页面转发模块 (v2.8.0)
=================================================================
iCenter 空间页面转发技能

将空间页面或页面链接转发到 iCenter 消息或邮件。默认转发到消息，可转发给本人或指定用户（工号或姓名）；
使用 --to-email 时转发到邮件，需至少指定一名收件人，可选抄送、密送、附言与邮件主题。
支持从完整页面链接中解析 space_id 与 content_id；若按姓名指定接收人且存在重名，会返回候选列表并提示使用 --to-emp-no 选择。

跨平台支持：
- ✅ Linux (Ubuntu/CentOS/Debian)
- ✅ Windows 10/11
- ✅ macOS (通过 Python)

使用方式：
  # Linux / macOS
  python3 forward_page_to_msg.py --url "<页面链接>"
  python3 forward_page_to_msg.py --url "<页面链接>" --to-email --to-name "张三"

  # Windows
  python forward_page_to_msg.py --url "<页面链接>"

依赖：
- Python 3.6+
- requests

作者：Co-Claw Team
创建：2026-03-05
更新：2026-03-07
"""

import argparse
import json
import os
import platform
import re
import sys
import tempfile
from datetime import datetime
from typing import List, Optional, Tuple

import requests

from system_param import build_icenter_api_url, get_user_and_token
from _platform_utils import configure_stdio_utf8

# 按姓名关键字查找用户接口（addresearch/docs/search）
USER_SEARCH_BY_NAME_URL = os.getenv(
    "FORWARD_USER_SEARCH_URL",
    build_icenter_api_url("/zte-km-icenter-addresearch/user/plain/docs/search"),
)

# 页面详情接口（用于获取页面标题，转发到邮件时作为邮件主题）
CONTENT_DETAIL_BASE = build_icenter_api_url("/zte-rdcloud-space-content/content")

# 转发到邮件接口
SHARE_CONTENT_BASE = build_icenter_api_url("/zte-rd-icenter-contents/content")


# 禁用 SSL 证书验证警告
try:
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass


def parse_page_link(url: str) -> Optional[Tuple[str, str]]:
    """
    从空间页面链接中解析 space_id 和 content_id（页面ID）。

    支持的链接格式：
    - https://i.zte.com.cn/#/shared/{space_id}/wiki/page/{content_id}/view
    - https://i.zte.com.cn/index/ispace/#/space/{space_id}/wiki/page/{content_id}/view

    Returns:
        (space_id, content_id) 或 None
    """
    if not url or not url.strip():
        return None
    url = url.strip()
    # 取 # 后的部分（hash 路由）或整段 URL 做匹配
    part = url.split("#", 1)[-1] if "#" in url else url
    # 匹配 /shared/ 或 /space/ 后的 32 位十六进制 id，以及 /wiki/page/ 后的 32 位十六进制 id
    pattern = r"(?:shared|space)/([a-f0-9]{32})/wiki/page/([a-f0-9]{32})/view"
    match = re.search(pattern, part, re.IGNORECASE)
    if match:
        return match.group(1), match.group(2)
    return None


def get_user_name(emp_no: str, token: str, lang: str = "zh_CN") -> str:
    """
    获取用户姓名，用于转发消息接口。
    """
    url = build_icenter_api_url("/zte-km-icenter-user/user/plain/queryUsersByIds")
    headers = {
        "Accept": "application/json, text/plain",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "X-Auth-Value": token,
        "X-Emp-No": emp_no,
        "X-Lang-Id": lang,
        "X-Tenant-Id": "ZTE",
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(url, headers=headers, json=[emp_no], timeout=30)
        response.raise_for_status()
        result = response.json()
        user_list = None
        if isinstance(result, list):
            user_list = result
        elif isinstance(result, dict):
            if result.get("code", {}).get("code") == "0000" and "bo" in result:
                user_list = result.get("bo", [])
            elif "bo" in result:
                user_list = result.get("bo", [])
        if isinstance(user_list, list) and len(user_list) > 0:
            u = user_list[0]
            name = u.get("name") or u.get("chName") or u.get("employeeName") or ""
            if name:
                return name
    except Exception as e:
        print(f"[WARN] 获取用户姓名失败: {e}", file=sys.stderr)
    return emp_no


def search_users_by_name(
    name: str,
    caller_emp_no: str,
    token: str,
    lang: str = "zh_CN",
) -> List[dict]:
    """
    根据姓名关键字搜索用户，返回候选列表 [{employeeNo, employeeName}, ...]。
    使用接口：zte-km-icenter-addresearch/user/plain/docs/search
    """
    if not name or not name.strip():
        return []
    name = name.strip()
    url = USER_SEARCH_BY_NAME_URL
    headers = {
        "Accept": "application/json, text/plain",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "X-Auth-Value": token,
        "X-Emp-No": caller_emp_no,
        "X-Lang-Id": lang,
        "Content-Type": "application/json;charset=UTF-8",
    }
    body = {
        "employeeShortId": caller_emp_no,
        "enableLabel": True,
        "keyword": name,
    }
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[WARN] 按姓名搜索用户失败: {e}", file=sys.stderr)
        return []
    code_info = data.get("code") or {}
    if isinstance(code_info, dict) and code_info.get("code") != "0000":
        return []
    items = data.get("bo") or []
    if not isinstance(items, list):
        return []
    result = []
    for u in items:
        if not isinstance(u, dict):
            continue
        eid = (
            u.get("employeeShortId")
            or u.get("employeeNO")
            or u.get("employeeNo")
            or u.get("id")
            or ""
        )
        ename = u.get("name") or u.get("chName") or u.get("employeeName") or ""
        if eid:
            result.append(
                {
                    "employeeNo": str(eid).strip(),
                    "employeeName": (ename or str(eid)).strip(),
                }
            )
    return result


def resolve_recipients(
    emp_no_str: Optional[str],
    name_str: Optional[str],
    caller_emp_no: str,
    token: str,
    lang: str,
    space_id: str = "",
    content_id: str = "",
) -> Tuple[Optional[List[Tuple[str, str]]], Optional[dict]]:
    """
    将「工号串」和「姓名串」解析为 [(emp_no, name), ...]。
    若某姓名重名，返回 (None, need_choice_result)，调用方应打印并退出；否则返回 (targets, None)。
    """
    emp_nos = [s.strip() for s in (emp_no_str or "").split(",") if s.strip()]
    names = [s.strip() for s in (name_str or "").split(",") if s.strip()]
    targets: List[Tuple[str, str]] = []
    for emp_no in emp_nos:
        name = get_user_name(emp_no, token, lang=lang)
        targets.append((emp_no, name))
    for name in names:
        candidates = search_users_by_name(name, caller_emp_no, token, lang=lang)
        if not candidates:
            return None, {
                "success": False,
                "message": f"未找到姓名「{name}」对应用户，请使用工号参数指定。",
                "search_name": name,
            }
        if len(candidates) > 1:
            return None, {
                "success": False,
                "need_choice": True,
                "message": f"姓名「{name}」存在多名用户，请使用工号参数指定。",
                "search_name": name,
                "candidates": candidates,
                "page_link": f"https://i.zte.com.cn/#/shared/{space_id}/wiki/page/{content_id}/view"
                if space_id and content_id
                else "",
            }
        targets.append((candidates[0]["employeeNo"], candidates[0]["employeeName"]))
    return targets, None


def _is_permission_error(result: dict) -> bool:
    """根据接口返回判断是否为无权限类错误。"""
    code_info = result.get("code") or {}
    if isinstance(code_info, dict):
        code = str(code_info.get("code", "")).strip()
        msg = str(code_info.get("message") or code_info.get("desc") or "").strip()
    else:
        code, msg = "", ""
    top_msg = str(result.get("message", "")).strip()
    text = f"{code} {msg} {top_msg}".lower()
    if code in ("403", "0403", "040003"):
        return True
    if "权限" in text or "无权限" in text or "permission" in text or "forbidden" in text:
        return True
    return False


def forward_page_to_users(
    space_id: str,
    content_id: str,
    caller_emp_no: str,
    token: str,
    targets: List[Tuple[str, str]],
    lang: str = "zh_CN",
) -> Tuple[bool, str, Optional[str]]:
    """
    将页面转发到消息（支持多人）。caller 为当前操作人（鉴权），targets 为接收人列表 [(emp_no, name), ...]。
    Returns:
        (是否成功, 页面链接, 失败原因；成功时第三项为 None)
    """
    if not targets:
        return False, "", "接收人列表为空"
    api_url = build_icenter_api_url(
        f"/zte-rd-icenter-contents/content/{content_id}/msgForward/"
    )
    headers = {
        "Accept": "application/json, text/plain",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "X-Auth-Value": token,
        "X-Emp-No": caller_emp_no,
        "X-Lang-Id": lang,
        "X-Tenant-Id": "ZTE",
        "Content-Type": "application/json",
    }
    params = {
        "t": int(datetime.now().timestamp() * 1000),
        "spaceId": space_id,
        "environmentType": "browser",
        "system": "space-1920485495",
    }
    link_pc = f"https://i.zte.com.cn/index/ispace/#/shared/{space_id}/wiki/page/{content_id}/view"
    link_mobile = build_icenter_api_url(
        f"/zte-rd-ispace-mobile/#/wiki/page/{content_id}/view?fromType=MOA&spaceId={space_id}"
    )
    employees = [{"employeeNo": e, "employeeName": n} for e, n in targets]
    body = {
        "employees": employees,
        "groups": [],
        "forwardOverfolow": False,
        "maxNum": 1000,
        "link": {"linkPC": link_pc, "linkMobile": link_mobile},
        "groupKeyList": [],
        "spaceId": space_id,
    }
    try:
        response = requests.post(
            api_url, headers=headers, params=params, json=body, timeout=30
        )
        response.raise_for_status()
        result = response.json()
        code_info = result.get("code", {})
        if code_info.get("code") == "0000":
            return True, link_pc, None
        fail_reason = "没有页面转发权限" if _is_permission_error(result) else None
        return False, link_pc, fail_reason
    except requests.exceptions.RequestException as e:
        print(f"转发消息请求失败: {e}", file=sys.stderr)
        return False, link_pc, None
    except Exception as e:
        print(f"转发消息处理失败: {e}", file=sys.stderr)
        return False, link_pc, None


def get_page_title(
    space_id: str, content_id: str, caller_emp_no: str, token: str, lang: str = "zh_CN"
) -> str:
    """
    从页面详情接口获取页面标题，用于转发到邮件时的邮件主题。
    """
    if not space_id or not content_id or not token:
        return ""
    url = f"{CONTENT_DETAIL_BASE}/{content_id}"
    headers = {
        "Accept": "application/json, text/plain",
        "X-Auth-Value": token,
        "X-Emp-No": caller_emp_no,
        "X-Lang-Id": lang,
        "X-Tenant-Id": "ZTE",
        "Content-Type": "application/json",
    }
    params = {"spaceId": space_id}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            return (data.get("title") or data.get("bo", {}).get("title") or "").strip()
    except Exception as e:
        print(f"[WARN] 获取页面标题失败: {e}", file=sys.stderr)
    return ""


def _polish_api_error(result: dict) -> str:
    """将接口返回的非 0000 错误信息润色为可读提示。"""
    code_info = result.get("code") or {}
    if isinstance(code_info, dict):
        code = str(code_info.get("code", "")).strip()
        msg = str(code_info.get("message") or code_info.get("desc") or "").strip()
    else:
        code, msg = "", ""
    top_msg = str(result.get("message", "")).strip()
    if msg:
        return msg
    if top_msg:
        return top_msg
    if code and code != "0000":
        return f"接口返回错误（code: {code}），请稍后重试或联系管理员。"
    return "操作失败，请稍后重试。"


def forward_page_to_email(
    space_id: str,
    content_id: str,
    caller_emp_no: str,
    token: str,
    receivers: Optional[List[Tuple[str, str]]] = None,
    cc_receivers: Optional[List[Tuple[str, str]]] = None,
    bcc_receivers: Optional[List[Tuple[str, str]]] = None,
    postscript: str = "",
    email_title: Optional[str] = None,
    lang: str = "zh_CN",
) -> Tuple[bool, str, Optional[str]]:
    """
    将页面转发到邮件。receivers/cc_receivers/bcc_receivers 至少一个非空即可。
    email_title 为空时使用默认：Co-Claw转发自空间页面"页面标题"。
    Returns: (是否成功, jumpUrl, 失败原因；成功时第三项为 None)
    """
    if not receivers and not cc_receivers and not bcc_receivers:
        return False, "", "收件人、抄送人、密送人不能全部为空"
    jump_url = f"https://i.zte.com.cn/#/shared/{space_id}/wiki/page/{content_id}/view"
    page_title = (
        get_page_title(space_id, content_id, caller_emp_no, token, lang)
        if not email_title
        else ""
    )
    title = (email_title or "").strip() or f'Co-Claw转发自空间页面"{page_title or "未命名页面"}"'

    api_url = f"{SHARE_CONTENT_BASE}/{content_id}/shareContent/"
    headers = {
        "Accept": "application/json, text/plain",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://i.zte.com.cn",
        "X-Auth-Value": token,
        "X-Emp-No": caller_emp_no,
        "X-Lang-Id": lang,
        "X-Tenant-Id": "ZTE",
    }
    params = {
        "t": int(datetime.now().timestamp() * 1000),
        "spaceId": space_id,
        "environmentType": "browser",
        "system": "space-1920484534",
    }
    body = {
        "receivers": [
            {"employeeNo": e, "employeeName": n} for e, n in (receivers or [])
        ],
        "ccReceivers": [
            {"employeeNo": e, "employeeName": n} for e, n in (cc_receivers or [])
        ],
        "bccReceivers": [
            {"employeeNo": e, "employeeName": n} for e, n in (bcc_receivers or [])
        ],
        "postscript": (postscript or "").strip(),
        "emailTitle": title,
        "iframeHeight": "800px",
        "jumpUrl": jump_url,
        "groupKeyList": [],
        "spaceId": space_id,
    }
    try:
        response = requests.post(
            api_url, headers=headers, params=params, json=body, timeout=30
        )
        response.raise_for_status()
        result = response.json()
        code_info = result.get("code") or {}
        code = code_info.get("code", "") if isinstance(code_info, dict) else ""
        if code == "0000":
            return True, jump_url, None
        return False, jump_url, _polish_api_error(result)
    except requests.exceptions.RequestException as e:
        print(f"转发邮件请求失败: {e}", file=sys.stderr)
        return False, jump_url, str(e)
    except Exception as e:
        print(f"转发邮件处理失败: {e}", file=sys.stderr)
        return False, jump_url, None


def main():
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(
        description="将空间页面或页面链接转发到 iCenter 消息或邮件（默认消息；--to-email 时转发到邮件，需至少指定一名收件人）",
    )
    parser.add_argument(
        "--url",
        dest="url",
        required=True,
        help="空间页面完整链接",
    )
    parser.add_argument(
        "--emp-no",
        dest="emp_no",
        default=get_user_and_token(strict=False)[0],
        help="当前用户工号（鉴权用，默认从环境变量 coclaw_empno 读取）",
    )
    parser.add_argument(
        "--to-emp-no",
        dest="to_emp_no",
        default=None,
        help="接收人工号，多个用逗号分隔，如 0668000238,0668000571；与 --to-name 可同时使用；不传且无 --to-name 则转发给本人",
    )
    parser.add_argument(
        "--to-name",
        dest="to_name",
        default=None,
        help="接收人姓名，多个用逗号分隔；若某姓名存在重名会返回候选列表，需用 --to-emp-no 指定该人",
    )
    parser.add_argument(
        "--to-email",
        dest="to_email",
        action="store_true",
        help="转发到邮件（默认转发到消息）；启用时需至少指定一名收件人、抄送人或密送人（--to-emp-no/--to-name、--cc-emp-no/--cc-name 或 --bcc-emp-no/--bcc-name）",
    )
    parser.add_argument(
        "--cc-emp-no",
        dest="cc_emp_no",
        default=None,
        help="转发到邮件时的抄送人工号，多个用逗号分隔",
    )
    parser.add_argument(
        "--cc-name",
        dest="cc_name",
        default=None,
        help="转发到邮件时的抄送人姓名，多个用逗号分隔",
    )
    parser.add_argument(
        "--bcc-emp-no",
        dest="bcc_emp_no",
        default=None,
        help="转发到邮件时的密送人工号，多个用逗号分隔",
    )
    parser.add_argument(
        "--bcc-name",
        dest="bcc_name",
        default=None,
        help="转发到邮件时的密送人姓名，多个用逗号分隔",
    )
    parser.add_argument(
        "--postscript",
        dest="postscript",
        default="",
        help="转发到邮件时的附言内容；若未提供且未传 --no-postscript，将返回提示询问用户是否添加附言",
    )
    parser.add_argument(
        "--no-postscript",
        dest="no_postscript",
        action="store_true",
        help="转发到邮件时明确不添加附言（用户确认后使用，跳过附言询问）",
    )
    parser.add_argument(
        "--email-title",
        dest="email_title",
        default=None,
        help="转发到邮件时的邮件主题，不传则使用默认：Co-Claw转发自空间页面“页面标题”",
    )
    parser.add_argument(
        "--lang",
        dest="lang",
        default="zh_CN",
        help="语言，默认 zh_CN",
    )
    args = parser.parse_args()

    caller_emp_no = (args.emp_no or "").strip()
    _, token = get_user_and_token(strict=False)
    token = (token or "").strip()
    if not caller_emp_no or not token:
        result = {
            "success": False,
            "message": "缺少用户身份：请提供 --emp-no 或设置环境变量 coclaw_empno；token 必须通过环境变量 coclaw_token 设置",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(1)

    parsed = parse_page_link(args.url)
    if not parsed:
        result = {
            "success": False,
            "message": "无法从链接中解析空间ID和页面ID，请确认链接格式为："
            "https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view 或 "
            "https://i.zte.com.cn/index/ispace/#/space/{spaceId}/wiki/page/{pageId}/view",
            "url": args.url,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(1)

    space_id, content_id = parsed

    # ---------- 转发到邮件 ----------
    if args.to_email:
        # 解析收件人、抄送人、密送人（允许全部为空，后续统一校验）
        receivers, err = resolve_recipients(
            args.to_emp_no,
            args.to_name,
            caller_emp_no,
            token,
            args.lang,
            space_id,
            content_id,
        )
        if err:
            print(json.dumps(err, ensure_ascii=False, indent=2))
            sys.exit(1)
        cc, err_cc = resolve_recipients(
            args.cc_emp_no,
            args.cc_name,
            caller_emp_no,
            token,
            args.lang,
            space_id,
            content_id,
        )
        if err_cc:
            print(json.dumps(err_cc, ensure_ascii=False, indent=2))
            sys.exit(1)
        bcc, err_bcc = resolve_recipients(
            args.bcc_emp_no,
            args.bcc_name,
            caller_emp_no,
            token,
            args.lang,
            space_id,
            content_id,
        )
        if err_bcc:
            print(json.dumps(err_bcc, ensure_ascii=False, indent=2))
            sys.exit(1)

        # 校验：receivers/cc/bcc 至少有一个非空
        if not receivers and not cc and not bcc:
            result = {
                "success": False,
                "message": "转发到邮件需至少指定一名收件人、抄送人或密送人，请使用 --to-emp-no/--to-name、--cc-emp-no/--cc-name 或 --bcc-emp-no/--bcc-name。",
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            sys.exit(1)
        # 未提供附言且未明确「不添加附言」时，先询问用户是否添加附言
        postscript = (args.postscript or "").strip()
        if not postscript and not args.no_postscript:
            result = {
                "success": False,
                "need_postscript_prompt": True,
                "message": '是否添加邮件附言？若需要请提供附言内容后再次调用（带上 --postscript "附言内容"）；若不需要请再次调用并加上 --no-postscript。',
                "page_link": f"https://i.zte.com.cn/#/shared/{space_id}/wiki/page/{content_id}/view",
                "space_id": space_id,
                "content_id": content_id,
            }
            # 只在有对应收件人时才添加到结果中
            if receivers:
                result["to_recipients"] = [
                    {"employeeNo": e, "employeeName": n} for e, n in receivers
                ]
            if cc:
                result["cc_recipients"] = [
                    {"employeeNo": e, "employeeName": n} for e, n in cc
                ]
            if bcc:
                result["bcc_recipients"] = [
                    {"employeeNo": e, "employeeName": n} for e, n in bcc
                ]
            print(json.dumps(result, ensure_ascii=False, indent=2))
            sys.exit(0)
        success, jump_url, fail_reason = forward_page_to_email(
            space_id,
            content_id,
            caller_emp_no,
            token,
            receivers or [],
            cc or [],
            bcc or [],
            postscript,
            args.email_title,
            args.lang,
        )
        msg = "已成功将页面转发到邮件" if success else (fail_reason or "转发到邮件失败，请检查权限或链接是否有效。")
        result = {
            "success": success,
            "message": msg,
            "page_link": jump_url,
            "space_id": space_id,
            "content_id": content_id,
        }
        # 只在有对应收件人时才添加到结果中
        if receivers:
            result["to_recipients"] = [
                {"employeeNo": e, "employeeName": n} for e, n in receivers
            ]
        if cc:
            result["cc_recipients"] = [
                {"employeeNo": e, "employeeName": n} for e, n in cc
            ]
        if bcc:
            result["bcc_recipients"] = [
                {"employeeNo": e, "employeeName": n} for e, n in bcc
            ]
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if success else 1)

    # ---------- 转发到消息 ----------
    targets, err = resolve_recipients(
        args.to_emp_no,
        args.to_name,
        caller_emp_no,
        token,
        args.lang,
        space_id,
        content_id,
    )
    if err:
        print(json.dumps(err, ensure_ascii=False, indent=2))
        sys.exit(1)
    unique_targets: List[Tuple[str, str]] = list(targets) if targets else []
    if not unique_targets:
        self_name = get_user_name(caller_emp_no, token, lang=args.lang)
        unique_targets = [(caller_emp_no, self_name)]
    else:
        seen = set()
        deduped: List[Tuple[str, str]] = []
        for e, n in unique_targets:
            if e not in seen:
                seen.add(e)
                deduped.append((e, n))
        unique_targets = deduped

    success, page_link, fail_reason = forward_page_to_users(
        space_id,
        content_id,
        caller_emp_no,
        token,
        unique_targets,
        lang=args.lang,
    )

    if success:
        msg = "已成功将页面转发到消息"
    elif fail_reason:
        msg = fail_reason
    else:
        msg = "转发到消息失败，请检查权限或链接是否有效"

    result = {
        "success": success,
        "message": msg,
        "page_link": page_link,
        "space_id": space_id,
        "content_id": content_id,
        "to_recipients": [
            {"employeeNo": e, "employeeName": n} for e, n in unique_targets
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if success else 1)


# =============================================================================
# 跨平台支持 - 平台检测
# =============================================================================


def detect_platform() -> Tuple[str, str, bool]:
    """检测当前操作系统平台。"""
    system = platform.system()
    release = platform.release()
    is_windows = system == "Windows"
    return system, release, is_windows


def get_log_dir() -> str:
    """获取日志目录，支持跨平台运行。"""
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


SYSTEM, SYSTEM_RELEASE, IS_WINDOWS = detect_platform()


if __name__ == "__main__":
    main()
