#!/usr/bin/env python3
"""
zMail 技能统一入口。子命令（顺序同 python main.py --help）：
search-emails, view, download-attach, transfer-attach, mark-email, reply, reply-brief, forward, check-forward, summary,
draft-list, create-draft, save-draft, operate-draft, rollback-sent, send, folder-list,
delete-mails, move-emails, recover-mails, search-users, get-time, my-info。

支持 Python 3.6.x 及以上。
"""

import argparse
import io
import json
import os
import sys
import time

# 确保脚本目录在 path 中
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from log import send_page_load_event
from util import split_email_cli_arg, validate_mail_id
from zmail_search_emails import ZmailSearchEmails
from zmail_send import ZmailSend
from zmail_draft import ZmailDraft
from zmail_summary import ZmailSummary

# Windows 下 stdout/stderr 默认 GBK，输出含 \xa0 等字符会报错，统一为 UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:  # Python 3.6 无 reconfigure
        if getattr(sys.stdout, "buffer", None) is not None:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        if getattr(sys.stderr, "buffer", None) is not None:
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


def _split_to_cc_bcc(args):
    """从 args 解析 --to/--cc/--bcc（逗号分隔邮箱）。返回 (to_list, cc_list, bcc_list, error_or_None)。"""
    to_list, err = split_email_cli_arg(getattr(args, "to", None) or "")
    if err:
        return None, None, None, f"--to: {err}"
    cc_list, err = split_email_cli_arg(getattr(args, "cc", None) or "")
    if err:
        return None, None, None, f"--cc: {err}"
    bcc_list, err = split_email_cli_arg(getattr(args, "bcc", None) or "")
    if err:
        return None, None, None, f"--bcc: {err}"
    return to_list, cc_list, bcc_list, None


def _build_used_custom_props(args, subparsers_action, argv) -> dict:
    """仅记录本次实际使用到的参数（显式传参 + 实际传入的位置参数）。"""
    command = getattr(args, "command", None)
    custom_props = {"command": command} if command else {}
    if not command:
        return custom_props

    subparser = subparsers_action.choices.get(command)
    if subparser is None:
        return custom_props

    # 仅统计当前子命令后的参数（argv: [script, command, ...]）
    cmd_tokens = list(argv[2:]) if len(argv) > 2 else []

    # 显式传入的可选参数（--xxx / --xxx=yyy）。
    explicit_option_dests = set()
    for token in cmd_tokens:
        if not token.startswith("-"):
            continue
        opt = token.split("=", 1)[0]
        action = subparser._option_string_actions.get(opt)
        if action and action.dest != "help":
            explicit_option_dests.add(action.dest)

    # 实际传入的位置参数（避免把可选位置参数默认值当成“使用到”）。
    for action in subparser._actions:
        if action.option_strings or action.dest in ("help", "command"):
            continue
        val = getattr(args, action.dest, None)
        if action.nargs == "?" and val == action.default:
            continue
        if action.nargs == "*" and (val == action.default or val == []):
            continue
        if val is not None:
            custom_props[action.dest] = val

    for dest in sorted(explicit_option_dests):
        custom_props[dest] = getattr(args, dest, None)

    return {k: v for k, v in custom_props.items() if v is not None}


def _cmd_search_emails(args):
    categories = None
    if getattr(args, "categories", "") and str(args.categories).strip():
        categories = [c.strip().lower() for c in str(args.categories).split(",") if c.strip()]
    q = ZmailSearchEmails()
    result = q.search(
        mailbox_type=getattr(args, "mailbox_type", "inbox") or "inbox",
        keyword=args.keyword or "",
        days=args.days,
        sender=args.sender or "",
        recipients=getattr(args, "recipients", "") or "",
        cc=getattr(args, "cc", "") or "",
        content=getattr(args, "content", "") or "",
        attachment_name=getattr(args, "attachment_name", "") or "",
        mail_attribute=getattr(args, "mail_attribute", "") or "",
        folder=getattr(args, "folder", "") or "",
        start_date=(getattr(args, "start_date", "") or "").strip() or None,
        end_date=(getattr(args, "end_date", "") or "").strip() or None,
        categories=categories,
    )
    print(json.dumps(result, ensure_ascii=False))


def _add_search_emails_parser(sub):
    p = sub.add_parser("search-emails", help="搜索邮件")
    p.add_argument("keyword", nargs="?", default="", help="关键字")
    p.add_argument(
        "--mailbox-type",
        default="inbox",
        dest="mailbox_type",
        choices=["inbox", "outbox", "deleted", "spam", "folder"],
        help="箱体类型：inbox/outbox/deleted（已删除）/spam（垃圾邮件）/folder，默认 inbox",
    )
    p.add_argument("--days", type=int, default=None, help="最近 N 天")
    p.add_argument("--start-date", default="", dest="start_date", help="起始日期 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS")
    p.add_argument("--end-date", default="", dest="end_date", help="结束日期 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS")
    p.add_argument("--sender", default="", help="发件人姓名")
    p.add_argument("--recipients", default="", help="收件人")
    p.add_argument("--cc", default="", help="抄送人")
    p.add_argument("--content", default="", help="正文内容")
    p.add_argument("--attachment-name", default="", dest="attachment_name", help="附件名称")
    p.add_argument("--mail-attribute", default="", dest="mail_attribute", help="邮件属性：attachment（附件）/external（外部），空为全部")
    p.add_argument(
        "--folder",
        default="",
        help="文件夹名称或纯数字文件夹 ID（仅 --mailbox-type folder 时有效；纯数字按 ID 匹配，否则按名称解析）",
    )
    p.add_argument("--categories", default="", help="逗号分隔：unread,starred,urgent,to_me；收件箱搜索时按分类筛选")
    p.set_defaults(func=_cmd_search_emails)


