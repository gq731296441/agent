#!/usr/bin/env python3
"""zMail 邮件查看：根据邮件 ID 获取正文（JSON）。"""

from typing import Any, Dict, List, Optional, Tuple, Union
import json

from html_markdown import html_to_markdown, get_space_link_from_mail_html

from util import ats_to_list, display_name_from_user, display_names_from_user_objs, is_history_mail_id, to_json_object
from zmail_base import ZmailBase


def sender_recipients_from_mail_context(d: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    """
    从 SysGetMailContext / 历史邮件详情返回的 D 解析 sender、recipients、cc。

    仅供 view 使用（放在 zmail_view 内部，避免 util 过度膨胀）。
    - sender: 来自 MF
    - recipients: 来自 MT（主送），多项用中文分号 `；` 连接
    - cc: 来自 MC/MCC（抄送），多项用中文分号 `；` 连接
    """
    if not d or not isinstance(d, dict):
        return "", "", ""
    mf_raw = d.get("MF")
    mt_raw = d.get("MT")
    # 不同接口/版本抄送字段可能不同，这里按“尽量兼容”策略取值
    cc_raw = d.get("MC")
    if cc_raw is None:
        cc_raw = d.get("MCC")
    mf = to_json_object(mf_raw) if mf_raw is not None else None
    if mf is not None and not isinstance(mf, dict):
        mf = {}
    if mf is None:
        mf = {}
    sender = display_name_from_user(mf) if mf else ""
    recipients = display_names_from_user_objs(mt_raw, sep="；")
    cc = display_names_from_user_objs(cc_raw, sep="；")
    return sender, recipients, cc


class ZmailView(ZmailBase):
    """查看邮件正文，支持多个 ID，返回对象数组。"""

    def get_attach_view_list_from_ats(self, ats_raw: str) -> dict:
        """
        从 ATS 原始数据获取附件预览结果（按可预览/不可预览分类）。
        :param ats_raw: ATS 字符串（JSON）或对象。
        :return: {
            "preview_attachments": [{"id": 附件ID, "name": 名称, "preview_url": 预览地址}, ...],
            "non_preview_attachments": [{"id": 附件ID, "name": 名称}, ...],
        }
        """
        ats_list = ats_to_list(ats_raw)
        preview_attachments: List[dict] = []
        non_preview_attachments: List[dict] = []
        for item in ats_list:
            attach_id = item.get("ID") or item.get("id") or ""
            name = item.get("N") or item.get("n") or ""
            if not attach_id:
                continue
            preview_url, err = self._request.get_attach_view_url(item)
            if err:
                non_preview_attachments.append({
                    "id": str(attach_id),
                    "name": str(name),
                })
                continue
            pu = (preview_url or "").strip()
            if not pu:
                non_preview_attachments.append({
                    "id": str(attach_id),
                    "name": str(name),
                })
                continue
            preview_attachments.append({
                "id": str(attach_id),
                "name": str(name),
                "preview_url": pu,
            })
        return {
            "preview_attachments": preview_attachments,
            "non_preview_attachments": non_preview_attachments,
        }

    def get_history_mail_attach_view_list_from_ats(self, ats_raw: str) -> dict:
        """
        从 ATS 原始数据获取历史邮件附件预览结果（按可预览/不可预览分类）。

        注：当前实现逻辑与 `get_attach_view_list_from_ats` 保持一致；
        后续若历史邮件的附件字段/预览接口存在差异，可在此方法内做专门适配。

        :param ats_raw: ATS 字符串（JSON）或对象。
        :return: {
            "preview_attachments": [{"id": 附件ID, "name": 名称, "preview_url": 预览地址}, ...],
            "non_preview_attachments": [{"id": 附件ID, "name": 名称}, ...],
        }
        """
        ats_list = ats_to_list(ats_raw)
        preview_attachments: List[dict] = []
        non_preview_attachments: List[dict] = []
        for item in ats_list:
            attach_id = item.get("ID") or item.get("id") or ""
            name = item.get("N") or item.get("n") or ""
            if not attach_id:
                continue
            preview_url, err = self._request.get_history_mail_attach_view_url(item)
            if err:
                non_preview_attachments.append({
                    "id": str(attach_id),
                    "name": str(name),
                })
                continue
            pu = (preview_url or "").strip()
            if not pu:
                non_preview_attachments.append({
                    "id": str(attach_id),
                    "name": str(name),
                })
                continue
            preview_attachments.append({
                "id": str(attach_id),
                "name": str(name),
                "preview_url": pu,
            })
        return {
            "preview_attachments": preview_attachments,
            "non_preview_attachments": non_preview_attachments,
        }

    def _get_email_content(self, email_id: str, mailbox_type: str = "inbox") -> dict:
        """获取单封邮件正文（含在线与历史，由 get_mail_context 统一路由）。"""
        d = self._request.get_mail_context(email_id, mailbox_type=mailbox_type)
        if d.get("error"):
            return {
                "id": email_id,
                "title": "",
                "date": "",
                "body": "",
                "sender": "",
                "recipients": "",
                "cc": "",
                "mpt": "",
                "t": "",
                "attachments": [],
                "error": d["error"],
            }
        html_content = d.get("MCT") or ""
        space_link = get_space_link_from_mail_html(html_content)
        body_md = "" if space_link else html_to_markdown(html_content)
        title = d.get("TI") or d.get("S") or "无主题"
        date = d.get("SDT") or d.get("DT") or ""
        sender, recipients, cc = sender_recipients_from_mail_context(d)
        result = {
            "id": email_id,
            "title": title,
            "date": date,
            "body": body_md,
            "sender": sender,
            "recipients": recipients,
            "cc": cc,
            "mpt": d.get("MPT", ""),
            "t": d.get("T", ""),
            "space_link": space_link,
        }
        ats = d.get("ATS")
        if ats and str(ats).strip():
            if is_history_mail_id(email_id):
                # 历史邮件附件接口需要在 ATS 对象中携带 MID=email_id
                ats_for_history = ats
                try:
                    if isinstance(ats, str):
                        ats_obj = json.loads(ats)
                        if isinstance(ats_obj, dict):
                            ats_obj["MID"] = str(email_id).strip()
                            ats_for_history = json.dumps(ats_obj, ensure_ascii=False)
                        elif isinstance(ats_obj, list):
                            for it in ats_obj:
                                if isinstance(it, dict):
                                    it["MID"] = str(email_id).strip()
                            ats_for_history = json.dumps(ats_obj, ensure_ascii=False)
                except Exception:
                    # 兜底：若 ATS 不是合法 JSON 或解析失败，则回退到原始 ats
                    ats_for_history = ats

                attach_view_data = self.get_history_mail_attach_view_list_from_ats(ats_for_history)
            else:
                attach_view_data = self.get_attach_view_list_from_ats(ats)
            preview_attachments = attach_view_data.get("preview_attachments") or []
            non_preview_attachments = attach_view_data.get("non_preview_attachments") or []
            result["attachments"] = [
                {
                    "id": a.get("id"),
                    "name": a.get("name"),
                    "preview_url": a.get("preview_url") or "",
                }
                for a in preview_attachments
            ]
            result["non_preview_attachments"] = [
                {
                    "id": a.get("id"),
                    "name": a.get("name"),
                }
                for a in non_preview_attachments
            ]
        return result

    def create_contents(self, ids: List[str], mailbox_type: str = "inbox") -> dict:
        """按邮件 ID 列表获取正文（view 模式），返回 {count, list}。"""
        id_to_content: dict = {}
        for eid in ids:
            content = self._get_email_content(eid, mailbox_type=mailbox_type)
            if not is_history_mail_id(eid):
                self._request.set_mail_mark(eid)
            id_to_content[eid] = content
        result = [id_to_content[eid] for eid in ids]
        return {"count": len(result), "list": result}

    def show_email(
        self,
        email_ids: Union[str, List[str]],
    ) -> dict:
        """
        获取邮件正文（view 模式），支持多个 ID，返回对象。
        :param email_ids: 单个 ID、逗号分隔字符串或 ID 列表。
        :return: {"count", "list"}。
        """
        if not email_ids:
            raise ValueError("必须提供 email_id 参数")
        ids: List[str] = []
        if isinstance(email_ids, str):
            ids = [x.strip() for x in email_ids.split(",") if x and x.strip()]
        elif isinstance(email_ids, list):
            ids = [str(x).strip() for x in email_ids if x is not None and str(x).strip()]
        if not ids:
            raise ValueError("必须提供至少一个有效的邮件 ID")
        return self.create_contents(ids)
