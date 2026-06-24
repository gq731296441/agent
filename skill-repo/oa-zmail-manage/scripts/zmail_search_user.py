#!/usr/bin/env python3
"""收件人/抄送人/密送人/群组搜索（查用户或群组），独立于 ZmailSend / ZmailDraft。仅依赖 ZmailBase 的 _request（pccontacts API）。收件人、抄送人、密送人、群组均通过本模块的 search_users 能力解析。"""

from util import is_email_address

from zmail_base import ZmailBase


class ZmailSearchUser(ZmailBase):
    """收件人/抄送人/密送人/群组搜索：调用 SysSearchUser 或按邮箱返回。收件人、抄送人、密送人、群组都走此能力。"""

    def _external_email_item(self, email: str) -> dict:
        email = (email or "").strip()
        return {"UNO": "", "N": email, "E": email, "T": ""}

    def search_users(
        self,
        to_keys: list,
        cc_keys: list,
        bcc_keys: list = None,
        contact_type: str = "user",
    ) -> dict:
        """
        按关键字或邮箱搜索收件人/抄送人/密送人/群组。收件人、抄送人、密送人、群组均须通过此接口解析后再参与发送。
        :param to_keys: 收件人关键字或邮箱列表
        :param cc_keys: 抄送人关键字或邮箱列表
        :param bcc_keys: 密送人关键字或邮箱列表
        :param contact_type: "user" 或 "group"
        :return: 与 main search-users 子命令一致的 result 结构
        """
        to_keys = [k.strip() for k in (to_keys or []) if k and str(k).strip()]
        cc_keys = [k.strip() for k in (cc_keys or []) if k and str(k).strip()]
        bcc_keys = [k.strip() for k in (bcc_keys or []) if k and str(k).strip()]

        def search_keys_for_groups(keys):
            return [{"keyword": kw, "groups": self._request.search_user(kw, "group")} for kw in keys]

        def search_keys_for_users(keys):
            result = []
            for kw in keys:
                if is_email_address(kw):
                    result.append({"keyword": kw, "users": [self._external_email_item(kw)]})
                else:
                    result.append({"keyword": kw, "users": self._request.search_user(kw, "user")})
            return result

        def format_users_output(searches):
            return [
                {"keyword": item.get("keyword", ""), "users": [
                    {"UNO": u.get("UNO", ""), "N": u.get("N", ""), "E": u.get("E", ""), "T": u.get("T", "")}
                    for u in (item.get("users") or [])
                ]}
                for item in searches
            ]

        if contact_type == "group":
            to_group_searches = search_keys_for_groups(to_keys)
            cc_group_searches = search_keys_for_groups(cc_keys)
            bcc_group_searches = search_keys_for_groups(bcc_keys)
            all_group_searches = to_group_searches + cc_group_searches + bcc_group_searches
            keywords_with_multiple = {}
            for item in all_group_searches:
                groups = item.get("groups") or []
                if len(groups) > 1:
                    keywords_with_multiple[item.get("keyword", "")] = [
                        {"N": g.get("N", ""), "T": g.get("T", "")} for g in groups
                    ]
            return {
                "status": True,
                "type": "group",
                "to_group_searches": to_group_searches,
                "cc_group_searches": cc_group_searches,
                "bcc_group_searches": bcc_group_searches,
                "keywords_with_multiple": keywords_with_multiple,
                "no_match_keys": [kw for kw, g in zip(to_keys, [x["groups"] for x in to_group_searches]) if not g],
                "no_match_cc_keys": [kw for kw, g in zip(cc_keys, [x["groups"] for x in cc_group_searches]) if not g],
                "no_match_bcc_keys": [kw for kw, g in zip(bcc_keys, [x["groups"] for x in bcc_group_searches]) if not g],
            }

        # type == "user"
        to_searches = search_keys_for_users(to_keys)
        cc_searches = search_keys_for_users(cc_keys)
        bcc_searches = search_keys_for_users(bcc_keys)

        keywords_with_multiple = {}
        for item in to_searches + cc_searches + bcc_searches:
            users = item.get("users") or []
            if len(users) > 1:
                keywords_with_multiple[item.get("keyword", "")] = [
                    {"UNO": u.get("UNO", ""), "N": u.get("N", ""), "T": u.get("T", "")} for u in users
                ]
        no_match_keys = [item.get("keyword", "") for item in to_searches + cc_searches + bcc_searches if not item.get("users")]
        return {
            "status": True,
            "type": "user",
            "to_searches": format_users_output(to_searches),
            "cc_searches": format_users_output(cc_searches),
            "bcc_searches": format_users_output(bcc_searches),
            "keywords_with_multiple": keywords_with_multiple,
            "no_match_keys": no_match_keys,
        }
