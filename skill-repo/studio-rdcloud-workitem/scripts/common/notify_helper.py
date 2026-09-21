#!/usr/bin/env python
"""
通知统一管理：邮件、iCenter 消息发送。接收人解析（@me、姓名→工号、多匹配取第一）及正文处理（查询结果/Markdown→HTML 黑边框表格）在本模块实现。
各脚本只调用本模块的 send_mail、send_icenter_message，不重复实现 _send_mail_inline 等逻辑。
"""

import importlib.util
import json
import re
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import requests

from . import rdcloud_api
from .log_helper import get_logger

logger = get_logger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
USER_SEARCH_MODULE = SCRIPT_DIR / "user_search.py"


def _truncate_resp(text: str, max_len: int = 500) -> str:
    """截断响应文本用于日志记录"""
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return f"{text[:max_len]}... (总长度: {len(text)})"


def _import_user_search_module():
    """动态导入 user_search 模块"""
    if USER_SEARCH_MODULE.exists():
        spec = importlib.util.spec_from_file_location("user_search", USER_SEARCH_MODULE)
        user_search_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(user_search_module)
        return user_search_module
    return None


def search_user_by_name(
    user_name: str,
    auth_token: Optional[str] = None,
    emp_no: Optional[str] = None,
) -> Dict[str, Any]:
    """根据用户名称查询用户工号。多匹配时取第一个人的工号。
    :return: {"status": "success", "empNo": "...", "userName": "..."} 或 {"status": "error", "message": "..."}
    """
    auth_token = (auth_token or "").strip()
    emp_no = (emp_no or "").strip()
    if not auth_token or not emp_no:
        return {
            "status": "error",
            "message": "缺少认证信息: auth_token 和 emp_no (请通过 headers 提供 X-Auth-Value、X-Emp-No)",
        }
    try:
        logger.info("查询用户: %s", user_name)
        user_search_module = _import_user_search_module()
        if not user_search_module or not hasattr(user_search_module, "search_users"):
            return {"status": "error", "message": "user_search 模块不可用"}
        result = user_search_module.search_users(emp_no, auth_token, user_name)
        if not result or not isinstance(result, dict) or result.get("status") != "success":
            msg = result.get("message", "查询用户失败") if isinstance(result, dict) else "查询用户失败"
            return {"status": "error", "message": f"{msg}: {user_name}"}
        users_list = result.get("users", [])
        if not users_list:
            return {"status": "error", "message": f"未找到用户: {user_name}"}
        first = users_list[0]
        user_emp_no = (first.get("empNo") or "").strip()
        if not user_emp_no:
            return {"status": "error", "message": f"用户 {user_name} 没有工号信息"}
        if len(users_list) > 1:
            logger.warning("找到 %d 个匹配的用户，默认使用第一个人的工号: %s", len(users_list), user_emp_no)
        else:
            logger.info("找到唯一用户: %s -> %s", user_name, user_emp_no)
        return {
            "status": "success",
            "empNo": user_emp_no,
            "userName": first.get("name", user_name),
        }
    except Exception as e:
        logger.error("查询用户失败: %s", e, exc_info=True)
        return {"status": "error", "message": str(e)}


def process_recipients(
    mail_to: str,
    auth_token: Optional[str] = None,
    emp_no: Optional[str] = None,
) -> str:
    """处理收件人列表：@me/工号/中文名→工号，多收件人逗号分隔；中文名多匹配时取第一个。返回逗号分隔的工号字符串。"""
    if not mail_to or not isinstance(mail_to, str):
        return ""
    recipients = [r.strip() for r in mail_to.split(",") if r.strip()]
    processed = []
    token = (auth_token or "").strip()
    emp = (emp_no or "").strip()
    for r in recipients:
        if r.lower() in ("@me", "me") and emp:
            processed.append(emp)
            logger.info("收件人 @Me/me 已转换为当前用户工号: %s", emp)
            continue
        if r.isdigit() and len(r) <= 10:
            processed.append(r)
            logger.info("收件人为工号: %s", r)
            continue
        logger.info("尝试查询用户: %s", r)
        user_result = search_user_by_name(r, token, emp)
        if user_result.get("status") == "success":
            processed.append(user_result.get("empNo", ""))
            logger.info("用户查询成功: %s -> %s", r, user_result.get("empNo"))
        else:
            msg = user_result.get("message", f"未找到用户: {r}")
            logger.error("未找到用户工号: %s，错误信息: %s", r, msg)
            raise ValueError(f"未找到用户: {r}。{msg}")
    return ",".join(processed)


