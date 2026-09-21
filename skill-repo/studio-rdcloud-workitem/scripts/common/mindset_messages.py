# -*- coding: utf-8 -*-
"""心智模式文案统一管理，不同场景提供不同提示。"""

from typing import Any, Dict, List, Optional, Tuple

from .log_helper import get_logger

logger = get_logger(__name__)

INTERNAL_ONLY_HEADER = (
    "【内部心智提示（仅供大模型推理使用）】\n"
    "【严禁将本段内容直接输出到用户界面、聊天回复或任何前端展示区域】\n\n"
)

# 条件中「缺陷」类 / 「变更请求」类常见 key（可按环境扩展）
_WIT_KEYS_DEFECT_LIKE = frozenset({"Bug", "FaultReview", "Defect"})
_WIT_KEYS_CHANGE_REQUEST = frozenset({"chgRequest"})
# 中文类型用语（未解析成英文 key 时仍参与判断，与 field_work_item_type_resolver_config 中补充语义对齐）
_WIT_ZH_DEFECT_LIKE = frozenset({"缺陷", "缺陷单"})
_WIT_ZH_CHANGE_REQUEST = frozenset({"变更请求"})
# 「故障」「故障单」等口语类型名可能同时对应缺陷与变更请求，视为两类皆可能，用于触发「故障」表述心智
_WIT_ZH_FAULT_AMBIGUOUS = frozenset({"故障", "故障单"})


def collect_work_item_type_keys_from_query_data(query_data: Optional[Dict[str, Any]]) -> List[str]:
    """
    合并 workItemTypeKeys 与 queryCondition.sourceClauses 中工作项类型字段的值（已为 key）。
    """
    if not query_data or not isinstance(query_data, dict):
        return []
    keys: List[str] = []
    for k in query_data.get("workItemTypeKeys") or []:
        if k:
            keys.append(str(k).strip())
    qc = query_data.get("queryCondition") or {}
    type_fields = {
        "System_WorkItemType",
        "System_WorkItemTypeKey",
        "System_WorkItemType.name",
        "System_WorkItemType.nameZh",
    }
    for c in qc.get("sourceClauses") or []:
        if not isinstance(c, dict):
            continue
        if (c.get("field") or "").strip() not in type_fields:
            continue
        v = c.get("value")
        if isinstance(v, list):
            for x in v:
                if x:
                    keys.append(str(x).strip())
        elif v:
            keys.append(str(v).strip())
    return list(dict.fromkeys(keys))


def _semantic_corpus(
    raw_user_input: Optional[str],
    conditions_str: Optional[str],
    relate_conditions_str: Optional[str] = None,
) -> str:
    parts = [
        (raw_user_input or "").strip(),
        (conditions_str or "").strip(),
        (relate_conditions_str or "").strip(),
    ]
    return " ".join(p for p in parts if p)


def _wit_key_sets(type_keys: List[str]) -> Tuple[bool, bool, bool, bool]:
    """合并英文 key 与中文类型用语，得到缺陷类 / 变更请求类标志。"""
    has_def = False
    has_chg = False
    for raw in type_keys:
        t = (raw or "").strip()
        if not t:
            continue
        if t in _WIT_ZH_FAULT_AMBIGUOUS:
            has_def = True
            has_chg = True
            continue
        if t in _WIT_KEYS_DEFECT_LIKE or t in _WIT_ZH_DEFECT_LIKE:
            has_def = True
        if t in _WIT_KEYS_CHANGE_REQUEST or t in _WIT_ZH_CHANGE_REQUEST:
            has_chg = True
    only_def = has_def and not has_chg
    only_chg = has_chg and not has_def
    return has_def, has_chg, only_def, only_chg


