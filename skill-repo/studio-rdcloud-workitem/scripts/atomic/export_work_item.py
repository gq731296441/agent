#!/usr/bin/env python
"""
工作项导出：按条件查询工作项后导出为 Excel。
遵循总分总：参数解析 → 参数调整 → 调用 API → 结果处理 → 结果格式化 → 追加心智提示。
"""

import sys
import json
import os
import argparse
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_ROOT = SCRIPT_DIR.parent
SKILL_DIR = SCRIPTS_ROOT.parent
CONFIG_FILE = SKILL_DIR / "assets" / "config.json"

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common.log_helper import get_logger, safe_print, apply_log_config, log_raw_argv, log_complete_params, setup_logging, ENABLE_LOG, format_parsed_summary
from common.config_helper import load_base_config
from common.auth_helper import get_auth_headers
from common.rdcloud_api import export_work_items_stream
from common.workspace import apply_workspace_clause_to_query_condition, resolve_workspace_with_source
from common.sql_query_parser import QUERY_LIMIT_MAX, build_query_data_from_select_and_conditions
from common.field_resolver import fetch_fields, resolve_extra_header_names
from common.mindset_messages import (
    build_query_context_mindset_extras,
    get_mindset_message,
)
from query_work_item import add_default_logical_operator, convert_work_item_type_values, _cell_value

# 容器内 skill 为只读挂载（/mnt/skills/<id>），日志改写到可写工作区，并按 skill 分目录
LOG_DIR = (Path("/workspace/.skill-logs") / SKILL_DIR.name) if Path("/workspace").is_dir() else (SKILL_DIR / "logs")
LOG_FILE = LOG_DIR / f"export_work_item_{datetime.now().strftime('%Y%m%d')}.log"
logger = get_logger(__name__)

# 导出每页条数：未指定 limit 时默认 10000
EXPORT_PAGE_SIZE_DEFAULT = 10000

# 无条件时限制创建时间范围（近 2 年）
EXPORT_EMPTY_CONDITION_DAYS = 730

# 用户描述含以下词时自动使用 flap=true（层级导出）
FLAP_TRUE_KEYWORDS = ("关联导出", "层级导出", "带关联导出")


def _resolve_flap(flap_arg: str, user_input: str) -> str:
    """根据 --flap 与 user_input 解析 flap：用户说关联导出/层级导出/带关联导出时则 true。"""
    if (user_input or "").strip():
        for kw in FLAP_TRUE_KEYWORDS:
            if kw in (user_input or ""):
                return "true"
    return "true" if str(flap_arg or "").strip().lower() == "true" else "false"


def _is_cross_workspace_export(workspace, query_condition) -> bool:
    """判定是否跨工作区导出：工作区解析为多值，或条件中工作区 in 多值。"""
    if isinstance(workspace, (list, tuple)):
        ws_list = [str(w).strip() for w in workspace if str(w).strip()]
        if len(ws_list) > 1:
            return True
    if not isinstance(query_condition, dict):
        return False
    clauses = query_condition.get("sourceClauses") or []
    if not isinstance(clauses, list):
        return False
    for clause in clauses:
        if not isinstance(clause, dict):
            continue
        field = (clause.get("field") or "").strip()
        if field != "System_WorkspaceKey":
            continue
        value = clause.get("value")
        if isinstance(value, list):
            vals = [str(v).strip() for v in value if str(v).strip()]
            if len(vals) > 1:
                return True
    return False


def _add_created_date_guard(query_condition):
    """当 queryCondition 无任何条件时，追加创建时间近 2 年的限制，避免全量导出。"""
    if not query_condition or not isinstance(query_condition, dict):
        return query_condition
    clauses = query_condition.get("sourceClauses", [])
    if not isinstance(clauses, list):
        return query_condition
    # 已有任意有效条件则不追加
    has_effective = any(isinstance(c, dict) and (c.get("field") or c.get("value") is not None) for c in clauses)
    if has_effective:
        return query_condition
    # 无任何条件：追加 创建时间 > @Today-730d
    clauses.append({
        "leftGroup": 0, "rightGroup": 0, "logicalOperator": "",
        "field": "System_CreatedDate", "operator": ">", "value": f"@Today-{EXPORT_EMPTY_CONDITION_DAYS}d"
    })
    query_condition["sourceClauses"] = clauses
    logger.info("导出无查询条件，已追加创建时间近 %d 天限制", EXPORT_EMPTY_CONDITION_DAYS)
    return query_condition


