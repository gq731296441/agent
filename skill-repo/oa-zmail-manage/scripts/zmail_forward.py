#!/usr/bin/env python3
"""zMail 邮件转发。复用回复的原文 HTML 构建逻辑，收件人为 to/cc/bcc 邮箱列表。"""

import json

from util import is_history_mail_id
from zmail_send_draft_base import ZmailSendDraftBase
from zmail_reply_forward_base import ZmailReplyForwardBase


def _forward_title(title: str) -> str:
    """转发邮件标题，加「转发: 」前缀。"""
    t = (title or "").strip()
    return title if t.startswith("转发:") else f"转发: {title}"


class ZmailForward(ZmailSendDraftBase, ZmailReplyForwardBase):
    """zMail 转发客户端，继承 ZmailSendDraftBase 与 ZmailReplyForwardBase。"""

    def check_can_forward(self, email_id: str, include_detail: bool = False) -> dict:
        """检查邮件是否支持转发，可选返回 email_detail 供后续复用。"""
        email_detail = self._get_email_content(email_id)
        if not bool(email_detail.get("can_forward", True)):
            result = {"status": False, "error": "该邮件不支持转发"}
            if include_detail:
                result["email_detail"] = email_detail
            return result
        result = {"status": True}
        if str(email_detail.get("att_encrypt") or "").strip() == "1" and str(email_detail.get("mpt") or "").strip() != "1":
            result["notice"] = "该邮件已被设置为附件授权，转发不能携带附件"
        if include_detail:
            result["email_detail"] = email_detail
        return result

    def forward(
        self,
        email_id: str,
        to_list: list,
        cc_list: list,
        bcc_list: list = None,
        content: str = "",
        with_attachments: bool = True,
    ) -> dict:
        """
        转发指定邮件。
        :param email_id: 要转发的邮件 ID
        :param to_list/cc_list/bcc_list: 逗号拆分后的邮箱列表（可与群组邮箱混排）
        :param content: 可选转发正文，为空则仅转发原文
        :param with_attachments: 是否携带原邮件附件，默认 True
        :return: {"status": True} 或 {"status": False, "error": "..."}
        """
        to_list, cc_list, bcc_list = self._normalize_lists(
            to_list or [], cc_list or [], bcc_list or [],
        )
        can_forward_result = self.check_can_forward(email_id, include_detail=True)
        if not can_forward_result.get("status"):
            return can_forward_result
        email_detail = can_forward_result.get("email_detail") or {}

        effective_with_attachments = with_attachments
        send_ae = "0"
        att_encrypt = str(email_detail.get("att_encrypt") or "").strip()
        mpt = str(email_detail.get("mpt") or "").strip()
        # 附件授权：当 att_encrypt=1 且 mpt=1 时，转发邮件需要携带 AE=1；当 mpt!=1 时不允许携带附件
        if att_encrypt == "1":
            if mpt == "1":
                send_ae = "1"
            else:
                effective_with_attachments = False

        to_resolved, cc_resolved, bcc_resolved, err = self._resolve_all_recipients(
            to_list, cc_list, bcc_list,
        )
        if err:
            return {"status": False, "error": err}

        new_mail_id = self._request.get_new_mail_id()
        mf = self._get_user_info(self.emp_no)
        if not mf or not mf.get("UNO"):
            return {"status": False, "error": "获取发件人信息失败。"}
        forward_title = _forward_title(email_detail.get("title") or "")
        mct = self._prepare_content_with_original(email_detail, content or "")
        is_hist = is_history_mail_id(email_id)
        rid = "" if is_hist else email_id
        if is_hist:
            ats = self._build_histroy_mail_ats_for_new_mail(
                new_mail_id,
                email_detail.get("ats"),
                email_id,
                with_attachments=effective_with_attachments,
            )
        else:
            ats = self._build_ats_for_new_mail(
                new_mail_id,
                email_detail.get("ats"),
                with_attachments=effective_with_attachments,
            )

        mt, mc = self._build_mt_mc(to_resolved, cc_resolved)
        mm = self._build_mm(bcc_resolved)
        mf_clean = {k: mf.get(k, "") for k in ("UNO", "N", "Y", "E", "T")}

        D = {
            "ID": new_mail_id,
            "RID": rid,
            "T": "3",
            "MCF": "0",
            "TI": forward_title,
            "IP": "0",  # 是否已回复
            "IT": "1",  # 是否已转发
            "MF": json.dumps(mf_clean, ensure_ascii=False),
            "MT": json.dumps(mt, ensure_ascii=False),
            "MC": json.dumps(mc, ensure_ascii=False) if mc else None,
            "MM": json.dumps(mm, ensure_ascii=False) if mm else None,
            "MCT": mct,
            "ATS": ats,
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
        if send_ae == "1":
            D["AE"] = send_ae

        try:
            resp = self._request.send_email(D)
            if resp.get("S"):
                mid = (resp.get("D") or {}).get("mid") or new_mail_id
                return {"status": True, "mid": mid}
            msg = resp.get("M") or resp.get("C") or "转发发送失败"
            return {"status": False, "error": f"zmail SysSendEmail 未成功: {msg}"}
        except Exception as e:
            return {"status": False, "error": str(e)}