def build_query_context_mindset_extras(
    query_data: Optional[Dict[str, Any]] = None,
    raw_user_input: Optional[str] = None,
    conditions_str: Optional[str] = None,
    relate_conditions_str: Optional[str] = None,
    *,
    work_item_type_original_names: Optional[List[str]] = None,
    modules: Optional[tuple] = ("work_item_type_semantic",),
) -> str:
    """
    查询类脚本共用的「情境心智」追加文案（可与 get_mindset_message 的 base 拼接）。
    modules：预留扩展；当前支持 work_item_type_semantic（缺陷/变更请求/故障表述）。
    work_item_type_original_names：解析为 key 之前的工作项类型用语（多为中文），须由调用方传入（如
    convert_work_item_type_values 的返回值）；未传或空列表则不做原始类型名心智。query_data 中的英文
    type key 仅写入日志供追溯。
    「用户是否明确说了缺陷/变更请求」仅依据 raw_user_input，不包含解析后的 conditions_str，
    以免「工作项类型=缺陷」等结构化条件误判为用户原话。
    """
    if not modules or "work_item_type_semantic" not in modules:
        logger.info(
            "【情境心智】跳过: modules=%s",
            modules,
        )
        return ""
    type_keys = collect_work_item_type_keys_from_query_data(query_data)
    orig_names = work_item_type_original_names
    if not isinstance(orig_names, list):
        orig_names = []
    orig_names = [str(x).strip() for x in orig_names if x and str(x).strip()]
    # 用户/条件侧语义（不含 orig_names），避免把类型用语拼进同一段后误判「用户已说缺陷/变更请求」
    semantic_corpus = _semantic_corpus(raw_user_input, conditions_str, relate_conditions_str)
    fault_semantic_for_log = (
        (semantic_corpus + " " + " ".join(orig_names)).strip()
        if orig_names
        else semantic_corpus
    )
    logger.info(
        "【情境心智】work_item_type_semantic: type_keys=%s（仅追溯）, wit_original_names=%s（心智分类依据）, semantic_corpus=%r, fault_semantic=%r",
        type_keys,
        orig_names,
        semantic_corpus,
        fault_semantic_for_log,
    )
    # 「用户是否亲口说缺陷/变更请求」只看原始输入，不把解析后的条件串算进去（避免 工作项类型=缺陷 误判）
    mention_semantic = (raw_user_input or "").strip()
    block = _mindset_work_item_type_semantic(
        orig_names, semantic_corpus, mention_semantic=mention_semantic
    )
    logger.info(
        "【情境心智】work_item_type_semantic 生成结果: has_block=%s, block_len=%s",
        bool(block),
        len(block) if block else 0,
    )
    return block