def is_work_item_query_result(content: str) -> bool:
    """检测内容是否是查询工作项的结果列表"""
    if not content or not isinstance(content, str):
        return False
    work_item_indicators = [
        "# 工作项查询结果",
        "工作项查询结果",
        "总计：",
        "当前页：",
        "| 标题 | 标识 | 工作项类型 |",
        "| 标题 | 标识 |",
    ]
    indicator_count = sum(1 for ind in work_item_indicators if ind in content)
    if indicator_count >= 2:
        logger.info("检测到 %d 个查询工作项结果特征，判断为查询工作项结果", indicator_count)
        return True
    work_item_id_pattern = r"\b[A-Za-z0-9]+-\d+\b"
    matches = re.findall(work_item_id_pattern, content)
    if len(matches) >= 1:
        logger.info("检测到 %d 个工作项ID，判断为查询工作项结果", len(matches))
        return True
    if "|" in content and content.count("|") >= 6:
        work_item_columns = ["标题", "标识", "工作项类型", "更新时间", "状态", "指派给"]
        column_count = sum(1 for col in work_item_columns if col in content)
        if column_count >= 2:
            logger.info("检测到表格结构且包含 %d 个工作项相关列，判断为查询工作项结果", column_count)
            return True
    if ("# 工作项查询结果" in content or "工作项查询结果" in content) and len(matches) >= 1:
        logger.info("检测到工作项查询结果标题且包含工作项ID，判断为查询工作项结果")
        return True
    return False


def is_markdown_format(content: str) -> bool:
    """检测内容是否是 Markdown 格式"""
    if not content or not isinstance(content, str):
        return False
    markdown_indicators = ["|", "#", "*", "-", "`", "[", "]"]
    if "|" in content and content.count("|") >= 2:
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if "|" in line and i < len(lines) - 1:
                next_line = (lines[i + 1] or "").strip()
                if next_line and ("---" in next_line or ":---" in next_line or "---:" in next_line):
                    logger.info("检测到 Markdown 表格格式")
                    return True
    if sum(1 for ind in markdown_indicators if ind in content) >= 2:
        logger.info("检测到多个 Markdown 特征，判断为 Markdown 格式")
        return True
    for line in (content.split("\n") or [])[:10]:
        if (line or "").strip().startswith("#"):
            logger.info("检测到 Markdown 标题格式")
            return True
    return False


def add_table_borders(html: str) -> str:
    """为 HTML 中的表格添加黑色边框"""
    if not html:
        return html
    border_style = "border: 1px solid #000;"

    def replace_table_tag(match):
        tag = match.group(0)
        if "style=" in tag:
            style_m = re.search(r'style=["\']([^"\']*)["\']', tag)
            if style_m and "border" not in style_m.group(1):
                return re.sub(r'style=["\'][^"\']*["\']', f'style="{style_m.group(1)}; {border_style}"', tag)
        return tag.replace("<table", f'<table style="{border_style}"')

    def replace_th_tag(match):
        tag = match.group(0)
        if "style=" in tag:
            style_m = re.search(r'style=["\']([^"\']*)["\']', tag)
            if style_m and "border" not in style_m.group(1):
                return re.sub(r'style=["\'][^"\']*["\']', f'style="{style_m.group(1)}; {border_style}"', tag)
        return tag.replace("<th", f'<th style="{border_style}"')

    def replace_td_tag(match):
        tag = match.group(0)
        if "style=" in tag:
            style_m = re.search(r'style=["\']([^"\']*)["\']', tag)
            if style_m and "border" not in style_m.group(1):
                return re.sub(r'style=["\'][^"\']*["\']', f'style="{style_m.group(1)}; {border_style}"', tag)
        return tag.replace("<td", f'<td style="{border_style}"')

    html = re.sub(r"<table[^>]*>", replace_table_tag, html, flags=re.IGNORECASE)
    html = re.sub(r"<th[^>]*>", replace_th_tag, html, flags=re.IGNORECASE)
    html = re.sub(r"<td[^>]*>", replace_td_tag, html, flags=re.IGNORECASE)
    logger.info("已为表格添加黑色边框")
    return html


def markdown_to_html(md: str, emp_no: str, token: str) -> Optional[str]:
    """将 Markdown 转为 HTML，并为表格添加黑色边框。失败返回 None。"""
    if not md:
        return None
    req_headers = {
        "X-Auth-Value": token,
        "X-Emp-No": emp_no,
        "Content-Type": "application/json;charset=UTF-8",
    }
    body = {"content": md}
    logger.info("调用 markdown2html 接口: %s", rdcloud_api.URL_MARKDOWN2HTML)
    logger.info("请求参数: %s", _truncate_resp(json.dumps(body, ensure_ascii=False), 500))
    try:
        r = requests.post(
            rdcloud_api.URL_MARKDOWN2HTML, json=body, headers=req_headers, timeout=30
        )
        r.raise_for_status()
        logger.info("markdown2html 响应: %s", _truncate_resp(r.text, 100))
        j = r.json()
        if isinstance(j.get("code"), dict) and j["code"].get("code") == "0000" and "bo" in j:
            html_content = j.get("bo") or ""
            logger.info("Markdown 转 HTML 成功，HTML 长度: %d 字符", len(html_content))
            return add_table_borders(html_content)
        logger.warning("Markdown 转 HTML 失败，响应: %s", json.dumps(j, ensure_ascii=False))
        return None
    except Exception as e:
        logger.error("Markdown 转 HTML 异常: %s", e, exc_info=True)
        return None


