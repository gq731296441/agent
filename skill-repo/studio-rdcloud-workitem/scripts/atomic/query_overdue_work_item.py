#!/usr/bin/env python
"""查询我超期的工作项（指派给我且计划完成日期已过的单据）。

支持指定工作项类型（默认：任务），按类型自动选用超期日期字段：
  - 任务 / 用户故事 / 产品需求 → PlanFinishDate
  - 变更请求 → ExpectedResolvedDate
  - 缺陷 → PlanResolvedDate
"""

import sys
import json
import argparse
import re
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta, date

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_ROOT = SCRIPT_DIR.parent
SKILL_DIR = SCRIPTS_ROOT.parent
CONFIG_FILE = SKILL_DIR / "assets" / "config.json"
OVERDUE_CONFIG_FILE = SCRIPT_DIR / "query_overdue_work_item_config.json"
# 容器内 skill 为只读挂载（/mnt/skills/<id>），日志改写到可写工作区，并按 skill 分目录
LOG_DIR = (Path("/workspace/.skill-logs") / SKILL_DIR.name) if Path("/workspace").is_dir() else (SKILL_DIR / "logs")
LOG_FILE = LOG_DIR / f"query_overdue_work_item_{datetime.now().strftime('%Y%m%d')}.log"

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from common.sql_query_parser import QUERY_LIMIT_MAX

from common.log_helper import (
    ENABLE_LOG, get_logger, safe_print, sanitize_for_log,
    setup_logging, truncate_for_log, apply_log_config, log_raw_argv, log_complete_params,
)
from common.auth_helper import get_auth_headers, value_error_missing_emp_no_from_env
from common.config_helper import load_base_config
from common.rdcloud_api import URL_QUERY_WORK_ITEMS, uportal_wim_workitem_url

if ENABLE_LOG:
    setup_logging(LOG_FILE)
logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# 配置（从 JSON 加载，缺失时用默认）
# ---------------------------------------------------------------------------

_DEFAULT_OVERDUE_CFG = {
    "work_item_type_mapping": {
        "变更活动": "chgActivity", "变更申请": "ChangeApply", "变更记录": "ChgRecord",
        "变更请求": "chgRequest", "版本构建": "EcVersionBuild", "测试申请": "ApplyForTest",
        "二级签名": "EcSecondarySign", "市场需求": "MR", "市场需求包": "MR_Packet",
        "产品需求": "PR", "用户故事": "US", "任务": "Task", "缺陷": "Bug",
        "故障": "Fault", "外场故障": "FieldDefect", "测试计划": "TestPlan",
        "测试记录": "TestRecord", "测试用例": "TestCase", "计划": "Plan",
    },
    "overdue_date_field_map": {
        "任务": "PlanFinishDate", "用户故事": "PlanFinishDate", "产品需求": "PlanFinishDate",
        "变更请求": "ExpectedResolvedDate", "缺陷": "PlanResolvedDate",
    },
    "default_overdue_date_field": "PlanFinishDate",
    "all_overdue_date_fields": ["PlanFinishDate", "ExpectedResolvedDate", "PlanResolvedDate"],
}


