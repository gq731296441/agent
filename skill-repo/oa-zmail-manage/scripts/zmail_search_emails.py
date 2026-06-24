#!/usr/bin/env python3
"""zMail 搜索邮件。"""

import math
from datetime import datetime, timedelta
from typing import List, Optional

from util import SEARCH_DAYS, SEARCH_MAX_DAYS, validate_positive_int, validate_search_end_date
from zmail_base import ZmailBase
from zmail_folder import ZmailFolder


class ZmailSearchEmails(ZmailBase):
    """搜索邮件。"""

    def _passes_categories(self, item: dict, cats: List[str]) -> bool:
        """按分类筛选：unread(IR=0)、starred(IMS=1)、urgent(PL=1)。to_me 已在搜索接口通过 STS=12 实现，不在此过滤。"""
        if "unread" in cats:
            ir = item.get("IR")
            if str(ir).strip() != "0":
                return False
        if "starred" in cats:
            ims = item.get("IMS")
            if str(ims).strip() != "1":
                return False
        if "urgent" in cats:
            pl = item.get("PL")
            if str(pl).strip() != "1":
                return False
        return True

    def _search_mail_expert_all(
        self,
        mailbox_type: str = "inbox",
        keyword: str = "",
        days: int = 182,
        sender: str = "",
        recipients: str = "",
        cc: str = "",
        content: str = "",
        attachment_name: str = "",
        mail_attribute: str = "",
        folder_id: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        to_me: bool = False,
    ) -> List[dict]:
        """
        按 keyword/days/sender 或 start/end 分页拉全量，返回所有页的 D 列表（原始 item dict）。
        start/end 为 'YYYY-MM-DD HH:MM:SS'，若同时传入则优先于 days。
        mail_attribute：全部/空不传；attachment（附件）；external（外部）。
        to_me：主送我，True 时传 STS=12 给搜索接口。
        """
        page_size = 50
        scs = []
        if (keyword or "").strip():
            scs.append({"SC": keyword.strip(), "STS": "6"})
        if (sender or "").strip():
            scs.append({"SC": sender.strip(), "STS": "1"})
        if (recipients or "").strip():
            scs.append({"SC": recipients.strip(), "STS": "2"})
        if (cc or "").strip():
            scs.append({"SC": cc.strip(), "STS": "4"})
        if (content or "").strip():
            scs.append({"SC": content.strip(), "STS": "7"})
        if (attachment_name or "").strip():
            scs.append({"SC": attachment_name.strip(), "STS": "14"})
        ma = (mail_attribute or "").strip().lower()
        if ma == "attachment":
            scs.append({"SC": "1", "STS": "13"})
        elif ma == "external":
            scs.append({"SC": "1", "STS": "15"})
        if to_me:
            scs.append({"SC": "", "STS": "12"})
        if start and end:
            start_s, end_s = start, end
        else:
            validate_positive_int(days, "days", min_value=1)
            now = datetime.now()
            end_dt = now
            start_dt = now - timedelta(days=max(0, days - 1))
            start_s = start_dt.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
            end_s = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999).strftime("%Y-%m-%d %H:%M:%S")
        resp = self._request.search_mail_expert(
            start=start_s,
            end=end_s,
            scs=scs,
            mailbox_type=mailbox_type,
            folder_id=folder_id,
            pno=1,
            psize=page_size,
        )
        d_list = []
        for item in resp.get("D") or []:
            if isinstance(item, dict):
                d_list.append(item)
        p_info = resp.get("P") or {}
        total = int(p_info.get("T", 0))
        total_pages = int(math.ceil(total / page_size)) if total > 0 else 1
        for pno in range(2, total_pages + 1):
            r = self._request.search_mail_expert(
                start=start_s,
                end=end_s,
                scs=scs,
                mailbox_type=mailbox_type,
                folder_id=folder_id,
                pno=pno,
                psize=page_size,
            )
            for item in r.get("D") or []:
                if isinstance(item, dict):
                    d_list.append(item)
        return d_list

    def _to_email_items(self, d_list: List[dict], is_outbox: bool) -> List[dict]:
        """将原始 D 列表转为统一格式的 emails。"""
        emails = []
        for item in d_list:
            if not isinstance(item, dict):
                continue
            sid = (item.get("SID") or "").strip() if isinstance(item.get("SID"), str) else item.get("SID")
            iom = (item.get("IOM") or "").strip() if isinstance(item.get("IOM"), str) else item.get("IOM")
            is_external = str(sid).strip() == "0" and str(iom).strip() != "0"
            ia = item.get("IA")
            has_attachments = str(ia).strip() == "1"
            d = {
                "id": item.get("ID", ""),
                "title": item.get("TI", ""),
                "date": item.get("SDT", ""),
            }
            if is_external:
                d["is_external"] = True
            if has_attachments:
                d["has_attachments"] = True
            if is_outbox and item.get("IRC") == "1":
                d["is_rollback"] = True
            emails.append(d)
        return emails

    def _search_current(
        self,
        start_str: str,
        end_str: str,
        mailbox_type: str,
        keyword: str,
        sender: str,
        recipients: str,
        cc: str,
        content: str,
        attachment_name: str,
        mail_attribute: str,
        folder_id: Optional[str],
        to_me: bool,
        cats: List[str],
    ) -> List[dict]:
        """当前搜索，返回 emails 列表。仅当前搜索应用分类过滤（unread/starred/urgent）。"""
        d_list = self._search_mail_expert_all(
            mailbox_type=mailbox_type,
            keyword=keyword,
            sender=sender,
            recipients=recipients,
            cc=cc,
            content=content,
            attachment_name=attachment_name,
            mail_attribute=mail_attribute,
            folder_id=folder_id,
            start=start_str,
            end=end_str,
            to_me=to_me,
        )
        if cats:
            d_list = [item for item in d_list if self._passes_categories(item, cats)]
        is_outbox = (mailbox_type or "").strip().lower() == "outbox"
        return self._to_email_items(d_list, is_outbox)

    def _search_history(
        self,
        start_date: str,
        end_date: str,
        mailbox_type: str,
        keyword: str = "",
        sender: str = "",
        recipients: str = "",
        cc: str = "",
    ) -> List[dict]:
        """历史搜索，返回 emails 列表。mailbox_type 仅支持 inbox/outbox。"""
        mt = (mailbox_type or "").strip().lower()
        if mt not in ("inbox", "outbox"):
            raise ValueError(f"mailbox_type 仅支持 inbox 或 outbox，当前: {mailbox_type!r}")
        sd = (start_date or "").strip() if isinstance(start_date, str) else str(start_date or "")
        ed = (end_date or "").strip() if isinstance(end_date, str) else str(end_date or "")
        sd_full = f"{sd} 00:00:00" if len(sd) == 10 else sd[:19]
        ed_full = f"{ed} 23:59:59" if len(ed) == 10 else ed[:19]
        page_no = 1
        page_size = 50
        subject = (keyword or "").strip() or None
        sender_arg = sender or None
        recipient_arg = recipients or None
        cc_arg = cc or None
        is_outbox = mt == "outbox"

        resp = self._request.get_history_mail_list(
            mailbox_type=mt,
            start_time=sd_full,
            end_time=ed_full,
            subject=subject,
            sender=sender_arg,
            recipient=recipient_arg,
            cc=cc_arg,
            pno=page_no,
            psize=page_size,
        )
        if not resp.get("S"):
            raise RuntimeError(resp.get("M") or resp.get("error") or "查询历史邮件失败")

        total_count = 0
        p_obj = resp.get("P") or {}
        if isinstance(p_obj, dict):
            t_val = p_obj.get("T")
            try:
                if t_val is not None:
                    total_count = int(t_val)
            except (TypeError, ValueError):
                total_count = 0

        d_list = resp.get("D") or []
        emails = self._to_email_items(d_list, is_outbox)

        if total_count <= 0 or len(emails) >= total_count:
            return emails

        current_pno = page_no
        while len(emails) < total_count:
            current_pno += 1
            next_resp = self._request.get_history_mail_list(
                mailbox_type=mt,
                start_time=sd_full,
                end_time=ed_full,
                subject=subject,
                sender=sender_arg,
                recipient=recipient_arg,
                cc=cc_arg,
                pno=current_pno,
                psize=page_size,
            )
            if not next_resp.get("S"):
                raise RuntimeError(next_resp.get("M") or next_resp.get("error") or "查询历史邮件失败")
            next_d_list = next_resp.get("D") or []
            page_emails = self._to_email_items(next_d_list, is_outbox)
            if not page_emails:
                break
            emails.extend(page_emails)

        if total_count > 0 and len(emails) > total_count:
            emails = emails[:total_count]

        return emails

    def search(
        self,
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
    ) -> dict:
        """
        返回 {emails, count}。
        第一步解析时间，第二步根据时间决定走当前/历史/二者，第三步合并输出。
        folder 模式仅支持当前搜索。
        """
        folder_id = None
        use_folder = (mailbox_type or "").strip().lower() == "folder" and (folder or "").strip()
        if use_folder:
            f = (folder or "").strip()
            if f.isdigit():
                folder_id = f
            else:
                folder_result = ZmailFolder().get_folder_id_by_name(folder)
                folder_id = folder_result["id"] if folder_result else None
            if folder_id is None:
                folder_data = ZmailFolder().get_folder_list()
                return {
                    "error": "文件夹「{}」不存在".format(folder.strip()),
                    "emails": [],
                    "count": 0,
                    "folder_data": folder_data,
                }
        cats = [c.strip().lower() for c in (categories or []) if c and c.strip().lower()]
        is_to_me = "to_me" in cats

        # --- 第一步：解析时间，得到 start_str/end_str 和 start_d/end_d ---
        if start_date and end_date:
            sd = start_date.strip()
            ed = end_date.strip()
            start_s = sd[:10]
            end_s = ed[:10]
            try:
                start_dt = datetime.strptime(start_s, "%Y-%m-%d")
                end_dt = datetime.strptime(end_s, "%Y-%m-%d")
            except ValueError:
                raise ValueError("开始日期与结束日期须为 YYYY-MM-DD 格式")
            if end_dt.date() < start_dt.date():
                raise ValueError("结束日期不能早于开始日期，当前: 开始 {} 结束 {}".format(start_s, end_s))
            for s, name in [(sd, "开始"), (ed, "结束")]:
                if len(s) > 10:
                    try:
                        datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        raise ValueError("{}日期时间格式不合法".format(name))
            start_str = sd if len(sd) > 10 else sd + " 00:00:00"
            end_str = ed if len(ed) > 10 else ed + " 23:59:59"
            start_d = start_dt.date()
            end_d = end_dt.date()
        else:
            effective_days = days if days is not None else SEARCH_DAYS
            now = datetime.now()
            end_dt = now
            start_dt = now - timedelta(days=max(0, effective_days - 1))
            start_str = start_dt.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
            end_str = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999).strftime("%Y-%m-%d %H:%M:%S")
            start_s = start_str[:10]
            end_s = end_str[:10]
            start_d = start_dt.date()
            end_d = end_dt.date()

        # 历史查询的时间边界：仅允许查询最近 SEARCH_MAX_DAYS 天内的邮件；
        # 早于该日期的数据需要走“历史库/归档库”查询，或直接拦截（如 folder 查询场景）。
        cutoff = (datetime.now() - timedelta(days=SEARCH_MAX_DAYS)).date()

        if use_folder:
            ok, _ = validate_search_end_date(end_s)
            if not ok:
                return {"beyond_half_year": True, "emails": [], "count": 0}
            emails = self._search_current(
                start_str, end_str, mailbox_type, keyword, sender, recipients, cc,
                content, attachment_name, mail_attribute, folder_id, is_to_me, cats,
            )
            return {"emails": emails, "count": len(emails)}

        # --- 第二步：根据时间决定走当前/历史/二者 ---
        current_emails: List[dict] = []
        history_emails: List[dict] = []

        # 结束日期落在可查询窗口内（>= cutoff）时，需要查询“当前库”：
        # 若 start_d 早于 cutoff，则当前库的起始时间应从 cutoff 的下一天 00:00:00 开始，
        # 避免与历史库在 cutoff 当天产生重复/交叉。
        if end_d >= cutoff:
            cur_start = (
                start_str
                if start_d >= cutoff
                else ((cutoff + timedelta(days=1)).strftime("%Y-%m-%d") + " 00:00:00")
            )
            current_emails = self._search_current(
                cur_start, end_str, mailbox_type, keyword, sender, recipients, cc,
                content, attachment_name, mail_attribute, folder_id, is_to_me, cats,
            )
        # 开始日期早于 cutoff 时，需要补查“历史库”（仅 inbox/outbox 支持历史查询）。
        # 历史库的结束日期最多到 cutoff（包含 cutoff 当天）。
        if start_d < cutoff:
            mt = (mailbox_type or "").strip().lower()
            if mt in ("inbox", "outbox"):
                hist_end_d = min(end_d, cutoff)
                history_emails = self._search_history(
                    start_s, hist_end_d.strftime("%Y-%m-%d"),
                    mailbox_type, keyword, sender, recipients, cc,
                )

        # --- 第三步：合并输出 ---
        emails = current_emails + history_emails
        return {"emails": emails, "count": len(emails)}
