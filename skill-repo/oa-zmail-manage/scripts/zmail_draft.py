#!/usr/bin/env python3
"""zMail 预约发送、草稿箱操作（定时/立即/取消/列表/删除）。"""

import json
import os
import re

from enums import MailboxId
from util import ats_to_list, format_mt_list, format_timestamp, is_valid_schedule_time, to_json_object, zmail_log, zmail_log_warning
from zmail_reply_forward_base import extract_inline_attach_ids
from zmail_send_draft_base import ZmailSendDraftBase
from zmail_attachment import ZmailAttachment


# 草稿箱 SysGetMailL 的 F 列表
_GET_MAIL_F_DRAFT = [{"K": "T", "V": MailboxId.DRAFT.value}, {"K": "F", "V": "1"}, {"K": "D", "V": "2"}, {"K": "MT", "V": "0"}]


class ZmailDraft(ZmailSendDraftBase):
    """预约发送新邮件、草稿箱定时/立即/取消/列表/删除。"""

    @staticmethod
    def _looks_like_file_path(value: str) -> bool:
        """粗略判断字符串是否像文件路径，兼容 Windows/Linux 常见写法。"""
        s = str(value or "").strip()
        if not s:
            return False
        if s.startswith(("/", "./", "~/", ".\\")):
            return True
        if re.match(r"^[a-zA-Z]:[\\/]", s):
            return True
        if s.startswith("\\\\"):
            return True
        if "/" in s or "\\" in s:
            return True
        if os.path.splitext(s)[1]:
            return True
        return False

    # ========== 新建邮件预约发送 ==========

    def create_draft(
        self,
        title: str,
        to_list: list,
        cc_list: list,
        body: str,
        send_at=None,
        bcc_list: list = None,
        attachments: list = None,
        is_inline: str = "0",
    ) -> dict:
        """有 send_at 则预约发送，无则保存为草稿（不发送）。attachments：本地文件路径列表（可选），发送前校验合法性。
        is_inline：'1' 时将附件中的图片插入正文（仅图片生效），'0' 或 '1'，默认 '0'（与 send 一致）。
        """
        try:
            if send_at and not (title and str(title).strip()):
                return {"status": False, "error": "指定具体发送时间时邮件必须填写主题。"}
            if attachments:
                att = ZmailAttachment()
                ok, failures = att.validate_files(attachments)
                if not ok:
                    reasons = "; ".join(f"{p}: {r}" for p, r in failures)
                    return {"status": False, "error": f"附件校验失败: {reasons}"}
            ctx, err = self._prepare_new_mail(
                title, body, to_list, cc_list,
                bcc_list=bcc_list,
                allow_empty_recipients=(send_at is None),
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
            if send_at:
                valid, reason = is_valid_schedule_time(send_at)
                if not valid:
                    return {"status": False, "error": reason}
                send_at_ts = self._parse_send_at(send_at)
                D["ISE"] = "1"
                D["SSD"] = send_at_ts
                D["_pageID"] = f"writeMail_{ctx['new_mail_id']}"
            else:
                D["T"] = MailboxId.DRAFT.value
            resp = self._request.send_email(D)
            if not resp.get("S"):
                msg = resp.get("M") or resp.get("C") or "SysSendEmail 失败"
                return {"status": False, "error": f"zmail SysSendEmail 未成功: {msg}"}
            mid = (resp.get("D") or {}).get("mid") or ctx["new_mail_id"]
            result = {"status": True, "mid": mid}
            if send_at:
                result["send_at"] = send_at_ts
            return result
        except Exception as e:
            return {"status": False, "error": str(e)}

    def _prepare_save_draft_with_id(
        self,
        draft_id: str,
        title: str,
        to_list: list,
        cc_list: list,
        body: str,
        bcc_list: list = None,
        attachments: list = None,
    ) -> tuple:
        """
        传入 draft_id 时的独立处理：不新建 ID，按该草稿保存。
        当 title/body/收件人信息/附件任一缺失时，从草稿详情回填对应字段。

        定时策略：保存请求固定为普通草稿（T=2、ISE=0，不含 SSD），不保留原「定时发送」。
        若用户仍需定时，须在 save 成功后另行调用 operate-draft --at（原时间或新时间）。

        返回 (D, removed_attach_ids, err_dict_or_None)。
        """
        draft_data = self._get_draft_content(draft_id)
        if not isinstance(draft_data, dict) or not draft_data.get("ID"):
            return None, None, {"status": False, "error": "获取草稿内容失败或草稿不存在。"}

        # 规范化入参
        to_list, cc_list, bcc_list = self._normalize_lists(
            to_list, cc_list, bcc_list or [],
        )
        has_recipient_input = bool(to_list or cc_list or bcc_list)

        # 基础 D 先按草稿详情构建，后续按传参与回填覆盖
        D = self._build_draft_base_d(draft_data)
        D["T"] = MailboxId.DRAFT.value
        D["ISE"] = "0"
        D.pop("SSD", None)

        # 标题回填
        final_title = (title or "").strip() or (draft_data.get("TI") or draft_data.get("LPN") or "")
        D["TI"] = final_title

        # 正文回填：传了 body 用新正文；没传则保留草稿原 MCT
        if body and str(body).strip():
            D["MCT"] = self._build_send_content(body or "")
        else:
            D["MCT"] = draft_data.get("MCT") or ""

        # 收件人回填：若传入任一收件参数则按传入重建 MT/MC/MM；否则沿用草稿原值
        if has_recipient_input:
            to_resolved, cc_resolved, bcc_resolved, err = self._resolve_all_recipients(
                to_list, cc_list, bcc_list,
            )
            if err:
                return None, None, {"status": False, "error": err}
            mt, mc = self._build_mt_mc(to_resolved or [], cc_resolved or [])
            mm = self._build_mm(bcc_resolved or [])
            D["MT"] = json.dumps(mt, ensure_ascii=False)
            D["MC"] = json.dumps(mc, ensure_ascii=False) if mc else None
            D["MM"] = json.dumps(mm, ensure_ascii=False) if mm else None
        else:
            D["MT"] = self._draft_to_json_str(draft_data.get("MT"), "[]")
            D["MC"] = self._mc_field(draft_data.get("MC"))
            mm_raw = draft_data.get("MM")
            if mm_raw is None or (isinstance(mm_raw, str) and mm_raw.strip() == "null"):
                D["MM"] = None
            else:
                D["MM"] = self._draft_to_json_str(mm_raw, "[]")

        removed_attach_ids = []

        # 附件回填：传 draft_id 时先取草稿详情，再按 delete_ids / upload_paths 修改原 ATS；
        # 未传 attachments 或传 null 时沿用草稿 ATS。旧附件删除放到草稿保存成功后再执行。
        if attachments is not None:
            ats_list, removed_attach_ids, err = self._rebuild_draft_attachments(draft_id, draft_data, attachments)
            if err:
                return None, None, err
            D["ATS"] = json.dumps(ats_list, ensure_ascii=False) if ats_list else "[]"
        else:
            D["ATS"] = self._draft_to_json_str(draft_data.get("ATS"), "[]")

        return D, removed_attach_ids, None

    def _rebuild_draft_attachments(self, draft_id: str, draft_data: dict, attachments: list) -> tuple:
        """
        编辑草稿时重建附件集合：
        - 从原 ATS 中删除 delete_ids 指定的附件；
        - 将 upload_paths 指定的本地文件上传后追加到 ATS；
        - 其余原附件默认保留。
        返回 (final_ats_list, removed_attach_ids, err_dict_or_None)。
        """
        attachments = attachments or {}
        original_ats_list = ats_to_list(draft_data.get("ATS") or "")
        original_by_id = {}
        for item in original_ats_list:
            if not isinstance(item, dict):
                continue
            attach_id = str(item.get("ID") or item.get("id") or "").strip()
            if attach_id:
                original_by_id[attach_id] = item

        delete_ids_input = attachments.get("delete_ids") or []
        upload_paths_input = attachments.get("upload_paths") or []

        delete_id_set = set()
        for raw in delete_ids_input:
            val = str(raw or "").strip()
            if not val:
                continue
            if val not in original_by_id:
                zmail_log_warning("save_draft 跳过不存在于原附件列表中的待删除附件ID: {}", val)
                continue
            delete_id_set.add(val)

        keep_original = []
        for item in original_ats_list:
            if not isinstance(item, dict):
                continue
            attach_id = str(item.get("ID") or item.get("id") or "").strip()
            if not attach_id or attach_id in delete_id_set:
                continue
            keep_original.append(item)

        upload_paths = []
        for raw in upload_paths_input:
            val = str(raw or "").strip()
            if not val:
                continue
            if not self._looks_like_file_path(val):
                zmail_log_warning("save_draft 跳过无效附件路径: {}", val)
                continue
            expanded_path = os.path.abspath(os.path.expanduser(val))
            if not os.path.exists(expanded_path):
                return None, None, {"status": False, "error": "要上传的附件文件不存在: {}".format(val)}
            upload_paths.append(val)

        uploaded_ats = []
        if upload_paths:
            att = ZmailAttachment()
            ok, failures = att.validate_files(upload_paths)
            if not ok:
                reasons = "; ".join(f"{p}: {r}" for p, r in failures)
                return None, None, {"status": False, "error": f"附件校验失败: {reasons}"}
            upload_result = att.upload_attach(
                draft_id,
                upload_paths,
                start_index=self._next_upload_index(original_ats_list, draft_id),
            )
            if isinstance(upload_result, dict) and not upload_result.get("status", True):
                return None, None, upload_result
            uploaded_ats = upload_result if isinstance(upload_result, list) else []

        final_ats_list = keep_original + uploaded_ats
        removed_attach_ids = list(delete_id_set)
        return final_ats_list, removed_attach_ids, None

    def _next_upload_index(self, original_ats_list: list, draft_id: str) -> int:
        """根据服务端编号与原草稿 ATS 取较大值，再加 1 作为新增附件起始序号。"""
        draft_id = str(draft_id or "").strip()
        if not draft_id:
            return 1
        local_max_index = 0
        for item in original_ats_list or []:
            if not isinstance(item, dict):
                continue
            attach_id = str(item.get("ID") or item.get("id") or "").strip()
            if not attach_id or not attach_id.startswith(draft_id):
                continue
            suffix = attach_id[len(draft_id):]
            if len(suffix) != 5 or not suffix.isdigit():
                continue
            local_max_index = max(local_max_index, int(suffix))

        server_att_no = 0
        try:
            server_att_no = int(self._request.get_attach_no(draft_id) or 0)
            zmail_log("save_draft SysGetATTNo success | draft_id={} | server_att_no={}", draft_id, server_att_no)
        except Exception:
            zmail_log_warning("save_draft SysGetATTNo failed | draft_id={}", draft_id)
            server_att_no = 0

        start_index = max(local_max_index, server_att_no) + 1
        zmail_log(
            "save_draft attachment index resolved | draft_id={} | local_max_index={} | server_att_no={} | start_index={}",
            draft_id,
            local_max_index,
            server_att_no,
            start_index,
        )
        return start_index

    def _delete_removed_attachments(self, removed_attach_ids: list):
        """删除原草稿里已不在最终附件集合中的旧附件。"""
        delete_errors = []
        for attach_id in removed_attach_ids or []:
            attach_id = str(attach_id or "").strip()
            if not attach_id:
                continue
            try:
                resp = self._request.delete_attach(attach_id)
            except Exception as e:
                delete_errors.append("{}: {}".format(attach_id, str(e)))
                continue
            if not resp.get("S"):
                msg = resp.get("M") or resp.get("C") or "SysDeleteAttach 失败"
                delete_errors.append("{}: {}".format(attach_id, msg))
        if delete_errors:
            return {"status": False, "error": "删除草稿旧附件失败: " + "; ".join(delete_errors)}
        return None

    def save_draft(
        self,
        title: str,
        to_list: list,
        cc_list: list,
        body: str,
        bcc_list: list = None,
        attachments: list = None,
        draft_id: str = None,
        is_inline: str = "0",
    ) -> dict:
        """
        保存草稿：
        - 未传 draft_id：新建草稿；可将图片插入正文（is_inline，与 create_draft 一致）。
        - 传 draft_id：按该草稿 ID 保存，不新建 ID；缺失参数按草稿详情回填。
          编辑保存后始终为普通草稿（清除定时）；若要继续定时发送须再调用 operate-draft --at。
          编辑分支不支持将图片插入正文（is_inline 非 \"0\" 会报错）。
        """
        try:
            is_inline = str(is_inline or "0").strip() or "0"
            if draft_id and str(draft_id).strip():
                if is_inline != "0":
                    return {
                        "status": False,
                        "error": "将图片插入正文仅适用于新建草稿；编辑已有草稿时仅支持普通附件上传。",
                    }
                D, removed_attach_ids, err = self._prepare_save_draft_with_id(
                    draft_id=str(draft_id).strip(),
                    title=title,
                    to_list=to_list,
                    cc_list=cc_list,
                    body=body,
                    bcc_list=bcc_list,
                    attachments=attachments,
                )
                if err:
                    return err
                resp = self._request.send_email(D)
                if not resp.get("S"):
                    msg = resp.get("M") or resp.get("C") or "SysSendEmail 失败"
                    return {"status": False, "error": f"zmail SysSendEmail 未成功: {msg}"}
                err = self._delete_removed_attachments(removed_attach_ids)
                if err:
                    return err
                return {"status": True, "mid": str(draft_id).strip()}

            # 未传 draft_id 时沿用原有新建草稿流程
            return self.create_draft(
                title=title,
                to_list=to_list,
                cc_list=cc_list,
                body=body,
                send_at=None,
                bcc_list=bcc_list,
                attachments=attachments,
                is_inline=is_inline,
            )
        except Exception as e:
            return {"status": False, "error": str(e)}

    # ========== 草稿箱列表 / 删除 ==========

    def get_draft_list(self, pno: int = 1, psize: int = 200) -> dict:
        """草稿箱列表，返回 {count, list: [{id, ssd, title, mt, date}]}。"""
        try:
            resp = self._request.get_mail_list(
                T=0, F=_GET_MAIL_F_DRAFT, P={"S": "1970-01-01 08:00:00", "E": ""}, pno=pno, psize=psize
            )
            if not resp.get("S"):
                return {"count": 0, "list": []}
            d_list = resp.get("D") or []
            if not isinstance(d_list, list):
                return {"count": 0, "list": []}
            out = []
            for item in d_list:
                if not isinstance(item, dict):
                    continue
                mid = item.get("ID") or ""
                if not mid:
                    continue
                ssd_raw = item.get("SSD") or item.get("ssd")
                ssd = format_timestamp(ssd_raw) if ssd_raw else ""
                title = item.get("TI") or ""
                lud_raw = item.get("LUD") or ""
                date = format_timestamp(lud_raw)
                mt_raw = item.get("MT")
                mt_parsed = to_json_object(mt_raw) if isinstance(mt_raw, str) else mt_raw
                if mt_parsed is not None and not isinstance(mt_parsed, list):
                    mt_parsed = [mt_parsed] if mt_parsed else []
                mt = format_mt_list(mt_parsed or [])
                out.append({"id": mid, "ssd": ssd, "title": title, "mt": mt, "date": date})
            return {"count": len(out), "list": out}
        except Exception:
            return {"count": 0, "list": []}

    # ========== 草稿操作（定时/立即/取消） ==========

    def _get_draft_content(self, draft_id: str) -> dict:
        if not draft_id or not str(draft_id).strip():
            return {}
        try:
            return self._request.get_mail_context(str(draft_id).strip())
        except Exception:
            return {}

    def _draft_to_json_str(self, v, default: str = "[]") -> str:
        """MF/MT/MC/ATS 等转为 SysSendEmail D 所需的 JSON 字符串。"""
        if v is None:
            return default
        if isinstance(v, str):
            if v.strip() == "null":
                return default
            return v
        return json.dumps(v, ensure_ascii=False)

    def _mc_field(self, mc_val):
        """草稿 MC 字段：null 值保持 None，否则转 JSON 字符串。"""
        if mc_val is None or (isinstance(mc_val, str) and mc_val.strip() == "null"):
            return None
        return self._draft_to_json_str(mc_val, "[]")

    def _build_draft_base_d(self, draft_data: dict) -> dict:
        """草稿 D 公共字段。"""
        id_val = draft_data.get("ID") or ""
        return {
            "ID": id_val,
            "RID": id_val,
            "MCF": "0",
            "TI": draft_data.get("TI") or draft_data.get("LPN") or "",
            "IP": "0",
            "IT": "0",
            "MF": self._draft_to_json_str(draft_data.get("MF"), "{}"),
            "MT": self._draft_to_json_str(draft_data.get("MT"), "[]"),
            "MC": self._mc_field(draft_data.get("MC")),
            "MM": None,
            "MCT": draft_data.get("MCT") or "",
            "ATS": self._draft_to_json_str(draft_data.get("ATS"), "[]"),
            "PL": "3",
            "IL": "0",
            "INR": "0",
            "SBFLAG": "0",
            "MSB": "0",
            "MSender": None,
            "UAL": 0,
            "AE": "0",
            "IFM": "0",
            "MDEF": 0,
            "_pageID": f"writeMail_{id_val}",
            "LATS": "[]",
        }

    def _build_draft_schedule_d(self, draft_data: dict, send_at_ts: int) -> dict:
        """构建在指定时间发送草稿的 D（T=3，ISE=1，SSD）。"""
        d = self._build_draft_base_d(draft_data)
        d["T"] = MailboxId.OUTBOX.value
        d["ISE"] = "1"
        d["SSD"] = send_at_ts
        return d

    def _build_draft_send_now_d(self, draft_data: dict) -> dict:
        """构建立即发送草稿的 D（T=3，ISE=0，无 SSD）。"""
        d = self._build_draft_base_d(draft_data)
        d["T"] = MailboxId.OUTBOX.value
        d["ISE"] = "0"
        return d

    def _build_draft_cancel_d(self, draft_data: dict, send_at_ts) -> dict:
        """构建取消已设发送时间的 D（T=3，ISE=2）。"""
        d = self._build_draft_schedule_d(draft_data, send_at_ts)
        d["ISE"] = "2"
        return d

    def send_draft_now(self, draft_id: str) -> dict:
        """立即发送草稿。"""
        try:
            if not draft_id or not str(draft_id).strip():
                return {"status": False, "error": "草稿 ID 不能为空。"}
            draft_data = self._get_draft_content(draft_id)
            if not isinstance(draft_data, dict) or not draft_data.get("ID"):
                return {"status": False, "error": "获取草稿内容失败或草稿不存在。"}
            if not draft_data.get("MT"):
                return {"status": False, "error": "草稿无收件人。"}
            D = self._build_draft_send_now_d(draft_data)
            resp = self._request.send_email(D)
            if not resp.get("S"):
                msg = resp.get("M") or resp.get("C") or "SendEmail 失败"
                return {"status": False, "error": f"zmail 发送邮件未成功: {msg}"}
            return {"status": True}
        except Exception as e:
            return {"status": False, "error": str(e)}

    def schedule_draft_send(self, draft_id: str, send_at=None) -> dict:
        """在指定时间发送草稿。"""
        try:
            if not draft_id or not str(draft_id).strip():
                return {"status": False, "error": "草稿 ID 不能为空。"}
            draft_data = self._get_draft_content(draft_id)
            if not isinstance(draft_data, dict) or not draft_data.get("ID"):
                return {"status": False, "error": "获取草稿内容失败或草稿不存在。"}
            if not draft_data.get("MT"):
                return {"status": False, "error": "草稿无收件人。"}
            valid, reason = is_valid_schedule_time(send_at)
            if not valid:
                return {"status": False, "error": reason}
            send_at_ts = self._parse_send_at(send_at)
            D = self._build_draft_schedule_d(draft_data, send_at_ts)
            resp = self._request.send_email(D)
            if not resp.get("S"):
                msg = resp.get("M") or resp.get("C") or "SendEmail 失败"
                return {"status": False, "error": f"zmail 发送邮件未成功: {msg}"}
            return {"status": True}
        except Exception as e:
            return {"status": False, "error": str(e)}

    def cancel_draft_schedule(self, draft_id: str) -> dict:
        """取消草稿已设的发送时间。"""
        try:
            if not draft_id or not str(draft_id).strip():
                return {"status": False, "error": "草稿 ID 不能为空。"}
            draft_data = self._get_draft_content(draft_id)
            if not isinstance(draft_data, dict) or not draft_data.get("ID"):
                return {"status": False, "error": "获取草稿内容失败或草稿不存在。"}
            if not draft_data.get("MT"):
                return {"status": False, "error": "草稿无收件人。"}
            existing_ssd = draft_data.get("SSD") or draft_data.get("ssd") or ""
            if not existing_ssd:
                return {"status": False, "error": "该草稿未设置发送时间，无需取消。"}
            D = self._build_draft_cancel_d(draft_data, existing_ssd)
            resp = self._request.send_email(D)
            if not resp.get("S"):
                msg = resp.get("M") or resp.get("C") or "SendEmail 失败"
                return {"status": False, "error": f"zmail 发送邮件未成功: {msg}"}
            return {"status": True}
        except Exception as e:
            return {"status": False, "error": str(e)}