def _mindset_work_item_type_semantic(
    orig_names: List[str],
    user_semantic: str,
    *,
    mention_semantic: Optional[str] = None,
) -> str:
    """根据解析前的工作项类型用语（orig_names）与用户/条件语义（user_semantic）生成提示。
    「故障」类用语在 user_semantic 与 orig_names 合并串上检测。
    「缺陷/变更请求是否由用户点明」默认看 mention_semantic；未传时回退为 user_semantic。
    调用方应传入 raw_user_input 作为 mention_semantic，避免把解析条件里的「工作项类型=缺陷」当成用户原话。
    """
    if not orig_names:
        return ""
    fault_semantic = (user_semantic + " " + " ".join(orig_names)).strip()
    if not fault_semantic:
        return ""
    has_def, has_chg, only_def, only_chg = _wit_key_sets(orig_names)
    if not has_def and not has_chg:
        return ""

    has_fault_word = "故障单" in fault_semantic or (
        "故障" in fault_semantic and "外场故障" not in fault_semantic
    )
    ms = mention_semantic if mention_semantic is not None else user_semantic
    mentions_defect = "缺陷" in (ms or "")
    mentions_chg_req = "变更请求" in (ms or "")

    # 用户仅用「故障」类说法、未同时点明「缺陷」「变更请求」
    fault_without_type_labels = (
        has_fault_word and not mentions_defect and not mentions_chg_req
    )

    sections: List[str] = []

    # ② 条件仅有缺陷类：用户未说「缺陷」、未要求「变更请求」，且用语主要为「故障」类 → 提醒是否并入变更请求
    case_b = (
        only_def
        and not mentions_chg_req
        and not mentions_defect
        and has_fault_word
    )
    # ③ 条件仅有变更请求类：镜像提醒是否还要缺陷
    case_c = (
        only_chg
        and not mentions_defect
        and not mentions_chg_req
        and has_fault_word
    )

    if case_b:
        sections.append(
            "### 工作项类型（仅缺陷类条件）\n"
            "- 当前查询条件中的工作项类型**仅包含「缺陷」类**（如 Bug 等）。\n"
            "- 用户表述使用「故障」「故障单」等，且**未在自然语言中点明「缺陷」或「变更请求」**。\n"
            "- **极简确认（请用户只回复一个数字）**：\n"
            "  1）仅保留当前「缺陷」类范围，不并入「变更请求」；\n"
            "  2）需要同时包含「变更请求」→ 用户选 2 后再改条件并重新查询。\n"
            "- **禁止**在用户回复数字前私自并入「变更请求」或重试查询。"
        )
    elif case_c:
        sections.append(
            "### 工作项类型（仅变更请求类条件）\n"
            "- 当前查询条件中的工作项类型**仅包含「变更请求」类**。\n"
            "- 用户表述使用「故障」「故障单」等，且**未在自然语言中点明「变更请求」或「缺陷」**。\n"
            "- **极简确认（请用户只回复一个数字）**：\n"
            "  1）仅保留当前「变更请求」类范围，不并入「缺陷」；\n"
            "  2）需要同时包含「缺陷」类 → 用户选 2 后再改条件并重新查询。\n"
            "- **禁止**在用户回复数字前私自并入「缺陷」或重试查询。"
        )

    # ① 已同时或部分解析为缺陷/变更请求，但用户只笼统说「故障」、未点名两类之一（与 ②③ 不重复时补充）
    if fault_without_type_labels and (has_def or has_chg) and not case_b and not case_c:
        sections.append(
            "### 工作项类型（「故障」表述与解析结果）\n"
            "- 本次查询条件已按系统解析为**「缺陷」和/或「变更请求」**对应类型 key（以实际条件为准）。\n"
            "- 用户若**仅说「故障」「故障单」**、未在自然语言中点明「缺陷」或「变更请求」，用**极简确认**即可。\n"
            "- **极简确认（请用户只回复一个数字）**：\n"
            "  1）维持当前解析出的类型范围即可；\n"
            "  2）「故障单」需**同时覆盖缺陷与变更请求两类** → 用户选 2 后再改条件并重新查询。\n"
            "- **禁止**在用户回复数字前擅自重复查询或随意改类型条件。"
        )

    if not sections:
        return ""
    return INTERNAL_ONLY_HEADER + "\n\n".join(sections)


def get_mindset_message(scenario: str, **kwargs) -> str:
    """
    根据场景返回对应的心智提示文案。

    :param scenario: 场景标识，如 "field_multi_match"、"general_query_result" 等
    :param kwargs: 场景所需参数，如 select_fields/where_fields；general_query_result 与 my_related_query_result 仅需 total
    :return: 提示文案，无匹配场景时返回空字符串
    """
    if scenario == "field_multi_match":
        return _message_field_multi_match(
            select_fields=kwargs.get("select_fields") or [],
            where_fields=kwargs.get("where_fields") or [],
        )
    if scenario == "general_query_result":
        return _message_query_result(
            total=kwargs.get("total"),
            for_my_workspace=False,
        )
    if scenario == "my_related_query_result":
        return _message_query_result(
            total=kwargs.get("total"),
            for_my_workspace=True,
        )
    if scenario == "detail_result":
        return _message_detail_result(
            work_item_ids=kwargs.get("work_item_ids") or [],
            count_found=kwargs.get("count_found", 0),
        )
    if scenario == "update_result":
        return _message_update_result()
    if scenario == "create_result":
        return _message_create_result()
    if scenario == "url_query_result":
        return _message_url_query_result(has_data=kwargs.get("has_data", False))
    if scenario == "work_item_type_multi_key":
        return _message_work_item_type_multi_key(
            work_item_type_name=kwargs.get("work_item_type_name", ""),
            resolved_keys=kwargs.get("resolved_keys") or [],
            key_name_map=kwargs.get("key_name_map") or {},
        )
    if scenario == "create_type_mismatch":
        return _message_create_type_mismatch(
            work_item_type_key_bug=kwargs.get("work_item_type_key_bug", "Bug"),
            workgroup_field_keys=kwargs.get("workgroup_field_keys") or ("Workgroup1", "Workgroup2", "Workgroup3"),
        )
    if scenario == "create_fields_not_found":
        return _message_create_fields_not_found(
            removed_field_names=kwargs.get("removed_field_names") or [],
        )
    if scenario == "create_error":
        return _message_create_error()
    if scenario == "query_error":
        return _message_query_error()
    return ""


