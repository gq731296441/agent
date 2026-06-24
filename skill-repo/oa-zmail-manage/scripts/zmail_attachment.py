#!/usr/bin/env python3
"""
zMail 附件：下载到本地、转存到云盘；上传文件（Mapi UpLoadFileJSONService）。
上传使用 multipart/form-data，Cookie 传 zMail_tid_prod（zmail token）。
兼容 Python 3.6+。
"""

import json
import os
import re
import time
import urllib.parse
import requests
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from request_client import request_get
from net_error import build_zmail_network_error
from util import ats_to_exact_attach_list, ats_to_list, is_history_mail_id, to_json_object
from zmail_base import ZmailBase

_STYPE_DOC = frozenset({"doc", "docx", "xls", "xlsx", "csv", "ppt", "pptx", "pdf", "txt", "rtf", "odt", "ods", "odp"})
_STYPE_IMAGE = frozenset({"jpg", "jpeg", "png", "gif", "bmp", "webp", "svg", "ico"})
_STYPE_AUDIO = frozenset({"mp3", "wav", "ogg", "m4a", "aac", "flac", "wma"})
_STYPE_VIDEO = frozenset({"mp4", "avi", "mov", "wmv", "flv", "mkv", "webm"})
_STYPE_ARCHIVE = frozenset({"zip", "rar", "7z", "tar", "gz", "bz2", "xz"})


def _sanitize_filename(name: str) -> str:
    """去除文件名中的非法字符，兼容 Linux 与 Windows。"""
    if not name or not name.strip():
        return "attachment"
    # Windows 非法: \ / : * ? " < > |
    name = re.sub(r'[\\/:*?"<>|]', "_", name.strip())
    return name or "attachment"


def _unique_path(dir_path: Path, base_name: str) -> Path:
    """在目录下生成不重名的路径：若存在则追加 _1, _2 等（在扩展名前）。"""
    full = dir_path / base_name
    if not full.exists():
        return full
    stem = full.stem
    suffix = full.suffix
    n = 1
    while True:
        candidate = dir_path / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


