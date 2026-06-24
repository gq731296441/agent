#!/usr/bin/env python3
"""
zMail 请求统一管理：工号、token、所有 pcmail/pccontacts API 调用。
供 zmail_base 子类及 main 子命令使用。

兼容 Python 3.6+。
"""

import json
import requests
import time
import urllib.parse
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

from net_error import build_zmail_network_error
from enums import MailboxId
from request_client import request_post
from api_endpoints import (
    get_zmail_download_url,
    get_zmail_historymail_url,
    get_zmail_pcmail_url,
    get_zmail_pccontacts_url,
    get_zmail_pcfolder_url,
    get_zmail_pconline_read_url,
    get_zmail_pcrecall_url,
    get_zmail_upload_file_url,
    get_zmail_upload_url,
)
from api_token import get_zmail_token
from util import (
    SEARCH_DAYS,
    is_email_address,
    is_history_mail_id,
    log_api_error,
    validate_datetime_param,
    validate_positive_int,
)



class ZmailRequestManager:
    """
    zMail API 请求管理器。持有工号、token，统一发起 pcmail / pccontacts 请求。
    构造时自动调用 ensure_token() 获取 token 及 URL，同一实例内只取一次并缓存。
    """

    def __init__(self):
        self._emp_no: Optional[str] = None
        self._zmail_tid: Optional[str] = None
        self._context: Optional[str] = None
        self._emp_uin: Optional[str] = None
        self._is_test: Optional[str] = None
        self._resid: Optional[str] = None
        self._pcmail_url = None
        self._pccontacts_url = None
        self._pcfolder_url = None
        self._pcrecall_url = None
        self._pconline_read_url = None
        self._download_url = None
        self.ensure_token()

    def ensure_token(self) -> None:
        """获取并缓存工号、zmail token、context 及 pcmail/pccontacts URL。已有缓存则不再请求。"""
        if self._emp_no and self._zmail_tid:
            return
        emp_no, zmail_tid, context, emp_uin, is_test, resid = get_zmail_token()
        self._emp_no = emp_no
        self._zmail_tid = zmail_tid
        self._context = context
        self._emp_uin = emp_uin
        self._is_test = is_test
        self._resid = resid
        self._pcmail_url = get_zmail_pcmail_url(context)
        self._pccontacts_url = get_zmail_pccontacts_url(context)
        self._pcfolder_url = get_zmail_pcfolder_url(context)
        self._pcrecall_url = get_zmail_pcrecall_url(context)
        self._pconline_read_url = get_zmail_pconline_read_url(context)
        self._download_url = get_zmail_download_url(context)
        # 历史邮件列表 URL（命令字为 sysGetMailList），正文等其它命令由各自方法调用 get_zmail_historymail_url 拼接
        self._historymail_url = get_zmail_historymail_url("sysGetMailList")

    @property
    def emp_no(self) -> str:
        self.ensure_token()
        return self._emp_no or ""

    @property
    def zmail_tid(self) -> str:
        self.ensure_token()
        return self._zmail_tid or ""

    @property
    def emp_uin(self) -> str:
        self.ensure_token()
        return self._emp_uin or ""

    @property
    def is_test(self) -> str:
        self.ensure_token()
        return self._is_test or ""

    @property
    def resid(self) -> str:
        self.ensure_token()
        return self._resid or ""

    def _headers(self) -> dict:
        return {
            "Accept": "text/plain, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "x-zmail-uid": self.emp_no,
            "x-zmail-tid": self.zmail_tid,
            "x-zmail-resid": self.resid,
            "x-zmail-istest": self.is_test,
            "x-emp-no": self.emp_no,
            "x-requested-with": "XMLHttpRequest",
        }

    def _headers_history_mail(self) -> dict:
        """历史邮件接口专用请求头：Accept/Content-Type 为 JSON，x-zmail-token 等于 x-zmail-tid。"""
        headers = dict(self._headers())
        headers.update({
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Content-Type": "application/json",
            "x-zmail-token": self.zmail_tid,
            "x-zmail-clienttype": "3",
        })
        return headers

    def _post(self, url: str, payload: dict, timeout: int = 30) -> dict:
        """发送 POST，body 为 JSON 的 URL 编码，返回解析后的 JSON。出错时记录请求与响应（脱敏）并抛出。"""
        self._last_url = url
        self._last_headers = self._headers()
        self._last_payload = payload
        body_str = json.dumps(payload, ensure_ascii=False)
        body_bytes = urllib.parse.quote(body_str, safe="").encode("utf-8")
        try:
            resp = request_post(url, data=body_bytes, headers=self._last_headers, timeout=timeout)
        except requests.exceptions.RequestException as e:
            log_api_error(url, self._last_headers, payload, None, str(e))
            raise build_zmail_network_error(e) from e

        raw = resp.content.decode("utf-8", errors="replace")
        if not (200 <= resp.status_code < 300):
            resp_body = None
            try:
                resp_body = json.loads(raw)
            except Exception:
                resp_body = raw
            log_api_error(url, self._last_headers, payload, resp_body, f"HTTP {resp.status_code}")
            resp.raise_for_status()

        try:
            decoded = urllib.parse.unquote(raw)
            result = json.loads(decoded)
        except json.JSONDecodeError:
            try:
                result = json.loads(raw)
            except json.JSONDecodeError as e:
                log_api_error(url, self._last_headers, payload, raw, str(e))
                raise
        return result

    def _post_history_mail(self, url: str, payload: dict, timeout: int = 30) -> dict:
        """发送 POST（历史邮件接口）：请求头使用 _headers_history_mail，body 为原始 JSON，返回解析后的 JSON。"""
        self._last_url = url
        self._last_headers = self._headers_history_mail()
        self._last_payload = payload
        body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            resp = request_post(url, data=body_bytes, headers=self._last_headers, timeout=timeout)
        except requests.exceptions.RequestException as e:
            log_api_error(url, self._last_headers, payload, None, str(e))
            raise build_zmail_network_error(e) from e

        raw = resp.content.decode("utf-8", errors="replace")
        if not (200 <= resp.status_code < 300):
            resp_body = None
            try:
                resp_body = json.loads(raw)
            except Exception:
                resp_body = raw
            log_api_error(url, self._last_headers, payload, resp_body, f"HTTP {resp.status_code}")
            resp.raise_for_status()

        try:
            decoded = urllib.parse.unquote(raw)
            result = json.loads(decoded)
        except json.JSONDecodeError:
            try:
                result = json.loads(raw)
            except json.JSONDecodeError as e:
                log_api_error(url, self._last_headers, payload, raw, str(e))
                raise
        return result

    def _pcmail_url_for(self, c: str, req_id: str) -> str:
        return f"{self._pcmail_url}?1=1&c={c}&reqid={urllib.parse.quote(req_id)}"

    def _pccontacts_url_for(self, c: str, req_id: str) -> str:
        return f"{self._pccontacts_url}?1=1&c={c}&reqid={urllib.parse.quote(req_id)}"

    def _pcfolder_url_for(self, c: str, req_id: str) -> str:
        return f"{self._pcfolder_url}?1=1&c={c}&reqid={urllib.parse.quote(req_id)}"

    def _pcrecall_url_for(self, c: str, req_id: str) -> str:
        return f"{self._pcrecall_url}?1=1&c={c}&reqid={urllib.parse.quote(req_id)}"

    def _pconline_read_url_for(self, c: str, req_id: str) -> str:
        return f"{self._pconline_read_url}?1=1&c={c}&reqid={urllib.parse.quote(req_id)}"

    def _base_payload(self, c: str, req_id: str, D: Any = "null", F: Optional[list] = None, P: Any = None) -> dict:
        dt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        return {
            "C": c,
            "T": "",
            "L": "2052",
            "P": P,
            "D": json.dumps(D, ensure_ascii=False) if isinstance(D, (dict, list)) else D,
            "F": F,
            "VER": "1.0",
            "DT": dt,
            "UID": self._emp_uin,
            "UNO": self.emp_no,
            "DATA": None,
            "FD": None,
            "ID": req_id,
        }

    def _build_pcweb_req_id(self, action: str) -> str:
        """生成 PcWeb 请求 req_id（统一格式）。"""
        action = str(action or "").strip()
        if not action:
            raise ValueError("action 不能为空")
        return f"1020060072026013003_{self.emp_no}_zMailSkill_{action}_{int(time.time() * 1000)}"

    def _build_history_req_id(self, api: str) -> str:
        """生成历史邮件接口请求 req_id（统一格式）。"""
        api = str(api or "").strip()
        if not api:
            raise ValueError("api 不能为空")
        return f"1020060072026031101_{self.emp_no}_zMailSkill_{api}_{int(time.time() * 1000)}_Cbb2d260b8bd505c3ffe8d590528942cb"

    # ---------- SysGetMailL 统一入口 ----------

    def get_mail_list(
        self,
        T: int,
        F: List[dict],
        P: dict,
        pno: int = 1,
        psize: int = 50,
    ) -> dict:
        """
        统一调用 SysGetMailL 获取邮件列表。
        :param T: P 中的 T 字段（箱体/分类标识）。
        :param F: 筛选条件列表。
        :param P: 基础 P 参数（含 S、E 等），函数内合并 T、PNO、PSIZE。
        :param pno: 页码。
        :param psize: 每页条数。
        :return: 原始响应 dict（含 S, D 等）。
        """
        validate_positive_int(pno, "pno", min_value=1)
        validate_positive_int(psize, "psize", min_value=1, max_value=500)
        api = "SysGetMailL"
        req_id = self._build_pcweb_req_id(api)
        url = self._pcmail_url_for(api, req_id)
        P_final = {**P, "T": T, "PNO": pno, "PSIZE": str(psize)}
        payload = self._base_payload(api, req_id, "null", F=F, P=P_final)
        return self._post(url, payload)

    def get_history_mail_list(
        self,
        mailbox_type: str,
        start_time: str,
        end_time: str,
        subject: Optional[str] = None,
        sender: Optional[str] = None,
        recipient: Optional[str] = None,
        cc: Optional[str] = None,
        pno: int = 1,
        psize: int = 50,
    ) -> dict:
        """
        命令字 sysGetMailList（SysGetMailL）查询历史邮件列表。

        - mailbox_type: 仅支持 "inbox"（收件箱）或 "outbox"（发件箱）
        - 返回原始响应 dict，由调用方判断 S 并解析 D、P
        """
        mt = (mailbox_type or "").strip().lower()
        if mt not in ("inbox", "outbox"):
            return {"S": False, "M": f"mailbox_type 仅支持 inbox 或 outbox，当前: {mailbox_type!r}", "error": f"mailbox_type 仅支持 inbox 或 outbox，当前: {mailbox_type!r}"}
        self.ensure_token()
        api = "sysGetMailList"
        req_id = self._build_history_req_id(api)
        url = self._historymail_url or get_zmail_historymail_url("sysGetMailList")
        if mt == "inbox":
            t_val = "1"
            mt_val = "1"
        else:
            t_val = "3"
            mt_val = "2"
        F: List[dict] = [
            {"K": "CT", "V": "3"},
            {"K": "MT", "V": mt_val},
            {"K": "T", "V": t_val},
        ]
        if subject and str(subject).strip():
            F.append({"K": "TT", "V": str(subject).strip()})
        if sender and str(sender).strip():
            F.append({"K": "MF", "V": str(sender).strip()})
        if recipient and str(recipient).strip():
            F.append({"K": "MTO", "V": str(recipient).strip()})
        if cc and str(cc).strip():
            F.append({"K": "MCC", "V": str(cc).strip()})
        P_final = {
            "S": start_time,
            "E": end_time,
            "PNO": pno,
            "PSIZE": psize,
        }
        payload = self._base_payload(api, req_id, "null", F=F, P=P_final)
        return self._post_history_mail(url, payload)

    # ---------- pcmail 业务方法 ----------

    def get_new_mail_id(self) -> str:
        """SysGetNMID，返回 D.ID。失败抛 RuntimeError。"""
        req_id = self._build_pcweb_req_id("SysGetNMID")
        url = self._pcmail_url_for("SysGetNMID", req_id)
        payload = self._base_payload("SysGetNMID", req_id, "null", [{"K": "F", "V": "1"}])
        resp = self._post(url, payload)
        if not resp.get("S"):
            msg = resp.get("M") or resp.get("C") or "SysGetNMID 失败"
            raise RuntimeError(f"zmail SysGetNMID 未成功: {msg}")
        d = resp.get("D")
        if isinstance(d, dict):
            return d.get("ID") or ""
        return ""

    def search_mail_expert(
        self,
        start: str,
        end: str,
        scs: List[dict],
        mailbox_type: str = "inbox",
        folder_id: Optional[str] = None,
        pno: int = 1,
        psize: int = 100,
    ) -> dict:
        """
        SysSearchMailExpert。start/end 为 'YYYY-MM-DD HH:MM:SS'，scs 为搜索条件列表。
        mailbox_type：inbox（收件箱=MailboxId.INBOX）、outbox（发件箱=MailboxId.OUTBOX）、deleted（已删除=MailboxId.DELETED）、spam（垃圾邮件=MailboxId.SPAM）、folder（文件夹）。
        folder_id：mailbox_type 为 folder 时，空用 9999，有值用 folder_id。
        返回原始响应 dict。
        """
        validate_datetime_param(start, "start", required=True)
        validate_datetime_param(end, "end", required=True)
        validate_positive_int(pno, "pno", min_value=1)
        validate_positive_int(psize, "psize", min_value=1, max_value=500)
        mt = (mailbox_type or "inbox").strip().lower()
        if mt not in ("inbox", "outbox", "deleted", "spam", "folder"):
            raise ValueError("mailbox_type 须为 inbox、outbox、deleted、spam 或 folder")
        if mt == "inbox":
            t_val = MailboxId.INBOX.value
        elif mt == "outbox":
            t_val = MailboxId.OUTBOX.value
        elif mt == "deleted":
            t_val = MailboxId.DELETED.value
        elif mt == "spam":
            t_val = MailboxId.SPAM.value
        else:
            fid = (folder_id or "").strip()
            t_val = fid if fid else "9999"
        d_inner = {"SCS": scs, "ORDER": ["1"]}
        d_str = json.dumps(d_inner, ensure_ascii=False)
        req_id = self._build_pcweb_req_id("SysSearchMailExpert")
        url = self._pcmail_url_for("SysSearchMailExpert", req_id)
        payload = self._base_payload(
            "SysSearchMailExpert",
            req_id,
            d_str,
            F=[{"K": "CL", "V": "0"}, {"K": "T", "V": t_val}],
            P={"S": start, "E": end, "PNO": pno, "PSIZE": psize},
        )
        resp = self._post(url, payload)
        if not resp.get("S"):
            msg = resp.get("M") or resp.get("C") or "搜索失败"
            raise RuntimeError(f"zmail SysSearchMailExpert 未成功: {msg}")
        return resp

    def get_history_mail_context(self, history_email_id: str, mailbox_type: str = "inbox") -> dict:
        """
        命令字 sysGetMailContext，查询单封历史邮件正文，返回响应 D（dict）。

        - mailbox_type: 仅支持 "inbox"（收件箱）或 "outbox"（发件箱）
        """
        if not (history_email_id or "").strip():
            raise ValueError("参数 history_email_id 不能为空")
        mt = (mailbox_type or "").strip().lower()
        if mt not in ("inbox", "outbox"):
            return {"error": f"mailbox_type 仅支持 inbox 或 outbox，当前: {mailbox_type!r}"}
        self.ensure_token()
        api = "sysGetMailContext"
        req_id = self._build_history_req_id(api)
        url = get_zmail_historymail_url("sysGetMailContext")
        if mt == "outbox":
            t_val = "3"
            mt_val = "2"
        else:
            t_val = "1"
            mt_val = "1"
        F: List[dict] = [
            {"K": "CT", "V": "3"},
            {"K": "MT", "V": mt_val},
            {"K": "T", "V": t_val},
            {"K": "ID", "V": str(history_email_id).strip()},
        ]
        payload = self._base_payload(api, req_id, None, F=F, P=None)
        resp = self._post_history_mail(url, payload)
        if not resp.get("S"):
            msg = resp.get("M") or resp.get("C") or "获取历史邮件内容失败"
            raise RuntimeError(f"zmail sysGetMailContext 未成功: {msg}")
        d = resp.get("D")
        return d if isinstance(d, dict) else {}

    def upload_history_mail_attach_to_zmail(
        self,
        attach_obj: dict,
        new_mail_attach_id: str,
        original_mail_id: str,
    ) -> dict:
        """
        历史邮件附件转挂到新邮件（命令字 sysUploadToZmail）。

        :param attach_obj: 历史邮件附件对象，需包含 ID/N/U/S/I 等字段。
        :param new_mail_attach_id: 新邮件附件 ID（同时用于 attInfo.ID / Data）。
        :param original_mail_id: 原邮件 ID，作为 MID 传入。
        :return: {"new_attach_id": 新附件ID, "attach_attr": 附件属性, "old_attach_id": 原附件ID}
        """
        if not attach_obj or not isinstance(attach_obj, dict):
            raise ValueError("参数 attach_obj 无效")
        if not (new_mail_attach_id or "").strip():
            raise ValueError("参数 new_mail_attach_id 不能为空")
        if not (original_mail_id or "").strip():
            raise ValueError("参数 original_mail_id（原邮件ID）不能为空")
        self.ensure_token()

        mid_val = str(original_mail_id).strip()
        old_attach_id = str(attach_obj.get("ID") or attach_obj.get("id") or "").strip()
        n_val = str(attach_obj.get("N") or attach_obj.get("n") or "").strip()
        u_val = str(attach_obj.get("U") or attach_obj.get("u") or "").strip()
        s_val = str(attach_obj.get("S") or attach_obj.get("s") or "").strip()
        i_val = str(attach_obj.get("I") or attach_obj.get("i") or "").strip()
        t_val = str(attach_obj.get("T") or attach_obj.get("t") or "").strip()
        if not old_attach_id:
            raise ValueError("附件对象缺少 ID")

        new_id = str(new_mail_attach_id).strip()
        d_obj = {
            "attInfo": {
                "ID": new_id,
                "CID": "upinfo",
                "SName": n_val,
                "Stype": t_val,
                # 历史邮件转挂接口要求 attInfo.Data 使用原附件的 Data（attach_obj.D）
                # 若原附件缺少 D 字段则回退为 new_id，避免请求体不完整。
                "Data": attach_obj.get("D") or attach_obj.get("d") or new_id,
                "isInline": i_val,
            },
            "MID": mid_val,
            "ID": old_attach_id,
            "N": n_val,
            "U": u_val,
            "S": s_val,
            "I": i_val,
        }

        api = "sysUploadToZmail"
        req_id = self._build_history_req_id(api)
        url = get_zmail_historymail_url(api)
        payload = self._base_payload(
            api,
            req_id,
            json.dumps(d_obj, ensure_ascii=False),
            F=None,
            P=None,
        )
        resp = self._post_history_mail(url, payload)
        if not resp.get("S"):
            msg = resp.get("M") or resp.get("C") or "历史附件转挂失败"
            raise RuntimeError(f"zmail {api} 未成功: {msg}")

        d = resp.get("D")
        new_attach_id = new_id
        # 附件属性应取自 attach_obj.D（而不是接口返回的 resp.D）
        attach_d = attach_obj.get("D") or attach_obj.get("d")
        attach_attr: dict = {}
        if isinstance(attach_d, dict):
            attach_attr = attach_d
        elif isinstance(attach_d, str) and attach_d.strip():
            try:
                parsed = json.loads(attach_d)
                attach_attr = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                attach_attr = {}
        if isinstance(d, dict):
            new_attach_id = str(
                d.get("ID") or d.get("id") or d.get("AID") or d.get("aid") or new_id
            ).strip()
        return {
            "new_attach_id": new_attach_id,
            "attach_attr": attach_attr,
            "old_attach_id": old_attach_id,
        }

    def get_mail_context(self, email_id: str, mailbox_type: str = "inbox") -> dict:
        """
        SysGetMailContext，返回响应 D（dict）。失败抛 RuntimeError。

        若 email_id 为历史邮件 ID，则自动调用 get_history_mail_context 并返回其结果。
        mailbox_type 仅在历史邮件时生效：inbox（收件箱）或 outbox（发件箱）。
        """
        if not (email_id or "").strip():
            raise ValueError("参数 email_id 不能为空")
        if is_history_mail_id(email_id):
            return self.get_history_mail_context(email_id, mailbox_type=mailbox_type or "inbox")
        req_id = self._build_pcweb_req_id("SysGetMailContext")
        url = self._pcmail_url_for("SysGetMailContext", req_id)
        payload = self._base_payload(
            "SysGetMailContext",
            req_id,
            "null",
            F=[
                {"K": "ID", "V": email_id},
                {"K": "T", "V": "1"},
                {"K": "R", "V": "0"},
                {"K": "RT", "V": "0"},
            ],
        )
        resp = self._post(url, payload)
        if not resp.get("S"):
            msg = resp.get("M") or resp.get("C") or "获取邮件内容失败"
            raise RuntimeError(f"zmail SysGetMailContext 未成功: {msg}")
        d = resp.get("D")
        return d if isinstance(d, dict) else {}

    def get_attach_view_url(self, attach_obj: dict) -> Tuple[str, Optional[str]]:
        """
        根据附件对象获取附件预览地址。
        :param attach_obj: 完整附件对象（来自邮件详情 ATS 中某一项），将作为请求体 D 传入。
        :return: (预览 URL 字符串, 错误信息)。成功时为 (url, None)，失败或异常时为 ("", err_msg)。
        """
        if not attach_obj or not isinstance(attach_obj, dict):
            return "", "附件对象无效"
        req_id = self._build_pcweb_req_id("SysAttachOnlineView")
        url = self._pconline_read_url_for("SysAttachOnlineView", req_id)
        payload = self._base_payload(
            "SysAttachOnlineView",
            req_id,
            attach_obj,
            F=[{"K": "T", "V": "0"}, {"K": "CT", "V": "0"}],
        )
        try:
            resp = self._post(url, payload)
        except Exception as e:
            return "", str(e)
        if not resp.get("S"):
            msg = resp.get("M") or resp.get("C") or "获取附件预览地址失败"
            return "", msg
        d = resp.get("D")
        if isinstance(d, dict) and d.get("URL"):
            return str(d.get("URL", "")).strip(), None
        if isinstance(d, str) and d.strip():
            return d.strip(), None
        return "", "响应中无预览地址"

    def get_history_mail_attach_view_url(self, attach_obj: dict) -> Tuple[str, Optional[str]]:
        """
        历史邮件附件在线预览地址：与 `get_attach_view_url` 参数/返回保持一致。

        - 命令号：`sysAttachOnlineView`
        - F 数组固定包含：T=0, CT=3, OPUNO=self.emp_no
        - D：附件对象字符串（JSON 序列化后作为字符串传入）
        - P：None
        - UID：self.emp_uin；UNO：self.emp_no；FD：None（由 _base_payload 统一设置）
        """
        if not attach_obj or not isinstance(attach_obj, dict):
            return "", "附件对象无效"

        self.ensure_token()

        api = "sysAttachOnlineView"
        req_id = self._build_history_req_id(api)
        url = get_zmail_historymail_url(api)

        f_arr = [
            {"K": "T", "V": "0"},
            {"K": "CT", "V": "3"},
            {"K": "OPUNO", "V": self.emp_no},
        ]

        # 历史邮件接口按要求：D 使用“附件对象字符串”（即 JSON 字符串）
        d_str = json.dumps(attach_obj, ensure_ascii=False)

        payload = self._base_payload(
            api,
            req_id,
            d_str,
            F=f_arr,
            P=None,
        )
        try:
            resp = self._post_history_mail(url, payload)
        except Exception as e:
            return "", str(e)

        if not resp.get("S"):
            msg = resp.get("M") or resp.get("C") or "获取附件预览地址失败"
            return "", msg

        d = resp.get("D")
        if isinstance(d, dict) and d.get("URL"):
            return str(d.get("URL", "")).strip(), None
        if isinstance(d, str) and d.strip():
            return d.strip(), None
        return "", "响应中无预览地址"

    def get_attach_download_url(
        self,
        attach_obj: dict,
        uno: Optional[str] = None,
        opuno: Optional[str] = None,
    ) -> Tuple[str, Optional[str]]:
        """
        根据附件对象生成附件下载链接（不发起请求）。链接格式为 service.dssm?CT=3&ID=...&N=... 等。
        :param attach_obj: 完整附件对象（来自邮件详情 ATS 中某一项），提供 ID、N、S、I、T、U、RID、D 等字段。
        :param uno: 请求人工号，未传则使用当前登录工号。
        :param opuno: 实际操作人工号，未传则使用当前登录工号。
        :return: (下载 URL 字符串, 错误信息)。成功时为 (url, None)，失败时为 ("", err_msg)。
        """
        if not attach_obj or not isinstance(attach_obj, dict):
            return "", "附件对象无效"
        attach_id = attach_obj.get("ID") or attach_obj.get("id") or ""
        if not attach_id:
            return "", "附件对象缺少 ID"
        n_val = attach_obj.get("N") or attach_obj.get("n") or ""
        s_val = attach_obj.get("S") or attach_obj.get("s") or ""
        i_val = attach_obj.get("I") or attach_obj.get("i") or ""
        t_val = attach_obj.get("T") or attach_obj.get("t") or ""
        u_val = attach_obj.get("U") or attach_obj.get("u") or ""
        rid_val = attach_obj.get("RID") or attach_obj.get("rid") or ""
        d_val = attach_obj.get("D") or attach_obj.get("d") or ""
        ae_val = attach_obj.get("AE") or attach_obj.get("ae") or ""
        aon_val = attach_obj.get("AON") or attach_obj.get("aon") or ""
        aep_val = attach_obj.get("AEP") or attach_obj.get("aep") or ""
        req_uno = (uno or "").strip() or self.emp_no
        req_opuno = (opuno or "").strip() or self.emp_no
        if not req_uno or not req_opuno:
            return "", "必须提供 UNO 与 OPUNO（或已登录工号）"
        params = {
            "CT": "3",
            "ID": attach_id,
            "N": n_val,
            "U": u_val,
            "S": s_val,
            "I": i_val,
            "T": t_val,
            "RID": rid_val,
            "D": d_val,
            "UNO": req_uno,
            "OPUNO": req_opuno,
            "RC": "1",
        }
        # 附件授权相关字段（若已在附件对象中设置，则一并带上）
        if ae_val:
            params["AE"] = ae_val
        if aon_val:
            params["AON"] = aon_val
        if aep_val:
            params["AEP"] = aep_val
        qs = urllib.parse.urlencode(params, encoding="utf-8", quote_via=urllib.parse.quote)
        return f"{self._download_url}?{qs}", None

    def get_history_mail_attach_download_url(
        self,
        attach_obj: dict,
        email_id: str,
        uno: Optional[str] = None,
        opuno: Optional[str] = None,
    ) -> Tuple[str, Optional[str]]:
        """
        根据历史邮件附件对象生成附件下载链接（不发起请求）。

        :param attach_obj: 完整附件对象（来自历史邮件详情 ATS 中某一项）。
        :param email_id: 邮件 ID（用于 MID）。
        :param uno: 请求人工号，未传则使用当前登录工号。
        :param opuno: 实际操作人工号，未传则使用当前登录工号。
        :return: (下载 URL 字符串, 错误信息)。成功时为 (url, None)，失败时为 ("", err_msg)。
        """
        if not attach_obj or not isinstance(attach_obj, dict):
            return "", "附件对象无效"
        if not (email_id or "").strip():
            return "", "必须提供邮件ID"

        attach_id = attach_obj.get("ID") or attach_obj.get("id") or ""
        if not attach_id:
            return "", "附件对象缺少 ID"

        n_val = attach_obj.get("N") or attach_obj.get("n") or ""
        s_val = attach_obj.get("S") or attach_obj.get("s") or ""
        i_val = attach_obj.get("I") or attach_obj.get("i") or ""
        t_val = attach_obj.get("T") or attach_obj.get("t") or ""

        req_uno = (uno or "").strip() or self.emp_no
        req_opuno = (opuno or "").strip() or self.emp_no
        if not req_uno or not req_opuno:
            return "", "必须提供 UNO 与 OPUNO（或已登录工号）"

        params = {
            "MID": str(email_id).strip(),
            "ID": attach_id,
            "N": n_val,
            "RID": "",
            "T": t_val,
            "S": s_val,
            "I": i_val,
            "UNO": req_uno,
            "resid": self.resid,
            "isTest": self.is_test,
            "UID": self.emp_uin,
            "CT": "3",
            "RC": "1",
            "OPUNO": req_opuno,
        }
        qs = urllib.parse.urlencode(params, encoding="utf-8", quote_via=urllib.parse.quote)
        history_download_base = get_zmail_historymail_url("sysDownload")
        return f"{history_download_base}?{qs}", None

    def delete_mails(self, mail_ids: List[str]) -> dict:
        """
        DeleteMails（命令字 ``SysDeleteMails``）：POST 至 ``…/zmailserver/pcmail/services.jssm``。

        - **D**：邮件 ID 列表（数组，序列化进 payload）。
        - **F**：``M=0`` 表示默认普通邮件；含 ``T=1`` 与既有 Web 端协议一致。
        - **FD**：``[{"K": "F", "V": "1"}]``

        收件箱/发件箱/文件夹/草稿等**所有“删除邮件”**均应通过本方法，不得误用移动接口。
        """
        if not mail_ids or not isinstance(mail_ids, list):
            raise ValueError("参数 mail_ids 必须为非空列表")
        ids = [str(x).strip() for x in mail_ids if x is not None and str(x).strip()]
        if not ids:
            raise ValueError("参数 mail_ids 必须为非空列表")
        action = "SysDeleteMails"
        req_id = self._build_pcweb_req_id("DeleteMails")
        url = self._pcmail_url_for(action, req_id)
        payload = self._base_payload(
            action,
            req_id,
            ids,
            F=[{"K": "T", "V": "1"}, {"K": "M", "V": "0"}],
        )
        payload["FD"] = [{"K": "F", "V": "1"}]
        return self._post(url, payload)

    def recover_mails(self, mail_ids: List[str]) -> dict:
        """
        RecoverMails：从“已删除/回收站”恢复邮件。

        注意：
        - 业务侧必须确保 mail_ids 均来自“已删除”箱体；接口本身可能对非已删除邮件返回失败。
        - 支持多个邮件 ID。
        """
        if not mail_ids or not isinstance(mail_ids, list):
            raise ValueError("参数 mail_ids 必须为非空列表")
        action = "SysRecoverMails"
        req_id = self._build_pcweb_req_id(action)
        url = self._pcmail_url_for(action, req_id)
        payload = self._base_payload(
            action,
            req_id,
            mail_ids,
            # 与 delete_mails 一致的结构，服务端根据 mail_id 判断恢复落点
            F=[{"K": "T", "V": "0"}],
        )
        payload["FD"] = [{"K": "F", "V": "1"}]
        return self._post(url, payload)

    def delete_attach(self, attach_id: str) -> dict:
        """SysDeleteAttach。按附件 ID 删除附件，返回原始响应 dict。"""
        attach_id = str(attach_id or "").strip()
        if not attach_id:
            raise ValueError("参数 attach_id 不能为空")
        action = "SysDeleteAttach"
        req_id = self._build_pcweb_req_id(action)
        url = self._pcmail_url_for(action, req_id)
        payload = self._base_payload(
            action,
            req_id,
            "null",
            F=[{"K": "ID", "V": attach_id}],
        )
        return self._post(url, payload)

    def get_attach_no(self, mail_id: str) -> int:
        """SysGetATTNo。获取服务端当前附件编号，返回整数。"""
        mail_id = str(mail_id or "").strip()
        if not mail_id:
            raise ValueError("参数 mail_id 不能为空")
        action = "SysGetATTNo"
        req_id = self._build_pcweb_req_id(action)
        url = self._pcmail_url_for(action, req_id)
        payload = self._base_payload(
            action,
            req_id,
            "null",
            F=[{"K": "MID", "V": mail_id}],
        )
        resp = self._post(url, payload)
        if not resp.get("S"):
            msg = resp.get("M") or resp.get("C") or "SysGetATTNo 失败"
            raise RuntimeError(msg)

        d = resp.get("D")
        candidates = []
        if isinstance(d, dict):
            candidates.extend([
                d.get("ATTNo"), d.get("ATTNO"), d.get("attNo"), d.get("attno"),
                d.get("NO"), d.get("No"), d.get("no"), d.get("ID"), d.get("V"),
            ])
        else:
            candidates.append(d)

        for val in candidates:
            if val is None:
                continue
            s = str(val).strip()
            if not s:
                continue
            if s.isdigit():
                return int(s)

        raise RuntimeError("SysGetATTNo 返回中未找到有效附件编号")

    def set_mail_mark(
        self,
        email_ids: Union[str, List[str]],
        action: str = "read",
        v: str = "1",
        t: str = "1",
        m: str = "0",
    ) -> dict:
        """
        设置邮件状态（已读/未读/标星/取消标星）。
        - action="read" → API SysSetMRF
        - action="star" → API SysStarMails

        :param email_ids: 邮件 ID，支持单个字符串或列表。
        :param action: 操作类型："read"（已读/未读）、"star"（标星/取消标星）。
        :param v: 状态值，"1"=已读/已标星，"0"=未读/未标星，默认 "1"。
        :param t: 文件夹类型，默认 "1"。
        :param m: 邮件类型，默认 "0"。
        :return: 原始响应 dict。
        """
        if isinstance(email_ids, str):
            email_ids = [eid.strip() for eid in email_ids.split(",") if eid.strip()]
        else:
            email_ids = [eid.strip() for eid in email_ids if (eid or "").strip()]
        if not email_ids:
            raise ValueError("参数 email_ids 不能为空")

        action_lower = action.lower().strip()
        if action_lower not in ("read", "star"):
            raise ValueError(f"参数 action 必须为 read/star，当前值: {action!r}")

        api = "SysStarMails" if action_lower == "star" else "SysSetMRF"
        req_id = self._build_pcweb_req_id(api)
        url = self._pcmail_url_for(api, req_id)
        payload = self._base_payload(
            api,
            req_id,
            email_ids,
            F=[{"K": "T", "V": str(t)}, {"K": "V", "V": str(v)}, {"K": "M", "V": str(m)}],
        )
        payload["FD"] = [{"K": "F", "V": "1"}]

        resp = self._post(url, payload)
        if not resp.get("S"):
            msg = resp.get("M") or resp.get("C") or f"{api} 未成功"
            raise RuntimeError(f"zmail {api} 未成功: {msg}")
        return resp

    def send_email(self, D: dict) -> dict:
        """SysSendEmail，D 为请求体 D 参数（dict）。返回原始响应。"""
        if not D or not isinstance(D, dict):
            raise ValueError("参数 D 必须为非空 dict")
        req_id = self._build_pcweb_req_id("SysSendEmail")
        url = self._pcmail_url_for("SysSendEmail", req_id)
        payload = self._base_payload("SysSendEmail", req_id, D, F=None)
        return self._post(url, payload)

    def rollback_send_mail(self, email_id: str) -> dict:
        """
        撤回已发送邮件（SysReCallMail）。返回原始响应 dict。
        :param email_id: 邮件 ID。
        """
        if not (email_id or "").strip():
            raise ValueError("参数 email_id 不能为空")
        api = "SysReCallMail"
        req_id = self._build_pcweb_req_id(api)
        url = self._pcrecall_url_for(api, req_id)
        payload = self._base_payload(
            api,
            req_id,
            D="null",
            F=[{"K": "ID", "V": str(email_id).strip()}, {"K": "T", "V": "0"}],
            P=None,
        )
        payload["FD"] = [{"K": "F", "V": "1"}]
        return self._post(url, payload)

    def query_signature(self) -> str:
        """SysQuerySG，返回 D[0].CT 用 div 包裹的 HTML；失败返回 ''。"""
        req_id = self._build_pcweb_req_id("SysQuerySG")
        url = self._pcmail_url_for("SysQuerySG", req_id)
        payload = self._base_payload("SysQuerySG", req_id, "null", [{"K": "F", "V": "1"}])
        resp = self._post(url, payload)
        if not resp.get("S"):
            return ""
        d = resp.get("D")
        if isinstance(d, list) and len(d) > 0 and isinstance(d[0], dict):
            ct = d[0].get("CT") or ""
            return f'<div id="zMailSign" class="zMailSign" style="line-height:1.5">{ct}</div>'
        return ""

    def get_folder_list(
        self,
        pno: int = 1,
        psize: int = 100,
    ) -> List[dict]:
        """SysGetUFList，返回响应 D（数组）。失败抛 RuntimeError。"""
        req_id = self._build_pcweb_req_id("SysGetUFList")
        url = self._pcfolder_url_for("SysGetUFList", req_id)
        payload = self._base_payload(
            "SysGetUFList",
            req_id,
            "null",
            F=[{"K": "F", "V": "1"}],
            P={"S": "1970-01-01 08:00:00", "E": None, "T":None,  "PNO": pno, "PSIZE": psize},
        )
        payload["FD"] = [{"K": "F", "V": "1"}]
        resp = self._post(url, payload)
        if not resp.get("S"):
            msg = resp.get("M") or resp.get("C") or "获取文件夹列表失败"
            raise RuntimeError(f"zmail SysGetUFList 未成功: {msg}")
        d = resp.get("D")
        return d if isinstance(d, list) else []

    def move_mails_to_folder(
        self,
        mail_ids: List[str],
        old_folder_id: str,
        new_folder_id: str,
    ) -> dict:
        """
        移动普通邮件到指定文件夹（SysMMToUF）。
        :param mail_ids: 邮件 ID 字符串数组。
        :param old_folder_id: 原文件夹 ID。
        :param new_folder_id: 新文件夹 ID。
        :return: 原始响应 dict（含 S, D 等），由调用方解析。
        """
        if not mail_ids or not isinstance(mail_ids, list):
            raise ValueError("参数 mail_ids 必须为非空列表")
        if not (old_folder_id or "").strip():
            raise ValueError("参数 old_folder_id 不能为空")
        if not (new_folder_id or "").strip():
            raise ValueError("参数 new_folder_id 不能为空")
        api = "SysMMToUF"
        req_id = self._build_pcweb_req_id(api)
        url = self._pcfolder_url_for(api, req_id)
        # D 字段：格式为字符串 '{"id1","id2",...}'（花括号 + 逗号分隔的带引号 ID），非数组格式
        d_str = json.dumps(mail_ids, ensure_ascii=False)
        payload = self._base_payload(
            api,
            req_id,
            d_str,
            F=[
                {"K": "FID", "V": str(new_folder_id).strip()},
                {"K": "OID", "V": str(old_folder_id).strip()},
            ],
            P=None,
        )
        # 将 payload 序列化为 JSON 字符串（含正确转义），作为请求体发送
        payload_json_str = json.dumps(payload, ensure_ascii=False)
        self._last_payload_json = payload_json_str
        return self._post(url, payload)

    # ---------- pccontacts ----------

    def search_user(
        self,
        keyword: str,
        contact_type: str = "user",
        pno: int = 1,
        psize: int = 10,
    ) -> List[dict]:
        """
        SysSearchUser。contact_type 为 'group' 时在 keyword 前加 $。
        返回 List[dict]，每项含：UNO 工号、N 姓名、E 邮箱、T 部门。
        """
        if not (keyword or "").strip():
            return []
        validate_positive_int(pno, "pno", min_value=1)
        validate_positive_int(psize, "psize", min_value=1, max_value=100)
        k = (keyword or "").strip()
        if contact_type == "group":
            k = "$" + k
        req_id = self._build_pcweb_req_id("SysSearchUser")
        url = self._pccontacts_url_for("SysSearchUser", req_id)
        payload = self._base_payload(
            "SysSearchUser",
            req_id,
            "null",
            F=[{"K": "K", "V": k}, {"K": "A", "V": "1"}],
            P={"S": "1970-01-01 08:00:00", "PNO": pno, "PSIZE": psize},
        )
        payload["FD"] = [{"K": "F", "V": "1"}]
        try:
            resp = self._post(url, payload)
        except Exception:
            return []
        if not resp.get("S"):
            return []
        d = resp.get("D")
        if not isinstance(d, list):
            return []
        out = []
        for item in d:
            if isinstance(item, dict):
                out.append({
                    "UNO": item.get("UNO") or "",
                    "N": item.get("N") or "",
                    "E": item.get("E") or item.get("D") or "",
                    "T": item.get("T") or "",
                })
        return out

    def get_user(self, uno_or_email: str) -> dict:
        """SysGetUser：K 传工号或邮箱；工号时 T=0，邮箱时 T=1。返回 D 转成的 MF 风格 dict；失败返回 {}。"""
        if not (uno_or_email or "").strip():
            return {}
        key = str(uno_or_email).strip()
        t_flag = "1" if is_email_address(key) else "0"
        req_id = self._build_pcweb_req_id("SysGetUser")
        url = self._pccontacts_url_for("SysGetUser", req_id)
        payload = self._base_payload(
            "SysGetUser",
            req_id,
            "null",
            F=[{"K": "K", "V": key}, {"K": "T", "V": t_flag}, {"K": "D", "V": "1"}],
        )
        payload["FD"] = [{"K": "F", "V": "1"}]
        try:
            resp = self._post(url, payload)
        except Exception:
            return {}
        if not resp.get("S"):
            return {}
        d = resp.get("D")
        if not isinstance(d, dict):
            return {}
        return {
            "UNO": d.get("UNO") or "",
            "N": d.get("N") or "",
            "E": d.get("E") or "",
            "T": d.get("T") or "",
            "D": d.get("D"),
            "P": d.get("P") or "",
            "Y": d.get("Y") or "",
            "C": d.get("C") or "",
            "F": d.get("F"),
        }
    # ---------- 转存云盘----------
    def transfer_attach_to_personal_space(self, payload: dict, timeout: int = 30) -> dict:
        """
        调用附件转存个人空间接口（SysAttachTransferUDM）。

        :param payload: 业务请求体片段 dict，至少包含 D/F/FD 等字段，C/ID/DT/UID/UNO 等由本方法统一组装；
                        业务字段需按《附件转存个人空间接口文档》组装。
        :param timeout: 超时时间（秒），默认 30。
        :return: 调用成功返回完整响应 dict，失败或异常返回 {}。
        """
        if not payload or not isinstance(payload, dict):
            raise ValueError("参数 payload 必须为非空 dict")
        action = "SysAttachTransferUDM"
        req_id = self._build_pcweb_req_id(action)
        base_url = get_zmail_upload_file_url(self._context)
        url = f"{base_url}?1=1&c={action}&reqid={urllib.parse.quote(req_id)}"

        # 按接口期望组装请求体，保持 payload['D'] 原样（避免再次 json.dumps）
        now_dt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        full_payload = {
            "C": action,
            "T": "",
            "L": "2052",
            "P": payload.get("P"),
            "D": payload.get("D", "null"),
            "F": payload.get("F") or [],
            "VER": "1.0",
            "DT": now_dt,
            "UID": self.emp_no,
            "UNO": self.emp_no,
            "DATA": None,
            "FD": payload.get("FD") or [],
            "ID": req_id,
        }

        try:
            resp = self._post(url, full_payload, timeout=timeout)
        except Exception:
            return {}
        if not resp.get("S"):
            return {}
        return resp


    # ──────────────────── 上传附件 ────────────────────
    def upload_one_file(
        self,
        body_bytes: bytes,
        boundary: str,
        timeout: int = 60,
    ) -> dict:
        """
        上传单个文件到 Zmail Mapi（UpLoadFileJSONService）。
        :param body_bytes: multipart/form-data 请求体。
        :param boundary: multipart boundary。
        :param timeout: 超时秒数。
        :return: 服务端返回的 JSON dict 或 list。
        """
        upload_context = (self._context or "").strip()
        base_url = get_zmail_upload_url(upload_context)
        qs = urllib.parse.urlencode({"CT": "3", "UNO": self.emp_no, "OPUNO": self.emp_no, "RC": "1"})
        url = base_url + "?" + qs
        headers = {
            "Accept": "*/*",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Cookie": f"zMail_tid_prod={self.zmail_tid}",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
        }
        try:
            resp = request_post(url, data=body_bytes, headers=headers, timeout=timeout)
        except requests.exceptions.RequestException as e:
            raise build_zmail_network_error(e, "上传请求失败") from e

        raw = resp.content.decode("utf-8", errors="replace")
        if not (200 <= resp.status_code < 300):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                raise RuntimeError(f"上传失败 HTTP {resp.status_code}: {raw[:500]}")

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            result = {"raw": raw}
        return result
