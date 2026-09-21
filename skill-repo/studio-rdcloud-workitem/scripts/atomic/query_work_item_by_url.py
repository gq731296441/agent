#!/usr/bin/env python
"""
根据查询链接查询工作项：用户提供研发云工作项查询 URL（iChange 或 WIM 视图链接），按链接对应查询执行并返回结果。
遵循总分总：参数解析 → 参数调整 → 调用 API → 结果处理 → 结果格式化 → 追加心智提示。
iChange 模式：调用 GET rdc/query/queryWorkItem/{queryId}，queryId 为 URL 中 ? 前最后一个 / 后的片段；结果在 bo.result.items。
"""

import sys
import re
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_ROOT = SCRIPT_DIR.parent
SKILL_DIR = SCRIPTS_ROOT.parent
CONFIG_FILE = SKILL_DIR / "assets" / "config.json"

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from query_work_item import _cell_value
from common.log_helper import (
    get_logger,
    safe_print,
    apply_log_config,
    log_raw_argv,
    log_complete_params,
    ENABLE_LOG,
    setup_logging,
    truncate_for_log,
    sanitize_for_log,
    format_parsed_summary,
)
from common.config_helper import load_base_config
from common.auth_helper import get_auth_headers
from common.rdcloud_api import (
    parse_query_url,
    URL_QUERY_WORK_ITEM_BY_QUERY_ID,
    request_get,
    get_view_query_config,
    query_work_items,
    get_query_by_keyword,
    first_query_id_from_keyword_bo,
    LINK_RELATION_TYPE_MAP,
    studio_workitem_transmit_url,
)
from common.mindset_messages import get_mindset_message
from common.work_item_hierarchy import (
    flatten_hierarchy_items_with_virtual_filter,
    hierarchy_prefix,
    build_hierarchy_statistics_markdown,
)
from common.field_resolver import fetch_fields, resolve_extra_header_names

# 容器内 skill 为只读挂载（/mnt/skills/<id>），日志改写到可写工作区，并按 skill 分目录
LOG_DIR = (Path("/workspace/.skill-logs") / SKILL_DIR.name) if Path("/workspace").is_dir() else (SKILL_DIR / "logs")
LOG_FILE = LOG_DIR / f"query_work_item_by_url_{datetime.now().strftime('%Y%m%d')}.log"

# 仅在脚本作为主程序执行时初始化自己的日志文件；
# 被其它脚本 import（作为工具函数使用）时不单独创建日志文件，避免生成空日志。
if __name__ == "__main__" and ENABLE_LOG:
    setup_logging(LOG_FILE)

logger = get_logger(__name__)
_RELATION_ID_TO_NAME = {v: k for k, v in LINK_RELATION_TYPE_MAP.items()}
_RELATED_OPTIONS_API_TO_ZH = {
    "linksOneHopMustContain": "有匹配的关联工作项",
    "linksOneHopDoesNotContain": "无匹配的关联工作项",
    "linksOneHopMayContain": "所有工作项",
}
_TREE_OPTIONS_API_TO_ZH = {
    "top": "基于顶级显示",
    "related": "基于关联显示",
    "all": "显示所有",
}


def _query_mode_to_zh(query_mode):
    m = (query_mode or "").strip()
    if m == "oneHop":
        return "关联查询"
    if m == "tree":
        return "树查询"
    return "普通查询"


def _select_items_to_text(select_items):
    names = []
    if isinstance(select_items, list):
        for s in select_items:
            if isinstance(s, dict):
                n = (s.get("name") or "").strip() or (s.get("nameZh") or "").strip() or (s.get("key") or "").strip()
                if n:
                    names.append(n)
    return ",".join(names)


def _related_type_to_zh(v):
    if isinstance(v, list):
        vals = [str(x).strip() for x in v if str(x).strip()]
    else:
        vals = [str(v).strip()] if str(v or "").strip() else []
    names = [_RELATION_ID_TO_NAME.get(x, x) for x in vals]
    return ",".join(names)


def _related_options_to_zh(v):
    s = str(v or "").strip()
    return _RELATED_OPTIONS_API_TO_ZH.get(s, s)


def _tree_options_to_zh(v):
    s = str(v or "").strip()
    return _TREE_OPTIONS_API_TO_ZH.get(s, s)