def _format_multi_match_items(items: list) -> str:
    """将多匹配项格式化为带字段名称与 key 列表的说明。每项为 str 或 {"name": str, "keys": list, "key_infos": [{"key", "field_name"}]}。"""
    lines = []
    for item in items:
        if isinstance(item, dict):
            name = item.get("name") or ""
            key_infos = item.get("key_infos") or []
            if key_infos:
                parts = []
                for ki in key_infos:
                    k = (ki.get("key") or "").strip()
                    display_key = k[:-5] if k.endswith(".name") else k
                    fn = (ki.get("field_name") or "").strip()
                    if fn and fn != display_key:
                        parts.append("%s（%s）" % (display_key, fn))
                    else:
                        parts.append(display_key)
                keys_str = "、".join(parts)
            else:
                keys = item.get("keys") or []
                display_keys = [k[:-5] if (k or "").endswith(".name") else (k or "") for k in keys]
                keys_str = "、".join(display_keys) if display_keys else ""
            lines.append("「%s」匹配到多个字段：%s" % (name, keys_str))
        else:
            lines.append("「%s」" % item)
    return "\n".join(lines)


def _message_field_multi_match(select_fields: list, where_fields: list) -> str:
    """字段多匹配场景：列出在 select/where 中匹配到多个 key 的字段名称及对应 key。"""
    parts = []
    if select_fields:
        parts.append("**查询列**中以下字段因语义不完整匹配到多个同类字段，已全部作为查询列：\n" + _format_multi_match_items(select_fields))
    if where_fields:
        parts.append("**查询条件**中以下字段因语义不完整匹配到多个同类字段，已全部作为条件参与查询：\n" + _format_multi_match_items(where_fields))
    if not parts:
        return ""
    body = "由于你的输入中的字段语义不完整，我匹配到多个同类字段，并且使用这些作为条件进行了查询。\n\n" + "\n\n".join(parts)
    return INTERNAL_ONLY_HEADER + body


