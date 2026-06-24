#!/usr/bin/env python3
"""发件箱：撤回已发送邮件。"""

from zmail_base import ZmailBase


class ZmailOutbox(ZmailBase):
    """发件箱相关操作：撤回。由 main.py rollback-sent 子命令调用。"""

    def rollback_send_mail(self, email_id: str) -> dict:
        """
        撤回已发送邮件。内部调用 request_manager.rollback_send_mail，并规范化返回结构。
        :param email_id: 邮件 ID。
        :return: {"status": true/false, "error": ""}，成功时 error 为空或省略。
        """
        email_id = (email_id or "").strip()
        if not email_id:
            return {"status": False, "error": "email_id 不能为空"}
        resp = self._request.rollback_send_mail(email_id)
        if resp.get("S"):
            return {"status": True}
        # 仅支持 7 天内的邮件撤回
        return {"status": False, "error": "仅支持 7 天内的邮件撤回"}
