# -*- coding: utf-8 -*-
"""
字段值解析：根据工作区、工作项类型、字段类型等对 field.value 做二次查找。

- 人员字段（USER）：姓名 / @me 等解析为工号（resolve_person_to_emp_no）。
- 高级数据（advancedData）：
  - sourceType=remote：远程模糊查询候选，支持 ZX/XY 同后缀时优先取 XY。
  - sourceType=remoteRealTime：实时数据源查询，同上过滤。
- 字符串下拉（string + pickListDataScopeConfig）：按 referencePickListName 拉候选，多候选时完全匹配优先。
- 其它类型：原样返回，不解析。

供创建、查询、更新等场景统一使用；创建流程中会配合 PUT event 与描述模版（富文本填空）处理。
"""

from typing import Any, Dict, List, Optional
import json
import re
import time

from .auth_helper import resolve_person_to_emp_no
from .log_helper import get_logger, truncate_for_log
from .field_resolver import fetch_fields
from . import rdcloud_api

logger = get_logger(__name__)

# 所属产品、所属项目需优先解析，顺序：BelongProduct → BelongProject
FIELDS_PRIORITY_ORDER = ("BelongProduct", "BelongProject")


def reorder_with_priority(keys: List[str], priority_order: tuple = FIELDS_PRIORITY_ORDER) -> List[str]:
    """
    将 priority_order 中的 key 移到最前并保持其顺序，其余保持原序。
    供 fields、required_field_keys 等统一使用。
    """
    if not keys:
        return []
    priority_set = set(priority_order)
    front = [k for k in priority_order if k in keys]
    rest = [k for k in keys if k not in priority_set]
    return front + rest


def _reorder_fields_with_priority(fields_list: List[dict]) -> List[dict]:
    """将 BelongProduct、BelongProject 移到最前，BelongProduct 在 BelongProject 前；其余保持原序。"""
    if not fields_list:
        return []
    valid = [f for f in fields_list if isinstance(f, dict) and (f.get("key") or "").strip()]
    keys = [(f.get("key") or "").strip() for f in valid]
    ordered_keys = reorder_with_priority(keys)
    key_to_field = {k: f for k, f in zip(keys, valid)}
    return [key_to_field[k] for k in ordered_keys]


# ---------------------------------------------------------------------------
# 字段元数据：从 field_items 中按 key 取项、类型、展示名
# ---------------------------------------------------------------------------


def _get_field_item(field_key: str, field_items: List[dict]) -> Optional[Dict[str, Any]]:
    """从字段列表中取指定 key 的完整字段项（含 type、baseDataReference、textData 等）。"""
    if not field_key or not field_items:
        return None
    key = (field_key or "").strip()
    for item in field_items:
        if not isinstance(item, dict):
            continue
        if (item.get("key") or "").strip() == key:
            return item
    return None


def _get_field_type(field_key: str, field_items: List[dict]) -> Optional[str]:
    """从 field_items 中取字段类型（如 string、USER、advancedData）。"""
    item = _get_field_item(field_key, field_items)
    if not item:
        return None
    return (item.get("type") or "").strip() or None


def _get_field_display_name(
    field_key: str,
    field_items: List[dict],
    lang: str = "zh",
) -> str:
    """取字段展示名：优先 nameZh，否则 name，否则 nameEn。"""
    item = _get_field_item(field_key, field_items)
    if not item:
        return (field_key or "").strip()
    name_zh = (item.get("nameZh") or "").strip()
    name = (item.get("name") or "").strip()
    name_en = (item.get("nameEn") or "").strip()
    if name_zh:
        return name_zh
    if name:
        return name
    return name_en or (field_key or "").strip()


# ---------------------------------------------------------------------------
# advancedData / string 候选请求体构建；请求体格式需与 rdcloud_api 约定一致。
# ---------------------------------------------------------------------------


def _is_advanced_data_remote(field_item: Dict[str, Any]) -> bool:
    """是否 advancedData 且 sourceType=remote。"""
    ref = (field_item or {}).get("baseDataReference") or {}
    adv = ref.get("baseData") or {}
    return (adv.get("sourceType") or "").strip().lower() == "remote"


def _is_advanced_data_remote_real_time(field_item: Dict[str, Any]) -> bool:
    """是否 advancedData 且 sourceType=remoteRealTime。"""
    ref = (field_item or {}).get("baseDataReference") or {}
    adv = ref.get("baseData") or {}
    return (adv.get("sourceType") or "").strip().lower() == "remoterealtime"


def _is_string_with_pick_list_config(field_item: Dict[str, Any]) -> bool:
    """是否 string 类型且配置了 pickListDataScopeConfig。"""
    text_data = (field_item or {}).get("textData") or {}
    pick = text_data.get("pickListDataScopeConfig")
    return bool(pick and isinstance(pick, dict))