def _load_overdue_config() -> dict:
    """从 query_overdue_work_item_config.json 加载配置，缺失或异常时返回默认配置。"""
    if OVERDUE_CONFIG_FILE.exists():
        try:
            with open(OVERDUE_CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            cfg = dict(_DEFAULT_OVERDUE_CFG)
            if isinstance(loaded.get("work_item_type_mapping"), dict):
                cfg["work_item_type_mapping"] = {**cfg["work_item_type_mapping"], **loaded["work_item_type_mapping"]}
            if isinstance(loaded.get("overdue_date_field_map"), dict):
                cfg["overdue_date_field_map"] = {**cfg["overdue_date_field_map"], **loaded["overdue_date_field_map"]}
            if loaded.get("default_overdue_date_field"):
                cfg["default_overdue_date_field"] = str(loaded["default_overdue_date_field"])
            if isinstance(loaded.get("all_overdue_date_fields"), list):
                cfg["all_overdue_date_fields"] = [str(x) for x in loaded["all_overdue_date_fields"] if x]
            return cfg
        except Exception as e:
            logger.warning("加载超期查询配置失败，使用默认: %s", e)
    return dict(_DEFAULT_OVERDUE_CFG)


_OVERDUE_CFG: dict | None = None


def _get_overdue_cfg() -> dict:
    """懒加载并返回超期查询配置。"""
    global _OVERDUE_CFG
    if _OVERDUE_CFG is None:
        _OVERDUE_CFG = _load_overdue_config()
    return _OVERDUE_CFG


# ---------------------------------------------------------------------------
# 类型 / 日期辅助
# ---------------------------------------------------------------------------

def resolve_type_key(name_or_key: str) -> str:
    """中文名 → API key；若已是 key 则原样返回。"""
    cfg = _get_overdue_cfg()
    mapping = cfg["work_item_type_mapping"]
    s = (name_or_key or "").strip()
    if not s:
        return s
    if s in mapping.values():
        return s
    mapped = mapping.get(s)
    return mapped if mapped else s


def get_overdue_date_fields(type_names: list) -> list:
    """返回给定类型对应的超期日期字段去重列表（顺序与 overdue_date_field_map 一致）。"""
    cfg = _get_overdue_cfg()
    field_map = cfg["overdue_date_field_map"]
    default_field = cfg["default_overdue_date_field"]
    seen = set()
    result = []
    for t in (type_names or []):
        f = field_map.get(t.strip(), default_field)
        if f not in seen:
            seen.add(f)
            result.append(f)
    return result or [default_field]


def _to_local_date(dt):
    if dt is None:
        return None
    if dt.tzinfo is not None and dt.tzinfo.utcoffset(dt) == timedelta(0):
        local_tz = timezone(timedelta(hours=8))
        dt = dt.replace(tzinfo=timezone.utc).astimezone(local_tz)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=8)))
    return dt.date() if hasattr(dt, "date") else dt


def parse_date_value(val):
    """将 API 返回的日期值解析为 date（东八区）。"""
    if val is None:
        return None
    if isinstance(val, dict):
        val = val.get("value") or val.get("displayValue") or val.get("$date")
        if val is None:
            return None
        if isinstance(val, dict):
            val = val.get("$numberLong") or val.get("$numberInt")
            if val is not None:
                val = int(val)
    if isinstance(val, (int, float)):
        try:
            ts = val / 1000.0 if val > 1e12 else val
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return _to_local_date(dt)
        except Exception:
            return None
    if isinstance(val, str):
        try:
            if "ISODate(" in val:
                m = re.search(r'ISODate\s*\(\s*["\']([^"\']+)["\']\s*\)', val)
                if m:
                    val = m.group(1)
            val = val.replace("Z", "+00:00") if "Z" in val and "+00:00" not in val else val
            dt = datetime.fromisoformat(val)
            return _to_local_date(dt)
        except Exception:
            return None
    return None


def get_overdue_days(item: dict) -> int | None:
    """从工作项中提取超期天数；按类型选对应日期字段。"""
    cfg = _get_overdue_cfg()
    field_map = cfg["overdue_date_field_map"]
    all_fields = cfg["all_overdue_date_fields"]
    default_field = cfg["default_overdue_date_field"]
    wt = item.get("System_WorkItemType")
    type_zh = ""
    if isinstance(wt, dict):
        type_zh = wt.get("nameZh") or wt.get("name") or ""
    elif isinstance(wt, str):
        type_zh = wt
    primary = field_map.get(type_zh, default_field)
    fields = [primary] + [f for f in all_fields if f != primary]
    for key in fields:
        d = parse_date_value(item.get(key))
        if d is not None:
            delta = date.today() - d
            if delta.days >= 0:
                return delta.days
            return None
    return None


# ---------------------------------------------------------------------------
# 查询条件构建
# ---------------------------------------------------------------------------

def _build_overdue_source_clauses(overdue_date_fields: list) -> list:
    """构建超期日期 sourceClauses（多字段时用 OR + 括号）。"""
    if not overdue_date_fields:
        overdue_date_fields = [_get_overdue_cfg()["default_overdue_date_field"]]
    n = len(overdue_date_fields)
    clauses = []
    for i, f in enumerate(overdue_date_fields):
        if n == 1:
            left_group = right_group = 0
        elif i == 0:
            left_group, right_group = 1, 0
        elif i == n - 1:
            left_group, right_group = 0, 1
        else:
            left_group = right_group = 0
        clauses.append({
            "leftGroup": left_group,
            "rightGroup": right_group,
            "logicalOperator": "" if i == 0 else "OR",
            "field": f,
            "operator": "<",
            "value": "@Today",
        })
    return clauses