def _build_parsed_summary_payload(parsed, data):
    """
    构造 URL 查询场景解析摘要（中文字段名，按查询模式输出必要字段）。
    """
    parsed = parsed or {}
    data = data or {}
    cfg = (data.get("config") or {}) if isinstance(data, dict) else {}
    q = (cfg.get("queryCondition") or {}) if isinstance(cfg, dict) else {}
    query_mode = (q.get("type") or "").strip()
    parsed_type = (parsed.get("type") or "").strip()

    if parsed_type == "by_keyword":
        source_desc = "按查询名/视图名"
    elif parsed_type == "ichange":
        source_desc = "按 iChange 链接"
    elif parsed_type == "wim":
        source_desc = "按 WIM 链接"
    else:
        source_desc = "按查询链接"

    summary = {
        "查询来源": source_desc,
        "工作区": (parsed.get("workspace") or "").strip(),
        "查询名称/视图名称": (parsed.get("keyword") or "").strip(),
        "查询ID": (parsed.get("query_id") or "").strip(),
        "视图ID": (parsed.get("view_id") or "").strip(),
        "查询模式": _query_mode_to_zh(query_mode),
        "查询列": _select_items_to_text(cfg.get("selectItems")),
    }

    if query_mode == "oneHop":
        summary.update(
            {
                "关联类型": _related_type_to_zh(q.get("relatedType")),
                "关联选项": _related_options_to_zh(q.get("relatedOptions")),
                "主对象条件": q.get("sourceClauses") or [],
                "关联侧条件": q.get("targetClauses") or [],
            }
        )
    elif query_mode == "tree":
        summary.update(
            {
                "树关联类型": _related_type_to_zh(q.get("relatedType")),
                "树展示选项": _tree_options_to_zh(q.get("treeOptions")),
                "主对象条件": q.get("sourceClauses") or [],
                "关联侧条件": q.get("targetClauses") or [],
            }
        )
    else:
        summary.update({"主对象条件": q.get("sourceClauses") or []})
    return summary


# 根据查询名/视图名提取关键词的正则（支持：根据查询名称、查询名、查询视图、视图名、视图名称）
_KEYWORD_INTENT_RE = re.compile(
    r"根据\s*(?:查询名|视图名|查询名称|视图名称|查询视图)\s*[：:\s]+\s*(.+)\s*$",
    re.IGNORECASE,
)


def extract_keyword_from_user_input(user_input: str):
    """
    从用户输入中提取「根据查询名/视图名 XXX」中的 XXX。
    返回 strip 后的字符串；若末尾为「 查询」则去掉该后缀，未匹配则返回 None。
    """
    if not user_input or not isinstance(user_input, str):
        return None
    m = _KEYWORD_INTENT_RE.search(user_input.strip())
    if not m:
        return None
    keyword = m.group(1).strip()
    if keyword.endswith(" 查询"):
        keyword = keyword[:-3].rstrip()  # 去掉末尾「 查询」（1 空格 + 2 字）
    return keyword or None