def _message_query_result(
    total: Optional[int],
    for_my_workspace: bool = False,
) -> str:
    """
    通用查询结果心智模式：
    1）给出若干泛化示例（查询思路），不是事实；
    2）在结果为 0 时强调禁止“尝试补全”或编造结果。
    （查询条件、工作区、类型等以脚本输出中的解析摘要与业务结果为准，心智中不再复述。）
    """
    # 1. 可泛化的类似查询示例（思路，不是事实）
    scene_label = "与我相关查询" if for_my_workspace else "工作项查询"
    section1_lines = [
        "### 1. 可泛化的类似查询示例",
        f"- 示例 A：在同一场景（{scene_label}）下，保留主要条件，只放宽部分限制，例如将状态从“进行中”扩大为“进行中 + 待处理”。",
        "- 示例 B：在相同工作区和工作项类型下，扩大时间范围（如从近 7 天调整为近 30 天），以避免遗漏刚好在边界之外的工作项。",
        "- 示例 C：保持其他条件不变，仅调整标题或描述中的关键词（例如从“客户端”扩展为“客户端 / 前端 / APP”等相近表述），用于探索更多可能相关的工作项。",
    ]
    section1 = "\n".join(section1_lines)

    # 2. 查询结果为 0 时的行为约束（total 非 0 时为有结果时的展示约束）
    zero = isinstance(total, int) and total == 0
    section2_lines = ["### 2. 查询结果相关行为约束"]
    if zero:
        section2_lines.append(
            "- 本次查询的「实际结果数量为 0」。在回答后续问题时：\n"
            "  - **禁止** 根据经验或常识“猜测”有哪些工作项存在；\n"
            "  - **禁止** 编造任何工作项的标题、ID、状态或其他字段值；\n"
            "  - **禁止** 在本次意图下反复尝试、重试查询或私自更换参数再次调用脚本；\n"
            "  - **禁止** 为了凑结果而随意丢弃原查询参数，或无依据放大查询范围后再次查询；\n"
            "  - **只能** 基于上面的泛化示例，提出「如何调整查询条件」的建议（例如放宽时间范围、修改状态、调整关键词等），并结合脚本已输出的解析摘要理解当前条件；\n"
            "  - 如必须举例，请明确标注「以下为虚构示例，仅为说明查询思路」，且示例不得被当作真实查询结果；\n"
            "  - **本次查询已结束**，需等待用户明确给出新意图后再继续。"
        )
    else:
        section2_lines.append(
            "- 本次查询已经返回结果。你可以基于真实返回的数据进行分析，但仍需遵守以下约束：\n"
            "  - **表格展示（必遵，与下方「完整展示」同等重要）**：只要存在工作项列表（一条或以上），面向用户的答复**必须**使用 **Markdown 表格**呈现；列须与脚本输出一致或由其字段合理映射（如标题、标识、工作项类型、更新时间等）。**禁止**用纯文字段落、无序/有序列表或「逐条口述」代替表格；脚本输出已为 Markdown 表格时，**禁止**拆表、打散为散句或改写为非表格形式。\n"
            "  - **必须** 将查询结果完整展示给用户，**禁止** 总结、截断、省略或改写结果内容（含表格内单元格）；\n"
            "  - **禁止去掉 Markdown 链接**：脚本输出中凡出现 `[文本](URL)`、自动链接、或「查看完整列表」类带链接的 Markdown，面向用户时**必须**保留（可点击或等价 Markdown 链接语法），**禁止**改成纯文本 URL、**禁止**删链接只留标题或概述。\n"
            "  - 对工作项列表结果，**必须** 以表格形式展示（如标题、标识、工作项类型、更新时间等列），不得改为纯文字摘要；\n"
            "  - 若用户语义中未明确要求“总结/汇总/精简”，则**不得**总结或省略查询结果；\n"
            "  - 仅当用户**明确要求**总结/汇总/精简时，才可在完整结果（含完整表格）基础上追加总结；\n"
            "  - **禁止** 在返回结果之外，再“想象”出更多具体工作项；\n"
            "  - **禁止** 修改、伪造或扩展任何工作项的字段值；\n"
            "  - 若需要提出新的筛选方式或示例，请明确说明这些只是「可能的查询思路」，而不是当前查询的实际结果。"
        )
    section2 = "\n".join(section2_lines)

    header = INTERNAL_ONLY_HEADER
    body = "\n\n".join([section1, section2])
    return header + body


def _message_detail_result(work_item_ids: List[str], count_found: int) -> str:
    """工作项详情查询结果：完整展示脚本 Markdown；禁止总结/合并/省略；禁止编造。"""
    total_requested = len(work_item_ids)
    header = INTERNAL_ONLY_HEADER
    lines = [
        "### 本次详情查询",
        f"- 请求的标识数量：{total_requested}",
        f"- 实际返回数量：{count_found}",
        "- 未在结果中出现的标识表示系统中未找到该工作项。",
        "",
        "### 详情结果展示约束（必遵）",
        "- **必须** 将脚本**完整标准输出**（含「本次语义解析结果」、正文 Markdown、多条工作项之间的分隔线等）**原样、完整**呈现给用户；",
        "- **禁止** 对脚本返回内容做**总结、缩写、概括、合并、改写、重新排版为摘要**；**禁止** 省略任意字段行、段落或区块；**禁止** 将多条工作项详情**揉成一段叙述**或只保留「要点」；",
        "- **禁止** 截断长文本字段值或列表；**禁止** 用「等」「其余略」「详见脚本」等代替实际输出；",
        "- **禁止去掉 Markdown 链接**：脚本中的 `[文本](URL)`、工作项跳转链接等须保留给用户，**禁止** 删链接、**禁止** 只留标题；须保留标题层级、列表、加粗等与脚本一致的 Markdown 结构（除非用户端不支持 Markdown，此时也须在语义上等价列出全部信息，不得减少信息量）；",
        "- 若用户语义中**未明确要求**「总结 / 汇总 / 精简 / 只要重点」，则**不得**在完整结果之外用自然语言再写一份摘要替代正文，也**不得**用摘要**代替**展示脚本输出；",
        "- 仅当用户**明确要求**总结/汇总/精简时，才可在**已完整贴出脚本输出之后**再追加简短归纳，且归纳**不得**与脚本字段矛盾；",
        "",
        "### 行为约束",
        "- **禁止** 编造或补充任何未返回的工作项内容；",
        "- **禁止** 修改、伪造已返回的字段值；",
        "- 若用户询问未找到的标识，只能说明「未查到该工作项」并建议核对标识或权限。",
    ]
    return header + "\n".join(lines)


