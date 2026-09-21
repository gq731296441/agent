# -*- coding: utf-8 -*-
"""工作区解析、查询条件与工作区子句合并（供脚本 from common.workspace import ...）。"""

from .workspace_resolver import resolve_workspace, resolve_workspace_with_source
from .workspace_clause_helper import (
    WORKSPACE_CLAUSE_FIELDS,
    append_workspace_clause_if_needed,
    apply_workspace_clause_to_query_condition,
)

__all__ = [
    "resolve_workspace",
    "resolve_workspace_with_source",
    "WORKSPACE_CLAUSE_FIELDS",
    "append_workspace_clause_if_needed",
    "apply_workspace_clause_to_query_condition",
]