def parse_args():
    """从命令行解析查询列、条件、排序、条数等。"""
    p = argparse.ArgumentParser(
        description="工作项导出：按查询列与条件查询后导出为 Excel。",
        epilog="""
示例:
  %(prog)s --select-items "标题,状态" --conditions "状态=进行中"
  %(prog)s -c "工作项类型=任务 AND (状态=进行中 OR 状态=待处理)" --order-by "更新时间 desc" -n 100

完整示例:
  %(prog)s --select-items "标识,标题,状态,工作项类型" --conditions "工作项类型=用户故事 AND 工作区=业务运营 AND 状态=进行中" --order-by "更新时间 desc" --limit 200
""" % {"prog": "export_work_item.py"},
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--select-items", "--selectItems", dest="select_items", default="", metavar="ITEMS",
                   help="查询列，英文逗号分隔。示例: 标题,状态 或 标识,标题,状态,更新时间")
    p.add_argument("--conditions", "-c", dest="conditions", default="", metavar="CONDITIONS",
                   help="条件组合，支持 AND/OR、括号()。示例: 状态=进行中 或 工作项类型=任务 AND (状态=进行中 OR 状态=待处理)")
    p.add_argument("--order-by", "--orderBy", dest="order_by", default="", metavar="ORDER",
                   help="排序。示例: 更新时间 desc 或 标题 asc, 状态 desc")
    p.add_argument("--limit", "-n", dest="limit", default=None, metavar="N", type=int,
                   help="导出条数上限，正整数，最大 10000；超过则按 10000 处理。示例: 100")
    p.add_argument("--flap", dest="flap", default="false", choices=["false", "true"],
                   help="false=列表导出，true=层级导出，默认 false")
    p.add_argument("--user-input", "--userInput", dest="user_input", default="", metavar="TEXT",
                   help="用户的原始输入，用于工作区/类型解析的上下文")
    return p.parse_args()




def adjust_params(args, base_config, headers):
    """解析工作区、构建查询体并设置导出分页。返回 (workspace, query_data, max_export, work_item_type_original_names)。"""
    raw_user_input = (getattr(args, "user_input", None) or "").strip() or None
    ws_override = None  # 工作区由配置或最近访问解析，不通过命令行传入
    ws_from_config = base_config.get("workspace") or ""

    workspace, workspace_source, err = resolve_workspace_with_source(
        ws_override or (ws_from_config if ws_from_config else None),
        ws_from_config,
        headers,
        raw_user_input=raw_user_input,
    )
    if err:
        raise ValueError(err)
    workspace_single = workspace[0] if isinstance(workspace, list) else workspace
    if workspace_single == "SYSMySpace":
        raise ValueError("导出请使用通用工作区，请确保 config 中 api.workspace 为具体工作区。")
    select_items_str = (args.select_items or "").strip() or None
    conditions_str = (args.conditions or "").strip() or None
    order_by_str = (args.order_by or "").strip() or None
    query_data = build_query_data_from_select_and_conditions(
        select_items_str, conditions_str, workspace_single, headers, for_my_workspace=False,
        order_by_str=order_by_str, limit=None, raw_user_input=raw_user_input
    )
    query_data.pop("_multi_match_fields", None)
    try:
        apply_workspace_clause_to_query_condition(
            query_data.setdefault("queryCondition", {}),
            workspace,
            workspace_source,
        )
    except Exception as e:
        logger.warning("自动合并/补充工作区条件时发生异常（忽略）: %s", e)
    query_data["queryCondition"] = add_default_logical_operator(query_data["queryCondition"])
    _qc, _wit_orig = convert_work_item_type_values(
        query_data["queryCondition"], workspace_single, headers
    )
    query_data["queryCondition"] = _qc
    query_data["queryCondition"] = _add_created_date_guard(query_data["queryCondition"])
    query_data["pageNo"] = 1
    lim = getattr(args, "limit", None)
    raw_ps = lim if lim is not None else EXPORT_PAGE_SIZE_DEFAULT
    query_data["pageSize"] = min(max(1, int(raw_ps)), QUERY_LIMIT_MAX)
    max_export = min(max(1, int(lim)), QUERY_LIMIT_MAX) if lim is not None else None
    query_data["workspaceKey"] = workspace_single
    query_data["crossWorkspace"] = _is_cross_workspace_export(workspace, query_data.get("queryCondition"))
    log_complete_params(
        logger,
        "参数调整完成",
        {
            "workspace": workspace_single,
            "select_items": args.select_items,
            "conditions": args.conditions,
            "order_by": args.order_by,
            "limit": getattr(args, "limit", None),
            "effective_limit": max_export,
            "cross_workspace": query_data.get("crossWorkspace"),
        },
    )
    return workspace_single, query_data, max_export, _wit_orig


def fetch_all_items(workspace, query_data, headers, max_items=None, flap="false"):
    """调用导出接口（流式），解析响应为 items 列表。"""
    #logger.info("开始调用导出接口: workspace=%s", workspace)
    resp = export_work_items_stream(workspace, query_data, headers, flap=flap)
    items = _parse_export_response(resp, query_data)
    #logger.info("导出接口返回 %d 条", len(items))
    if max_items is not None and len(items) > max_items:
        items = items[:max_items]
    return items