def add_time_range_condition(query_condition: dict) -> dict:
    clauses = query_condition.get("sourceClauses", [])
    if any(isinstance(c, dict) and c.get("field") == "System_ChangedDate" and c.get("value") == "@Today-365d" for c in clauses):
        return query_condition
    if clauses and clauses[-1].get("logicalOperator") == "":
        clauses[-1]["logicalOperator"] = "AND"
    clauses.append({
        "leftGroup": 0, "rightGroup": 0, "logicalOperator": "AND",
        "field": "System_ChangedDate", "operator": ">", "value": "@Today-365d",
    })
    return query_condition


def add_exclude_completed_condition(query_condition: dict) -> dict:
    clauses = query_condition.get("sourceClauses", [])
    if any(isinstance(c, dict) and "System_State" in (c.get("field") or "") for c in clauses):
        return query_condition
    if any(
        isinstance(c, dict)
        and c.get("field") == "System_State.stateGroup.groupNameZh"
        and c.get("operator") == "not in"
        and isinstance(c.get("value"), list)
        and "已取消" in c["value"] and "处理完成" in c["value"]
        for c in clauses
    ):
        return query_condition
    if clauses and clauses[-1].get("logicalOperator") == "":
        clauses[-1]["logicalOperator"] = "AND"
    clauses.append({
        "leftGroup": 0, "rightGroup": 0, "logicalOperator": "AND",
        "field": "System_State.stateGroup.groupNameZh",
        "operator": "not in",
        "value": ["已取消", "处理完成"],
    })
    return query_condition


def add_default_logical_operator(query_condition: dict) -> dict:
    clauses = query_condition.get("sourceClauses", [])
    for i, c in enumerate(clauses):
        if isinstance(c, dict) and (c.get("logicalOperator") is None or c.get("logicalOperator") == "") and i > 0:
            c["logicalOperator"] = "AND"
    return query_condition


def get_my_workspace_conditions(emp_no: str) -> list:
    if not emp_no:
        raise ValueError("缺少用户工号（emp_no），无法构建 myWorkspaceConditions")
    return [
        f"(System_AppointedTo IN ('{emp_no}')\n\tOR System_AssignedTo IN ('{emp_no}'))\nAND assignedToMe('{emp_no}')"
    ]


# ---------------------------------------------------------------------------
# Markdown 格式化
# ---------------------------------------------------------------------------

def _extract_workspace_key(work_item_id: str) -> str:
    if not work_item_id:
        return ""
    i = work_item_id.rfind("-")
    return work_item_id[:i] if i != -1 else work_item_id


def _format_changed_date(val) -> str:
    if not val:
        return "-"
    try:
        if isinstance(val, (int, float)):
            ts = val / 1000 if val > 1e12 else val
            dt = datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8)))
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(val, str):
            s = val.replace("Z", "+00:00") if val.endswith("Z") else val
            dt = datetime.fromisoformat(s)
            if dt.tzinfo:
                dt = dt.astimezone(timezone(timedelta(hours=8)))
            else:
                dt = dt.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=8)))
            return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    return str(val)


def _format_date_field(val) -> str:
    d = parse_date_value(val)
    return d.strftime("%Y-%m-%d") if d else "-"


