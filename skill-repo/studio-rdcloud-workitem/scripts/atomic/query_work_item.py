#!/usr/bin/env python
"""查询工作项脚本（通用）。支持命令行参数 --select-items、--conditions 及 -h/--help。"""

import sys
import json
import os
import re
import requests
import logging
import argparse
import urllib.parse
import importlib.util
from pathlib import Path
from datetime import datetime, timezone, timedelta

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_ROOT = SCRIPT_DIR.parent
SKILL_DIR = SCRIPTS_ROOT.parent
CONFIG_FILE = SKILL_DIR / "assets" / "config.json"
USER_SEARCH_MODULE = SCRIPTS_ROOT / "common" / "user_search.py"
# 容器内 skill 为只读挂载（/mnt/skills/<id>），日志改写到可写工作区，并按 skill 分目录
LOG_DIR = (Path("/workspace/.skill-logs") / SKILL_DIR.name) if Path("/workspace").is_dir() else (SKILL_DIR / "logs")
LOG_FILE = LOG_DIR / f"query_work_item_{datetime.now().strftime('%Y%m%d')}.log"

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))
from common.log_helper import (
    get_log_config,
    truncate_for_log,
    get_logger,
    ENABLE_LOG,
    safe_print,
    mask_emp_no_for_log,
    sanitize_for_log,
    truncate_subprocess_output_for_log,
    setup_logging,
    format_parsed_summary,
)
from common.auth_helper import (
    get_auth_headers,
    resolve_auth_token_from_env,
    resolve_emp_no_from_env,
)
from common.notify_helper import send_mail as notify_send_mail
from common.sql_query_parser import build_query_data_from_select_and_conditions
from common.mindset_messages import (
    build_query_context_mindset_extras,
    get_mindset_message,
)

# 仅在脚本作为主程序执行时初始化自己的日志文件；
# 被其它脚本 import（作为工具函数使用）时不单独创建日志文件，避免生成空日志。
if __name__ == "__main__" and ENABLE_LOG:
    setup_logging(LOG_FILE)
logger = get_logger(__name__)
from common.workspace import (
    append_workspace_clause_if_needed,
    apply_workspace_clause_to_query_condition,
    resolve_workspace_with_source,
)
from common.rdcloud_api import (
    BASE_WIC_API,
    URL_QUERY_WORK_ITEMS,
    URL_VIEWS,
    URL_SEND_MAIL,
    URL_GROUP_CHAT_SEARCH,
    URL_ICHAT_SHARE,
    get_wim_query_view_url,
    studio_workitem_transmit_url,
    _normalize_workspace_single,
    LINK_RELATION_TYPE_MAP,
)
from common.field_resolver import fetch_fields, get_datetime_field_keys, resolve_extra_header_names
from common.work_item_hierarchy import (
    flatten_hierarchy_items_with_virtual_filter,
    hierarchy_prefix,
    build_hierarchy_statistics_markdown,
)

# 字典类型字段的取值优先级（按顺序命中）；含 userId 的用户对象优先用 nameDisplayLong
_DICT_VALUE_PRIORITY_KEYS = ("value", "name", "nameZh", "tag")


def load_base_config():
    base_config = {
        "base_url": BASE_WIC_API,
        "workspace": "",
        "tenant_id": "ZTE",
        "format_as_markdown": True,
        "filter_virtual_root_for_related": True,
        "omit_large_children_for_display": False,
        "children_display_limit": 0,
        # 直接关联查询（oneHop）专用 children 省略阈值；None 表示回退 children_display_limit
        "one_hop_children_display_limit": None,
    }
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
            api = config.get("api", {})
            base_config["workspace"] = api.get("workspace", "")
            base_config["tenant_id"] = api.get("tenant_id", base_config["tenant_id"])
            base_config["format_as_markdown"] = config.get("format", {}).get(
                "as_markdown", True
            )
            base_config["filter_virtual_root_for_related"] = bool(
                config.get("format", {}).get("filter_virtual_root_for_related", True)
            )
            base_config["omit_large_children_for_display"] = bool(
                config.get("format", {}).get("omit_large_children_for_display", False)
            )
            try:
                base_config["children_display_limit"] = max(
                    0,
                    int(config.get("format", {}).get("children_display_limit", 0)),
                )
            except (TypeError, ValueError):
                base_config["children_display_limit"] = 0
            if "one_hop_children_display_limit" in config.get("format", {}):
                try:
                    base_config["one_hop_children_display_limit"] = max(
                        0,
                        int(config.get("format", {}).get("one_hop_children_display_limit")),
                    )
                except (TypeError, ValueError):
                    base_config["one_hop_children_display_limit"] = None
        except Exception as e:
            logger.warning(f"配置文件读取失败: {e}")
    return base_config


def extract_workspace_key(work_item_id):
    if not work_item_id:
        return ""
    i = work_item_id.rfind("-")
    return work_item_id[:i] if i != -1 else work_item_id


# 已知的系统日期/时间字段（fields 为空时的 fallback）
_KNOWN_DATETIME_KEYS = frozenset({
    "System_ChangedDate", "System_CreatedDate", "PlanFinishDate", "PlanStartDate",
})

# 表头与 item 字段 key 的固定列（保留现有四列）
_FIXED_COLUMNS = [
    ("标题", "System_Title"),
    ("标识", "System_Id"),
    ("工作项类型", "System_WorkItemType"),
    ("更新时间", "System_ChangedDate"),
]
_FIXED_KEYS = {k for _, k in _FIXED_COLUMNS}


def strip_html_to_text(html_str: str) -> str:
    """将富文本 HTML 转为纯文本：去掉所有标签，只保留可见文字。"""
    if not html_str or not isinstance(html_str, str):
        return ""
    s = html_str
    # 先移除 script/style 等不用于展示的内容
    s = re.sub(
        r"<(script|style)[^>]*>.*?</\\1>", " ", s, flags=re.IGNORECASE | re.DOTALL
    )
    # 移除 <...> 标签（含自闭合如 <img />）
    s = re.sub(r"<[^>]+>", " ", s)
    # 常见 HTML 实体
    s = (
        s.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    s = re.sub(
        r"&#x([0-9a-fA-F]+);",
        lambda m: chr(int(m.group(1), 16)) if m.group(1) else "",
        s,
    )
    s = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))) if m.group(1) else "", s)
    # 合并多余空白并去掉首尾
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _format_datetime_value(val):
    """将日期/时间值格式化为 YYYY-MM-DD HH:MM:SS 字符串。"""
    if val is None or val == "":
        return ""
    try:
        if isinstance(val, (int, float)):
            return datetime.fromtimestamp(val / 1000).strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(val, str):
            changed_iso = val.replace("Z", "+00:00") if val.endswith("Z") else val
            dt = datetime.fromisoformat(changed_iso)
            if dt.tzinfo and dt.tzinfo.utcoffset(dt) == timedelta(0):
                dt = dt.replace(tzinfo=timezone.utc).astimezone(
                    timezone(timedelta(hours=8))
                )
            elif dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc).astimezone(
                    timezone(timedelta(hours=8))
                )
            return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    return str(val)


