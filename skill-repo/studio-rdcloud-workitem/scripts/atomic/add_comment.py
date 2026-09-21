#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
添加工作项评论：顶级评论或回复已有评论；commentDetail 支持 HTML。
技能根目录：studio-rdcloud-workitem。
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_ROOT = SCRIPT_DIR.parent
SKILL_DIR = SCRIPTS_ROOT.parent
CONFIG_FILE = SKILL_DIR / "assets" / "config.json"
# 容器内 skill 为只读挂载（/mnt/skills/<id>），日志改写到可写工作区，并按 skill 分目录
LOG_DIR = (Path("/workspace/.skill-logs") / SKILL_DIR.name) if Path("/workspace").is_dir() else (SKILL_DIR / "logs")
LOG_FILE = LOG_DIR / f"add_comment_{datetime.now().strftime('%Y%m%d')}.log"

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from common.auth_helper import get_auth_headers
from common.config_helper import load_base_config
from common.log_helper import get_logger, setup_logging
from common.rdcloud_api import URL_WORK_ITEM_COMMENTS

logger = get_logger(__name__)


def _mask_sensitive_header(header_name, header_value):
    if not header_value:
        return ""
    if header_name.lower() in ("x-auth-value", "authorization"):
        if len(header_value) > 6:
            return f"{header_value[:3]}***{header_value[-3:]}"
        return "***"
    return header_value


def format_result_as_markdown(result):
    if not result or not isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False, indent=2)
    bo_data = result.get("bo", {})
    if not isinstance(bo_data, dict):
        bo_data = result
    comment_id = bo_data.get("id", "")
    create_by = bo_data.get("createBy", "")
    create_date = bo_data.get("createDate", "")
    comment_detail = bo_data.get("commentDetail", "")
    business_id = bo_data.get("businessId", "")
    parent_id = bo_data.get("parentId", "")
    level = bo_data.get("level", 1)
    text_content = re.sub(r"<[^>]+>", "", comment_detail or "")
    text_content = (
        text_content.replace("&nbsp;", " ")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
    )
    lines = [
        "# 评论添加成功\n\n",
        "| 字段 | 值 |\n",
        "|------|------|\n",
        f"| 评论ID | {comment_id} |\n",
        f"| 创建人 | {create_by} |\n",
        f"| 创建时间 | {create_date} |\n",
        f"| 工作项ID | {business_id} |\n",
    ]
    if parent_id:
        lines.append(f"| 父评论ID | {parent_id} |\n")
    lines.extend(
        [
            f"| 评论层级 | {level} |\n",
            f"| 评论内容 | {text_content} |\n",
        ]
    )
    return "".join(lines)


def add_comment(
    business_type,
    business_id,
    comment_detail,
    title,
    parent_id,
    email_disabled,
    headers,
):
    url = URL_WORK_ITEM_COMMENTS.format(business_type=business_type, business_id=business_id)
    payload = {"commentDetail": comment_detail}
    if title:
        payload["title"] = title
    if parent_id:
        payload["parentId"] = parent_id
    if email_disabled:
        payload["emailDisabled"] = True
    logger.info("添加评论 POST %s", url)
    for key, value in headers.items():
        logger.info("  %s: %s", key, _mask_sensitive_header(key, value))
    logger.info("body keys=%s", list(payload.keys()))
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    result = response.json()
    code_obj = result.get("code", {})
    if isinstance(code_obj, dict) and code_obj.get("code") != "0000":
        msg = code_obj.get("msg", "添加失败")
        raise RuntimeError(f"添加评论失败: {msg}")
    return result


