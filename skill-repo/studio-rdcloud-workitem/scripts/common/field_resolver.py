#!/usr/bin/env python
"""
字段解析：字段名称/中文名 → 系统 key。
通过工作区字段列表（withUsageDistinct）匹配；0 条或多条时可选 Studio Chat 兜底；仍未命中时可配置兜底直接映射（如含「描述」→ 某 key）。
供查询/创建/更新/统计等脚本统一使用，禁止在各 atomic 脚本内重复实现。
"""

import json
import requests
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from . import rdcloud_api
from .log_helper import get_logger

logger = get_logger(__name__)

def _contains_chinese(s: str) -> bool:
    """判断字符串是否包含中文字符。"""
    if not s:
        return False
    for c in s:
        if "\u4e00" <= c <= "\u9fff" or "\u3400" <= c <= "\u4dbf":
            return True
    return False

# 前后/包含匹配例外：以下字段名禁止使用前后/中间包含匹配，仅允许精确匹配。可配置扩展。
FIELD_MATCH_EXCEPTIONS: List[str] = ["工作项类型", "状态"]

# 匹配时可去掉的字段名/候选名尾部后缀（如「号」），避免「发现版本」无法匹配「iChange发现版本号」。
# 未来可从配置扩展，目前只配置「号」。
FIELD_MATCH_STRIP_SUFFIXES: List[str] = ["号",  "号名称", "名称",]

# 采纳时已要求「原文中出现候选」的分支；最终 keys 不得仅因 overlap 低于其它项而被丢弃
FIELD_MATCH_BRANCHES_EXEMPT_FROM_OVERLAP_TIEBREAK = frozenset(
    {
        "original_input_name_prefix_suffix_candidate_in_text",
        "original_input_middle_contains_stripped_in_text",
    }
)

# 兜底映射配置缓存（从 common/field_resolver_config.json 的 fallback_map 加载）
_field_fallback_rules_cache: Optional[List[Dict[str, str]]] = None

# 字段推荐名规则缓存（field_recommended_names）：工作区/工作项类型可选 + 字段标识 → 推荐展示名列表
_field_recommended_names_rules_cache: Optional[List[Dict[str, Any]]] = None

_FIELD_RECOMMENDED_NAMES_RESERVED_KEYS = frozenset({"workspace", "work_item_type_key"})


def _get_field_fallback_rules() -> List[Dict[str, str]]:
    """从 common/field_resolver_config.json 读取 fallback_map，格式 [{"name_contains": "描述", "map_to_key": "System_Description"}, ...]。"""
    global _field_fallback_rules_cache
    if _field_fallback_rules_cache is not None:
        return _field_fallback_rules_cache
    try:
        config_path = Path(__file__).resolve().parent / "field_resolver_config.json"
        if not config_path.exists():
            _field_fallback_rules_cache = []
            return []
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        rules = config.get("fallback_map") or []
        if not isinstance(rules, list):
            _field_fallback_rules_cache = []
            return []
        out = []
        for r in rules:
            if isinstance(r, dict) and r.get("name_contains") and r.get("map_to_key"):
                out.append({"name_contains": str(r["name_contains"]).strip(), "map_to_key": str(r["map_to_key"]).strip()})
        _field_fallback_rules_cache = out
        return out
    except Exception as e:
        logger.debug("加载 common/field_resolver_config.json fallback_map 失败: %s", e)
        _field_fallback_rules_cache = []
        return []


def _get_field_recommended_name_rules() -> List[Dict[str, Any]]:
    """从 common/field_resolver_config.json 读取 field_recommended_names（对象数组）。"""
    global _field_recommended_names_rules_cache
    if _field_recommended_names_rules_cache is not None:
        return _field_recommended_names_rules_cache
    try:
        config_path = Path(__file__).resolve().parent / "field_resolver_config.json"
        if not config_path.exists():
            _field_recommended_names_rules_cache = []
            return []
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        raw = config.get("field_recommended_names") or []
        if not isinstance(raw, list):
            _field_recommended_names_rules_cache = []
            return []
        out: List[Dict[str, Any]] = [x for x in raw if isinstance(x, dict)]
        _field_recommended_names_rules_cache = out
        return out
    except Exception as e:
        logger.debug("加载 field_resolver_config.json field_recommended_names 失败: %s", e)
        _field_recommended_names_rules_cache = []
        return []


def _field_mappings_from_recommended_rule(rule: Dict[str, Any]) -> Dict[str, List[str]]:
    """从单条规则中取出「字段标识 → 推荐名列表」（排除 workspace、work_item_type_key）。"""
    out: Dict[str, List[str]] = {}
    for k, v in rule.items():
        if k in _FIELD_RECOMMENDED_NAMES_RESERVED_KEYS:
            continue
        key = (k or "").strip()
        if not key:
            continue
        if isinstance(v, list):
            names = [str(x).strip() for x in v if str(x).strip()]
        elif isinstance(v, str) and v.strip():
            names = [v.strip()]
        else:
            continue
        if names:
            out[key] = names
    return out