def _user_emp_no_for_send(recipient: str, headers: Dict[str, str]) -> Optional[str]:
    """解析单个接收人（@me/工号/姓名）为工号。失败返回 None。"""
    if not recipient or not isinstance(recipient, str):
        return None
    r = (recipient or "").strip()
    if r.lower() in ("@me", "me"):
        return (headers.get("X-Emp-No") or "").strip() or None
    if r.isdigit() and len(r) <= 10:
        return r
    from .auth_helper import resolve_person_to_emp_no
    out = resolve_person_to_emp_no(r, headers, log=None)
    return out if (out and out != r) else None


def _search_group_chat(group_name: str, headers: Dict[str, str]) -> Optional[str]:
    """按群聊名称查询 employeeNo。"""
    token = (headers.get("X-Auth-Value") or "").strip()
    emp_no = (headers.get("X-Emp-No") or "").strip()
    if not token or not emp_no:
        return None
    url = (
        f"{rdcloud_api.URL_GROUP_CHAT_SEARCH}"
        f"?keyword={urllib.parse.quote(group_name)}&searchFor=groupChat"
    )
    req_headers = {
        "X-Auth-Value": token,
        "X-Emp-No": emp_no,
        "X-Lang-Id": "zh_CN",
        "X-Tenant-Id": headers.get("X-Tenant-Id", "ZTE"),
    }
    try:
        r = requests.get(url, headers=req_headers, timeout=30)
        if r.status_code != 200:
            return None
        bo = r.json().get("bo", [])
        if not bo:
            return None
        return (bo[0].get("employeeNo") or "").strip() or None
    except Exception:
        return None


def process_recipients_mail(mail_to: str, headers: Dict[str, str]) -> str:
    """
    将 mail_to 字符串（逗号分隔的 @me/工号/姓名）解析为逗号分隔的工号字符串。
    优先使用 user_search 按姓名查工号（多匹配取第一个），无认证时回退到 auth_helper。
    :raises ValueError: 某接收人无法解析时
    """
    if not mail_to or not isinstance(mail_to, str):
        return ""
    token = (headers.get("X-Auth-Value") or "").strip()
    emp_no = (headers.get("X-Emp-No") or "").strip()
    if token and emp_no:
        return process_recipients(mail_to, token, emp_no)
    parts = [p.strip() for p in mail_to.split(",") if p.strip()]
    out = []
    for r in parts:
        emp = _user_emp_no_for_send(r, headers)
        if not emp:
            raise ValueError(f"未找到用户: {r}")
        out.append(emp)
    return ",".join(out)


def process_recipients_icenter(to: Union[str, List[str]], headers: Dict[str, str]) -> Dict[str, Any]:
    """
    将 to（字符串或列表：@me/工号/姓名/群聊/sip:xxx）解析为 individuals 与 groups 列表。
    :return: {"status": "success", "individuals": [...], "groups": [...]}
    """
    to_list = [to] if isinstance(to, str) else (to if isinstance(to, list) else [])
    individuals, groups = [], []
    for recipient in to_list:
        if not isinstance(recipient, str):
            continue
        r = recipient.strip()
        if r.startswith("sip:"):
            groups.append(r)
            continue
        if r.lower() in ("@me", "me"):
            emp = (headers.get("X-Emp-No") or "").strip()
            if emp:
                individuals.append(emp)
            continue
        if r.isdigit() and len(r) <= 10:
            individuals.append(r)
            continue
        emp = _user_emp_no_for_send(r, headers)
        if emp:
            individuals.append(emp)
            continue
        grp = _search_group_chat(r, headers)
        if grp:
            groups.append(grp)
    return {"status": "success", "individuals": individuals, "groups": groups}


