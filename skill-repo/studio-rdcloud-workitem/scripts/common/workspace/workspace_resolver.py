#!/usr/bin/env python
"""
工作区解析：根据用户指定关键字搜索工作区，或取最近访问工作区。
- 用户指明工作区时：调用「根据名称或 Key 模糊搜索工作区」接口（strSearchName=用户指定），有结果则取精准匹配或第一个，并写回 config.json。
- 接口结果为空或用户未指明时：调用「查询用户最近访问工作区」接口，有结果取第一个并写回 config.json；无则提示用户指明工作区。
"""

import json
import logging
import requests
from pathlib import Path
from typing import Tuple, Optional, List, Union, Dict, Any

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent.parent.parent
CONFIG_FILE = SKILL_DIR / "assets" / "config.json"
AI_RULES_FILE = SCRIPT_DIR / "workspace_ai_rules.json"

from ..log_helper import get_logger
from ..rdcloud_api import URL_QUERY_WORKSPACE_ALL, URL_QUERY_WORKSPACE_ACCESS_LOG, WORKSPACE_RESOLVE_AI_URL

logger = get_logger(__name__)

_ai_rules_cache: Optional[Dict[str, Any]] = None


def _load_workspace_ai_rules() -> Dict[str, Any]:
    """加载 AI 工作区采纳规则（workspace_ai_rules.json）。失败时返回空规则。"""
    global _ai_rules_cache
    if _ai_rules_cache is not None:
        return _ai_rules_cache
    try:
        if not AI_RULES_FILE.exists():
            _ai_rules_cache = {}
            return {}
        data = json.loads(AI_RULES_FILE.read_text(encoding="utf-8"))
        _ai_rules_cache = data if isinstance(data, dict) else {}
        return _ai_rules_cache
    except Exception as e:
        logger.warning("加载 workspace_ai_rules.json 失败，忽略规则: %s", e)
        _ai_rules_cache = {}
        return {}


def _rule_workspace_raw_match_map() -> Dict[str, List[str]]:
    """
    从 workspace_ai_rules.json 读取 key -> raw 命中词列表 的映射。

    规则格式：
    {
      "require_key_in_raw_for_keys": [
        { "PLM": ["PLM", "产品研发"] },
        { "DT_ITOW": ["DT_ITOW", "ITOW"] }
      ]
    }

    含义：AI 返回的 workspace key 若命中该映射，则只有当用户原始语义 raw 命中映射词任一项时才采纳。
    """
    rules = _load_workspace_ai_rules()
    raw_rules = rules.get("require_key_in_raw_for_keys") or []
    if not isinstance(raw_rules, list):
        return {}
    out: Dict[str, List[str]] = {}
    for it in raw_rules:
        if not isinstance(it, dict):
            continue
        for k, v in it.items():
            kk = (str(k).strip() if k is not None else "")
            if not kk:
                continue
            if isinstance(v, list):
                tokens = [str(x).strip() for x in v if x and str(x).strip()]
            elif isinstance(v, str) and v.strip():
                tokens = [v.strip()]
            else:
                tokens = []
            if tokens:
                out[kk] = tokens
    return out


def _raw_contains(raw: str, needle: str) -> bool:
    if not raw or not needle:
        return False
    return needle.lower() in raw.lower()

def _raw_contains_any(raw: str, needles: List[str]) -> bool:
    if not raw or not needles:
        return False
    raw_preview = raw[:200] + ("..." if len(raw) > 200 else "")
    for n in needles:
        if _raw_contains(raw, n):
            logger.info(
                "工作区 AI 规则命中: token=%r, raw_preview=%r",
                n,
                raw_preview,
            )
            return True
    logger.info(
        "工作区 AI 规则未命中任何 token: tokens=%s, raw_preview=%r",
        needles,
        raw_preview,
    )
    return False


def _raw_mentions_workspace(raw: str, *, key: str, name: str, require_key: bool) -> bool:
    """
    判断 AI 解析到的 workspace 是否可被原始语义采纳：
    - 默认：raw 中命中 key 或 name 任一即可；
    - require_key=True：必须命中 key（name 命中不够）。
    """
    r = (raw or "").strip()
    k = (key or "").strip()
    n = (name or "").strip()
    if not r or not k:
        return False
    if require_key:
        return _raw_contains(r, k)
    return _raw_contains(r, k) or (n and _raw_contains(r, n))


def _normalize(s: str) -> str:
    return (s or "").strip()