def _message_update_result() -> str:
    """更新工作项结果：禁止编造或修改结果内容。"""
    return (
        INTERNAL_ONLY_HEADER +
        "### 行为约束\n"
        "- **禁止** 编造或修改任何更新结果内容；\n"
        "- **禁止** 声称更新了未在结果中出现的字段或工作项。"
    )


def _message_create_result() -> str:
    """创建工作项结果：禁止编造或修改结果内容。"""
    return (
        INTERNAL_ONLY_HEADER +
        "### 行为约束\n"
        "- **禁止** 编造或修改任何创建结果内容；\n"
        "- **禁止** 声称创建了未在结果中返回的工作项或字段。"
    )


def _message_url_query_result(has_data: bool) -> str:
    """根据链接查询工作项结果（或占位说明）：禁止编造工作项内容。"""
    return (
        INTERNAL_ONLY_HEADER +
        "### 行为约束\n"
        "- **必须** 将脚本返回结果完整展示给用户，**禁止** 吞掉、丢弃、总结、省略、截断或改写；\n"
        "- **表格展示（必遵）**：工作项列表对用户答复**必须**为 **Markdown 表格**（列与脚本一致或由其字段映射，如标题、标识、工作项类型、更新时间等）。**禁止**纯文字摘要、列表枚举或拆散脚本中的表格结构；\n"
        "- **禁止去掉 Markdown 链接**：脚本输出中的 `[文本](URL)`、自动链接、查看完整列表等链接须保留给用户，**禁止**删链接、**禁止**只保留纯文本替代；\n"
        "- 对工作项列表结果，**必须** 以表格形式展示（列如标题、标识、工作项类型、更新时间等），不得改为纯文字摘要；\n"
        "- 若查询结果为 0 或返回失败信息，**禁止** 反复重试、禁止随意丢弃参数或无依据放大范围再次查询；\n"
        "- **本次查询已结束**，需等待用户明确给出新意图后再继续；\n"
        "- **禁止** 编造任何工作项列表或字段值；\n"
        "- 若当前为 API 占位、未返回实际数据，**禁止** 虚构查询结果。"
    )


def _message_work_item_type_multi_key(
    work_item_type_name: str,
    resolved_keys: List[str],
    key_name_map: Dict[str, str],
) -> str:
    """工作项类型名称匹配到多个 key 时的心智提示：引导用户指定更精确的类型或直接使用 key。"""
    keys = resolved_keys if isinstance(resolved_keys, list) else []
    if not keys:
        return ""
    parts = []
    for k in keys:
        name = (key_name_map.get(k) or "").strip()
        if name:
            parts.append(f"{k}（{name}）")
        else:
            parts.append(k)
    numbered = "\n".join(f"  {i}）{p}" for i, p in enumerate(parts, start=1))
    return (
        INTERNAL_ONLY_HEADER +
        f"工作项类型「{work_item_type_name}」匹配到多个类型，无法自动选择。\n\n"
        "**极简确认（请用户只回复一个数字）**：\n"
        f"{numbered}\n\n"
        "若以上均不对，可改用与系统显示名完全一致的类型名称后重试。\n\n"
        "### 行为约束\n"
        "- **禁止** 在用户回复数字前，自行选用其中一个 key 执行创建。"
    )