def _build_advanced_data_candidate_body(
    field_item: Dict[str, Any],
    work_item_type_key: str,
    search_keyword: str,
    workspace: str = "",
) -> Dict[str, Any]:
    """构建 advancedData 候选请求体，供 fetch_fields_candidate 使用。请求体以 advancedData 为 key。"""
    #logger.info("_build_advanced_data_candidate_body 完整 field_item=%s", json.dumps(field_item, ensure_ascii=False, default=str))
    ref = field_item.get("baseDataReference") or {}
    base = ref.get("baseData") or {}
    adv = field_item.get("advancedData") or {}
    base_data_key = (base.get("baseDataKey") or base.get("key") or "").strip()
    return {
        "type": "advancedData",
        "advancedData": adv,
        "workItemTypeKey": (work_item_type_key or "").strip(),
        "baseDataKey": base_data_key,
        "searchKeyWord": (search_keyword or "").strip(),
        "searchField": (adv.get("searchField") or "").strip(),
        "workItemSessionId": "",
        "currentValue": "",
    }


def _build_remote_real_time_body(
    field_key: str,
    field_item: Dict[str, Any],
    work_item_type_key: str,
    search_keyword: str,
    workspace: str = "",
    event_id: str = "",
) -> Dict[str, Any]:
    """构建 remoteRealTime 候选请求体，供 fetch_remote_data_source_realtime 使用。"""
    ref = (field_item or {}).get("baseDataReference") or {}
    adv = ref.get("advancedData") or {}
    return {
        "workItemTypeKey": (work_item_type_key or "").strip(),
        "key": (field_key or "").strip(),
        "eventId": (event_id or "").strip(),
        "remoteDataConfig": json.dumps(adv) if adv else "",
        "keywords": (search_keyword or "").strip(),
        "currentValue": "",
    }


def _build_string_pick_list_candidate_body(
    field_item: Dict[str, Any],
    work_item_type_key: str,
    search_keyword: str,
) -> Dict[str, Any]:
    """构建 string 下拉候选请求体，含 textData.pickListDataScopeConfig。"""
    text_data = (field_item or {}).get("textData") or {}
    pick = text_data.get("pickListDataScopeConfig") or {}
    remote_data_source = (field_item or {}).get("remoteDataSource")
    body = {
        "type": "string",
        "workItemTypeKey": (work_item_type_key or "").strip(),
        "searchKeyWord": (search_keyword or "").strip(),
        "workItemSessionId": "",
        "currentValue": "",
        "textData": {"pickListDataScopeConfig": pick, "textDataScopeConfig": text_data.get("textDataScopeConfig")},
    }
    # 若字段配置中含有 remoteDataSource 且非空，则一并放入请求体，供后端按数据源配置过滤候选
    if remote_data_source:
        body["remoteDataSource"] = remote_data_source
    return body


def _extract_values_from_bo_items(bo_items: List[Any]) -> List[Any]:
    """从接口返回的 bo 项列表中提取 value；若无 value 则取 label。"""
    if not isinstance(bo_items, list):
        return []
    out = []
    for it in bo_items:
        if not isinstance(it, dict):
            continue
        v = it.get("value") if it.get("value") is not None else it.get("label")
        if v is not None:
            out.append(v)
    #logger.info("【字段候选值解析】从 bo.items 中提取到的候选值列表: %s", out)
    return out


def _filter_base_data_value_nodes_zx_xy(nodes: List[Dict[str, Any]]) -> List[Any]:
    """当同时存在 ZX 与 XY 前缀且后缀相同时，只保留 XY 对应项；否则原样返回 value 列表。"""
    #logger.info("_filter_base_data_value_nodes_zx_xy 输入 nodes=%s", nodes)
    if not nodes or not isinstance(nodes, list):
        out = []
        #logger.info("_filter_base_data_value_nodes_zx_xy 输出 (空输入)=%s", out)
        return out
    values = [n.get("value") for n in nodes if isinstance(n, dict) and n.get("value") is not None]
    if len(values) <= 1:
        #logger.info("_filter_base_data_value_nodes_zx_xy 输出 (≤1 项)=%s", values)
        return values
    zx_vals = [v for v in values if str(v).strip().upper().startswith("ZX")]
    xy_vals = [v for v in values if str(v).strip().upper().startswith("XY")]
    if zx_vals and xy_vals:
        xy_suffix = {}
        for v in xy_vals:
            s = str(v).strip()
            if len(s) > 2:
                xy_suffix[s[2:]] = v
        for v in zx_vals:
            s = str(v).strip()
            if len(s) > 2 and s[2:] in xy_suffix:
                out = [xy_suffix[s[2:]]]
                #logger.info("_filter_base_data_value_nodes_zx_xy 输出 (ZX/XY 同后缀取 XY)=%s", out)
                return out
        #logger.info("_filter_base_data_value_nodes_zx_xy 输出 (仅 XY)=%s", xy_vals)
        return xy_vals
    #logger.info("_filter_base_data_value_nodes_zx_xy 输出 (原样)=%s", values)
    return values


def _find_node_by_value(nodes: List[Dict[str, Any]], value: Any) -> Optional[Dict[str, Any]]:
    """从节点列表中按 value（或 label）匹配，返回完整节点 dict；未找到返回 None。"""
    if not nodes or value is None:
        return None
    val_str = str(value).strip()
    for n in nodes:
        if not isinstance(n, dict):
            continue
        v = n.get("value") if n.get("value") is not None else n.get("label")
        if v is not None and str(v).strip() == val_str:
            return n
    return None