class ZmailAttachment(ZmailBase):
    """邮件附件：下载到本地、转存到云盘、上传文件（Mapi UpLoadFileJSONService）。"""

    def _apply_attach_auth(self, mail_ctx: Dict[str, Any], att: Dict[str, Any]) -> None:
        """
        处理附件授权字段：
        - 当邮件 AE 为 "1" 时，给附件设置：
          - AE = "1"
          - AON = 发件人工号
          - AEP = 授权范围：发件人/收件人 = "3"，抄送/密送 = "1"
        """
        if str(mail_ctx.get("AE", "")) != "1":
            return
        mf = to_json_object(mail_ctx.get("MF"))
        mc = to_json_object(mail_ctx.get("MC"))
        mt = to_json_object(mail_ctx.get("MT"))
        sender_uno = ""
        if isinstance(mf, dict):
            sender_uno = str(mf.get("UNO") or mf.get("uno") or "").strip()
        current_uno = str(self.emp_no or "").strip()
        if not current_uno or not sender_uno:
            return

        def _in_list(lst, uno: str) -> bool:
            if not lst:
                return False
            for x in lst:
                if isinstance(x, dict):
                    u = str(x.get("UNO") or x.get("uno") or "").strip()
                    if u == uno:
                        return True
            return False

        aep = None
        # 发件人
        if current_uno == sender_uno:
            aep = "3"
        else:
            # 收件人（主送）
            if isinstance(mt, list) and _in_list(mt, current_uno):
                aep = "3"
            # 抄送/密送（这里统一看作 MC 列表）
            elif isinstance(mc, list) and _in_list(mc, current_uno):
                aep = "1"

        if not aep:
            return
        att["AE"] = "1"
        att["AON"] = sender_uno
        att["AEP"] = aep

    def transfer_attach(
        self,
        email_id: str,
        attach_ids: Optional[List[str]] = None,
        transfer_inline: bool = True,
    ) -> Dict:
        """
        将指定邮件的附件转存到云盘。

        :param email_id: 邮件 ID。
        :param attach_ids: 可选。需要转存的附件 ID 列表；不传或为空时表示转存该邮件全部附件。
        :param transfer_inline: 是否转存插在正文里的图片（ATS 项 I=1）。默认 True（转存）。
        :return: 结果 dict，包含 status / message / transferred 等字段。
        """
        if not email_id or not str(email_id).strip():
            raise ValueError("必须提供 email_id 参数")
        email_id = str(email_id).strip()

        if is_history_mail_id(email_id):
            ctx = self._request.get_history_mail_context(email_id)
        else:
            ctx = self._request.get_mail_context(email_id)
        ats_raw = ctx.get("ATS") or ""
        ats_list = ats_to_list(ats_raw)
        if not transfer_inline:
            ats_list = ats_to_exact_attach_list(ats_raw)
        if not ats_list:
            return {
                "status": False,
                "message": "该邮件中不存在任何附件。",
                "transferred": [],
            }

        normalized_ids = [str(aid).strip() for aid in (attach_ids or []) if str(aid).strip()]
        if normalized_ids:
            ats_by_id = {str(item.get("ID") or "").strip(): item for item in ats_list if isinstance(item, dict)}
            missing = [aid for aid in normalized_ids if aid not in ats_by_id]
            if missing:
                return {
                    "status": False,
                    "message": f"以下附件 ID 在邮件中不存在: {', '.join(missing)}",
                    "transferred": [],
                }
            attachments_to_transfer = [ats_by_id[aid] for aid in normalized_ids]
        else:
            attachments_to_transfer = [item for item in ats_list if isinstance(item, dict)]

        if not attachments_to_transfer:
            return {
                "status": False,
                "message": "未找到需要转存的附件。",
                "transferred": [],
            }

        transferred = []
        for att in attachments_to_transfer:
            att_id = str(att.get("ID") or "").strip()
            #d_field = att.get("D") or ""
            d_payload = {k: att.get(k) for k in ("ID", "RID", "N", "U", "S", "I", "T", "D")}
            payload = {
                "D": json.dumps(d_payload, ensure_ascii=False),
                "F": [
                    {"K": "CT", "V": "0"},
                    {"K": "AE", "V": "0"},
                    {"K": "AON", "V": str(self._request.emp_no)},
                    {"K": "NCP", "V": "0"},
                ],
                "FD": [],
            }
            resp = self._request.transfer_attach_to_personal_space(payload)
            if resp.get("S"):
                transferred.append({"id": att_id, "name": att.get("N")})
            else:
                transferred.append({
                    "id": att_id,
                    "name": att.get("N"),
                    "error": resp.get("M") or resp.get("C") or "附件转存失败",
                })

        return {
            "status": True,
            "message": "附件转存操作已完成。",
            "transferred": transferred,
        }

    def transfer_history_mail_attach_to_zmail(
        self,
        email_id: str,
        new_email_id: str,
        transfer_attachments: bool,
        mailbox_type: str = "inbox",
    ) -> Dict[str, Any]:
        """
        将历史邮件附件转挂到新邮件，并返回附件属性数组与（可能替换正文插图引用 ID 后的）HTML 正文。

        :param email_id: 原邮件 ID。
        :param new_email_id: 新邮件 ID（用于拼接新附件 ID）。
        :param transfer_attachments: 是否转存非正文插图附件；为 True 时转存所有附件，为 False 时仅处理插在正文里的图片。
        :param mailbox_type: 历史邮件箱类型，仅支持 "inbox" 或 "outbox"，默认 "inbox"。
        :return: {"attachments": [{"new_attach_id", "old_attach_id", "attach_attr", "is_inline"}, ...], "html_body": str}
        """
        if not (email_id or "").strip():
            raise ValueError("必须提供 email_id（原邮件ID）参数")
        if not (new_email_id or "").strip():
            raise ValueError("必须提供 new_email_id（新邮件ID）参数")
        email_id = str(email_id).strip()
        new_email_id = str(new_email_id).strip()

        ctx = self._request.get_history_mail_context(email_id, mailbox_type=mailbox_type or "inbox")
        if ctx.get("error"):
            raise RuntimeError(ctx.get("error", "获取历史邮件内容失败"))

        mct = ctx.get("MCT") or ""
        if not isinstance(mct, str):
            mct = str(mct)
        ats_raw = ctx.get("ATS") or ""
        ats_list = ats_to_list(ats_raw)
        if not ats_list:
            return {"attachments": [], "html_body": mct}

        attachments_result: List[Dict[str, Any]] = []
        for idx, item in enumerate(ats_list, start=1):
            if not isinstance(item, dict):
                continue
            old_attach_id = str(item.get("ID") or item.get("id") or "").strip()
            if not old_attach_id:
                continue
            i_val = item.get("I") or item.get("i")
            is_inline = str(i_val) == "1"

            if not is_inline and not transfer_attachments:
                continue

            new_attach_id = new_email_id + f"{idx:04d}"

            try:
                ret = self._request.upload_history_mail_attach_to_zmail(
                    item, new_attach_id, email_id
                )
            except Exception as e:
                # 网络/服务抖动可能导致转挂失败：捕获通讯错误并重试一次。
                time.sleep(0.3)
                try:
                    ret = self._request.upload_history_mail_attach_to_zmail(
                        item, new_attach_id, email_id
                    )
                except Exception:
                    # 重试仍失败：跳过该附件转挂处理，继续处理下一个附件。
                    continue

            if not isinstance(ret, dict):
                continue

            new_attach_id = ret.get("new_attach_id") or new_attach_id
            attach_attr = ret.get("attach_attr") or {}

            if is_inline:
                mct = mct.replace(old_attach_id, new_attach_id)

            attachments_result.append({
                "new_attach_id": new_attach_id,
                "old_attach_id": old_attach_id,
                "attach_attr": attach_attr,
                "is_inline": is_inline,
            })

        return {
            "attachments": attachments_result,
            "html_body": mct,
        }

    def down_attach(
        self,
        email_id: str,
        save_path: str,
        attach_id_list: Optional[List[str]] = None,
        download_inline: bool = True,
    ) -> Dict[str, Any]:
        """
        下载邮件附件到指定目录。
        :param email_id: 邮件 ID。
        :param save_path: 保存目录路径（不存在则自动创建）。
        :param attach_id_list: 附件 ID 列表，仅下载这些附件；为 None 或空则下载所有附件（跳过 I='1'）。
        :param download_inline: 是否下载插在正文里的图片（I=1）。默认 True（下载）；为 False 时跳过。
        :return: 成功时 {"status": True, "list": [...]}，外部邮件时 {"status": False, "error": "外部邮件不支持下载附件"}。
        """
        if not email_id or not str(email_id).strip():
            raise ValueError("必须提供 email_id 参数")
        if not save_path or not str(save_path).strip():
            raise ValueError("必须提供 save_path 参数")
        email_id = str(email_id).strip()
        save_path = str(save_path).strip()
        if is_history_mail_id(email_id):
            d = self._request.get_history_mail_context(email_id)
        else:
            d = self._request.get_mail_context(email_id)
        if str(d.get("IOM", "")) == "1":
            return {"status": False, "error": "外部邮件不支持下载附件"}
        ats_raw = d.get("ATS")
        ats_list = ats_to_list(ats_raw)
        id_set = None
        if attach_id_list:
            id_set = {str(aid).strip() for aid in attach_id_list if aid is not None and str(aid).strip()}
        ats_id_set = {str(item.get("ID") or item.get("id", "")).strip() for item in ats_list if (item.get("ID") or item.get("id"))}
        dir_path = Path(save_path)
        dir_path.mkdir(parents=True, exist_ok=True)
        result: List[dict] = []
        for item in ats_list:
            # 附件授权处理（仅当邮件 AE 为 "1" 时生效）
            self._apply_attach_auth(d, item)
            attach_id = (item.get("ID") or item.get("id") or "")
            name = (item.get("N") or item.get("n") or "")
            # 统一清理，避免 ATS 返回的 ID 带空格导致 id_set 匹配失败
            attach_id = str(attach_id).strip()
            name = str(name).strip()
            if not attach_id:
                continue
            if item.get("I") in ("1", 1) and not download_inline:
                # 即使 attach_id_list 中指定了该正文插图附件 ID，只要配置为不下载正文中的图片，则跳过
                continue
            if id_set is not None and attach_id not in id_set:
                continue
            base_name = _sanitize_filename(name) if name else f"attach_{attach_id}"
            if is_history_mail_id(email_id):
                download_url, err = self._request.get_history_mail_attach_download_url(
                    item, email_id, self.emp_no, self.emp_no
                )
            else:
                download_url, err = self._request.get_attach_download_url(
                    item, self.emp_no, self.emp_no
                )
            if err or not download_url:
                result.append({
                    "status": False,
                    "id": str(attach_id),
                    "msg": f"{name or '附件'}未获取到下载地址，原因是{err or '未知错误'}",
                })
                continue
            file_path = _unique_path(dir_path, base_name)
            last_err: Optional[Exception] = None
            success = False
            for attempt in range(3):
                try:
                    if is_history_mail_id(email_id):
                        headers = dict(self._request._headers_history_mail())
                    else:
                        headers = dict(self._request._headers())
                    headers["Cookie"] = f"zMail_tid_prod={self._request.zmail_tid}"
                    resp = request_get(download_url, headers=headers, timeout=60)
                    resp.raise_for_status()
                    data = resp.content
                    file_path.write_bytes(data)
                    if attempt == 0:
                        msg = f"{name or '附件'}成功下载"
                    else:
                        msg = f"{name or '附件'}成功下载（第{attempt + 1}次尝试）"
                    result.append({
                        "status": True,
                        "id": str(attach_id),
                        "msg": msg,
                    })
                    success = True
                    break
                except requests.exceptions.RequestException as e:
                    last_err = build_zmail_network_error(e)
                    if attempt < 2:
                        time.sleep(0.5)
                except Exception as e:
                    last_err = e
                    # 若仍有剩余重试次数，稍作等待
                    if attempt < 2:
                        time.sleep(0.5)
            if not success:
                result.append({
                    "status": False,
                    "id": str(attach_id),
                    "msg": f"{name or '附件'}下载失败，已重试3次，最后一次错误为{str(last_err)}，下载地址为: {download_url}",
                })
            # 若还有后续附件要下载，则稍作间隔，避免连续请求过于频繁
            time.sleep(0.5)
        return {"status": True, "data": result}

    def _stype_from_filename(self, filename: str) -> int:
        """按文件后缀返回 SType：0=其他 1=文档 2=图片 3=音频 4=视频 9=压缩。"""
        ext = (filename.rsplit(".", 1)[-1].lower() if "." in filename else "") or ""
        if ext in _STYPE_DOC:
            return 1
        if ext in _STYPE_IMAGE:
            return 2
        if ext in _STYPE_AUDIO:
            return 3
        if ext in _STYPE_VIDEO:
            return 4
        if ext in _STYPE_ARCHIVE:
            return 9
        return 0

    def _is_image_filename(self, filename: str) -> bool:
        """按后缀判断是否图片（与 _STYPE_IMAGE / SType=2 一致）。"""
        ext = (filename.rsplit(".", 1)[-1].lower() if "." in filename else "") or ""
        return ext in _STYPE_IMAGE

    def _effective_inline(self, is_inline: str, filename: str) -> str:
        """
        实际是否作为正文插图（插入正文）：须同时满足「参数 is_inline 为 1」且「文件名为图片后缀」。
        与上传 D 中 isInline、返回 ATS 项中 I 一致。
        """
        want = str(is_inline or "0").strip() == "1"
        return "1" if (want and self._is_image_filename(filename)) else "0"

    def _build_d_entry(
        self,
        mail_id: str,
        index_1based: int,
        filename: str,
        is_inline: str = "0",
    ) -> dict:
        """
        构造 D 中单条：ID=邮件ID+5位序号，SName=URL 编码文件名，SType 由后缀推断。
        :param is_inline: 是否希望插入正文；isInline 由 _effective_inline 结合图片后缀得到。
        """
        entry_id = f"{mail_id}{str(index_1based).zfill(5)}"
        sname = urllib.parse.quote(filename, safe="")
        stype = self._stype_from_filename(filename)
        eff_inline = self._effective_inline(is_inline, filename)
        return {
            "SType": stype,
            "ID": entry_id,
            "SName": sname,
            "CID": "upinfo",
            "isInline": eff_inline,
            "Data": entry_id,
        }

    def _build_multipart_body(
        self,
        uno: str,
        d_param: Union[List[Any], Dict[str, Any], str],
        file_bytes: bytes,
        filename: str,
        req_id: str,
    ) -> Tuple[bytes, str]:
        """
        构建 multipart/form-data 请求体与 boundary。
        :return: (body_bytes, boundary)
        """
        boundary = "----ZmailUploadBoundary" + str(int(time.time() * 1000))
        crlf = b"\r\n"
        body: List[bytes] = []

        def add_field(name: str, value: str):
            body.append(f"--{boundary}".encode("utf-8"))
            body.append(crlf)
            body.append(f'Content-Disposition: form-data; name="{name}"'.encode("utf-8"))
            body.append(crlf)
            body.append(crlf)
            body.append(value.encode("utf-8"))
            body.append(crlf)

        add_field("UNO", f"{uno}")
        if isinstance(d_param, (list, dict)):
            d_str = json.dumps(d_param, ensure_ascii=False)
        else:
            d_str = str(d_param)
        add_field("D", d_str)
        add_field("type", "ajax")
        add_field("ReqID", f'"{req_id}"')

        body.append(f"--{boundary}".encode("utf-8"))
        body.append(crlf)
        body.append(f'Content-Disposition: form-data; name="upfile"; filename="{filename}"'.encode("utf-8"))
        body.append(crlf)
        body.append(b"Content-Type: application/octet-stream")
        body.append(crlf)
        body.append(crlf)
        body.append(file_bytes)
        body.append(crlf)
        body.append(f"--{boundary}--".encode("utf-8"))
        body.append(crlf)

        return b"".join(body), boundary

    def _check_one_file(self, file_path: str) -> Tuple[bool, Tuple[str, str]]:
        """
        纯校验单个文件（存在性、是否文件、大小、可读），不读取内容。
        :return: (True, (abs_path, filename)) 通过；(False, (path, reason)) 未通过。
        """
        if not (file_path and str(file_path).strip()):
            return False, (file_path or "", "文件路径不能为空")
        path = os.path.abspath(os.path.expanduser(file_path.strip()))
        if not os.path.exists(path):
            return False, (path, "文件不存在")
        if not os.path.isfile(path):
            return False, (path, "路径不是文件")
        try:
            os.path.getsize(path)
        except OSError:
            return False, (path, "无法获取文件大小")
        if not os.access(path, os.R_OK):
            return False, (path, "无权限读取文件")
        return True, (path, os.path.basename(path))

    def _read_file(self, file_path: str) -> Tuple[bytes, str]:
        """
        内部使用：校验并读取单文件，失败抛异常。
        :return: (file_bytes, filename)
        """
        ok, result = self._check_one_file(file_path)
        if not ok:
            path, reason = result
            if "无权限" in reason:
                raise PermissionError(f"无权限读取文件: {path}")
            raise ValueError(f"{reason}: {path}")
        path, filename = result
        try:
            with open(path, "rb") as f:
                data = f.read()
        except PermissionError:
            raise PermissionError(f"无权限读取文件: {path}")
        except OSError as e:
            raise ValueError(f"读取文件失败: {e}: {path}")
        return data, filename

    def validate_files(
        self,
        file_paths: Union[str, List[str]],
    ) -> Tuple[bool, List[Tuple[str, str]]]:
        """
        对外接口：校验文件（存在性、是否文件、可读权限），不读取内容，不抛异常。
        附件数目不得超过 99 个，总大小不得超过 50MB。

        :param file_paths: 单个路径（str）或路径列表（List[str]）。
        :return:
          - 全部通过： (True, [])
          - 存在未通过： (False, [(path, reason), ...])，仅包含未通过项及原因。
        """
        if not file_paths:
            return False, [("", "文件列表不能为空")]
        if isinstance(file_paths, str):
            file_paths = [file_paths]
        if len(file_paths) > 99:
            return False, [("", f"附件数目不能超过99个（当前 {len(file_paths)} 个）")]
        failures: List[Tuple[str, str]] = []
        total_size = 0
        for path in file_paths:
            ok, result = self._check_one_file(path)
            if not ok:
                failures.append(result)
                continue
            abs_path, _ = result
            try:
                total_size += os.path.getsize(abs_path)
            except OSError:
                failures.append((abs_path, "无法获取文件大小"))
        if failures:
            return False, failures
        if total_size > 50 * 1024 * 1024:
            return False, [("", f"附件总大小不能超过50M（当前 {total_size} 字节）")]
        return True, []

    def upload_attach(
        self,
        mail_id: str,
        file_paths: Union[str, List[str]],
        is_inline: str = "0",
        start_index: int = 1,
        timeout: int = 60,
    ) -> Union[List[dict], dict]:
        """
        上传文件到 zMail Mapi（UpLoadFileJSONService）。
        D 由邮件 ID 与文件列表自动构造：ID=邮件ID+5位序号，SName=URL 编码文件名，SType 由后缀推断。
        ReqID 内部生成随机 GUID，无需传入。

        :param mail_id: 邮件 ID，用于生成每条 D 的 ID（邮件ID+00001、00002…）。
        :param file_paths: 待上传文件路径，可为单个路径或路径列表。
        :param is_inline: 是否希望把图片插入正文；仅当为 "1" 且文件为图片后缀时才会按正文插图上传，否则按普通附件处理。
        :param timeout: 请求超时秒数，默认 60。
        :return: 成功时返回附件信息列表 [{ID, N, U, S, I, T, RID, D, IsEncrpt}, ...]；
            其中 I 为是否实际作为正文插图，由「is_inline 为 1」且「文件为图片后缀」共同决定；任一次上传失败则立即终止并返回
            {"status": False, "error": "原因", "file_path": "失败文件路径", "index": 序号}。
        """
        if isinstance(file_paths, str):
            file_paths = [file_paths]
        if not file_paths:
            raise ValueError("文件列表不能为空")
        mail_id = (mail_id or "").strip()
        if not mail_id:
            raise ValueError("邮件 ID 不能为空")
        uno = self.emp_no
        if not uno:
            raise RuntimeError("无法获取工号，上传失败")
        results = []
        for i, path in enumerate(file_paths):
            idx = start_index + i
            path = path.strip()
            file_bytes, filename = self._read_file(path)
            d_entry = self._build_d_entry(mail_id, idx, filename, is_inline=is_inline)
            one_req_id = str(uuid.uuid4())
            body_bytes, boundary = self._build_multipart_body(
                uno, [d_entry], file_bytes, filename, one_req_id
            )
            try:
                resp = self._request.upload_one_file(
                    body_bytes=body_bytes,
                    boundary=boundary,
                    timeout=timeout,
                )
            except Exception as e:
                return {
                    "status": False,
                    "error": str(e),
                    "file_path": os.path.abspath(os.path.expanduser(path)),
                    "index": idx,
                }
            items = resp if isinstance(resp, list) else [resp]
            if not items or not isinstance(items[0], dict):
                return {
                    "status": False,
                    "error": "上传返回格式异常",
                    "file_path": os.path.abspath(os.path.expanduser(path)),
                    "index": idx,
                }
            item = items[0]
            if item.get("S") is False:
                msg = item.get("M") or item.get("C") or "上传失败"
                return {
                    "status": False,
                    "error": msg,
                    "code": item.get("C"),
                    "file_path": os.path.abspath(os.path.expanduser(path)),
                    "index": idx,
                    "response": item,
                }
            att_id = item.get("ID") or d_entry["ID"]
            att_name = item.get("FileName") or filename
            is_encrpt = item.get("IsEncrpt") or "0"
            try:
                file_size = os.path.getsize(os.path.abspath(os.path.expanduser(path)))
            except OSError:
                file_size = 0
            eff_inline = self._effective_inline(is_inline, filename)
            built = {
                "ID": att_id,
                "N": att_name,
                "U": uno,
                "S": file_size,
                "I": eff_inline,
                "T": d_entry["SType"],
                "RID": "",
                "D": f"{att_id}&IsEncrpt={is_encrpt}",
                "IsEncrpt": is_encrpt,
            }
            results.append(built)
        return results