def _parse_export_response(resp, query_data):
    """解析导出接口响应：支持 JSON（returnData=true）或 Excel 流；区分业务错误消息（如 errMsg、status=checkError）。"""
    content_type = (resp.headers.get("Content-Type") or "").lower()
    logger.info("导出响应 Content-Type=%s", content_type)
    if "application/json" in content_type:
        try:
            data = resp.json()
            _raise_on_business_error(data, action="导出查询")
            # 业务消息：checkError=校验失败；async=已启动离线导出，结果将通过邮件通知
            err_msg = data.get("errMsg") or data.get("error") or data.get("message")
            status = data.get("status")
            if err_msg and status in ("checkError", "async"):
                logger.info("导出返回业务消息: status=%s, errMsg=%s", status, err_msg)
                raise ValueError(str(err_msg))
            items = _extract_items(data)
            #logger.info("导出 JSON 解析完成: 共 %d 条", len(items))
            return items
        except ValueError:
            raise
        except Exception as e:
            logger.warning("导出响应 JSON 解析失败: %s", e)
            return []
    # Excel 或二进制流
    try:
        items = _parse_excel_stream(resp.content, query_data)
        #logger.info("导出 Excel 流解析完成: 共 %d 条", len(items))
        return items
    except Exception as e:
        logger.warning("导出响应 Excel 解析失败: %s", e)
        return []


def _parse_excel_stream(content: bytes, query_data) -> list:
    """从 Excel 字节流解析为 items 列表。"""
    try:
        import io
        from openpyxl import load_workbook
    except ImportError:
        logger.warning("openpyxl 未安装，无法解析 Excel 流。请执行: pip install openpyxl")
        return []
    if not content or len(content) < 100:
        return []
    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    if not ws:
        return []
    rows = list(ws.iter_rows(values_only=True))
    if len(rows) < 2:
        return []
    headers = [str(c or "").strip() for c in rows[0]]
    items = []
    for row in rows[1:]:
        item = {}
        for i, h in enumerate(headers):
            if h and i < len(row):
                item[h] = row[i]
        if item:
            items.append(item)
    wb.close()
    return items


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


def _extract_items(response):
    """从查询响应中取出 items 列表。"""
    if isinstance(response.get("data"), dict):
        return response["data"].get("items") or []
    bo = response.get("bo") or {}
    res = bo.get("result")
    if isinstance(res, dict):
        return res.get("items") or []
    return []


# 导出默认列（与查询一致）
_DEFAULT_EXPORT_KEYS = ["System_Id", "System_Title", "System_State", "System_WorkItemType", "System_ChangedDate"]
# 无 fields 时，默认列的回退中文名
_DEFAULT_KEY_TO_NAME = {
    "System_Id": "标识",
    "System_Title": "标题",
    "System_State": "状态",
    "System_WorkItemType": "工作项类型",
    "System_ChangedDate": "更新时间",
}


def _resolve_export_keys_and_headers(items, query_data, workspace=None, headers=None):
    """
    解析导出列 key 和表头中文名。返回 (keys, header_names, fields)。
    keys: 用于 item[k] 取值的 key 列表（必须与 items 内实际 key 一致）
    header_names: 表头展示名（中文）
    fields: 字段列表，供 _cell_value 时间格式化

    优先使用 items[0].keys()，确保与导出接口返回的数据结构一致（JSON 为 System_*，Excel 流可能为中文列名）。
    """
    keys = []
    if items:
        keys = list(items[0].keys()) if items else []
    if not keys and query_data and isinstance(query_data.get("selectItems"), list):
        keys = [s.get("key") for s in query_data["selectItems"] if isinstance(s, dict) and s.get("key")]
    if not keys:
        keys = _DEFAULT_EXPORT_KEYS.copy()
    fields = None
    ws = workspace[0] if isinstance(workspace, list) else workspace
    if ws and headers:
        try:
            fields = fetch_fields(ws, headers)
        except Exception as e:
            logger.warning("导出 fetch_fields 失败，表头回退 key: %s", e)
    header_names = resolve_extra_header_names(
        keys, fields=fields, workspace=workspace, headers=headers
    )
    # 无 fields 时，对默认列使用回退中文名
    if not fields and header_names == keys:
        header_names = [_DEFAULT_KEY_TO_NAME.get(k, k) for k in keys]
    return keys, header_names, fields


def format_as_csv(items, query_data, workspace=None, headers=None):
    """将结果格式化为 CSV 文本。先查询字段，表头 key 转为中文展示。"""
    keys, header_names, fields = _resolve_export_keys_and_headers(
        items, query_data, workspace, headers
    )
    line = ",".join(header_names) + "\n"
    for item in items:
        cells = []
        for k in keys:
            v = _cell_value(item, k, fields)
            if "," in v or '"' in v or "\n" in v:
                v = '"' + v.replace('"', '""') + '"'
            cells.append(v)
        line += ",".join(cells) + "\n"
    return line