def _cell_value(item, key, fields=None):
    """从工作项 item 中按 key 取值并格式化为表格单元格字符串。key 以 _html 结尾时去除富文本标签只保留文字。fields 为字段列表，传入后在内部判断 key 是否为时间类型并格式化。"""
    val = item.get(key)
    if val is None or val == "":
        return ""
    if isinstance(val, dict):
        out = ""
        uid = val.get("userId")
        if uid is not None and uid != "":
            ndl = val.get("nameDisplayLong")
            if ndl is not None and ndl != "":
                out = str(ndl)
        # 无 nameDisplayLong（或仅有 userId 未配长名）时与普通过字典一致，按 _DICT_VALUE_PRIORITY_KEYS 兜底
        if not out:
            for k in _DICT_VALUE_PRIORITY_KEYS:
                v = val.get(k)
                if v is not None and v != "":
                    out = str(v)
                    break
    elif isinstance(val, list):
        out = ", ".join(
            str(_cell_value({"x": x}, "x", fields) if isinstance(x, dict) else str(x))
            for x in val
        )
    else:
        effective_dt_keys = (get_datetime_field_keys(fields) if fields else frozenset()) | _KNOWN_DATETIME_KEYS
        if key in effective_dt_keys:
            out = _format_datetime_value(val)
        else:
            out = str(val)
    if key.endswith("_html") and out:
        out = strip_html_to_text(out)
    return out


def format_result_as_markdown(
    result, query_data=None, base_config=None, workspace=None, headers=None
):
    """将查询结果格式化为 Markdown 表格。保留固定列，并根据 query_data.selectItems 增加多出的列；额外列名通过字段列表解析为中文。"""
    if not result or not isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False, indent=2)
    items, total, link_row = [], None, None
    if "data" in result and isinstance(result["data"], dict):
        items = result["data"].get(
            "items", result["data"] if isinstance(result["data"], list) else []
        )
        total = result["data"].get("total")
    elif "bo" in result and isinstance(result["bo"], dict) and "result" in result["bo"]:
        r = result["bo"]["result"]
        if isinstance(r, dict) and "items" in r:
            items = r["items"]
            pd = r.get("pageDetail")
            total = pd.get("totalRow") if isinstance(pd, dict) else r.get("totalRow")
            link_row = pd.get("linkRow") if isinstance(pd, dict) else None
    elif isinstance(result, list):
        items = result
    if not items:
        return "# 工作项查询结果\n\n**未找到符合条件的工作项**\n"
    filter_virtual = True
    if isinstance(base_config, dict):
        filter_virtual = bool(base_config.get("filter_virtual_root_for_related", True))
    query_mode = ""
    if isinstance(query_data, dict):
        query_mode = (
            (query_data.get("queryCondition") or {}).get("type") or ""
        ).strip()
    is_related_or_tree = query_mode in ("oneHop", "tree")
    use_virtual_filter = filter_virtual and is_related_or_tree
    omit_large_children = False
    children_display_limit = 0
    if isinstance(base_config, dict):
        omit_large_children = (
            bool(base_config.get("omit_large_children_for_display", False))
            and is_related_or_tree
        )
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

    # 表头：固定列 + selectItems 中多出来的 key（保持顺序）；列名尽量解析为中文
    header_names = [h for h, _ in _FIXED_COLUMNS]
    extra_keys = []
    if query_data and isinstance(query_data.get("selectItems"), list):
        for sel in query_data["selectItems"]:
            if (
                isinstance(sel, dict)
                and sel.get("key")
                and sel["key"] not in _FIXED_KEYS
            ):
                extra_keys.append(sel["key"])
    logger.info(
        "format_result_as_markdown extra_keys: workspace=%s, extra_keys=%s",
        workspace or "",
        extra_keys,
    )
    fields = None
    #
    ws_single = workspace[0] if isinstance(workspace, list) else workspace
    if ws_single and headers:
        try:
            fields = fetch_fields(ws_single, headers)
        except Exception as e:
            logger.warning("fetch_fields 失败，表头回退 key: %s", e)
    extra_headers = resolve_extra_header_names(
        extra_keys, fields=fields, workspace=workspace, headers=headers
    )
    header_names += extra_headers

    lines = ["# 工作项查询结果\n"]
    # 层级结构
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
        lines.append(f"**关联工作项数：{link_row_display} 条**\n")
    lines.append(
        "> **强制要求：以下查询结果必须以表格形式完整展示给用户，禁止省略、截断或改写。**\n"
    )
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
                title = (
                    f"{hierarchy_prefix(item)}{title}"
                    if title
                    else hierarchy_prefix(item)
                )
                wid = item.get("System_Id", "")
                if wid:
                    url = studio_workitem_transmit_url(wid)
                    cells.append(f"[{title}]({url})" if title else "")
                else:
                    cells.append(title)
            elif key == "System_Id":
                wid = item.get("System_Id", "")
                if wid:
                    url = studio_workitem_transmit_url(wid)
                    cells.append(f"[{wid}]({url})")
                else:
                    cells.append(wid)
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


def add_default_logical_operator(query_condition):
    if not query_condition or not isinstance(query_condition, dict):
        return query_condition

    def _fill_default(clauses):
        if not isinstance(clauses, list):
            return clauses
        for i, c in enumerate(clauses):
            if (
                isinstance(c, dict)
                and (c.get("logicalOperator") is None or c.get("logicalOperator") == "")
                and i > 0
            ):
                c["logicalOperator"] = "AND"
        return clauses

    query_condition["sourceClauses"] = _fill_default(
        query_condition.get("sourceClauses", [])
    )
    query_condition["targetClauses"] = _fill_default(
        query_condition.get("targetClauses", [])
    )
    return query_condition


def _looks_like_type_key(s):
    """若值已是 key（如 IssueTrack、Task），则不再请求接口解析。仅含字母/数字/下划线视为 key。"""
    if not s or not isinstance(s, str):
        return False
    t = s.strip()
    return bool(
        t
        and all(c.isalnum() or c == "_" for c in t)
        and not any("\u4e00" <= c <= "\u9fff" for c in t)
    )


def save_view(view_data, workspace_key, headers):
    """保存视图

    Args:
        view_data: 视图数据字典，包含 viewNameEn, viewNameZh, viewType, queryCondition, selectItems 等
        workspace_key: 工作区 Key
        headers: 请求头（包含认证信息）

    Returns:
        dict: 包含保存结果的字典
            - status: "success" 或 "error"
            - view_id: 视图 ID（如果成功）
            - message: 错误或提示信息
    """
    logger.info(f"开始保存视图")
    if not workspace_key:
        error_msg = "缺少工作区 Key (workspace_key)"
        logger.error(error_msg)
        return {"status": "error", "message": error_msg}
    workspace_key = _normalize_workspace_single(workspace_key)
    view_api_url = URL_VIEWS.format(workspace=workspace_key)
    view_headers = {
        "Content-Type": "application/json",
        "X-Tenant-Id": headers.get("X-Tenant-Id", "ZTE"),
        "X-Auth-Value": headers.get("X-Auth-Value", ""),
        "X-Emp-No": headers.get("X-Emp-No", ""),
    }

    # 确保 view_data 包含必需的字段
    if "viewNameEn" not in view_data or "viewNameZh" not in view_data:
        error_msg = "视图数据缺少必需字段: viewNameEn 或 viewNameZh"
        logger.error(error_msg)
        return {"status": "error", "message": error_msg}

    # 设置默认值
    if "viewType" not in view_data:
        view_data["viewType"] = "personal"
    if "queryType" not in view_data:
        view_data["queryType"] = "filter"
    if "workItemTypeKeys" not in view_data:
        view_data["workItemTypeKeys"] = []

    try:
        logger.info(
            f"保存视图: workspace={workspace_key}, viewNameZh={view_data.get('viewNameZh')}"
        )
        logger.info(f"请求URL: {view_api_url}")
        logger.info(
            "请求体: %s",
            json.dumps(sanitize_for_log(view_data), ensure_ascii=False, indent=2),
        )

        response = requests.post(
            view_api_url, headers=view_headers, json=view_data, timeout=30
        )

        logger.info(f"响应状态码: {response.status_code}")

        if response.status_code == 200:
            result_data = response.json()
            if (
                isinstance(result_data.get("code"), dict)
                and result_data["code"].get("code") == "0000"
            ):
                view_id = result_data.get("bo", {}).get("id")
                if view_id:
                    logger.info(f"视图保存成功，视图ID: {view_id}")
                    return {
                        "status": "success",
                        "view_id": view_id,
                        "message": "视图保存成功",
                    }
                else:
                    logger.warning("视图保存成功，但未返回视图ID")
                    return {
                        "status": "error",
                        "message": "视图保存成功，但未返回视图ID",
                    }
            else:
                error_msg = f"视图保存失败: {result_data.get('code', {}).get('msg', '未知错误')}"
                logger.error(
                    "错误详情: %s",
                    truncate_for_log(
                        json.dumps(sanitize_for_log(result_data), ensure_ascii=False),
                        500,
                    ),
                )
                return {"status": "error", "message": error_msg}
        else:
            error_msg = f"视图保存失败，状态码: {response.status_code}"
            try:
                error_detail = response.json()
                error_msg += f", 响应: {json.dumps(error_detail, ensure_ascii=False)}"
                logger.error(
                    "错误详情: %s",
                    truncate_for_log(
                        json.dumps(sanitize_for_log(error_detail), ensure_ascii=False),
                        500,
                    ),
                )
            except:
                error_msg += f", 响应内容: {response.text}"
                logger.error("错误响应: %s", truncate_for_log(response.text or "", 500))

            return {
                "status": "error",
                "message": error_msg,
                "status_code": response.status_code,
            }
    except requests.exceptions.RequestException as e:
        error_msg = f"视图保存失败: {str(e)}"
        logger.error(error_msg)
        return {"status": "error", "message": error_msg}
    except Exception as e:
        error_msg = f"视图保存失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {"status": "error", "message": error_msg}