# ---------------------------------------------------------------------------
# 单字段取值解析：USER / advancedData / string 下拉
# ---------------------------------------------------------------------------


def resolve_field_value(
    key: str,
    current_value: Any,
    workspace: str,
    work_item_type_key: str,
    field_items: List[dict],
    headers: Dict[str, Any],
    scene: str = "",
    event_id: Optional[str] = None,
) -> Any:
    """按字段类型解析 value：USER 走人员解析，advancedData 走候选接口，string 下拉走候选；其它原样返回。"""
    if isinstance(current_value, str):
        current_value = current_value.strip()
    item = _get_field_item(key, field_items)
    field_name = _get_field_display_name(key, field_items=field_items)
    logger.info("【resolve_field_value】字段元数据 item: key=%s, name=%s", key, field_name)
    if not item:
        return current_value
    field_type = (item.get("type") or "").strip().lower()
    if field_type == "user":
        # 已是 USER 对象（来自智能填充等）：直接返回，避免 str(dict) 导致值被破坏
        if isinstance(current_value, dict):
            emp = current_value.get("employeeNo") or current_value.get("userId") or current_value.get("longId")
            if emp and str(emp).strip():
                return current_value
        val = current_value if isinstance(current_value, str) else str(current_value or "")
        resolved = resolve_person_to_emp_no(val.strip(), headers, log=logger)
        return resolved if (resolved or "").strip() else current_value
    if _is_advanced_data_remote_real_time(item):
        keyword = (current_value if isinstance(current_value, str) else str(current_value or "")).strip()
        body = _build_remote_real_time_body(key, item, work_item_type_key, keyword, workspace, event_id or "")
        try:
            data = rdcloud_api.fetch_remote_data_source_realtime(workspace, body, headers, field=item)
        except Exception as e:
            field_name = _get_field_display_name(key, field_items=field_items)
            logger.warning("remoteRealTime 查询失败 key=%s, name=%s: %s", key, field_name, e)
            return current_value
        bo = data.get("bo") if isinstance(data.get("bo"), list) else []
        if not bo:
            result = current_value
        else:
            nodes = [b for b in bo if isinstance(b, dict)]
            filtered = _filter_base_data_value_nodes_zx_xy(nodes)
            if filtered:
                chosen = None
                if len(filtered) == 1:
                    chosen = filtered[0]
                else:
                    for v in filtered:
                        if str(v).strip() == keyword:
                            chosen = v
                            break
                if chosen is not None:
                    full_node = _find_node_by_value(nodes, chosen)
                    return full_node if full_node is not None else chosen
                if len(filtered) > 1:
                    field_name = _get_field_display_name(key, field_items=field_items)
                    candidates_preview = [str(v) for v in filtered]
                    raise ValueError(f"多候选未唯一匹配: key={key}, name={field_name}, 候选值={candidates_preview}")
                result = current_value
            else:
                first = bo[0] if bo else {}
                if isinstance(first, dict):
                    return first
                result = current_value
        # 回退：去除所有空格后再调用接口查一次
        keyword_no_spaces = keyword.replace(" ", "")
        if result is current_value and keyword_no_spaces != keyword:
            body = _build_remote_real_time_body(key, item, work_item_type_key, keyword_no_spaces, workspace, event_id or "")
            try:
                data = rdcloud_api.fetch_remote_data_source_realtime(workspace, body, headers, field=item)
                bo = data.get("bo") if isinstance(data.get("bo"), list) else []
                if bo:
                    nodes = [b for b in bo if isinstance(b, dict)]
                    filtered = _filter_base_data_value_nodes_zx_xy(nodes)
                    if filtered:
                        chosen = None
                        if len(filtered) == 1:
                            chosen = filtered[0]
                        else:
                            for v in filtered:
                                if str(v).strip() == str(keyword_no_spaces).strip():
                                    chosen = v
                                    break
                        if chosen is not None:
                            full_node = _find_node_by_value(nodes, chosen)
                            return full_node if full_node is not None else chosen
                        if len(filtered) > 1:
                            field_name = _get_field_display_name(key, field_items=field_items)
                            candidates_preview = [str(v) for v in filtered]
                            raise ValueError(f"多候选未唯一匹配: key={key}, name={field_name}, 候选值={candidates_preview}")
                    first = bo[0] if bo else {}
                    if isinstance(first, dict):
                        return first
            except Exception:
                pass
        return result
    if _is_advanced_data_remote(item):
        keyword = (current_value if isinstance(current_value, str) else str(current_value or "")).strip()
        # logger.info(
        #     "【resolve_field_value】字段 key=%s value=%s 调用接口: fetch_fields_candidate (advancedData remote)",
        #     key,
        #     keyword[:80] + ("..." if len(str(keyword)) > 80 else ""),
        # )
        body = _build_advanced_data_candidate_body(item, work_item_type_key, keyword, workspace)
        body["workItemSessionId"] = event_id or ""
        data = rdcloud_api.fetch_fields_candidate(workspace, body, headers, field=item)
        bo = data.get("bo") or {}
        nodes = bo.get("baseDataValueNodeList") or bo.get("baseDataValueNodes") or []
        if isinstance(nodes, list) and nodes:
            filtered = _filter_base_data_value_nodes_zx_xy(nodes)
            if filtered:
                chosen = None
                if len(filtered) == 1:
                    chosen = filtered[0]
                else:
                    for v in filtered:
                        if str(v).strip() == str(keyword).strip():
                            chosen = v
                            break
                if chosen is not None:
                    full_node = _find_node_by_value(nodes, chosen)
                    return full_node if full_node is not None else chosen
                field_name = _get_field_display_name(key, field_items=field_items)
                candidates_preview = [str(v) for v in filtered]
                raise ValueError(f"多候选未唯一匹配: key={key}, name={field_name}, 候选值={candidates_preview}")
            vals = _extract_values_from_bo_items(nodes)
            if len(vals) == 1:
                full_node = _find_node_by_value(nodes, vals[0])
                return full_node if full_node is not None else vals[0]
            if len(vals) > 1:
                for v in vals:
                    if str(v).strip() == str(keyword).strip():
                        full_node = _find_node_by_value(nodes, v)
                        return full_node if full_node is not None else v
                field_name = _get_field_display_name(key, field_items=field_items)
                candidates_preview = [str(v) for v in vals]
                raise ValueError(f"多候选未唯一匹配: key={key}, name={field_name}, 候选值={candidates_preview}")
        # 回退：去除所有空格后再调用接口查一次
        keyword_no_spaces = keyword.replace(" ", "")
        if keyword_no_spaces != keyword:
            body = _build_advanced_data_candidate_body(item, work_item_type_key, keyword_no_spaces, workspace)
            body["workItemSessionId"] = event_id or ""
            data = rdcloud_api.fetch_fields_candidate(workspace, body, headers, field=item)
            bo = data.get("bo") or {}
            nodes = bo.get("baseDataValueNodeList") or bo.get("baseDataValueNodes") or []
            if isinstance(nodes, list) and nodes:
                filtered = _filter_base_data_value_nodes_zx_xy(nodes)
                if filtered:
                    chosen = None
                    if len(filtered) == 1:
                        chosen = filtered[0]
                    else:
                        for v in filtered:
                            if str(v).strip() == str(keyword_no_spaces).strip():
                                chosen = v
                                break
                    if chosen is not None:
                        full_node = _find_node_by_value(nodes, chosen)
                        return full_node if full_node is not None else chosen
                vals = _extract_values_from_bo_items(nodes)
                if len(vals) == 1:
                    full_node = _find_node_by_value(nodes, vals[0])
                    return full_node if full_node is not None else vals[0]
                if len(vals) > 1:
                    for v in vals:
                        if str(v).strip() == str(keyword_no_spaces).strip():
                            full_node = _find_node_by_value(nodes, v)
                            return full_node if full_node is not None else v
        return current_value
    if field_type == "string" and _is_string_with_pick_list_config(item):
        keyword = (current_value if isinstance(current_value, str) else str(current_value or "")).strip()
        body = _build_string_pick_list_candidate_body(item, work_item_type_key, keyword)
        body["workItemSessionId"] = event_id or ""
        data = rdcloud_api.fetch_fields_candidate(workspace, body, headers, field=item)
        bo = data.get("bo") or {}
        items = bo.get("textPickList") or bo.get("baseDataValueNodeList") or []
        vals = _extract_values_from_bo_items(items) if isinstance(items, list) else []
        if len(vals) == 1:
            return vals[0]
        if len(vals) > 1:
            for v in vals:
                if str(v).strip() == str(keyword).strip():
                    return v
            field_name = _get_field_display_name(key, field_items=field_items)
            candidates_preview = [str(v) for v in vals]
            raise ValueError(f"多候选未唯一匹配: key={key}, name={field_name}, 候选值={candidates_preview}")
        # len(vals) == 0，回退：去除所有空格后再调用接口查一次
        keyword_no_spaces = keyword.replace(" ", "")
        if keyword_no_spaces != keyword:
            body = _build_string_pick_list_candidate_body(item, work_item_type_key, keyword_no_spaces)
            body["workItemSessionId"] = event_id or ""
            data = rdcloud_api.fetch_fields_candidate(workspace, body, headers, field=item)
            bo = data.get("bo") or {}
            items = bo.get("textPickList") or bo.get("baseDataValueNodeList") or []
            vals = _extract_values_from_bo_items(items) if isinstance(items, list) else []
            if len(vals) == 1:
                return vals[0]
            if len(vals) > 1:
                for v in vals:
                    if str(v).strip() == str(keyword_no_spaces).strip():
                        return v
                field_name = _get_field_display_name(key, field_items=field_items)
                candidates_preview = [str(v) for v in vals]
                raise ValueError(f"多候选未唯一匹配: key={key}, name={field_name}, 候选值={candidates_preview}")
        return current_value
    return current_value