def _recommended_names_from_config(
    workspace: Optional[str],
    work_item_type_key: Optional[str],
    field_name: str,
) -> List[str]:
    """
    按优先级从配置中取某字段标识对应的推荐名列表，供 _rule_based_text_allows_field_match 判断候选是否与推荐名一致。
    1）workspace + work_item_type_key + 字段名（配置中键）
    2）work_item_type_key + 字段名（若规则含 workspace 则须与当前 workspace 一致）
    3）仅字段名（规则中不含 workspace 与 work_item_type_key）
    """
    fn = (field_name or "").strip()
    if not fn:
        return []
    rules = _get_field_recommended_name_rules()
    ws = (workspace or "").strip()
    wit = (work_item_type_key or "").strip()

    # 1）工作区 + 类型 + 字段名
    if ws and wit:
        for rule in rules:
            if (rule.get("workspace") or "").strip() != ws:
                continue
            if (rule.get("work_item_type_key") or "").strip() != wit:
                continue
            m = _field_mappings_from_recommended_rule(rule)
            if fn in m:
                return m[fn]

    # 2）工作项类型 + 字段名
    if wit:
        for rule in rules:
            if (rule.get("work_item_type_key") or "").strip() != wit:
                continue
            rw = (rule.get("workspace") or "").strip()
            if rw and rw != ws:
                continue
            m = _field_mappings_from_recommended_rule(rule)
            if fn in m:
                return m[fn]

    # 3）仅字段名
    for rule in rules:
        if (rule.get("workspace") or "").strip() or (rule.get("work_item_type_key") or "").strip():
            continue
        m = _field_mappings_from_recommended_rule(rule)
        if fn in m:
            return m[fn]

    return []


def _rule_based_text_allows_field_match(
    workspace: Optional[str],
    work_item_type_key: Optional[str],
    field_name: str,
    c_lower: str,
) -> bool:
    """候选名 c_lower 与 field_recommended_names 中该字段的某一推荐名相同（忽略大小写）时采纳，不要求出现在用户原文中。"""
    recs = _recommended_names_from_config(workspace, work_item_type_key, field_name)
    if not recs:
        return False
    cl = (c_lower or "").strip().lower()
    if not cl:
        return False
    for r in recs:
        if (r or "").strip().lower() == cl:
            return True
    return False


def fetch_fields(workspace: str, headers: Dict[str, Any]) -> List[dict]:
    """获取工作区字段列表（withUsageDistinct）。委托 rdcloud_api.fetch_fields。"""
    fields = rdcloud_api.fetch_fields(workspace, headers)
    return fields


# 研发云字段元数据中时间类 type（大小写不敏感）
_DATETIME_FIELD_TYPES = frozenset({
    "DATETIME", "DATE", "DATE_TIME", "TIMESTAMP",
})


def get_datetime_field_keys(fields: Optional[List[dict]] = None) -> frozenset:
    """
    从字段列表中提取日期/时间类型字段的 key 集合。
    供 _cell_value 等对时间类型统一格式化使用。
    """
    if not fields:
        return frozenset()
    keys = set()
    for f in fields:
        if not isinstance(f, dict):
            continue
        k = (f.get("key") or "").strip()
        if not k:
            continue
        ft = (f.get("type") or "").strip().upper()
        if ft in _DATETIME_FIELD_TYPES or "DATETIME" in ft or ft == "DATE":
            keys.add(k)
    return frozenset(keys)


