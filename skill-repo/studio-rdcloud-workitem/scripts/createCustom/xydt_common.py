# -*- coding: utf-8 -*-
"""
xydt 组织下通用工作项创建逻辑。
create_work_item 从 _create_work_item_default 完整复制实现。
"""

import sys
import json
import requests
from pathlib import Path

_SCRIPTS_ROOT = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_ROOT))

from common.log_helper import get_log_config, truncate_for_log, get_logger
from common.field_resolver import fetch_fields, resolve_field_key
from common.field_value_resolver import resolve_fields_values, apply_description_template_to_fields, _is_description_template
from common.field_value_resolver import _get_field_display_name
from common.work_item_type_resolver import resolve_valid_work_item_type_key_from_root
from common.mindset_messages import get_mindset_message

from createCustom.xydt_shared import apply_smart_fill_supplement_fields
from atomic.create_work_item import (
    _step_create_init,
    _ensure_required_fields_filled,
    _fill_required_from_smart_candidates,
    _create_work_item_post_with_fallback,
    _step_destroy_event,
    _title_fallback_from_description,
    _reorder_fields_description_after_product,
    CONFIG_FILE,
    WORK_ITEM_TYPE_KEY_BUG,
    WORKGROUP_FIELD_KEYS,
    REQUIRED_FIELD_KEYS_EXCLUDED,
    TITLE_FIELD_KEY,
    TITLE_FALLBACK_MAX_CHARS,
    DISCOVERY_VERSION_FIELD_KEY,
)

logger = get_logger(__name__)

DESCRIPTION_KEYS = frozenset({"System_Description", "System_Description_html"})


def _get_template_from_init_if_empty(template, init_list):
    """若模版未获取到，但 required_field_keys_with_value_init 中有描述字段且值为模版格式，则取其值。"""
    if template and _is_description_template(template):
        return template
    for r in (init_list or []):
        if not isinstance(r, dict):
            continue
        k = (r.get("key") or "").strip()
        if k not in DESCRIPTION_KEYS:
            continue
        v = r.get("value")
        if v is not None and _is_description_template(v):
            return v
    return template