# ---------------------------------------------------------------------------
# PUT event 与描述模版
# ---------------------------------------------------------------------------

DESCRIPTION_FIELD_KEY_HTML = "System_Description_html"
DESCRIPTION_KEYS = frozenset({"System_Description", "System_Description_html"})
TITLE_FIELD_KEY = "System_Title"

# 当 fields 中含有以下 key 时，跳过 _put_event_after_field_resolved。可通过修改此变量配置
PUT_EVENT_SKIP_WHEN_KEYS = frozenset({"System_Description_html"})


def _truncate_required_fields_with_value_for_log(fields_list: List[dict], max_len: int = 80) -> List[dict]:
    """对 required_fields_with_value 列表做 value 截断，供日志打印使用。"""
    if not fields_list:
        return []
    out = []
    for r in fields_list:
        if not isinstance(r, dict):
            continue
        k = r.get("key")
        v = r.get("value")
        v_str = truncate_for_log(str(v) if v is not None else "", max_len)
        out.append({"key": k, "value": v_str})
    return out


def _is_description_template(value: Any) -> bool:
    """是否为富文本描述模版（含 html 标签）。"""
    if value is None:
        return False
    return "<" in str(value).strip() and ">" in str(value).strip()


def _get_description_or_title_content(fields: List[dict]) -> str:
    """从 fields 取描述或标题内容。"""
    for f in fields or []:
        if not isinstance(f, dict):
            continue
        k = (f.get("key") or "").strip()
        if k in DESCRIPTION_KEYS:
            v = f.get("value")
            if v is not None and str(v).strip():
                return str(v).strip()
    return _get_title_content_from_fields(fields)


