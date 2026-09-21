"""
`stats_work_item.py aggregate` 子命令：各场景 query_data 构建（原多文件合并）。

与 query_work_item、query_my_work_item、query_overdue_work_item 主流程对齐；由 `stats_work_item.py` 的 aggregate 路径追加 groupingKeys。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from common.rdcloud_api import get_view_query_config, parse_query_url
from common.sql_query_parser import QUERY_LIMIT_MAX, build_query_data_from_select_and_conditions
from common.workspace import apply_workspace_clause_to_query_condition

from query_my_work_item import (
    _extract_work_item_type_keys_from_query_condition,
    add_default_logical_operator as add_default_logical_operator_my,
    add_exclude_completed_condition as add_exclude_completed_condition_my,
    add_time_range_condition as add_time_range_condition_my,
    get_my_workspace_conditions as get_my_workspace_conditions_my,
)
from query_overdue_work_item import (
    _build_overdue_source_clauses,
    _get_overdue_cfg,
    add_default_logical_operator as add_default_logical_operator_od,
    add_exclude_completed_condition as add_exclude_completed_condition_od,
    add_time_range_condition as add_time_range_condition_od,
    get_my_workspace_conditions as get_my_workspace_conditions_od,
    get_overdue_date_fields,
    resolve_type_key,
)
from query_work_item import (
    _normalize_related_options,
    _normalize_tree_options,
    _validate_query_mode_input,
    add_default_logical_operator as add_default_logical_operator_qwi,
    convert_work_item_type_values,
    resolve_relation_type_id,
)


# ---------------------------------------------------------------------------
# 视图 URL / 视图 ID
# ---------------------------------------------------------------------------


def _is_24_hex(s: str) -> bool:
    if not s or not isinstance(s, str) or len(s) != 24:
        return False
    return all(c in "0123456789abcdefABCDEF" for c in s)


def _resolve_view_id_and_workspace(
    query_url: str,
    view_id: str,
    view_workspace: str,
    fallback_workspace: str,
) -> Tuple[str, str]:
    """返回 (request_workspace, view_id_24)。"""
    vid = (view_id or "").strip()
    vws = (view_workspace or "").strip()
    url = (query_url or "").strip()

    if vid:
        if not _is_24_hex(vid):
            raise ValueError("聚合视图仅支持 24 位十六进制视图 ID（--view-id）")
        ws = vws or (fallback_workspace or "").strip()
        if not ws:
            raise ValueError("使用 --view-id 时请同时指定 --view-workspace，或在配置中设置 api.workspace")
        return ws, vid.lower()

    if not url:
        raise ValueError("请提供 --query-url（研发云视图链接）或同时提供 --view-id 与 --view-workspace")

    parsed = parse_query_url(url)
    if not parsed:
        raise ValueError("无法从 --query-url 解析为支持的查询链接，请使用含 24 位视图 ID 的 WIM/iChange/subject 链接")

    qid = (parsed.get("view_id") or parsed.get("query_id") or "").strip()
    if not _is_24_hex(qid):
        raise ValueError(
            "分组聚合视图场景当前仅支持 24 位视图 ID（get_view_query_config）；"
            "数字 queryId 的链接请使用列表查询脚本，或使用含 wicViewId 的链接。"
        )

    ws = (parsed.get("workspace") or "").strip() or vws or (fallback_workspace or "").strip()
    if not ws:
        raise ValueError("该链接未包含工作区，请设置 --view-workspace 或配置 api.workspace")
    return ws, qid.lower()


def build_view_query_body_for_aggregate(
    query_url: str,
    view_id: str,
    view_workspace: str,
    fallback_workspace: str,
    headers: Dict[str, Any],
) -> Tuple[Dict[str, Any], str]:
    """返回 (query_data, request_workspace)。"""
    ws, vid = _resolve_view_id_and_workspace(
        query_url, view_id, view_workspace, fallback_workspace
    )
    step1 = get_view_query_config(ws, vid, headers)
    code_obj = (step1 or {}).get("code") or {}
    code_val = code_obj.get("code") if isinstance(code_obj, dict) else None
    if code_val != "0000":
        msg = code_obj.get("msg") or code_obj.get("msgId") or "Unknown"
        raise ValueError(f"拉取视图配置失败：{msg}（code={code_val}）")
    bo = (step1 or {}).get("bo")
    if not isinstance(bo, dict):
        raise ValueError("视图配置响应 bo 格式异常")
    body = {k: v for k, v in bo.items() if k != "allWorkItemTypeKey"}
    if not body.get("queryCondition") and not body.get("selectItems"):
        raise ValueError("视图配置中缺少有效查询条件，无法聚合")
    return body, ws


# ---------------------------------------------------------------------------
# 超期
# ---------------------------------------------------------------------------


def _parse_type_names(work_item_type_str: str) -> List[str]:
    if not work_item_type_str:
        return ["任务"]
    return [t.strip() for t in work_item_type_str.split(",") if t.strip()]


def _build_sort_items(order_by: str, overdue_date_fields: List[str]) -> List[Dict[str, Any]]:
    if order_by:
        parts = order_by.strip().split()
        field = parts[0]
        is_asc = len(parts) < 2 or parts[1].lower() != "desc"
        return [{"key": field, "isAscending": is_asc}]
    cfg = _get_overdue_cfg()
    default_field = cfg["default_overdue_date_field"]
    primary_field = overdue_date_fields[0] if overdue_date_fields else default_field
    return [{"key": primary_field, "isAscending": True}]


def build_overdue_query_body_for_aggregate(
    work_item_type_str: str,
    order_by_str: Optional[str],
    limit: Any,
    headers: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[str]]:
    """
    返回 (query_data, 工作项类型中文名列表)。
    不含 onlyResult / pageNo / pageSize / groupingKeys（由 stats_work_item aggregate 统一写入）。
    """
    emp_no = headers.get("X-Emp-No", "")
    if not emp_no:
        raise ValueError("超期分组聚合需要认证头中的工号 X-Emp-No")

    type_names = _parse_type_names((work_item_type_str or "").strip() or "任务")
    type_keys = [resolve_type_key(t) for t in type_names if resolve_type_key(t)]
    overdue_date_fields = get_overdue_date_fields(type_names)

    overdue_clauses = _build_overdue_source_clauses(overdue_date_fields)
    query_condition: Dict[str, Any] = {
        "type": "flat",
        "sourceClauses": overdue_clauses,
    }
    query_condition = add_time_range_condition_od(query_condition)
    query_condition = add_exclude_completed_condition_od(query_condition)
    query_condition = add_default_logical_operator_od(query_condition)

    cfg = _get_overdue_cfg()
    select_items: List[Dict[str, str]] = [
        {"key": "System_Id"},
        {"key": "System_Title"},
        {"key": "System_WorkItemType"},
        {"key": "System_State"},
        {"key": "System_ChangedDate"},
    ] + [{"key": f} for f in cfg["all_overdue_date_fields"]]

    sort_items = _build_sort_items((order_by_str or "").strip(), overdue_date_fields)

    page_size = QUERY_LIMIT_MAX
    if limit is not None:
        try:
            page_size = min(max(1, int(limit)), QUERY_LIMIT_MAX)
        except (TypeError, ValueError):
            page_size = QUERY_LIMIT_MAX

    query_data: Dict[str, Any] = {
        "crossWorkspace": True,
        "myWorkspaceConditions": get_my_workspace_conditions_od(emp_no),
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

    return query_data, type_names


# ---------------------------------------------------------------------------
# 与我相关
# ---------------------------------------------------------------------------


def build_my_related_query_body_for_aggregate(
    select_items_str: Optional[str],
    conditions_str: Optional[str],
    order_by_str: Optional[str],
    limit: Any,
    query_type: str,
    api_workspace: Any,
    workspace_source: Any,
    headers: Dict[str, Any],
    emp_no: str,
    raw_user_input: Optional[str] = None,
    logger=None,
) -> Tuple[Dict[str, Any], List[str]]:
    """
    返回 (query_data, work_item_type_original_names)。
    不含 onlyResult / pageNo / pageSize（由 stats_work_item aggregate 在写入 groupingKeys 后统一设置）。
    """
    log = logger.warning if logger else (lambda *a, **k: None)

    api_workspace_single = api_workspace[0] if isinstance(api_workspace, list) else api_workspace
    query_data = build_query_data_from_select_and_conditions(
        select_items_str or None,
        conditions_str or None,
        api_workspace_single,
        headers,
        for_my_workspace=True,
        order_by_str=order_by_str or None,
        limit=limit,
        raw_user_input=raw_user_input,
    )
    query_data.pop("_multi_match_fields", None)
    query_data["crossWorkspace"] = True
    query_data["myWorkspaceConditions"] = get_my_workspace_conditions_my(query_type, emp_no)
    _qc, _wit_orig = convert_work_item_type_values(
        query_data["queryCondition"], api_workspace_single, headers
    )
    query_data["queryCondition"] = _qc
    qc_after_type, type_keys = _extract_work_item_type_keys_from_query_condition(
        query_data.get("queryCondition") or {}
    )
    query_data["queryCondition"] = qc_after_type
    if type_keys:
        existing = query_data.get("workItemTypeKeys") or []
        merged = list(dict.fromkeys(list(existing) + list(type_keys)))
        query_data["workItemTypeKeys"] = merged
    query_data["queryCondition"] = add_default_logical_operator_my(query_data["queryCondition"])
    query_data["queryCondition"] = add_time_range_condition_my(query_data["queryCondition"])
    query_data["queryCondition"] = add_exclude_completed_condition_my(query_data["queryCondition"])
    try:
        apply_workspace_clause_to_query_condition(
            query_data.setdefault("queryCondition", {}),
            api_workspace,
            workspace_source,
        )
    except Exception as e:
        log("自动合并/补充工作区条件时发生异常（忽略，不影响查询）: %s", e)
    return query_data, _wit_orig


# ---------------------------------------------------------------------------
# 关联 / 树
# ---------------------------------------------------------------------------


def build_link_query_body_for_aggregate(
    select_items_str: Optional[str],
    conditions_str: Optional[str],
    relate_conditions_str: Optional[str],
    order_by_str: Optional[str],
    limit: Any,
    relate_type: str,
    tree_type: str,
    related_options: str,
    tree_options: str,
    workspace: Any,
    workspace_source: Any,
    headers: Dict[str, Any],
    raw_user_input: Optional[str] = None,
    logger=None,
) -> Tuple[Dict[str, Any], List[str]]:
    """返回 (query_data, work_item_type_original_names)。"""
    log = logger.warning if logger else (lambda *a, **k: None)

    input_data = {
        "relate_type": relate_type or "",
        "tree_type": tree_type or "",
        "relate_conditions": relate_conditions_str or "",
        "relatedOptions": related_options or "",
        "treeOptions": tree_options or "",
    }
    _validate_query_mode_input(input_data)

    api_workspace_single = workspace[0] if isinstance(workspace, list) else workspace

    query_data = build_query_data_from_select_and_conditions(
        select_items_str or None,
        conditions_str or None,
        api_workspace_single,
        headers,
        for_my_workspace=False,
        order_by_str=order_by_str or None,
        limit=limit,
        raw_user_input=raw_user_input,
    )
    try:
        apply_workspace_clause_to_query_condition(
            query_data.setdefault("queryCondition", {}),
            workspace,
            workspace_source,
        )
    except Exception as e:
        log("自动合并/补充工作区条件时发生异常（忽略）: %s", e)

    query_condition = query_data.get("queryCondition") or {}
    rt = (relate_type or "").strip()
    tt = (tree_type or "").strip()
    target_clauses: List[Any] = []
    if rt or tt:
        target_query_data = build_query_data_from_select_and_conditions(
            None,
            relate_conditions_str or None,
            api_workspace_single,
            headers,
            for_my_workspace=False,
            order_by_str=None,
            limit=None,
            raw_user_input=raw_user_input,
        )
        target_clauses = (target_query_data.get("queryCondition") or {}).get("sourceClauses") or []

    if rt:
        if rt.strip() in ("全部", "所有", "所有类型"):
            relation_ids: List[Any] = []
        else:
            relation_ids = [resolve_relation_type_id(rt)]
        query_condition["type"] = "oneHop"
        query_condition["removeTop"] = True
        query_condition["relatedType"] = relation_ids
        related_options_api = (
            _normalize_related_options(related_options) or "linksOneHopMustContain"
        )
        query_condition["relatedOptions"] = related_options_api
        query_condition["targetClauses"] = target_clauses
    elif tt:
        relation_id = resolve_relation_type_id(tt)
        query_condition["type"] = "tree"
        query_condition["removeTop"] = True
        query_condition["relatedType"] = [relation_id]
        tree_options_api = _normalize_tree_options(tree_options) or "top"
        query_condition["treeOptions"] = tree_options_api
        query_condition["targetClauses"] = target_clauses

    query_data["queryCondition"] = query_condition
    query_data.pop("_multi_match_fields", None)
    query_data["queryCondition"] = add_default_logical_operator_qwi(query_data["queryCondition"])
    _qc, _wit_orig = convert_work_item_type_values(
        query_data["queryCondition"], api_workspace_single, headers
    )
    query_data["queryCondition"] = _qc
    query_data["onlyResult"] = True
    query_data["crossWorkspace"] = True
    return query_data, _wit_orig
