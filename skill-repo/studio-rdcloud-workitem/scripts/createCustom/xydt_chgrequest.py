# -*- coding: utf-8 -*-
"""
xydt 变更请求（chgRequest）专用定制创建逻辑。
方法名为 create_work_item，参数与 _create_work_item_default 一致。
"""

import sys
import json
import requests
from pathlib import Path
from difflib import SequenceMatcher

_SCRIPTS_ROOT = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_ROOT))

from common.log_helper import get_log_config, truncate_for_log, get_logger
from common.field_resolver import fetch_fields, resolve_field_key
from common.field_value_resolver import resolve_fields_values, resolve_field_value, _get_field_display_name, apply_description_template_to_fields, log_description_template_and_field_value
from common.work_item_type_resolver import resolve_valid_work_item_type_key_from_root
from common.mindset_messages import get_mindset_message
from common import rdcloud_api

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
)

logger = get_logger(__name__)

# 定制：与「iChange发现版本号」相似度超过此阈值且 key 含「版本」时，强制映射为 iChangeDiscoveryVersion
CHGREQUEST_DISCOVERY_VERSION_LABEL = "iChange发现版本号"
CHGREQUEST_DISCOVERY_VERSION_KEY = "iChangeDiscoveryVersion"
CHGREQUEST_DISCOVERY_VERSION_SIMILARITY_THRESHOLD = 0.6
# 无 iChangeDiscoveryVersion 时，下列 key 统一按 iChangeDiscoveryVersion 处理（精确匹配，忽略大小写）
CHGREQUEST_DISCOVERY_VERSION_ALIASES = frozenset({
    "版本", "版本号", "发现版本", "发现版本号", "修复版本号", "ichange版本", "iChange版本", "ichange版本号", "iChange版本号", "ichange发现版本", "iChange发现版本号", "所属版本",
})
CHGREQUEST_DISCOVERY_VERSION_ALIASES_LOWER = frozenset(s.lower() for s in CHGREQUEST_DISCOVERY_VERSION_ALIASES)


