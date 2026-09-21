#!/usr/bin/env python
"""更新工作项脚本。支持命令行参数（--work-item-id、--fields 等）；工作项标识支持多个，以英文逗号分隔。"""

import sys
import json
import os
import re
import copy
import requests
import logging
import argparse
import urllib.parse
from pathlib import Path
from datetime import datetime

# 脚本所在目录为 atomic/；技能根目录为 scripts 的上级
SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_ROOT = SCRIPT_DIR.parent
SKILL_DIR = SCRIPTS_ROOT.parent
CONFIG_FILE = SKILL_DIR / "assets" / "config.json"
USER_SEARCH_MODULE = SCRIPTS_ROOT / "common" / "user_search.py"
# 容器内 skill 为只读挂载（/mnt/skills/<id>），日志改写到可写工作区，并按 skill 分目录
LOG_DIR = (Path("/workspace/.skill-logs") / SKILL_DIR.name) if Path("/workspace").is_dir() else (SKILL_DIR / "logs")
LOG_FILE = LOG_DIR / f"update_work_item_{datetime.now().strftime('%Y%m%d')}.log"

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))
from common.log_helper import get_log_config, truncate_for_log, get_logger, ENABLE_LOG, safe_print, sanitize_for_log, truncate_subprocess_output_for_log, mask_emp_no_for_log, setup_logging, format_parsed_summary
from common.field_resolver import fetch_fields, resolve_field_key
from common.work_item_type_resolver import resolve_work_item_type_key
from common.mindset_messages import get_mindset_message
from common.auth_helper import (
    get_auth_headers,
    resolve_auth_token_from_env,
    resolve_emp_no_from_env,
)
from common import rdcloud_api

if ENABLE_LOG:
    setup_logging(LOG_FILE, add_stderr=True)
logger = get_logger(__name__)

# 导入 user_search 模块
def import_user_search_module():
    """动态导入 user_search 模块"""
    import importlib.util
    if USER_SEARCH_MODULE.exists():
        spec = importlib.util.spec_from_file_location("user_search", USER_SEARCH_MODULE)
        user_search_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(user_search_module)
        return user_search_module
    return None

def search_users(emp_no: str, token: str, keyword: str):
    """搜索用户（封装 user_search 模块）"""
    try:
        user_search_module = import_user_search_module()
        if user_search_module and hasattr(user_search_module, 'search_users'):
            result = user_search_module.search_users(emp_no, token, keyword)
            return result
        logger.warning("user_search 模块不可用")
    except Exception as e:
        logger.warning(f"搜索用户失败: {str(e)}")
    return None

# 需要将“人”解析为工号的字段 key（指派给→System_AppointedTo，处理人→System_AssignedTo）；value 为 @me/me 时用当前用户，否则走 search_users 取第一个
ASSIGNEE_FIELD_KEYS = frozenset({"System_AppointedTo", "System_AssignedTo"})
STATE_FIELD_KEY = "System_State"


def _is_me_value(value):
    """判断是否为“我”的表述：@me / me / @Me / ME 等（忽略首尾空格，不区分大小写）"""
    if not value or not isinstance(value, str):
        return False
    return value.strip().lower() in ("@me", "me")


def _should_append_after_by_user_input(user_input: str, field_name: str) -> bool:
    """根据用户原始输入判断某字段是否表达为“追加”语义。"""
    ui = (user_input or "").strip()
    fn = (field_name or "").strip()
    if not ui or not fn:
        return False
    return (
        f"{fn}追加" in ui
        or f"{fn} 追加" in ui
        or f"给{fn}追加" in ui
        or f"给{fn} 追加" in ui
    )


def _apply_modify_type_from_user_input(fields, user_input):
    """
    按用户语义给命中的字段补充 modifyType=appendAfter。
    仅处理未显式传 modifyType 的字段；已传则尊重入参。
    """
    if not isinstance(fields, list):
        return
    for item in fields:
        if not isinstance(item, dict):
            continue
        if (item.get("modifyType") or "").strip():
            continue
        original_key = (item.get("_original_key") or item.get("key") or "").strip()
        if _should_append_after_by_user_input(user_input, original_key):
            item["modifyType"] = "appendAfter"


def _split_append_value_to_list(value):
    """
    追加场景值拆分：
    - 输入为字符串时，按中英文分隔符拆分为数组；
    - 输入已是数组时保持不变；
    - 其它类型保持原值。
    """
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    parts = [
        x.strip()
        for x in re.split(r"[，,、；;]+", text)
        if isinstance(x, str) and x.strip()
    ]
    if len(parts) <= 1:
        return value
    return parts


def _normalize_append_fields_value(fields):
    """
    仅对 modifyType=appendAfter 的字段做值归一化：
    - value 为字符串且可按分隔符拆分时，转为数组。
    """
    if not isinstance(fields, list):
        return
    for item in fields:
        if not isinstance(item, dict):
            continue
        if (item.get("modifyType") or "").strip() != "appendAfter":
            continue
        if "value" not in item:
            continue
        item["value"] = _split_append_value_to_list(item.get("value"))


# 以下两个函数为旧版“基于 fetch_fields 元数据判定多值”的实现。
# 当前主流程已切换为“基于更新初始化接口 init_fields 的 multiValue 判定”
# （见 _bind_init_meta_for_fields / _normalize_multi_value_fields_value_by_init_meta）。
# 先注释保留，后续稳定后可删除。
#
# def _is_multi_value_field_meta(field_item):
#     """根据字段元数据判断是否多值字段（尽量兼容不同接口字段命名）。"""
#     if not isinstance(field_item, dict):
#         return False
#     # 优先：按元数据显式 multiValue 判定
#     mv = field_item.get("multiValue")
#     if isinstance(mv, bool):
#         return mv
#     if isinstance(mv, str) and mv.strip().lower() in ("true", "1", "yes", "y", "t"):
#         return True
#     for flag_key in ("multiple", "multi", "isMulti", "isMultiple", "multiValue", "allowMultiple"):
#         v = field_item.get(flag_key)
#         if isinstance(v, bool) and v:
#             return True
#         if isinstance(v, str) and v.strip().lower() in ("true", "1", "yes", "y", "t"):
#             return True
#     # 部分接口用控件/值类型表达多选
#     for text_key in ("controlType", "valueType", "type"):
#         tv = (field_item.get(text_key) or "").strip().lower()
#         if "multi" in tv:
#             return True
#     return False
#
#
# def _normalize_multi_value_fields_value(fields, field_items):
#     """
#     非追加场景下的多值字段兼容：
#     - 仅当字段元数据可判定为“多值”且 value 为可拆分字符串时，转为数组。
#     """
#     if not isinstance(fields, list):
#         return
#     field_meta_map = {}
#     for it in field_items or []:
#         if not isinstance(it, dict):
#             continue
#         k = (it.get("key") or "").strip()
#         if k:
#             field_meta_map[k] = it
#     for item in fields:
#         if not isinstance(item, dict):
#             continue
#         if (item.get("modifyType") or "").strip() == "appendAfter":
#             continue
#         key = (item.get("key") or "").strip()
#         if not key:
#             continue
#         meta = field_meta_map.get(key)
#         if not _is_multi_value_field_meta(meta):
#             continue
#         if "value" not in item:
#             continue
#         item["value"] = _split_append_value_to_list(item.get("value"))


