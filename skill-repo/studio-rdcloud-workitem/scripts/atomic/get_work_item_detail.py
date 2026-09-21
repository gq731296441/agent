#!/usr/bin/env python
"""
工作项详情：按工作项标识查询详情并格式化输出；支持多个标识，以英文逗号分隔。
遵循总分总：参数解析 → 参数调整 → 调用 API → 结果处理 → 结果格式化 → 追加心智提示。
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_ROOT = SCRIPT_DIR.parent
SKILL_DIR = SCRIPTS_ROOT.parent
CONFIG_FILE = SKILL_DIR / "assets" / "config.json"

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from common.log_helper import get_logger, safe_print, apply_log_config, log_raw_argv, log_complete_params, setup_logging, format_parsed_summary
from common.config_helper import load_base_config
from common.auth_helper import get_auth_headers
from common.rdcloud_api import query_work_item_details, studio_workitem_transmit_url
from common.workspace.workspace_resolver import resolve_workspace
from common.mindset_messages import get_mindset_message

# 容器内 skill 为只读挂载（/mnt/skills/<id>），日志改写到可写工作区，并按 skill 分目录
LOG_DIR = (Path("/workspace/.skill-logs") / SKILL_DIR.name) if Path("/workspace").is_dir() else (SKILL_DIR / "logs")
LOG_FILE = LOG_DIR / f"get_work_item_detail_{datetime.now().strftime('%Y%m%d')}.log"
logger = get_logger(__name__)

# 置于详情正文之前，随 stdout 输出，提醒后续环节完整转发脚本结果。
DETAIL_DISPLAY_NOTICE = (
    "> **【展示要求】** 须**完整显示**以下工作项详情（含全部字段与 Markdown 结构）；"
    "**禁止**总结、省略、合并、截断、改写或仅保留要点；**禁止**去掉链接。"
)


def _build_arg_parser():
    """构建命令行参数解析器（含完整示例）。"""
    example_text = (
        "完整示例:\n"
        "  # 查询单个工作项详情\n"
        "  python scripts/atomic/get_work_item_detail.py "
        "--work-item-id \"PLM-3292609\" --user-input \"查询PLM-3292609详情\"\n\n"
        "  # 查询多个工作项详情（英文逗号分隔）\n"
        "  python scripts/atomic/get_work_item_detail.py "
        "--work-item-id \"PLM-3292609,PLM-3292610\" --user-input \"查询这两个工作项详情\""
    )
    p = argparse.ArgumentParser(
        description="查询工作项详情：按标识返回完整信息，支持多个标识。",
        epilog=example_text,
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument("--work-item-id", "--workItemId", dest="work_item_id", required=True, metavar="ID", help="工作项标识，多个以英文逗号分隔，如 PLM-12345,PLM-12346")
    p.add_argument("--user-input", "--userInput", dest="user_input", default="", metavar="TEXT", help="用户的原始输入，必须是用户真实完整的一句话。")
    return p


def parse_args():
    """从命令行解析工作项标识等参数。"""
    return _build_arg_parser().parse_args()


def parse_work_item_ids(work_item_id_str):
    """将逗号分隔的工作项标识字符串解析为列表（去空白）。"""
    if not work_item_id_str or not isinstance(work_item_id_str, str):
        return []
    return [x.strip() for x in work_item_id_str.strip().split(",") if x.strip()]




def extract_workspace_from_id(work_item_id):
    """从工作项标识中解析工作区（最后一个 '-' 前为工作区）。"""
    if not work_item_id:
        return ""
    i = (work_item_id or "").rfind("-")
    return work_item_id[:i] if i > 0 else work_item_id


def adjust_params(args, base_config, headers):
    """
    参数调整：工作区仅从第一个 work_item_id 解析，校验必填。
    返回 (workspace, work_item_ids)。
    """
    work_item_ids = parse_work_item_ids(args.work_item_id)
    if not work_item_ids:
        raise ValueError("工作项标识不能为空")
    workspace_from_id = extract_workspace_from_id(work_item_ids[0])
    workspace_input = workspace_from_id or None
    raw_user_input = (getattr(args, "user_input", None) or "").strip() or None
    workspace, err = resolve_workspace(workspace_input, "", headers, raw_user_input=raw_user_input)
    if err:
        raise ValueError(err)
    log_complete_params(logger, "参数调整完成", {"workspace": workspace, "work_item_ids": work_item_ids})
    return workspace, work_item_ids


def fetch_detail(work_item_ids, headers):
    """调用详情接口获取工作项（POST body 为 ID 列表，返回 bo.items）。"""
    raw = query_work_item_details(work_item_ids, headers)
    _raise_on_business_error(raw, action="工作项详情查询")
    return raw


def _raise_on_business_error(response, action="查询工作项"):
    """HTTP 成功但业务码失败时抛错（code.code != 0000）。"""
    if not isinstance(response, dict):
        return
    code_obj = response.get("code") or {}
    if not isinstance(code_obj, dict):
        return
    code_val = str(code_obj.get("code") or "").strip()
    if not code_val or code_val == "0000":
        return
    msg = str(code_obj.get("msg") or code_obj.get("msgId") or "未知错误").strip()
    raise ValueError(f"{action}失败：{msg}（接口 code={code_val}）")


def _fields_to_flat_item(fields):
    """
    将详情接口返回的 fields 列表转为 key -> value 的扁平字典，并附带字段展示元数据。
    返回 (flat, field_meta)，其中 field_meta[key] = {"name": str, "label": str}。
    """
    if not fields or not isinstance(fields, list):
        return {}, {}
    flat = {}
    field_meta = {}
    for f in fields:
        if not isinstance(f, dict):
            continue
        key = f.get("key")
        if not key:
            continue
        val = f.get("value")
        if val is None or (isinstance(val, str) and val == ""):
            val = f.get("persistentValue")
        flat[key] = val
        name = f.get("name")
        label = f.get("label")
        field_meta[key] = {
            "name": "" if name is None else str(name),
            "label": "" if label is None else str(label),
        }
    return flat, field_meta


def _raw_item_to_flat(raw_item):
    """将单条 bo.items 元素转为扁平字段字典；无效则返回 None。"""
    if not isinstance(raw_item, dict):
        return None
    flat, field_meta = _fields_to_flat_item(raw_item.get("fields"))
    if not flat:
        return None
    flat["_raw_id"] = raw_item.get("id")
    flat["_field_meta"] = field_meta
    return flat


def extract_all_items(response):
    """从详情接口响应 bo.items 中取出全部记录，并转为扁平字段字典列表。"""
    bo = response.get("bo") or {}
    items = bo.get("items")
    if not items or not isinstance(items, list):
        return []
    result = []
    for raw_item in items:
        flat = _raw_item_to_flat(raw_item)
        if flat:
            result.append(flat)
    return result


def extract_first_item(response):
    """从详情接口响应中取第一条并转为扁平字典（兼容单条场景与测试）。"""
    items = extract_all_items(response)
    return items[0] if items else None


def _markdown_field_line_label(field_key, item):
    """
    生成详情行里「字段标题」两段式信息：
    - 前段（粗体）：字段名称 name 在前；若 label 存在且与 name 不同，则紧跟「（显示名称：label）」。
    - 是否在括号中追加字段 key：当存在可用的 name/label 元数据时为 True，此时输出「（字段key：field_key）」；
      无元数据时主标题即为 key，不再重复括号内的 key。
    返回 (主标题文本, 是否追加字段key括号)。
    """
    meta_map = item.get("_field_meta") or {}
    m = meta_map.get(field_key) if isinstance(meta_map, dict) else None
    if not isinstance(m, dict):
        return field_key, False
    name = (m.get("name") or "").strip()
    label = (m.get("label") or "").strip()
    if not name and not label:
        return field_key, False
    if name and label and name != label:
        main = f"{name}（显示名称：{label}）"
    elif name:
        main = name
    else:
        main = label
    return main, True


def format_field_value(val):
    """将字段值格式化为可读字符串。"""
    if val is None or val == "":
        return ""
    if isinstance(val, dict):
        return (
            val.get("fullNo")
            or val.get("nameDisplayLong")
            or val.get("name")
            or val.get("nameZh")
            or val.get("value")
            or str(val)
        )
    if isinstance(val, list):
        return ", ".join(format_field_value(x) for x in val)
    return str(val)


def _work_item_id_for_link(item, fallback_id):
    """从 item 中解析用于链接的工作项标识（字符串）。"""
    raw = item.get("_raw_id")
    if raw:
        return str(raw)
    sid = item.get("System_Id")
    if isinstance(sid, dict) and sid.get("fullNo"):
        return str(sid["fullNo"])
    if sid is not None and str(sid).strip():
        return str(sid)
    return fallback_id or ""


def format_detail_as_markdown(item, work_item_id):
    """将单条工作项格式化为 Markdown 详情（键值对）。"""
    if not item:
        return ""
    lines = ["# 工作项详情\n"]
    wid = _work_item_id_for_link(item, work_item_id)
    title = item.get("System_Title") or ""
    if isinstance(title, dict):
        title = title.get("name") or title.get("nameZh") or str(title)
    url = studio_workitem_transmit_url(wid)
    lines.append(f"- **标识**：[{wid}]({url})")
    lines.append(f"- **标题**：{title}\n")
    lines.append("## 字段\n")
    for k, v in sorted(item.items()):
        if k.startswith("_") or v is None:
            continue
        disp = format_field_value(v)
        if disp == "":
            continue
        main, show_key_suffix = _markdown_field_line_label(k, item)
        if show_key_suffix:
            label = f"**{main}**（字段key：{k}）"
        else:
            label = f"**{main}**"
        lines.append(f"- {label}：{disp}\n")
    return "".join(lines)


def append_hints(has_data, work_item_ids):
    """在用户找不到预期结果时追加提示。"""
    if has_data:
        return ""
    return (
        "\n---\n**未查到该工作项**。可检查：① 标识是否完整正确（如 PLM-12345）；"
        "② 当前账号是否有该工作区的查看权限；③ 是否需要在其他工作区中查询。"
    )


def run():
    """
    总方法：列出执行流程，依次为参数解析、参数调整、调用 API、结果处理、结果格式化、追加心智提示。
    """
    args = parse_args()
    log_complete_params(logger, "参数解析完成", vars(args))
    base_config = load_base_config(CONFIG_FILE)
    headers = get_auth_headers(None, base_config, logger=logger)
    workspace, work_item_ids = adjust_params(args, base_config, headers)
    response = fetch_detail(work_item_ids, headers)
    items = extract_all_items(response)
    parts = []
    for item in items:
        wid = item.get("_raw_id") or ""
        parts.append(format_detail_as_markdown(item, wid))
    text = "\n\n---\n\n".join(p for p in parts if p)
    if not text:
        ids_display = ", ".join(work_item_ids[:5]) + (" 等" if len(work_item_ids) > 5 else "")
        text = f"# 工作项详情\n\n未找到标识为 **{ids_display}** 的工作项。"
    text += append_hints(bool(items), work_item_ids)
    mindset_text = get_mindset_message(
        "detail_result",
        work_item_ids=work_item_ids,
        count_found=len(items),
    )
    parsed_summary = format_parsed_summary(
        {
            "work_item_ids": work_item_ids,
            "workspace": workspace,
        },
        title="本次语义解析结果",
    )
    body_detail = DETAIL_DISPLAY_NOTICE + "\n\n" + text
    body_with_mindset = (body_detail + "\n\n" + mindset_text) if mindset_text else body_detail
    out_for_display = (parsed_summary + "\n" + body_with_mindset) if parsed_summary else body_with_mindset
    #logger.info(f"out_for_display: {out_for_display}")
    safe_print(out_for_display)


def main():
    """入口：统一日志配置、打印原始完整入参，再执行 run。"""
    apply_log_config(CONFIG_FILE)
    setup_logging(LOG_FILE, add_stderr=True)
    log_raw_argv(logger, sys.argv)
    try:
        run()
    except ValueError as e:
        err_out = f"错误: {e}"
        mindset_text = get_mindset_message("query_error")
        if mindset_text and mindset_text not in err_out:
            err_out = err_out + "\n\n" + mindset_text
        safe_print(err_out, file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        logger.exception("执行失败")
        err_out = f"错误: {e}"
        mindset_text = get_mindset_message("query_error")
        if mindset_text and mindset_text not in err_out:
            err_out = err_out + "\n\n" + mindset_text
        safe_print(err_out, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