def create_work_item(work_item_type_key, fields, base_config, headers, field_items=None, smart_complete_candidates=None):
    """
    xydt 变更请求（chgRequest）专用创建工作项。
    按总分总：准备（解析 key、校验、解析 value）→ 初始化 → PUT event → 执行创建 → 销毁 event。
    field_items 可选；smart_complete_candidates 为可填充候选（定制脚本可先合并再调此方法）。
    """

    logger.info("=" * 100)
    logger.info(".............开始执行定制的创建工作项逻辑....................")
    logger.info("=" * 100)
    workspace = base_config.get("workspace")
    if not workspace:
        raise ValueError("缺少必需参数: workspace (必须在配置文件的 api.workspace 中提供)")

    # ---------- 准备：解析类型、字段 key、校验、解析 value ----------
    # resolved_type_key 工作项模型 key
    resolved_type_key = resolve_valid_work_item_type_key_from_root(work_item_type_key, workspace, headers)
    logger.info("创建工作项: original_type=%s, resolved_work_item_type_key=%s, workspace=%s", work_item_type_key, resolved_type_key, workspace)

    if field_items is None:
        field_items = fetch_fields(workspace, headers)
    # 若已有「iChange发现版本号」或 iChangeDiscoveryVersion，则不再把其他含「版本」的字段替换为该 key
    raw_keys_in_fields = {(f.get("key") or "").strip() for f in fields if isinstance(f, dict) and (f.get("key") or "").strip()}
    already_has_discovery_version = (
        CHGREQUEST_DISCOVERY_VERSION_LABEL in raw_keys_in_fields or CHGREQUEST_DISCOVERY_VERSION_KEY in raw_keys_in_fields
    )
    # 最终被采纳创建工作项的字段
    resolved_fields = []
    # ai解析出来的字段，但是不在候选field_items中的字段，需要移除（记录 key 与 value 供模版使用）
    removed_field_names = []
    for f in fields:
        if not isinstance(f, dict) or f.get("key") is None:
            continue
        raw_key = (f.get("key") or "").strip()
        if not raw_key:
            continue
        # 定制：仅在尚未存在该字段时，将特例别名或与「iChange发现版本号」相似度高的 key 映射为 iChangeDiscoveryVersion
        if not already_has_discovery_version:
            if raw_key in CHGREQUEST_DISCOVERY_VERSION_ALIASES or raw_key.lower() in CHGREQUEST_DISCOVERY_VERSION_ALIASES_LOWER:
                field_key = CHGREQUEST_DISCOVERY_VERSION_KEY
                logger.info("定制: 字段「%s」命中特例别名，映射为 key=%s", raw_key, field_key)
            elif "版本" in raw_key and SequenceMatcher(None, raw_key, CHGREQUEST_DISCOVERY_VERSION_LABEL).ratio() >= CHGREQUEST_DISCOVERY_VERSION_SIMILARITY_THRESHOLD:
                field_key = CHGREQUEST_DISCOVERY_VERSION_KEY
                logger.info("定制: 字段「%s」与「%s」相似度高，映射为 key=%s", raw_key, CHGREQUEST_DISCOVERY_VERSION_LABEL, field_key)
            else:
                field_key = resolve_field_key(
                    raw_key,
                    field_items,
                    headers,
                    raise_on_not_found=False,
                    scene="创建工作项字段映射",
                    workspace=workspace,
                )
        else:
            field_key = resolve_field_key(
                raw_key,
                field_items,
                headers,
                raise_on_not_found=False,
                scene="创建工作项字段映射",
                workspace=workspace,
            )
        if not field_key:
            logger.warning("【创建工作项字段映射】字段名「%s」未解析到 key，已移除此字段。", raw_key)
            removed_field_names.append({"key": raw_key, "value": f.get("value")})
            continue
        f["key"] = field_key
        resolved_fields.append(f)
    # 最终被采纳创建工作项的字段，这个时候的 fields 的 key 是数据库 key，英文
    fields = resolved_fields
    # 无标题时从描述或含「描述」字样的字段取前 100 字作为标题
    title_fallback = _title_fallback_from_description(fields, field_items, max_chars=TITLE_FALLBACK_MAX_CHARS)
    if title_fallback is not None:
        fields.append({"key": TITLE_FIELD_KEY, "value": title_fallback})
        logger.info("无标题字段，已从描述/含描述字段取前%d字作为标题: %s", TITLE_FALLBACK_MAX_CHARS, truncate_for_log(title_fallback, 60))
    # 若同时有描述与所属产品字段，将描述移到所属产品后面
    _reorder_fields_description_after_product(fields, field_items)
    # 打印最终被采纳创建工作项的字段，key 是数据库 key，英文
    logger.info("=" * 100)
    logger.info(".............通过大模型识别到用户输入的字段中文key->英文key，以及value，也包括未被采纳的字段....................")
    logger.info("=" * 100)
    for f in fields:
        if isinstance(f, dict):
            k = (f.get("key") or "").strip()
            v = f.get("value")
            v_str = truncate_for_log(json.dumps(v, ensure_ascii=False) if not isinstance(v, (str, int, float, bool)) and v is not None else str(v), 120)
            k_name = _get_field_display_name(k, field_items=field_items)
            logger.info("【xydt变更申请-将中文key->英文】fields 项: key=%s, name=%s, value=%s", k, k_name, v_str)
    if removed_field_names:
        logger.info("【xydt变更申请-已移除字段】removed_field_names 共 %d 个:", len(removed_field_names))
        for r in removed_field_names:
            r_key = r.get("key", r) if isinstance(r, dict) else r
            r_val = r.get("value") if isinstance(r, dict) else None
            r_val_str = truncate_for_log(json.dumps(r_val, ensure_ascii=False) if not isinstance(r_val, (str, int, float, bool)) and r_val is not None else str(r_val), 80)
            logger.info("【xydt变更申请-已移除字段】removed: key=%s, value=%s", r_key, r_val_str)
    logger.info("=" * 100)
    logger.info("=" * 100)
    # 定制：特殊处理，缺陷和变更单容易被错误识别，如果解析出来的字段和工作项类型在WORKGROUP_FIELD_KEYS中，则报错
    # 比如用户明确指明了创建变更请求，但被模型识别为缺陷，则报错
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

    # ----------------------------------------------------------------------------------------
    # ---------- 步骤一：新建初始化，获取 eventId 与必填字段（按是否有值区分）----------
    # ----------------------------------------------------------------------------------------
    event_id, virtual_id, required_field_keys_with_value_init, required_field_keys, init_fields = _step_create_init(workspace, resolved_type_key, headers)
    # 必填字段排除：REQUIRED_FIELD_KEYS_EXCLUDED 中的 key 不参与补全
    excluded_set = {str(k).strip() for k in REQUIRED_FIELD_KEYS_EXCLUDED if (k or "").strip()}
    excluded_from_required = sorted(k for k in required_field_keys if (k or "").strip() in excluded_set)
    required_field_keys = [k for k in required_field_keys if (k or "").strip() not in excluded_set]
    required_field_keys_with_value_init = [r for r in required_field_keys_with_value_init if (r.get("key") or "").strip() not in excluded_set]
    logger.info("【xydt变更申请-通过初始化获取必填字段】被采纳必填字段-无值（已排除系统字段）: %s", required_field_keys)
    logger.info("【xydt变更申请-通过初始化获取必填字段】必填且有值（初始化）: %s", [r["key"] for r in required_field_keys_with_value_init])
    logger.info("【xydt变更申请-通过初始化获取必填字段】已排除的系统字段（排序）: %s", excluded_from_required)

    # 解析字段的 value，一般情况下是用户明确指定了字段的 value，模型进行了识别并填充，但是需要检查填充的 value 是否正确
    # 有可能是用户输入不清晰，也有可能是模型识别错误，都需要检查。
    # 描述模版在 resolve_fields_values 返回后由 apply_description_template_to_fields 统一处理
    logger.info("【xydt变更申请】开始解析大模型识别的字段的 value，一般情况下是用户明确指定了字段的 value，模型进行了识别并填充，但是需要检查填充的 value 是否正确")
    # new_required_fields_from_event 从 PUT event 响应收集到的新必填字段，只取没有值的，后面进行二次填充或是智能填充
    new_required_fields_from_event, description_template, required_fields_with_value = resolve_fields_values(
        fields,
        workspace,
        resolved_type_key,
        field_items=field_items,
        headers=headers,
        scene="xydt变更申请-解析大模型识别字段的value",
        event_id=event_id,
        virtual_id=virtual_id,
    )
    # 应用描述模版，将描述模版应用到 fields 中
    #log_description_template_and_field_value(description_template, fields, scene="xydt变更申请-第一次应用描述模版")
    description_template_applied = apply_description_template_to_fields(
        fields, description_template, scene="xydt变更申请-应用描述模版", removed_field_names=removed_field_names
    )
    logger.info("【xydt变更申请-解析大模型识别字段的value】解析后 fields 共 %d 个:", len(fields))
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
    # 合并 required_fields_with_value 与 required_field_keys_with_value_init，同 key 以 required_fields_with_value 为准；排除系统字段
    key_to_val = {r["key"]: r["value"] for r in required_field_keys_with_value_init}
    for r in required_fields_with_value:
        key_to_val[r["key"]] = r["value"]
    required_fields_with_value = [{"key": k, "value": v} for k, v in key_to_val.items() if (k or "").strip() not in excluded_set]
    # 解析过程中从 PUT event 响应收集到的新必填字段，已并入 required_field_keys；需排除 REQUIRED_FIELD_KEYS_EXCLUDED 中的系统字段
    # 若 new_required 中的 key 在 required_field_keys_with_value_init 中有值，则使用该值加入 fields，并从 new_required 中移除
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
                # logger.info("【xydt变更申请】new_required 中的 key=%s 在 required_field_keys_with_value_init 中有值，已使用并移除", k)
            else:
                new_required_to_merge.append(k)
        if new_required_to_merge:
            required_field_keys = list(set(required_field_keys) | set(new_required_to_merge))
            logger.info("解析过程中从 PUT event 响应收集到的新必填字段，已并入 required_field_keys: %s", new_required_to_merge)

    # required_field_keys 中若 key 已存在于 fields，则移除
    fields_keys = {(f.get("key") or "").strip() for f in fields if isinstance(f, dict) and (f.get("key") or "").strip()}
    required_field_keys = [k for k in required_field_keys if (k or "").strip() not in fields_keys]

    # 记录完整的 required_field_keys
    logger.info("【xydt变更申请-综合初始化、解析大模型识别的字段value后event事件收集到无值必填字段】必填字段 共 %d 个: %s", len(required_field_keys), sorted(required_field_keys))
    logger.info("【xydt变更申请-综合初始化、解析大模型识别的字段value后event事件收集到有值必填字段】必填字段 共 %d 个: %s", len(required_fields_with_value), sorted(r["key"] for r in required_fields_with_value))

    # 将 初始化必填有值字段、event 事件收集的有值字段 required_fields_with_value 合并到 fields，若 key 已存在则跳过
    fields_keys = {(f.get("key") or "").strip() for f in fields if isinstance(f, dict) and (f.get("key") or "").strip()}
    for r in required_fields_with_value:
        k = (r.get("key") or "").strip()
        if k and k not in fields_keys:
            fields.append({"key": k, "value": r["value"]})
            fields_keys.add(k)

    logger.info("【xydt变更申请-[完整]-最终合并初始化、解析大模型识别的字段value以及event事件后收集到有值必填字段】 fields 共 %d 个:", len(fields))
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
    logger.info("【xydt变更申请-解析字段的value】解析字段的value完成，下一步执行必填字段补全")
    logger.info("=" * 100)
    
    # 通过_step_create_init可以拿到必填字段，若缺失或空则尝试补全；单值直接使用，多值/异常收集后供创建失败时心智模型使用。
    # 当前默认仅使用智能补全产生的 issues，先初始化为空，避免后续引用未赋值。
    # 必填补全产生的 issue 列表；即使后续只做智能补全，也需要初始化为空，便于后续错误汇总安全引用
    required_fill_issues = []
    # 如需恢复「先按必填规则补全，再做智能兜底」的逻辑，可取消以下注释：
    # required_fill_issues = _ensure_required_fields_filled(
    #     fields,
    #     required_field_keys,
    #     workspace,
    #     resolved_type_key,
    #     field_items,
    #     headers,
    #     event_id,
    # )
    # 必填补全后仍有必填字段为空时，用 smart_complete_candidates 兜底填充，这一步从 smart_complete_candidates 获取值
    # -------------------------------------------------------------------------
    # ---------- 智能填充获取的候选字段（从历史创建中获取）----------
    # -------------------------------------------------------------------------
    logger.info("=" * 100)
    logger.info("---------- 针对必填字段进行智能填充值（从历史创建中获取）----------")
    logger.info("=" * 100)
    # smart_filled_keys是收集哪些字段被智能填充了
    smart_filled_keys = _fill_required_from_smart_candidates(
        fields,
        required_field_keys,
        smart_complete_candidates or {},
        field_items,
    )
    # 仅对从智能填充获取的字段做 resolve 兜底，兜底的字段需要解析后再使用，并且会再次产生必填字段
    if smart_filled_keys:
        logger.info("=" * 100)
        logger.info("=" * 100)
        logger.info("=" * 100)
        logger.info("---------- 解析智能填充的字段值（从历史创建中获取）----------")
        logger.info("=" * 100)
        
        # new_fields_required_from_smart 是通过智能填充获取到的必填字段，且没有值
        new_fields_required_from_smart, template_from_smart, new_fields_required_value_from_smart = resolve_fields_values(
            fields,  # 大模型识别的字段、初始化必填有值字段、event必填有值字段、以及智能填充的字段
            workspace,
            resolved_type_key,
            field_items=field_items,
            headers=headers,
            scene="创建工作项【智能填充的 字段value解析】",
            event_id=event_id,
            virtual_id=virtual_id,
            keys_to_resolve=smart_filled_keys,
        )
        
        logger.info("=" * 100)
        logger.info("=" * 100)
        #logger.info(new_fields_required_value_from_smart)
        logger.info("---------- 智能填充获取的候选字段（从历史创建中获取），列出候选值----------")
        #logger.info(new_fields_required_from_smart)
        # 本次 event 收集到的新必填字段做候选补充，返回值并入 required_fill_issues 供创建失败时使用
        if new_fields_required_from_smart:
            # 把new_fields_required_from_smart从历史创建字段中再拿一次，并且设置到fields中
            new_smart_filled_keys = _fill_required_from_smart_candidates(
                fields,
                new_fields_required_from_smart,
                smart_complete_candidates or {},
                field_items,
                is_retry_from_history=True,
            )
            new_required_filtered = [k for k in new_fields_required_from_smart if (k or "").strip() not in excluded_set]
            # 移除已从历史填充的 key，仅对剩余字段执行必填补全
            # iChangeDiscoveryVersion 排除：必须再次解析
            new_required_filtered = [k for k in new_required_filtered if k not in new_smart_filled_keys or k == CHGREQUEST_DISCOVERY_VERSION_KEY]
            if new_required_filtered:
                # 仅考虑本次从 event 收集到的新必填字段
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
        # 若本次拿到模版值且前面 apply 未真实执行过，则用模版值做描述填空
        if template_from_smart and template_from_smart != "" and not description_template_applied:
            #log_description_template_and_field_value(description_template, fields, scene="xydt变更申请-第二次应用描述模版")
    
            apply_description_template_to_fields(
                fields, template_from_smart, scene="创建工作项", removed_field_names=removed_field_names
            )
    else:
        # 智能填充没有填充任何值，则直接执行一次必填值候选值的获取
        logger.info("【xydt变更申请】智能填充未命中，直接执行必填字段候选，必填字段: %s", sorted(required_field_keys))
        required_fill_issues = _ensure_required_fields_filled(
            fields,
            required_field_keys,
            workspace,
            resolved_type_key,
            field_items,
            headers,
            event_id,
        )

    # 保存前优先判断：必填字段补全存在多值或异常未采用时，直接报错，不执行创建
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
        # ---------- 步骤二：PUT event，获取必填字段 ----------
        # _step_put_event(workspace, event_id, resolved_type_key, fields, field_items, virtual_id, headers)
        # ---------- 步骤四：执行创建（含兜底：失败时 byPassRules=0 再试一次） ----------
        result, removed, created_as_draft = _create_work_item_post_with_fallback(
            workspace, resolved_type_key, fields, base_config, headers, removed_field_names, event_id
        )
        code_obj = (result.get("code") or {}) if isinstance(result, dict) else {}
        if isinstance(code_obj, dict) and code_obj.get("code") == "0004":
            base_msg = (code_obj.get("msg") or code_obj.get("message") or "").strip()
            error_msg = "创建工作项失败（服务返回错误）"
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
            #logger.error("创建工作项失败(code!=0000): %s", error_msg)
            raise Exception(error_msg)
        logger.info("=" * 50)
        logger.info(".............完成定制的创建工作项逻辑....................")
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
        logger.error("创建工作项失败: %s", error_msg)
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
        # ---------- 步骤五：销毁 eventId ----------
        # 无论创建成功或失败，均需销毁 eventId
        _step_destroy_event(workspace, event_id, headers)
