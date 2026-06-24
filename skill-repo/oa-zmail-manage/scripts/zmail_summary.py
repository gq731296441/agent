#!/usr/bin/env python3
"""zMail 总结：收件箱/发件箱邮件总结。先 search，再按 ID 列表生成总结内容。"""

from typing import List, Optional

from util import SEARCH_DAYS, resolve_date_range
from util import user_from_mf

from html_markdown import html_to_text

from zmail_base import ZmailBase
from zmail_search_emails import ZmailSearchEmails

_MAX_MAIL_COUNT_FOR_SUMMARY = 100
_CONTENT_LENGTH_THRESHOLD = 200


class ZmailSummary(ZmailBase):
    """收件箱/发件箱邮件总结。先调用 search，再按邮件 ID 列表生成 summary 结果。"""

    def _get_email_summary_from_context(
        self, email_id: str, d: dict, status: Optional[str] = None, title: Optional[str] = None
    ) -> str:
        """从已获取的 context d 构建总结用纯文本，避免重复请求。"""
        title_str = title or d.get("TI") or d.get("LPN") or ""
        line = f"【标题】{title_str}" if title_str else ""
        if status is None:
            status = d.get("PL")
        if status is not None and str(status) == "1":
            if line:
                line = "**紧急**\n\n" + line
            else:
                line = "**紧急**"
        if line and len(line) >= _CONTENT_LENGTH_THRESHOLD:
            return line[:_CONTENT_LENGTH_THRESHOLD]
        html_content = d.get("MCT") or ""
        body_text = html_to_text(html_content)
        if body_text:
            content = f"{line}\n\n【正文】\n{body_text}" if line else f"【正文】\n{body_text}"
        else:
            content = line
        if content and len(content) > _CONTENT_LENGTH_THRESHOLD:
            content = content[:_CONTENT_LENGTH_THRESHOLD]
        return content

    def _build_summary_content(self, user_to_ids: dict, id_to_context: dict) -> str:
        """按 user_to_ids 和 id_to_context 构建总结内容字符串。"""
        blocks = []
        for user, id_list in (user_to_ids or {}).items():
            id_list = id_list or []
            items = []
            for entry in id_list:
                email_id = entry.get("id") if isinstance(entry, dict) else entry
                if not email_id:
                    continue
                status = entry.get("status") if isinstance(entry, dict) else None
                title = entry.get("title") if isinstance(entry, dict) else None
                d = id_to_context.get(email_id)
                if d:
                    items.append(self._get_email_summary_from_context(email_id, d, status, title))
            if id_list:
                parts = "---\n\n".join(items) if items else ""
                blocks.append(f"{user}\n在查询范围内的邮件内容：\n{parts}")
        return "\n".join(blocks)

    def create_summary(self, ids: List[str]) -> dict:
        """按邮件 ID 获取内容，按 internal/external/public 分组。"""
        if not ids:
            return {
                "internal": {"user_count": 0, "mail_count": 0, "content": ""},
                "external": {"user_count": 0, "mail_count": 0, "content": ""},
                "public": {"user_count": 0, "mail_count": 0, "content": ""},
            }
        internal_data: dict = {}
        external_data: dict = {}
        public_data: dict = {}
        id_to_context: dict = {}
        for eid in ids:
            d = self._request.get_mail_context(eid)
            id_to_context[eid] = d
            sid = str(d.get("SID") or "").strip()
            iom = str(d.get("IOM") or "").strip()
            user = user_from_mf(d.get("MF")) or "未知"
            entry = {"id": eid, "status": d.get("PL"), "title": d.get("TI")}
            if sid != "0":
                if user not in public_data:
                    public_data[user] = []
                public_data[user].append(entry)
            elif iom == "0":
                if user not in internal_data:
                    internal_data[user] = []
                internal_data[user].append(entry)
            else:
                if user not in external_data:
                    external_data[user] = []
                external_data[user].append(entry)

        return {
            "internal": {
                "user_count": len(internal_data),
                "mail_count": sum(len(v or []) for v in internal_data.values()),
                "content": self._build_summary_content(internal_data, id_to_context),
            },
            "external": {
                "user_count": len(external_data),
                "mail_count": sum(len(v or []) for v in external_data.values()),
                "content": self._build_summary_content(external_data, id_to_context),
            },
            "public": {
                "user_count": len(public_data),
                "mail_count": sum(len(v or []) for v in public_data.values()),
                "content": self._build_summary_content(public_data, id_to_context),
            },
        }

    def summary_emails(
        self,
        mailbox_type: str = "inbox",
        keyword: str = "",
        days: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        sender: str = "",
        recipients: str = "",
        cc: str = "",
        content: str = "",
        attachment_name: str = "",
        mail_attribute: str = "",
        folder: str = "",
        categories: Optional[List[str]] = None,
        ids: Optional[List[str]] = None,
    ) -> dict:
        """
        总结邮件。
        若传入 ids，直接按 ID 列表总结，忽略其他搜索参数；
        否则参数与 ZmailSearchEmails.search 一致，先 search 再总结（超过 100 条仅取前 100）。
        """
        if ids:
            mail_ids = [str(x).strip() for x in ids if x and str(x).strip()]
            if len(mail_ids) > _MAX_MAIL_COUNT_FOR_SUMMARY:
                mail_ids = mail_ids[:_MAX_MAIL_COUNT_FOR_SUMMARY]
            return self.create_summary(mail_ids)

        start_d, end_d = resolve_date_range(
            days=days, start_date=start_date, end_date=end_date, sender=sender
        )

        def _s(x): return (x or "").strip() or None
        result = ZmailSearchEmails().search(
            mailbox_type=mailbox_type,
            keyword=_s(keyword),
            # 以 resolve_date_range 的结果为准，保证搜索范围与返回字段一致
            days=None,
            start_date=start_d,
            end_date=end_d,
            sender=_s(sender) or "",
            recipients=_s(recipients) or "",
            cc=_s(cc) or "",
            content=_s(content) or "",
            attachment_name=_s(attachment_name) or "",
            mail_attribute=_s(mail_attribute) or "",
            folder=_s(folder) or "",
            categories=categories,
        )

        if result.get("error"):
            return {"error": result["error"]}
        if result.get("folder_data") is not None:
            return {"error": result.get("error", "文件夹不存在"), "folder_data": result["folder_data"]}
        ids = [e.get("id") for e in (result.get("emails") or []) if e.get("id")]
        if len(ids) > _MAX_MAIL_COUNT_FOR_SUMMARY:
            ids = ids[:_MAX_MAIL_COUNT_FOR_SUMMARY]

        summary_result = self.create_summary(ids)
        if result.get("beyond_half_year"):
            summary_result["beyond_half_year"] = True
        summary_result["start_date"] = start_d
        summary_result["end_date"] = end_d
        summary_result["default_days"] = SEARCH_DAYS
        return summary_result
