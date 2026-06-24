#!/usr/bin/env python3
"""zMail 新建邮件立即发送。"""

import json

from zmail_send_draft_base import ZmailSendDraftBase
from zmail_reply_forward_base import extract_inline_attach_ids
from zmail_attachment import ZmailAttachment


class ZmailSend(ZmailSendDraftBase):
    """新建邮件立即发送。"""

    def send_new_mail(
        self,
        title: str,
        to_list: list,
        cc_list: list,
        body: str,
        bcc_list: list = None,
        attachments: list = None,
        is_inline: str = "0",
    ) -> dict:
        """
        立即发送新邮件。
        to_list/cc_list/bcc_list：收件人列表（通常来自 search-users 的确认结果或 CLI 逗号分隔邮箱拆分）。
        attachments：本地文件路径列表（可选），发送前校验合法性。
        is_inline："1" 时将附件中的图片插入正文（仅图片），"0" 或 "1"，默认 "0"。
        """
        if attachments:
            att = ZmailAttachment()
            ok, failures = att.validate_files(attachments)
            if not ok:
                reasons = "; ".join(f"{p}: {r}" for p, r in failures)
                return {"status": False, "error": f"附件校验失败: {reasons}"}
        ctx, err = self._prepare_new_mail(
            title, body, to_list, cc_list,
            bcc_list=bcc_list,
        )
        if err:
            return err
        new_mail_id = ctx["new_mail_id"]
        D = self._build_new_mail_d(
            new_mail_id, title, ctx["mf"], ctx["to_resolved"], ctx["cc_resolved"], ctx["mct"],
            bcc_list=ctx.get("bcc_resolved"),
        )
        if attachments:
            upload_result = att.upload_attach(new_mail_id, attachments, is_inline=is_inline)
            if isinstance(upload_result, dict) and not upload_result.get("status", True):
                return upload_result
            ats_list = upload_result if isinstance(upload_result, list) else []
            if ats_list:
                D["ATS"] = json.dumps(ats_list, ensure_ascii=False)
            inline_ids = extract_inline_attach_ids(ats_list)
            D["MCT"] = self._build_send_content(body or "", inline_attach_ids=inline_ids)
        resp = self._request.send_email(D)
        if not resp.get("S"):
            msg = resp.get("M") or resp.get("C") or "SysSendEmail 失败"
            return {"status": False, "error": f"zmail SysSendEmail 未成功: {msg}"}
        mid = (resp.get("D") or {}).get("mid") or ctx["new_mail_id"]
        return {"status": True, "mid": mid}