def _cmd_view(args):
    # view 子命令仅支持单封邮件；多封总结请使用 summary 子命令
    raw = (getattr(args, "email_id", "") or "").strip()
    if not raw:
        print(json.dumps({"error": "必须提供邮件 ID"}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    segments = [x.strip() for x in str(raw).split(",") if x and x.strip()]
    if len(segments) > 1:
        print(json.dumps({"error": "仅支持查看单封邮件，多个 ID 请分别调用 view 或使用 summary 子命令"}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    email_id = segments[0] if segments else raw
    valid, reason = validate_mail_id(email_id)
    if not valid:
        print(json.dumps({"error": f"邮件 ID「{email_id}」：{reason}"}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    from zmail_view import ZmailView
    v = ZmailView()
    out = v.show_email(email_id)
    print(json.dumps(out, ensure_ascii=False))


def _add_view_parser(sub):
    p = sub.add_parser(
        "view",
        help="查看单封邮件正文（含附件）",
        description="查看单封邮件正文（含附件）。仅支持单个邮件 ID；详细规则查看 view.md。",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument("email_id", help="邮件 ID（单封）")
    p.set_defaults(func=_cmd_view)


def _cmd_download_attach(args):
    """下载邮件附件到指定目录。"""
    email_id = (getattr(args, "email_id", "") or "").strip()
    valid, reason = validate_mail_id(email_id)
    if not valid:
        print(json.dumps({"status": False, "error": reason}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    from zmail_attachment import ZmailAttachment
    attach_ids = None
    if getattr(args, "attach_ids", None) and str(args.attach_ids).strip():
        attach_ids = [aid.strip() for aid in str(args.attach_ids).split(",") if aid.strip()]
    client = ZmailAttachment()
    result = client.down_attach(
        email_id=email_id,
        save_path=args.save_path,
        attach_id_list=attach_ids,
        download_inline=getattr(args, "download_inline", True),
    )
    if result.get("status"):
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


def _add_download_attach_parser(sub):
    p = sub.add_parser("download-attach", help="下载邮件附件到指定目录")
    p.add_argument("--email-id", required=True, dest="email_id", help="邮件 ID")
    p.add_argument("--save-path", required=True, dest="save_path", help="保存目录路径（不存在则自动创建）")
    p.add_argument("--attach-ids", default="", dest="attach_ids", help="附件 ID 列表，逗号分隔；不传则下载所有附件")
    p.add_argument(
        "--no-download-inline",
        action="store_false",
        dest="download_inline",
        default=True,
        help="不下载邮件正文中的图片；不传则默认下载所有附件（含正文中的图片）",
    )
    p.set_defaults(func=_cmd_download_attach)


def _cmd_transfer_attach(args):
    """将邮件附件转存到云盘。"""
    email_id = (getattr(args, "email_id", "") or "").strip()
    valid, reason = validate_mail_id(email_id)
    if not valid:
        print(json.dumps({"status": False, "error": reason}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    from zmail_attachment import ZmailAttachment
    attach_ids = None
    if getattr(args, "attach_ids", None) and str(args.attach_ids).strip():
        attach_ids = [aid.strip() for aid in str(args.attach_ids).split(",") if aid.strip()]
    client = ZmailAttachment()
    result = client.transfer_attach(
        email_id=email_id,
        attach_ids=attach_ids,
        transfer_inline=bool(getattr(args, "transfer_inline", True)),
    )
    print(json.dumps(result, ensure_ascii=False))


def _add_transfer_attach_parser(sub):
    p = sub.add_parser("transfer-attach", help="将邮件附件转存到云盘")
    p.add_argument("--email-id", required=True, dest="email_id", help="邮件 ID")
    p.add_argument("--attach-ids", default="", dest="attach_ids", help="附件 ID 列表，逗号分隔；不传则转存所有附件")
    p.add_argument(
        "--no-transfer-inline",
        action="store_false",
        dest="transfer_inline",
        default=True,
        help="不转存邮件正文中的图片；不传则默认转存所有附件（含正文中的图片）",
    )
    p.set_defaults(func=_cmd_transfer_attach)


def _cmd_mark_email(args):
    """标记邮件：已读/未读/标星/取消标星。"""
    categories = None
    if getattr(args, "categories", "") and str(args.categories).strip():
        categories = [c.strip().lower() for c in str(args.categories).split(",") if c.strip()]
    from zmail_mark import ZmailMark
    client = ZmailMark()
    email_ids_raw = (getattr(args, "email_ids", "") or "").strip()
    result = client.mark(
        email_ids=email_ids_raw or None,
        status=getattr(args, "status", "read"),
        mailbox_type=getattr(args, "mailbox_type", "inbox") or "inbox",
        keyword=args.keyword or "",
        days=args.days,
        sender=args.sender or "",
        recipients=getattr(args, "recipients", "") or "",
        cc=getattr(args, "cc", "") or "",
        content=getattr(args, "content", "") or "",
        attachment_name=getattr(args, "attachment_name", "") or "",
        mail_attribute=getattr(args, "mail_attribute", "") or "",
        folder=getattr(args, "folder", "") or "",
        start_date=(getattr(args, "start_date", "") or "").strip() or None,
        end_date=(getattr(args, "end_date", "") or "").strip() or None,
        categories=categories,
    )
    if result.get("status"):
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


def _add_mark_email_parser(sub):
    # mark-email（标记邮件：已读/未读/标星/取消标星；--email-ids 优先，未传时按搜索条件先搜再标）
    p = sub.add_parser("mark-email", help="标记邮件：已读/未读/标星/取消标星")
    p.add_argument("keyword", nargs="?", default="", help="搜索关键字（未传 --email-ids 时与 search-emails 一致）")
    p.add_argument("--email-ids", default="", dest="email_ids", help="邮件 ID，多个用逗号分隔；传入则仅标记这些 ID，忽略搜索条件")
    p.add_argument(
        "--status",
        default="read",
        choices=["read", "unread", "starred", "unstarred"],
        help="标记状态：read（已读）、unread（未读）、starred（标星）、unstarred（取消标星），默认 read",
    )
    p.add_argument(
        "--mailbox-type",
        default="inbox",
        dest="mailbox_type",
        choices=["inbox", "outbox", "deleted", "spam", "folder"],
        help="箱体类型，默认 inbox",
    )
    p.add_argument("--days", type=int, default=None, help="最近 N 天（未传 --email-ids 时有效）")
    p.add_argument("--start-date", default="", dest="start_date", help="起始日期 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS")
    p.add_argument("--end-date", default="", dest="end_date", help="结束日期 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS")
    p.add_argument("--sender", default="", help="发件人姓名")
    p.add_argument("--recipients", default="", help="收件人")
    p.add_argument("--cc", default="", help="抄送人")
    p.add_argument("--content", default="", help="正文内容")
    p.add_argument("--attachment-name", default="", dest="attachment_name", help="附件名称")
    p.add_argument("--mail-attribute", default="", dest="mail_attribute", help="邮件属性：attachment（附件）/external（外部），空为全部")
    p.add_argument(
        "--folder",
        default="",
        help="文件夹名称或纯数字文件夹 ID（仅 --mailbox-type folder 时有效；纯数字按 ID 匹配，否则按名称解析）",
    )
    p.add_argument("--categories", default="", help="逗号分隔：unread,starred,urgent,to_me；收件箱搜索时按分类筛选")
    p.set_defaults(func=_cmd_mark_email)


def _cmd_reply(args):
    email_id = (getattr(args, "email_id", "") or "").strip()
    valid, reason = validate_mail_id(email_id)
    if not valid:
        print(json.dumps({"status": False, "error": reason}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    from zmail_reply import ZmailReply
    client = ZmailReply()
    with_attachments = bool(getattr(args, "with_attachments", False))
    to_list, cc_list, bcc_list, err = _split_to_cc_bcc(args)
    if err:
        print(json.dumps({"status": False, "error": err}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    if to_list or cc_list or bcc_list:
        result = client.reply(
            email_id,
            args.content,
            to_list=to_list,
            cc_list=cc_list,
            bcc_list=bcc_list,
            with_attachments=with_attachments,
        )
    else:
        result = client.reply(
            email_id,
            args.content,
            with_attachments=with_attachments,
        )
    if result.get("status"):
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


def _add_reply_parser(sub):
    p = sub.add_parser("reply", help="回复邮件")
    p.add_argument("email_id", help="邮件 ID")
    p.add_argument("content", help="回复内容")
    p.add_argument("--to", default="", help="可选。主送邮箱，多个逗号分隔（含群组邮箱）；不传则仅回复原发件人")
    p.add_argument("--cc", default="", help="可选。抄送邮箱，多个逗号分隔")
    p.add_argument("--bcc", default="", help="可选。密送邮箱，多个逗号分隔")
    p.add_argument(
        "--with-attachments",
        action="store_true",
        default=False,
        dest="with_attachments",
        help="可选。是否携带原邮件附件；不传则默认不带",
    )
    p.set_defaults(func=_cmd_reply)


def _cmd_email_brief(args):
    email_id = (getattr(args, "email_id", "") or "").strip()
    valid, reason = validate_mail_id(email_id)
    if not valid:
        print(json.dumps({"status": False, "error": reason}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    from zmail_reply import ZmailReply
    client = ZmailReply()
    result = client.get_email_brief(email_id)
    print(json.dumps(result, ensure_ascii=False))


def _add_reply_brief_parser(sub):
    p = sub.add_parser("reply-brief", help="获取邮件摘要（标题、收件人列表），用于回复前展示")
    p.add_argument("email_id", help="邮件 ID")
    p.set_defaults(func=_cmd_email_brief)


def _cmd_forward(args):
    """转发指定邮件，收件人为 --to/--cc/--bcc，转发正文可选。"""
    email_id = (args.email_id or "").strip()
    valid, reason = validate_mail_id(email_id)
    if not valid:
        print(json.dumps({"status": False, "error": reason}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    to_list, cc_list, bcc_list, err = _split_to_cc_bcc(args)
    if err:
        print(json.dumps({"status": False, "error": err}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    if not (to_list or cc_list or bcc_list):
        print(json.dumps({"status": False, "error": "转发须提供 --to / --cc / --bcc 至少一项（逗号分隔邮箱）。"}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    from zmail_forward import ZmailForward
    client = ZmailForward()
    content = (args.content or "").strip() if getattr(args, "content", None) is not None else ""
    with_attachments = str(getattr(args, "with_attachments", "true")).lower() == "true"
    result = client.forward(
        email_id=email_id,
        to_list=to_list,
        cc_list=cc_list,
        bcc_list=bcc_list,
        content=content,
        with_attachments=with_attachments,
    )
    if result.get("status"):
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


def _add_forward_parser(sub):
    p = sub.add_parser("forward", help="转发指定邮件，收件人为逗号分隔邮箱")
    p.add_argument("email_id", help="要转发的邮件 ID")
    p.add_argument("content", nargs="?", default="", help="可选转发正文，为空则仅转发原文")
    p.add_argument("--to", default="", help="主送邮箱，多个逗号分隔（可与群组邮箱混排）")
    p.add_argument("--cc", default="", help="抄送邮箱，多个逗号分隔")
    p.add_argument("--bcc", default="", help="密送邮箱，多个逗号分隔")
    p.add_argument(
        "--with-attachments",
        default="true",
        choices=["true", "false"],
        dest="with_attachments",
        help="是否携带原邮件附件，默认 true；不带附件传 false",
    )
    p.set_defaults(func=_cmd_forward)


def _cmd_check_forward(args):
    """检查指定邮件是否支持转发。"""
    email_id = (args.email_id or "").strip()
    valid, reason = validate_mail_id(email_id)
    if not valid:
        print(json.dumps({"status": False, "error": reason}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    from zmail_forward import ZmailForward
    client = ZmailForward()
    result = client.check_can_forward(email_id=email_id)
    if result.get("status"):
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


def _add_check_forward_parser(sub):
    p = sub.add_parser("check-forward", help="检查指定邮件是否支持转发")
    p.add_argument("email_id", help="要检查的邮件 ID")
    p.set_defaults(func=_cmd_check_forward)


def _cmd_summary(args):
    def _parse_cats(s):
        if not s or not s.strip():
            return None
        return [x.strip() for x in s.split(",") if x and x.strip()]

    def _parse_ids(s):
        if not s or not s.strip():
            return None
        return [x.strip() for x in s.split(",") if x and x.strip()]

    s = ZmailSummary()
    result = s.summary_emails(
        mailbox_type=args.mailbox_type or "inbox",
        keyword=args.keyword or "",
        days=args.days,
        start_date=args.start_date.strip() or None if args.start_date else None,
        end_date=args.end_date.strip() or None if args.end_date else None,
        sender=args.sender or "",
        recipients=args.recipients or "",
        cc=args.cc or "",
        content=args.content or "",
        attachment_name=args.attachment_name or "",
        mail_attribute=args.mail_attribute or "",
        folder=args.folder or "",
        categories=_parse_cats(args.categories or ""),
        ids=_parse_ids(getattr(args, "ids", "") or ""),
    )
    if isinstance(result, dict) and result.get("error"):
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, ensure_ascii=False))


def _add_summary_parser(sub):
    p = sub.add_parser("summary", help="总结邮件")
    p.add_argument("keyword", nargs="?", default="", help="关键字")
    p.add_argument(
        "--mailbox-type",
        default="inbox",
        dest="mailbox_type",
        choices=["inbox", "outbox", "deleted", "spam", "folder"],
        help="箱体类型：inbox/outbox/deleted（已删除）/spam（垃圾邮件）/folder，默认 inbox",
    )
    p.add_argument("--days", type=int, default=None, help="最近 N 天")
    p.add_argument("--start-date", default="", dest="start_date", help="起始日期 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS")
    p.add_argument("--end-date", default="", dest="end_date", help="结束日期 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS")
    p.add_argument("--sender", default="", help="发件人姓名")
    p.add_argument("--recipients", default="", help="收件人")
    p.add_argument("--cc", default="", help="抄送人")
    p.add_argument("--content", default="", help="正文内容")
    p.add_argument("--attachment-name", default="", dest="attachment_name", help="附件名称")
    p.add_argument("--mail-attribute", default="", dest="mail_attribute", help="邮件属性：attachment（附件）/external（外部），空为全部")
    p.add_argument(
        "--folder",
        default="",
        help="文件夹名称或纯数字文件夹 ID（仅 --mailbox-type folder 时有效；纯数字按 ID 匹配，否则按名称解析）",
    )
    p.add_argument("--categories", default="", help="逗号分隔：unread,starred,urgent,to_me；收件箱搜索时按分类筛选")
    p.add_argument("--ids", default="", help="邮件 ID 列表，逗号分隔；传入时直接按 ID 总结，忽略其他搜索参数")
    p.set_defaults(func=_cmd_summary)


def _cmd_draft_list(args):
    sch = ZmailDraft()
    result = sch.get_draft_list(pno=args.pno, psize=args.psize)
    print(json.dumps(result, ensure_ascii=False))


def _add_draft_list_parser(sub):
    p = sub.add_parser("draft-list", help="草稿箱列表")
    p.add_argument("--pno", type=int, default=1, help="页码，默认 1")
    p.add_argument("--psize", type=int, default=200, help="每页条数，默认 200")
    p.set_defaults(func=_cmd_draft_list)


def _cmd_rollback_sent(args):
    """撤回已发送邮件。"""
    from zmail_outbox import ZmailOutbox
    email_id = (args.email_id or "").strip()
    valid, reason = validate_mail_id(email_id)
    if not valid:
        print(json.dumps({"status": False, "error": reason}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    client = ZmailOutbox()
    result = client.rollback_send_mail(email_id)
    print(json.dumps(result, ensure_ascii=False))


def _add_rollback_sent_parser(sub):
    p = sub.add_parser("rollback-sent", help="撤回已发送的邮件")
    p.add_argument("--email-id", required=True, dest="email_id", help="要撤回的邮件 ID")
    p.set_defaults(func=_cmd_rollback_sent)


def _cmd_create_draft(args):
    to_list, cc_list, bcc_list, err = _split_to_cc_bcc(args)
    if err:
        print(json.dumps({"status": False, "error": err}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    if not (to_list or cc_list or bcc_list):
        print(json.dumps({"status": False, "error": "请提供 --to / --cc / --bcc 至少一项（逗号分隔邮箱）。"}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    attachments = None
    if getattr(args, "attachments", None) and str(args.attachments).strip():
        attachments = [p.strip() for p in str(args.attachments).split(",") if p.strip()]
    is_inline = str(getattr(args, "is_inline", "0") or "0").strip() or "0"
    sch = ZmailDraft()
    result = sch.create_draft(
        args.title or "",
        to_list,
        cc_list,
        args.body or "",
        send_at=args.at,
        bcc_list=bcc_list or None,
        attachments=attachments,
        is_inline=is_inline,
    )
    if result.get("status"):
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


def _add_create_draft_parser(sub):
    p = sub.add_parser("create-draft", help="新建草稿并指定发送时间（收件人为逗号分隔邮箱）")
    p.add_argument("--title", default="")
    p.add_argument("--body", default="")
    p.add_argument("--to", default="", help="主送邮箱，多个逗号分隔")
    p.add_argument("--cc", default="", help="抄送邮箱，多个逗号分隔")
    p.add_argument("--bcc", default="", help="密送邮箱，多个逗号分隔")
    p.add_argument("--at", required=True, help="预定发送时间 YYYY-MM-DD HH:MM")
    p.add_argument("--attachments", default="", help="附件路径，多个用逗号分隔")
    # 与 send/save-draft 一致；底层上传接口名勿写入 argparse help
    p.add_argument(
        "--is-inline",
        default="0",
        dest="is_inline",
        help='是否将图片插入正文，"0" 或 "1"，默认 "0"',
    )
    p.set_defaults(func=_cmd_create_draft)


def _cmd_save_draft(args):
    to_list, cc_list, bcc_list, err = _split_to_cc_bcc(args)
    if err:
        print(json.dumps({"status": False, "error": err}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    draft_id = (getattr(args, "draft", "") or "").strip() or None
    del_raw = getattr(args, "attachments_del", None)
    if del_raw and str(del_raw).strip() and not draft_id:
        print(
            json.dumps(
                {"status": False, "error": "--attachments-del 仅在与 --draft 同时使用时有效。"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        sys.exit(1)
    attachments = _build_save_draft_attachments(
        getattr(args, "attachments", None),
        del_raw,
        has_draft=bool(draft_id),
    )
    is_inline = str(getattr(args, "is_inline", "0") or "0").strip() or "0"
    if draft_id and is_inline != "0":
        print(
            json.dumps(
                {
                    "status": False,
                    "error": "将图片插入正文仅支持新建草稿（不传 --draft）；编辑已有草稿时请勿使用 --is-inline。",
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        sys.exit(1)
    sch = ZmailDraft()
    result = sch.save_draft(
        title=args.title or "",
        to_list=to_list,
        cc_list=cc_list,
        body=args.body or "",
        bcc_list=bcc_list or None,
        attachments=attachments,
        draft_id=draft_id,
        is_inline=is_inline,
    )
    if result.get("status"):
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


def _add_save_draft_parser(sub):
    p = sub.add_parser("save-draft", help="新建邮件保存为草稿（不发送）")
    p.add_argument("--title", default="")
    p.add_argument("--body", default="")
    p.add_argument("--to", default="", help="可选。主送邮箱，多个逗号分隔（含群组邮箱）；不传可无收件人")
    p.add_argument("--cc", default="", help="可选。抄送邮箱，多个逗号分隔")
    p.add_argument("--bcc", default="", help="可选。密送邮箱，多个逗号分隔")
    p.add_argument(
        "--attachments",
        default="",
        help="可选。逗号分隔的本地文件路径，表示要添加的附件；传 --draft 时表示新增上传的附件。",
    )
    p.add_argument(
        "--attachments-del",
        default="",
        dest="attachments_del",
        help="可选。仅与 --draft 联用：逗号分隔的附件 ID，从原草稿删除对应附件。",
    )
    p.add_argument("--draft", default="", help="可选。草稿 ID；传入后按该 ID 更新保存，不再新建 ID。未传参数将从该草稿详情回填。")
    # 仅新建草稿路径有效；底层上传接口名勿写入 argparse help
    p.add_argument(
        "--is-inline",
        default="0",
        dest="is_inline",
        help='是否将图片插入正文，"0" 或 "1"，默认 "0"；仅在不传 --draft（新建草稿）时有效',
    )
    p.set_defaults(func=_cmd_save_draft)


def _comma_separated_nonempty(raw) -> list:
    """逗号分隔字符串 → 去空白后的非空片段列表。"""
    if raw is None:
        return []
    s = str(raw).strip()
    if not s:
        return []
    return [p.strip() for p in s.split(",") if p.strip()]


def _build_save_draft_attachments(paths_raw, del_raw, has_draft: bool):
    """
    save-draft 附件参数：
    - --attachments：始终为逗号分隔的本地文件路径，表示要上传/新增的附件。
    - --attachments-del：仅与 --draft 联用，逗号分隔的附件 ID，表示要从原草稿删除的附件。
    未传 --draft 时返回路径列表或 None；传 --draft 时返回 {"delete_ids","upload_paths"} 或 None（两者皆空表示不修改附件）。
    """
    upload_paths = _comma_separated_nonempty(paths_raw)
    if not has_draft:
        return upload_paths if upload_paths else None
    delete_ids = _comma_separated_nonempty(del_raw)
    if not upload_paths and not delete_ids:
        return None
    return {"delete_ids": delete_ids, "upload_paths": upload_paths}


def _cmd_send(args):
    to_list, cc_list, bcc_list, err = _split_to_cc_bcc(args)
    if err:
        print(json.dumps({"status": False, "error": err}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    if not (to_list or cc_list or bcc_list):
        print(json.dumps({"status": False, "error": "请提供 --to / --cc / --bcc 至少一项（逗号分隔邮箱）。"}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    attachments = None
    if getattr(args, "attachments", None) and str(args.attachments).strip():
        attachments = [p.strip() for p in str(args.attachments).split(",") if p.strip()]
    is_inline = str(getattr(args, "is_inline", "0") or "0").strip() or "0"
    sender = ZmailSend()
    result = sender.send_new_mail(
        args.title or "",
        to_list,
        cc_list,
        args.body or "",
        bcc_list=bcc_list or None,
        attachments=attachments,
        is_inline=is_inline,
    )
    if result.get("status"):
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


def _add_send_parser(sub):
    p = sub.add_parser("send", help="立即发送（收件人为逗号分隔邮箱）")
    p.add_argument("--title", default="")
    p.add_argument("--body", default="")
    p.add_argument("--to", default="", help="主送邮箱，多个逗号分隔（可与群组邮箱混排）")
    p.add_argument("--cc", default="", help="抄送邮箱，多个逗号分隔")
    p.add_argument("--bcc", default="", help="密送邮箱，多个逗号分隔")
    p.add_argument("--attachments", default="", help="附件路径，多个用逗号分隔")
    # 底层上传接口名勿写入 argparse help
    p.add_argument(
        "--is-inline",
        default="0",
        dest="is_inline",
        help='是否将图片插入正文，"0" 或 "1"，默认 "0"',
    )
    p.set_defaults(func=_cmd_send)


def _cmd_operate_draft(args):
    sch = ZmailDraft()
    if args.cancel:
        result = sch.cancel_draft_schedule(args.draft)
    elif args.now:
        result = sch.send_draft_now(args.draft)
    else:
        result = sch.schedule_draft_send(args.draft, send_at=args.at)
    if result.get("status"):
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


def _add_operate_draft_parser(sub):
    p = sub.add_parser("operate-draft", help="草稿设具体发送时间/立即发送/取消已设时间")
    p.add_argument("--draft", required=True, help="草稿 ID")
    p.add_argument(
        "--at",
        default=None,
        help="预定发送时间 YYYY-MM-DD HH:MM；与 --cancel / --now 互斥，用于首次或修改预约发送",
    )
    p.add_argument("--cancel", action="store_true", help="取消该草稿已设置的定时发送")
    p.add_argument("--now", action="store_true", help="立即发送该草稿")
    p.set_defaults(func=_cmd_operate_draft)


def _cmd_get_time(args):
    """获取当前系统时间（JSON），供相对时间解析使用。"""
    try:
        from get_current_time import get_current_time as _get_current_time
        result = _get_current_time()
        print(json.dumps(result, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


def _add_get_time_parser(sub):
    p = sub.add_parser("get-time", help="获取当前系统时间，输出 JSON")
    p.set_defaults(func=_cmd_get_time)


def _cmd_my_info(args):
    """获取当前用户完整信息（先取工号再调 get_user），用于代表「我」；输出 JSON 与 search-users 用户项一致（UNO/N/E/T 等）。"""
    try:
        from api_token import get_emp_no
        emp_no = (get_emp_no() or "").strip()
        if not emp_no:
            print(json.dumps({"status": False, "error": "无法获取当前用户工号"}, ensure_ascii=False), file=sys.stderr)
            sys.exit(1)
        from zmail_base import ZmailBase
        base = ZmailBase()
        info = base._get_user_info(emp_no)
        if not info or not (info.get("UNO") or info.get("E")):
            print(json.dumps({"status": False, "error": "无法获取当前用户信息"}, ensure_ascii=False), file=sys.stderr)
            sys.exit(1)
        print(json.dumps({"status": True, "user": info}, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"status": False, "error": str(e)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


def _add_my_info_parser(sub):
    p = sub.add_parser(
        "my-info",
        help="获取当前用户完整信息（UNO/N/E/T），用于代表「我」；收件人为「我」时先调用本命令再参与 search-users/发送",
    )
    p.set_defaults(func=_cmd_my_info)


def _cmd_folder_list(args):
    """获取当前用户的文件夹列表。"""
    from zmail_folder import ZmailFolder
    client = ZmailFolder()
    result = client.get_folder_list()
    print(json.dumps(result, ensure_ascii=False))


def _add_folder_list_parser(sub):
    p = sub.add_parser("folder-list", help="获取当前用户的文件夹列表")
    p.set_defaults(func=_cmd_folder_list)


def _cmd_delete_mails(args):
    """删除邮件（移入已删除/回收站）：使用 delete-mails，不使用 move-emails。"""
    email_ids_raw = (getattr(args, "email_id", "") or "").strip()
    ids = [x.strip() for x in email_ids_raw.split(",") if x and x.strip()]
    if not ids:
        print(json.dumps({"status": False, "error": "必须提供至少一个有效的邮件 ID"}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    for eid in ids:
        valid, reason = validate_mail_id(eid)
        if not valid:
            print(json.dumps({"status": False, "error": f"邮件 ID「{eid}」：{reason}"}, ensure_ascii=False), file=sys.stderr)
            sys.exit(1)
    from zmail_folder import ZmailFolder
    client = ZmailFolder()
    result = client.delete_emails(getattr(args, "email_id", "") or "")
    print(json.dumps(result, ensure_ascii=False))


def _add_delete_mails_parser(sub):
    p = sub.add_parser(
        "delete-mails",
        help="删除邮件（移入已删除/回收站）：调用 delete-mails，勿用 move-emails 代替",
    )
    p.add_argument("--email-id", required=True, dest="email_id", help="邮件 ID，多个用逗号分隔")
    p.set_defaults(func=_cmd_delete_mails)


def _cmd_move_emails(args):
    """将邮件移动到指定箱体/文件夹。"""
    email_ids_raw = (getattr(args, "email_id", "") or "").strip()
    ids = [x.strip() for x in email_ids_raw.split(",") if x and x.strip()]
    if not ids:
        print(json.dumps({"status": False, "error": "必须提供至少一个有效的邮件 ID"}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    for eid in ids:
        valid, reason = validate_mail_id(eid)
        if not valid:
            print(json.dumps({"status": False, "error": f"邮件 ID「{eid}」：{reason}"}, ensure_ascii=False), file=sys.stderr)
            sys.exit(1)
    from zmail_folder import ZmailFolder
    client = ZmailFolder()
    result = client.move_emails(
        email_ids=getattr(args, "email_id", "") or "",
        mailbox_type=getattr(args, "mailbox_type", "") or "",
        folder=getattr(args, "folder", "") or "",
    )
    print(json.dumps(result, ensure_ascii=False))


def _add_move_emails_parser(sub):
    p = sub.add_parser(
        "move-emails",
        help="将邮件移动到收件箱、垃圾邮件或自定义文件夹（不含移入已删除；删除请用 delete-mails）",
    )
    p.add_argument("--email-id", required=True, dest="email_id", help="邮件 ID，多个用逗号分隔")
    p.add_argument(
        "--mailbox-type",
        required=True,
        dest="mailbox_type",
        choices=["inbox", "spam", "folder"],
        help="目标：inbox=收件箱，spam=垃圾邮件；folder=自定义文件夹且必须同时传 --folder（名称或纯数字 ID）",
    )
    p.add_argument("--folder", default="", help="自定义文件夹名称或纯数字 ID；仅 --mailbox-type folder 时必填")
    p.set_defaults(func=_cmd_move_emails)

def _cmd_recover_mails(args):
    """从“已删除(回收站)”恢复邮件。"""
    email_ids_raw = (getattr(args, "email_id", "") or "").strip()
    ids = [x.strip() for x in email_ids_raw.split(",") if x and x.strip()]
    if not ids:
        print(json.dumps({"status": False, "error": "必须提供至少一个有效的邮件 ID"}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    for eid in ids:
        valid, reason = validate_mail_id(eid)
        if not valid:
            print(json.dumps({"status": False, "error": f"邮件 ID「{eid}」：{reason}"}, ensure_ascii=False), file=sys.stderr)
            sys.exit(1)
    from zmail_folder import ZmailFolder
    client = ZmailFolder()
    result = client.recover_mails(getattr(args, "email_id", "") or "")
    print(json.dumps(result, ensure_ascii=False))


def _add_recover_mails_parser(sub):
    p = sub.add_parser("recover-mails", help="恢复已删除箱体中的邮件")
    p.add_argument("--email-id", required=True, dest="email_id", help="邮件 ID，多个用逗号分隔")
    p.set_defaults(func=_cmd_recover_mails)


def _cmd_search_users(args):
    """收件人、抄送人、密送人、群组均通过 search-users 解析后再参与发送。"""
    from zmail_search_user import ZmailSearchUser
    to_keys = [k.strip() for k in (args.to or "").split(",") if k.strip()]
    cc_keys = [k.strip() for k in (args.cc or "").split(",") if k.strip()]
    bcc_keys = [k.strip() for k in (args.bcc or "").split(",") if k.strip()]
    if not to_keys and not cc_keys and not bcc_keys:
        print(json.dumps({"status": False, "error": "收件人、抄送人、密送人不能同时为空，请至少提供 --to、--cc 或 --bcc 之一。"}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    searcher = ZmailSearchUser()
    result = searcher.search_users(to_keys, cc_keys, bcc_keys, contact_type=args.type)
    print(json.dumps(result, ensure_ascii=False))

def _add_search_users_parser(sub):
    p = sub.add_parser("search-users", help="搜索收件人/抄送人/密送人/群组，解析后再参与发送")
    p.add_argument("--type", choices=["user", "group"], default="user")
    p.add_argument("--to", default="", help="收件人关键字，多个用逗号分隔")
    p.add_argument("--cc", default="", help="抄送人关键字，多个用逗号分隔")
    p.add_argument("--bcc", default="", help="密送人关键字，多个用逗号分隔")
    p.set_defaults(func=_cmd_search_users)

def _register_subparsers(sub):
    """集中注册所有子命令，避免 main 过长且便于维护顺序。"""

    # search
    _add_search_emails_parser(sub)

    # view
    _add_view_parser(sub)

    # download-attach（下载邮件附件）
    _add_download_attach_parser(sub)

    # transfer-attach（附件转存到云盘）
    _add_transfer_attach_parser(sub)

    # mark-email（标记邮件：已读/未读/标星/取消标星；--email-ids 优先，未传时按搜索条件先搜再标）
    _add_mark_email_parser(sub)

    # reply
    _add_reply_parser(sub)

    # reply-brief
    _add_reply_brief_parser(sub)

    # forward（转发邮件，收件人 --to/--cc/--bcc）
    _add_forward_parser(sub)

    # check-forward（检查邮件是否支持转发）
    _add_check_forward_parser(sub)

    # summary（参数与 search-emails 一致）
    _add_summary_parser(sub)

    # 草稿相关（draft-list, save-draft）
    _add_draft_list_parser(sub)
    _add_save_draft_parser(sub)

    # 定时邮件（create-draft, operate-draft）
    _add_create_draft_parser(sub)
    _add_operate_draft_parser(sub)

    # rollback-sent（撤回已发送邮件）
    _add_rollback_sent_parser(sub)

    # send（收件人/抄送人/群组均来自 search-users）
    _add_send_parser(sub)

    # folder-list（文件夹列表）
    _add_folder_list_parser(sub)

    # delete-mails（删除邮件 → 已删除/回收站，DeleteMails）
    _add_delete_mails_parser(sub)

    # move-emails（移动邮件到指定箱体/文件夹）
    _add_move_emails_parser(sub)

    # recover-mails（恢复已删除/回收站中的邮件）
    _add_recover_mails_parser(sub)

    # search-users（收件人、抄送人、密送人、群组均走此子命令解析）
    _add_search_users_parser(sub)

    # get-time（当前系统时间，供相对时间解析）
    _add_get_time_parser(sub)

    # my-info（当前用户完整信息，用于代表「我」）
    _add_my_info_parser(sub)


def main():
    parser = argparse.ArgumentParser(description="zMail 技能统一入口")
    sub = parser.add_subparsers(dest="command", help="子命令")
    _register_subparsers(sub)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    custom_props = _build_used_custom_props(args, sub, sys.argv)
    start_time = time.time()
    exec_ok = True
    exec_fail_reason = None  # 执行失败时写入埋点，便于统计与排障
    try:
        args.func(args)
    except Exception as e:
        exec_ok = False
        exec_fail_reason = str(e)
        print(json.dumps({"error": exec_fail_reason}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    finally:
        custom_props["duration_ms"] = int((time.time() - start_time) * 1000)
        custom_props["status"] = 1 if exec_ok else 0
        if not exec_ok and exec_fail_reason:
            custom_props["failure_reason"] = exec_fail_reason
        send_page_load_event(tag=args.command, custom_properties=custom_props)


if __name__ == "__main__":
    main()