def _build_arg_parser():
    """构建命令行参数解析器（含完整示例）。"""
    example_text = (
        "完整示例:\n"
        "  # 方式一：按研发云查询 URL 查询（iChange/WIM）\n"
        "  python scripts/atomic/query_work_item_by_url.py "
        "--query-url \"https://rdcloud.zte.com.cn/uportal/rd-uportal/workspaces/PLM/apps/ichange/workspacequery/5198925\" "
        "--user-input \"查一下这个链接里的工作项\"\n\n"
        "  # 方式二：按查询名/视图名查询（从用户原话解析）\n"
        "  python scripts/atomic/query_work_item_by_url.py "
        "--user-input \"根据查询名 WODE 查询\"\n\n"
        "  # 方式三：直接将查询名作为 --query-url 传入\n"
        "  python scripts/atomic/query_work_item_by_url.py "
        "--query-url \"WODE\" --user-input \"根据查询名 WODE 查询\""
    )
    p = argparse.ArgumentParser(
        description="根据查询链接或查询名/视图名查询工作项：可传 URL（iChange/WIM）或根据查询名/视图名描述。",
        epilog=example_text,
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument(
        "--query-url", "--queryUrl", dest="query_url",
        default="", metavar="URL",
        help="查询链接（iChange/WIM URL）或直接传查询名/视图名（如 WODE）；与 --user-input 二选一或配合使用。"
    )
    p.add_argument(
        "--user-input", "--userInput", dest="user_input",
        default="", metavar="TEXT",
        help="用户完整输入，用于从「根据查询名/视图名 XXX」中解析关键词；未传 query-url 时必填。"
    )
    return p


def parse_args():
    """从命令行解析查询链接与用户原始输入。"""
    return _build_arg_parser().parse_args()


def adjust_params(args):
    """
    参数调整：优先按 URL 解析；若非 URL 或未传 URL，则按「根据查询名/视图名」从 user_input 或 query_url 取关键词。
    返回 (parsed, error_message)。parsed 含 type（ichange/wim/by_keyword）、workspace、query_id/view_id/keyword 等。
    """
    url = (args.query_url or "").strip()
    user_input = (args.user_input or "").strip()

    if url:
        parsed = parse_query_url(url)
        if parsed:
            log_complete_params(logger, "参数调整完成（URL）", {"parsed": parsed})
            return parsed, None

    keyword = extract_keyword_from_user_input(user_input)
    if not keyword and url and not (url.startswith("http://") or url.startswith("https://")):
        keyword = url
    if keyword:
        workspace = "PLM"
        log_complete_params(logger, "参数调整完成（根据查询名/视图名）", {"keyword": keyword, "workspace": workspace})
        return {"type": "by_keyword", "workspace": workspace, "keyword": keyword}, None

    if not url and not user_input:
        return None, "请提供查询链接（--query-url）或用户输入（--user-input，如：根据查询名 WODE 查询）。"
    return None, "未识别为有效链接或「根据查询名/视图名」描述，请提供研发云查询 URL 或说明根据哪个查询名/视图名查询。"


def _is_24_hex(s):
    """判断是否为 24 位十六进制字符串（viewId）。"""
    if not s or not isinstance(s, str) or len(s) != 24:
        return False
    return all(c in "0123456789abcdefABCDEF" for c in s)


def fetch_by_url(parsed, headers):
    """
    根据解析结果调用对应查询接口。
    - by_keyword：先 POST getQueryByKeyword 用名称查视图，取第一个 child 的 id，再 GET queryWorkItem/{queryId}。
    - 24 位 viewId：两步——先 GET view_queries/{viewId} 取 bo，去掉 allWorkItemTypeKey 后 POST query_work_items。
    - 否则：GET rdc/query/queryWorkItem/{queryId}，成功时返回 bo.result.items。
    """

    # 根据视图名查询（先根据视图名获取视图id,再根据视图id查询）
    if parsed.get("type") == "by_keyword":
        workspace = (parsed.get("workspace") or "").strip() or "PLM"
        keyword = (parsed.get("keyword") or "").strip()
        if not keyword:
            logger.warning("【根据查询名/视图名】缺少 keyword")
            return {"items": [], "error": "未提供查询名/视图名"}
        logger.info("【根据查询名/视图名】workspace=%s keyword=%s", workspace, keyword)
        try:
            resp = get_query_by_keyword(workspace, [keyword], headers)
        except Exception as e:
            logger.exception("【根据查询名/视图名】getQueryByKeyword 请求失败: %s", e)
            return {"items": [], "error": str(e)}
        code_obj = (resp or {}).get("code") or {}
        code_val = code_obj.get("code") if isinstance(code_obj, dict) else None
        if code_val != "0000":
            msg = code_obj.get("msg") or code_obj.get("msgId") or "Unknown"
            logger.warning("【根据查询名/视图名】getQueryByKeyword 返回非成功 code=%s msg=%s", code_val, msg)
            return {"items": [], "error": f"{msg}（接口 code={code_val}）。请稍后重试或确认查询名与权限。"}
        bo = resp.get("bo")
        query_id = first_query_id_from_keyword_bo(bo)
        if query_id is None:
            logger.warning("【根据查询名/视图名】未找到匹配的查询视图 bo=%s", type(bo).__name__)
            return {"items": [], "error": "未找到匹配的查询视图，请确认查询名/视图名是否正确"}
        query_id = str(query_id)
        logger.info("【根据查询名/视图名】解析得到 query_id=%s，调用 queryWorkItem", query_id)
        url = URL_QUERY_WORK_ITEM_BY_QUERY_ID.format(queryId=query_id)
        logger.info("【根据查询名/视图名】请求 queryWorkItem url=%s", url)
        try:
            resp2 = request_get(url, headers)
        except Exception as e:
            logger.exception("【根据查询名/视图名】queryWorkItem 请求失败: %s", e)
            return {"items": [], "error": str(e)}
        code_val2 = (resp2 or {}).get("code", {}) if isinstance(resp2, dict) else {}
        code_val2 = code_val2.get("code") if isinstance(code_val2, dict) else None
        bo2 = (resp2 or {}).get("bo") or {}
        result2 = bo2.get("result") if isinstance(bo2, dict) else {}
        items_count = len(result2.get("items") or []) if isinstance(result2, dict) else 0
        logger.info("【根据查询名/视图名】queryWorkItem 响应 code=%s, items 条数=%d", code_val2, items_count)
        # 响应体较长时也打一份截断日志，便于排查
        _rsp2_str = json.dumps(sanitize_for_log(resp2), ensure_ascii=False, indent=2) if isinstance(resp2, dict) else str(resp2)
        logger.info("【根据查询名/视图名】queryWorkItem 响应体: %s", truncate_for_log(_rsp2_str, 1500))
        code_obj2 = (resp2 or {}).get("code") or {}
        code_val2 = code_obj2.get("code") if isinstance(code_obj2, dict) else None
        if code_val2 != "0000":
            msg = code_obj2.get("msg") or code_obj2.get("msgId") or "Unknown"
            return {"items": [], "error": f"{msg}（接口 code={code_val2}）。请稍后重试或确认链接与权限。"}
        result = bo2.get("result") or {}
        items = result.get("items") if isinstance(result.get("items"), list) else []
        return {
            "items": items,
            "config": bo2.get("config"),
            "pageDetail": result.get("pageDetail") or {},
            "totalRow": result.get("totalRow"),
        }

    # 根据查询url查询，比如https://rdcloud.zte.com.cn/uportal/rd-uportal/workspaces/PLM/apps/wim/queries/5425716?type=root2&teamId=bdv_1102925&tenantId=31003
    # 可以提取到query_id
    if parsed["type"] in ("ichange", "wim"):
        query_id = parsed.get("query_id") or parsed.get("view_id") or ""
        if not query_id:
            logger.warning("【根据 URL 查询】%s 模式缺少 query/query/view_id", parsed["type"])
            return {
                "_placeholder": True,
                "type": parsed["type"],
                "workspace": parsed.get("workspace", ""),
                "query_id": "",
                "items": [],
            }
        workspace = (parsed.get("workspace") or "").strip() or "PLM"

        if _is_24_hex(query_id):
            # 24 位 viewId：两步查询
            logger.info("【根据 URL 查询】24 位 viewId 两步查询 workspace=%s viewId=%s", workspace, query_id)
            try:
                step1 = get_view_query_config(workspace, query_id, headers)
            except Exception as e:
                logger.exception("【根据 URL 查询】view_queries 请求失败: %s", e)
                return {"items": [], "error": str(e)}
            _s1_code = (step1 or {}).get("code") or {}
            _s1_code_val = _s1_code.get("code") if isinstance(_s1_code, dict) else None
            logger.info("【根据 URL 查询】view_queries 响应 code=%s", _s1_code_val)
            code_obj = (step1 or {}).get("code") or {}
            code_val = code_obj.get("code") if isinstance(code_obj, dict) else None
            if code_val != "0000":
                msg = code_obj.get("msg") or code_obj.get("msgId") or "Unknown"
                logger.warning("【根据 URL 查询】view_queries 返回非成功 code=%s msg=%s，尝试 WIM 后端单步 queryWorkItem", code_val, msg)
                # WIM 链接且工作区可能走 WIM 后端（如 iRAI）：用 24 位 viewId 直接调 WIM queryWorkItem
                if parsed.get("type") == "wim":
                    try:
                        url_wim = URL_QUERY_WORK_ITEM_BY_QUERY_ID.format(queryId=query_id)
                        logger.info("【根据 URL 查询】WIM 兜底请求 queryWorkItem url=%s", url_wim)
                        resp_wim = request_get(url_wim, headers)
                        _code = (resp_wim or {}).get("code") or {}
                        _cv = _code.get("code") if isinstance(_code, dict) else None
                        _bo = (resp_wim or {}).get("bo") or {}
                        _result = _bo.get("result") or {} if isinstance(_bo, dict) else {}
                        _items = _result.get("items") if isinstance(_result.get("items"), list) else []
                        if _cv == "0000":
                            logger.info("【根据 URL 查询】WIM 兜底成功，items 条数=%d", len(_items))
                            return {
                                "items": _items,
                                "config": _bo.get("config") if isinstance(_bo, dict) else None,
                                "pageDetail": _result.get("pageDetail") or {},
                                "totalRow": _result.get("totalRow"),
                            }
                    except Exception as e:
                        logger.warning("【根据 URL 查询】WIM 兜底请求失败: %s", e)
                return {"items": [], "error": f"{msg}（接口 code={code_val}）。请稍后重试或确认链接与权限。"}
            bo = (step1 or {}).get("bo") or {}
            if not isinstance(bo, dict):
                return {"items": [], "error": "view_queries 响应 bo 格式异常"}
            body = {k: v for k, v in bo.items() if k != "allWorkItemTypeKey"}
            try:
                step2 = query_work_items(workspace, body, headers)
            except Exception as e:
                logger.exception("【根据 URL 查询】query_work_items 请求失败: %s", e)
                return {"items": [], "error": str(e)}
            bo2 = (step2 or {}).get("bo") or {}
            result = bo2.get("result") if isinstance(bo2, dict) else {}
            _s2_items = len(result.get("items") or []) if isinstance(result, dict) else 0
            _s2_code = (step2 or {}).get("code") or {}
            _s2_code_val = _s2_code.get("code") if isinstance(_s2_code, dict) else None
            logger.info("【根据 URL 查询】query_work_items 响应 code=%s, items 条数=%d", _s2_code_val, _s2_items)
            if _s2_code_val != "0000":
                _s2_msg = _s2_code.get("msg") or _s2_code.get("msgId") or "Unknown"
                logger.warning("【根据 URL 查询】query_work_items 返回非成功 code=%s msg=%s", _s2_code_val, _s2_msg)
                return {"items": [], "error": f"{_s2_msg}（接口 code={_s2_code_val}）。请稍后重试或确认链接与权限。"}
            # 第二步响应与单步一致：bo.result 或 data
            if isinstance(result, dict) and "items" in result:
                items = result.get("items") if isinstance(result.get("items"), list) else []
                page_detail = result.get("pageDetail") or {}
                total_row = result.get("totalRow")
            else:
                data = (step2 or {}).get("data") or {}
                items = data.get("items") if isinstance(data.get("items"), list) else []
                page_detail = {}
                total_row = data.get("total") if isinstance(data, dict) else None
            return {
                "items": items,
                "config": bo2.get("config") if isinstance(bo2, dict) else None,
                "pageDetail": page_detail,
                "totalRow": total_row,
            }

        # 非 24 位：单步 GET queryWorkItem
        url = URL_QUERY_WORK_ITEM_BY_QUERY_ID.format(queryId=query_id)
        logger.info("【根据 URL 查询】%s 模式 workspace=%s query_id=%s", parsed["type"], parsed.get("workspace", ""), query_id)
        logger.info("【根据 URL 查询】请求 queryWorkItem url=%s", url)
        try:
            resp = request_get(url, headers)
        except Exception as e:
            logger.exception("【根据 URL 查询】请求失败: %s", e)
            return {"items": [], "error": str(e)}
        code_obj = (resp or {}).get("code") or {}
        code_val = code_obj.get("code") if isinstance(code_obj, dict) else None
        bo = (resp or {}).get("bo") or {}
        result = bo.get("result") or {} if isinstance(bo, dict) else {}
        items = result.get("items") if isinstance(result.get("items"), list) else []
        logger.info("【根据 URL 查询】queryWorkItem 响应 code=%s, items 条数=%d", code_val, len(items))
        _resp_str = json.dumps(sanitize_for_log(resp), ensure_ascii=False, indent=2) if isinstance(resp, dict) else str(resp)
        logger.info("【根据 URL 查询】queryWorkItem 响应体: %s", truncate_for_log(_resp_str, 1500))
        if code_val != "0000":
            msg = code_obj.get("msg") or code_obj.get("msgId") or "Unknown"
            logger.warning("【根据 URL 查询】接口返回非成功 code=%s msg=%s", code_val, msg)
            err_text = f"{msg}（接口 code={code_val}）。请稍后重试或确认链接与权限。"
            return {"items": [], "error": err_text}
        config = bo.get("config") if isinstance(bo, dict) else None
        page_detail = result.get("pageDetail") or {}
        total_row = result.get("totalRow")
        return {
            "items": items,
            "config": config,
            "pageDetail": page_detail,
            "totalRow": total_row,
        }
    return {"_placeholder": True, "items": []}


# 表格固定列（与 query_work_item 一致）
_FIXED_COLUMNS = [
    ("标题", "System_Title"),
    ("标识", "System_Id"),
    ("工作项类型", "System_WorkItemType"),
    ("更新时间", "System_ChangedDate"),
]
_FIXED_KEYS = {k for _, k in _FIXED_COLUMNS}


def format_result(data, workspace=None, headers=None, base_config=None):
    """将接口返回数据格式化为用户可读文本（Markdown 表格或占位说明）。"""
    if not data:
        return "未获取到数据。"
    if data.get("_placeholder"):
        ws = data.get("workspace", "")
        if data.get("type") == "ichange":
            return f"已识别为 iChange 工作区查询（工作区：{ws}，查询 ID：{data.get('query_id', '')}）。\n\n**查询 API 待接入**，接入后将在此直接返回该查询下的工作项列表。"
        if data.get("type") == "wim":
            return f"已识别为 WIM 视图查询（工作区：{ws}，视图 ID：{data.get('view_id', '')}）。\n\n**查询 API 待接入**，接入后将在此直接返回该视图下的工作项列表。"
        return "已识别为支持的查询链接，**查询 API 待接入**。"
    items = data.get("items")
    if not items or not isinstance(items, list):
        if data.get("error"):
            err = data["error"]
            return f"查询失败：{err}"
        return "未找到符合条件的工作项。"
    config = data.get("config") or {}
    select_items = config.get("selectItems") or []
    query_mode = ""
    q_condition = (config.get("queryCondition") or {}) if isinstance(config, dict) else {}
    if isinstance(q_condition, dict):
        query_mode = (q_condition.get("type") or "").strip()
    filter_virtual = True
    if isinstance(base_config, dict):
        filter_virtual = bool(base_config.get("filter_virtual_root_for_related", True))
    is_related_or_tree = query_mode in ("oneHop", "tree")
    use_virtual_filter = filter_virtual and is_related_or_tree
    omit_large_children = False
    children_display_limit = 0
    if isinstance(base_config, dict):
        omit_large_children = bool(base_config.get("omit_large_children_for_display", False)) and is_related_or_tree
        try:
            base_limit = max(0, int(base_config.get("children_display_limit", 0)))
            one_hop_limit = base_config.get("one_hop_children_display_limit", None)
            if query_mode == "oneHop" and one_hop_limit is not None:
                children_display_limit = max(0, int(one_hop_limit))
            else:
                children_display_limit = base_limit
        except (TypeError, ValueError):
            children_display_limit = 0
    flat_items, virtual_count = flatten_hierarchy_items_with_virtual_filter(
        items,
        filter_virtual_root=use_virtual_filter,
        omit_large_children=omit_large_children,
        children_display_limit=children_display_limit,
    )
    extra_keys = [s["key"] for s in select_items if isinstance(s, dict) and s.get("key") and s["key"] not in _FIXED_KEYS]
    logger.info("format_result extra_keys: workspace=%s, extra_keys=%s", workspace or "", extra_keys)
    fields = None
    ws_single = workspace[0] if isinstance(workspace, list) else workspace
    if ws_single and headers:
        try:
            fields = fetch_fields(ws_single, headers)
        except Exception as e:
            logger.warning("fetch_fields 失败，表头回退 key: %s", e)
    extra_headers = resolve_extra_header_names(
        extra_keys, fields=fields, workspace=workspace, headers=headers
    )
    header_names = [h for h, _ in _FIXED_COLUMNS] + extra_headers
    total = data.get("totalRow")
    link_row = (data.get("pageDetail") or {}).get("linkRow") if isinstance(data.get("pageDetail"), dict) else None
    lines = ["# 工作项查询结果\n"]
    if is_related_or_tree:
        hierarchy_stats_text = build_hierarchy_statistics_markdown(
            items,
            filter_virtual_root=use_virtual_filter,
        )
        if hierarchy_stats_text:
            lines.append(hierarchy_stats_text + "\n")
    if total is not None:
        lines.append(f"**总计：{total} 条**\n")
    if link_row is not None and query_mode in ("oneHop", "tree"):
        try:
            link_row_display = max(int(link_row) - int(virtual_count or 0), 0)
        except (TypeError, ValueError):
            link_row_display = link_row
        logger.info(
            "关联工作项数扣减: query_mode=%s, link_row=%s, virtual_count=%s, link_row_display=%s",
            query_mode,
            link_row,
            virtual_count,
            link_row_display,
        )
        lines.append(f"**关联工作项数：{link_row_display} 条**\n")
    sep = "|" + "|".join(["------"] * len(header_names)) + "|\n"
    lines.append("| " + " | ".join(header_names) + " |\n")
    lines.append(sep)
    for item in flat_items:
        if not isinstance(item, dict):
            continue
        cells = []
        for disp_name, key in _FIXED_COLUMNS:
            if key == "System_Title":
                title = item.get("System_Title", "")
                title = f"{hierarchy_prefix(item)}{title}" if title else hierarchy_prefix(item)
                wid = item.get("System_Id", "")
                if wid:
                    url = studio_workitem_transmit_url(wid)
                    cells.append(f"[{title}]({url})" if title else "")
                else:
                    cells.append(title or "")
            elif key == "System_Id":
                wid = item.get("System_Id", "")
                if wid:
                    url = studio_workitem_transmit_url(wid)
                    cells.append(f"[{wid}]({url})")
                else:
                    cells.append(wid or "")
            elif key == "System_WorkItemType":
                wo = item.get("System_WorkItemType")
                if isinstance(wo, dict):
                    cells.append(wo.get("name", ""))
                elif isinstance(wo, str):
                    cells.append(wo)
                else:
                    cells.append("")
            else:
                cells.append(_cell_value(item, key, fields))
        for key in extra_keys:
            cells.append(_cell_value(item, key, fields))
        lines.append("| " + " | ".join(cells) + " |\n")
    return "".join(lines)


def append_hints(text, has_data):
    """在结果后追加心智提示。"""
    if has_data:
        return text
    hint = "\n\n---\n若未看到预期结果，可确认链接是否在浏览器中能正常打开、当前账号是否有该工作区与查询的访问权限。"
    return text + hint


def run():
    """总方法：参数解析 → 参数调整 → 调用 API → 结果处理 → 结果格式化 → 追加心智提示。"""
    parsed, err = adjust_params(parse_args())
    if err:
        err_out = str(err)
        mindset_text = get_mindset_message("query_error")
        if mindset_text and mindset_text not in err_out:
            err_out = err_out + "\n\n" + mindset_text
        safe_print(err_out)
        return 1

    base_config = load_base_config(CONFIG_FILE)
    headers = get_auth_headers(None, base_config, logger=logger)

    data = fetch_by_url(parsed, headers)
    has_data = bool(data and data.get("items"))
    result_text = format_result(data, workspace=parsed.get("workspace"), headers=headers, base_config=base_config)
    result_text = append_hints(result_text, has_data)
    mindset_text = get_mindset_message("url_query_result", has_data=has_data)
    parsed_summary_payload = _build_parsed_summary_payload(parsed, data)
    parsed_summary = format_parsed_summary(parsed_summary_payload, title="本次语义解析结果")
    body_with_mindset = (result_text + "\n\n" + mindset_text) if mindset_text else result_text
    result_for_display = (parsed_summary + "\n" + body_with_mindset) if parsed_summary else body_with_mindset
    safe_print(result_for_display)
    return 0


def main():
    apply_log_config(CONFIG_FILE)
    log_raw_argv(logger, sys.argv)
    try:
        return run()
    except ValueError as e:
        err_out = f"错误: {e}"
        mindset_text = get_mindset_message("query_error")
        if mindset_text and mindset_text not in err_out:
            err_out = err_out + "\n\n" + mindset_text
        safe_print(err_out)
        return 1
    except Exception as e:
        logger.exception("执行失败")
        err_out = f"错误: {e}"
        mindset_text = get_mindset_message("query_error")
        if mindset_text and mindset_text not in err_out:
            err_out = err_out + "\n\n" + mindset_text
        safe_print(err_out)
        return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