def _get_title_content_from_fields(fields: List[dict]) -> str:
    """从 fields 取标题内容。"""
    for f in fields or []:
        if not isinstance(f, dict):
            continue
        if (f.get("key") or "").strip() == TITLE_FIELD_KEY:
            v = f.get("value")
            if v is not None:
                return str(v).strip()
    return ""


# 描述内容插入的候选占位，按顺序匹配，命中第一个则在其后插入
_DESCRIPTION_CONTENT_MARKERS = (
    "【问题描述】",
    "【*详细描述：】",
    "【详细描述】",
    "【*详细描述】",
)


def _fill_description_template(template: str, content: str) -> str:
    """将内容填入描述模版：在候选占位（如【问题描述】、【*详细描述：】等）后插入；否则拼到模版前。"""
    logger.info("_fill_description_template: template=%s, content=%s", template or "", content or "")
    if not (template or "").strip():
        return (content or "").strip()
    content = (content or "").strip()
    for marker in _DESCRIPTION_CONTENT_MARKERS:
        if marker in template:
            idx = template.find(marker)
            tail = template[idx + len(marker) :].lstrip()
            if tail.startswith("\n") or tail.startswith("<br"):
                return template[: idx + len(marker)] + "\n\n<br/>" + content + "\n" + tail
            return template[: idx + len(marker)] + "\n\n<br/>" + content + (("\n" + tail) if tail else "")
    return content + "\n\n<br/>" + template


def log_description_template_and_field_value(
    description_template: Any,
    fields: List[dict],
    scene: str = "创建",
    max_len: int = 200,
) -> None:
    """打印描述模版与描述字段的值，供 xydt 与默认创建工作项统一使用。"""
    desc_field = next(
        (f for f in (fields or []) if isinstance(f, dict) and (f.get("key") or "").strip() in DESCRIPTION_KEYS),
        None,
    )
    desc_value = desc_field.get("value") if desc_field else None
    # logger.info(
    #     "【%s】description_template: %s, 描述字段值: %s",
    #     scene,
    #     truncate_for_log(str(description_template), max_len),
    #     truncate_for_log(str(desc_value), max_len),
    # )


def _normalize_placeholder_key(s: str) -> str:
    """【】中只去掉前后 * : ： 空格等字符，中间不去掉，用于占位与 key 的匹配。"""
    if not s:
        return ""
    s = str(s).strip()
    chars = " *:：\t\n\r"
    s = s.lstrip(chars)
    s = s.rstrip(chars)
    return s.strip()