def _build_init_fields_lookup(init_fields):
    """构建初始化字段索引：支持按 key/name 查找字段元数据。"""
    by_key = {}
    by_name = {}
    for it in init_fields or []:
        if not isinstance(it, dict):
            continue
        k = (it.get("key") or "").strip()
        n = (it.get("name") or "").strip()
        if k:
            by_key[k] = it
        if n:
            by_name[n] = it
    return by_key, by_name


def _bind_init_meta_for_fields(fields, init_fields):
    """
    用更新初始化接口返回的 fields 元数据标注用户入参字段：
    - 匹配优先级：resolved key -> original key 对应 init key -> original key 对应 init name
    - 标注字段：_meta_type / _meta_multi_value
    """
    by_key, by_name = _build_init_fields_lookup(init_fields)
    for f in fields or []:
        if not isinstance(f, dict):
            continue
        resolved_key = (f.get("key") or "").strip()
        original_key = (f.get("_original_key") or "").strip()
        meta = None
        if resolved_key:
            meta = by_key.get(resolved_key)
        if meta is None and original_key:
            meta = by_key.get(original_key)
        if meta is None and original_key:
            meta = by_name.get(original_key)
        if not isinstance(meta, dict):
            continue
        f["_meta_type"] = (meta.get("type") or "").strip()
        f["_meta_multi_value"] = bool(meta.get("multiValue") is True)


def _normalize_multi_value_fields_value_by_init_meta(fields):
    """按初始化元数据 multiValue=true 归一化 value（字符串拆分为列表）。"""
    for item in fields or []:
        if not isinstance(item, dict):
            continue
        if (item.get("modifyType") or "").strip() == "appendAfter":
            continue
        if item.get("_meta_multi_value") is not True:
            continue
        if "value" not in item:
            continue
        item["value"] = _split_append_value_to_list(item.get("value"))


def _resolve_person_to_emp_no(value, emp_no, token, log=None):
    """
    将人员取值解析为工号。
    - value 为 @me/me/@Me/ME → 返回当前用户工号 emp_no；
    - 否则调用 search_users(keyword=value)，取第一个匹配用户的工号；多结果取第一个，失败或为空则返回原 value。
    """
    log = log or logger
    if _is_me_value(value):
        log.info("指派给解析: value=%s -> 当前用户工号 %s", value, mask_emp_no_for_log(emp_no))
        return emp_no or value
    if not token or not emp_no:
        log.warning("指派给解析: 缺少 token/emp_no，无法调用 search_users，保留原值")
        return value
    result = search_users(emp_no, token, value.strip())
    if not result or result.get("status") != "success":
        log.warning("指派给解析: search_users 未成功 keyword=%s，保留原值", value)
        return value
    users = result.get("users") or []
    if not users:
        log.warning("指派给解析: 未找到用户 keyword=%s，保留原值", value)
        return value
    first_emp_no = users[0].get("empNo") or ""
    if first_emp_no:
        log.info("指派给解析: keyword=%s -> 工号 %s（共 %d 个结果，取第一个）", value, mask_emp_no_for_log(first_emp_no), len(users))
    return first_emp_no if first_emp_no else value

def load_base_config():
    """加载基础配置（仅非敏感信息）"""
    logger.info("开始加载基础配置")
    base_config = {
        "base_url": rdcloud_api.BASE_WIC_REST,
        "workspace": None,
        "tenant_id": "ZTE",
        "format_as_markdown": False
    }
    
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                api_config = config.get("api", {})
                if "workspace" in api_config:
                    base_config["workspace"] = api_config["workspace"]
                base_config["tenant_id"] = api_config.get("tenant_id", base_config["tenant_id"])
                format_config = config.get("format", {})
                base_config["format_as_markdown"] = format_config.get("as_markdown", base_config["format_as_markdown"])
            logger.info(f"配置文件加载成功: workspace={base_config.get('workspace', 'N/A')}, format_as_markdown={base_config['format_as_markdown']}")
        except Exception as e:
            logger.warning(f"配置文件读取失败，使用默认值: {str(e)}")
    else:
        logger.warning(f"配置文件不存在: {CONFIG_FILE}，使用默认值")
    
    return base_config

def extract_workspace_key(work_item_id):
    """从工作项ID中提取工作区Key"""
    if not work_item_id:
        return ""
    last_dash_index = work_item_id.rfind('-')
    if last_dash_index == -1:
        return work_item_id
    return work_item_id[:last_dash_index]

