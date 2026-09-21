#!/usr/bin/env python
"""
工作项类型解析：类型名称 → key、根 key → 有效模型 key。
供查询/创建/更新等脚本统一使用，禁止在各 atomic 脚本内重复实现。
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from . import rdcloud_api
from .log_helper import get_logger

logger = get_logger(__name__)

# field_work_item_type_resolver_config.json：field_work_item_type_recommended_names 规则缓存
_field_wit_recommended_rules_cache: Optional[List[Dict[str, Any]]] = None
_WIT_RULE_RESERVED_KEYS = frozenset({"workspace"})


def _get_field_work_item_type_recommended_rules() -> List[Dict[str, Any]]:
    """读取 common/field_work_item_type_resolver_config.json 中 field_work_item_type_recommended_names。"""
    global _field_wit_recommended_rules_cache
    if _field_wit_recommended_rules_cache is not None:
        return _field_wit_recommended_rules_cache
    try:
        p = Path(__file__).resolve().parent / "field_work_item_type_resolver_config.json"
        if not p.exists():
            _field_wit_recommended_rules_cache = []
            return []
        with open(p, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        raw = cfg.get("field_work_item_type_recommended_names") or []
        if not isinstance(raw, list):
            _field_wit_recommended_rules_cache = []
            return []
        _field_wit_recommended_rules_cache = [x for x in raw if isinstance(x, dict)]
        return _field_wit_recommended_rules_cache
    except Exception as e:
        logger.debug("加载 field_work_item_type_resolver_config.json 失败: %s", e)
        _field_wit_recommended_rules_cache = []
        return []


def _wit_rule_name_mappings(rule: Dict[str, Any]) -> Dict[str, List[str]]:
    """规则中除 workspace 外：源类型名称 → 待补充类型名称列表。"""
    out: Dict[str, List[str]] = {}
    for k, v in rule.items():
        if k in _WIT_RULE_RESERVED_KEYS:
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

# source_name: 大模型识别的工作项类型中文名
# 返回补充的工作项类型中文名称
def get_supplementary_work_item_type_names(
    workspace: str,
    source_name: str,
) -> List[str]:
    """
    按配置为「已解析成功」的源类型名补充关联类型名称（仍为中文名，由上层再调接口解析为 key）。
    匹配优先级：① 规则含 workspace 且与当前工作区一致 + 源名称键；② 规则不含 workspace + 源名称键（全局兜底）。
    """
    # 如果用户语义中没有提到工作项查询的类型名称，则不进行补充
    sn = (source_name or "").strip()
    if not sn:
        return []
    # 用户语义中提到了工作项查询的类型名称，并且命中了规则，比如提到故障，则将故障补充为缺陷、变更请求    
    ws = (workspace or "").strip()
    rules = _get_field_work_item_type_recommended_rules()

    for rule in rules:
        rw = (rule.get("workspace") or "").strip()
        if not rw or rw != ws:
            continue
        m = _wit_rule_name_mappings(rule)
        if sn in m:
            return list(m[sn])


    for rule in rules:
        # 跳过有工作区要求的规则
        if (rule.get("workspace") or "").strip():
            continue
        #
        m = _wit_rule_name_mappings(rule)
        if sn in m:
            return list(m[sn])

    return []

# source_name 大模型识别的工作项类型中文名
# resolved 将大模型识别的工作项类型中文名source_name转换为key
# key_name_map是 resolved对应的 {key: 显示名} 映射
# extra_names是补充的工作项类型中文名称。如果根据extra_names补充规则查询到工作项类型名转换为key，则需要将这些key合并到resolved中
# 返回值：(keys, key_name_map)：
#   - keys: str（单个 key）或 list[str]（多个 key）
#   - key_name_map: {key: 显示名} 映射，便于上层记录日志或做展示
def supplement_resolved_work_item_type_keys(
    source_name: str,
    workspace: str,
    headers: Dict[str, Any],
    resolved: Union[str, List[str]],
    key_name_map: Dict[str, str],
    extra_names: Optional[List[str]] = None,
) -> Tuple[Union[str, List[str]], Dict[str, str]]:
    """
    在 resolve_work_item_type_key 已成功返回后，按 field_work_item_type_recommended_names 将关联类型 key 合并进结果（去重保序）。
    extra_names：若调用方已从 c.get(\"value\") 对应字符串查过规则，可传入以避免重复查询；为 None 时内部再查一次。
    """
    # 不存在要补充的工作项类型名，则根据source_name再执行一次补充规则，实际上应该走不到此分支
    if extra_names is None:
        # 获取补充的工作项类型中文名称
        extra_names = get_supplementary_work_item_type_names(workspace, source_name)
    # 不存在要补充的工作项类型名，则返回原始的    
    if not extra_names:
        return resolved, dict(key_name_map or {})
    merged_km = dict(key_name_map or {})
    keys_ordered: List[str] = []
    seen = set()

    def _add_key(k: str) -> None:
        k = str(k).strip()
        if not k or k in seen:
            return
        seen.add(k)
        keys_ordered.append(k)

    for k in (resolved if isinstance(resolved, list) else [resolved]):
        _add_key(str(k).strip())
    #    
    for en in extra_names:
        try:
            r2, km2 = resolve_work_item_type_key(en, workspace, headers)
            merged_km.update(km2 or {})
            for k in (r2 if isinstance(r2, list) else [r2]):
                _add_key(str(k).strip())
        except Exception as e:
            logger.warning(
                "【工作项类型补充】解析推荐类型「%s」失败（跳过）: %s", en, e
            )
    if len(keys_ordered) == 1:
        return keys_ordered[0], merged_km
    return keys_ordered, merged_km


def _contains_chinese(s: str) -> bool:
    """判断字符串是否包含中文字符。"""
    if not s:
        return False
    for c in s:
        if "\u4e00" <= c <= "\u9fff" or "\u3400" <= c <= "\u4dbf":
            return True
    return False


def _fuzzy_match_work_item_type(table_name: str, item: dict) -> bool:
    """根据 name/nameZh/nameEn 做模糊匹配：相等或包含即视为匹配。"""
    if not isinstance(item, dict):
        return False
    t = (table_name or "").strip()
    if not t:
        return False
    name = (item.get("name") or "").strip()
    name_zh = (item.get("nameZh") or "").strip()
    name_en = (item.get("nameEn") or "").strip()
    return (
        t == name or t == name_zh or t == name_en
        or (name and (t in name or name in t))
        or (name_zh and (t in name_zh or name_zh in t))
        or (name_en and (t in name_en or name_en in t))
    )


def _resolve_work_item_type_key_by_root_types(
    table_name: str,
    workspace: str,
    headers: Dict[str, Any],
) -> List[str]:
    """
    备用：调用 work_item_root_types 接口，根据列表中的 name/nameZh/nameEn 做模糊匹配。
    返回匹配到的 key 列表（可能 0 个、1 个或多个）。
    """
    name = (table_name or "").strip()
    items = rdcloud_api.fetch_work_item_root_types(workspace, headers)
    if not isinstance(items, list):
        return []
    keys = []
    for it in items:
        if _fuzzy_match_work_item_type(name, it):
            k = (it.get("key") or "").strip()
            if k:
                keys.append(k)
    return keys

# 如果查询场景、导出场景时，解析工作项类型时还可以应用特殊规则，比如将故障 映射为缺陷
# 解析工作项类型中文转换为key
def resolve_work_item_type_key(
    table_name: str,
    workspace: str,
    headers: Dict[str, Any],
) -> Tuple[Union[str, List[str]], Dict[str, str]]:
    """
    通过工作项类型 key 接口解析表名（工作项类型）为 key。
    表名若为逗号分隔的多个（如「变更请求,缺陷,外场故障」），会拆成数组请求接口。
    先调 workItemType/keys；若返回的 keys 不存在、不是列表或不止一个，则走备用接口 work_item_root_types，
    按 name/nameZh/nameEn 模糊匹配，匹配到多个时返回多值。
    兜底：若仍未匹配到且表名不含中文，则直接使用表名作为 key。
    返回：(keys, key_name_map)：
      - keys: str（单个 key）或 list[str]（多个 key）
      - key_name_map: {key: 显示名} 映射，便于上层记录日志或做展示
    """
    raw = (table_name or "").strip()
    if not raw:
        raise ValueError("表名（工作项类型）不能为空")
    names = [s.strip() for s in raw.split(",") if s.strip()]
    if not names:
        raise ValueError("表名（工作项类型）不能为空")
    name = names[0] if len(names) == 1 else raw
    logger.info("【工作项类型解析】正在解析: %s，请求接口获取 key", names if len(names) > 1 else name)

    type_names = names if len(names) > 1 else [name]
    body = type_names if len(type_names) > 1 else [type_names[0]]
    # 在当前工作区下，根据工作项类型中文名称模糊获取工作项类型key
    bo = rdcloud_api.fetch_work_item_type_keys(workspace, body, headers)
    if not isinstance(bo, dict):
        bo = {}

    # 用户语义中涉及到多个工作项类型的中文名称，比如查询市场需求、用户故事    
    if len(names) > 1:
        result_keys: List[str] = []
        # key_name_map: {key: 显示名} 映射，便于上层记录日志或做展示
        key_name_map: Dict[str, str] = {}
        for n in names:
            val = bo.get(n)
            # 新结构：val 为 {key: 显示名}，取第一个 key；若为空则走备用接口
            if isinstance(val, dict) and val:
                logger.info("【工作项类型解析】表名「%s」接口返回候选 key→名称: %s", n, val)
                k = next(iter(val.keys()))
                logger.info("【工作项类型解析】表名「%s」选用 key=%s, name=%s", n, k, val.get(k, ""))
                result_keys.append(k)
                key_name_map[k] = str(val.get(k, "") or "")
            else:
                logger.info("【工作项类型解析】首接口未命中，走备用接口 work_item_root_types(获取根工作区下的工作项类型) 模糊匹配")
                fallback = _resolve_work_item_type_key_by_root_types(n, "ZTE", headers)
                if fallback:
                    ks = fallback if isinstance(fallback, list) else [fallback]
                    result_keys.extend(ks)
                    for k in ks:
                        key_name_map[str(k)] = key_name_map.get(str(k), "")
                elif not _contains_chinese(n):
                    logger.info("【工作项类型解析】表名「%s」未匹配到，且不含中文，兜底直接使用", n)
                    result_keys.append(n)
                    key_name_map[n] = n
                else:
                    raise ValueError(f"未找到工作项类型：{n}，请确认表名后重试")
        logger.info("【工作项类型解析】多表名 %s 匹配到 key: %s", names, result_keys)
        return (result_keys, key_name_map)

    val = bo.get(name)
    # 新结构：val 应为 {key: 显示名}
    if not isinstance(val, dict) or not val:
        logger.info("【工作项类型解析】首接口未命中，走备用接口 work_item_root_types(获取根工作区下的工作项类型)  模糊匹配")
        keys = _resolve_work_item_type_key_by_root_types(name, "ZTE", headers)
        if not keys:
            if not _contains_chinese(name):
                logger.info("【工作项类型解析】表名「%s」未匹配到，且不含中文，兜底直接使用", name)
                return (name, {name: name})
            raise ValueError(f"未找到工作项类型：{name}，请确认表名后重试")
        key_name_map_fallback = {str(k): "" for k in (keys if isinstance(keys, list) else [keys])}
        if isinstance(keys, list) and len(keys) == 1:
            logger.info("【工作项类型解析】表名「%s」备用匹配到 key: %s", name, keys[0])
            return (keys[0], key_name_map_fallback)
        logger.info("【工作项类型解析】表名「%s」备用匹配到多 key: %s", name, keys)
        return (keys, key_name_map_fallback)

    keys_dict = val
    logger.info("【工作项类型解析】表名「%s」接口返回 key→名称: %s", name, keys_dict)
    key_name_map = {str(k): str(v or "") for k, v in keys_dict.items()}
    if len(keys_dict) == 1:
        key = next(iter(keys_dict.keys()))
        logger.info("【工作项类型解析】表名「%s」从接口匹配到唯一 key=%s, name=%s", name, key, keys_dict.get(key, ""))
        return (key, key_name_map)

    # 多个 key 时返回 key 列表，由上层根据场景决定如何处理
    candidate_keys = list(keys_dict.keys())
    logger.info("【工作项类型解析】表名「%s」接口返回多个 key: %s，对应名称: %s", name, candidate_keys, keys_dict)
    return (candidate_keys, key_name_map)


def resolve_valid_work_item_type_key_from_root(
    root_key: str,
    workspace: str,
    headers: Dict[str, Any],
) -> str:
    """
    根据工作项类型根 key 解析出该工作区下有效（valid=true）的模型 key。
    用于创建工作项时：入参为根 key（如 Task）时，返回实际可用的 workItemTypeKey。
    若未找到 valid=true 的项，返回原 root_key。
    """
    root_key = (root_key or "").strip()
    if not root_key:
        return root_key
    logger.info("【工作项类型解析】根 key -> 有效模型 key: %s, workspace=%s", root_key, workspace)
    items = rdcloud_api.fetch_work_item_types_by_root_key(workspace, root_key, headers)
    if not isinstance(items, list):
        logger.warning("【工作项类型解析】接口未返回列表，使用原值: %s", root_key)
        return root_key
    for item in items:
        if isinstance(item, dict) and item.get("valid") is True:
            k = (item.get("key") or "").strip()
            if k:
                logger.info("【工作项类型解析】找到有效 key: %s -> %s", root_key, k)
                return k
    logger.warning("【工作项类型解析】未找到 valid=true 的项，使用原值: %s", root_key)
    return root_key
