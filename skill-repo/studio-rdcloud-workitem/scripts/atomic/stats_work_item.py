#!/usr/bin/env python
"""
工作项统计与分组聚合（同一入口文件）。

- **默认（无子命令）**：按条件分页拉取后做 COUNT / SUM / AVG 等客户端统计（原 stats）。
- **aggregate 子命令**：服务端 groupingKeys 分桶聚合，与 query_work_item / 各场景同源拼体。

用法示例：
  python scripts/atomic/stats_work_item.py --stats count -c "工作项类型=缺陷"
  python scripts/atomic/stats_work_item.py aggregate --group-by "状态" -c "工作项类型=缺陷"
"""

import argparse
import copy
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_ROOT = SCRIPT_DIR.parent
SKILL_DIR = SCRIPTS_ROOT.parent
CONFIG_FILE = SKILL_DIR / "assets" / "config.json"

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common.auth_helper import get_auth_headers
from common.config_helper import load_base_config
from common.field_resolver import fetch_fields, resolve_field_key_for_select_list
from common.log_helper import (
    ENABLE_LOG,
    apply_log_config,
    format_parsed_summary,
    get_logger,
    log_complete_params,
    log_raw_argv,
    safe_print,
    sanitize_for_log,
    setup_logging,
)
from common.mindset_messages import (
    build_query_context_mindset_extras,
    get_mindset_message,
)
from common.rdcloud_api import fetch_fields_v2_with_usage_distinct, query_work_items
from common.sql_query_parser import (
    QUERY_LIMIT_MAX,
    build_query_data_from_select_and_conditions,
)
from common.workspace import (
    apply_workspace_clause_to_query_condition,
    resolve_workspace_with_source,
)
from stats_aggregate_scenario_queries import (
    build_link_query_body_for_aggregate,
    build_my_related_query_body_for_aggregate,
    build_overdue_query_body_for_aggregate,
    build_view_query_body_for_aggregate,
)
from query_work_item import add_default_logical_operator, convert_work_item_type_values

# 容器内 skill 为只读挂载（/mnt/skills/<id>），日志改写到可写工作区，并按 skill 分目录
LOG_DIR = (Path("/workspace/.skill-logs") / SKILL_DIR.name) if Path("/workspace").is_dir() else (SKILL_DIR / "logs")
LOG_FILE_STATS = LOG_DIR / f"stats_work_item_{datetime.now().strftime('%Y%m%d')}.log"
LOG_FILE_AGGREGATE = LOG_DIR / f"stats_work_item_aggregate_{datetime.now().strftime('%Y%m%d')}.log"
logger = get_logger(__name__)

STATS_PAGE_SIZE = 1000

# ---------- 分组聚合（aggregate 子命令）常量与工具 ----------

AGGREGATE_DEFAULT_PAGE_SIZE = 200
WIC_TERMS_OTHER_BUCKET_NAME = "sum_of_other_doc_counts"


def log_full_aggregate_query_body(
    query_data: Dict[str, Any],
    stage: str = "分组聚合·请求前",
) -> None:
    """将本次 query_work_items 的请求体完整写入日志（脱敏、不截断）。"""
    safe = sanitize_for_log(query_data or {})
    text = json.dumps(safe, ensure_ascii=False, indent=2)
    logger.info("【%s】完整 query_data（不截断）:\n%s", stage, text)


def _normalize_aggregate_group_by_str(group_by_str: str) -> str:
    """
    规范化 aggregate 的 --group-by 文案。

    单独写「类型」时，字段元数据里常见「工作项类型」「单据类型」等均以「类型」结尾，
    模糊匹配会与 doctype 等冲突且顺序上可能先命中错误字段；分组聚合默认按工作项类型分桶。
    若需按单据类型等其它「*类型」字段分组，请写全名（如「单据类型」）或写字段 key。
    """
    if not (group_by_str or "").strip():
        return ""
    parts = [x.strip() for x in re.split(r"\s*,\s*", group_by_str.strip()) if x.strip()]
    parts = ["工作项类型" if p == "类型" else p for p in parts]
    return ",".join(parts)


def merge_select_str_with_group_by(
    select_items_str: Optional[str],
    group_by_str: str,
) -> Optional[str]:
    gb = (group_by_str or "").strip()
    sel = (select_items_str or "").strip()
    if not gb:
        return sel if sel else None
    sel_parts = [x.strip() for x in re.split(r"\s*,\s*", sel) if x.strip()]
    gb_parts = [x.strip() for x in re.split(r"\s*,\s*", gb) if x.strip()]
    seen = set(sel_parts)
    merged = list(sel_parts)
    for p in gb_parts:
        if p not in seen:
            merged.append(p)
            seen.add(p)
    return ",".join(merged) if merged else None


def _normalize_workspace_key_for_aggregate_api(key: str) -> str:
    k = (key or "").strip()
    if k == "System_Workspace":
        return "System_WorkspaceKey"
    return k


def _normalize_select_items_workspace_keys(query_data: Dict[str, Any]) -> None:
    items = query_data.get("selectItems")
    if not isinstance(items, list):
        return
    for it in items:
        if isinstance(it, dict) and (it.get("key") or "").strip() == "System_Workspace":
            it["key"] = "System_WorkspaceKey"


def merge_aggregate_query_body(
    query_data: Dict[str, Any],
    grouping_keys: List[str],
) -> Dict[str, Any]:
    out = copy.deepcopy(query_data)
    out["groupingKeys"] = [
        _normalize_workspace_key_for_aggregate_api(k)
        for k in grouping_keys
        if (k or "").strip()
    ]
    _ensure_select_items_contain_keys(out, out["groupingKeys"])
    return out