def query_workspaces_by_name(keyword: str, headers: dict) -> List[dict]:
    """
    根据名称或 Key 模糊搜索工作区。请求体 strSearchName=keyword。
    :param keyword: 搜索关键字（名称或 Key）
    :param headers: 请求头（X-Tenant-Id, X-Auth-Value, X-Emp-No, Content-Type）
    :return: workSpaceDetailList 列表，每项含 strSpaceKey、strSpaceName 等；失败或无结果返回 []
    """
    if not _normalize(keyword):
        return []
    url = URL_QUERY_WORKSPACE_ALL
    body = {"strSearchName": keyword.strip(), "pageNo": 1, "pageSize": 30}
    h = {
        "Content-Type": "application/json",
        "X-Tenant-Id": headers.get("X-Tenant-Id", "ZTE"),
        "X-Auth-Value": headers.get("X-Auth-Value", ""),
        "X-Emp-No": headers.get("X-Emp-No", ""),
    }
    logger.info("调用接口 根据名称或Key模糊搜索工作区: url=%s strSearchName=%s pageNo=1 pageSize=30", url, body["strSearchName"])
    try:
        r = requests.post(url, headers=h, json=body, timeout=30)
        logger.info("queryWorkSpaceAll 响应 status=%s", r.status_code)
        r.raise_for_status()
        data = r.json()
        code = data.get("code")
        if code != "1":
            logger.warning("queryWorkSpaceAll 返回 code 非 1: code=%s", code)
            return []
        bo = data.get("bo") or {}
        lst = bo.get("workSpaceDetailList")
        if not isinstance(lst, list):
            logger.info("queryWorkSpaceAll workSpaceDetailList 为空或非列表")
            return []
        logger.info("queryWorkSpaceAll 返回 workSpaceDetailList 条数=%s", len(lst))
        return lst
    except Exception as e:
        logger.warning("queryWorkSpaceAll 请求异常: %s", e)
        return []


def query_recent_workspaces(headers: dict) -> List[dict]:
    """
    查询用户最近访问的工作区列表。请求方法：GET（接口文档规定）。
    :param headers: 请求头
    :return: workSpaceAccessLogList 列表；失败或无结果返回 []
    """
    url = URL_QUERY_WORKSPACE_ACCESS_LOG
    h = {
        "X-Tenant-Id": headers.get("X-Tenant-Id", "ZTE"),
        "X-Auth-Value": headers.get("X-Auth-Value", ""),
        "X-Emp-No": headers.get("X-Emp-No", ""),
    }
    logger.info("调用接口 查询用户最近访问工作区: method=GET url=%s", url)
    try:
        r = requests.get(url, headers=h, timeout=30)
        logger.info("queryWorkSpaceAccessLog 响应 status=%s", r.status_code)
        r.raise_for_status()
        data = r.json()
        code_obj = data.get("code")
        if isinstance(code_obj, dict) and code_obj.get("code") != "0000":
            logger.warning("queryWorkSpaceAccessLog 返回 code 非 0000: code=%s", code_obj)
            return []
        bo = data.get("bo") or {}
        lst = bo.get("workSpaceAccessLogList")
        if not isinstance(lst, list):
            logger.info("queryWorkSpaceAccessLog workSpaceAccessLogList 为空或非列表")
            return []
        logger.info("queryWorkSpaceAccessLog 返回 workSpaceAccessLogList 条数=%s", len(lst))
        return lst
    except Exception as e:
        logger.warning("queryWorkSpaceAccessLog 请求异常: %s", e)
        return []


def _pick_from_search_list(keyword: str, items: List[dict]) -> Optional[str]:
    """
    从搜索结果中选取工作区 Key：仅当 strSpaceKey 或 strSpaceName 与 keyword 精确匹配时返回；
    匹配不到则返回 None，由调用方继续后续逻辑。
    """
    if not items or not _normalize(keyword):
        return None
    kw = _normalize(keyword)
    kw_lower = kw.lower()
    for it in items:
        key = _normalize((it.get("strSpaceKey") or ""))
        name = _normalize((it.get("strSpaceName") or ""))
        if key.lower() == kw_lower or name == kw or key == kw or name.lower() == kw_lower:
            logger.info("工作区精确匹配: keyword=%s -> strSpaceKey=%s", keyword, key)
            return key
    logger.info("工作区无精确匹配，不取第一个: keyword=%s", keyword)
    return None


def _recent_to_candidate_text(recent: List[dict]) -> str:
    """将最近访问工作区列表格式化为候选文本，格式：key: X, name: Y 每行一个。"""
    if not recent:
        return ""
    lines = []
    for w in recent:
        if not isinstance(w, dict):
            continue
        key = (w.get("strSpaceKey") or "").strip()
        name = (w.get("strSpaceName") or "").strip()
        if key:
            lines.append(f"key: {key}, name: {name}")
    return "\n".join(lines)

def _recent_to_key_name_map(recent: List[dict]) -> Dict[str, str]:
    """从最近访问工作区构造 key->name 映射。"""
    m: Dict[str, str] = {}
    if not recent:
        return m
    for w in recent:
        if not isinstance(w, dict):
            continue
        key = (w.get("strSpaceKey") or "").strip()
        name = (w.get("strSpaceName") or "").strip()
        if key and key not in m:
            m[key] = name
    return m