def get_view_url_after_query(workspace, query_condition, input_data, headers):
    """保存当前查询条件为视图，返回视图跳转链接供后续使用。

    Args:
        workspace: 工作区 Key
        query_condition: 查询条件（与本次查询一致）
        input_data: 输入数据，用于 selectItems、sortItems
        headers: 请求头（认证信息）

    Returns:
        str | None: 成功时返回视图链接 view_url，失败时返回 None（不影响查询结果）
    """
    view_url = None
    try:
        logger.info("开始保存视图...")
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        view_name_en = f"view_{timestamp}"
        view_name_zh = f"查询视图_{timestamp}"
        view_data = {
            "viewNameEn": view_name_en,
            "viewNameZh": view_name_zh,
            "viewName": view_name_zh,
            "viewType": "personal",
            "workItemTypeKeys": [],
            "queryType": "filter",
            "crossWorkspace": True,
            "queryCondition": query_condition,
            "selectItems": input_data.get("selectItems", []),
            "sortItems": input_data.get("sortItems", []),
            "filterItems": [],
        }
        save_result = save_view(view_data, workspace, headers)
        if save_result.get("status") == "success":
            view_id = save_result.get("view_id")
            if view_id:
                view_url = get_wim_query_view_url(workspace, view_id)
                logger.info(f"视图保存成功，视图链接: {view_url}")
                safe_print(f"视图保存成功，视图链接: {view_url}")
            else:
                logger.warning("视图保存成功，但未获取到视图ID")
        else:
            logger.warning(
                f"视图保存失败: {save_result.get('message')}，继续返回查询结果"
            )
    except Exception as e:
        logger.warning(f"保存视图时发生异常: {str(e)}，继续返回查询结果", exc_info=True)
    return view_url


def convert_work_item_type_values(query_condition, workspace, headers):
    """将 source/target 子句中的工作项类型值解析为 key；若已是 key 则跳过。
    从子句 value 取出后，仅当 field_work_item_type_resolver_config 对原始取值命中补充规则时，才合并关联类型 key。

    :return: (query_condition, original_type_names) — original_type_names 为解析前从 value 中收集的
             工作项类型用语（多为中文），供心智模型等与 key 对照；已像 key 的取值不收录。
    """
    if not query_condition or not isinstance(query_condition, dict):
        return query_condition, []
    if not workspace or not headers:
        return query_condition, []
    from common.work_item_type_resolver import (
        get_supplementary_work_item_type_names,
        resolve_work_item_type_key,
        supplement_resolved_work_item_type_keys,
    )

    target_fields = (
        "System_WorkItemType",
        "System_WorkItemTypeKey",
        "System_WorkItemType.name",
        "System_WorkItemType.nameZh",
    )
    collected_original_type_names: list[str] = []

    def _convert_clause(c):
        if not isinstance(c, dict):
            return
        f, v = c.get("field", ""), c.get("value")
        if f not in target_fields or not v:
            return
        if isinstance(v, list):
            resolved_list = []
            for x in v:
                if not x:
                    continue
                if _looks_like_type_key(str(x).strip()):
                    resolved_list.append(str(x).strip())
                else:
                    try:
                        # 大模型识别的工作项类型中文名
                        x_str = str(x).strip()
                        collected_original_type_names.append(x_str)
                        # 根据x_str读取补充规则，获取要补充的工作项类型中文名称
                        extras = get_supplementary_work_item_type_names(
                            workspace, x_str
                        )
                        logger.info(
                            "工作项类型补充规则查询: workspace=%s, raw_type=%r, supplementary_names=%s",
                            workspace,
                            x_str,
                            extras,
                        )
                        # 将x_str中文工作项类型名转换为类型key，放在r中。km是{key: 显示名} 映射
                        r, km = [], {}
                        try:
                            r, km = resolve_work_item_type_key(x_str, workspace, headers)
                        except Exception as e_resolve:
                            logger.warning(
                                "工作项类型主解析失败，将继续尝试补充规则: workspace=%s, raw_type=%r, error=%s",
                                workspace,
                                x_str,
                                e_resolve,
                            )
                        # 如果前面补充规则命中，则将补充工作项类型中文转换为key
                        if extras:
                            r, km = supplement_resolved_work_item_type_keys(
                                x_str,
                                workspace,
                                headers,
                                r,
                                km,
                                extra_names=extras,
                            )
                        vals = r if isinstance(r, list) else [r]
                        vals = [str(t).strip() for t in vals if t and str(t).strip()]
                        if vals:
                            resolved_list.extend(vals)
                        else:
                            resolved_list.append(x_str)
                    except Exception as e:
                        logger.warning(
                            "查询条件中工作项类型解析失败（仅记录，不中断）: value=%s, error=%s",
                            x,
                            e,
                        )
                        resolved_list.append(str(x).strip())
            if resolved_list:
                c["value"] = resolved_list
                if len(resolved_list) > 1 and c.get("operator") == "=":
                    c["operator"] = "in"
            return

        v_str = str(v).strip()
        if _looks_like_type_key(v_str):
            return
        collected_original_type_names.append(v_str)
        try:
            # 根据v_str读取补充规则，获取要补充的工作项类型中文名称
            extras = get_supplementary_work_item_type_names(workspace, v_str)
            logger.info(
                "工作项类型补充规则查询: workspace=%s, raw_type=%r, supplementary_names=%s",
                workspace,
                v_str,
                extras,
            )
            # 先将大模型识别的工作项类型名转换为key，放在r中。km是{key: 显示名} 映射
            r, km = [], {}
            try:
                r, km = resolve_work_item_type_key(v_str, workspace, headers)
            except Exception as e_resolve:
                logger.warning(
                    "工作项类型主解析失败，将继续尝试补充规则: workspace=%s, raw_type=%r, error=%s",
                    workspace,
                    v_str,
                    e_resolve,
                )
            # 如果补充规则命中，则将补充工作项类型中文转换为key，同时与原始工作项类型key合并。km也需要合并
            if extras:
                r, km = supplement_resolved_work_item_type_keys(
                    v_str,
                    workspace,
                    headers,
                    r,
                    km,
                    extra_names=extras,
                )
            vals = r if isinstance(r, list) else [r]
            vals = [str(t).strip() for t in vals if t and str(t).strip()]
            if vals:
                if len(vals) > 1:
                    c["value"] = vals
                    if c.get("operator") == "=":
                        c["operator"] = "in"
                else:
                    c["value"] = vals[0]
        except Exception as e:
            logger.warning(
                "查询条件中工作项类型解析失败（仅记录，不中断）: value=%s, error=%s",
                v_str,
                e,
            )

    for c in query_condition.get("sourceClauses", []):
        _convert_clause(c)
    for c in query_condition.get("targetClauses", []):
        _convert_clause(c)
    return query_condition, list(dict.fromkeys(collected_original_type_names))