def format_result_as_markdown(result, work_item_id):
    """将更新结果格式化为 Markdown 格式"""
    logger.info("开始格式化更新结果为 Markdown")
    if not result or not isinstance(result, dict):
        logger.warning("更新结果格式不正确，返回原始 JSON")
        return json.dumps(result, ensure_ascii=False, indent=2)
    
    # 从 result["bo"] 中提取字段
    bo_data = result.get("bo", {})
    if not bo_data or not isinstance(bo_data, dict):
        bo_data = result
    
    # 提取字段值
    title = bo_data.get("System_Title", "")
    work_item_type = ""
    
    # 提取工作项类型名称
    work_item_type_obj = bo_data.get("System_WorkItemType")
    if isinstance(work_item_type_obj, dict):
        work_item_type = work_item_type_obj.get("name", "") or work_item_type_obj.get("nameZh", "")
    elif isinstance(work_item_type_obj, str):
        work_item_type = work_item_type_obj
    
    # 生成跳转链接
    if work_item_id:
        workspace_key = extract_workspace_key(work_item_id)
        link_url = rdcloud_api.studio_workitem_transmit_url(work_item_id)
        title_link = f"[{title}]({link_url})" if title else ""
        id_link = f"[{work_item_id}]({link_url})"
    else:
        title_link = title if title else ""
        id_link = work_item_id if work_item_id else ""
    
    # 生成 Markdown
    md_lines = []
    md_lines.append("# 工作项更新成功\n\n")
    md_lines.append("| 字段 | 值 |\n")
    md_lines.append("|------|------|\n")
    md_lines.append(f"| 标识 | {id_link} |\n")
    md_lines.append(f"| 标题 | {title_link} |\n")
    md_lines.append(f"| 类型 | {work_item_type} |\n")
    
    return "".join(md_lines)

def mask_sensitive_header(header_name, header_value):
    """掩码敏感请求头信息"""
    if not header_value:
        return ""
    if header_name.lower() in ['x-auth-value', 'authorization']:
        if len(header_value) > 6:
            return f"{header_value[:3]}***{header_value[-3:]}"
        else:
            return "***"
    return header_value


def _is_empty_field_value(v):
    if v is None:
        return True
    if isinstance(v, str) and (v or "").strip() == "":
        return True
    if isinstance(v, list) and len(v) == 0:
        return True
    if isinstance(v, dict) and len(v) == 0:
        return True
    return False


def _resolve_work_item_type_key_for_update(work_item_id, work_item_type_key, headers):
    """更新预校验需要 workItemTypeKey；优先用入参，缺失时尝试从详情接口推断。"""
    wtk = (work_item_type_key or "").strip()
    if wtk:
        return wtk
    try:
        detail = rdcloud_api.query_work_item_details([work_item_id], headers)
        items = ((detail.get("bo") or {}).get("items") or []) if isinstance(detail, dict) else []
        if not items:
            return ""
        fields = items[0].get("fields") or []
        if isinstance(fields, dict):
            fields = list(fields.values())
        for f in fields:
            if not isinstance(f, dict):
                continue
            key = (f.get("key") or f.get("referenceName") or "").strip()
            if key != "System_WorkItemType":
                continue
            value = f.get("value")
            if isinstance(value, dict):
                return (value.get("key") or "").strip()
            if isinstance(value, str) and value.strip():
                return value.strip()
            break
    except Exception as e:
        logger.warning("从工作项详情推断 workItemTypeKey 失败: %s", e)
    return ""


def _collect_required_missing_from_update_init(init_fields, user_fields):
    """
    更新初始化必填判定：
    - 用户本次请求中提到了必填字段：通过（不再提示）；
    - 未给时根据 persistentValue/value 判定是否缺失。
    """
    key_to_user = {}
    for f in user_fields or []:
        if not isinstance(f, dict):
            continue
        k = (f.get("key") or "").strip()
        if k:
            key_to_user[k] = f.get("value")
    missing = []
    for item in init_fields or []:
        if not isinstance(item, dict) or item.get("required") is not True:
            continue
        key = (item.get("key") or "").strip()
        if not key:
            continue
        # 只要用户本次请求提到了该字段，就不再纳入“缺失必填”提示
        if key in key_to_user:
            continue
        persistent_value = item.get("persistentValue")
        current_value = item.get("value")
        persistent_empty = _is_empty_field_value(persistent_value)
        current_empty = _is_empty_field_value(current_value)
        if not current_empty:
            continue
        # 当前值为空时，persistentValue 有值（或两者都为空）都视为缺失
        if (not persistent_empty and current_empty) or (persistent_empty and current_empty):
            missing.append(key)
    return missing


def _collect_event_required_missing(event_response):
    """从 PUT event 响应中收集 required=true 且 persistentValue 为空字段。"""
    bo = (event_response.get("bo") or {}) if isinstance(event_response, dict) else {}
    fields = bo.get("fields") or bo.get("fieldList") or []
    if isinstance(fields, dict):
        fields = list(fields.values())
    missing = []
    for item in fields:
        if not isinstance(item, dict):
            continue
        required = item.get("required") is True or item.get("isRequired") is True
        if not required:
            continue
        key = (item.get("key") or "").strip()
        if not key:
            continue
        if _is_empty_field_value(item.get("persistentValue")):
            missing.append(key)
    return missing


def _resolve_state_value_for_event(raw_value, available_states):
    """仅用于 event 校验：将状态值映射为初始化返回的完整状态对象。"""
    if isinstance(raw_value, dict):
        return raw_value
    value_text = (str(raw_value).strip() if raw_value is not None else "")
    if not value_text:
        return raw_value
    for st in available_states or []:
        if not isinstance(st, dict):
            continue
        candidates = [
            (st.get("nameZh") or "").strip(),
            (st.get("name") or "").strip(),
            (st.get("nameEn") or "").strip(),
            (st.get("key") or "").strip(),
        ]
        if value_text in [c for c in candidates if c]:
            return st
    return raw_value


def _resolve_person_to_user_object_for_event(value, emp_no, token, log=None):
    """
    仅用于 event 校验：若人员值可唯一匹配，返回用户完整对象；否则返回原值。
    """
    log = log or logger
    if value is None:
        return value
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return value
    raw = value.strip()
    if not raw:
        return value
    if _is_me_value(raw):
        return {"employeeNo": emp_no} if emp_no else value
    if not token or not emp_no:
        return value
    result = search_users(emp_no, token, raw)
    users = (result or {}).get("users") if isinstance(result, dict) else None
    if isinstance(users, list) and len(users) == 1 and isinstance(users[0], dict):
        log.info("event 人员对象化: keyword=%s 命中唯一用户，使用完整对象", raw)
        return users[0]
    return value