def _build_workspace_candidate_text() -> Tuple[str, Dict[str, str]]:
    """从 workspace_top_100.WORKSPACES 组装候选工作区列表文本，并返回 key->name 映射。"""
    try:
        from .workspace_top_100 import WORKSPACES
    except ImportError:
        logger.warning("无法导入 workspace_top_100.WORKSPACES，使用空候选列表")
        return "", {}
    if not isinstance(WORKSPACES, list):
        return "", {}
    lines = []
    km: Dict[str, str] = {}
    for w in WORKSPACES:
        if not isinstance(w, dict):
            continue
        key = (w.get("workspace_key") or w.get("strSpaceKey") or "").strip()
        name = (w.get("name") or w.get("name_en") or w.get("strSpaceName") or "").strip()
        if key:
            lines.append(f"key: {key}, name: {name}")
            if key not in km:
                km[key] = name
    return "\n".join(lines), km


def _resolve_workspace_by_ai(raw_input: str, candidate_text: str, headers: dict, workspace_for_url: str = "PLM") -> Optional[Union[str, List[str]]]:
    """
    调用 AI 接口解析工作区。请求体 type=workspace，input 为 Raw Input + Candidate Workspace List。
    workspace_for_url：请求 URL 中的工作区参数，解析工作区时尚未确定工作区，默认 PLM。
    返回：解析到的工作区 key，单个为 str、多个为 List[str]；未找到/接口失败/原始输入为空时返回 None。
    """
    if not candidate_text.strip():
        logger.info("工作区 AI 解析：候选列表为空，跳过")
        return None
    raw = (raw_input or "").strip()
    if not raw:
        logger.info("工作区 AI 解析：原始输入为空，跳过")
        return None
    input_body = (
        "**Raw Input**\n\n" + raw + "\n**Candidate Workspace List**\n\n```\n" + candidate_text.strip() + "\n```"
    )
    payload = {"type": "workspace", "input": input_body}
    h = {
        "Content-Type": "application/json",
        "X-Tenant-Id": headers.get("X-Tenant-Id", "ZTE"),
        "X-Auth-Value": headers.get("X-Auth-Value", ""),
        "X-Emp-No": headers.get("X-Emp-No", ""),
        "X-Lang-Id": headers.get("X-Lang-Id", "zh_CN"),
    }
    url = WORKSPACE_RESOLVE_AI_URL.format(workspace=workspace_for_url or "PLM")
    try:
        input_raw = payload.get("input") or ""
        logger.info("调用工作区 AI 解析: url=%s, 请求参数 type=%s, input 长度=%s, input 预览(原始)=%s", url, payload.get("type"), len(input_raw), repr(input_raw))
        r = requests.post(url, headers=h, json=payload, timeout=15)
        r.raise_for_status()
        data = r.json()
        code = data.get("code") or {}
        if isinstance(code, dict) and code.get("code") != "0000":
            logger.warning("工作区 AI 解析返回非 0000: %s", code)
            return None
        bo = data.get("bo") or {}
        result = (bo.get("result") or "").strip()
        #logger.info("result: %s", result)
        if not result or "未找到语义相近的工作区" in result:
            logger.info("工作区 AI 解析：未找到语义相近的工作区或结果为空")
            return None
        keys = [k.strip() for k in result.split("\n") if k.strip()]
        if keys:
            logger.info("工作区 AI 解析结果: %s", keys)
            return keys[0] if len(keys) == 1 else keys
        return None
    except Exception as e:
        logger.warning("工作区 AI 解析请求异常: %s", e)
        return None