def create_work_item(work_item_type_key, fields, base_config, headers, field_items=None, smart_complete_candidates=None):
    """
    xydt 通用工作项创建。按总分总：准备（解析 key、校验、解析 value）→ 初始化 → PUT event → 执行创建 → 销毁 event。
    field_items 可选；smart_complete_candidates 为可填充候选（定制脚本可先合并再调此方法）。
    """
    logger.info("=" * 100)
    logger.info(".............开始执行 xydt 通用工作项创建逻辑....................")
    logger.info("=" * 100)
    workspace = base_config.get("workspace")
    if not workspace:
        raise ValueError("缺少必需参数: workspace (必须在配置文件的 api.workspace 中提供)")

    # ---------- 准备：解析类型、字段 key、校验、解析 value ----------
    resolved_type_key = resolve_valid_work_item_type_key_from_root(work_item_type_key, workspace, headers)
    logger.info("【xydt 通用工作项-解析工作项模型key】: original_type=%s, resolved_work_item_type_key=%s, workspace=%s", work_item_type_key, resolved_type_key, workspace)

    if field_items is None:
        field_items = fetch_fields(workspace, headers)
    resolved_fields = []
    removed_field_names = []
    for f in fields:
        if not isinstance(f, dict) or f.get("key") is None:
            continue
        raw_key = (f.get("key") or "").strip()
        if not raw_key:
            continue
        field_key = resolve_field_key(
            raw_key,
            field_items,
            headers,
            raise_on_not_found=False,
            scene="xydt 通用工作项-字段映射-中文key->英文key",
            workspace=workspace,
        )
        if not field_key:
            logger.warning("【xydt 通用工作项字段映射】字段名「%s」未解析到 key，已移除此字段。", raw_key)
            removed_field_names.append({"key": raw_key, "value": f.get("value")})
            continue
        f["key"] = field_key
        resolved_fields.append(f)
    fields = resolved_fields
    title_fallback = _title_fallback_from_description(fields, field_items, max_chars=TITLE_FALLBACK_MAX_CHARS)
    if title_fallback is not None:
        fields.append({"key": TITLE_FIELD_KEY, "value": title_fallback})
        logger.info("【xydt 通用工作项字段映射】无标题字段，已从描述/含描述字段取前%d字作为标题: %s", TITLE_FALLBACK_MAX_CHARS, truncate_for_log(title_fallback, 60))
    _reorder_fields_description_after_product(fields, field_items)

    logger.info("=" * 100)
    logger.info(".............通过大模型识别到用户输入的字段中文key->英文key，以及value，也包括未被采纳的字段....................")
    logger.info("=" * 100)
    for f in fields:
        if isinstance(f, dict):
            k = (f.get("key") or "").strip()
            v = f.get("value")
            v_str = truncate_for_log(json.dumps(v, ensure_ascii=False) if not isinstance(v, (str, int, float, bool)) and v is not None else str(v), 120)
            k_name = _get_field_display_name(k, field_items=field_items)
            logger.info("【xydt 通用工作项-将中文key-英文】fields 项: key=%s, name=%s, value=%s", k, k_name, v_str)
    if removed_field_names:
        logger.info("【xydt 通用工作项-已移除字段】removed_field_names 共 %d 个:", len(removed_field_names))
        for r in removed_field_names:
            r_key = r.get("key", r) if isinstance(r, dict) else r
            r_val = r.get("value") if isinstance(r, dict) else None
            r_val_str = truncate_for_log(json.dumps(r_val, ensure_ascii=False) if not isinstance(r_val, (str, int, float, bool)) and r_val is not None else str(r_val), 80)
            logger.info("【xydt 通用工作项-已移除字段】removed: key=%s, value=%s", r_key, r_val_str)
    logger.info("=" * 100)
    logger.info("=" * 100)

    # 定制：特殊处理，缺陷和变更单容易被错误识别
    field_keys_present = {(f.get("key") or "").strip() for f in fields if isinstance(f, dict) and (f.get("key") or "").strip()}
    if (
        (resolved_type_key or "").strip() == WORK_ITEM_TYPE_KEY_BUG
        and all(k in field_keys_present for k in WORKGROUP_FIELD_KEYS)
    ):
        mindset_text = get_mindset_message(
            "create_type_mismatch",
            work_item_type_key_bug=WORK_ITEM_TYPE_KEY_BUG,
            workgroup_field_keys=WORKGROUP_FIELD_KEYS,
        )
        err_msg = "创建类型识别错误，请确认。"
        if mindset_text:
            err_msg = err_msg + "\n\n" + mindset_text
        raise ValueError(err_msg)

    event_id, virtual_id, required_field_keys_with_value_init, required_field_keys, init_fields = _step_create_init(workspace, resolved_type_key, headers)
    excluded_set = {str(k).strip() for k in REQUIRED_FIELD_KEYS_EXCLUDED if (k or "").strip()}
    excluded_from_required = sorted(k for k in required_field_keys if (k or "").strip() in excluded_set)
    required_field_keys = [k for k in required_field_keys if (k or "").strip() not in excluded_set]
    required_field_keys_with_value_init = [r for r in required_field_keys_with_value_init if (r.get("key") or "").strip() not in excluded_set]
    logger.info("【xydt 通用工作项-通过初始化获取必填字段】被采纳必填字段-无值（已排除系统字段）: %s", required_field_keys)
    logger.info("【xydt 通用工作项-通过初始化获取必填字段】必填且有值（初始化）: %s", [r["key"] for r in required_field_keys_with_value_init])
    logger.info("【xydt 通用工作项-通过初始化获取必填字段】已排除的系统字段（排序）: %s", excluded_from_required)

    logger.info("【xydt 通用工作项】开始解析大模型识别的字段的value")
    new_required_fields_from_event, description_template, required_fields_with_value = resolve_fields_values(
        fields,
        workspace,
        resolved_type_key,
        field_items=field_items,
        headers=headers,
        scene="xydt 通用工作项-解析大模型识别字段的value",
        event_id=event_id,
        virtual_id=virtual_id,
    )
    template_to_apply = _get_template_from_init_if_empty(description_template, required_field_keys_with_value_init)
    description_template_applied = apply_description_template_to_fields(
        fields, template_to_apply, scene="xydt 通用工作项-应用描述模版", removed_field_names=removed_field_names
    )
    logger.info("【xydt 通用工作项-解析大模型识别字段的value】解析后 fields 共 %d 个:", len(fields))
    for f in fields:
        if isinstance(f, dict):
            k = (f.get("key") or "").strip()
            v = f.get("value")
            v_str = truncate_for_log(
                json.dumps(v, ensure_ascii=False) if not isinstance(v, (str, int, float, bool)) and v is not None else str(v),
                120,
            )
            k_name = _get_field_display_name(k, field_items=field_items)
            logger.info("  field: key=%s, name=%s, value=%s", k, k_name, v_str)

    key_to_val = {r["key"]: r["value"] for r in required_field_keys_with_value_init}
    for r in required_fields_with_value:
        key_to_val[r["key"]] = r["value"]
    required_fields_with_value = [{"key": k, "value": v} for k, v in key_to_val.items() if (k or "").strip() not in excluded_set]
    if new_required_fields_from_event:
        new_required_filtered = [k for k in new_required_fields_from_event if (k or "").strip() not in excluded_set]
        init_key_to_val = {r["key"]: r["value"] for r in required_field_keys_with_value_init}
        key_to_field = {(f.get("key") or "").strip(): f for f in fields if isinstance(f, dict) and (f.get("key") or "").strip()}
        new_required_to_merge = []
        for k in new_required_filtered:
            if k in init_key_to_val:
                f = key_to_field.get(k)
                if f is not None:
                    f["value"] = init_key_to_val[k]
                else:
                    fields.append({"key": k, "value": init_key_to_val[k]})
                    key_to_field[k] = fields[-1]
            else:
                new_required_to_merge.append(k)
        if new_required_to_merge:
            required_field_keys = list(set(required_field_keys) | set(new_required_to_merge))

    fields_keys = {(f.get("key") or "").strip() for f in fields if isinstance(f, dict) and (f.get("key") or "").strip()}
    required_field_keys = [k for k in required_field_keys if (k or "").strip() not in fields_keys]

    logger.info("【xydt 通用工作项-综合初始化、解析大模型识别的字段value后event事件收集到无值必填字段】必填字段 共 %d 个: %s", len(required_field_keys), sorted(required_field_keys))
    logger.info("【xydt 通用工作项-综合初始化、解析大模型识别的字段value后event事件收集到有值必填字段】必填字段 共 %d 个: %s", len(required_fields_with_value), sorted(r["key"] for r in required_fields_with_value))

    fields_keys = {(f.get("key") or "").strip() for f in fields if isinstance(f, dict) and (f.get("key") or "").strip()}
    for r in required_fields_with_value:
        k = (r.get("key") or "").strip()
        if k and k not in fields_keys:
            fields.append({"key": k, "value": r["value"]})
            fields_keys.add(k)

    logger.info("【xydt 通用工作项-[完整]-最终合并初始化、解析大模型识别的字段value以及event事件后收集到有值必填字段】 fields 共 %d 个:", len(fields))
    for f in fields:
        if isinstance(f, dict):
            k = (f.get("key") or "").strip()
            v = f.get("value")
            v_str = truncate_for_log(
                json.dumps(v, ensure_ascii=False) if not isinstance(v, (str, int, float, bool)) and v is not None else str(v),
                120,
            )
            k_name = _get_field_display_name(k, field_items=field_items)
            logger.info("  field: key=%s, name=%s, value=%s", k, k_name, v_str)
    logger.info("【xydt 通用工作项-解析字段的value】解析字段的value完成，下一步执行必填字段补全")
    logger.info("=" * 100)
    logger.info("=" * 100)
    required_fill_issues = []
    logger.info("---------- 智能填充获取的候选字段（从历史创建中获取）----------")
    smart_filled_keys = _fill_required_from_smart_candidates(
        fields,
        required_field_keys,
        smart_complete_candidates or {},
        field_items,
    )
    if smart_filled_keys:
        apply_smart_fill_supplement_fields(
            fields, smart_filled_keys, smart_complete_candidates or {}, workspace, resolved_type_key
        )
        logger.info("=" * 100)
        logger.info("---------- 解析智能填充获取的候选字段（从历史创建中获取）----------")
        logger.info("=" * 100)
        new_fields_required_from_smart, template_from_smart, new_fields_required_value_from_smart = resolve_fields_values(
            fields,
            workspace,
            resolved_type_key,
            field_items=field_items,
            headers=headers,
            scene="xydt 通用工作项【智能填充的 字段value解析】",
            event_id=event_id,
            virtual_id=virtual_id,
            keys_to_resolve=smart_filled_keys,
        )
        logger.info("=" * 100)
        logger.info("---------- 智能填充获取的候选字段（从历史创建中获取），列出候选值----------")
        logger.info("=" * 100)
        if new_fields_required_from_smart:
            new_smart_filled_keys = _fill_required_from_smart_candidates(
                fields,
                new_fields_required_from_smart,
                smart_complete_candidates or {},
                field_items,
                is_retry_from_history=True,
            )
            new_required_filtered = [k for k in new_fields_required_from_smart if (k or "").strip() not in excluded_set]
            new_required_filtered = [k for k in new_required_filtered if k not in new_smart_filled_keys or k == DISCOVERY_VERSION_FIELD_KEY]
            if new_required_filtered:
                required_field_keys = new_required_filtered
                logger.info("智能兜底 resolve 后从 event 收集到的新必填字段，仅本次必填列表: %s", new_required_filtered)
                fill_issues_smart = _ensure_required_fields_filled(
                    fields,
                    new_required_filtered,
                    workspace,
                    resolved_type_key,
                    field_items,
                    headers,
                    event_id,
                    False
                )
                required_fill_issues = required_fill_issues + fill_issues_smart
        template_to_apply = _get_template_from_init_if_empty(template_from_smart, required_field_keys_with_value_init)
        if template_to_apply and _is_description_template(template_to_apply) and not description_template_applied:
            apply_description_template_to_fields(
                fields, template_to_apply, scene="xydt 通用工作项", removed_field_names=removed_field_names
            )
    else:
        logger.info("【xydt 通用工作项】智能填充未命中，直接执行必填字段补全，必填字段: %s", sorted(required_field_keys))
        required_fill_issues = _ensure_required_fields_filled(
            fields,
            required_field_keys,
            workspace,
            resolved_type_key,
            field_items,
            headers,
            event_id,
        )

    if required_fill_issues:
        _step_destroy_event(workspace, event_id, headers)
        error_msg = "必填字段的值未提供，无法继续创建，请根据后续值进行补充。"
        if removed_field_names:
            mindset_text = get_mindset_message("create_fields_not_found", removed_field_names=removed_field_names)
            if mindset_text:
                error_msg = error_msg + "\n\n" + mindset_text
        error_msg = error_msg + "\n\n必填字段的候选值（必须完整展示给用户进行参考）：" + "；".join(
            f"「{x.get('field_name', x.get('field_key', ''))}」（{x.get('field_key', '')}）{x.get('message', '')}" for x in required_fill_issues
        )
        extra_mindset = get_mindset_message("create_error")
        if extra_mindset:
            error_msg = error_msg + "\n\n" + extra_mindset
        raise Exception(error_msg)

    try:
        result, removed, created_as_draft = _create_work_item_post_with_fallback(
            workspace, resolved_type_key, fields, base_config, headers, removed_field_names, event_id
        )
        code_obj = (result.get("code") or {}) if isinstance(result, dict) else {}
        if isinstance(code_obj, dict) and code_obj.get("code") == "0004":
            base_msg = (code_obj.get("msg") or code_obj.get("message") or "").strip()
            error_msg = "创建 xydt 通用工作项失败（服务返回错误）"
            if base_msg:
                error_msg = error_msg + f": {base_msg}"
            if removed_field_names:
                mindset_text = get_mindset_message("create_fields_not_found", removed_field_names=removed_field_names)
                if mindset_text:
                    error_msg = error_msg + "\n\n" + mindset_text
            if required_fill_issues:
                error_msg = error_msg + "\n\n必填字段的候选值（必须完整展示给用户进行参考）：" + "；".join(
                    f"「{x.get('field_name', x.get('field_key', ''))}」（{x.get('field_key', '')}）{x.get('message', '')}" for x in required_fill_issues
                )
            extra_mindset = get_mindset_message("create_error")
            if extra_mindset:
                error_msg = error_msg + "\n\n" + extra_mindset
            raise Exception(error_msg)
        logger.info("=" * 50)
        logger.info(".............完成 xydt 通用工作项创建逻辑....................")
        logger.info("=" * 50)
        return (result, removed, created_as_draft)
    except requests.exceptions.RequestException as e:
        error_msg = f"API 请求失败: {str(e)}"
        if removed_field_names:
            mindset_text = get_mindset_message("create_fields_not_found", removed_field_names=removed_field_names)
            if mindset_text:
                error_msg = error_msg + "\n\n" + mindset_text
        if required_fill_issues:
            error_msg = error_msg + "\n\n必填字段补全时未采用（多值或异常，可作参考）：" + "；".join(
                f"「{x.get('field_name', x.get('field_key', ''))}」（{x.get('field_key', '')}）{x.get('message', '')}" for x in required_fill_issues
            )
        extra_mindset = get_mindset_message("create_error")
        if extra_mindset:
            error_msg = error_msg + "\n\n" + extra_mindset
        if hasattr(e, "response") and e.response is not None:
            _m = get_log_config(CONFIG_FILE).get("response_body_max_length", 500)
            try:
                error_detail = e.response.json()
                _t = json.dumps(error_detail, ensure_ascii=False, indent=2)
                logger.error("错误响应 body=%s", truncate_for_log(_t, _m))
            except Exception:
                logger.error("错误响应 body=%s", truncate_for_log(e.response.text or "", _m))
        raise Exception(error_msg)
    finally:
        _step_destroy_event(workspace, event_id, headers)