def _build_update_event_fields(fields, field_items, available_states=None, user_type_keys=None, emp_no="", token=""):
    event_fields = []
    for f in fields:
        if not isinstance(f, dict):
            continue
        key = (f.get("key") or "").strip()
        if not key:
            continue
        field_type = "string"
        for it in field_items or []:
            if not isinstance(it, dict):
                continue
            if (it.get("key") or "").strip() == key:
                field_type = (it.get("type") or "string").strip() or "string"
                break
        value_for_event = f.get("value")
        if key in (user_type_keys or set()) or key in ASSIGNEE_FIELD_KEYS:
            if isinstance(value_for_event, str):
                value_for_event = _resolve_person_to_user_object_for_event(value_for_event, emp_no, token, logger)
            elif isinstance(value_for_event, list):
                value_for_event = [
                    _resolve_person_to_user_object_for_event(x, emp_no, token, logger) if isinstance(x, str) else x
                    for x in value_for_event
                ]
        if key == STATE_FIELD_KEY:
            value_for_event = _resolve_state_value_for_event(value_for_event, available_states or [])
        event_fields.append({"key": key, "value": value_for_event, "type": field_type})
    return event_fields


def _raise_update_required_error(missing_keys, field_items=None, init_fields=None):
    init_name_map = {}
    for item in init_fields or []:
        if not isinstance(item, dict):
            continue
        k = (item.get("key") or "").strip()
        if k and item.get("name"):
            init_name_map[k] = item.get("name")
    field_name_map = {}
    for item in field_items or []:
        if not isinstance(item, dict):
            continue
        k = (item.get("key") or "").strip()
        if k:
            field_name_map[k] = item.get("nameZh") or item.get("name") or ""
    names = []
    for k in missing_keys:
        n = init_name_map.get(k) or field_name_map.get(k) or k
        names.append(f"「{n}」（{k}）")
    raise ValueError("更新前校验发现必填字段缺失，请补充后重试：" + "、".join(names))


def _validate_state_candidate(fields, available_states):
    """若用户修改状态字段，校验其值是否在 availableStates.nameZh 中。"""
    state_field = None
    for f in fields or []:
        if not isinstance(f, dict):
            continue
        if (f.get("key") or "").strip() == STATE_FIELD_KEY:
            state_field = f
            break
    if state_field is None:
        return
    state_value = state_field.get("value")
    state_value_name = state_value.get("nameZh") if isinstance(state_value, dict) else state_value
    state_value_name = (str(state_value_name).strip() if state_value_name is not None else "")
    allowed = [(s.get("nameZh") or "").strip() for s in (available_states or []) if isinstance(s, dict) and (s.get("nameZh") or "").strip()]
    if state_value_name and state_value_name in allowed:
        return
    raise ValueError(f"状态字段可选值不合法：{state_value_name or '空'}。可选状态为：{ '、'.join(allowed) if allowed else '无' }")


def _format_required_debug(init_fields, keys):
    name_map = {}
    for item in init_fields or []:
        if not isinstance(item, dict):
            continue
        k = (item.get("key") or "").strip()
        if k:
            name_map[k] = item.get("name") or ""
    return [f"{k}({name_map.get(k) or '-'})" for k in (keys or [])]