def resolve_workspace_with_source(
    user_workspace_keyword: Optional[str],
    config_workspace: Optional[str],
    headers: dict,
    raw_user_input: Optional[str] = None,
) -> Tuple[Optional[Union[str, List[str]]], Optional[str], Optional[str]]:
    """
    解析本次查询使用的工作区 Key，并返回来源信息：
    - 用户指明工作区：调用搜索接口（strSearchName=用户指定），有结果则**仅精确匹配**（strSpaceKey/strSpaceName 与 keyword 一致），source=\"user_input\"；
      匹配不到则继续。
    - 用户未指明或搜索无结果：调用最近访问接口，拿到列表后若原始输入非空则调用 AI 接口（候选列表来自 workspace_top_100.WORKSPACES + 用户输入），
      解析出工作区 key 列表（AI 返回几个就返回几个），source=\"ai\"；若原始输入为空、AI 未返回或返回「未找到语义相近的工作区」，则取最近访问第一个，source=\"recent\"。

    :param user_workspace_keyword: 用户指明的工作区（名称或 Key），可为 None 或空
    :param config_workspace: 预留，当前未使用
    :param headers: 认证等请求头
    :param raw_user_input: 用户原始输入（可选），供 AI 解析时使用；不传则用 user_workspace_keyword；为空时跳过 AI 解析
    :return: (workspace_key 或 workspace_key 列表, source, error_message)，成功时 error_message 为 None，source 取值：
             - \"user_input\"：来自用户显式指定
             - \"ai\"：来自 AI 语义解析
             - \"recent\"：来自最近访问工作区
             - None：未解析成功
    """
    # 1. 用户指明了工作区 → 调用搜索接口，有结果则仅精确匹配
    if user_workspace_keyword and _normalize(user_workspace_keyword):
        list_from_search = query_workspaces_by_name(user_workspace_keyword, headers)
        if list_from_search:
            picked = _pick_from_search_list(user_workspace_keyword, list_from_search)
            if picked:
                logger.info("因用户指明工作区返回: keyword=%s, picked=%s", user_workspace_keyword, picked)
                return (picked, "user_input", None)
        logger.info("用户指明工作区但搜索无精确匹配或结果为空，继续后续逻辑: keyword=%s", user_workspace_keyword)

    # 2. 查询最近访问工作区
    recent = query_recent_workspaces(headers)
    # 2.1 若有 recent，将 recent + candidate_text 合并后调用 AI 解析；解析到则返回
    if recent:
        recent_text = _recent_to_candidate_text(recent)
        recent_km = _recent_to_key_name_map(recent)
        candidate_text, candidate_km = _build_workspace_candidate_text()
        merged_candidates = (recent_text + "\n" + candidate_text).strip() if candidate_text else recent_text
        raw_for_ai = (raw_user_input or user_workspace_keyword or "").strip()
        ai_keys = _resolve_workspace_by_ai(raw_for_ai, merged_candidates, headers)
        if ai_keys:
            # 规则：AI 解析到的工作区必须在用户原始语义中命中（key 或 name）才采纳；
            # 若命中 workspace_ai_rules.json 的映射规则，则必须命中映射词任一项。
            km_all: Dict[str, str] = {}
            km_all.update(candidate_km or {})
            km_all.update(recent_km or {})
            raw_match_map = _rule_workspace_raw_match_map()
            raw_ref = raw_for_ai
            keys_in = ai_keys if isinstance(ai_keys, list) else [ai_keys]
            accepted: List[str] = []
            for k in keys_in:
                kk = (str(k).strip() if k is not None else "")
                if not kk:
                    continue
                name = (km_all.get(kk) or "").strip()
                # 规则优先：命中规则 key 时，按映射词判断；否则按 key/name 命中判断
                tokens = raw_match_map.get(kk) or []
                if tokens:
                    ok = _raw_contains_any(raw_ref, tokens)
                else:
                    # 未命中任何规则：直接采纳，不再做 raw_contains(key/name) 判断
                    ok = True
                if ok:
                    accepted.append(kk)
                else:
                    logger.info(
                        "AI 工作区未被原始语义命中，已拒绝: key=%s, name=%r, rule_tokens=%s, raw=%r",
                        kk,
                        name,
                        tokens,
                        raw_ref,
                    )
            accepted = [k for i, k in enumerate(accepted) if k and k not in accepted[:i]]
            if accepted:
                out = accepted[0] if len(accepted) == 1 else accepted
                logger.info("使用 AI 解析的工作区（经语义命中规则过滤）: %s", out)
                return (out, "ai", None)
    # 2.2 继续：若有 recent 则取第一个
    if recent:
        first_key = (recent[0].get("strSpaceKey") or "").strip()
        if first_key:
            logger.info("AI 未解析到工作区，使用最近访问工作区（第一个）: strSpaceKey=%s", first_key)
            return (first_key, "recent", None)

    # 3. 最近访问也为空 → 提示用户指明工作区
    return (
        None,
        None,
        "未匹配到工作区，且暂无最近访问记录。请指明工作区（名称或 Key）后再试。",
    )


def resolve_workspace(
    user_workspace_keyword: Optional[str],
    config_workspace: Optional[str],
    headers: dict,
    raw_user_input: Optional[str] = None,
) -> Tuple[Optional[Union[str, List[str]]], Optional[str]]:
    """
    向后兼容的工作区解析入口，仅返回 (workspace, error_message)，忽略来源信息。
    其他脚本仍可按原接口调用。
    """
    logger.info("工作区解析开始: user_workspace_keyword=%s, config_workspace=%s", user_workspace_keyword, config_workspace)
    workspace, _source, err = resolve_workspace_with_source(
        user_workspace_keyword, config_workspace, headers, raw_user_input=raw_user_input
    )
    logger.info("工作区解析完成: workspace=%s, err=%s", workspace, err)
    return workspace, err