def _import_user_search_module():
    if not USER_SEARCH_MODULE.exists():
        return None
    spec = importlib.util.spec_from_file_location("user_search", USER_SEARCH_MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _search_user_by_name_for_send(user_name, auth_token, emp_no):
    if not auth_token or not emp_no:
        return {"status": "error", "message": "缺少认证信息"}
    mod = _import_user_search_module()
    if not mod or not hasattr(mod, "search_users"):
        return {"status": "error", "message": "user_search 模块不可用"}
    result = mod.search_users(emp_no, auth_token, (user_name or "").strip())
    if not result or result.get("status") != "success":
        return {
            "status": "error",
            "message": (
                result.get("message") if isinstance(result, dict) else "未找到用户"
            )
            or "未找到用户",
        }
    users_list = result.get("users", [])
    if not users_list:
        return {"status": "error", "message": f"未找到用户: {user_name}"}
    first_emp = users_list[0].get("empNo", "")
    if not first_emp:
        return {"status": "error", "message": f"用户 {user_name} 没有工号信息"}
    return {"status": "success", "empNo": first_emp}


def _process_recipients_mail(mail_to, auth_token, emp_no):
    if not mail_to or not isinstance(mail_to, str):
        return ""
    recipients = [r.strip() for r in mail_to.split(",") if r.strip()]
    out = []
    for r in recipients:
        if r.lower() in ("@me", "me") and emp_no:
            out.append(emp_no)
        elif r.isdigit() and len(r) <= 10:
            out.append(r)
        else:
            res = _search_user_by_name_for_send(r, auth_token, emp_no)
            if res.get("status") == "success":
                out.append(res["empNo"])
            else:
                raise ValueError(res.get("message", f"未找到用户: {r}"))
    return ",".join(out)


def _send_mail_inline(
    mail_title,
    mail_to,
    mail_body,
    mail_from=None,
    mail_lan="zh_CN",
    auth_token=None,
    emp_no=None,
):
    auth_token = auth_token or resolve_auth_token_from_env()
    emp_no = emp_no or resolve_emp_no_from_env()
    if not auth_token or not emp_no:
        return {"status": "error", "message": "缺少认证信息"}
    try:
        processed_to = _process_recipients_mail(mail_to, auth_token, emp_no)
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    url = URL_SEND_MAIL
    data = {
        "mailFrom": mail_from or "rdc@zte.com.cn",
        "mailLan": mail_lan,
        "mailTitle": mail_title,
        "mailTo": processed_to,
        "mailBody": mail_body,
    }
    req_headers = {
        "Content-Type": "application/json",
        "X-Auth-Value": auth_token,
        "X-Emp-No": emp_no,
        "X-Tenant-Id": "ZTE",
    }
    try:
        r = requests.post(url, headers=req_headers, data=json.dumps(data), timeout=60)
        return (
            {"status": "success"}
            if r.status_code == 200
            else {
                "status": "error",
                "message": f"邮件发送失败，状态码: {r.status_code}",
            }
        )
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _search_group_chat_for_send(group_name, auth_token, emp_no):
    if not auth_token or not emp_no:
        return {"status": "error", "message": "缺少认证信息"}
    url = (
        f"{URL_GROUP_CHAT_SEARCH}"
        f"?keyword={urllib.parse.quote(group_name)}&searchFor=groupChat"
    )
    req_headers = {
        "X-Auth-Value": auth_token,
        "X-Emp-No": emp_no,
        "X-Lang-Id": "zh_CN",
        "X-Tenant-Id": "ZTE",
    }
    try:
        resp = requests.get(url, headers=req_headers, timeout=30)
        if resp.status_code != 200:
            return {"status": "error", "message": f"查询群聊组失败: {resp.status_code}"}
        bo_list = resp.json().get("bo", [])
        if not bo_list:
            return {"status": "error", "message": f"未找到群聊组: {group_name}"}
        emp_no_val = bo_list[0].get("employeeNo", "")
        return (
            {"status": "success", "employeeNo": emp_no_val}
            if emp_no_val
            else {"status": "error", "message": "群聊组无 employeeNo"}
        )
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _process_to_recipients_icenter(to, auth_token, emp_no):
    if not to:
        return {"status": "success", "individuals": [], "groups": []}
    to_list = [to] if isinstance(to, str) else (to if isinstance(to, list) else [])
    individuals, groups = [], []
    for recipient in to_list:
        if not isinstance(recipient, str):
            continue
        if recipient.startswith("sip:"):
            groups.append(recipient)
        elif recipient.strip().lower() in ("@me", "me") and emp_no:
            individuals.append(emp_no)
        elif recipient.isdigit() and len(recipient) <= 10:
            individuals.append(recipient)
        else:
            ur = _search_user_by_name_for_send(recipient, auth_token, emp_no)
            if ur.get("status") == "success":
                individuals.append(ur["empNo"])
            else:
                gr = _search_group_chat_for_send(recipient, auth_token, emp_no)
                if gr.get("status") == "success":
                    groups.append(gr["employeeNo"])
    return {"status": "success", "individuals": individuals, "groups": groups}


def _send_single_icenter_message(
    message_content,
    subject,
    recipients,
    to_type,
    from_emp_no,
    auth_token,
    emp_no,
    view_url=None,
):
    url = URL_ICHAT_SHARE
    data = {
        "forward": True,
        "from": from_emp_no,
        "items": [
            {
                "multiLine": "false",
                "title": {"en": "Id", "zh": "详情"},
                "value": {"en": message_content, "zh": message_content},
            }
        ],
        "subject": {"en": subject or message_content, "zh": subject or message_content},
        "summary": {},
        "to": recipients,
        "toType": to_type,
    }
    if view_url:
        data["view"] = {"customMsgType": "1", "url": view_url}
    req_headers = {
        "X-Auth-Value": auth_token,
        "X-Emp-No": emp_no,
        "X-Lang-Id": "zh_CN",
        "X-Tenant-Id": "ZTE",
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(url, headers=req_headers, data=json.dumps(data), timeout=30)
        return (
            {"status": "success"}
            if r.status_code == 200
            else {"status": "error", "message": f"状态码: {r.status_code}"}
        )
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _send_chat_message_inline(
    message_content, subject=None, to=None, view_url=None, auth_token=None, emp_no=None
):
    auth_token = auth_token or resolve_auth_token_from_env()
    emp_no = emp_no or resolve_emp_no_from_env()
    if not auth_token or not emp_no:
        return {"status": "error", "message": "缺少认证信息"}
    res = _process_to_recipients_icenter(to, auth_token, emp_no)
    if res.get("status") != "success":
        return res
    individuals, groups = res.get("individuals", []), res.get("groups", [])
    if not individuals and not groups:
        return {"status": "error", "message": "没有有效的接收人"}
    from_emp = emp_no
    all_ok = True
    if individuals:
        if (
            _send_single_icenter_message(
                message_content,
                subject,
                individuals,
                1,
                from_emp,
                auth_token,
                emp_no,
                view_url,
            ).get("status")
            != "success"
        ):
            all_ok = False
    if groups:
        if (
            _send_single_icenter_message(
                message_content,
                subject,
                groups,
                2,
                from_emp,
                auth_token,
                emp_no,
                view_url,
            ).get("status")
            != "success"
        ):
            all_ok = False
    return {
        "status": "success" if all_ok else "error",
        "message": "iCenter 消息发送成功" if all_ok else "部分发送失败",
    }


def _send_email_after_query(
    input_data, output_text, base_config, headers, view_url=None
):
    """查询成功后按 is_send_email 发送邮件（内联逻辑）。"""
    try:
        body = (
            input_data.get("mail_body")
            or input_data.get("mailBody")
            or output_text
            or "查询完成"
        )
        if len(body) > 50000:
            body = body[:50000] + "\n...(内容已截断)"
        if view_url:
            body = body + f"\n\n查看完整工作项列表：{view_url}"
        auth = input_data.get("auth") or {}
        auth_token = auth.get("auth_token") or headers.get("X-Auth-Value")
        emp_no = auth.get("emp_no") or headers.get("X-Emp-No", "")
        mail_title = (
            input_data.get("mail_title")
            or input_data.get("mailTitle")
            or "工作项查询结果"
        )
        mail_to = input_data.get("mail_to") or input_data.get("mailTo") or emp_no
        logger.info(
            "发送邮件: 场景=查询, 标题=%s, 收件人=%s, 正文长度=%d 字符, view_url=%s",
            truncate_for_log(mail_title, 80),
            truncate_for_log(str(mail_to), 50),
            len(body),
            "有" if view_url else "无",
        )
        # 复用 notify_helper.send_mail，与 create/update 脚本保持一致的发送逻辑（包括收件人解析等）
        mail_headers = dict(headers)
        if auth_token:
            mail_headers["X-Auth-Value"] = auth_token
        if emp_no:
            mail_headers["X-Emp-No"] = emp_no
        result_send = notify_send_mail(
            mail_title=mail_title,
            mail_to=mail_to,
            mail_body=body,
            headers=mail_headers,
            mail_from=input_data.get("mailFrom", "rdc@zte.com.cn"),
            mail_lan=input_data.get("mailLan", "zh_CN"),
        )
        if result_send.get("status") == "error":
            logger.warning(
                "发送邮件未成功: 标题=%s, 错误=%s",
                truncate_for_log(mail_title, 60),
                result_send.get("message", ""),
            )
        else:
            logger.info(
                "发送邮件完成: 场景=查询, 标题=%s, 收件人=%s",
                truncate_for_log(mail_title, 60),
                truncate_for_log(str(mail_to), 50),
            )
    except Exception as e:
        logger.warning("发送邮件异常（不影响查询结果）: %s", e)


def _send_icenter_after_query(
    input_data, output_text, base_config, headers, view_url=None
):
    """查询成功后按 is_send_icenter 发送 iCenter 消息（内联逻辑）。"""
    try:
        message = (
            input_data.get("message")
            or input_data.get("mailBody")
            or output_text
            or "查询完成"
        )
        if len(message) > 10000:
            message = message[:10000] + "\n...(内容已截断)"
        auth = input_data.get("auth") or {}
        auth_token = auth.get("auth_token") or headers.get("X-Auth-Value")
        emp_no = auth.get("emp_no") or headers.get("X-Emp-No", "")
        to = input_data.get("icenter_to") or emp_no
        if isinstance(to, str) and "," in to:
            to = [x.strip() for x in to.split(",") if x.strip()]
        subject = (
            input_data.get("subject")
            or input_data.get("mail_title")
            or input_data.get("mailTitle")
            or "工作项查询结果"
        )
        view_url_val = (
            view_url or input_data.get("viewUrl") or input_data.get("view_url") or ""
        )
        logger.info(
            "发送 iCenter 消息: 场景=查询, 主题=%s, 接收人=%s, view_url=%s, 正文长度=%d 字符",
            truncate_for_log(subject, 80),
            truncate_for_log(str(to), 50),
            "有" if view_url_val else "无",
            len(message),
        )
        result_send = _send_chat_message_inline(
            message_content=message,
            subject=subject,
            to=to,
            view_url=view_url_val,
            auth_token=auth_token,
            emp_no=emp_no,
        )
        if result_send.get("status") == "error":
            logger.warning(
                "发送 iCenter 消息未成功: 主题=%s, 错误=%s",
                truncate_for_log(subject, 60),
                result_send.get("message", ""),
            )
        else:
            logger.info(
                "发送 iCenter 消息完成: 场景=查询, 主题=%s, 接收人=%s",
                truncate_for_log(subject, 60),
                truncate_for_log(str(to), 50),
            )
    except Exception as e:
        logger.warning("发送 iCenter 消息异常（不影响查询结果）: %s", e)


def query_work_items(query_data, base_config, headers, workspace=None):
    workspace = workspace or base_config.get("workspace", "")
    if not workspace:
        raise ValueError(f"未配置工作区，请在 {CONFIG_FILE} 中设置 api.workspace")
    if workspace == "SYSMySpace":
        raise ValueError(
            "禁止使用 SYSMySpace 查询不与我相关的工作项，请使用 query_my_work_item.py"
        )
    workspace = _normalize_workspace_single(workspace)
    url = URL_QUERY_WORK_ITEMS.format(workspace=workspace)
    _log_cfg = get_log_config(CONFIG_FILE)
    _max = _log_cfg.get("response_body_max_length", 500)
    _req_str = json.dumps(
        {"url": url, "body": query_data}, ensure_ascii=False, indent=2
    )
    logger.info("query_work_items 请求: %s", _req_str)

    r = requests.post(url, headers=headers, json=query_data, timeout=30)
    r.raise_for_status()
    out = r.json()
    _rsp_str = json.dumps(out, ensure_ascii=False, indent=2)
    logger.info("query_work_items 响应: %s", truncate_for_log(_rsp_str, _max))
    _raise_on_business_error(out, action="查询工作项")

    return out


def _raise_on_business_error(response, action="查询工作项"):
    """HTTP 成功但业务码失败时抛错（code.code != 0000）。"""
    if not isinstance(response, dict):
        return
    code_obj = response.get("code") or {}
    if not isinstance(code_obj, dict):
        return
    code_val = str(code_obj.get("code") or "").strip()
    if not code_val or code_val == "0000":
        return
    msg = str(code_obj.get("msg") or code_obj.get("msgId") or "未知错误").strip()
    raise ValueError(f"{action}失败：{msg}（接口 code={code_val}）")


def _parse_bool(s):
    if s is None:
        return False
    return str(s).strip().lower() in ("true", "1", "yes")


_RELATED_OPTIONS_ALLOWED = {
    "linksOneHopMustContain",
    "linksOneHopDoesNotContain",
    "linksOneHopMayContain",
}
_TREE_OPTIONS_ALLOWED = {"top", "related", "all"}
_RELATED_OPTIONS_ZH_TO_API = {
    "有匹配的关联工作项": "linksOneHopMustContain",
    "无匹配的关联工作项": "linksOneHopDoesNotContain",
    "所有工作项": "linksOneHopMayContain",
}
_TREE_OPTIONS_ZH_TO_API = {
    "基于顶级显示": "top",
    "基于关联显示": "related",
    "显示所有": "all",
}
_RELATED_OPTIONS_API_TO_ZH = {v: k for k, v in _RELATED_OPTIONS_ZH_TO_API.items()}
_TREE_OPTIONS_API_TO_ZH = {v: k for k, v in _TREE_OPTIONS_ZH_TO_API.items()}


def _normalize_related_options(option_value):
    """关联选项：支持中文语义值与接口枚举值，统一转换为接口枚举值。"""
    v = (option_value or "").strip()
    if not v:
        return ""
    if v in _RELATED_OPTIONS_ALLOWED:
        return v
    if v in _RELATED_OPTIONS_ZH_TO_API:
        return _RELATED_OPTIONS_ZH_TO_API[v]
    raise ValueError(
        "relatedOptions 仅支持：有匹配的关联工作项、无匹配的关联工作项、所有工作项"
    )


def _normalize_tree_options(option_value):
    """树选项：支持中文语义值与接口枚举值，统一转换为接口枚举值。"""
    v = (option_value or "").strip()
    if not v:
        return ""
    if v in _TREE_OPTIONS_ALLOWED:
        return v
    if v in _TREE_OPTIONS_ZH_TO_API:
        return _TREE_OPTIONS_ZH_TO_API[v]
    raise ValueError("treeOptions 仅支持：基于顶级显示、基于关联显示、显示所有")


def _query_mode_to_zh(query_mode):
    m = (query_mode or "").strip()
    if m == "oneHop":
        return "关联查询"
    if m == "tree":
        return "树查询"
    return "普通查询"


def _to_zh_related_options(option_value):
    v = (option_value or "").strip()
    if not v:
        return ""
    if v in _RELATED_OPTIONS_ZH_TO_API:
        return v
    return _RELATED_OPTIONS_API_TO_ZH.get(v, v)


def _to_zh_tree_options(option_value):
    v = (option_value or "").strip()
    if not v:
        return ""
    if v in _TREE_OPTIONS_ZH_TO_API:
        return v
    return _TREE_OPTIONS_API_TO_ZH.get(v, v)


def _build_parsed_summary_payload(
    query_mode,
    workspace,
    select_items,
    conditions,
    relate_conditions,
    input_data,
    order_by,
    limit,
):
    """
    构造面向用户展示的解析摘要（中文字段名，按查询模式输出必要字段）。
    """
    mode = (query_mode or "").strip()
    # 处理 workspace 可能是字符串或列表的情况
    workspace_str = ""
    if isinstance(workspace, list):
        workspace_str = ", ".join(str(w).strip() for w in workspace if w)
    else:
        workspace_str = (workspace or "").strip()
    base = {
        "查询模式": _query_mode_to_zh(mode),
        "工作区": workspace_str,
        "查询列": (select_items or "").strip(),
        "主对象条件": (conditions or "").strip(),
        "排序": (order_by or "").strip(),
        "条数上限": limit,
    }
    if mode == "oneHop":
        base.update(
            {
                "关联类型": (input_data.get("relate_type") or "").strip(),
                "关联选项": _to_zh_related_options(input_data.get("relatedOptions")),
                "关联侧条件": (relate_conditions or "").strip(),
            }
        )
    elif mode == "tree":
        base.update(
            {
                "树关联类型": (input_data.get("tree_type") or "").strip(),
                "树展示选项": _to_zh_tree_options(input_data.get("treeOptions")),
                "关联侧条件": (relate_conditions or "").strip(),
            }
        )
    return base


def resolve_relation_type_id(relate_type_zh):
    """将中文关联类型（如 子级/父级/相关）转换为 relation type id。"""
    t = (relate_type_zh or "").strip()
    if not t:
        raise ValueError("关联类型不能为空")
    rid = LINK_RELATION_TYPE_MAP.get(t)
    if not rid:
        raise ValueError(f"不支持的关联类型: {t}")
    return rid


def _validate_query_mode_input(input_data):
    """
    校验查询模式互斥：
    - 普通查询：不带 relate/tree 参数
    - 关联查询：带 relate_type（可带 relatedOptions）
    - 树查询：带 tree_type（可带 treeOptions）
    三者不可混用。
    """
    relate_type = (input_data.get("relate_type") or "").strip()
    tree_type = (input_data.get("tree_type") or "").strip()
    relate_conditions = (input_data.get("relate_conditions") or "").strip()
    related_options = (input_data.get("relatedOptions") or "").strip()
    tree_options = (input_data.get("treeOptions") or "").strip()

    if relate_type and tree_type:
        raise ValueError(
            "普通查询、关联查询、树查询不能混用：--relate-type 与 --tree-type 不能同时设置"
        )
    if related_options and not relate_type:
        raise ValueError("使用 --relatedOptions 时必须同时设置 --relate-type")
    if tree_options and not tree_type:
        raise ValueError("使用 --treeOptions 时必须同时设置 --tree-type")
    if relate_conditions and not (relate_type or tree_type):
        raise ValueError(
            "使用 --relate-conditions 时必须同时设置 --relate-type 或 --tree-type"
        )

    if related_options:
        _normalize_related_options(related_options)
    if tree_options:
        _normalize_tree_options(tree_options)


def _build_parser():
    p = argparse.ArgumentParser(
        prog="query_work_item.py",
        description=(
            "查询工作项（通用）：按查询列与条件查询；可选发邮件/发 iCenter 消息。"
            "「与我相关」类查询请使用 query_my_work_item.py（见下方说明）。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "与我相关查询:\n"
            "  若语义或入参实为「与我相关 / 分配给我 / 我创建的」等，误走到本脚本时，须立即停止执行，"
            "改为执行 query_my_work_item.py；本脚本不支持 SYSMySpace / 与我相关工作区查询。\n\n"
            "示例:\n"
            '  %(prog)s --select-items "标题,状态" --conditions "标题 like 测试"\n'
            '  %(prog)s --select-items "标识,标题,状态" --conditions "工作项类型=任务 AND 状态=进行中"\n'
            '  %(prog)s --select-items "标题,状态" --conditions "工作区=PLM AND 工作项类型=任务 AND 状态=进行中"\n'
            '  %(prog)s --select-items "标识,标题,状态" --conditions "状态=进行中" --relate-conditions "状态!=已关闭" --relate-type 子级 --relatedOptions 有匹配的关联工作项\n'
            '  %(prog)s --select-items "标识,标题,状态" --conditions "状态=进行中" --relate-conditions "优先级=P0" --tree-type 子级 --treeOptions 显示所有\n'
            '  %(prog)s --user-input "查询PLM、DT_TP下的任务" --select-items "标题,状态" --conditions "状态=进行中"\n\n'
            "关联场景主从规则:\n"
            "  - 主对象条件放 --conditions（sourceClauses）\n"
            "  - 关联对象/关联条件放 --relate-conditions（targetClauses）\n"
            "  - 一般语义按“前主后从”识别（前面对象=主对象，后面对象=关联对象）；若出现“Y 对应的 X”等反向词，以语义方向优先\n"
            '  - 例：查询PLM-3298581有哪些关联工作项 -> --conditions "标识=PLM-3298581"，--relate-conditions ""\n'
            '  - 例：查询市场需求下面的用户故事 -> --conditions "工作项类型=市场需求"，--relate-conditions "工作项类型=用户故事"\n'
        )
        % {"prog": "query_work_item.py"},
    )
    p.add_argument(
        "--select-items",
        "--selectItems",
        dest="select_items",
        default="",
        metavar="ITEMS",
        help="查询列，英文逗号分隔的字段名称，如 标题,状态,标识；不传则使用默认列：标识、标题、状态、工作项类型、更新时间。",
    )
    p.add_argument(
        "--conditions",
        "-c",
        dest="conditions",
        default="",
        metavar="CONDITIONS",
        help="主对象条件（sourceClauses）：支持 AND/OR、括号 () 组合与嵌套；可含工作区、工作项类型，如 工作区=PLM、工作项类型=任务 AND 状态=进行中、(状态=进行中 OR 状态=待处理) AND 优先级=P0；不传则无过滤。",
    )
    p.add_argument(
        "--relate-conditions",
        "--relateConditions",
        dest="relate_conditions",
        default="",
        metavar="CONDITIONS",
        help="关联对象/关联侧条件（targetClauses），格式与 --conditions 完全一致；仅关联查询/树查询使用。主对象条件不得放在本参数。",
    )
    p.add_argument(
        "--relate-type",
        "--relateType",
        dest="relate_type",
        default="",
        metavar="TYPE",
        help="关联查询类型中文名，如 子级、父级、相关、前置、后续；或 全部 表示查询所有类型的关联（与树查询互斥）。",
    )
    p.add_argument(
        "--relatedOptions",
        "--related-options",
        dest="related_options",
        default="",
        metavar="OPTION",
        help="关联选项：有匹配的关联工作项、无匹配的关联工作项、所有工作项（接口值分别为 linksOneHopMustContain、linksOneHopDoesNotContain、linksOneHopMayContain）。",
    )
    p.add_argument(
        "--tree-type",
        "--treeType",
        dest="tree_type",
        default="",
        metavar="TYPE",
        help="树查询类型中文名，如 子级、父级、相关、前置、后续（与关联查询互斥）。仅当用户语义明确关系类型时传入；未明确时不得传值。",
    )
    p.add_argument(
        "--treeOptions",
        "--tree-options",
        dest="tree_options",
        default="",
        metavar="OPTION",
        help="树选项：基于顶级显示、基于关联显示、显示所有（接口值分别为 top、related、all）。",
    )
    p.add_argument(
        "--order-by",
        "--orderBy",
        dest="order_by",
        default="",
        metavar="ORDER",
        help="排序，如 更新时间 desc、标题 asc, 状态 desc；不传则默认按更新时间倒序。",
    )
    p.add_argument(
        "--limit",
        "-n",
        dest="limit",
        default=None,
        metavar="N",
        type=int,
        help="返回条数上限，正整数，最大 10000；不传则默认 20。",
    )
    p.add_argument(
        "--is-send-email",
        "--is_send_email",
        dest="is_send_email",
        default="false",
        metavar="BOOL",
        help="是否发送邮件：true/false，默认 false。",
    )
    p.add_argument(
        "--is-send-icenter",
        "--is_send_icenter",
        dest="is_send_icenter",
        default="false",
        metavar="BOOL",
        help="是否发送 iCenter 消息：true/false，默认 false。",
    )
    p.add_argument(
        "--mail-to",
        "--mailTo",
        dest="mail_to",
        default=None,
        metavar="RECIPIENTS",
        help="邮件接收人，多人英文逗号分隔。",
    )
    p.add_argument(
        "--icenter-to",
        "--icenter_to",
        dest="icenter_to",
        default=None,
        metavar="RECIPIENTS",
        help="iCenter 消息接收人，多人英文逗号分隔。",
    )
    p.add_argument(
        "--user-input",
        "--userInput",
        dest="user_input",
        default="",
        metavar="TEXT",
        help="用户的原始输入，必须是用户真实完整的一句话。若为与我相关类意图，勿用本脚本，应改用 query_my_work_item.py。",
    )
    return p


def _args_to_input_data(args):
    data = {
        "select_items": (args.select_items or "").strip(),
        "conditions": (args.conditions or "").strip(),
        "relate_conditions": (getattr(args, "relate_conditions", "") or "").strip(),
        "relate_type": (getattr(args, "relate_type", "") or "").strip(),
        "relatedOptions": (getattr(args, "related_options", "") or "").strip(),
        "tree_type": (getattr(args, "tree_type", "") or "").strip(),
        "treeOptions": (getattr(args, "tree_options", "") or "").strip(),
        "order_by": (args.order_by or "").strip()
        if getattr(args, "order_by", None)
        else "",
        "limit": getattr(args, "limit", None),
    }
    data["is_send_email"] = _parse_bool(args.is_send_email)
    data["is_send_icenter"] = _parse_bool(args.is_send_icenter)
    if args.mail_to:
        data["mailTo"] = args.mail_to
    if args.icenter_to:
        data["icenter_to"] = args.icenter_to
    if getattr(args, "user_input", None):
        data["userInput"] = (args.user_input or "").strip()
    return data


def main():
    parser = _build_parser()
    args = parser.parse_args()
    input_data = _args_to_input_data(args)
    raw_user_input = (input_data.get("userInput") or "").strip()
    if raw_user_input:
        logger.info(
            "原始用户输入: %s",
            raw_user_input[:500] if len(raw_user_input) > 500 else raw_user_input,
        )
    select_items_str = (
        input_data.get("select_items") or input_data.get("selectItems") or ""
    )
    conditions_str = input_data.get("conditions") or ""
    relate_conditions_str = input_data.get("relate_conditions") or ""
    order_by_str = input_data.get("order_by") or input_data.get("orderBy") or ""
    limit = input_data.get("limit")
    logger.info(
        "命令行入参: select_items=%s, conditions=%s, relate_conditions=%s, relate_type=%s, relatedOptions=%s, tree_type=%s, treeOptions=%s, order_by=%s, limit=%s",
        select_items_str[:200] if len(select_items_str) > 200 else select_items_str,
        conditions_str[:200] if len(conditions_str) > 200 else conditions_str,
        relate_conditions_str[:200]
        if len(relate_conditions_str) > 200
        else relate_conditions_str,
        (input_data.get("relate_type") or ""),
        (input_data.get("relatedOptions") or ""),
        (input_data.get("tree_type") or ""),
        (input_data.get("treeOptions") or ""),
        order_by_str,
        limit,
    )
    logger.info(
        "input_data: %s",
        json.dumps(sanitize_for_log(input_data), ensure_ascii=False, default=str),
    )
    try:
        _validate_query_mode_input(input_data)
        base_config = load_base_config()
        headers = get_auth_headers(input_data, base_config, logger=logger)
        # 解析工作区： 从语义中拿 -> 从最近访问记录中拿(会结合语义) -> 从配置中拿
        workspace, workspace_source, resolve_err = resolve_workspace_with_source(
            None,
            base_config.get("workspace", ""),
            headers,
            raw_user_input=raw_user_input,
        )
        if resolve_err:
            safe_print(resolve_err, file=sys.stderr)
            sys.exit(1)
        if workspace == "SYSMySpace":
            safe_print(
                "错误: 禁止使用 SYSMySpace，请使用 query_my_work_item.py",
                file=sys.stderr,
            )
            sys.exit(1)
        if str(SCRIPTS_ROOT) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_ROOT))

        # 解析select-items的字段 和 conditions字段和值
        query_data = build_query_data_from_select_and_conditions(
            select_items_str or None,
            conditions_str or None,
            workspace,
            headers,
            for_my_workspace=False,
            order_by_str=order_by_str or None,
            limit=limit,
            raw_user_input=raw_user_input,
        )
        # 工作区来自 AI 时：合并进已有工作区条件（=→in）或补充 System_WorkspaceKey 子句
        try:
            apply_workspace_clause_to_query_condition(
                query_data.setdefault("queryCondition", {}),
                workspace,
                workspace_source,
            )
        except Exception as e:
            logger.warning(
                "自动合并/补充工作区条件时发生异常（忽略，不影响查询）: %s", e
            )

        # 查询模式：普通 / 关联 / 树（三者互斥）
        query_condition = query_data.get("queryCondition") or {}
        relate_type = (input_data.get("relate_type") or "").strip()
        tree_type = (input_data.get("tree_type") or "").strip()
        target_clauses = []
        if relate_type or tree_type:
            # 解析关联查询条件relateConditions
            target_query_data = build_query_data_from_select_and_conditions(
                None,
                relate_conditions_str or None,
                workspace,
                headers,
                for_my_workspace=False,
                order_by_str=None,
                limit=None,
                raw_user_input=raw_user_input,
            )
            target_clauses = (target_query_data.get("queryCondition") or {}).get(
                "sourceClauses"
            ) or []
            # 关联条件如果无任何工作区，不需要追加AI识别出来的工作区条件。
            # 但是这里面有个问题，如果在自然语言中的关联条件确实提到了工作区，这个时候怎么办？
            # target_clauses = append_workspace_clause_if_needed(
            #     target_clauses, workspace, workspace_source
            # )
        if relate_type:
            # "全部"/"所有" 表示查询所有类型的关联，对应 API 的 relatedType=[]
            if relate_type.strip() in ("全部", "所有", "所有类型"):
                relation_ids = []
            else:
                relation_ids = [resolve_relation_type_id(relate_type)]
            query_condition["type"] = "oneHop"
            query_condition["removeTop"] = True
            query_condition["relatedType"] = relation_ids
            related_options_api = (
                _normalize_related_options(input_data.get("relatedOptions"))
                or "linksOneHopMustContain"
            )
            query_condition["relatedOptions"] = related_options_api
            query_condition["targetClauses"] = target_clauses
            logger.info(
                "启用关联查询: relate_type=%s, relatedType=%s, relatedOptions=%s",
                relate_type,
                relation_ids,
                related_options_api,
            )
        elif tree_type:
            relation_id = resolve_relation_type_id(tree_type)
            query_condition["type"] = "tree"
            query_condition["removeTop"] = True
            query_condition["relatedType"] = [relation_id]
            tree_options_api = (
                _normalize_tree_options(input_data.get("treeOptions")) or "top"
            )
            query_condition["treeOptions"] = tree_options_api
            query_condition["targetClauses"] = target_clauses
            logger.info(
                "启用树查询: tree_type=%s, relation_id=%s, treeOptions=%s",
                tree_type,
                relation_id,
                tree_options_api,
            )
        query_data["queryCondition"] = query_condition

        multi_match = query_data.pop("_multi_match_fields", None) or {}
        query_data["queryCondition"] = add_default_logical_operator(
            query_data["queryCondition"]
        )
        # 将查询条件中的工作项类型中文值转换为工作项类型key；保留原始用语供心智模型
        _qc, _wit_orig = convert_work_item_type_values(
            query_data["queryCondition"], workspace, headers
        )
        query_data["queryCondition"] = _qc
        query_data["onlyResult"] = True
        query_data["crossWorkspace"] = True
        result = query_work_items(query_data, base_config, headers, workspace)
        data = result.get("data")
        bo_res = (
            (result.get("bo") or {}).get("result")
            if isinstance(result.get("bo"), dict)
            else {}
        )
        if isinstance(data, dict):
            total = (
                data.get("total")
                if data.get("total") is not None
                else len(data.get("items", []))
            )
        elif isinstance(bo_res, dict):
            pd = bo_res.get("pageDetail")
            total = (
                pd.get("totalRow")
                if isinstance(pd, dict)
                else bo_res.get("totalRow") or len(bo_res.get("items", []))
            )
        else:
            total = 0
        logger.info("查询完成，共 %s 条", total)
        # 仅当查询结果非空时保存视图并获取跳转链接；结果为空不生成视图
        view_url = None
        if total and total > 0:
            # 生成视图，并返回链路
            view_url = get_view_url_after_query(
                workspace, query_data["queryCondition"], input_data, headers
            )
            if view_url:
                input_data["viewUrl"] = view_url
        if base_config.get("format_as_markdown", True):
            try:
                out = format_result_as_markdown(
                    result,
                    query_data,
                    base_config=base_config,
                    workspace=workspace,
                    headers=headers,
                )
            except TypeError as e:
                if "base_config" in str(e) or "unexpected keyword" in str(e).lower():
                    out = format_result_as_markdown(result, query_data)
                else:
                    raise
        else:
            out = json.dumps(result, ensure_ascii=False, indent=2)
        logger.info(
            "input_data_changed: %s",
            json.dumps(sanitize_for_log(input_data), ensure_ascii=False, default=str),
        )
        if view_url and base_config.get("format_as_markdown", True):
            out = out + f"\n\n**查看完整工作项列表：** [{view_url}]({view_url})"
        if multi_match and (multi_match.get("select") or multi_match.get("where")):
            out = (
                out
                + "\n\n"
                + get_mindset_message(
                    "field_multi_match",
                    select_fields=multi_match.get("select", []),
                    where_fields=multi_match.get("where", []),
                )
            )
        query_mode = (query_data.get("queryCondition") or {}).get("type")
        parsed_summary_payload = _build_parsed_summary_payload(
            query_mode=query_mode,
            workspace=workspace,
            select_items=select_items_str,
            conditions=conditions_str,
            relate_conditions=relate_conditions_str,
            input_data=input_data,
            order_by=order_by_str,
            limit=limit,
        )
        parsed_summary = format_parsed_summary(
            parsed_summary_payload, title="本次语义解析结果"
        )
        # 查询结果为空时：默认不发送通知（可由 config.query.send_notify_when_empty_result 开启）
        send_notify_when_query_empty = bool(
            base_config.get("send_notify_when_query_empty", False)
        )
        requested_send_email = input_data.get("is_send_email") is True
        requested_send_icenter = input_data.get("is_send_icenter") is True
        skip_notify_on_empty = (
            total <= 0
            and (requested_send_email or requested_send_icenter)
            and not send_notify_when_query_empty
        )
        notify_skip_hint = ""
        if skip_notify_on_empty:
            notify_skip_hint = (
                "\n\n---\n"
                "**通知已跳过**：本次查询结果为 0。尽管你在语义中要求发送邮件或消息，默认不发送。"
            )
            logger.info(
                "查询结果为空且配置禁止空结果发送，已跳过通知: send_email=%s, send_icenter=%s",
                requested_send_email,
                requested_send_icenter,
            )

        # 心智模式仅追加到打印输出，供后续 AI 理解；邮件与 iCenter 不携带心智模式
        mindset_text = get_mindset_message("general_query_result", total=total)
        extra_mindset = build_query_context_mindset_extras(
            query_data=query_data,
            raw_user_input=raw_user_input or None,
            conditions_str=conditions_str,
            relate_conditions_str=relate_conditions_str or None,
            work_item_type_original_names=_wit_orig,
        )
        if extra_mindset:
            mindset_text = (
                (mindset_text + "\n\n" + extra_mindset)
                if mindset_text
                else extra_mindset
            )
        out_with_hint = out + notify_skip_hint if notify_skip_hint else out
        body_with_mindset = (
            (out_with_hint + "\n\n" + mindset_text) if mindset_text else out_with_hint
        )
        out_for_display = (
            (parsed_summary + "\n" + body_with_mindset)
            if parsed_summary
            else body_with_mindset
        )
        if requested_send_email and not skip_notify_on_empty:
            _send_email_after_query(
                input_data, out, base_config, headers, view_url=view_url
            )
        if requested_send_icenter and not skip_notify_on_empty:
            _send_icenter_after_query(
                input_data, out, base_config, headers, view_url=view_url
            )
        #logger.info(out_for_display)
        safe_print(out_for_display)
    except ValueError as e:
        logger.warning("执行终止: %s", e)
        err_out = f"错误: {e}"
        mindset_text = get_mindset_message("query_error")
        if mindset_text and mindset_text not in err_out:
            err_out = err_out + "\n\n" + mindset_text
        safe_print(err_out)
        sys.exit(1)
    except Exception as e:
        logger.exception("执行失败")
        err_out = f"错误: {e}"
        mindset_text = get_mindset_message("query_error")
        if mindset_text and mindset_text not in err_out:
            err_out = err_out + "\n\n" + mindset_text
        safe_print(err_out)
        sys.exit(1)


if __name__ == "__main__":
    main()
