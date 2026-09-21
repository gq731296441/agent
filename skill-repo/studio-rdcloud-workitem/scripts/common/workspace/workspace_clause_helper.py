# -*- coding: utf-8 -*-
"""
查询条件 sourceClauses 与工作区解析结果联动：
当工作区来源为 AI 语义解析时，将解析得到的工作区 key 并入已有工作区条件（= / =_accurate 改为 in），
或在没有工作区条件时插入 System_WorkspaceKey 子句。

供 query_work_item、query_my_work_item、export_work_item、stats_work_item 等脚本共用。
"""


def _workspace_keys_to_merge(workspace):
    """将解析得到的工作区（str 或 list）规范为去重后的 key 列表（顺序保留）。"""
    if isinstance(workspace, list):
        raw = [str(w).strip() for w in workspace if w is not None and str(w).strip()]
    else:
        s = str(workspace).strip()
        raw = [s] if s else []
    seen = set()
    out = []
    for x in raw:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _merge_ai_workspace_into_existing_clause(clause, ws_keys):
    """
    工作区来自 AI 且 where 中已有工作区条件时：把 ws_keys 并入该条件的 value。
    - 原操作符为 = 或 =_accurate 时，改为 in，值为 [原值..., ...ws_keys]（去重保序）。
    - 原操作符为 in 时，在原列表上追加 ws_keys（去重保序）。
    - 其它操作符不修改。
    """
    if not isinstance(clause, dict) or not ws_keys:
        return False
    op = (clause.get("operator") or "").strip().lower()
    val = clause.get("value")
    if op in ("=", "=_accurate"):
        if isinstance(val, list):
            old = [str(v).strip() for v in val if v is not None and str(v).strip()]
        else:
            old = [str(val).strip()] if val is not None and str(val).strip() else []
        merged = _workspace_keys_to_merge(old + ws_keys)
        clause["operator"] = "in"
        clause["value"] = merged
        return True
    if op == "in":
        if isinstance(val, list):
            old = [str(v).strip() for v in val if v is not None and str(v).strip()]
        else:
            old = [str(val).strip()] if val is not None and str(val).strip() else []
        merged = _workspace_keys_to_merge(old + ws_keys)
        clause["value"] = merged
        return True
    return False


WORKSPACE_CLAUSE_FIELDS = frozenset(
    {
        "System_WorkspaceKey",
        "System_Workspace",
        "System_WorkspaceKey.name",
        "System_Workspace.name",
    }
)


def append_workspace_clause_if_needed(clauses, workspace, workspace_source):
    """
    当工作区来自 AI（workspace_source == \"ai\"）时：
    - 若条件中尚无工作区字段，则在首部插入 System_WorkspaceKey 条件；
    - 若已有工作区条件（= / =_accurate / in），则将解析得到的工作区 key 并入 value（= / =_accurate 会改为 in）。
    - 插入首部子句后，若紧随其后的第一条子句 logicalOperator 为空或空字符串，则设为 AND。

    :param clauses: queryCondition.sourceClauses 列表（原地修改）
    :param workspace: 解析得到的工作区 key，str 或 list
    :param workspace_source: resolve_workspace_with_source 的 source，仅 \"ai\" 时生效
    :return: 同一 clauses 引用
    """
    if workspace_source != "ai" or not workspace:
        return clauses
    if not isinstance(clauses, list):
        return clauses
    ws_keys = _workspace_keys_to_merge(workspace)
    if not ws_keys:
        return clauses
    merged_any = False
    for c in clauses:
        if isinstance(c, dict) and (c.get("field") or "") in WORKSPACE_CLAUSE_FIELDS:
            if _merge_ai_workspace_into_existing_clause(c, ws_keys):
                merged_any = True
                break
    if merged_any:
        return clauses
    if isinstance(workspace, list):
        clause_value = [w for w in workspace if w]
        if not clause_value:
            return clauses
        clause_operator = "in"
    else:
        clause_value = workspace
        clause_operator = "="
    new_clause = {
        "leftGroup": 0,
        "rightGroup": 0,
        "logicalOperator": "",
        "field": "System_WorkspaceKey",
        "operator": clause_operator,
        "value": clause_value,
    }
    clauses.insert(0, new_clause)
    if len(clauses) > 1:
        nxt = clauses[1]
        if isinstance(nxt, dict):
            lo = nxt.get("logicalOperator")
            if lo is None or (isinstance(lo, str) and lo.strip() == ""):
                nxt["logicalOperator"] = "AND"
    return clauses


def apply_workspace_clause_to_query_condition(query_condition, workspace, workspace_source):
    """
    对 queryCondition 字典就地更新 sourceClauses（调用 append_workspace_clause_if_needed）。
    若 query_condition 非法则原样返回。
    """
    if not query_condition or not isinstance(query_condition, dict):
        return query_condition
    qc = query_condition
    source_clauses = qc.get("sourceClauses") or []
    if not isinstance(source_clauses, list):
        return query_condition
    append_workspace_clause_if_needed(source_clauses, workspace, workspace_source)
    qc["sourceClauses"] = source_clauses
    return query_condition