def apply_description_template_to_fields(
    fields: List[dict],
    template: Any,
    scene: str = "创建",
    removed_field_names: Optional[List[dict]] = None,
) -> bool:
    """若有描述模版，则用描述/标题内容填空并写回 fields。返回是否真实执行了模版应用。
    removed_field_names: 未解析到的字段列表 [{"key": str, "value": Any}, ...]，若模版中【key】匹配到则使用其 value 并移除。
    占位支持【key】及【*key：】等格式，【】内会去掉前后 * : ： 空格后与 key 匹配。
    """
    #logger.info("%s 开始应用描述模版: template=%s", scene, truncate_for_log(str(template), 120))
    if not _is_description_template(template):
        logger.info("%s 描述模版校验未通过，跳过应用: template=%s", scene, truncate_for_log(str(template), 120))
        return False
    template_str = str(template).strip()
    # 模版中【xxx】若匹配 removed_field_names 的 key，则在占位后换行插入 value，保留占位不破坏其他占位
    # 支持【*详细描述：】【修改建议：】等格式，【】内去掉 * : ： 空格后与 key 匹配
    if removed_field_names:
        placed_indices = set()
        for m in re.finditer(r"【([^】]*)】", template_str):
            content = m.group(1)
            normalized = _normalize_placeholder_key(content)
            if not normalized:
                continue
            full_placeholder = m.group(0)
            for i, item in enumerate(removed_field_names):
                if i in placed_indices:
                    continue
                if not isinstance(item, dict):
                    continue
                k = (item.get("key") or "").strip()
                if not k:
                    continue
                if normalized == k:
                    v = item.get("value")
                    val_str = str(v).strip() if v is not None else ""
                    template_str = template_str.replace(full_placeholder, full_placeholder + "\n\n<br/>" + val_str, 1)
                    placed_indices.add(i)
                    logger.info("%s 模版占位【%s】后插入已移除字段的值并移除", scene, k)
                    break
        removed_field_names[:] = [item for i, item in enumerate(removed_field_names) if i not in placed_indices]
    content = _get_description_or_title_content(fields)
    filled = _fill_description_template(template_str, content)
    for f in fields or []:
        if not isinstance(f, dict):
            continue
        if (f.get("key") or "").strip() == DESCRIPTION_FIELD_KEY_HTML:
            f["value"] = filled
            logger.info("%s 已应用描述模版到 %s", scene, DESCRIPTION_FIELD_KEY_HTML)
            return True
    for f in fields or []:
        if not isinstance(f, dict):
            continue
        if (f.get("key") or "").strip() == "System_Description":
            f["value"] = filled
            logger.info("%s 已应用描述模版到 System_Description", scene)
            return True
    fields.append({"key": DESCRIPTION_FIELD_KEY_HTML, "value": filled})
    logger.info("%s 无描述字段，已追加 %s", scene, DESCRIPTION_FIELD_KEY_HTML)
    return True


def _put_event_after_field_resolved(
    workspace: str,
    event_id: str,
    work_item_type_key: str,
    fields: List[dict],
    field_items: List[dict],
    virtual_id: str,
    headers: Dict[str, Any],
    get_field_type_fn,
) -> tuple:
    """在字段 value 解析完成后执行一次 PUT event，从响应中收集新必填与描述模版。"""
    event_fields = []
    for f in fields or []:
        if not isinstance(f, dict):
            continue
        k = (f.get("key") or "").strip()
        if not k:
            continue
        type_ = get_field_type_fn(k, field_items) if field_items else "string"
        event_fields.append({"key": k, "value": f.get("value"), "type": type_ or "string"})
    body = {
        "workItemTypeKey": (work_item_type_key or "").strip(),
        "fields": event_fields,
        "virtualId": virtual_id or "",
    }
    #logger.info("_put_event_after_field_resolved 请求体 body=%s", json.dumps(body, ensure_ascii=False, default=str))
    try:
        resp = rdcloud_api.put_work_item_event(workspace, event_id, body, headers)
    except Exception as e:
        logger.warning("PUT event 失败，无法收集新必填与描述模版: %s", e)
        return [], None, []
    # 从响应中收集新必填字段与描述模版
    # new_required_fields_from_event 是新的必填字段，且没有值
    new_required_fields_from_event = []
    required_fields_with_value = []  # 必填且有值的字段
    bo = resp.get("bo") or {}
    if isinstance(bo, dict):
        flist = bo.get("fields") or bo.get("fieldList") or []
        if isinstance(flist, list):
            for ff in flist:
                if not isinstance(ff, dict):
                    continue
                key = (ff.get("key") or "").strip()
                required = ff.get("required") is True or ff.get("isRequired") is True
                val = ff.get("value")
                empty = val is None or (isinstance(val, str) and (val or "").strip() == "")
                if key and required and empty:
                    new_required_fields_from_event.append(key)
                elif key and required and not empty:
                    required_fields_with_value.append({"key": key, "value": val})
    description_template = None
    if isinstance(bo, dict):
        for f in (bo.get("fields") or bo.get("fieldList") or []):
            if not isinstance(f, dict):
                continue
            if (f.get("key") or "").strip() == DESCRIPTION_FIELD_KEY_HTML:
                v = f.get("value")
                v_str = str(v) if v is not None else ""
                v_log = v_str[:500] + ("..." if len(v_str) > 500 else "")
                if _is_description_template(v):
                    description_template = v
                    # logger.info(
                    #     "_put_event_after_field_resolved 从响应取得描述模版 key=%s, value 长度=%s, value=%s",
                    #     DESCRIPTION_FIELD_KEY_HTML,
                    #     len(v_str),
                    #     v_log,
                    # )
                else:
                    # logger.info(
                    #     "_put_event_after_field_resolved 响应中含 %s 但非模版，未采用 value=%s",
                    #     DESCRIPTION_FIELD_KEY_HTML,
                    #     v_log,
                    # )
                    pass
                break
        # if description_template is None:
        #     logger.info("_put_event_after_field_resolved 响应中未取得描述模版")
    return new_required_fields_from_event, description_template, required_fields_with_value