def resolve_extra_header_names(
    extra_keys: List[str],
    fields: Optional[List[dict]] = None,
    workspace: Optional[Union[str, List[str]]] = None,
    headers: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """
    将查询结果表格中「固定列之外」的字段 key 转为展示用列名（优先 name，其次 nameZh，否则回退 key）。
    供 query_work_item、query_my_work_item、query_work_item_by_url 等 Markdown 表头一致使用。
    :param extra_keys: 额外列 key 列表
    :param fields: 字段列表（推荐由调用方 fetch_fields 后传入，避免重复请求）；未传时由 workspace+headers 获取
    :param workspace: 工作区（fields 为空时用于 fetch_fields）
    :param headers: 认证头（fields 为空时用于 fetch_fields）
    """
    if not extra_keys:
        return []
    items = fields
    if items is None or (isinstance(items, list) and not items):
        if isinstance(workspace, list):
            ws = str(workspace[0]).strip() if workspace else ""
        else:
            ws = str(workspace or "").strip()
        if ws and headers:
            try:
                items = fetch_fields(ws, headers)
            except Exception as e:
                logger.warning("额外字段中文名解析失败，回退 key 展示: %s", e)
                return list(extra_keys)
        else:
            return list(extra_keys)
    key_to_name: Dict[str, str] = {}
    for f in (items or []):
        if not isinstance(f, dict):
            continue
        k = (f.get("key") or "").strip()
        if not k:
            continue
        n = (f.get("name") or "").strip() or (f.get("nameZh") or "").strip() or k
        key_to_name[k] = n
    return [key_to_name.get(k, k) for k in extra_keys]


def _resolve_field_name_by_chat(
    field_name: str,
    headers: Dict[str, Any],
    workspace: str = "PLM",
    scene: str = "字段映射兜底",
) -> Optional[str]:
    """
    调用 Studio Chat 接口，将用户输入的字段名解析为规范字段名。
    请求体 type=field、input 为 field_name，返回 bo.result 作为匹配到的字段名。失败时返回 None。
    workspace：请求 URL 中的工作区参数，须由调用方传入。
    """
    name = (field_name or "").strip()
    if not name:
        return None
    req_headers = {
        "Content-Type": "application/json",
        "X-Emp-No": headers.get("X-Emp-No") or "",
        "X-Auth-Value": headers.get("X-Auth-Value") or "",
        "X-Tenant-Id": headers.get("X-Tenant-Id") or "ZTE",
    }
    body = {
        "type": "field",
        "input": name
    }
    url = rdcloud_api.STUDIO_CHAT_API_URL.format(workspace=workspace or "PLM")
    try:
        logger.info("【%s】Studio Chat url=%s body=%s", scene, url, body)
        r = requests.post(url, headers=req_headers, json=body, timeout=15)
        r.raise_for_status()
        data = r.json()
        if (data.get("code") or {}).get("code") != "0000":
            logger.warning("【%s】Studio Chat 返回非 0000: %s", scene, data.get("code"))
            return None
        bo = data.get("bo") or {}
        result = (bo.get("result") or "").strip()
        if result:
            logger.info("【%s】Studio Chat 返回规范字段名: %s -> %s", scene, name, result)
            return result
        return None
    except Exception as e:
        logger.warning("【%s】Studio Chat 请求失败: %s", scene, e)
        return None


def _normalize_field_name_for_match(field_name: str) -> str:
    """去掉前后空格、单引号、双引号；工作项标识统一处理成标识。"""
    s = (field_name or "").strip().strip("'\"").strip()
    if not s:
        return ""
    if s == "工作项标识":
        return "标识"
    return s


def _strip_match_suffixes(s: str) -> str:
    """匹配时去掉配置的尾部后缀（如「号」），用来放宽「发现版本」vs「iChange发现版本号」等场景。"""
    if not s:
        return s
    out = s
    for suf in FIELD_MATCH_STRIP_SUFFIXES:
        suf = (suf or "").strip()
        if suf and out.endswith(suf):
            out = out[: -len(suf)]
    return out


def _match_field_key(field_name: str, items: List[dict]) -> List[str]:
    """
    将字段名与 items 做匹配，返回匹配到的 key 列表（可能 0 个、1 个或多个）。
    会先做规范化：去前后空格与引号，工作项标识→标识。
    """
    name = _normalize_field_name_for_match(field_name)
    if not name:
        return []
    matches = []
    for it in items:
        if not isinstance(it, dict):
            continue
        k = (it.get("key") or "").strip()
        n = (it.get("name") or "").strip()
        nz = (it.get("nameZh") or "").strip()
        ne = (it.get("nameEn") or "").strip()
        if name == k or name == n or name == nz or name == ne:
            matches.append(k)
        elif name.lower() in ((k or "").lower(), (n or "").lower(), (nz or "").lower(), (ne or "").lower()):
            matches.append(k)
    return list(dict.fromkeys(matches))

# 专门在解析查询字段、条件字段进行放宽匹配范围
# 放宽字段范围是有问题的，因为会匹配到很多不相关的字段，导致查询结果不准确。！！！！！！（不过我会在心智模型中提示用户进行了字段放宽）
def _match_field_key_prefix_suffix(
    field_name: str,
    items: List[dict],
    original_input: Optional[str] = None,
    scene: str = "字段映射",
    workspace: Optional[str] = None,
    work_item_type_key: Optional[str] = None,
) -> List[str]:
    """
    仅做精确匹配、前后模糊匹配或中间包含匹配。
    - 精确匹配：一律采纳。
    - 前后匹配：采纳「候选以用户输入开头或结尾」或「用户输入以候选开头或结尾」的情况（如 [发现版本号XXX]、[XXX发现版本号]）。
    - 中间包含：当字段名与候选存在包含关系（如 字段名「版本」与候选「iChange发现版本」），且候选整体在 original_input 中出现时采纳。
    - 字段名或候选名在 FIELD_MATCH_EXCEPTIONS 中时禁止前后匹配与中间包含匹配，仅允许精确匹配。
    - 多 key 歧义时：overlap 最大者优先；但「原文中已出现候选」才采纳的两类分支（见 FIELD_MATCH_BRANCHES_EXEMPT_FROM_OVERLAP_TIEBREAK）不因 overlap 较小被剔除。
    - workspace / work_item_type_key：可选；用于 field_recommended_names 分级匹配；候选 c_lower 与配置中推荐名一致时可采纳，不要求出现在用户原文中。
    scene：日志场景标识，如「SELECT字段映射」「WHERE字段映射」，便于在日志中区分调用来源。
    """
    # 大模型解析出来的字段中文名
    name = _normalize_field_name_for_match(field_name)
    if not name:
        logger.info("【%s】前后匹配: 规范化后字段名为空, 原始字段名=%r", scene, field_name)
        return []
    # 大模型解析出来的字段中文名，转小写    
    name_lower = name.lower()
    # 大模型解析出来的字段中文名去掉后缀“号”或“名称”或其他，因为用户可能多输入了，实际上字段中文名没有这个后缀【不太合理！！！！！】
    name_base = _strip_match_suffixes(name_lower)
    logger.info(
        "【%s】前后匹配: 开始处理大模型识别的字段名「%s」, 经过去除特定后缀后的字段名=%s, original_input=%s",
        scene,
        name,
        name_base,
        (original_input[:100] + "..." if isinstance(original_input, str) and len(original_input) > 100 else original_input),
    )
    # 原始用户输入，转小写
    orig_lower = (original_input or "").lower()
    # 禁止前后匹配、中间包含匹配，仅允许精确匹配的字段名集合
    exception_set = {x.strip() for x in FIELD_MATCH_EXCEPTIONS if (x or "").strip()}
    # (key, overlap_length, matched_candidate_lower, match_branch)：match_branch 用于「原文锚定」分支不参与纯 overlap 淘汰
    match_with_len: List[tuple] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        k = (it.get("key") or "").strip()
        n = (it.get("name") or "").strip()
        nz = (it.get("nameZh") or "").strip()
        ne = (it.get("nameEn") or "").strip()
        # if "版本" in (name or "") and "版本" in (n or ""):
        #     logger.info(
        #         "【%s】前后匹配: 待匹配 name=%r name_base=%r | 候选 key=%r name=%r nameZh=%r nameEn=%r",
        #         scene,
        #         name,
        #         name_base,
        #         k,
        #         n,
        #         nz,
        #         ne,
        #     )
        for c in (k, n, nz, ne):
            if not c:
                continue
            #从字段表取到的候选字段名称
            c_lower = c.lower()
            #从字段表取到的字段名称去掉后缀“号”或“名称”或其他【不太合理！！！！！】
            c_base = _strip_match_suffixes(c_lower)
            overlap = 0
            match_branch = ""
            # 精确匹配
            if name_lower == c_lower:
                overlap = len(name_lower)
                match_branch = "exact"
            # 前中后匹配
            elif (
                c_lower.startswith(name_lower)
                or c_lower.endswith(name_lower)
                or name_lower.startswith(c_lower)
                or name_lower.endswith(c_lower)
                # 中间包含匹配（去指定后缀，如“号”）：大模型识别的字段名和候选字段名存在包含关系
                or (name_base and c_base and (name_base in c_base or c_base in name_base))
            ):
                if name.strip() in exception_set:
                    break
                if any((s or "").strip() in exception_set for s in (n, nz, ne, k) if s):
                    break
                # 没有接收到原始用户输入
                if not original_input:
                    # 如果大模型识别的字段中文名称在候选字段的中文名称的开头或结尾，则采纳
                    # 用户说了一个“更长的字段叫法”，而字段表里存在一个“更短的标准字段名”（或反过来），通过“前缀/后缀包含”把它们关联起来
                    if name_lower.startswith(c_lower) or name_lower.endswith(c_lower):
                        overlap = len(c_lower)
                        match_branch = "no_original_input_name_prefix_suffix_candidate"
                    else:
                        overlap = len(name_lower)
                        match_branch = "no_original_input_candidate_prefix_suffix_name_or_middle"
                # 接收到原始用户输入，并且大模型识别的字段中文名称在候选字段的中文名称的开头或结尾
                elif c_lower.startswith(name_lower) or c_lower.endswith(name_lower):
                    # 仅当「候选以用户输入开头或结尾」时采纳：比如用户输入XXX, 则[发现版本号XXX]、[XXX发现版本号] 采纳；[xxx发现版本号xxx] 不采纳
                    overlap = len(name_lower)
                    match_branch = "original_input_candidate_prefix_suffix_name"
                # 大模型解析出来的字段中文名称更长、候选的更短，比如大模型解析出来的是“发现版本”，候选是“版本”，并且原始语义中有“版本”则采纳
                elif name_lower.startswith(c_lower) or name_lower.endswith(c_lower):
                    # 用户输入以候选开头或结尾（如 发现版本号 以 版本号 结尾）：仅当 original_input 中含该候选时才采纳，避免短片段
                    if any((s or "").lower() in orig_lower for s in (n, nz, ne, k) if s):
                        overlap = len(c_lower)
                        match_branch = "original_input_name_prefix_suffix_candidate_in_text"
                #
                elif original_input and name_base and c_base and (name_base in c_base or c_base in name_base):
                    # 中间包含匹配（去后缀）：字段名与候选存在包含关系。是否在用户原文中出现（或候选与 field_recommended_names 推荐名一致）由下方统一过滤，此处不再重复判断。
                    overlap = len(name_base) if name_base in c_base else len(c_base)
                    match_branch = "original_input_middle_contains_stripped_in_text"
            if overlap <= 0:
                continue
            # logger.info(
            #     "【%s】前后匹配命中分支: branch=%s overlap=%s name=%r name_base=%r c=%r c_base=%r key=%r",
            #     scene,
            #     match_branch,
            #     overlap,
            #     name,
            #     name_base,
            #     c,
            #     c_base,
            #     k,
            # )
            match_with_len.append((k, overlap, c_lower, match_branch))
            break
    if not match_with_len:
        logger.info("【%s】前后匹配未命中: 字段名「%s」, name_base=%s", scene, name, name_base)
        return []
    # logger.info(
    #     "【%s】前后匹配初次命中列表: 字段名「%s」, name_base=%s, 条数=%s, 明细(key,overlap,c_lower,branch)=%s",
    #     scene,
    #     name,
    #     name_base,
    #     len(match_with_len),
    #     match_with_len,
    # )

    # 若提供了用户原始输入，仅采纳「匹配到的候选字段名在用户原始输入中包含」的项；【这个逻辑是有问题的，用户输入不完整时就存在大问题】
    # 对于通过去后缀得到的候选，也允许其去后缀形态出现在 original_input 中。FIELD_MATCH_EXCEPTIONS 中的字段不做此要求。
    if original_input and name.strip() not in exception_set:
        match_with_len = [
            (k, overlap, c_lower, br)
            # key 候选字段key
            # c_lower：这次命中的候选字段中文
            for (k, overlap, c_lower, br) in match_with_len
            # 只有候选字符串在原始用户输入中包含，或者候选字符串去掉后缀后在原始用户输入中包含，则采纳
            # 这个逻辑是有问题的，大模型给出的语义可能不准，或是用户表达的不准。应该能根据特定规则来猜测
            if (
                # 在原文中匹配到，则将候选字段采纳
                c_lower in orig_lower
                # 去掉特定后缀后在原文中匹配到，则将候选字段采纳
                or _strip_match_suffixes(c_lower) in orig_lower
                # name_lower是大模型识别的字段中文， c_lower是候选字段中文
                or _rule_based_text_allows_field_match(
                    workspace, work_item_type_key, name_lower, c_lower
                )
            )
        ]

        if not match_with_len:
            logger.info(
                "【%s】前后匹配后无候选名出现在用户输入中: 字段名「%s」, name_base=%s, 原文=%s",
                scene,
                name,
                name_base,
                original_input,
            )
            return []
        else:
            logger.info(
                "【%s】前后匹配经原文过滤后保留: 大模型识别字段名「%s」, 经过处理的字段名=%s, 条数=%s, 原文=%s, 明细(key,overlap,c_lower)=%s",
                scene,
                name,
                name_base,
                len(match_with_len),
                original_input,
                [(m[0], m[1], m[2], m[3]) for m in match_with_len],
            )

    # 取 overlap 最大者；FIELD_MATCH_BRANCHES_EXEMPT_FROM_OVERLAP_TIEBREAK 中的分支已在原文中出现候选，须一并保留
    max_overlap = max(m[1] for m in match_with_len)
    result = list(
        dict.fromkeys(
            m[0]
            for m in match_with_len
            if m[1] == max_overlap or m[3] in FIELD_MATCH_BRANCHES_EXEMPT_FROM_OVERLAP_TIEBREAK
        )
    )
    logger.info(
        "【%s】前后匹配: 字段名「%s」, name_base=%s -> 采纳的keys=%s, max_overlap=%s",
        scene,
        name,
        name_base,
        result,
        max_overlap,
    )
    return result


def _resolve_field_key_on_single_list(
    name: str,
    items: List[dict],
    headers: Optional[Dict[str, Any]],
    scene: str,
    workspace: Optional[str],
) -> Optional[Union[str, List[str]]]:
    """
    仅在一份字段列表上执行 resolve_field_key 的核心逻辑。
    返回：唯一 key（str）；未找到（None）；多条（非空 list，表示歧义，由外层抛错）。
    """
    if "iChange" in name:
        logger.info("name: %s", name)
    matches = _match_field_key(name, items)
    if "iChange" in name:
        logger.info("matches: %s", matches)
    if len(matches) == 1:
        logger.info("【%s】字段名「%s」精确匹配到 key: %s", scene, name, matches[0])
        return matches[0]
    if headers and _contains_chinese(name):
        suggested = _resolve_field_name_by_chat(name, headers, workspace=workspace or "PLM", scene=scene)
        if suggested and suggested != name:
            retry_matches = _match_field_key(suggested, items)
            if len(retry_matches) == 1:
                logger.info("【%s】Ai兜底后字段名「%s」->「%s」匹配到 key: %s", scene, name, suggested, retry_matches[0])
                return retry_matches[0]
    if not matches:
        rules = _get_field_fallback_rules()
        logger.info("【%s】Ai兜底规则数量: %d，规则: %s", scene, len(rules), rules)
        item_keys = {(str(it.get("key") or "").strip()) for it in (items or []) if isinstance(it, dict)}
        for rule in rules:
            contains = (rule.get("name_contains") or "").strip()
            map_key = (rule.get("map_to_key") or "").strip()
            if not contains or contains not in name:
                continue
            logger.info("【%s】兜底规则命中: 字段名「%s」含「%s」，map_to_key=%s，map_to_key 在 item_keys=%s", scene, name, contains, map_key, map_key in item_keys if map_key else "N/A")
            if map_key and map_key in item_keys:
                logger.info("【%s】从配置规则中兜底映射: 字段名「%s」含「%s」-> key: %s", scene, name, contains, map_key)
                return map_key
            candidates = []
            for it in (items or []):
                if not isinstance(it, dict):
                    continue
                k = (it.get("key") or "").strip()
                if not k:
                    continue
                disp = (str(it.get("name") or "") + str(it.get("nameZh") or "") + str(it.get("nameEn") or ""))
                if contains in disp:
                    candidates.append(k)
            candidates = list(dict.fromkeys(candidates))
            logger.info("【%s】兜底按显示名含「%s」匹配到 %d 个字段: %s", scene, contains, len(candidates), candidates[:10] if len(candidates) > 10 else candidates)
            if len(candidates) == 1:
                logger.info("【%s】从配置规则中兜底映射: 字段名「%s」含「%s」-> key: %s（按显示名匹配）", scene, name, contains, candidates[0])
                return candidates[0]
            if len(candidates) > 1 and map_key:
                preferred = [c for c in candidates if c == map_key or c.startswith(map_key + "_") or c.startswith(map_key + ".")]
                if preferred:
                    chosen = preferred[0] if len(preferred) == 1 else (map_key if map_key in preferred else preferred[0])
                    logger.info("【%s】从配置规则中兜底映射: 字段名「%s」含「%s」-> key: %s（多字段时按 map_to_key 前缀优选）", scene, name, contains, chosen)
                    return chosen
            if len(candidates) > 1:
                logger.info("【%s】兜底规则「%s」匹配到多字段 %s，不采用", scene, contains, candidates)
        return None
    return matches


# 字段解析，将字段的中文名称解析为key
def resolve_field_key(
    field_name: str,
    items: List[dict],
    headers: Optional[Dict[str, Any]] = None,
    raise_on_not_found: bool = True,
    scene: str = "字段映射",
    workspace: Optional[str] = None,
    field_items_fallback: Optional[List[dict]] = None,
) -> Optional[str]:
    """
    将字段名解析为唯一 key。
    会先做规范化：去前后空格与引号，工作项标识→标识。
    0 条或多条匹配时：若传入 headers 则请求 Studio Chat 兜底接口，用返回的 result 再匹配一次。
    仍未命中时：若配置了 common/field_resolver_config.json 的 fallback_map，则按「名称包含 → map_to_key」做兜底直接映射，且 map_to_key 须在 items 中存在。
    raise_on_not_found=True（默认）：未找到或对应多条时抛出 ValueError。
    raise_on_not_found=False：未找到时返回 None 并打日志跳过；对应多条时仍抛出 ValueError。
    scene：兜底日志场景标识，如「SELECT字段映射兜底」「WHERE字段映射兜底」「创建/更新工作项字段映射兜底」等。
    field_items_fallback：备选字段列表；仅在当前列表 items 上全流程未找到唯一 key 时，再在备选列表上重复同一套逻辑（不合并两份列表）。
    """
    name = _normalize_field_name_for_match(field_name)
    if not name:
        if raise_on_not_found:
            raise ValueError("字段名为空")
        return None

    lists_to_try: List[List[dict]] = [items or []]
    # 如果在items中找不到字段key，则去备选的字段列表找。只有查询场景需要从备选找。创建、更新只会在items中找
    # 查询场景下，items是当前工作区的字段，备选字段列表是跨工作区的字段。
    if field_items_fallback:
        lists_to_try.append(field_items_fallback)

    for idx, one_list in enumerate(lists_to_try):
        if not one_list:
            continue
        label = "主字段列表" if idx == 0 else "备选字段列表"
        # 在单个字段列表上执行 resolve_field_key 的核心逻辑
        out = _resolve_field_key_on_single_list(name, one_list, headers, scene, workspace)
        if isinstance(out, list):
            logger.warning("【%s】字段「%s」在%s对应多条 key: %s", scene, name, label, out)
            raise ValueError(f"字段「{field_name}」对应多条，请确认后重试：{', '.join(out)}")
        if out is not None:
            if idx == 1:
                logger.info("【%s】字段名「%s」在主列表未命中，由备选字段列表解析到 key: %s", scene, name, out)
            return out

    logger.warning("【%s】未找到匹配字段，已跳过: %s", scene, name)
    if raise_on_not_found:
        raise ValueError(f"未找到匹配字段：{field_name}")
    return None


# select items专用
def _resolve_select_list_on_single_list(
    name: str,
    items: List[dict],
    headers: Optional[Dict[str, Any]],
    original_input: Optional[str],
    workspace: Optional[str],
    work_item_type_key: Optional[str] = None,
) -> List[str]:
    """仅在一份字段列表上做 select 解析（精确 + 前后匹配；再无则 AI + 精确）。"""
    # 精确匹配
    matches_exact = _match_field_key(name, items)
    if matches_exact and not _contains_chinese(name):
        logger.info(
            "【SELECT字段映射】select 字段名「%s」非中文且精确匹配到 key: %s（直接采纳）",
            name,
            matches_exact,
        )
        return matches_exact
    # 前中后匹配
    matches_ps = _match_field_key_prefix_suffix(
        name,
        items,
        original_input=original_input,
        scene="SELECT字段映射",
        workspace=workspace,
        work_item_type_key=work_item_type_key,
    )
    matches = list(dict.fromkeys((matches_exact or []) + (matches_ps or [])))
    if matches:
        logger.info("【SELECT字段映射】select 字段名「%s」精确+前后匹配到 key: %s", name, matches)
        return matches
    if headers:
        suggested = _resolve_field_name_by_chat(name, headers, workspace=workspace or "PLM", scene="SELECT字段映射兜底")
        if suggested and (suggested or "").strip():
            suggested = (suggested or "").strip()
            matches = _match_field_key(suggested, items)
            if matches:
                logger.info("【SELECT字段映射】select 字段名「%s」经 AI 兜底「%s」匹配到 key: %s", name, suggested, matches)
                return matches
    return []


# select items专用
def resolve_field_key_for_select_list(
    field_name: str,
    items: List[dict],
    headers: Optional[Dict[str, Any]] = None,
    raise_on_not_found: bool = False,
    original_input: Optional[str] = None,
    workspace: Optional[str] = None,
    field_items_fallback: Optional[List[dict]] = None,
    work_item_type_key: Optional[str] = None,
) -> List[str]:
    """
    select 列表专用：按顺序解析字段名 → key 列表。
    1）从 field_items 精确匹配到则采纳；
    2）再通过前后匹配（候选名在 original_input 中出现）到则采纳；
    3）都未找到则调 AI 解析，AI 返回的规范名再走 1）2）匹配，找到则采纳。
    返回 key 列表（可能 0 个、1 个或多个）。
    field_items_fallback：备选字段列表；仅在主列表上全流程未匹配到任何 key 时，再在备选列表上重复同一套逻辑（不合并）。
    work_item_type_key：可选；传入时参与 field_recommended_names 规则匹配。
    """
    name = _normalize_field_name_for_match(field_name)
    if not name:
        if raise_on_not_found:
            raise ValueError("字段名为空")
        return []

    lists_to_try: List[List[dict]] = [items or []]
    if field_items_fallback:
        lists_to_try.append(field_items_fallback)

    for idx, one_list in enumerate(lists_to_try):
        if not one_list:
            continue
        keys = _resolve_select_list_on_single_list(
            name, one_list, headers, original_input, workspace, work_item_type_key
        )
        if keys:
            if idx == 1:
                logger.info("【SELECT字段映射】字段名「%s」在主列表未命中，由备选字段列表匹配到 key: %s", name, keys)
            return keys

    logger.info("【SELECT字段映射】select 字段名「%s」未匹配到 key", name)
    if raise_on_not_found:
        raise ValueError(f"【SELECT字段映射】未找到匹配字段：{field_name}")
    return []

# where 中条件字段解析专用
def _resolve_where_on_single_list(
    name: str,
    items: List[dict],
    headers: Optional[Dict[str, Any]],
    original_input: Optional[str],
    workspace: Optional[str],
    field_name_for_log: str,
    work_item_type_key: Optional[str] = None,
) -> List[str]:
    """仅在一份字段列表上解析 WHERE 字段名 → key 列表（可能空、单元素或多元素）。"""
    # 精确匹配（非中文时优先直接采纳）
    matches_exact = _match_field_key(name, items)
    if matches_exact and not _contains_chinese(name):
        logger.info(
            "【WHERE字段映射】where 字段名「%s」非中文且精确匹配到 key: %s（直接采纳）",
            field_name_for_log,
            matches_exact,
        )
        return matches_exact
    # 只有包含中文时才不可信，因为大模型识别的字段名可能不准，需要纠正
    matches = _match_field_key_prefix_suffix(
        name,
        items,
        original_input=original_input,
        scene="WHERE字段映射",
        workspace=workspace,
        work_item_type_key=work_item_type_key,
    )
    if not matches and headers:
        suggested = _resolve_field_name_by_chat(name, headers, workspace=workspace or "PLM", scene="WHERE字段映射兜底")
        if suggested:
            logger.info("【WHERE字段映射兜底】Studio Chat 返回规范字段名: %s -> %s", field_name_for_log, suggested)
            matches = _match_field_key(suggested, items) or _match_field_key(name, items)
        else:
            matches = _match_field_key(name, items)
    return matches


# where conditions专用，用于where条件中的字段解析，不是value解析
def resolve_field_key_for_where(
    field_name: str,
    items: List[dict],
    headers: Optional[Dict[str, Any]] = None,
    original_input: Optional[str] = None,
    workspace: Optional[str] = None,
    field_items_fallback: Optional[List[dict]] = None,
    work_item_type_key: Optional[str] = None,
) -> Optional[Union[str, List[str]]]:
    """
    精确匹配一律采纳；前后模糊匹配仅当候选字段名在 original_input 中出现时采纳；
    例外：FIELD_MATCH_EXCEPTIONS 中的字段禁止前后匹配。
    用于 where 条件解析：传入原始 conditions 或用户话术为 original_input。
    返回：1 个 key 时返回 str；多个 key 时返回 List[str]；0 个时返回 None。
    field_items_fallback：备选字段列表；仅在主列表上全流程未匹配到 key 时，再在备选列表上重复同一套逻辑（不合并）。
    work_item_type_key：可选；传入时参与 field_recommended_names 规则匹配。
    """
    logger.info("【字段映射】开始解析 WHERE 字段: %s", field_name)
    name = _normalize_field_name_for_match(field_name)
    if not name:
        return None

    lists_to_try: List[List[dict]] = [items or []]
    if field_items_fallback:
        # 从备选字段列表中再次解析
        lists_to_try.append(field_items_fallback)

    for idx, one_list in enumerate(lists_to_try):
        if not one_list:
            continue
        #
        matches = _resolve_where_on_single_list(
            name,
            one_list,
            headers,
            original_input,
            workspace,
            field_name,
            work_item_type_key,
        )
        if not matches:
            continue
        if len(matches) == 1:
            if idx == 1:
                logger.info("【WHERE字段映射】WHERE 字段名「%s」在主列表未命中，由备选列表解析到唯一 key: %s", name, matches[0])
            else:
                logger.info("【WHERE字段映射】WHERE 字段名「%s」解析到唯一 key: %s", name, matches[0])
            return matches[0]
        if idx == 1:
            logger.info("【WHERE字段映射】WHERE 字段名「%s」在主列表未命中，备选列表匹配到多 key: %s", name, matches)
        else:
            logger.info("【WHERE字段映射】WHERE 字段名「%s」前后匹配到多 key: %s，将展开为 OR 条件", name, matches)
        return matches

    logger.info("【WHERE字段映射】WHERE 前后匹配/精确/AI 兜底在主列表与备选列表均未找到: %s", name)
    return None