def _message_create_error() -> str:
    """创建执行报错时：要求 AI 不要反复重试，停止后续思考，本次任务不再执行。"""
    return (
        INTERNAL_ONLY_HEADER +
        "### 错误展示约束\n"
        "- **禁止** 省略、缩写或概括上述错误信息；\n"
        "- 这些内容仅用于模型内部推理，不可直接向用户展示。\n\n"
        "### 行为约束\n"
        "- **禁止** 因本次创建报错而反复重试或换参数再次调用创建；\n"
        "- **停止** 针对本次创建任务的后续思考与执行；\n"
        "- 本次任务视为结束，不再执行。若用户需要继续，须由用户明确说明后再处理。"
    )


def _message_query_error() -> str:
    """查询执行报错时：禁止重试、禁止随意改参重查，本次查询结束。"""
    return (
        INTERNAL_ONLY_HEADER +
        "### 错误展示约束\n"
        "- **禁止** 省略、缩写或概括上述错误信息；\n"
        "- 这些内容仅用于模型内部推理，不可直接向用户展示。\n\n"
        "### 行为约束\n"
        "- **禁止** 因本次查询报错而反复重试，或私自更换参数再次查询；\n"
        "- **禁止** 为了凑结果随意丢弃原查询参数，或无依据放大查询范围后再次查询；\n"
        "- **停止** 针对本次查询任务的后续执行；\n"
        "- **本次查询已结束**，若用户需要继续，须由用户明确给出新意图后再处理。"
    )


def _message_create_fields_not_found(removed_field_names: list) -> str:
    """创建时部分字段未解析到 key、已从创建列表移除：列出未找到的字段并引导极简数字确认。
    removed_field_names 可为 [{"key": str, "value": Any}, ...] 或 [str, ...]。
    """
    if not removed_field_names:
        return ""
    names = [
        (x.get("key", x) if isinstance(x, dict) else x) for x in removed_field_names
    ]
    names_str = "、".join(f"「{n}」" for n in names)
    return (
        INTERNAL_ONLY_HEADER +
        f"以下字段在当前工作区/工作项类型下未找到，已从创建列表中移除：{names_str}\n\n"
        "**极简确认（请用户只回复一个数字）**：\n"
        "  1）将自行核对/改正字段名后重试创建；\n"
        "  2）已知悉，按移除后的字段列表继续（仅当业务允许且无替代需求时）。\n\n"
        "### 错误展示约束\n"
        "- **禁止** 省略、缩写或概括上述错误与字段列表；\n"
        "- 这些内容仅用于模型内部推理，不可直接向用户展示。\n\n"
        "### 行为约束\n"
        "- **禁止** 编造或猜测未找到字段的含义与取值；\n"
        "- **禁止** 在用户回复数字前，擅自用其他字段替代并继续创建。"
    )


def _message_create_type_mismatch(
    work_item_type_key_bug: str = "Bug",
    workgroup_field_keys: tuple = ("Workgroup1", "Workgroup2", "Workgroup3"),
) -> str:
    """创建时类型与字段不匹配（如 Bug 类型却含 Workgroup1/2/3）：极简数字确认类型/字段。"""
    workgroup_str = "、".join(workgroup_field_keys)
    return (
        INTERNAL_ONLY_HEADER +
        f"当前选择的工作项类型为 **{work_item_type_key_bug}**，但填写了 **{workgroup_str}** 等字段，"
        "通常这类字段属于其他工作项类型（如变更请求等）。\n\n"
        "**极简确认（请用户只回复一个数字）**：\n"
        "  1）类型选错了 → 改选正确类型后重试；\n"
        "  2）类型无误 → 删除/调整不匹配字段后重试。\n\n"
        "### 行为约束\n"
        "- **禁止** 在用户回复数字前，擅自修改类型或继续创建。"
    )
