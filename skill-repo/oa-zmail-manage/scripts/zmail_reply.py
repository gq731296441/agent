#!/usr/bin/env python3
"""zMail 邮件回复。统一入口 main.py。"""

import json
from typing import Optional

from util import is_history_mail_id
from zmail_send_draft_base import ZmailSendDraftBase
from zmail_reply_forward_base import ZmailReplyForwardBase


class ZmailReply(ZmailSendDraftBase, ZmailReplyForwardBase):
    """zMail 回复客户端，继承 ZmailReplyForwardBase，对外提供 reply 方法。"""

    def _create_new_mail_id(self) -> str:
        return self._request.get_new_mail_id()

    def get_user_info(self, uno: str) -> dict:
        """获取用户信息，返回 mf 格式。"""
        if not uno or not str(uno).strip():
            return {}
        r = self._request.get_user(str(uno).strip())
        return {k: v for k, v in r.items() if v is not None}

    @staticmethod
    def _reply_title(title: str) -> str:
        t = (title or "").strip()
        return title if t.startswith("答复:") else f"答复: {title}"

    def _build_send_params(
        self,
        new_mail_id: str,
        email_detail: dict,
        content: str,
        reply_id: str,
        with_attachments: bool,
    ) -> dict:
        mf = self.get_user_info(self.emp_no) if self.emp_no else {}
        reply_title = self._reply_title(email_detail.get("title") or "")
        mf_from_detail = email_detail.get("mf") or {}
        mt = [mf_from_detail] if mf_from_detail else []
        rid = "" if is_history_mail_id(reply_id) else reply_id
        if is_history_mail_id(reply_id):
            ats = self._build_histroy_mail_ats_for_new_mail(
                new_mail_id,
                email_detail.get("ats"),
                reply_id,
                with_attachments=with_attachments,
            )
        else:
            ats = self._build_ats_for_new_mail(
                new_mail_id,
                email_detail.get("ats"),
                with_attachments=with_attachments,
            )
        D = {
            "ID": new_mail_id,
            "RID": rid,
            "T": "3",
            "MCF": "0",
            "TI": reply_title,
            "IP": "1",  # 是否已回复
            "IT": "0",  # 是否已转发
            "MF": json.dumps(mf, ensure_ascii=False),
            "MT": json.dumps(mt, ensure_ascii=False),
            "MC": None,
            "MM": None,
            "MCT": "",
            "ATS": ats,
            "PL": "3",
            "IL": "0",
            "INR": "0",
            "SBFLAG": "0",
            "MSB": "0",
            "MSender": None,
            "UAL": 1,
            "AE": "0",
            "IFM": "0",
            "ISE": "0",
            "MDEF": 0,
        }
        return {"new_mail_id": new_mail_id, "email_detail": email_detail, "content": content, "reply_id": reply_id, "D": D}

    def reply(
        self,
        email_id: str,
        content: str,
        to_list: Optional[list] = None,
        cc_list: Optional[list] = None,
        bcc_list: Optional[list] = None,
        with_attachments: bool = False,
    ):
        """回复邮件。未传 to/cc/bcc 时回复给原发件人；传则使用指定收件人/抄送/密送（邮箱列表）。"""
        new_mail_id = self._create_new_mail_id()
        email_detail = self._get_email_content(email_id)
        can_forward = bool(email_detail.get("can_forward", True))
        att_encrypt = str(email_detail.get("att_encrypt") or "").strip()
        mpt = str(email_detail.get("mpt") or "").strip()

        effective_with_attachments = with_attachments
        send_ae = "0"
        # 禁止转发：不允许携带原文与附件；仅在允许转发时才判断附件授权与 AE/MPT
        if not can_forward:
            effective_with_attachments = False
        else:
            # 附件授权：当 att_encrypt=1 且 mpt=1 时，回复邮件需要携带 AE=1；当 mpt!=1 时不允许携带附件
            if att_encrypt == "1":
                if mpt == "1":
                    send_ae = "1"
                else:
                    effective_with_attachments = False
        send_params = self._build_send_params(
            new_mail_id,
            email_detail,
            content,
            email_id,
            with_attachments=effective_with_attachments,
        )
        D = send_params["D"]
        if send_ae == "1":
            D["AE"] = send_ae

        to_list = to_list or []
        cc_list = cc_list or []
        bcc_list = bcc_list or []
        if to_list or cc_list or bcc_list:
            to_list, cc_list, bcc_list = self._normalize_lists(to_list, cc_list, bcc_list)
            to_resolved, cc_resolved, bcc_resolved, err = self._resolve_all_recipients(
                to_list, cc_list, bcc_list,
            )
            if err:
                return {"status": False, "error": err}
            mt, mc = self._build_mt_mc(to_resolved, cc_resolved)
            mm = self._build_mm(bcc_resolved)
            D["MT"] = json.dumps(mt, ensure_ascii=False)
            D["MC"] = json.dumps(mc, ensure_ascii=False) if mc else None
            D["MM"] = json.dumps(mm, ensure_ascii=False) if mm else None

        D["MCT"] = self._prepare_content_with_original(email_detail, content)
        try:
            resp = self._request.send_email(D)
            if resp.get("S"):
                message = ""
                if not email_detail.get("can_forward", True):
                    message = "该邮件已被设置为禁止转发，回复未携带原文和附件。"
                mid = (resp.get("D") or {}).get("mid") or new_mail_id
                return {"status": True, "mid": mid, "message": message}
            msg = resp.get("M") or resp.get("C") or "邮件发送失败"
            return {"status": False, "error": f"zmail 邮件发送未成功: {msg}"}
        except Exception as e:
            return {"status": False, "error": str(e)}

    def get_email_brief(self, email_id: str) -> dict:
        """返回邮件摘要信息：标题 title、收件人列表 mt。"""
        detail = self._get_email_content(email_id)
        result = {
            "title": self._reply_title(detail.get("title", "")),
            "mt": detail.get("mt", []),
        }
        if not bool(detail.get("can_forward", True)):
            result["notice"] = "该邮件已被设置为禁止转发，回复不能携带原文和附件"
        elif str(detail.get("att_encrypt") or "").strip() == "1" and str(detail.get("mpt") or "").strip() != "1":
            result["notice"] = "该邮件已被设置为附件授权，回复不能携带附件"
        return result
