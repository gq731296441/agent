# -*- coding: utf-8 -*-
"""工作项层级数据处理：children 展平、关系标签映射、显示前缀生成。"""

from typing import Any, Dict, List, Tuple

from .rdcloud_api import LINK_RELATION_TYPE_MAP

_RELATION_ID_TO_NAME = {str(v): k for k, v in LINK_RELATION_TYPE_MAP.items()}


def _relation_type_id(item: Dict[str, Any]) -> str:
    """从条目中提取 relation.Relation_Type_Id（字符串），无则返回空串。"""
    if not isinstance(item, dict):
        return ""
    rel = item.get("relation")
    if not isinstance(rel, dict):
        return ""
    rid = rel.get("Relation_Type_Id")
    return str(rid).strip() if rid is not None else ""


def relation_label_from_item(item: Dict[str, Any]) -> str:
    """
    将条目 relation.Relation_Type_Id 映射为中文标签。
    若映射不到，返回原始 id（便于排查）；无 id 返回空串。
    """
    rid = _relation_type_id(item)
    if not rid:
        return ""
    return _RELATION_ID_TO_NAME.get(rid, rid)


def _count_subtree_nodes(node: Dict[str, Any]) -> int:
    """统计单个节点子树总节点数（含自身）。"""
    if not isinstance(node, dict):
        return 0
    total = 1
    children = node.get("children")
    if isinstance(children, list):
        for c in children:
            total += _count_subtree_nodes(c)
    return total


def _filter_virtual_root_items(
    items: List[Dict[str, Any]],
    filter_virtual_root: bool = False,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    按需过滤首个 virtual 根节点。
    返回：过滤后的 source_items、virtual 子树节点数（不含 virtual 根自身）。
    """
    virtual_count = 0
    source_items = items if isinstance(items, list) else []
    if filter_virtual_root and source_items:
        first = source_items[0]
        if isinstance(first, dict) and str(first.get("_id") or "").strip() == "virtual":
            virtual_count = max(len(flatten_hierarchy_items([first])) - 1, 0)
            source_items = source_items[1:]
    return source_items, virtual_count


def _node_display_name(node: Dict[str, Any], index: int) -> str:
    if not isinstance(node, dict):
        return f"第{index}项"
    return (
        str(node.get("System_Id") or "").strip()
        or str(node.get("_id") or "").strip()
        or str(node.get("System_Title") or "").strip()
        or f"第{index}项"
    )


def build_hierarchy_statistics_markdown(
    items: List[Dict[str, Any]],
    filter_virtual_root: bool = False,
) -> str:
    """
    构造层级统计摘要：
    - 第 N 层有多少工作项
    - 第 N 层每个工作项各自 children 数
    """
    source_items, _ = _filter_virtual_root_items(items, filter_virtual_root=filter_virtual_root)
    current_level = [n for n in source_items if isinstance(n, dict)]
    if not current_level:
        return ""

    lines: List[str] = ["## 层级统计\n"]
    level = 1
    while current_level:
        lines.append(f"- 第{level}层：{len(current_level)} 个工作项")
        per_node = []
        next_level: List[Dict[str, Any]] = []
        for idx, node in enumerate(current_level, start=1):
            children_raw = node.get("children")
            children = [c for c in children_raw if isinstance(c, dict)] if isinstance(children_raw, list) else []
            if len(children) > 0:
                per_node.append(f"{_node_display_name(node, idx)}({len(children)})")
            next_level.extend(children)
        if per_node:
            lines.append(f"- 第{level}层每个工作项 children 数：{'，'.join(per_node)}")
        current_level = next_level
        level += 1
    return "\n".join(lines) + "\n"


def flatten_hierarchy_items(
    items: List[Dict[str, Any]],
    omit_large_children: bool = False,
    children_display_limit: int = 0,
) -> List[Dict[str, Any]]:
    """
    将 items 的 children 树形结构展开为线性列表（前序遍历）。
    每条记录附加：
    - __tree_depth: 层级深度（根=0）
    - __tree_relation_label: 关系标签（由 relation.Relation_Type_Id 映射）
    """
    out: List[Dict[str, Any]] = []

    limit = int(children_display_limit or 0)

    def _append_omitted_row(depth: int, omitted_rows: int):
        out.append(
            {
                "System_Title": f"...(已省略{omitted_rows}行)",
                "System_Id": "",
                "__tree_depth": depth,
                "__tree_relation_label": "",
                "__tree_omitted": True,
            }
        )

    def walk(node: Dict[str, Any], depth: int):
        if not isinstance(node, dict):
            return
        row = dict(node)
        row["__tree_depth"] = depth
        row["__tree_relation_label"] = relation_label_from_item(node)
        out.append(row)
        children = node.get("children")
        if isinstance(children, list):
            walk_list(children, depth + 1)

    def walk_list(nodes: List[Dict[str, Any]], depth: int):
        if not isinstance(nodes, list):
            return
        # 对同一层的兄弟节点统一应用省略策略（包含第一层 items）
        if omit_large_children and limit > 0 and len(nodes) > limit:
            shown_nodes = nodes[:limit]
            omitted_nodes = nodes[limit:]
            for n in shown_nodes:
                walk(n, depth)
            omitted_rows = sum(_count_subtree_nodes(n) for n in omitted_nodes)
            _append_omitted_row(depth, omitted_rows)
            return
        for n in nodes:
            walk(n, depth)

    if isinstance(items, list):
        walk_list(items, 0)
    return out


def flatten_hierarchy_items_with_virtual_filter(
    items: List[Dict[str, Any]],
    filter_virtual_root: bool = False,
    omit_large_children: bool = False,
    children_display_limit: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    展平层级数据，并按需过滤首个虚拟根节点（_id=virtual）。

    返回:
    - flat_items: 展平后的记录
    - virtual_count: 被过滤的 virtual 根节点子树总节点数（不含 virtual 根自身）
    """
    source_items, virtual_count = _filter_virtual_root_items(items, filter_virtual_root=filter_virtual_root)
    return flatten_hierarchy_items(
        source_items,
        omit_large_children=omit_large_children,
        children_display_limit=children_display_limit,
    ), virtual_count


def hierarchy_prefix(item: Dict[str, Any]) -> str:
    """
    生成层级前缀（用于标题列）：
    - 按深度增加缩进（每层 8 个 &nbsp;）
    - 非根层且有 relation 标签时，前置高亮标签（加粗 + 颜色）
    """
    if not isinstance(item, dict):
        return ""
    depth = item.get("__tree_depth")
    try:
        depth = int(depth or 0)
    except (TypeError, ValueError):
        depth = 0
    indent = "&nbsp;" * (depth * 8) if depth > 0 else ""
    label = (item.get("__tree_relation_label") or "").strip()
    if depth > 0 and label:
        return f'{indent}<span style="color:#d97706;"><strong>[{label}]</strong></span> '
    return indent