# new_required_fields_from_event_collected 是经过 event 事件后，新的必填字段，且没有值【不在fields中】
# required_fields_with_value_collected 是经过 event 事件后，必填且有值的字段，供后续使用【不在fields中】
# 解析大模型给出的字段value-> 执行event事件-> 收集新的必填无值字段-> 收集必填且有值的字段-> 返回
def resolve_fields_values(
    fields: List[dict],
    workspace: str,
    work_item_type_key: str,
    field_items: Optional[List[dict]] = None,
    headers: Optional[Dict[str, Any]] = None,
    scene: str = "",
    event_id: Optional[str] = None,
    virtual_id: Optional[str] = None,
    keys_to_resolve: Optional[List[str]] = None,
) -> tuple:
    """
    遍历 fields，逐项解析 value；每解析完一个字段后执行一次 PUT event（若有 event_id），
    以便依赖前序字段的字段能拿到最新候选。收集各次 PUT 的 new_required_fields_from_event、description_template、required_fields_with_value，最后一并返回。
    :param keys_to_resolve: 若指定，仅解析这些 key 的字段（兜底场景下只解析从智能填充获取的字段）；None 表示解析全部。
    :return: (new_required_fields_from_event: List[str], description_template: Any, required_fields_with_value: List[dict])
        required_fields_with_value: 必填且有值的字段 [{"key": str, "value": Any}, ...]，供后续使用
    """
    headers = headers or {}
    field_items = field_items or fetch_fields(workspace, headers)
    key_to_field = {(f.get("key") or "").strip(): f for f in (fields or []) if isinstance(f, dict) and (f.get("key") or "").strip()}
    new_required_fields_from_event_collected = []
    required_fields_with_value_collected = []  # 必填且有值的字段，供后续使用
    description_template = None
    eid = (event_id or "").strip()
    vid = (virtual_id or "").strip()
    # 如果指定了keys_to_resolve，则仅执行这些key的字段解析
    only_keys = set((k or "").strip() for k in (keys_to_resolve or [])) if keys_to_resolve else None
    #field_count = sum(1 for f in (fields or []) if isinstance(f, dict) and (f.get("key") or "").strip())
    # 获取所有字段key和name
    #field_keys = list(key_to_field.keys())
    #field_names = [_get_field_display_name(k, field_items=field_items) for k in field_keys]
    _scene = scene or "resolve_fields_values"
    # logger.info(
    #     "【%s】 开始: workspace=%s, work_item_type_key=%s, 字段数=%s, 字段key=%s, 字段name=%s, has_event_id=%s, keys_to_resolve=%s",
    #     _scene,
    #     workspace,
    #     work_item_type_key,
    #     field_count,
    #     field_keys,
    #     field_names,
    #     bool(eid),
    #     list(only_keys) if only_keys else None,
    # )
    # BelongProduct、BelongProject 优先解析
    fields_to_iterate = _reorder_fields_with_priority(fields or [])
    for f in fields_to_iterate:
        if not isinstance(f, dict):
            continue
        key = (f.get("key") or "").strip()
        if not key:
            continue
        if only_keys is not None and key not in only_keys:
            # 非智能填充的字段会被跳过，比如大模型识别到标题，然后智能填充中没有标题，则跳过
            continue
        current = f.get("value")
        field_name = _get_field_display_name(key, field_items=field_items)
        logger.info("【%s】 正在解析字段 key=%s, name=%s, value=%s", _scene, key, field_name, str(current)[:80] + ("..." if len(str(current)) > 80 else ""))
        try:
            resolved = resolve_field_value(
                key,
                current,
                workspace,
                work_item_type_key,
                field_items,
                headers,
                scene=scene,
                event_id=eid or None,
            )
            f["value"] = resolved
            logger.info(
                "【%s】 已解析字段 key=%s, name=%s, value=%s",
                _scene,
                key,
                field_name,
                str(resolved)[:80] + ("..." if len(str(resolved)) > 80 else ""),
            )
        except Exception as e:
            logger.info("【%s】 单字段解析跳过 key=%s, name=%s: %s", _scene, key, field_name, e)
        if eid:
            # 描述字段不需要走event事件
            if key in PUT_EVENT_SKIP_WHEN_KEYS:
                new_required_fields_from_event, template_from_put, required_fields_with_value = [], None, []
                logger.info("【%s】 跳过 PUT event（fields 含 skip key: %s）", _scene, key)
            else:
                # new_required_fields_from_event 是经过 event 事件后，新的必填字段，且没有值
                # required_fields_with_value 是经过 event 事件后，必填且有值的字段
                new_required_fields_from_event, template_from_put, required_fields_with_value = _put_event_after_field_resolved(
                    workspace,
                    eid,
                    work_item_type_key,
                    [f],
                    field_items,
                    vid,
                    headers,
                    _get_field_type,
                )
            fields_keys = {(f.get("key") or "").strip() for f in (fields or []) if isinstance(f, dict) and (f.get("key") or "").strip()}
            for k in new_required_fields_from_event:
                k_stripped = (k or "").strip()
                if k_stripped and k_stripped not in {r.strip() for r in new_required_fields_from_event_collected} and k_stripped not in fields_keys:
                    new_required_fields_from_event_collected.append(k_stripped)
            
            if required_fields_with_value:
                # 合并必填且有值的字段，同 key 以最后一次为准
                key_to_val = {r["key"]: r["value"] for r in required_fields_with_value_collected}
                for r in required_fields_with_value:
                    key_to_val[r["key"]] = r["value"]
                required_fields_with_value_collected = [{"key": k, "value": v} for k, v in key_to_val.items()]
            # 目前只支持描述字段模版
            if template_from_put is not None and template_from_put != "" and description_template is None:
                description_template = template_from_put
            logger.info(
                "【%s】 本次 PUT 完成 key=%s, name=%s, 本此收集到的新的必填无值字段数=%s, 共收集新的必填无值字段数=%s, 已收集 key=%s, 有模版=%s, 本次有值字段=%s, 已收集有值字段=%s",
                _scene,
                key,
                field_name,
                new_required_fields_from_event,
                len(new_required_fields_from_event_collected),
                new_required_fields_from_event_collected,
                template_from_put is not None and template_from_put != "",
                _truncate_required_fields_with_value_for_log(required_fields_with_value or []),
                _truncate_required_fields_with_value_for_log(required_fields_with_value_collected),
            )
    #logger.info("*" * 100)
    new_required_names = [_get_field_display_name(k, field_items=field_items) for k in new_required_fields_from_event_collected] if new_required_fields_from_event_collected else []
    #logger.info(new_required_names)
    logger.info(
        "【%s】 完成: 一共收集到新的必填无值字段数=%d, key=%s, name=%s, 有模版=%s, 已收集有值字段=%s",
        _scene,
        len(new_required_fields_from_event_collected),
        new_required_fields_from_event_collected,
        new_required_names,
        description_template is not None,
        _truncate_required_fields_with_value_for_log(required_fields_with_value_collected),
    )
    #logger.info(description_template)
    # new_required_fields_from_event 是经过 event 事件后标记为必填且当前空值的字段；
    # 若在 fields 中已经存在且 value 非空（例如后续流程已补值），则不再视为新必填，需从列表中移除。
    if new_required_fields_from_event_collected:
        key_to_field = {(f.get("key") or "").strip(): f for f in (fields or []) if isinstance(f, dict) and (f.get("key") or "").strip()}

        def _is_empty_for_new_required(v):
            if v is None:
                return True
            if isinstance(v, str) and (v or "").strip() == "":
                return True
            if isinstance(v, list) and len(v) == 0:
                return True
            return False

        filtered = []
        for k in new_required_fields_from_event_collected:
            f = key_to_field.get((k or "").strip())
            v = f.get("value") if f and isinstance(f, dict) else None
            if f is not None and not _is_empty_for_new_required(v):
                #k_name = _get_field_display_name(k, field_items=field_items)
                # logger.info(
                #     "【%s】 过滤已补值的新必填字段 key=%s, name=%s, current_value=%s",
                #     _scene,
                #     k,
                #     k_name,
                #     str(v)[:80] + ("..." if len(str(v)) > 80 else ""),
                # )
                continue
            filtered.append(k)
        # 只保留值为空的字段    
        new_required_fields_from_event_collected = filtered
        #filtered_names = [_get_field_display_name(k, field_items=field_items) for k in filtered]
        #logger.info("【%s】 通过event事件获取到的新的必填字段: key=%s, name=%s", _scene, filtered, filtered_names)

    # 若 fields 中已存在 key，则从必填列表中移除；对 required_fields_with_value_collected 还需将 fields 中的值替换为 event 返回的新值
    key_to_field = {(f.get("key") or "").strip(): f for f in (fields or []) if isinstance(f, dict) and (f.get("key") or "").strip()}
    new_required_filtered = [k for k in new_required_fields_from_event_collected if key_to_field.get((k or "").strip()) is None]
    if len(new_required_filtered) < len(new_required_fields_from_event_collected):
        #logger.info("【%s】 fields 中已存在以下 key，从 new_required 中移除: %s", _scene, sorted(set(new_required_fields_from_event_collected) - set(new_required_filtered)))
        pass
    new_required_fields_from_event_collected = new_required_filtered

    required_with_value_filtered = []
    for r in required_fields_with_value_collected:
        k = (r.get("key") or "").strip()
        if k in DESCRIPTION_KEYS:
            continue
        f = key_to_field.get(k)
        if f is not None:
            # 这个地方可能存在问题，将event事件的字段值强制覆盖fields中的值。
            f["value"] = r["value"]
            logger.info("【%s】 fields 中已存在 key=%s，已替换为 event 返回的新值", _scene, k)
        else:
            required_with_value_filtered.append(r)
    if len(required_with_value_filtered) < len(required_fields_with_value_collected):
        removed_keys = {r["key"] for r in required_fields_with_value_collected} - {r["key"] for r in required_with_value_filtered}
        #logger.info("【%s】 fields 中已存在以下 key，从 required_fields_with_value 中移除并已替换值: %s", _scene, sorted(removed_keys))
    required_fields_with_value_collected = required_with_value_filtered

    # new_required_fields_from_event 是经过 event 事件后，仍然没有值的必填字段 key 列表
    return (new_required_fields_from_event_collected, description_template, required_fields_with_value_collected)