def parse_args():
    epilog = (
        "示例:\n"
        "  # 顶级评论\n"
        '  python scripts/atomic/add_comment.py --work-item-id PLM-3875805 '
        '--comment-detail "<p>内容</p>" --user-input "给 PLM-3875805 加评论"\n'
        "  # 回复（推荐：按序号，无需复制 id）\n"
        "  python scripts/atomic/add_comment.py --work-item-id PLM-3875805 "
        '--comment-detail "<p>回复</p>" --reply-to 2 --user-input "回复第 2 条"\n'
        "  # 回复（按父评论 id）\n"
        "  python scripts/atomic/add_comment.py --work-item-id PLM-3875805 "
        '--comment-detail "<p>回复</p>" --parent-id 87e8e2b151564a9aa0894ce95e37e714\n'
    )
    p = argparse.ArgumentParser(
        description="为工作项添加评论或回复已有评论。",
        epilog=epilog,
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument(
        "--work-item-id",
        "--business-id",
        dest="work_item_id",
        required=True,
        metavar="ID",
        help="工作项标识",
    )
    p.add_argument(
        "--business-type",
        dest="business_type",
        default="wic",
        help="业务类型，默认 wic",
    )
    p.add_argument(
        "--comment-detail",
        dest="comment_detail",
        required=True,
        metavar="HTML",
        help="评论内容，支持 HTML（注意 shell 转义）",
    )
    p.add_argument("--title", default=None, help="可选标题，最长 100")
    p.add_argument(
        "--parent-id",
        dest="parent_id",
        default=None,
        metavar="ID",
        help="父评论 id（API 返回的 UUID）；与 --reply-to 二选一",
    )
    p.add_argument(
        "--reply-to",
        dest="reply_to",
        default=None,
        metavar="N",
        help="按序号指定要回复的评论（如 1、2、1.1）；脚本会先拉取评论列表解析序号→id，再回复",
    )
    p.add_argument(
        "--email-disabled",
        dest="email_disabled",
        action="store_true",
        help="不传邮件（emailDisabled=true）",
    )
    p.add_argument(
        "--raw-json",
        action="store_true",
        help="输出原始 JSON；不设则按 assets/config.json 的 format.as_markdown",
    )
    p.add_argument(
        "--user-input",
        "--userInput",
        dest="user_input",
        default="",
        metavar="TEXT",
        help="用户原始完整输入",
    )
    return p.parse_args()


def resolve_parent_id_from_reply_to(work_item_id, business_type, reply_to, headers):
    """
    用 --reply-to 序号拉取评论列表，解析出 parent_id。
    reply_to: 如 "1", "2", "1.1"（与查询结果中的 [1]、[2]、[1.1] 对应）。
    """
    from atomic.query_comment import path_to_id_map_from_result, query_comments

    result = query_comments(business_type, work_item_id.strip(), 1, 1000, "asc", headers)
    path_map = path_to_id_map_from_result(result)
    key = str(reply_to).strip().replace(" ", "")
    if not key:
        return None
    if key in path_map:
        return path_map[key]
    raise ValueError(
        f"序号「{reply_to}」在评论列表中不存在。请先执行查询评论确认序号（如 [1]、[2]、[1.1]），再回复。"
    )


def main():
    setup_logging(LOG_FILE, add_stderr=True)
    args = parse_args()
    base_config = load_base_config(CONFIG_FILE)
    headers = get_auth_headers({}, base_config, logger=logger)
    as_md = (not args.raw_json) and bool(base_config.get("format_as_markdown", True))

    parent_id = (args.parent_id or "").strip() or None
    if args.reply_to is not None and (args.reply_to or "").strip():
        parent_id = resolve_parent_id_from_reply_to(
            args.work_item_id,
            args.business_type,
            args.reply_to,
            headers,
        )
        logger.info("add_comment --reply-to=%s 解析为 parent_id=%s", args.reply_to, parent_id[:16] + "..." if parent_id and len(parent_id) > 16 else parent_id)

    logger.info(
        "add_comment business_id=%s parent_id=%s user_input=%s",
        args.work_item_id,
        parent_id,
        (args.user_input or "")[:200],
    )
    result = add_comment(
        args.business_type,
        args.work_item_id.strip(),
        args.comment_detail,
        args.title,
        parent_id,
        args.email_disabled,
        headers,
    )
    if as_md:
        print(format_result_as_markdown(result))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
    except requests.RequestException as e:
        print(f"错误: API 请求失败: {e}", file=sys.stderr)
        sys.exit(1)