def format_result_as_markdown(result: dict, type_names: list) -> str:
    """将查询结果格式化为 Markdown，默认 7 列：标题、标识、类型、状态、计划完成日期、更新时间、超期天数。"""
    if not result or not isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False, indent=2)

    items = []
    total = None
    if "data" in result and isinstance(result["data"], dict):
        items = result["data"].get("items", [])
        total = result["data"].get("total")
    elif "bo" in result and isinstance(result["bo"], dict):
        bo_res = result["bo"].get("result") or {}
        if isinstance(bo_res, dict):
            items = bo_res.get("items", [])
            pd = bo_res.get("pageDetail") or {}
            total = pd.get("totalRow") if isinstance(pd, dict) else bo_res.get("totalRow")

    logger.info("格式化超期查询结果，共 %d 条", len(items))

    type_desc = "、".join(type_names) if type_names else "任务"
    md_lines = [f"# 我超期的{type_desc}\n\n"]
    if total is not None:
        md_lines.append(f"**总计：{total} 条**\n\n")
    elif items:
        md_lines.append(f"**当前页：{len(items)} 条**\n\n")

    if not items:
        md_lines.append("**未找到超期工作项。** 太棒了！\n")
        return "".join(md_lines)

    overdue_fields = get_overdue_date_fields(type_names)
    primary_date_label = "计划完成日期"
    if len(overdue_fields) == 1 and overdue_fields[0] == "ExpectedResolvedDate":
        primary_date_label = "期望解决日期"
    elif len(overdue_fields) == 1 and overdue_fields[0] == "PlanResolvedDate":
        primary_date_label = "计划解决日期"

    headers = ["标题", "标识", "工作项类型", "状态", primary_date_label, "更新时间", "超期天数"]
    md_lines.append("| " + " | ".join(headers) + " |\n")
    md_lines.append("| " + " | ".join(["---"] * len(headers)) + " |\n")

    for item in items:
        if not isinstance(item, dict):
            continue
        title = item.get("System_Title", "")
        work_item_id = item.get("System_Id", "")
        wt_obj = item.get("System_WorkItemType")
        work_item_type = ""
        if isinstance(wt_obj, dict):
            work_item_type = wt_obj.get("nameZh") or wt_obj.get("name") or ""
        elif isinstance(wt_obj, str):
            work_item_type = wt_obj
        state_obj = item.get("System_State")
        state_str = ""
        if isinstance(state_obj, dict):
            state_str = state_obj.get("nameZh") or state_obj.get("name") or state_obj.get("displayValue") or ""
        elif isinstance(state_obj, str):
            state_str = state_obj

        if work_item_id:
            ws_key = _extract_workspace_key(work_item_id)
            link = uportal_wim_workitem_url(ws_key, work_item_id)
            title_cell = f"[{title}]({link})" if title else ""
            id_cell = f"[{work_item_id}]({link})"
        else:
            title_cell = title
            id_cell = work_item_id

        # 选取该工作项对应的超期日期字段值
        cfg = _get_overdue_cfg()
        type_key_zh = work_item_type
        primary_field = cfg["overdue_date_field_map"].get(
            type_key_zh, overdue_fields[0] if overdue_fields else cfg["default_overdue_date_field"]
        )
        date_str = _format_date_field(item.get(primary_field))

        changed_date = _format_changed_date(item.get("System_ChangedDate"))
        overdue_days = get_overdue_days(item)
        days_str = str(overdue_days) if overdue_days is not None else "-"

        row = [title_cell, id_cell, work_item_type, state_str, date_str, changed_date, days_str]
        md_lines.append("| " + " | ".join(c if c else "-" for c in row) + " |\n")

    return "".join(md_lines)


# ---------------------------------------------------------------------------
# API 调用
# ---------------------------------------------------------------------------