def format_as_excel(items, query_data, output_path=None, workspace=None, headers=None):
    """将结果格式化为 Excel 文件。先查询字段，表头 key 转为中文展示；优先使用 items 实际含有的 key。"""
    try:
        from openpyxl import Workbook
    except ImportError:
        raise ValueError("Excel 格式需要 openpyxl，请执行: pip install openpyxl")
    keys, header_names, fields = _resolve_export_keys_and_headers(
        items, query_data, workspace, headers
    )
    wb = Workbook()
    ws = wb.active
    ws.append(header_names)
    for item in items:
        row = [_cell_value(item, k, fields) for k in keys]
        ws.append(row)
    if output_path is None:
        # 容器内 skill 为只读挂载（/mnt/skills/<id>），导出产物落到可写工作区并按 skill 分目录
        export_dir = (
            (Path("/workspace/exports") / SKILL_DIR.name)
            if Path("/workspace").is_dir()
            else (SKILL_DIR / "logs")
        )
        export_dir.mkdir(parents=True, exist_ok=True)
        output_path = export_dir / f"export_work_item_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    wb.save(output_path)
    return str(output_path)


def format_as_markdown(items, query_data, workspace=None, headers=None):
    """将结果格式化为 Markdown 表格。先查询字段，表头 key 转为中文展示。"""
    if not items:
        return "# 工作项导出\n\n**0 条**\n"
    keys, header_names, fields = _resolve_export_keys_and_headers(
        items, query_data, workspace, headers
    )
    lines = ["# 工作项导出\n", f"**共 {len(items)} 条**\n\n", "| " + " | ".join(header_names) + " |\n", "|" + "|".join(["------"] * len(keys)) + "|\n"]
    for item in items:
        cells = [_cell_value(item, k, fields) for k in keys]
        lines.append("| " + " | ".join(cells) + " |\n")
    return "".join(lines)


def append_hints(has_data, total):
    """在用户得不到预期结果时追加提示。"""
    if has_data and total > 0:
        return ""
    return (
        "\n---\n**导出结果为空**。可尝试：① 放宽查询条件或去掉部分 where 条件；"
        "② 确认工作区与工作项类型是否正确；③ 确认时间范围或状态条件是否过严。"
    )


def run():
    """总方法：参数解析 → 参数调整 → 调用 API → 结果处理 → 结果格式化 → 追加心智提示。"""
    args = parse_args()
    log_complete_params(logger, "参数解析完成", vars(args))
    base_config = load_base_config(CONFIG_FILE)
    headers = get_auth_headers(None, base_config, logger=logger)
    workspace, query_data, max_export, _wit_orig = adjust_params(
        args, base_config, headers
    )
    # 导出工作项接口有最大条目限制，最大50W, 并且超过3000走异步通知
    flap = _resolve_flap(getattr(args, "flap", "false"), getattr(args, "user_input", "") or "")
    items = fetch_all_items(workspace, query_data, headers, max_items=max_export, flap=flap)
    out_path = format_as_excel(items, query_data, workspace=workspace, headers=headers)
    text = f"# 工作项导出\n\n**共 {len(items)} 条**，已保存至: {out_path}\n"
    text += append_hints(bool(items), len(items))
    mindset_text = get_mindset_message("general_query_result", total=len(items))
    extra_mindset = build_query_context_mindset_extras(
        query_data=query_data,
        raw_user_input=(getattr(args, "user_input", None) or "").strip() or None,
        conditions_str=(args.conditions or "").strip() or None,
        work_item_type_original_names=_wit_orig,
    )
    if extra_mindset:
        mindset_text = (
            (mindset_text + "\n\n" + extra_mindset)
            if mindset_text
            else extra_mindset
        )
    parsed_summary = format_parsed_summary(
        {
            "workspace": workspace,
            "select_items": args.select_items,
            "conditions": args.conditions,
            "order_by": args.order_by,
            "limit": args.limit,
            "max_export_count": max_export,
            "flap": flap,
        },
        title="本次语义解析结果",
    )
    body_with_mindset = (text + "\n\n" + mindset_text) if mindset_text else text
    out_for_display = (parsed_summary + "\n" + body_with_mindset) if parsed_summary else body_with_mindset
    logger.info("导出完成: 共 %d 条", len(items))
    safe_print(out_for_display)


def main():
    """入口：统一日志配置、打印原始完整入参，再执行 run。"""
    apply_log_config(CONFIG_FILE)
    if ENABLE_LOG:
        setup_logging(LOG_FILE)
    # 打印原始入参    
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
