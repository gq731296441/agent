#!/usr/bin/env python3
"""zMail 邮件标记：已读/未读、标星/取消标星。"""

from typing import Dict, List, Optional, Union

from util import is_history_mail_id
from zmail_base import ZmailBase
from zmail_search_emails import ZmailSearchEmails

_STATUS_MAP = {
    "read": "1",
    "unread": "0",
    "starred": "1",
    "unstarred": "0",
}

_ACTION_MAP = {
    "read": "read",
    "unread": "read",
    "starred": "star",
    "unstarred": "star",
}


class ZmailMark(ZmailBase):
    """邮件标记操作：已读/未读、标星/取消标星。"""

    @staticmethod
    def _normalize_email_ids(email_ids: Optional[Union[str, List[str]]]) -> List[str]:
        if email_ids is None:
            return []
        if isinstance(email_ids, str):
            return [eid.strip() for eid in email_ids.split(",") if eid.strip()]
        return [eid.strip() for eid in email_ids if (eid or "").strip()]

    def _mark_ids(self, email_ids: List[str], status_lower: str) -> Dict:
        v_val = _STATUS_MAP[status_lower]
        action = _ACTION_MAP[status_lower]

        history_ids = [eid for eid in email_ids if is_history_mail_id(eid)]
        if history_ids:
            return {
                "status": False,
                "error": "历史邮件不支持标记操作",
                "history_ids": history_ids,
            }

        results = []
        for eid in email_ids:
            try:
                self._request.set_mail_mark(
                    email_ids=eid,
                    action=action,
                    v=v_val,
                )
                results.append({"id": eid, "status": True})
            except Exception as e:
                results.append({"id": eid, "status": False, "error": str(e)})

        return {
            "status": all(r["status"] for r in results),
            "results": results,
        }

    def mark(
        self,
        email_ids: Optional[Union[str, List[str]]] = None,
        status: str = "read",
        mailbox_type: str = "inbox",
        keyword: str = "",
        days: Optional[int] = None,
        sender: str = "",
        recipients: str = "",
        cc: str = "",
        content: str = "",
        attachment_name: str = "",
        mail_attribute: str = "",
        folder: str = "",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        categories: Optional[List[str]] = None,
    ) -> Dict:
        """
        标记邮件状态。

        若传入非空的 email_ids（字符串逗号分隔或列表），仅对这些 ID 执行标记，忽略搜索相关参数；
        否则参数与 ZmailSearchEmails.search 一致：先搜索得到 ID，再逐封标记。

        :param email_ids: 可选。邮件 ID，支持单个字符串（逗号分隔多个）或列表。
        :param status: "read"（已读）、"unread"（未读）、"starred"（标星）、"unstarred"（取消标星）。
        :param mailbox_type, keyword, days, sender, recipients, cc, content, attachment_name,
               mail_attribute, folder, start_date, end_date, categories: 与 search 一致；仅在未提供
               email_ids 时使用。
        :return: {"status": True/False, "results": [...]}；按搜索条件标记时可能含 search_used、matched_count。
        """
        status_lower = status.lower().strip()
        if status_lower not in _STATUS_MAP:
            raise ValueError(f"status 必须为 read/unread/starred/unstarred，当前值: {status!r}")

        direct_ids = self._normalize_email_ids(email_ids)
        if direct_ids:
            return self._mark_ids(direct_ids, status_lower)

        def _s(x):
            return (x or "").strip() or None

        search_result = ZmailSearchEmails().search(
            mailbox_type=mailbox_type,
            keyword=_s(keyword),
            days=days,
            start_date=_s(start_date),
            end_date=_s(end_date),
            sender=_s(sender) or "",
            recipients=_s(recipients) or "",
            cc=_s(cc) or "",
            content=_s(content) or "",
            attachment_name=_s(attachment_name) or "",
            mail_attribute=_s(mail_attribute) or "",
            folder=_s(folder) or "",
            categories=categories,
        )

        if search_result.get("error"):
            return {
                "status": False,
                "error": search_result["error"],
                "folder_data": search_result.get("folder_data"),
            }

        emails = search_result.get("emails") or []
        resolved_ids = [e.get("id") for e in emails if e.get("id")]
        if not resolved_ids:
            err = "未找到符合条件的邮件"
            if search_result.get("beyond_half_year"):
                err = "搜索范围超出半年或暂无邮件"
            out = {
                "status": False,
                "error": err,
                "results": [],
            }
            if search_result.get("beyond_half_year"):
                out["beyond_half_year"] = True
            return out

        inner = self._mark_ids(resolved_ids, status_lower)
        inner["search_used"] = True
        inner["matched_count"] = len(resolved_ids)
        if search_result.get("beyond_half_year"):
            inner["beyond_half_year"] = True
        return inner