def update_work_item(work_item_id, fields, base_config, headers, work_item_type_key=None, user_input=""):
    """更新工作项（PUT URL_WORK_ITEM_UPDATE，请求体仅 fields）"""
    # 工作区从 work_item_id 截取：以最后一个 "-" 分割，前半部分为工作区
    workspace = extract_workspace_key(work_item_id)
    if not workspace:
        raise ValueError(f"无法从 work_item_id 解析工作区: {work_item_id}")
    url = rdcloud_api.URL_WORK_ITEM_UPDATE.format(workspace=workspace, work_item_id=work_item_id)
    logger.info(f"更新工作项: work_item_id={work_item_id}, workspace={workspace}, url={url}")
    # 解析 fields 中每个 key：先 fetch_fields，再挨个 resolve_field_key（支持中文名→系统 key）
    field_items = None
    if workspace:
        try:
            field_items = fetch_fields(workspace, headers)
            for f in fields:
                if isinstance(f, dict) and f.get("key") is not None:
                    raw_key = (f.get("key") or "").strip()
                    if raw_key:
                        f["_original_key"] = raw_key
                        f["key"] = resolve_field_key(raw_key, field_items, headers, scene="更新工作项字段映射", workspace=workspace)
        except ValueError as e:
            raise e
    else:
        logger.warning("无法从 work_item_id 解析工作区（需含 '-'），跳过 fields key 解析，按入参 key 原样请求")

    # 更新初始化预校验：状态可选值 + 必填字段 + 字段元数据(type/multiValue)
    event_id = ""
    try:
        resolved_work_item_type_key = _resolve_work_item_type_key_for_update(work_item_id, work_item_type_key, headers)
        if not resolved_work_item_type_key:
            raise ValueError("更新前预校验失败：无法确定 workItemTypeKey，请在入参提供 --work-item-type-key。")
        init_resp = rdcloud_api.fetch_update_work_item_init_detail(
            workspace=workspace,
            work_item_id=work_item_id,
            work_item_type_key=resolved_work_item_type_key,
            headers=headers,
        )
        code = (init_resp.get("code") or {}) if isinstance(init_resp, dict) else {}
        if not isinstance(code, dict) or code.get("code") != "0000":
            raise ValueError(f"更新初始化失败: code={code}")
        bo = init_resp.get("bo") or {}
        event_id = (bo.get("eventId") or "").strip()
        if not event_id:
            raise ValueError("更新初始化未返回 eventId")
        init_fields = bo.get("fields") or bo.get("fieldList") or []
        if isinstance(init_fields, dict):
            init_fields = list(init_fields.values())
        available_states = bo.get("availableStates") or []
        logger.info(
            "更新初始化成功: work_item_id=%s, work_item_type_key=%s, event_id=%s, availableStates=%d, init_fields=%d",
            work_item_id,
            resolved_work_item_type_key,
            event_id,
            len(available_states) if isinstance(available_states, list) else 0,
            len(init_fields) if isinstance(init_fields, list) else 0,
        )

        # 用初始化字段元数据标注用户输入字段（type/multiValue）
        _bind_init_meta_for_fields(fields, init_fields)

        # 先按语义与元数据归一化值形态（字符串 -> 列表），再进入字段类型处理逻辑（如 USER 人员解析）
        _apply_modify_type_from_user_input(fields, user_input)
        _normalize_multi_value_fields_value_by_init_meta(fields)
        _normalize_append_fields_value(fields)

        user_type_keys = {
            (f.get("key") or "").strip()
            for f in fields
            if isinstance(f, dict) and ((f.get("_meta_type") or "").strip().lower() == "user")
        }

        emp_no = (headers.get("X-Emp-No") or "").strip()
        token = (headers.get("X-Auth-Value") or "").strip()
        for f in fields:
            if not isinstance(f, dict) or "value" not in f:
                continue
            v = f["value"]
            key = (f.get("key") or "").strip()
            # 指派给等人员字段 或 字段元数据 type=USER：value 转为工号（@me→当前用户，否则 search_users 取第一个）
            if key in ASSIGNEE_FIELD_KEYS or key in user_type_keys:
                if isinstance(v, str):
                    f["value"] = _resolve_person_to_emp_no(v, emp_no, token, logger)
                elif isinstance(v, list):
                    f["value"] = [_resolve_person_to_emp_no(x, emp_no, token, logger) if isinstance(x, str) else x for x in v]
                continue
            # 其他字段：仅做 @me -> 当前用户工号
            if isinstance(v, str) and _is_me_value(v):
                f["value"] = emp_no
            elif isinstance(v, list):
                f["value"] = [emp_no if (isinstance(x, str) and _is_me_value(x)) else x for x in v]

        # event 校验专用字段副本：保持用户输入语义，不影响更新接口实际 payload
        fields_for_event = copy.deepcopy(fields)

        # 清理内部辅助字段，避免透传到接口
        for f in fields:
            if isinstance(f, dict):
                f.pop("_original_key", None)
                f.pop("_meta_type", None)
                f.pop("_meta_multi_value", None)

        # 新接口仅接受 fields，响应格式为 { "bo": {...}, "code": {...} }
        payload = {"fields": fields}

        # 1) 状态可选值校验
        _validate_state_candidate(fields, available_states)

        # 2) 初始化必填判断（persistentValue/value）
        missing_required = _collect_required_missing_from_update_init(init_fields, fields)
        logger.info(
            "更新预校验-初始化必填结果: missing=%s",
            _format_required_debug(init_fields, missing_required),
        )

        # 3) PUT event 再次收集 required=true 且 value 为空
        event_fields = _build_update_event_fields(
            fields_for_event,
            field_items,
            available_states,
            user_type_keys=user_type_keys,
            emp_no=emp_no,
            token=token,
        )
        logger.info(
            "更新预校验-PUT event 请求: fields=%d, user_input_fields=%d",
            len(event_fields),
            len(fields),
        )
        missing_required_from_event = []
        for single_field in event_fields:
            event_body = {
                "workItemTypeKey": resolved_work_item_type_key,
                "fields": [single_field],
            }
            event_resp = rdcloud_api.put_work_item_event(workspace, event_id, event_body, headers)
            missing_single = _collect_event_required_missing(event_resp)
            if missing_single:
                missing_required_from_event.extend(missing_single)
        missing_required_from_event = sorted(set(missing_required_from_event))
        logger.info(
            "更新预校验-event必填结果: missing=%s",
            _format_required_debug(init_fields, missing_required_from_event),
        )
        missing_required = sorted(set((missing_required or []) + (missing_required_from_event or [])))
        # 只要用户本次请求提到了该字段，就不再提示该字段缺失（init/event 统一规则）
        mentioned_keys = {
            (f.get("key") or "").strip()
            for f in fields
            if isinstance(f, dict) and (f.get("key") or "").strip()
        }
        missing_required = [k for k in missing_required if (k or "").strip() not in mentioned_keys]
        logger.info(
            "更新预校验-合并必填结果: missing=%s",
            _format_required_debug(init_fields, missing_required),
        )
        if missing_required:
            _raise_update_required_error(missing_required, field_items=field_items, init_fields=init_fields)
    finally:
        if event_id:
            try:
                rdcloud_api.delete_cache_event(workspace, event_id, headers)
                logger.info("更新预校验 event 缓存销毁完成: eventId=%s", event_id)
            except Exception as e:
                logger.warning("更新预校验 event 缓存销毁失败: eventId=%s, %s", event_id, e)
    
    _log_cfg = get_log_config(CONFIG_FILE)
    _max = _log_cfg.get("response_body_max_length", 500)
    _req_body = json.dumps({"url": url, "payload": sanitize_for_log(payload)}, ensure_ascii=False, indent=2)
    logger.info("请求 PUT url=%s body=%s", url, _req_body)
    try:
        response = requests.put(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        _rsp_str = json.dumps(result, ensure_ascii=False, indent=2)
        logger.info("响应 status=%s body=%s", response.status_code, truncate_for_log(_rsp_str, _max))
        code_obj = (result.get("code") or {}) if isinstance(result, dict) else {}
        if isinstance(code_obj, dict) and code_obj.get("code") not in ("0000", "", None):
            msg = (code_obj.get("msg") or code_obj.get("message") or "").strip()
            raise Exception(f"更新工作项失败（服务返回错误）: {msg or code_obj}")
        logger.info("工作项更新成功: %s", work_item_id)
        return result
    except requests.exceptions.RequestException as e:
        error_msg = f"API 请求失败: {str(e)}"
        logger.error("更新工作项失败: %s", error_msg)
        if hasattr(e, 'response') and e.response is not None:
            try:
                _t = json.dumps(e.response.json(), ensure_ascii=False, indent=2)
                logger.error("错误响应 body=%s", truncate_for_log(_t, _max))
            except Exception:
                logger.error("错误响应 body=%s", truncate_for_log(e.response.text or "", _max))
        raise Exception(error_msg)


def _search_user_by_name_for_send(user_name, auth_token, emp_no):
    if not auth_token or not emp_no:
        return {"status": "error", "message": "缺少认证信息"}
    result = search_users(emp_no, auth_token, (user_name or "").strip())
    if not result or result.get("status") != "success":
        return {"status": "error", "message": (result.get("message") if isinstance(result, dict) else "查询用户失败") or "未找到用户"}
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


def _send_mail_inline(mail_title, mail_to, mail_body, mail_from=None, mail_lan="zh_CN", auth_token=None, emp_no=None):
    auth_token = auth_token or resolve_auth_token_from_env()
    emp_no = emp_no or resolve_emp_no_from_env()
    if not auth_token or not emp_no:
        return {"status": "error", "message": "缺少认证信息"}
    try:
        processed_to = _process_recipients_mail(mail_to, auth_token, emp_no)
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    url = rdcloud_api.URL_SEND_MAIL
    data = {"mailFrom": mail_from or "rdc@zte.com.cn", "mailLan": mail_lan, "mailTitle": mail_title, "mailTo": processed_to, "mailBody": mail_body}
    req_headers = {"Content-Type": "application/json", "X-Auth-Value": auth_token, "X-Emp-No": emp_no, "X-Tenant-Id": "ZTE"}
    try:
        r = requests.post(url, headers=req_headers, data=json.dumps(data), timeout=30)
        return {"status": "success", "message": "邮件发送成功"} if r.status_code == 200 else {"status": "error", "message": f"邮件发送失败，状态码: {r.status_code}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _search_group_chat_for_send(group_name, auth_token, emp_no):
    if not auth_token or not emp_no:
        return {"status": "error", "message": "缺少认证信息"}
    url = (
        f"{rdcloud_api.URL_GROUP_CHAT_SEARCH}"
        f"?keyword={urllib.parse.quote(group_name)}&searchFor=groupChat"
    )
    req_headers = {"X-Auth-Value": auth_token, "X-Emp-No": emp_no, "X-Lang-Id": "zh_CN", "X-Tenant-Id": "ZTE"}
    try:
        resp = requests.get(url, headers=req_headers, timeout=30)
        if resp.status_code != 200:
            return {"status": "error", "message": f"查询群聊组失败: {resp.status_code}"}
        bo_list = resp.json().get("bo", [])
        if not bo_list:
            return {"status": "error", "message": f"未找到群聊组: {group_name}"}
        emp_no_val = bo_list[0].get("employeeNo", "")
        return {"status": "success", "employeeNo": emp_no_val} if emp_no_val else {"status": "error", "message": "群聊组无 employeeNo"}
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


def _send_single_icenter_message(message_content, subject, recipients, to_type, from_emp_no, auth_token, emp_no, view_url=None):
    url = rdcloud_api.URL_ICHAT_SHARE
    data = {
        "forward": True, "from": from_emp_no,
        "items": [{"multiLine": "false", "title": {"en": "Id", "zh": "详情"}, "value": {"en": message_content, "zh": message_content}}],
        "subject": {"en": subject or message_content, "zh": subject or message_content}, "summary": {}, "to": recipients, "toType": to_type,
    }
    if view_url:
        data["view"] = {"customMsgType": "1", "url": view_url}
    req_headers = {"X-Auth-Value": auth_token, "X-Emp-No": emp_no, "X-Lang-Id": "zh_CN", "X-Tenant-Id": "ZTE", "Content-Type": "application/json"}
    try:
        r = requests.post(url, headers=req_headers, data=json.dumps(data), timeout=30)
        return {"status": "success"} if r.status_code == 200 else {"status": "error", "message": f"状态码: {r.status_code}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _send_chat_message_inline(message_content, subject=None, to=None, view_url=None, auth_token=None, emp_no=None):
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
        if _send_single_icenter_message(message_content, subject, individuals, 1, from_emp, auth_token, emp_no, view_url).get("status") != "success":
            all_ok = False
    if groups:
        if _send_single_icenter_message(message_content, subject, groups, 2, from_emp, auth_token, emp_no, view_url).get("status") != "success":
            all_ok = False
    return {"status": "success" if all_ok else "error", "message": "iCenter 消息发送成功" if all_ok else "部分发送失败"}


def _send_email_after_update(input_data, work_item_id, result, base_config, headers):
    """更新成功后按 is_send_email 发送邮件（内联逻辑）。"""
    try:
        body_base = input_data.get("mail_body") or input_data.get("mailBody") or f"工作项已更新：{work_item_id}"
        work_item_url = (
            rdcloud_api.studio_workitem_transmit_url(work_item_id)
            if work_item_id
            else ""
        )
        mail_body = f"{body_base}<br/><a href=\"{work_item_url}\">查看工作项</a>" if work_item_url else body_base
        auth = input_data.get("auth") or {}
        auth_token = auth.get("auth_token") or headers.get("X-Auth-Value")
        emp_no = auth.get("emp_no") or headers.get("X-Emp-No", "")
        mail_title = input_data.get("mail_title") or input_data.get("mailTitle") or "工作项已更新"
        mail_to = input_data.get("mail_to") or input_data.get("mailTo") or emp_no
        logger.info("发送邮件: 场景=更新, 标题=%s, 收件人=%s, 正文长度=%d 字符", truncate_for_log(mail_title, 80), truncate_for_log(str(mail_to), 50), len(mail_body))
        result_send = _send_mail_inline(
            mail_title=mail_title,
            mail_to=mail_to,
            mail_body=mail_body,
            mail_from=input_data.get("mailFrom", "rdc@zte.com.cn"),
            mail_lan=input_data.get("mailLan", "zh_CN"),
            auth_token=auth_token,
            emp_no=emp_no,
        )
        if result_send.get("status") == "error":
            logger.warning("发送邮件未成功: 标题=%s, 错误=%s", truncate_for_log(mail_title, 60), result_send.get("message", ""))
        else:
            logger.info("发送邮件完成: 场景=更新, 标题=%s, 收件人=%s", truncate_for_log(mail_title, 60), truncate_for_log(str(mail_to), 50))
    except Exception as e:
        logger.warning("发送邮件异常（不影响更新结果）: %s", e)


def _send_icenter_after_update(input_data, work_item_id, result, base_config, headers):
    """更新成功后按 is_send_icenter 发送 iCenter 消息（内联逻辑）。"""
    try:
        view_url = input_data.get("viewUrl") or input_data.get("view_url")
        if not view_url and work_item_id:
            view_url = rdcloud_api.studio_workitem_transmit_url(work_item_id)
        auth = input_data.get("auth") or {}
        auth_token = auth.get("auth_token") or headers.get("X-Auth-Value")
        emp_no = auth.get("emp_no") or headers.get("X-Emp-No", "")
        to = input_data.get("icenter_to") or emp_no
        if isinstance(to, str) and "," in to:
            to = [x.strip() for x in to.split(",") if x.strip()]
        subject = input_data.get("subject") or input_data.get("mail_title") or input_data.get("mailTitle") or "工作项已更新"
        message_content = input_data.get("message") or input_data.get("mailBody") or f"工作项已更新：{work_item_id}"
        logger.info("发送 iCenter 消息: 场景=更新, 主题=%s, 接收人=%s, view_url=%s", truncate_for_log(subject, 80), truncate_for_log(str(to), 50), "有" if view_url else "无")
        result_send = _send_chat_message_inline(
            message_content=message_content,
            subject=subject,
            to=to,
            view_url=view_url or "",
            auth_token=auth_token,
            emp_no=emp_no,
        )
        if result_send.get("status") == "error":
            logger.warning("发送 iCenter 消息未成功: 主题=%s, 错误=%s", truncate_for_log(subject, 60), result_send.get("message", ""))
        else:
            logger.info("发送 iCenter 消息完成: 场景=更新, 主题=%s, 接收人=%s", truncate_for_log(subject, 60), truncate_for_log(str(to), 50))
    except Exception as e:
        logger.warning("发送 iCenter 消息异常（不影响更新结果）: %s", e)


def _parse_bool(s):
    if s is None:
        return False
    return str(s).strip().lower() in ("true", "1", "yes")


def _build_parser():
    examples = (
        "完整示例:\n"
        "  # 1) 更新单个工作项标题（最小示例）\n"
        "  %(prog)s --work-item-id PLM-12345 --fields '[{\"key\":\"System_Title\",\"value\":\"新标题\"}]'\n\n"
        "  # 2) 批量更新多个工作项状态（中文字段名）\n"
        "  %(prog)s --work-item-id PLM-12345,PLM-12346 --fields '[{\"key\":\"状态\",\"value\":\"进行中\"}]'\n\n"
        "  # 3) 更新后发送 iCenter 消息\n"
        "  %(prog)s --work-item-id PLM-12345 --fields '[{\"key\":\"System_State\",\"value\":\"进行中\"}]' --is-send-icenter true --icenter-to @me\n\n"
        "  # 4) 更新后发送邮件（多人）\n"
        "  %(prog)s --work-item-id PLM-12345 --fields '[{\"key\":\"System_Title\",\"value\":\"标题已更新\"}]' --is-send-email true --mail-to \"u1@zte.com.cn,u2@zte.com.cn\"\n\n"
        "  # 5) 指定工作项类型 key + 关联已有工作项\n"
        "  %(prog)s --work-item-id PLM-12345 --work-item-type-key Task --fields '[{\"key\":\"System_Title\",\"value\":\"补充说明\"}]' --relate-workitem-ids \"PLM-3292609,PLM-3292610\" --relate-type 相关\n\n"
        "  # 6) 携带用户原始输入（用于日志与语义辅助）\n"
        "  %(prog)s --work-item-id PLM-12345 --fields '[{\"key\":\"System_Title\",\"value\":\"标题更新\"}]' --user-input \"把 PLM-12345 的标题改成标题更新\""
    ) % {"prog": "update_work_item.py"}

    p = argparse.ArgumentParser(
        prog="update_work_item.py",
        description="更新工作项：按 workItemId 更新指定字段；可选更新后发邮件/发 iCenter 消息。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=examples,
    )
    p.add_argument("--work-item-id", "--workItemId", dest="work_item_id", required=True, metavar="ID", help="工作项标识，多个以英文逗号分隔，如 PLM-12345,PLM-12346。")
    p.add_argument("--fields", "-f", required=True, metavar="JSON", help="字段数组 JSON 字符串，如 [{\"key\":\"System_Title\",\"value\":\"新标题\"}]。")
    p.add_argument("--work-item-type-name", "--workItemTypeName", dest="work_item_type_name", default=None, metavar="NAME", help="工作项类型中文名（可选，用于请求体）。")
    p.add_argument("--work-item-type-key", "--workItemTypeKey", dest="work_item_type_key", default=None, metavar="KEY", help="工作项类型 key（可选）。")
    p.add_argument("--is-send-email", "--is_send_email", dest="is_send_email", default="false", metavar="BOOL", help="更新成功后是否发邮件：true/false，默认 false。")
    p.add_argument("--is-send-icenter", "--is_send_icenter", dest="is_send_icenter", default="false", metavar="BOOL", help="更新成功后是否发 iCenter 消息：true/false，默认 false。")
    p.add_argument("--mail-to", "--mailTo", dest="mail_to", default=None, metavar="RECIPIENTS", help="邮件接收人，多人英文逗号分隔。")
    p.add_argument("--icenter-to", "--icenter_to", dest="icenter_to", default=None, metavar="RECIPIENTS", help="iCenter 消息接收人，多人英文逗号分隔。")
    p.add_argument("--relate-workitem-ids", "--relateWorkitemIds", dest="relate_workitem_ids", default=None, metavar="IDS", help="更新后要关联的工作项标识，多个以英文逗号分隔；需与 --relate-type 同时使用。")
    p.add_argument("--relate-type", "--relateType", dest="relate_type", default=None, metavar="TYPE", help="关联类型中文名，如 子级、父级、相关；与 --relate-workitem-ids 同时使用。")
    p.add_argument("--user-input", "--userInput", dest="user_input", default="", metavar="TEXT", help="用户的原始输入，必须是用户真实完整的一句话。")
    return p


def _args_to_input_data(args):
    data = {"workItemId": args.work_item_id, "fields": json.loads(args.fields)}
    if args.work_item_type_name:
        data["workItemTypeName"] = args.work_item_type_name
    if args.work_item_type_key:
        data["workItemTypeKey"] = args.work_item_type_key
    data["is_send_email"] = _parse_bool(args.is_send_email)
    data["is_send_icenter"] = _parse_bool(args.is_send_icenter)
    if args.mail_to:
        data["mailTo"] = args.mail_to
    if args.icenter_to:
        data["icenter_to"] = args.icenter_to
    if getattr(args, "user_input", None):
        data["userInput"] = (args.user_input or "").strip()
    if getattr(args, "relate_workitem_ids", None) and (args.relate_workitem_ids or "").strip():
        data["relateWorkitemIds"] = (args.relate_workitem_ids or "").strip()
    if getattr(args, "relate_type", None) and (args.relate_type or "").strip():
        data["relateType"] = (args.relate_type or "").strip()
    return data


def main():
    """主函数"""
    parser = _build_parser()
    args = parser.parse_args()
    try:
        input_data = _args_to_input_data(args)
    except json.JSONDecodeError as e:
        safe_print(f"错误: --fields 必须是合法 JSON 数组: {e}", file=sys.stderr)
        sys.exit(1)
    work_item_id_raw = input_data.get("workItemId")
    work_item_ids = [x.strip() for x in (work_item_id_raw or "").split(",") if x.strip()]
    if not work_item_ids:
        logger.error("work_item_id 不能为空")
        raise ValueError("work_item_id 不能为空")
    fields = input_data.get("fields", [])
    logger.info("入参: work_item_ids=%s, 字段数=%s", work_item_ids, len(fields))
    raw_user_input = (input_data.get("userInput") or "").strip()
    if raw_user_input:
        logger.info("原始用户输入: %s", raw_user_input[:500] if len(raw_user_input) > 500 else raw_user_input)
    if not fields:
        logger.error("fields 不能为空数组")
        raise ValueError("fields 不能为空数组")
    try:
        logger.info("=" * 50)
        logger.info("开始更新工作项")
        logger.info(f"日志文件: {LOG_FILE}")
        logger.info(f"工作项ID: {work_item_ids}, 字段数量: {len(fields)}")
        
        # 加载基础配置与认证
        base_config = load_base_config()

        headers = get_auth_headers(input_data, base_config, logger=logger)
        
        # 工作项类型：用第一个 ID 解析工作区
        work_item_type_key = None
        work_item_type_name = input_data.get("workItemTypeName")
        work_item_type_key_input = input_data.get("workItemTypeKey")
        workspace = extract_workspace_key(work_item_ids[0])
        
        if work_item_type_name:
            if not workspace:
                raise ValueError("无法从 work_item_id 解析工作区（需含 '-'），无法解析工作项类型")
            resolved_keys, resolved_names = resolve_work_item_type_key(work_item_type_name, workspace, headers)
            work_item_type_key = resolved_keys[0] if isinstance(resolved_keys, list) else resolved_keys
            if isinstance(resolved_keys, list) and len(resolved_keys) > 1:
                logger.warning(f"工作项类型「{work_item_type_name}」匹配到多个 key，使用第一个: {work_item_type_key}")
            logger.info(
                "工作项类型: %s -> key=%s, name=%s",
                work_item_type_name,
                work_item_type_key,
                resolved_names.get(work_item_type_key, ""),
            )
        elif work_item_type_key_input:
            work_item_type_key = work_item_type_key_input
            logger.info(f"工作项类型Key: {work_item_type_key}")
        
        # 按 ID 逐个更新并收集结果
        results = []
        for wid in work_item_ids:
            result = update_work_item(
                wid,
                fields,
                base_config,
                headers,
                work_item_type_key,
                user_input=raw_user_input,
            )
            results.append((wid, result))
            if input_data.get("is_send_email") is True:
                _send_email_after_update(input_data, wid, result, base_config, headers)
            if input_data.get("is_send_icenter") is True:
                _send_icenter_after_update(input_data, wid, result, base_config, headers)

        # 按入参 relateWorkitemIds + relateType 在更新成功后建立关联
        relate_ids_str = (input_data.get("relateWorkitemIds") or "").strip()
        relate_type = (input_data.get("relateType") or "").strip()
        if relate_ids_str and relate_type:
            link_relation_id = rdcloud_api.LINK_RELATION_TYPE_MAP.get((relate_type or "").strip())
            if link_relation_id:
                relate_ids = [x.strip() for x in relate_ids_str.split(",") if x.strip()]
                body = [{"workItemId": wid, "linkRelationId": link_relation_id, "relatedWorkItemId": rid} for wid in work_item_ids for rid in relate_ids]
                try:
                    rel_resp = rdcloud_api.batch_create_relations(body, headers)
                    failed = (rel_resp.get("bo") or {}).get("failedItems") or []
                    if failed:
                        logger.warning("更新后关联部分失败: %s", failed)
                    else:
                        logger.info("更新后关联成功: work_item_ids=%s, relate_type=%s, relate_ids=%s", work_item_ids, relate_type, relate_ids)
                except Exception as e:
                    logger.warning("更新后关联请求异常: %s", e)
            else:
                logger.warning("未识别的关联类型「%s」，跳过更新后关联", relate_type)
        
        # 根据配置决定输出格式
        format_as_markdown = input_data.get("formatAsMarkdown")
        if format_as_markdown is None:
            format_as_markdown = base_config.get("format_as_markdown", False)
        
        logger.info(f"输出格式: {'Markdown' if format_as_markdown else 'JSON'}")
        
        if format_as_markdown:
            output_parts = [format_result_as_markdown(res, wid) for wid, res in results]
            output = "\n\n---\n\n".join(output_parts)
        else:
            output = json.dumps([{"work_item_id": wid, "result": res} for wid, res in results], ensure_ascii=False, indent=2)
        
        # 记录执行结果
        logger.info("=" * 50)
        logger.info("执行结果:")
        logger.info(f"输出格式: {'Markdown' if format_as_markdown else 'JSON'}")
        logger.info(f"输出长度: {len(output)} 字符")
        output_lines = output.split('\n')
        for line in output_lines:
            logger.info(line)
        logger.info("=" * 50)
        logger.info("工作项更新流程完成")
        mindset_text = get_mindset_message("update_result")
        parsed_summary = format_parsed_summary(
            {
                "work_item_ids": work_item_ids,
                "workspace": workspace,
                "work_item_type_name": work_item_type_name,
                "work_item_type_key": work_item_type_key,
                "fields_count": len(fields),
                "is_send_email": bool(input_data.get("is_send_email")),
                "is_send_icenter": bool(input_data.get("is_send_icenter")),
                "relate_type": (input_data.get("relateType") or ""),
                "relate_workitem_ids": (input_data.get("relateWorkitemIds") or ""),
            },
            title="本次语义解析结果",
        )
        body_with_mindset = (output + "\n\n" + mindset_text) if mindset_text else output
        output_for_display = (parsed_summary + "\n" + body_with_mindset) if parsed_summary else body_with_mindset
        safe_print(output_for_display)
        
    except json.JSONDecodeError as e:
        error_msg = f"JSON 格式无效: {str(e)}"
        logger.error(error_msg)
        safe_print(f"错误: {error_msg}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        logger.error(f"执行失败: {str(e)}", exc_info=True)
        safe_print(f"错误: {str(e)}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()

