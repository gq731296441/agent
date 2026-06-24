#!/usr/bin/env python3
"""
zMail 回复与转发的基类。提供原文 HTML 构建、邮件详情获取、附件映射、正文拼接等共用逻辑。
ZmailReply 与 ZmailForward 继承此类。
"""

import copy
import json
import time
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup, NavigableString, Tag
from html_markdown import markdown_to_html

from enums import MailboxId
from util import is_history_mail_id
from zmail_base import ZmailBase


# ---------- 原文 HTML 构建（回复/转发共用）----------

def _display_name(obj: Dict[str, Any]) -> str:
    """从 from/to/cc 单条对象得到展示名。优先姓名+工号，没有时再使用邮件地址。"""
    n = (obj.get("N") or "").strip()
    uno = (obj.get("UNO") or "").strip()
    e = (obj.get("E") or "").strip()
    if n and uno:
        return f"{n}{uno}"
    if n:
        return n
    if uno:
        return uno
    return e or ""


def _format_header_html(
    from_obj: Dict[str, Any],
    to_list: List[Dict[str, Any]],
    date: str,
    title: str,
    cc_list: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """生成 zhistoryHeader HTML。"""
    from_str = _display_name(from_obj)
    to_str = "; ".join(_display_name(t) for t in (to_list or []))
    if not to_str.strip():
        to_str = ""
    cc_list = cc_list or []
    cc_str = "; ".join(_display_name(c) for c in cc_list)
    lines = [
        '<div style="padding: 8px; background-color: #F5F6F8;" class="zhistoryHeader">',
        f'    <div><strong>From:&nbsp;</strong><span class="zreadUserName">{from_str}</span>\n &nbsp;</div>',
        f'    <div><strong>To:&nbsp;</strong><span style="display: inline;" class="zreadUserName">{to_str};</span>\n &nbsp;</div>',
    ]
    if cc_str:
        lines.append(
            f'    <div><strong>Cc:&nbsp;</strong><span style="display: inline;" class="zreadUserName">{cc_str};</span>\n &nbsp;</div>'
        )
    lines.extend([
        f'    <div><strong>Date:&nbsp;</strong><span data-zmail-format-date="res:WriteMailResource.sendDateFormat" data-zmail-timezone="8" data-zmail-timezone-code="Asia/Shanghai" data-zmail-timezone-value="{date}">{date}</span>\n &nbsp;</div>',
        f'    <div><strong>Subject:&nbsp;</strong><span class="zreadTitle"><strong>{title}</strong></span>\n &nbsp;</div>',
        "</div>",
    ])
    return "\n".join(lines)


def _style_has_display_block(style_val) -> bool:
    """与 zMail 一致：历史区 div 为 display:block；兼容 display: block 等空格写法。"""
    if not style_val:
        return False
    s = style_val if isinstance(style_val, str) else str(style_val)
    compact = "".join(s.split())
    return "display:block" in compact.lower()


def _collect_nodes_before_zhistory(root: Tag, zhistory_row: Tag) -> List[Any]:
    """
    收集 root 下、位于 zhistory_row 之前的节点（含文本与 br），用于拼入新 zhistoryPanel。

    若 zhistory_row 嵌在 <body> 等包装标签内（非 zcontentRow 直接兄弟），不得整段搬走 body，
    否则会把历史区一并塞进 content_inner，导致回复正文丢失或结构错乱。
    """
    out: List[Any] = []

    def walk(container: Tag) -> None:
        for child in list(container.children):
            if child is zhistory_row:
                return
            if isinstance(child, NavigableString):
                out.append(child)
                continue
            if not isinstance(child, Tag):
                continue
            if "zcontentRow" in (child.get("class") or []):
                continue
            if zhistory_row in child.descendants:
                walk(child)
            else:
                out.append(child)

    walk(root)
    return out


def _build_reply_html(
    title: str,
    from_obj: Dict[str, Any],
    to_list: List[Dict[str, Any]],
    date: str,
    htmlcontent: str,
    cc_list: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """根据原邮件信息构建回复/转发邮件的 Original 区域 HTML。"""
    soup = BeautifulSoup(htmlcontent, "html.parser")
    zhistory_row = soup.find("div", class_="zhistoryRow", style=_style_has_display_block)
    if not zhistory_row:
        root = soup.find("div", class_="zcontentRow") or soup
        if not isinstance(root, Tag):
            return htmlcontent
        zhistory_row = soup.new_tag("div", attrs={"style": "display:block", "class": "zhistoryRow"})
        zhistory_des = soup.new_tag("div", attrs={
            "style": "width: 100%; height: 28px; line-height: 28px; background-color: #E0E5E9; color: #1388FF; text-align: center;",
            "class": "zhistoryDes",
        })
        zhistory_des.string = "Original"
        zhistory_row.append(zhistory_des)
        container = soup.new_tag("div", attrs={"id": "zwriteHistoryContainer"})
        zhistory_row.append(container)
        root.append(zhistory_row)
    else:
        container = zhistory_row.find("div", id="zwriteHistoryContainer")
        if not container:
            container = soup.new_tag("div", attrs={"id": "zwriteHistoryContainer"})
            zhistory_row.insert(0, container)

    root = soup.find("div", class_="zcontentRow") or soup
    to_move: List[Any] = []
    if isinstance(root, Tag):
        to_move = _collect_nodes_before_zhistory(root, zhistory_row)
    if not to_move and htmlcontent.strip() and isinstance(root, Tag):
        # 无 zcontentRow / 结构异常时：仅移动非 zhistoryRow 的直接子节点，避免误抽历史区
        for child in list(root.contents):
            if isinstance(child, Tag):
                if "zcontentRow" in (child.get("class") or []):
                    continue
                if child.get("class") and "zhistoryRow" in child.get("class", []):
                    continue
            child.extract()
            to_move.append(child)

    zhistory_panel = soup.new_tag("div", attrs={"class": "control-group zhistoryPanel"})
    header_html = _format_header_html(from_obj, to_list, date, title, cc_list)
    header_soup = BeautifulSoup(header_html, "html.parser")
    first_header = header_soup.find("div", class_="zhistoryHeader")
    if first_header:
        zhistory_panel.append(first_header)

    zhistory_content = soup.new_tag("div", attrs={"class": "zhistoryContent"})
    content_inner = soup.new_tag("div", attrs={"class": ""})
    for node in to_move:
        try:
            node.extract()
        except Exception:
            pass
        content_inner.append(node)
    zhistory_content.append(content_inner)
    p_br = soup.new_tag("p")
    p_br.append(soup.new_tag("br"))
    zhistory_content.append(p_br)
    zhistory_panel.append(zhistory_content)
    container.insert(0, zhistory_panel)
    return str(soup)


def build_reply_html_from_email_detail(email_detail: Dict[str, Any]) -> str:
    """使用 email_detail（含 title、mf、mt、mc、sdt、content）构建回复/转发邮件的 Original 区域 HTML。"""
    ed = email_detail or {}
    can_forward = ed.get("can_forward", True)
    if not can_forward:
        # 返回最小容器，确保后续能继续插入正文块与签名。
        return "<div></div>"
    return _build_reply_html(
        title=ed.get("title") or "",
        from_obj=ed.get("mf") or {},
        to_list=ed.get("mt") or [],
        date=ed.get("sdt") or "",
        htmlcontent=ed.get("content") or "",
        cc_list=ed.get("mc") or [],
    )


def extract_inline_attach_ids(ats_list: List[Dict[str, Any]]) -> List[str]:
    """从 ATS 列表中提取正文插图附件 ID（I=1 且 ID 非空）。"""
    return [
        str(item.get("ID") or "").strip()
        for item in (ats_list or [])
        if isinstance(item, dict) and str(item.get("I") or "") == "1" and str(item.get("ID") or "").strip()
    ]


# ---------- ZmailReplyForwardBase ----------

_CONTENT_PLACEHOLDER = "{{CONTENT}}"
_CONTENT_BLOCK_HTML = (
    '<div style="font-size:14px;font-family:微软雅黑,Microsoft YaHei;line-height:1.5"><br></div>\n'
    '    <div style="font-size:14px;font-family:微软雅黑,Microsoft YaHei;line-height:1.5">'
    + _CONTENT_PLACEHOLDER
    + "</div>\n"
    '    <div style="font-size:14px;font-family:微软雅黑,Microsoft YaHei;line-height:1.5"><br></div>\n'
    '    <div style="font-size:14px;font-family:微软雅黑,Microsoft YaHei;line-height:1.5"><br></div>\n'
    '    <div style="font-size:14px;font-family:微软雅黑,Microsoft YaHei;line-height:1.5"><br></div>'
)


class ZmailReplyForwardBase(ZmailBase):
    """回复与转发的基类，提供邮件详情获取、原文 HTML 构建、附件映射、正文拼接等共用方法。"""

    def _to_json_object(self, value):
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return value
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None

    def _sdt_to_date(self, sdt) -> str:
        if sdt is None:
            return ""
        if isinstance(sdt, (int, float)):
            try:
                return time.strftime("%Y-%m-%d %H:%M", time.localtime(sdt / 1000.0 if sdt > 1e12 else sdt))
            except (ValueError, OSError):
                return ""
        if isinstance(sdt, str):
            sdt = sdt.strip()
            if not sdt:
                return ""
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    t = time.strptime(sdt[:19], fmt)
                    return time.strftime("%Y-%m-%d %H:%M", t)
                except ValueError:
                    continue
            return sdt[:10] if len(sdt) >= 10 else sdt
        return ""

    def _get_email_content(self, email_id: str) -> dict:
        """获取原邮件详情。"""
        empty = {
            "content": "",
            "title": "",
            "mf": {},
            "mc": None,
            "mt": [],
            "mpt": "",
            "att_encrypt": "",
            "sdt": "",
            "ats": None,
            "can_forward": True,
        }
        try:
            if is_history_mail_id(email_id):
                d = self._request.get_history_mail_context(email_id)
            else:
                d = self._request.get_mail_context(email_id)
        except Exception:
            return empty
        html_content = d.get("MCT") or ""
        title = d.get("TI") or d.get("LPN") or ""
        mf = self._to_json_object(d.get("MF"))
        if mf is None or not isinstance(mf, dict):
            mf = {}
        mc = self._to_json_object(d.get("MC"))
        if mc is None:
            mc = []
        elif not isinstance(mc, list):
            mc = [mc] if mc else []
        mt = self._to_json_object(d.get("MT"))
        if mt is None:
            mt = []
        elif not isinstance(mt, list):
            mt = [mt] if mt else []
        bid = str(d.get("BID") or "").strip()
        # BID=MailboxId.OUTBOX 表示发件箱，发件箱邮件默认都允许转发；其余箱体再按 IL 判断。
        can_forward = True if bid == MailboxId.OUTBOX.value else (str(d.get("IL") or "0") == "0")
        return {
            "content": html_content,
            "title": title,
            "mf": mf,
            "mc": mc,
            "mt": mt,
            "mpt": d.get("MPT") or "",
            "att_encrypt": d.get("AE") or "",
            "sdt": self._sdt_to_date(d.get("SDT")),
            "ats": d.get("ATS"),
            "can_forward": can_forward,
        }

    def _get_signature(self) -> str:
        return self._request.query_signature() or ""

    def _build_ats_for_new_mail(
        self,
        new_mail_id: str,
        ats_raw,
        with_attachments: bool = True,
    ) -> str:
        """将原邮件附件映射到新邮件 ATS。"""
        ats_list = self._to_json_object(ats_raw) if isinstance(ats_raw, str) else (
            ats_raw if isinstance(ats_raw, list) else []
        )
        if not ats_list:
            return "[]"
        new_ats = []
        new_idx = 1  # 跳过附件时仍保持新附件 ID 连续
        for i, item in enumerate(ats_list):
            if not isinstance(item, dict):
                continue
            is_inline = str(item.get("I") or item.get("i") or "") == "1"
            if not is_inline and not with_attachments:
                # with_attachments=False：只保留正文插图（I=1）
                continue
            new_item = copy.deepcopy(item)
            new_item["ID"] = new_mail_id + f"{new_idx:05d}"
            new_idx += 1
            new_item["RID"] = item.get("RID") or item.get("ID") or ""
            new_ats.append(new_item)
        return json.dumps(new_ats, ensure_ascii=False)

    def _build_histroy_mail_ats_for_new_mail(
        self,
        new_mail_id: str,
        ats_raw,
        original_mail_id: str,
        with_attachments: bool = True,
    ) -> str:
        """
        构建“历史邮件”转挂到新邮件后的 ATS。

        注意：
        - 构建时强制将 RID 置空；
        - 每个附件都必须调用 upload_history_mail_attach_to_zmail 上传。
        """

        ats_list = self._to_json_object(ats_raw) if isinstance(ats_raw, str) else (
            ats_raw if isinstance(ats_raw, list) else []
        )
        if not ats_list:
            return "[]"
        if not (original_mail_id or "").strip():
            return "[]"

        new_ats = []
        for idx, item in enumerate(ats_list, start=1):
            if not isinstance(item, dict):
                continue
            old_attach_id = str(item.get("ID") or item.get("id") or "").strip()
            if not old_attach_id:
                continue

            # 历史邮件转挂接口：新附件 ID 拼接按 idx 04 位生成（与转存附件逻辑保持一致）。
            new_mail_attach_id = new_mail_id + f"{idx:04d}"
            is_inline = str(item.get("I") or item.get("i") or "") == "1"
            if not is_inline and not with_attachments:
                # 仅在“允许转存附件”为否时跳过非正文插图附件；正文插图附件始终保留。
                continue
            # 网络/服务抖动可能导致转挂失败：捕获通讯错误并重试一次。
            try:
                ret = self._request.upload_history_mail_attach_to_zmail(
                    item, new_mail_attach_id, original_mail_id
                )
            except Exception:
                # 重试前做一点点让服务恢复（避免立刻重复同一瞬时失败）。
                time.sleep(0.3)
                try:
                    ret = self._request.upload_history_mail_attach_to_zmail(
                        item, new_mail_attach_id, original_mail_id
                    )
                except Exception:
                    # 重试仍失败：跳过该附件转挂处理，继续处理下一个附件。
                    continue

            if not isinstance(ret, dict):
                continue
            new_attach_id = str(ret.get("new_attach_id") or new_mail_attach_id).strip()
            attach_attr = ret.get("attach_attr") or {}

            # 以原附件为底，叠加上传返回的属性；RID 强制置空。
            new_item = copy.deepcopy(item)
            if isinstance(attach_attr, dict):
                for k, v in attach_attr.items():
                    if k in ("ID", "RID"):
                        continue
                    new_item[k] = v

            new_item["ID"] = new_attach_id
            new_item["RID"] = ""
            new_ats.append(new_item)

        return json.dumps(new_ats, ensure_ascii=False)

    def _prepare_content_with_original(self, email_detail: dict, user_content: str) -> str:
        """在原文 HTML 顶部插入用户内容块 + 签名，回复与转发共用。"""
        reply_html = build_reply_html_from_email_detail(email_detail)
        if not reply_html or not reply_html.strip():
            content_html = markdown_to_html(user_content or "")
            return content_html if content_html else (user_content or "")
        soup = BeautifulSoup(reply_html, "html.parser")
        root = soup.find()
        if root is None:
            content_html = markdown_to_html(user_content or "")
            return content_html if content_html else (user_content or "")
        content_html = markdown_to_html(user_content or "")
        block_html = _CONTENT_BLOCK_HTML.replace(_CONTENT_PLACEHOLDER, content_html)
        block_soup = BeautifulSoup(block_html, "html.parser")
        block_nodes = list(block_soup.contents)
        for node in reversed(block_nodes):
            root.insert(0, node)
        signature_html = self._get_signature()
        if signature_html and signature_html.strip():
            signature_soup = BeautifulSoup(signature_html, "html.parser")
            signature_nodes = list(signature_soup.contents)
            for node in reversed(signature_nodes):
                root.insert(len(block_nodes), node)
        return str(soup)