def query_work_items(query_data: dict, headers: dict) -> dict:
    url = URL_QUERY_WORK_ITEMS.format(workspace="SYSMySpace")
    req_summary = json.dumps(sanitize_for_log({"url": url, "body": query_data}), ensure_ascii=False)
    logger.info("query_overdue_work_item 请求: %s", truncate_for_log(req_summary, 1000))
    r = requests.post(url, headers=headers, json=query_data, timeout=30)
    r.raise_for_status()
    out = r.json()
    code_obj = out.get("code") or {}
    logger.info(
        "query_overdue_work_item 响应摘要: HTTP=%s code=%s msg=%s",
        r.status_code, code_obj.get("code"), code_obj.get("msg"),
    )
    code_val = str(code_obj.get("code") or "").strip()
    if code_val and code_val != "0000":
        msg = str(code_obj.get("msg") or code_obj.get("msgId") or "未知错误").strip()
        raise ValueError(f"查询超期工作项失败：{msg}（code={code_val}）")
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="query_overdue_work_item.py",
        description="查询我超期的工作项（指派给我且计划完成日期已过，默认类型：任务）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
                查询我超期的任务（默认）:
                    %(prog)s
                查询我超期的缺陷:
                    %(prog)s --work-item-type 缺陷
                查询超期的任务和用户故事:
                    %(prog)s --work-item-type 任务,用户故事
                限制返回 50 条:
                    %(prog)s --work-item-type 任务 --limit 50
                """,
    )
    p.add_argument(
        "--work-item-type", "--workItemType",
        dest="work_item_type", default="任务",
        metavar="TYPES",
        help="工作项类型，英文逗号分隔，如 任务,缺陷,用户故事；默认：任务。",
    )
    p.add_argument(
        "--limit", "-n",
        dest="limit", default=20, type=int,
        metavar="N",
        help="返回条数上限，默认 20，最大 10000。",
    )
    p.add_argument(
        "--order-by", "--orderBy",
        dest="order_by", default="",
        metavar="ORDER",
        help="排序字段，如 PlanFinishDate asc；不传则按超期日期升序（最早超期排前面）。",
    )
    p.add_argument(
        "--raw-json", "--rawJson",
        dest="raw_json", action="store_true",
        help="输出原始 JSON，不格式化为 Markdown。",
    )
    p.add_argument(
        "--user-input", "--userInput",
        dest="user_input", default="",
        metavar="TEXT",
        help="用户的原始输入，必须是用户真实完整的一句话。",
    )
    return p


def _parse_type_names(work_item_type_str: str) -> list:
    """解析逗号分隔的工作项类型名称列表。"""
    if not work_item_type_str:
        return ["任务"]
    return [t.strip() for t in work_item_type_str.split(",") if t.strip()]


def _build_sort_items(order_by: str, overdue_date_fields: list) -> list:
    """根据 order_by 字符串构建 sortItems，默认按主超期日期字段升序。"""
    if order_by:
        parts = order_by.strip().split()
        field = parts[0]
        is_asc = len(parts) < 2 or parts[1].lower() != "desc"
        return [{"key": field, "isAscending": is_asc}]
    default_field = _get_overdue_cfg()["default_overdue_date_field"]
    primary_field = overdue_date_fields[0] if overdue_date_fields else default_field
    return [{"key": primary_field, "isAscending": True}]


def main():
    apply_log_config(CONFIG_FILE)
    log_raw_argv(logger, sys.argv)
    parser = _build_parser()
    args = parser.parse_args()

    type_names = _parse_type_names(args.work_item_type)
    type_keys = [resolve_type_key(t) for t in type_names if resolve_type_key(t)]
    overdue_date_fields = get_overdue_date_fields(type_names)

    log_complete_params(logger, "参数解析完成", {
        "work_item_type": type_names, "type_keys": type_keys,
        "overdue_date_fields": overdue_date_fields, "limit": args.limit,
        "order_by": args.order_by, "raw_json": args.raw_json,
    })

    try:
        base_config = load_base_config(CONFIG_FILE)
        headers = get_auth_headers(None, base_config, logger=logger)
        emp_no = headers.get("X-Emp-No", "")
        if not emp_no:
            raise ValueError(value_error_missing_emp_no_from_env())

        my_workspace_conditions = get_my_workspace_conditions(emp_no)

        # 构建超期日期条件
        overdue_clauses = _build_overdue_source_clauses(overdue_date_fields)
        query_condition = {
            "type": "flat",
            "sourceClauses": overdue_clauses,
        }

        # 自动补充时间范围 + 排除已完成
        query_condition = add_time_range_condition(query_condition)
        query_condition = add_exclude_completed_condition(query_condition)
        query_condition = add_default_logical_operator(query_condition)

        # 必选字段 + 所有超期日期字段
        select_items = [
            {"key": "System_Id"},
            {"key": "System_Title"},
            {"key": "System_WorkItemType"},
            {"key": "System_State"},
            {"key": "System_ChangedDate"},
        ] + [{"key": f} for f in _get_overdue_cfg()["all_overdue_date_fields"]]

        sort_items = _build_sort_items(args.order_by, overdue_date_fields)

        page_size = min(max(1, int(args.limit)), QUERY_LIMIT_MAX)
        query_data = {
            "crossWorkspace": True,
            "myWorkspaceConditions": my_workspace_conditions,
            "queryCondition": query_condition,
            "selectItems": select_items,
            "sortItems": sort_items,
            "pageNo": 1,
            "pageSize": page_size,
            "onlyResult": True,
            "querySource": "skill",
        }
        if type_keys:
            query_data["workItemTypeKeys"] = type_keys

        result = query_work_items(query_data, headers)

        if args.raw_json or not base_config.get("format_as_markdown", True):
            out = json.dumps(result, ensure_ascii=False, indent=2)
        else:
            out = format_result_as_markdown(result, type_names)

        safe_print(out)

    except ValueError as e:
        logger.warning("执行终止: %s", e)
        safe_print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        logger.exception("执行失败")
        safe_print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