def _ensure_select_items_contain_keys(
    query_data: Dict[str, Any], keys: List[str]
) -> None:
    items = query_data.get("selectItems")
    if not isinstance(items, list):
        items = []
        query_data["selectItems"] = items
    existing = set()
    for it in items:
        if isinstance(it, dict) and it.get("key"):
            existing.add(str(it["key"]).strip())
    for k in keys:
        k = (k or "").strip()
        if not k or k in existing:
            continue
        items.append({"key": k})
        existing.add(k)


def extract_aggregations_from_response(
    response: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if not isinstance(response, dict):
        return None
    bo = response.get("bo")
    if not isinstance(bo, dict):
        return None
    res = bo.get("result")
    if not isinstance(res, dict):
        return None
    items = res.get("items")
    if not isinstance(items, list) or not items:
        return None
    first = items[0]
    if not isinstance(first, dict):
        return None
    agg = first.get("aggregations")
    return agg if isinstance(agg, dict) else None


def extract_total_row(response: Dict[str, Any]) -> Optional[int]:
    tr = None
    if isinstance(response, dict):
        bo = response.get("bo")
        if isinstance(bo, dict):
            res = bo.get("result")
            if isinstance(res, dict):
                tr = res.get("totalRow")
    if tr is None:
        return None
    try:
        return int(tr)
    except (TypeError, ValueError):
        return None


def _bucket_display_name(t: Dict[str, Any]) -> str:
    if not isinstance(t, dict):
        return "（无效）"
    raw_name = t.get("name")
    if raw_name is not None and str(raw_name).strip() == WIC_TERMS_OTHER_BUCKET_NAME:
        return "其余分桶合并"
    for k in ("name", "nameZh", "nameEn", "key", "title", "value", "id"):
        v = t.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return "（未命名）"


def _term_count(t: Dict[str, Any]) -> Optional[Any]:
    if not isinstance(t, dict):
        return None
    if t.get("count") is not None:
        return t.get("count")
    return t.get("doc_count")


def _md_table_cell(s: Any) -> str:
    if s is None:
        return ""
    return str(s).replace("|", "\\|").replace("\n", " ").strip()


def _terms_all_leaf_buckets(terms: List[Any]) -> bool:
    if not terms:
        return False
    for t in terms:
        if not isinstance(t, dict):
            continue
        sub = t.get("terms")
        if isinstance(sub, list) and len(sub) > 0:
            return False
    return True


def _terms_has_other_bucket(terms: List[Any]) -> bool:
    for t in terms or []:
        if not isinstance(t, dict):
            continue
        if (t.get("name") or "").strip() == WIC_TERMS_OTHER_BUCKET_NAME:
            return True
        sub = t.get("terms")
        if isinstance(sub, list) and _terms_has_other_bucket(sub):
            return True
    return False


def _bucket_column_title_for_table(
    grouping_keys: Optional[List[str]],
    grouping_display_names_zh: Optional[List[str]] = None,
) -> str:
    zh = [
        str(x).strip()
        for x in (grouping_display_names_zh or [])
        if x and str(x).strip()
    ]
    if zh:
        return ", ".join(zh)
    keys = [str(k).strip() for k in (grouping_keys or []) if k and str(k).strip()]
    if keys:
        return ", ".join(keys)
    return "分组值"


def _terms_flat_table_markdown(
    terms: List[Any], bucket_column_title: str = "分组值"
) -> str:
    col = _md_table_cell(bucket_column_title)
    lines = [f"| {col} | 数量 |", "|--------|------|"]
    for t in terms or []:
        if not isinstance(t, dict):
            continue
        nm = _bucket_display_name(t)
        cnt = _term_count(t)
        if cnt is None:
            cnt = "-"
        lines.append(f"| {_md_table_cell(nm)} | {_md_table_cell(cnt)} |")
    return "\n".join(lines)


def _flatten_two_level_nested_rows(terms: List[Any]) -> List[Tuple[str, str, Any]]:
    rows: List[Tuple[str, str, Any]] = []
    for t in terms or []:
        if not isinstance(t, dict):
            continue
        sub = t.get("terms")
        if not isinstance(sub, list) or not sub:
            continue
        d1 = _bucket_display_name(t)
        for ch in sub:
            if not isinstance(ch, dict):
                continue
            inner = ch.get("terms")
            if ch.get("type") == "sterms" and isinstance(inner, list):
                for leaf in inner:
                    if not isinstance(leaf, dict):
                        continue
                    if leaf.get("terms"):
                        continue
                    c = _term_count(leaf)
                    if c is None:
                        continue
                    rows.append((d1, _bucket_display_name(leaf), c))
                continue
            if isinstance(inner, list) and inner:
                for leaf in inner:
                    if not isinstance(leaf, dict):
                        continue
                    if leaf.get("terms"):
                        continue
                    c = _term_count(leaf)
                    if c is not None:
                        rows.append((d1, _bucket_display_name(leaf), c))
                continue
            if not (ch.get("terms") or []):
                c = _term_count(ch)
                if c is not None:
                    rows.append((d1, _bucket_display_name(ch), c))
    return rows


def _two_level_flat_table_markdown(
    rows: List[Tuple[str, str, Any]],
    col1_title: str,
    col2_title: str,
) -> str:
    c1 = _md_table_cell(col1_title)
    c2 = _md_table_cell(col2_title)
    lines = [f"| {c1} | {c2} | 数量 |", "|--------|--------|------|"]
    for d1, d2, cnt in rows:
        lines.append(
            f"| {_md_table_cell(d1)} | {_md_table_cell(d2)} | {_md_table_cell(cnt)} |"
        )
    return "\n".join(lines)


def _terms_to_markdown_lines(terms: List[Any], level: int = 0) -> List[str]:
    lines: List[str] = []
    indent = "  " * level
    for t in terms or []:
        if not isinstance(t, dict):
            continue
        nm = _bucket_display_name(t)
        cnt = _term_count(t)
        sub = t.get("terms")
        if not sub:
            lines.append(f"{indent}- {nm}: **{cnt}**")
            continue
        line = f"{indent}- **{nm}**"
        if cnt is not None:
            line += f"（合计 {cnt}）"
        lines.append(line)
        for ch in sub:
            if not isinstance(ch, dict):
                continue
            if ch.get("type") == "sterms":
                lines.extend(_terms_to_markdown_lines(ch.get("terms") or [], level + 1))
            elif ch.get("terms"):
                lines.extend(_terms_to_markdown_lines(ch.get("terms") or [], level + 1))
            elif _term_count(ch) is not None:
                cn = _bucket_display_name(ch)
                lines.append(f"{indent}  - {cn}: **{_term_count(ch)}**")
    return lines


def format_aggregations_markdown(
    aggregations: Dict[str, Any],
    total_row: Optional[int] = None,
    requested_page_size: Optional[int] = None,
    grouping_keys: Optional[List[str]] = None,
    grouping_display_names_zh: Optional[List[str]] = None,
) -> str:
    if not aggregations:
        return "# 分组聚合\n\n未获取到聚合数据。\n"
    name = (aggregations.get("name") or "").strip()
    agg_type = (aggregations.get("type") or "").strip()
    terms = aggregations.get("terms") or []
    parts = ["# 分组聚合\n"]
    if grouping_display_names_zh:
        parts.append(f"**聚合维度**：{', '.join(grouping_display_names_zh)}\n\n")
    elif name:
        parts.append(f"**聚合维度**: `{name}`")
        if agg_type:
            parts.append(f" · `{agg_type}`")
        parts.append("\n\n")
    if total_row is not None:
        parts.append(f"**totalRow**: {total_row}\n\n")
    if requested_page_size is not None:
        parts.append(f"**请求 pageSize**: {requested_page_size}\n\n")
    if not terms:
        parts.append("_无分桶数据_\n")
        return "".join(parts)
    bucket_title = _bucket_column_title_for_table(
        grouping_keys, grouping_display_names_zh=grouping_display_names_zh
    )
    zh_list = grouping_display_names_zh or []
    flat_two_level = _flatten_two_level_nested_rows(terms)
    use_two_dim_table = (
        len(zh_list) == 2
        and not _terms_all_leaf_buckets(terms)
        and len(flat_two_level) > 0
    )
    if use_two_dim_table:
        parts.append(
            _two_level_flat_table_markdown(flat_two_level, zh_list[0], zh_list[1])
        )
    elif _terms_all_leaf_buckets(terms):
        parts.append(
            _terms_flat_table_markdown(terms, bucket_column_title=bucket_title)
        )
    else:
        parts.append("\n".join(_terms_to_markdown_lines(terms, 0)))
    parts.append("\n")
    if _terms_has_other_bucket(terms):
        ps = requested_page_size if requested_page_size is not None else "—"
        parts.append(
            f"\n> **提示**：仍出现「其余分桶合并」（`{WIC_TERMS_OTHER_BUCKET_NAME}`）时，表示在 **pageSize={ps}** 下服务端仍合并了部分桶。"
            f" 请尝试 **增大 `--limit`**（写入请求体 `pageSize`，最大 {QUERY_LIMIT_MAX}）；"
            f"未传 `--limit` 时本脚本默认 **{AGGREGATE_DEFAULT_PAGE_SIZE}**。\n"
        )
    return "".join(parts)


def _field_display_name_for_group_by_key(key, items, items_fallback=None):
    key = (key or "").strip()
    if not key:
        return key
    lookup_key = key[:-5] if key.endswith(".name") else key
    for lst in (items or [], items_fallback or []):
        for it in lst:
            if not isinstance(it, dict):
                continue
            if (it.get("key") or "").strip() == lookup_key:
                nz = (it.get("nameZh") or "").strip()
                n = (it.get("name") or "").strip()
                ne = (it.get("nameEn") or "").strip()
                return nz or n or ne or key
    return key


def resolve_group_by_to_keys(
    group_by_str: str,
    workspace: str,
    headers: Dict[str, Any],
    raw_user_input: Optional[str] = None,
) -> Tuple[List[str], List[Dict[str, Any]], List[str]]:
    raw = (group_by_str or "").strip()
    if not raw:
        raise ValueError("请提供非空的 --group-by")
    raw_norm = _normalize_aggregate_group_by_str(raw)
    select_list = [x.strip() for x in re.split(r"\s*,\s*", raw_norm) if x.strip()]
    select_list = [
        "System_WorkspaceKey"
        if (x or "").strip() == "System_Workspace"
        else (x or "").strip()
        for x in select_list
    ]
    field_items = fetch_fields(workspace, headers)
    field_items_fallback = fetch_fields_v2_with_usage_distinct(workspace, headers)
    original_input_for_match = (raw_user_input or "").strip() or (
        (group_by_str or "").strip() if group_by_str else ""
    )
    multi_match: List[Dict[str, Any]] = []
    keys: List[str] = []
    display_names_zh: List[str] = []
    for n in select_list:
        ks = resolve_field_key_for_select_list(
            n,
            field_items,
            headers,
            raise_on_not_found=False,
            original_input=original_input_for_match,
            workspace=workspace,
            field_items_fallback=field_items_fallback,
            work_item_type_key=None,
        )
        if not ks:
            raise ValueError(f"无法解析分组字段：{n}")
        chosen = _normalize_workspace_key_for_aggregate_api(ks[0])
        if len(ks) > 1:
            key_infos = [
                {
                    "key": k,
                    "field_name": _field_display_name_for_group_by_key(
                        k, field_items, field_items_fallback
                    ),
                }
                for k in ks
            ]
            multi_match.append(
                {
                    "name": n,
                    "keys": list(ks),
                    "key_infos": key_infos,
                    "chosen_key": chosen,
                }
            )
            logger.warning(
                "分组字段「%s」匹配到多个 key，取第一个作为分组维度：%s",
                n,
                ks,
            )
        keys.append(chosen)
        display_names_zh.append(
            _field_display_name_for_group_by_key(
                chosen, field_items, field_items_fallback
            )
        )
    return keys, multi_match, display_names_zh


def format_group_by_multi_match_notice(multi_match: List[Dict[str, Any]]) -> str:
    if not multi_match:
        return ""
    lines = [
        "### 分组字段解析说明",
        "",
        "以下名称在字段元数据中匹配到多个 key；**列表查询**会将这些 key 都加入查询列，**分组聚合**仅取**第一个**作为分组维度。",
        "",
    ]
    for r in multi_match:
        name = (r.get("name") or "").strip()
        chosen = (r.get("chosen_key") or "").strip()
        infos = r.get("key_infos") or []
        parts = []
        for info in infos:
            if not isinstance(info, dict):
                continue
            fn = (info.get("field_name") or info.get("key") or "").strip()
            k = (info.get("key") or "").strip()
            parts.append(f"{fn}（`{k}`）")
        lines.append(
            f"- **「{name}」**：候选 "
            + "、".join(parts)
            + f"；已选用 **`{chosen}`** 分组。"
        )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _validate_scenario_exclusivity(args) -> None:
    qt = getattr(args, "query_type", None)
    od = getattr(args, "overdue", False)
    rt = (getattr(args, "relate_type", None) or "").strip()
    tt = (getattr(args, "tree_type", None) or "").strip()
    qu = (getattr(args, "query_url", None) or "").strip()
    vid = (getattr(args, "view_id", None) or "").strip()
    if qt:
        if od or rt or tt or qu or vid:
            raise ValueError(
                "使用 --query-type（与我相关）时，请勿同时使用 --overdue、"
                "关联/树参数（--relate-type / --tree-type 等）或视图参数（--query-url / --view-id）"
            )
        return
    n = sum(
        [
            1 if od else 0,
            1 if (rt or tt) else 0,
            1 if (qu or vid) else 0,
        ]
    )
    if n > 1:
        raise ValueError(
            "超期（--overdue）、关联/树（--relate-type / --tree-type）、"
            "视图（--query-url / --view-id）三种场景互斥，请只选一种"
        )


def adjust_aggregate_params(args, base_config, headers):
    _validate_scenario_exclusivity(args)

    raw_user_input = (getattr(args, "user_input", None) or "").strip() or None
    workspace, workspace_source, err = resolve_workspace_with_source(
        None, base_config.get("workspace") or "", headers, raw_user_input=raw_user_input
    )
    if err:
        raise ValueError(err)
    workspace_single = workspace[0] if isinstance(workspace, list) else workspace
    select_items_str = (args.select_items or "").strip() or None
    group_by_str_for_select = _normalize_aggregate_group_by_str(
        (args.group_by or "").strip()
    )
    select_items_str = merge_select_str_with_group_by(
        select_items_str, group_by_str_for_select
    )
    conditions_str = (args.conditions or "").strip() or None
    order_by_str = (args.order_by or "").strip() or None
    limit = getattr(args, "limit", None)
    query_type = getattr(args, "query_type", None)

    _wit_orig: List[str] = []
    scenario = "generic"
    query_type_out: Optional[str] = None

    if query_type:
        emp_no = headers.get("X-Emp-No", "")
        if not emp_no:
            raise ValueError("与我相关分组聚合需要认证头中的工号 X-Emp-No")
        if not workspace_single:
            raise ValueError("未解析到工作区，且配置中无 api.workspace。")
        query_data, _wit_orig = build_my_related_query_body_for_aggregate(
            select_items_str,
            conditions_str,
            order_by_str,
            limit,
            query_type,
            workspace,
            workspace_source,
            headers,
            emp_no,
            raw_user_input=raw_user_input,
            logger=logger,
        )
        request_workspace = "SYSMySpace"
        display_workspace = workspace
        scenario = "my_related"
        query_type_out = query_type
    elif getattr(args, "overdue", False):
        query_data, _wit_orig = build_overdue_query_body_for_aggregate(
            (args.work_item_type or "").strip() or "任务",
            order_by_str,
            limit,
            headers,
        )
        request_workspace = "SYSMySpace"
        display_workspace = workspace
        scenario = "overdue"
    elif (getattr(args, "relate_type", None) or "").strip() or (
        getattr(args, "tree_type", None) or ""
    ).strip():
        if workspace_single == "SYSMySpace":
            raise ValueError(
                "关联/树分组聚合请使用具体业务工作区，请配置 api.workspace 或通过语义解析工作区。"
            )
        query_data, _wit_orig = build_link_query_body_for_aggregate(
            select_items_str,
            conditions_str,
            (getattr(args, "relate_conditions", None) or "").strip() or None,
            order_by_str,
            limit,
            (getattr(args, "relate_type", None) or "").strip(),
            (getattr(args, "tree_type", None) or "").strip(),
            (getattr(args, "related_options", None) or "").strip(),
            (getattr(args, "tree_options", None) or "").strip(),
            workspace,
            workspace_source,
            headers,
            raw_user_input=raw_user_input,
            logger=logger,
        )
        request_workspace = workspace_single
        display_workspace = workspace
        scenario = "link"
    elif (getattr(args, "query_url", None) or "").strip() or (
        getattr(args, "view_id", None) or ""
    ).strip():
        fb = (base_config.get("workspace") or workspace_single or "").strip()
        query_data, request_workspace = build_view_query_body_for_aggregate(
            (getattr(args, "query_url", None) or "").strip(),
            (getattr(args, "view_id", None) or "").strip(),
            (getattr(args, "view_workspace", None) or "").strip(),
            fb,
            headers,
        )
        display_workspace = request_workspace
        scenario = "view"
    else:
        if workspace_single == "SYSMySpace":
            raise ValueError(
                "分组聚合请使用具体工作区，请确保 config 中 api.workspace 为具体工作区。"
            )
        query_data = build_query_data_from_select_and_conditions(
            select_items_str,
            conditions_str,
            workspace_single,
            headers,
            for_my_workspace=False,
            order_by_str=order_by_str,
            limit=limit,
            raw_user_input=raw_user_input,
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
        query_data["queryCondition"] = add_default_logical_operator(
            query_data["queryCondition"]
        )
        _qc, _wit_orig = convert_work_item_type_values(
            query_data["queryCondition"], workspace_single, headers
        )
        query_data["queryCondition"] = _qc
        query_data["crossWorkspace"] = True
        request_workspace = workspace_single
        display_workspace = workspace

    _normalize_select_items_workspace_keys(query_data)

    grouping_keys, group_by_multi_match, grouping_display_names_zh = (
        resolve_group_by_to_keys(
            group_by_str_for_select,
            workspace_single,
            headers,
            raw_user_input=raw_user_input,
        )
    )
    query_data = merge_aggregate_query_body(query_data, grouping_keys)

    query_data["onlyResult"] = True
    query_data["pageNo"] = 1
    if limit is None:
        query_data["pageSize"] = min(AGGREGATE_DEFAULT_PAGE_SIZE, QUERY_LIMIT_MAX)
    else:
        query_data["pageSize"] = min(max(1, int(limit)), QUERY_LIMIT_MAX)

    log_payload = {
        "workspace": display_workspace,
        "scenario": scenario,
        "query_type": query_type_out,
        "group_by": args.group_by,
        "grouping_keys": grouping_keys,
        "select_items": args.select_items,
        "conditions": args.conditions,
        "order_by": args.order_by,
        "limit": limit,
    }
    if scenario == "overdue":
        log_payload["work_item_type"] = getattr(args, "work_item_type", None)
    if scenario == "link":
        log_payload["relate_type"] = getattr(args, "relate_type", None)
        log_payload["tree_type"] = getattr(args, "tree_type", None)
    if scenario == "view":
        log_payload["query_url"] = getattr(args, "query_url", None)
        log_payload["view_id"] = getattr(args, "view_id", None)
    log_complete_params(logger, "参数调整完成", log_payload)
    return (
        request_workspace,
        query_data,
        _wit_orig,
        display_workspace,
        scenario,
        query_type_out,
        group_by_multi_match,
        grouping_display_names_zh,
    )


def parse_aggregate_args():
    p = argparse.ArgumentParser(
        description="工作项分组聚合：按与列表查询相同的条件查询，按 --group-by 维度服务端分桶，输出聚合结果。"
    )
    p.add_argument(
        "--group-by",
        "--groupBy",
        dest="group_by",
        required=True,
        metavar="DIMS",
        help="分组维度，英文逗号分隔的字段名称，如：状态、工作区,指派给、工作项类型。"
        " 单独写「类型」时视为「工作项类型」；若需按单据类型等其它字段分组请写全名或字段 key。",
    )
    p.add_argument(
        "--select-items",
        "--selectItems",
        dest="select_items",
        default="",
        metavar="ITEMS",
        help="查询列，英文逗号分隔；不传则使用默认列（会含分组所需字段）。",
    )
    p.add_argument(
        "--conditions",
        "-c",
        dest="conditions",
        default="",
        metavar="CONDITIONS",
        help="条件组合，与 query_work_item 相同；不传则无额外过滤。",
    )
    p.add_argument(
        "--order-by",
        "--orderBy",
        dest="order_by",
        default="",
        metavar="ORDER",
        help="排序；不传则默认按更新时间倒序。",
    )
    p.add_argument(
        "--limit",
        "-n",
        dest="limit",
        default=None,
        type=int,
        metavar="N",
        help="请求体 pageSize；不传时聚合默认 "
        f"{AGGREGATE_DEFAULT_PAGE_SIZE}（过小会导致分桶被截断）。最大 {QUERY_LIMIT_MAX}。",
    )
    p.add_argument(
        "--user-input",
        "--userInput",
        dest="user_input",
        default="",
        metavar="TEXT",
        help="用户原始话术，用于字段名前后匹配。",
    )
    p.add_argument(
        "--query-type",
        "--queryType",
        "-q",
        dest="query_type",
        default=None,
        metavar="TYPE",
        choices=[
            "assigned_to_me",
            "my_todo",
            "created_by_me",
            "edited_by_me",
            "followed_by_me",
        ],
        help="与我相关场景时传入，与 query_my_work_item 相同，请求走 SYSMySpace；与 --overdue、关联/树、视图参数互斥。",
    )
    p.add_argument(
        "--overdue",
        dest="overdue",
        action="store_true",
        help="超期场景（与 query_overdue_work_item 同源），请求走 SYSMySpace；可配合 --work-item-type、--order-by。",
    )
    p.add_argument(
        "--work-item-type",
        "--workItemType",
        dest="work_item_type",
        default="任务",
        metavar="TYPES",
        help="仅超期场景使用：工作项类型，英文逗号分隔；默认：任务。",
    )
    p.add_argument(
        "--relate-type",
        "--relateType",
        dest="relate_type",
        default="",
        metavar="TYPE",
        help="关联查询：与 query_work_item 相同；与 --tree-type 互斥。",
    )
    p.add_argument(
        "--tree-type",
        "--treeType",
        dest="tree_type",
        default="",
        metavar="TYPE",
        help="树查询：与 query_work_item 相同；与 --relate-type 互斥。",
    )
    p.add_argument(
        "--relate-conditions",
        "--relateConditions",
        dest="relate_conditions",
        default="",
        metavar="CONDITIONS",
        help="关联侧条件，与 query_work_item 的 --relate-conditions 相同。",
    )
    p.add_argument(
        "--relatedOptions",
        "--related-options",
        dest="related_options",
        default="",
        metavar="OPTION",
        help="关联选项，与 query_work_item 相同（需配合 --relate-type）。",
    )
    p.add_argument(
        "--treeOptions",
        "--tree-options",
        dest="tree_options",
        default="",
        metavar="OPTION",
        help="树选项，与 query_work_item 相同（需配合 --tree-type）。",
    )
    p.add_argument(
        "--query-url",
        "--queryUrl",
        dest="query_url",
        default="",
        metavar="URL",
        help="研发云视图链接（须能解析出 24 位视图 ID）；与 --view-id 二选一。",
    )
    p.add_argument(
        "--view-id",
        "--viewId",
        dest="view_id",
        default="",
        metavar="ID",
        help="24 位十六进制视图 ID；需同时指定 --view-workspace 或配置 api.workspace。",
    )
    p.add_argument(
        "--view-workspace",
        "--viewWorkspace",
        dest="view_workspace",
        default="",
        metavar="WS",
        help="视图所在工作区（--view-id 或部分 URL 无工作区时使用）。",
    )
    return p.parse_args()


def run_aggregate():
    args = parse_aggregate_args()
    log_complete_params(logger, "参数解析完成", vars(args))
    base_config = load_base_config(CONFIG_FILE)
    headers = get_auth_headers(None, base_config, logger=logger)
    (
        request_workspace,
        query_data,
        _wit_orig,
        display_workspace,
        scenario,
        query_type,
        group_by_multi_match,
        grouping_display_names_zh,
    ) = adjust_aggregate_params(args, base_config, headers)

    logger.info("stats_work_item aggregate 请求 query_data keys: %s", list(query_data.keys()))
    log_full_aggregate_query_body(query_data)
    raw = query_work_items(request_workspace, query_data, headers)
    _raise_on_business_error(raw, action="分组聚合查询")

    agg = extract_aggregations_from_response(raw)
    if not agg:
        raise ValueError(
            "接口未返回分组统计结果。"
            "请确认当前环境在传入分组字段（groupingKeys）时会返回各组数量，或检查查询条件。"
        )
    total_row = extract_total_row(raw)
    text = format_aggregations_markdown(
        agg,
        total_row=total_row,
        requested_page_size=query_data.get("pageSize"),
        grouping_keys=query_data.get("groupingKeys"),
        grouping_display_names_zh=grouping_display_names_zh,
    )

    mindset_text = get_mindset_message("general_query_result", total=1)
    extra_mindset = build_query_context_mindset_extras(
        query_data=query_data,
        raw_user_input=(getattr(args, "user_input", None) or "").strip() or None,
        conditions_str=(args.conditions or "").strip() or None,
        relate_conditions_str=(getattr(args, "relate_conditions", None) or "").strip()
        or None,
        work_item_type_original_names=_wit_orig,
    )
    if extra_mindset:
        mindset_text = (
            (mindset_text + "\n\n" + extra_mindset) if mindset_text else extra_mindset
        )

    summary_fields = {
        "workspace": display_workspace,
        "scenario": scenario,
        "group_by": args.group_by,
        "select_items": args.select_items,
        "conditions": args.conditions,
        "order_by": args.order_by,
        "limit": args.limit,
    }
    if query_type:
        summary_fields["query_type"] = query_type
    if scenario == "overdue":
        summary_fields["work_item_type"] = getattr(args, "work_item_type", None)
    if scenario == "link":
        summary_fields["relate_type"] = getattr(args, "relate_type", None)
        summary_fields["tree_type"] = getattr(args, "tree_type", None)
        summary_fields["relate_conditions"] = getattr(args, "relate_conditions", None)
    if scenario == "view":
        summary_fields["query_url"] = getattr(args, "query_url", None)
        summary_fields["view_id"] = getattr(args, "view_id", None)
    parsed_summary = format_parsed_summary(
        summary_fields,
        title="本次语义解析结果",
    )
    mm_notice = format_group_by_multi_match_notice(group_by_multi_match)
    body_with_mindset = (text + "\n\n" + mindset_text) if mindset_text else text
    blocks = []
    if parsed_summary:
        blocks.append(parsed_summary)
    if mm_notice:
        blocks.append(mm_notice.rstrip())
    blocks.append(body_with_mindset)
    out_for_display = "\n\n".join(blocks)
    safe_print(out_for_display)


# ---------- 客户端统计（原 stats_work_item） ----------

def parse_args():
    """从命令行解析查询列、条件、排序、条数、统计类型与统计字段。"""
    p = argparse.ArgumentParser(description="工作项统计：按查询列与条件查询后做 COUNT/SUM/AVG/MIN/MAX。")
    p.add_argument("--select-items", "--selectItems", dest="select_items", default="", metavar="ITEMS", help="查询列，英文逗号分隔的字段名称；不传则使用默认列。")
    p.add_argument("--conditions", "-c", dest="conditions", default="", metavar="CONDITIONS", help="条件组合：支持 AND/OR、括号 () 组合与嵌套；不传则无过滤。")
    p.add_argument("--order-by", "--orderBy", dest="order_by", default="", metavar="ORDER", help="排序，如 更新时间 desc；不传则默认按更新时间倒序。")
    p.add_argument(
        "--limit",
        "-n",
        dest="limit",
        default=None,
        metavar="N",
        type=int,
        help="参与统计的条数上限，正整数，最大 10000；不传则脚本最多拉取约 3000 条参与统计（分页拉取）。",
    )
    p.add_argument("--stats", "-t", choices=["count", "sum", "avg", "min", "max"], default="count", help="统计类型，默认 count")
    p.add_argument("--field", "-F", dest="field", default=None, help="统计字段（sum/avg/min/max 时必填，为字段名称）")
    p.add_argument("--user-input", "--userInput", dest="user_input", default="", metavar="TEXT", help="用户的原始输入，必须是用户真实完整的一句话。")
    return p.parse_args()


def adjust_params(args, base_config, headers):
    """解析工作区、构建查询体；校验 sum/avg/min/max 时必填 field。返回 (workspace, query_data, work_item_type_original_names)。"""
    if args.stats in ("sum", "avg", "min", "max") and not (args.field or "").strip():
        raise ValueError(f"统计类型为 {args.stats} 时需指定 --field（字段名称）")
    raw_user_input = (getattr(args, "user_input", None) or "").strip() or None
    workspace, workspace_source, err = resolve_workspace_with_source(
        None, base_config.get("workspace") or "", headers, raw_user_input=raw_user_input
    )
    if err:
        raise ValueError(err)
    workspace_single = workspace[0] if isinstance(workspace, list) else workspace
    if workspace_single == "SYSMySpace":
        raise ValueError("统计请使用通用工作区，请确保 config 中 api.workspace 为具体工作区。")
    select_items_str = (args.select_items or "").strip() or None
    conditions_str = (args.conditions or "").strip() or None
    order_by_str = (args.order_by or "").strip() or None
    limit = getattr(args, "limit", None)
    query_data = build_query_data_from_select_and_conditions(
        select_items_str, conditions_str, workspace_single, headers, for_my_workspace=False,
        order_by_str=order_by_str, limit=limit, raw_user_input=raw_user_input
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
    query_data["onlyResult"] = True
    query_data["pageNo"] = 1
    if limit is None:
        query_data["pageSize"] = STATS_PAGE_SIZE
    else:
        query_data["pageSize"] = min(max(1, int(limit)), QUERY_LIMIT_MAX)
    log_complete_params(
        logger,
        "参数调整完成",
        {
            "workspace": workspace_single,
            "stats": args.stats,
            "field": args.field,
            "select_items": args.select_items,
            "conditions": args.conditions,
            "order_by": args.order_by,
            "limit": limit,
        },
    )
    return workspace_single, query_data, _wit_orig


def fetch_items_for_stats(workspace, query_data, headers, limit=None):
    """拉取统计所需数据（分页）；未传 limit 时最多约 3 页；传 limit 时最多拉取 min(limit, QUERY_LIMIT_MAX) 条。"""
    if limit is not None:
        try:
            cap = min(max(1, int(limit)), QUERY_LIMIT_MAX)
        except (TypeError, ValueError):
            cap = STATS_PAGE_SIZE * 3
    else:
        cap = STATS_PAGE_SIZE * 3
    logger.info("开始拉取统计数据: workspace=%s, cap=%s", workspace, cap)
    all_items = []
    page = 1
    while len(all_items) < cap:
        q = dict(query_data)
        q["pageNo"] = page
        q["pageSize"] = min(STATS_PAGE_SIZE, cap - len(all_items))
        if q["pageSize"] <= 0:
            break
        raw = query_work_items(workspace, q, headers)
        _raise_on_business_error(raw, action="统计查询")
        items = _extract_items(raw)
        if not items:
            break
        room = cap - len(all_items)
        all_items.extend(items[:room])
        if len(items) < q["pageSize"]:
            break
        page += 1
    logger.info("统计拉取完成: 共 %d 条", len(all_items))
    return all_items


def _extract_items(response):
    """从查询响应中取出 items。"""
    if isinstance(response.get("data"), dict):
        return response["data"].get("items") or []
    bo = response.get("bo") or {}
    res = bo.get("result")
    if isinstance(res, dict):
        return res.get("items") or []
    return []


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


def _numeric_value(val):
    """从字段值中解析数字（用于 sum/avg/min/max）。"""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val) if not isinstance(val, bool) else None
    if isinstance(val, dict):
        v = val.get("value") or val.get("name") or val.get("nameZh")
        return _numeric_value(v)
    if isinstance(val, str):
        try:
            return float(val.strip())
        except ValueError:
            return None
    return None


def compute_stats(items, stats_type, field_key, query_data):
    """
    根据统计类型计算：count 直接返回 len；sum/avg/min/max 需从 items 中取 field 对应 key 并聚合法。
    若未传 field_key，从 query_data.selectItems 取第一个数值型字段（仅对 sum/avg/min/max）。
    """
    if stats_type == "count":
        return len(items), None
    if not items:
        return None, None
    if field_key:
        keys_to_try = [field_key]
    else:
        keys_to_try = []
        if query_data and isinstance(query_data.get("selectItems"), list):
            for s in query_data["selectItems"]:
                if isinstance(s, dict) and s.get("key"):
                    keys_to_try.append(s["key"])
        if not keys_to_try:
            keys_to_try = ["System_Order", "CompletedWork", "System_RevisedDate"]
    values = []
    for item in items:
        for k in keys_to_try:
            v = _numeric_value(item.get(k))
            if v is not None:
                values.append(v)
                break
    if not values:
        return None, keys_to_try[0] if keys_to_try else None
    if stats_type == "sum":
        return sum(values), keys_to_try[0]
    if stats_type == "avg":
        return sum(values) / len(values), keys_to_try[0]
    if stats_type == "min":
        return min(values), keys_to_try[0]
    if stats_type == "max":
        return max(values), keys_to_try[0]
    return None, None


def resolve_field_key_for_stats(field_name, workspace, headers):
    """将字段名称解析为 key（用于 sum/avg/min/max）。"""
    from common.field_resolver import fetch_fields, resolve_field_key
    field_items = fetch_fields(workspace, headers)
    return resolve_field_key((field_name or "").strip(), field_items, headers, raise_on_not_found=False, scene="统计字段映射", workspace=workspace)


def format_result(stats_type, value, count, field_hint):
    """将统计结果格式化为可读文本。"""
    lines = ["# 工作项统计\n"]
    if stats_type == "count":
        lines.append(f"**条数**：{value}\n")
    else:
        lines.append(f"**统计类型**：{stats_type}\n")
        lines.append(f"**参与条数**：{count}\n")
        if value is not None:
            lines.append(f"**结果**：{value}\n")
        if field_hint:
            lines.append(f"**统计字段**：{field_hint}\n")
    return "".join(lines)


def append_hints(has_data, stats_type):
    """在结果为空或异常时追加提示。"""
    if has_data and stats_type == "count":
        return ""
    if not has_data:
        return (
            "\n---\n**统计结果为空**。可尝试：① 放宽查询条件；"
            "② 确认工作区与工作项类型；③ 确认统计字段在该类型下存在且为数值。"
        )
    return ""


def run():
    """总方法：参数解析 → 参数调整 → 调用 API → 结果处理 → 结果格式化 → 追加心智提示。"""
    args = parse_args()
    log_complete_params(logger, "参数解析完成", vars(args))
    base_config = load_base_config(CONFIG_FILE)
    headers = get_auth_headers(None, base_config, logger=logger)
    workspace, query_data, _wit_orig = adjust_params(args, base_config, headers)
    field_key = None
    if args.stats in ("sum", "avg", "min", "max") and (args.field or "").strip():
        field_key = resolve_field_key_for_stats(args.field.strip(), workspace, headers) or args.field.strip()
    items = fetch_items_for_stats(workspace, query_data, headers, limit=args.limit)
    value, field_used = compute_stats(items, args.stats, field_key, query_data)
    if args.stats == "count":
        value = value if value is not None else 0
    text = format_result(args.stats, value, len(items), field_used or args.field)
    text += append_hints(len(items) > 0 or args.stats == "count", args.stats)
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
            "stats": args.stats,
            "field": args.field,
            "select_items": args.select_items,
            "conditions": args.conditions,
            "order_by": args.order_by,
            "limit": args.limit,
        },
        title="本次语义解析结果",
    )
    body_with_mindset = (text + "\n\n" + mindset_text) if mindset_text else text
    out_for_display = (parsed_summary + "\n" + body_with_mindset) if parsed_summary else body_with_mindset
    logger.info("统计完成: stats=%s, 参与条数=%d, 结果=%s", args.stats, len(items), value)
    safe_print(out_for_display)


def main():
    """入口：首参 `aggregate` 走分组聚合，否则走客户端统计。"""
    apply_log_config(CONFIG_FILE)
    argv_full = list(sys.argv)
    is_aggregate = len(argv_full) > 1 and argv_full[1] == "aggregate"
    if ENABLE_LOG:
        setup_logging(LOG_FILE_AGGREGATE if is_aggregate else LOG_FILE_STATS)
    log_raw_argv(logger, argv_full)
    if is_aggregate:
        sys.argv = [argv_full[0]] + argv_full[2:]
    try:
        if is_aggregate:
            run_aggregate()
        else:
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
    finally:
        sys.argv = argv_full


if __name__ == "__main__":
    main()