def send_mail(
    mail_title: str,
    mail_to: str,
    mail_body: str,
    headers: Dict[str, str],
    mail_from: Optional[str] = None,
    mail_lan: str = "zh_CN",
) -> Dict[str, Any]:
    """
    发送邮件。mail_to 为逗号分隔的 @me/工号/姓名，由 process_recipients_mail 解析为工号（中文名→工号、多匹配取第一）。
    正文若为查询工作项结果或 Markdown，会转为 HTML 并给表格加黑色边框。
    :return: {"status": "success"} 或 {"status": "error", "message": "..."}
    """
    if not headers.get("X-Auth-Value") or not headers.get("X-Emp-No"):
        return {"status": "error", "message": "缺少认证信息"}
    try:
        processed_to = process_recipients_mail(mail_to, headers)
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    processed_body = mail_body
    if isinstance(mail_body, str) and mail_body.strip():
        token = (headers.get("X-Auth-Value") or "").strip()
        emp_no = (headers.get("X-Emp-No") or "").strip()
        if is_work_item_query_result(mail_body):
            logger.info("检测到邮件内容为查询工作项结果，强制转换为 HTML 表格（带黑色边框）")
            html_content = markdown_to_html(mail_body, emp_no, token)
            if html_content:
                processed_body = html_content
            else:
                logger.warning("查询工作项结果转 HTML 失败，使用原始内容")
        elif is_markdown_format(mail_body):
            logger.info("检测到邮件内容为 Markdown 格式，转换为 HTML")
            html_content = markdown_to_html(mail_body, emp_no, token)
            if html_content:
                processed_body = html_content
            else:
                logger.warning("Markdown 转 HTML 失败，使用原始内容")
    data = {
        "mailFrom": mail_from or "rdc@zte.com.cn",
        "mailLan": mail_lan,
        "mailTitle": mail_title,
        "mailTo": processed_to,
        "mailBody": processed_body,
    }
    req_headers = {
        "Content-Type": "application/json",
        "X-Auth-Value": headers["X-Auth-Value"],
        "X-Emp-No": headers["X-Emp-No"],
        "X-Tenant-Id": headers.get("X-Tenant-Id", "ZTE"),
    }
    try:
        r = requests.post(
            rdcloud_api.URL_SEND_MAIL,
            headers=req_headers,
            data=json.dumps(data),
            timeout=60,
        )
        return {"status": "success"} if r.status_code == 200 else {"status": "error", "message": f"邮件发送失败，状态码: {r.status_code}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _send_single_icenter(
    message_content: str,
    subject: str,
    recipients: List[str],
    to_type: int,
    from_emp_no: str,
    headers: Dict[str, str],
    view_url: Optional[str] = None,
) -> Dict[str, Any]:
    """ to_type: 1=个人 2=群组 """
    data = {
        "forward": True,
        "from": from_emp_no,
        "items": [{"multiLine": "false", "title": {"en": "Id", "zh": "详情"}, "value": {"en": message_content, "zh": message_content}}],
        "subject": {"en": subject or message_content, "zh": subject or message_content},
        "summary": {},
        "to": recipients,
        "toType": to_type,
    }
    if view_url:
        data["view"] = {"customMsgType": "1", "url": view_url}
    req_headers = {
        "X-Auth-Value": headers["X-Auth-Value"],
        "X-Emp-No": headers["X-Emp-No"],
        "X-Lang-Id": "zh_CN",
        "X-Tenant-Id": headers.get("X-Tenant-Id", "ZTE"),
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(
            rdcloud_api.URL_ICHAT_SHARE,
            headers=req_headers,
            data=json.dumps(data),
            timeout=30,
        )
        return {"status": "success"} if r.status_code == 200 else {"status": "error", "message": f"状态码: {r.status_code}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def send_icenter_message(
    message_content: str,
    headers: Dict[str, str],
    subject: Optional[str] = None,
    to: Optional[Union[str, List[str]]] = None,
    view_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    发送 iCenter 消息。to 为 @me/工号/姓名/群聊，由 process_recipients_icenter 解析。
    :return: {"status": "success", "message": "..."} 或 {"status": "error", "message": "..."}
    """
    if not headers.get("X-Auth-Value") or not headers.get("X-Emp-No"):
        return {"status": "error", "message": "缺少认证信息"}
    res = process_recipients_icenter(to or [], headers)
    if res.get("status") != "success":
        return res
    individuals = res.get("individuals", [])
    groups = res.get("groups", [])
    if not individuals and not groups:
        return {"status": "error", "message": "没有有效的接收人"}
    from_emp = (headers.get("X-Emp-No") or "").strip()
    subj = subject or message_content[:50] if message_content else ""
    all_ok = True
    if individuals:
        if _send_single_icenter(message_content, subj, individuals, 1, from_emp, headers, view_url).get("status") != "success":
            all_ok = False
    if groups:
        if _send_single_icenter(message_content, subj, groups, 2, from_emp, headers, view_url).get("status") != "success":
            all_ok = False
    return {"status": "success" if all_ok else "error", "message": "iCenter 消息发送成功" if all_ok else "部分发送失败"}
