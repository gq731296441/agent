#!/usr/bin/env python3
"""zMail 文件夹：列表、按名称/ID 查找、移动邮件。"""

from typing import Dict, List, Optional, Union

from enums import MailboxId
from zmail_base import ZmailBase
from util import is_history_mail_id, validate_mail_id

_DELETE_VIA_DELETE_MAILS_MSG = (
    "移入已删除（回收站）必须使用 delete_mails，禁止使用 move-emails。"
)


class ZmailFolder(ZmailBase):
    """查看文件夹列表，返回 JSON。"""

    def _get_folder_data(self) -> tuple:
        """获取文件夹数据。返回 (flat, id_map)：flat 为 EF='Y' 的 [{id,pid,n}]，id_map 为全部目录的 id->{pid,n}（用于路径构建）。"""
        raw = self._request.get_folder_list()
        if not raw:
            return [], {}
        flat = []
        id_map = {}
        for f in raw:
            if not isinstance(f, dict) or f.get("ID") is None:
                continue
            fid = str(f["ID"])
            pid = str(f.get("PID") or 0)
            n = (f.get("N") or "").strip()
            id_map[fid] = {"pid": pid, "n": n}
            if f.get("EF") == "Y":
                flat.append({"id": fid, "pid": pid, "n": n})
        return flat, id_map

    def _build_path(self, fid: str, id_map: Dict[str, dict]) -> str:
        """根据 id_map 递归构建完整路径。"""
        if fid not in id_map:
            return ""
        f = id_map[fid]
        pid, n = f["pid"], f["n"]
        if pid == "0" or pid not in id_map:
            return n
        parent = self._build_path(pid, id_map)
        return f"{parent}/{n}" if parent else n

    def get_folder_list(self) -> dict:
        """返回 {folders: [{id, folder}, ...], count}。"""
        flat, id_map = self._get_folder_data()
        if not flat:
            return {"folders": [], "count": 0}
        folders = [
            {"id": f["id"], "folder": self._build_path(f["id"], id_map)}
            for f in flat
        ]
        return {"folders": folders, "count": len(folders)}

    def get_folder_id_by_name(self, folder_name: str) -> Optional[Dict[str, str]]:
        """
        根据文件夹名称或 ID 查找。纯数字时优先按 id 匹配，否则按名称匹配（先找父再找子）。
        :return: 找到时 {"id": "文件夹ID", "folder": "文件夹名称（有父子则为 父/子）"}，未找到时 None。
        """
        needle = (folder_name or "").strip()
        if not needle:
            return None
        flat, id_map = self._get_folder_data()
        if needle.isdigit():
            needle_str = str(needle)
            for f in flat:
                if str(f["id"]) == needle_str:
                    path = self._build_path(needle_str, id_map)
                    return {"id": needle_str, "folder": path or needle_str}
        candidates = []
        for f in flat:
            if f["n"] != needle:
                continue
            is_child, depth = False, 0
            curr = f["id"]
            while curr in id_map:
                if depth > 0 and id_map[curr]["n"] == needle:
                    is_child = True
                depth += 1
                pid = id_map[curr]["pid"]
                if pid == "0" or pid not in id_map:
                    break
                curr = pid
            candidates.append((is_child, depth, f["id"]))
        if not candidates:
            return None
        candidates.sort()
        fid = candidates[0][2]
        path = self._build_path(fid, id_map)
        return {"id": fid, "folder": path or ""}

    def delete_emails(self, email_ids: Union[str, List[str]]) -> dict:
        """
        删除邮件（移入已删除/回收站）：统一走 ``delete-mails`` 接口，**不使用** ``move-emails`` / ``move_mails_to_folder``。
        """
        if not email_ids:
            return {"status": False, "error": "不存在需要删除的邮件"}
        ids: List[str] = []
        if isinstance(email_ids, str):
            ids = [x.strip() for x in email_ids.split(",") if x and x.strip()]
        elif isinstance(email_ids, list):
            ids = [str(x).strip() for x in email_ids if x is not None and str(x).strip()]
        if not ids:
            return {"status": False, "error": "不存在需要删除的邮件"}

        target_name = "已删除(回收站)"
        success_count = 0
        fail_count = 0
        errors: List[str] = []

        ids_to_delete: List[str] = []
        for mid in ids:
            try:
                if is_history_mail_id(mid):
                    errors.append(f"{mid}: 历史邮件/归档邮件不能删除")
                    fail_count += 1
                    continue
                d = self._request.get_mail_context(mid)
                old_folder_id = str(d.get("BID") or "").strip()
                if not old_folder_id:
                    errors.append(f"{mid}: 无法获取邮件所在文件夹")
                    fail_count += 1
                    continue

                if old_folder_id == MailboxId.DELETED.value:
                    errors.append(f"{mid}: 已删除（回收站）中的邮件不支持再次删除")
                    fail_count += 1
                    continue
                ids_to_delete.append(mid)
            except Exception as e:
                errors.append(f"{mid}: {str(e)}")
                fail_count += 1

        if ids_to_delete:
            try:
                resp = self._request.delete_mails(ids_to_delete)
                if resp.get("S"):
                    success_count = len(ids_to_delete)
                else:
                    msg = resp.get("M") or resp.get("C") or "删除失败"
                    errors.append(f"删除失败：{msg}")
                    fail_count += len(ids_to_delete)
                    success_count = 0
            except Exception as e:
                errors.append(f"删除失败：{str(e)}")
                fail_count += len(ids_to_delete)
                success_count = 0

        if fail_count == 0:
            return {"status": True, "message": f"已成功将{success_count}封邮件移动到{target_name}", "success_count": success_count, "fail_count": 0}
        if success_count == 0:
            return {"status": False, "error": "；".join(errors), "success_count": 0, "fail_count": fail_count}
        return {
            "status": True,
            "message": f"已成功将{success_count}封邮件移动到{target_name}，{fail_count}封失败",
            "success_count": success_count,
            "fail_count": fail_count,
            "errors": errors,
        }

    def move_emails(
        self,
        email_ids: Union[str, List[str]],
        mailbox_type: str = "inbox",
        folder: str = "",
    ) -> dict:
        """
        将邮件移动到指定箱体/文件夹（**不含**移入已删除；删除请用 ``delete_emails`` / ``delete-mails``）。支持单个 ID、逗号分隔字符串或 ID 列表。
        :param email_ids: 单个 ID、逗号分隔字符串或 ID 列表。
        :param mailbox_type: 无 --folder 时支持 inbox（收件箱=1）、spam（垃圾邮件=5）；传 --folder 时填 folder 并以 folder 参数解析目标文件夹。
        :param folder: 文件夹名称或文件夹 ID（纯数字），仅 mailbox_type 为 folder 时有效。
        :return: 成功时 {status: True, message, success_count, fail_count}；失败时 {status: False, error}。
        """
        if not email_ids:
            return {"status": False, "error": "不存在需要移动的邮件"}
        ids: List[str] = []
        if isinstance(email_ids, str):
            ids = [x.strip() for x in email_ids.split(",") if x and x.strip()]
        elif isinstance(email_ids, list):
            ids = [str(x).strip() for x in email_ids if x is not None and str(x).strip()]
        if not ids:
            return {"status": False, "error": "不存在需要移动的邮件"}

        mt_lower = (mailbox_type or "").strip().lower()
        if mt_lower == "deleted":
            raise ValueError(_DELETE_VIA_DELETE_MAILS_MSG)

        folder_str = (folder or "").strip()
        if folder_str:
            folder_result = self.get_folder_id_by_name(folder_str)
            if folder_result is None:
                return {"status": False, "error": f"名称「{folder_str}」的文件夹不存在", "folder_data": self.get_folder_list()}
            new_folder_id = folder_result["id"]
            target_name = folder_result["folder"] or folder_str or "指定文件夹"
        else:
            if mt_lower == "inbox":
                new_folder_id = MailboxId.INBOX.value
                target_name = "收件箱"
            elif mt_lower == "spam":
                new_folder_id = MailboxId.SPAM.value
                target_name = "垃圾邮件"
            elif mt_lower == "folder":
                return {"status": False, "error": "mailbox_type 为 folder 时必须指定 --folder 参数"}
            else:
                return {"status": False, "error": f"不支持的箱体类型: {mailbox_type}，支持 inbox、spam、folder"}

        # 系统「已删除」箱体 ID=4：移入回收站须走 delete_mails，不得走 move_mails_to_folder
        if str(new_folder_id).strip() == MailboxId.DELETED.value:
            raise ValueError(_DELETE_VIA_DELETE_MAILS_MSG)

        success_count = 0
        fail_count = 0
        errors: List[str] = []

        for mid in ids:
            try:
                d = self._request.get_mail_context(mid)
                old_folder_id = str(d.get("BID") or "").strip()
                if not old_folder_id:
                    errors.append(f"{mid}: 无法获取邮件所在文件夹")
                    fail_count += 1
                    continue
                # 草稿箱(2)、发件箱(3)、已删除(4) 中的邮件不支持移动
                if old_folder_id == MailboxId.DRAFT.value:
                    errors.append(f"{mid}: 位于草稿箱的邮件无法被移动")
                    fail_count += 1
                    continue
                if old_folder_id == MailboxId.OUTBOX.value:
                    errors.append(f"{mid}: 位于发件箱的邮件无法被移动")
                    fail_count += 1
                    continue
                if old_folder_id == MailboxId.DELETED.value:
                    errors.append(f"{mid}: 位于已删除箱体的邮件无法被移动")
                    fail_count += 1
                    continue
                if new_folder_id == old_folder_id:
                    errors.append(f"{mid}: 已在目标文件夹中，无需移动")
                    fail_count += 1
                    continue
                resp = self._request.move_mails_to_folder(
                    mail_ids=[mid],
                    old_folder_id=old_folder_id,
                    new_folder_id=new_folder_id,
                )
                if resp.get("S"):
                    success_count += 1
                else:
                    errors.append(f"{mid}: {resp.get('M') or resp.get('C') or '移动失败'}")
                    fail_count += 1
            except Exception as e:
                errors.append(f"{mid}: {str(e)}")
                fail_count += 1

        if fail_count == 0:
            return {"status": True, "message": f"已成功将{success_count}封邮件移动到{target_name}", "success_count": success_count, "fail_count": 0}
        if success_count == 0:
            return {"status": False, "error": "；".join(errors), "success_count": 0, "fail_count": fail_count}
        return {
            "status": True,
            "message": f"已成功将{success_count}封邮件移动到{target_name}，{fail_count}封失败",
            "success_count": success_count,
            "fail_count": fail_count,
            "errors": errors,
        }

    def recover_mails(self, email_ids: Union[str, List[str]]) -> dict:
        """
        从“已删除(回收站)”恢复邮件。支持单个 ID、逗号分隔字符串或 ID 列表。

        约束：仅“已删除”箱体下的邮件可以恢复；其它箱体视为失败并给出原因。
        :return: 成功时 {status: True, message, success_count, fail_count}；失败时 {status: False, error}。
        """
        if not email_ids:
            return {"status": False, "error": "不存在需要恢复的邮件"}
        ids: List[str] = []
        if isinstance(email_ids, str):
            ids = [x.strip() for x in str(email_ids).split(",") if x and x.strip()]
        elif isinstance(email_ids, list):
            ids = [str(x).strip() for x in email_ids if x is not None and str(x).strip()]
        if not ids:
            return {"status": False, "error": "不存在需要恢复的邮件"}

        success_count = 0
        fail_count = 0
        errors: List[str] = []
        ids_to_recover: List[str] = []

        for mid in ids:
            try:
                if is_history_mail_id(mid):
                    errors.append(f"{mid}: 仅支持普通（在线）邮件ID，不支持历史邮件ID")
                    fail_count += 1
                    continue
                ok, reason = validate_mail_id(mid)
                if not ok:
                    errors.append(f"{mid}: {reason or '邮件 ID 无效'}")
                    fail_count += 1
                    continue
                d = self._request.get_mail_context(mid)
                old_folder_id = str(d.get("BID") or "").strip()
                if not old_folder_id:
                    errors.append(f"{mid}: 无法获取邮件所在文件夹")
                    fail_count += 1
                    continue
                if old_folder_id != MailboxId.DELETED.value:
                    errors.append(f"{mid}: 仅“已删除/回收站”下的邮件可以恢复")
                    fail_count += 1
                    continue
                ids_to_recover.append(mid)
            except Exception as e:
                errors.append(f"{mid}: {str(e)}")
                fail_count += 1

        if ids_to_recover:
            try:
                resp = self._request.recover_mails(ids_to_recover)
                if resp.get("S"):
                    succ = None
                    if isinstance(resp.get("D"), dict):
                        d = resp.get("D") or {}
                        succ = d.get("successNum") or d.get("sucessNum")
                    try:
                        succ_int = int(succ) if succ is not None else 0
                    except (TypeError, ValueError):
                        succ_int = 0
                    succ_int = max(0, min(succ_int, len(ids_to_recover)))
                    success_count = succ_int
                    fail_count += (len(ids_to_recover) - succ_int)
                else:
                    msg = resp.get("M") or resp.get("C") or "恢复失败"
                    errors.append(f"恢复失败：{msg}")
                    fail_count += len(ids_to_recover)
                    success_count = 0
            except Exception as e:
                errors.append(f"恢复失败：{str(e)}")
                fail_count += len(ids_to_recover)
                success_count = 0

        if fail_count == 0:
            return {
                "status": True,
                "message": f"已成功恢复{success_count}封邮件",
                "success_count": success_count,
                "fail_count": 0,
            }
        if success_count == 0:
            return {"status": False, "error": "；".join(errors), "success_count": 0, "fail_count": fail_count}
        return {
            "status": True,
            "message": f"已成功恢复{success_count}封邮件，{fail_count}封失败",
            "success_count": success_count,
            "fail_count": fail_count,
            "errors": errors,
        }
