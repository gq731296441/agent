#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
查询工作项评论：调用 RDC 评论 API，返回树形结构；可选 Markdown 输出。
技能根目录：studio-rdcloud-workitem（config 在 assets/config.json）。
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
LOG_FILE = LOG_DIR / f"query_comment_{datetime.now().strftime('%Y%m%d')}.log"

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


def format_comment_as_markdown(comment, level=1, indent=0, path=""):
    """path 为序号，如 1、1.1、1.2，用于回复时「回复第 N 条」。"""
    if not isinstance(comment, dict):
        return ""
    indent_str = "  " * indent
    create_by = comment.get("createBy", "")
    create_date = comment.get("createDate", "")
    comment_detail = comment.get("commentDetail", "")
    children = comment.get("children", [])
    text_content = re.sub(r"<[^>]+>", "", comment_detail or "")
    text_content = (
        text_content.replace("&nbsp;", " ")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
    )
    path_tag = f"[{path}] " if path else ""
    lines = [
        f"{indent_str}- {path_tag}**{create_by}** ({create_date})\n",
        f"{indent_str}  {text_content}\n",
    ]
    if children and isinstance(children, list):
        for i, child in enumerate(children):
            child_path = f"{path}.{i + 1}" if path else str(i + 1)
            lines.append(format_comment_as_markdown(child, level + 1, indent + 1, child_path))
    return "".join(lines)


def path_to_id_map_from_result(result):
    """
    从查询结果构建 序号→评论 id 的映射，供 add_comment 的 --reply-to 解析用。
    返回 dict，如 {"1": "xxx", "1.1": "yyy", "2": "zzz"}。
    """
    bo = result.get("bo") if isinstance(result.get("bo"), dict) else {}
    rows = bo.get("rows") if isinstance(bo.get("rows"), list) else []
    return _path_to_id_map_from_rows(rows)


def _path_to_id_map_from_rows(rows, prefix=""):
    out = {}
    for i, c in enumerate(rows):
        if not isinstance(c, dict):
            continue
        p = str(i + 1) if not prefix else f"{prefix}.{i + 1}"
        cid = (c.get("id") or "").strip()
        if cid:
            out[p] = cid
        kids = c.get("children") or []
        out.update(_path_to_id_map_from_rows(kids, p))
    return out


REPLY_HINT = (
    "\n\n---\n"
    "💡 **回复说明**：回复某条评论时，可使用「回复第 N 条」指定序号（如上 [1]、[2]、[1.1]），"
    "无需复制评论 ID。例如：「回复第 2 条：收到」或「给 PLM-xxx 回复第 1.1 条：同意」。"
)


def format_result_as_markdown(result):
    if not result or not isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False, indent=2)
    bo_data = result.get("bo", {})
    rows = bo_data.get("rows", []) if isinstance(bo_data, dict) else []
    total = bo_data.get("total") if isinstance(bo_data, dict) else None
    if isinstance(bo_data, dict):
        meta = bo_data.get("meta", {})
        if isinstance(meta, dict) and meta.get("commentTotalSize") is not None:
            total = meta.get("commentTotalSize")
    if not rows:
        return "# 评论查询结果\n\n**未找到评论**\n"
    md_lines = ["# 评论查询结果\n\n"]
    if total is not None:
        md_lines.append(f"**总计：{total} 条评论**\n\n")
    elif rows:
        md_lines.append(f"**当前页：{len(rows)} 条顶层评论**\n\n")
    for i, comment in enumerate(rows):
        if isinstance(comment, dict):
            path = str(i + 1)
            md_lines.append(format_comment_as_markdown(comment, level=1, indent=0, path=path))
            md_lines.append("\n")
    md_lines.append(REPLY_HINT)
    return "".join(md_lines)


def query_comments(business_type, business_id, page, size, sort, headers):
    url = URL_WORK_ITEM_COMMENTS.format(business_type=business_type, business_id=business_id)
    params = {"page": page, "size": size}
    if sort:
        params["sort"] = sort
    logger.info("query_comment GET %s params=%s", url, params)
    logger.info(
        "query_comment 请求头摘要: X-Tenant-Id=%s X-Emp-No=%s X-Lang-Id=%s X-Auth-Value=%s",
        headers.get("X-Tenant-Id", ""),
        headers.get("X-Emp-No", ""),
        headers.get("X-Lang-Id", ""),
        _mask_sensitive_header("x-auth-value", headers.get("X-Auth-Value", "")),
    )
    response = requests.get(url, params=params, headers=headers, timeout=30)
    response.raise_for_status()
    result = response.json()
    code_obj = result.get("code", {})
    if not isinstance(code_obj, dict):
        code_obj = {}
    bo = result.get("bo") if isinstance(result.get("bo"), dict) else {}
    rows = bo.get("rows") if isinstance(bo.get("rows"), list) else []
    meta = bo.get("meta") if isinstance(bo.get("meta"), dict) else {}
    total_hint = meta.get("commentTotalSize")
    if total_hint is None:
        total_hint = bo.get("total")
    msg_raw = str(code_obj.get("msg") or "")
    msg_snip = msg_raw[:200] + ("…" if len(msg_raw) > 200 else "")
    logger.info(
        "query_comment 响应摘要: http_status=%s biz_code=%s biz_msg=%s 顶层行数=%s total_or_meta=%s",
        response.status_code,
        code_obj.get("code", ""),
        msg_snip,
        len(rows),
        total_hint,
    )
    if code_obj.get("code") != "0000":
        msg = code_obj.get("msg", "查询失败")
        raise RuntimeError(f"查询评论失败: {msg}")
    return result


def parse_args():
    epilog = (
        "示例:\n"
        "  python scripts/atomic/query_comment.py --work-item-id PLM-4346370 "
        "--user-input \"查看 PLM-4346370 的评论\"\n"
    )
    p = argparse.ArgumentParser(
        description="查询指定工作项的评论（树形结构）。",
        epilog=epilog,
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument(
        "--work-item-id",
        "--business-id",
        dest="work_item_id",
        required=True,
        metavar="ID",
        help="工作项标识，如 PLM-4346370",
    )
    p.add_argument(
        "--business-type",
        dest="business_type",
        default="wic",
        help="业务类型，默认 wic",
    )
    p.add_argument("--page", type=int, default=1, help="页码，默认 1")
    p.add_argument("--size", type=int, default=1000, help="每页条数，默认 1000")
    p.add_argument(
        "--sort",
        default="asc",
        choices=("asc", "desc"),
        help="排序 asc/desc，默认 asc",
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
        help="用户原始完整输入，用于日志与上下文",
    )
    return p.parse_args()


def main():
    setup_logging(LOG_FILE, add_stderr=True)
    args = parse_args()
    base_config = load_base_config(CONFIG_FILE)
    headers = get_auth_headers({}, base_config, logger=logger)
    as_md = (not args.raw_json) and bool(base_config.get("format_as_markdown", True))
    logger.info(
        "query_comment business_id=%s user_input=%s as_markdown=%s",
        args.work_item_id,
        (args.user_input or "")[:200],
        as_md,
    )
    result = query_comments(
        args.business_type,
        args.work_item_id.strip(),
        args.page,
        args.size,
        args.sort,
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
