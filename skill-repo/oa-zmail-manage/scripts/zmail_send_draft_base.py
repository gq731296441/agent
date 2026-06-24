#!/usr/bin/env python3
"""
zMail 发送基类：收件人格式化与解析、正文构建、新邮件 D 构建、公共准备流程。
ZmailSend 和 ZmailDraft 继承此类。
"""

import json
from typing import List, Optional

from html_markdown import markdown_to_html
from util import is_email_address
from zmail_base import ZmailBase


class ZmailSendDraftBase(ZmailBase):
    """发送基类：收件人解析、正文构建、新邮件准备流程。"""

    # ---------- 正文构建 ----------

    def _build_send_content(
        self,
        content: str,
        inline_attach_ids: Optional[List[str]] = None,
    ) -> str:
        """
        正文 Markdown 转 HTML，并拼接签名。
        inline_attach_ids：可选。若传入且非空，则在正文 HTML 后追加正文插图（img，src 为 cid:附件ID，与 br）；
        不传或为 None/空列表时，正文与原先完全一致。
        """
        line_style = "font-size:14px;font-family:微软雅黑,Microsoft YaHei;line-height:1.5"
        body_html = markdown_to_html(content or "")
        content_div = f'<div style="{line_style}">{body_html or ""}</div>'
        if inline_attach_ids:
            extra_parts = []
            for raw in inline_attach_ids:
                aid = str(raw or "").strip()
                if not aid:
                    continue
                extra_parts.append(f'<img src="cid:{aid}"><br>')
            inner = "".join(extra_parts)
            if inner:
                content_div += f'<div style="{line_style}">{inner}</div>'
        signature_html = (self._request.query_signature() or "").replace("\r\n", "").replace("\n", "")
        parts = [
            '<div class="zcontentRow">',
            content_div,
            f'<div style="{line_style}"><br></div>',
        ]
        if signature_html.strip():
            parts.append(signature_html)
        parts.append('<div style="line-height:1.5" class="zMailFrom"></div>')
        parts.append("</div>")
        return "".join(parts)

    # ---------- 收件人格式化 ----------

    def _is_group_recipient(self, item: dict) -> bool:
        uno = (item.get("UNO") or "").strip()
        e = item.get("E") or ""
        n = item.get("N") or ""
        return uno == "" and ("group_" in e or (n and n.startswith("$")))

    def _to_mt_mc_item(self, item: dict) -> dict:
        uno = item.get("UNO") or ""
        e = item.get("E") or ""
        id_val = uno if uno else e
        return {
            "UNO": uno,
            "N": item.get("N") or "",
            "E": e,
            "T": item.get("T") or "",
            "Y": item.get("Y") or "",
            "F": item.get("F") or "6",
            "P": item.get("P") or "",
            "CT": "0",
            "ID": id_val,
            "GT": None,
            "TNO": None,
        }

    def _to_mt_mc_item_group(self, item: dict) -> dict:
        return {
            "UNO": None,
            "N": item.get("N") or "",
            "E": item.get("E") or "",
            "T": item.get("T") or None,
            "F": "2",
            "P": None,
            "CT": None,
            "ID": item.get("ID") or "",
            "GT": None,
            "TNO": None,
        }

    def _build_mt_mc(self, to_list: list, cc_list: list) -> tuple:
        mt = []
        for x in to_list:
            mt.append(self._to_mt_mc_item_group(x) if self._is_group_recipient(x) else self._to_mt_mc_item(x))
        mc = []
        for x in cc_list:
            mc.append(self._to_mt_mc_item_group(x) if self._is_group_recipient(x) else self._to_mt_mc_item(x))
        return mt, mc

    def _build_mm(self, bcc_list: list) -> list:
        """将密送列表构建为 zMail MM 字段所需的 dict 列表（与 MT/MC 格式一致）。"""
        if not bcc_list:
            return []
        mm = []
        for x in bcc_list:
            mm.append(self._to_mt_mc_item_group(x) if self._is_group_recipient(x) else self._to_mt_mc_item(x))
        return mm

    def _resolve_merged_recipient_list(self, items: list) -> tuple:
        """to/cc/bcc 合并列表：每项为邮箱；逐项 SysGetUser 后区分个人/群组。"""
        if not items:
            return [], None
        out = []
        for s in items:
            s = (s or "").strip() if isinstance(s, str) else ""
            if not s:
                continue
            if not is_email_address(s):
                return None, f"收件人必须为合法邮箱地址：{s!r}"
            u = self._request.get_user(s)
            if not u or (not (u.get("UNO") or "").strip() and not (u.get("E") or "").strip()):
                return None, f"SysGetUser 未返回该邮箱的有效用户信息：{s}"
            item = dict(u)
            out.append(self._to_mt_mc_item(item))
        return out, None

    # ---------- 新邮件 D 构建 ----------

    def _build_new_mail_d(
        self,
        new_mail_id: str,
        title: str,
        mf: dict,
        to_list: list,
        cc_list: list,
        mct: str,
        bcc_list: list = None,
    ) -> dict:
        mt, mc = self._build_mt_mc(to_list, cc_list)
        mm = self._build_mm(bcc_list or [])
        mf_clean = {k: mf.get(k, "") for k in ("UNO", "N", "Y", "E", "T")}
        return {
            "ID": new_mail_id,
            "RID": "",
            "T": "3",
            "MCF": "0",
            "TI": title or "",
            "IP": "0",
            "IT": "0",
            "MF": json.dumps(mf_clean, ensure_ascii=False),
            "MT": json.dumps(mt, ensure_ascii=False),
            "MC": json.dumps(mc, ensure_ascii=False) if mc else None,
            "MM": json.dumps(mm, ensure_ascii=False) if mm else None,
            "MCT": mct,
            "ATS": "[]",
            "PL": "3",
            "IL": "0",
            "INR": "0",
            "SBFLAG": "0",
            "MSB": "0",
            "MSender": None,
            "UAL": 0,
            "AE": "0",
            "IFM": "0",
            "ISE": "0",
            "MDEF": 0,
            "LATS": "[]",
        }

    # ---------- 公共准备流程 ----------

    @staticmethod
    def _normalize_lists(to_list, cc_list, bcc_list=None):
        """将 to/cc/bcc 规范化为字符串列表（已由 main 按逗号拆分时可再滤空）。"""
        to_list = to_list if isinstance(to_list, list) else [to_list] if to_list else []
        cc_list = cc_list if isinstance(cc_list, list) else (cc_list or [])
        bcc_list = bcc_list if isinstance(bcc_list, list) else (bcc_list or [])
        to_list = [str(x).strip() for x in to_list if x and str(x).strip()]
        cc_list = [str(x).strip() for x in cc_list if x and str(x).strip()]
        bcc_list = [str(x).strip() for x in bcc_list if x and str(x).strip()]
        return to_list, cc_list, bcc_list

    def _resolve_all_recipients(self, to_list, cc_list, bcc_list=None):
        """解析全部主送/抄送/密送（个人与群组邮箱合并于各列表），返回 (to_resolved, cc_resolved, bcc_resolved, error_msg)。"""
        to_resolved, err = self._resolve_merged_recipient_list(to_list or [])
        if err:
            return None, None, None, err
        cc_resolved, err = self._resolve_merged_recipient_list(cc_list or [])
        if err:
            return None, None, None, err
        bcc_resolved, err = self._resolve_merged_recipient_list(bcc_list or [])
        if err:
            return None, None, None, err
        return (to_resolved or [], cc_resolved or [], bcc_resolved or [], None)

    def _prepare_new_mail(
        self,
        title,
        body,
        to_list,
        cc_list,
        bcc_list=None,
        allow_empty_recipients=False,
        inline_attach_ids=None,
    ):
        """
        公共准备流程：规范化 → 解析收件人 → 获取邮件 ID → 获取发件人 → 构建正文。
        allow_empty_recipients 为 True 时（如保存草稿）允许收件人/抄送/密送全空。
        返回 (context_dict, None) 成功 或 (None, error_dict) 失败。
        """
        to_list, cc_list, bcc_list = self._normalize_lists(
            to_list, cc_list, bcc_list or [],
        )
        if not allow_empty_recipients:
            if not (to_list or cc_list or bcc_list):
                return None, {"status": False, "error": "收件人、抄送人、密送人不能同时为空，请提供 --to / --cc / --bcc 之一。"}
        to_resolved, cc_resolved, bcc_resolved, err = self._resolve_all_recipients(
            to_list, cc_list, bcc_list,
        )
        if err:
            return None, {"status": False, "error": err}
        new_mail_id = self._request.get_new_mail_id()
        mf = self._get_user_info(self.emp_no)
        if not mf or not mf.get("UNO"):
            return None, {"status": False, "error": "获取发件人信息失败。"}
        mct = self._build_send_content(body or "", inline_attach_ids=inline_attach_ids)
        return {
            "new_mail_id": new_mail_id,
            "mf": mf,
            "to_resolved": to_resolved,
            "cc_resolved": cc_resolved,
            "bcc_resolved": bcc_resolved,
            "mct": mct,
        }, None
