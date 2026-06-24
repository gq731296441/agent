#!/usr/bin/env python3
"""
zMail 基类：持有 RequestManager，提供 emp_no、_parse_send_at、_get_user_info。
所有业务类（ZmailCompose、ZmailSearchEmails、ZmailReply 等）继承此类。
"""

from request_manager import ZmailRequestManager
from util import parse_send_at


class ZmailBase:
    """基类：持有 RequestManager，提供 emp_no。"""

    def __init__(self):
        self._request = ZmailRequestManager()

    @property
    def emp_no(self) -> str:
        return self._request.emp_no

    @property
    def zmail_tid(self) -> str:
        return self._request.zmail_tid

    def _parse_send_at(self, send_at):
        return parse_send_at(send_at)

    def _get_user_info(self, uno: str) -> dict:
        if not uno or not str(uno).strip():
            return {}
        r = self._request.get_user(str(uno).strip())
        return {k: v for k, v in r.items() if v is not None} if r else {}
