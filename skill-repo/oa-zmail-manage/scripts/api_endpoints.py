#!/usr/bin/env python3
"""
zMail API 端点 URL 拼装。

对外提供：
  - get_zmail_services_url(context)   pcbasedata 服务 URL
  - get_zmail_pcmail_url(context)     pcmail 服务 URL
  - get_zmail_pccontacts_url(context) pccontacts 服务 URL
  - get_zmail_pcfolder_url(context)   pcfolder 服务 URL（SysGetUFMC 等）
  - get_zmail_pcrecall_url(context)   pcrecall 服务 URL（SysReCallMail 撤回邮件）
  - get_zmail_pconline_read_url(context) pconlineRead 服务 URL（附件在线预览等）
  - get_zmail_download_url(context)    下载文件 Mapi 服务 URL（UpLoadFileJSONService/service.dssm）
  - get_zmail_upload_url(context)      上传文件 Mapi 服务 URL（UpLoadFileJSONService）
  - get_zmail_historymail_url(cmd)    历史邮件服务 URL（根据命令字拼接，主机取自 ZMAIL_HOST）

兼容 Python 3.6+。
"""

import os
from typing import Optional

# zMail 服务主机，可通过环境变量 ZMAIL_HOST 覆盖
ZMAIL_HOST = os.environ.get("ZMAIL_HOST", "zmail.zte.com.cn")

# 内部 URL 模板（由 ZMAIL_HOST 拼装），不对外暴露
_ZMAIL_SERVICES_URL_TEMPLATE = f"https://{ZMAIL_HOST}/{{context}}/zmailserver/pcbasedata/services.jssm"
_ZMAIL_PCMAIL_SERVICES_URL_TEMPLATE = f"https://{ZMAIL_HOST}/{{context}}/zmailserver/pcmail/services.jssm"
_ZMAIL_PCCONTACTS_SERVICES_URL_TEMPLATE = f"https://{ZMAIL_HOST}/{{context}}/zmailserver/pccontacts/services.jssm"
_ZMAIL_PCFOLDER_SERVICES_URL_TEMPLATE = f"https://{ZMAIL_HOST}/{{context}}/zmailserver/pcfolder/services.jssm"
_ZMAIL_PCRECALL_SERVICES_URL_TEMPLATE = f"https://{ZMAIL_HOST}/{{context}}/zmailserver/pcrecall/services.jssm"
_ZMAIL_PCONLINEREAD_SERVICES_URL_TEMPLATE = f"https://{ZMAIL_HOST}/{{context}}/zmailserver/pconlineRead/services.jssm"
_ZMAIL_DOWNLOAD_SERVICES_URL_TEMPLATE = f"https://{ZMAIL_HOST}/{{context}}/Mapi/UpLoadFileJSONService/service.dssm"
_ZMAIL_UPLOAD_SERVICES_URL_TEMPLATE = f"https://{ZMAIL_HOST}/{{context}}/Mapi/UpLoadFileJSONService/service.ussm"
_ZMAIL_UPLOAD_FILE_SERVICES_URL_TEMPLATE = f"https://{ZMAIL_HOST}/{{context}}/zmailserver/pcAttachment/services.jssm"

def _get_zmail_url(template: str, context: Optional[str] = None) -> str:
    """
    按模板与 context 拼 URL（内部使用）：将 "/{context}" 替换为 "/context值" 或空。
    """
    path_context = (context or "").strip()
    replace_with = ("/" + path_context) if path_context else ""
    return template.replace("/{context}", replace_with)


def get_zmail_services_url(context: Optional[str] = None) -> str:
    """返回 zmail pcbasedata 服务 URL。"""
    return _get_zmail_url(_ZMAIL_SERVICES_URL_TEMPLATE, context)


def get_zmail_pcmail_url(context: Optional[str] = None) -> str:
    """返回 zmail pcmail 服务 URL。"""
    return _get_zmail_url(_ZMAIL_PCMAIL_SERVICES_URL_TEMPLATE, context)


def get_zmail_pccontacts_url(context: Optional[str] = None) -> str:
    """返回 zmail pccontacts 服务 URL。"""
    return _get_zmail_url(_ZMAIL_PCCONTACTS_SERVICES_URL_TEMPLATE, context)


def get_zmail_pcfolder_url(context: Optional[str] = None) -> str:
    """返回 zmail pcfolder 服务 URL（用于 SysGetUFMC 获取分类邮件总数等）。"""
    return _get_zmail_url(_ZMAIL_PCFOLDER_SERVICES_URL_TEMPLATE, context)


def get_zmail_pcrecall_url(context: Optional[str] = None) -> str:
    """返回 zmail pcrecall 服务 URL（用于 SysReCallMail 撤回邮件）。"""
    return _get_zmail_url(_ZMAIL_PCRECALL_SERVICES_URL_TEMPLATE, context)


def get_zmail_pconline_read_url(context: Optional[str] = None) -> str:
    """返回 zmail pconlineRead 服务 URL（用于附件在线预览 SysAttachOnlineView 等）。"""
    return _get_zmail_url(_ZMAIL_PCONLINEREAD_SERVICES_URL_TEMPLATE, context)


def get_zmail_download_url(context: Optional[str] = None) -> str:
    """返回 zmail 下载文件 Mapi 服务 URL（UpLoadFileJSONService/service.dssm），用于获取附件下载地址。"""
    return _get_zmail_url(_ZMAIL_DOWNLOAD_SERVICES_URL_TEMPLATE, context)


def get_zmail_upload_url(context: Optional[str] = None) -> str:
    """返回 zmail 上传文件 Mapi 服务 URL（UpLoadFileJSONService），不含查询参数，查询参数由调用方拼装。"""
    return _get_zmail_url(_ZMAIL_UPLOAD_SERVICES_URL_TEMPLATE, context)
def get_zmail_upload_file_url(context: Optional[str] = None) -> str:
    """返回 zmail 上传文件 Mapi 服务 URL（upLoadFile），不含查询参数，查询参数由调用方拼装。"""
    return _get_zmail_url(_ZMAIL_UPLOAD_FILE_SERVICES_URL_TEMPLATE, context)


# 历史邮件服务固定路径（仅用 ZMAIL_HOST，不含 context），命令字由调用方指定
_ZMAIL_HISTORYMAIL_URL_TEMPLATE = "https://{host}/zte-km-zmail-historymail/mailhis/{cmd}"


def get_zmail_historymail_url(cmd: str = "sysGetMailList") -> str:
    """返回历史邮件服务 URL，命令字由 cmd 指定，主机取自 ZMAIL_HOST。"""
    cmd = (cmd or "sysGetMailList").strip() or "sysGetMailList"
    return _ZMAIL_HISTORYMAIL_URL_TEMPLATE.format(host=ZMAIL_HOST, cmd=cmd)