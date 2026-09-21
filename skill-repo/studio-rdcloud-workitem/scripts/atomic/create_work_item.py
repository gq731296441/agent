#!/usr/bin/env python
"""创建工作项脚本。支持命令行参数（--work-item-type-name、--fields 等）及 -h/--help。"""

import sys
import json
import os
import re
import uuid
import requests
import logging
import argparse
import urllib.parse
from pathlib import Path
from datetime import datetime

# 获取脚本所在目录（atomic/）；技能根目录为 scripts 的上级
SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_ROOT = SCRIPT_DIR.parent
SKILL_DIR = SCRIPTS_ROOT.parent
CONFIG_FILE = SKILL_DIR / "assets" / "config.json"
USER_SEARCH_MODULE = SCRIPTS_ROOT / "common" / "user_search.py"
# 容器内 skill 为只读挂载（/mnt/skills/<id>），日志改写到可写工作区，并按 skill 分目录
LOG_DIR = (Path("/workspace/.skill-logs") / SKILL_DIR.name) if Path("/workspace").is_dir() else (SKILL_DIR / "logs")
LOG_FILE = LOG_DIR / f"create_work_item_{datetime.now().strftime('%Y%m%d')}.log"

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))
from common.log_helper import get_log_config, truncate_for_log, get_logger, ENABLE_LOG, safe_print, sanitize_for_log, truncate_subprocess_output_for_log, mask_emp_no_for_log, setup_logging, format_parsed_summary
from common.config_helper import load_base_config as load_base_config_from_helper
from common.auth_helper import get_auth_headers
from common.field_value_resolver import resolve_fields_values, resolve_field_value, apply_description_template_to_fields, log_description_template_and_field_value
from common.field_value_resolver import _get_field_type as get_field_type_from_items
from common.field_value_resolver import _get_field_display_name, reorder_with_priority
from common.notify_helper import send_mail as notify_send_mail, send_icenter_message as notify_send_icenter
from common.field_resolver import fetch_fields, resolve_field_key
from common.work_item_type_resolver import resolve_work_item_type_key as resolve_work_item_type_key_by_name, resolve_valid_work_item_type_key_from_root
from common.workspace.workspace_resolver import resolve_workspace
from common.mindset_messages import get_mindset_message
from common import rdcloud_api

if ENABLE_LOG:
    setup_logging(LOG_FILE, add_stderr=True)
logger = get_logger(__name__)

# 创建类型校验：若同时含以下字段且类型为 Bug，视为类型识别错误
WORKGROUP_FIELD_KEYS = ("Workgroup1", "Workgroup2", "Workgroup3")
WORK_ITEM_TYPE_KEY_BUG = "Bug"

# 智能填充：从历史记录中过滤掉的系统字段（ID、创建时间、标题等）
HISTORY_EXCLUDED_FIELDS = [
    "System_Id", "System_CreatedDate", "System_CreatedBy", "System_ChangedDate",
    "System_ChangedBy", "System_Version", "System_State", "System_AssignedTo",
    "System_Title", "System_WorkItemType", "System_WorkspaceKey", "System_Description_html",
]

# 智能补充候选排除：收集可填充候选时排除的字段（不从配置读取，使用全局变量）
SMART_COMPLETE_EXCLUDE_KEYS = [
    "System_Id", "System_CreatedDate", "System_CreatedBy", "System_ChangedDate",
    "System_ChangedBy", "System_Version", "System_State", "System_AssignedTo","System_StateChangeTime",
    "System_WorkItemTypeKey",
    "System_Title", "System_WorkItemType", "System_WorkspaceKey", "System_Description_html",
]

# 必填字段排除：初始化返回的 required 中以下 key 不参与必填补全
REQUIRED_FIELD_KEYS_EXCLUDED = [
    "Discoverer", "VerificationOwner", "System_ChangedDate", "System_WorkspaceKey", "System_WorkItemType", "System_State",
    "System_WorkItemTypeKey", "System_CreatedDate", "System_Id", "System_ChangedBy", "System_CreatedBy",
]

# 无标题时从描述取前 N 字作为标题：标题 key、描述 key 集合
TITLE_FIELD_KEY = "System_Title"
DESCRIPTION_FIELD_KEYS = frozenset({"System_Description", "System_Description_html"})
TITLE_FALLBACK_MAX_CHARS = 100

# 智能填充「再次拿」时，对 iChangeDiscoveryVersion 的截取配置
DISCOVERY_VERSION_FIELD_KEY = "iChangeDiscoveryVersion"
SMART_FILL_RETRY_VERSION_TRUNCATE_PATTERNS = ("V1.", "V2.")  # 包含字样则截取到该字样前
SMART_FILL_RETRY_VERSION_TRUNCATE_LENGTH = 10  # 否则截取前 N 位


def _reorder_fields_description_after_product(fields, field_items):
    """
    若 fields 中同时存在描述字段（key 为描述或含「描述」字样）与所属项目字段（含「项目」字样），
    则将描述字段移到所属项目字段后面。
    """
    if not fields or not field_items:
        return

    def is_description(f):
        if not isinstance(f, dict):
            return False
        k = (f.get("key") or "").strip()
        if k in DESCRIPTION_FIELD_KEYS:
            return True
        name = (_get_field_display_name(k, field_items=field_items) or "")
        return "描述" in name

    def is_project(f):
        if not isinstance(f, dict):
            return False
        k = (f.get("key") or "").strip()
        name = (_get_field_display_name(k, field_items=field_items) or "")
        return "项目" in name

    project_idx = None
    desc_idx = None
    for i, f in enumerate(fields):
        if project_idx is None and is_project(f):
            project_idx = i
        if desc_idx is None and is_description(f):
            desc_idx = i
        if project_idx is not None and desc_idx is not None:
            break
    if desc_idx is None or project_idx is None:
        return
    if desc_idx > project_idx:
        return
    item = fields.pop(desc_idx)
    if desc_idx < project_idx:
        project_idx -= 1
    fields.insert(project_idx + 1, item)
    logger.info("字段顺序已调整：描述字段已移到所属项目字段后面")

# 定制创建：(workspace, work_item_type_key) -> createCustom 下模块名（不含 .py）。不在集合中则走默认逻辑。
# 配置来自 scripts/common/create_custom_map.json
CREATE_CUSTOM_MAP_FILE = SCRIPTS_ROOT / "common" / "create_custom_map.json"


def _load_create_custom_map():
    """从 create_custom_map.json 加载定制创建映射。"""
    if not CREATE_CUSTOM_MAP_FILE.exists():
        return {}
    try:
        with open(CREATE_CUSTOM_MAP_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("create_custom_map", [])
        return {
            (str(item["workspace"] or "").strip(), str(item["work_item_type_key"] or "").strip()): str(item["module"] or "").strip()
            for item in items
            if isinstance(item, dict) and item.get("workspace") and item.get("work_item_type_key") and item.get("module")
        }
    except Exception as e:
        logger.warning("加载 create_custom_map 失败: %s，使用空映射", e)
        return {}


CREATE_CUSTOM_MAP = _load_create_custom_map()


def _value_to_plain_str(v):
    """将字段 value 转为可截取的纯文本（取前 N 字用）。"""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return (v.get("value") or v.get("text") or "").strip() or str(v)
    return str(v)


def _title_fallback_from_description(fields, field_items, max_chars=TITLE_FALLBACK_MAX_CHARS):
    """
    若 fields 中无标题字段，则从「描述」字段或含「描述」字样的字段的 value 取前 max_chars 个字符作为标题。
    返回 None 表示已有标题或无可用的描述内容；否则返回字符串（已截断至 max_chars）。
    """
    has_title = any(
        isinstance(f, dict)
        and (f.get("key") or "").strip() == TITLE_FIELD_KEY
        and _value_to_plain_str(f.get("value")).strip()
        for f in fields
    )
    if has_title:
        return None
    # 1）优先从「描述」字段（System_Description / System_Description_html）取
    for f in fields:
        if not isinstance(f, dict):
            continue
        k = (f.get("key") or "").strip()
        if k not in DESCRIPTION_FIELD_KEYS:
            continue
        s = _value_to_plain_str(f.get("value")).strip()
        if s:
            return s[:max_chars]
    # 2）再从 key 对应展示名含「描述」的字段取
    for f in fields:
        if not isinstance(f, dict):
            continue
        k = (f.get("key") or "").strip()
        name = (_get_field_display_name(k, field_items=field_items) or "") if field_items else ""
        if "描述" not in name:
            continue
        s = _value_to_plain_str(f.get("value")).strip()
        if s:
            return s[:max_chars]
    return None


def load_base_config():
    """加载基础配置（含 create 所需 smart_complete、byPassRules 等）。URL 由 common.rdcloud_api 统一管理，不从配置读取。"""
    cfg = load_base_config_from_helper(CONFIG_FILE)
    cfg.setdefault("byPassRules", 0)
    return cfg


def extract_workspace_key(work_item_id):
    """从工作项ID中提取工作区Key
    
    规则：以最后一个 `-` 分隔，取前半部分
    XXX-123456 -> XXX
          Test-rt-1234 -> Test-rt
    """
    if not work_item_id:
        return ""
    last_dash_index = work_item_id.rfind('-')
    if last_dash_index == -1:
        return work_item_id
    return work_item_id[:last_dash_index]

def format_result_as_markdown(result):
    """将创建结果格式化为 Markdown 格式
    
    提取关键信息：
    - System_Title: 标题
    - System_WorkItemType.name: 工作项类型名称
    - id: 工作项标识
    
    响应体结构：
    {
      "bo": {
        "id": "XXX-3227387",
        "System_Title": "标题",
        "System_WorkItemType": {
          "name": "任务"
        }
      },
      "code": {...}
    }
    """
    logger.info("开始格式化创建结果为 Markdown")
    if not result or not isinstance(result, dict):
        logger.warning("创建结果格式不正确，返回原始 JSON")
        return json.dumps(result, ensure_ascii=False, indent=2)
    
    # 从 result["bo"] 中提取字段（API 返回的实际结构）
    bo_data = result.get("bo", {})
    if not bo_data or not isinstance(bo_data, dict):
        logger.warning("响应体中未找到 bo 字段，尝试从根对象提取")
        bo_data = result
    
    # 提取字段值
    work_item_id = bo_data.get("id", "")
    title = bo_data.get("System_Title", "")
    work_item_type = ""
    
    # 提取工作项类型名称
    work_item_type_obj = bo_data.get("System_WorkItemType")
    if isinstance(work_item_type_obj, dict):
        work_item_type = work_item_type_obj.get("name", "") or work_item_type_obj.get("nameZh", "")
    elif isinstance(work_item_type_obj, str):
        work_item_type = work_item_type_obj
    
    # 记录提取的字段值（用于调试）
    logger.info(f"提取的字段值: id={work_item_id}, title={title}, work_item_type={work_item_type}")
    
    # 生成跳转链接
    if work_item_id:
        workspace_key = extract_workspace_key(work_item_id)
        link_url = rdcloud_api.studio_workitem_transmit_url(work_item_id)
        title_link = f"[{title}]({link_url})" if title else ""
        id_link = f"[{work_item_id}]({link_url})"
        logger.info(f"生成跳转链接: {link_url}")
    else:
        title_link = title if title else ""
        id_link = work_item_id if work_item_id else ""
        logger.warning("未找到工作项ID，无法生成跳转链接")
    
    # 生成 Markdown
    md_lines = []
    md_lines.append("# 工作项创建成功\n\n")
    md_lines.append("| 字段 | 值 |\n")
    md_lines.append("|------|------|\n")
    md_lines.append(f"| 标题 | {title_link} |\n")
    md_lines.append(f"| 标识 | {id_link} |\n")
    md_lines.append(f"| 类型 | {work_item_type} |\n")
    
    return "".join(md_lines)

def mask_sensitive_header(header_name, header_value):
    """掩码敏感请求头信息"""
    if not header_value:
        return ""
    if header_name.lower() in ['x-auth-value', 'authorization']:
        # 只显示前3个字符和后3个字符
        if len(header_value) > 6:
            return f"{header_value[:3]}***{header_value[-3:]}"
        else:
            return "***"
    return header_value

def query_latest_work_item_history(workspace, work_item_type_key, userid, token, tenant_id="ZTE", lang_id="zh_CN"):
    """
    查询用户在指定工作区创建的最新单据的历史记录（新建时的记录）。
    用于智能填充：从历史记录中学习并补充未填字段。URL 使用 common.rdcloud_api.URL_LATEST_LOG_BY_CREATOR。
    """
    try:
        url = rdcloud_api.URL_LATEST_LOG_BY_CREATOR.format(workspace=workspace)
        params = {"workItemTypeKey": work_item_type_key, "createUserId": userid}
        req_headers = {
            "X-Tenant-Id": tenant_id,
            "X-Emp-No": userid,
            "X-Auth-Value": token,
            "X-Lang-Id": lang_id,
            "Content-Type": "application/json",
        }
        #logger.info("查询历史工作项: 工作区=%s, 类型=%s", truncate_for_log(workspace, 30), work_item_type_key)
        response = requests.get(url, headers=req_headers, params=params, timeout=30)
        response.raise_for_status()
        result = response.json()
        if result.get("code", {}).get("code") == "0000" and result.get("bo"):
            history_fields = result["bo"]
            #logger.info("找到同类工作项创建历史记录，响应: %s", json.dumps(result, ensure_ascii=False, indent=2))
            return history_fields
        logger.info("未找到同类工作项创建历史记录")
        return None
    except Exception as e:
        logger.warning("查询同类工作项创建历史记录失败（跳过智能填充）: %s", e)
        return None


def extract_fields_from_history(history_fields):
    """
    从历史记录中提取可复用的字段。
    排除系统字段（ID、创建时间、标题等），返回 { referenceName: value }。
    使用全局变量 HISTORY_EXCLUDED_FIELDS 控制排除列表，便于统一维护。
    """
    extracted = {}
    for field in history_fields or []:
        reference_name = field.get("referenceName")
        value = field.get("value")
        field_type = field.get("type")
        if not value or reference_name in HISTORY_EXCLUDED_FIELDS:
            continue
        if field_type == "USER" and isinstance(value, list):
            if len(value) == 1 and value[0].get("employeeNo") and value[0].get("employeeNo") != "tenantUsers":
                extracted[reference_name] = value[0]["employeeNo"]
            elif len(value) > 1:
                emp_nos = [u["employeeNo"] for u in value if u.get("employeeNo") and u["employeeNo"] != "tenantUsers"]
                if emp_nos:
                    extracted[reference_name] = emp_nos
        elif field_type in ("TEXT", "STRING", "DATETIME", "NUMBER", "BOOLEAN"):
            extracted[reference_name] = value
        else:
            extracted[reference_name] = value
    return extracted


def _normalize_fields_keys_to_system(fields, field_items, headers, workspace="PLM"):
    """
    将 fields 中的 key 从中文（或名称）解析为系统 key，便于与历史字段（referenceName）做同一 key 空间比较。
    返回新列表，每项为 {"key": 解析后的系统 key, "value": 原 value}；解析不到则保留原 key。
    """
    if not field_items:
        return list(fields or [])
    normalized = []
    for f in (fields or []):
        if not isinstance(f, dict):
            normalized.append(f)
            continue
        raw_key = (f.get("key") or "").strip()
        if not raw_key:
            normalized.append(dict(f))
            continue
        resolved = resolve_field_key(raw_key, field_items, headers, raise_on_not_found=False, scene="创建工作项_智能填充字段映射-中文key->英文key", workspace=workspace)
        key_after = (resolved if isinstance(resolved, str) else None) or raw_key
        normalized.append({"key": key_after, "value": f.get("value")})
    return normalized

# 智能补充字段：优先使用用户指定的字段，仅对用户未填写的 key 从历史补充，不覆盖用户已填写的值。
def smart_complete_fields(user_fields, history_fields):
    """
    智能补充字段：优先使用用户指定的字段，仅对用户未填写的 key 从历史补充，不覆盖用户已填写的值。
    user_fields: [{"key": "xxx", "value": "yyy"}, ...] 用户指定（key 应为系统 key，以便与 history 一致）
    history_fields: {"key": "value", ...} 仅用于补充 user_fields 中不存在的 key（key 为 referenceName）
    """
    user_field_keys = {f.get("key") for f in (user_fields or []) if f.get("key")}
    completed = list(user_fields or [])
    for key, value in history_fields.items():
        if key not in user_field_keys:
            completed.append({"key": key, "value": value})
            logger.info("从历史补充字段: %s（用户未指定，使用历史值）", truncate_for_log(str(key), 40))
    return completed


# user_fields 大模型解析出来的字段，不需要从历史中获取。
def collect_smart_complete_candidates(work_item_type_key, workspace, headers, base_config, field_items, user_fields):
    """
    仅收集可填充的字段及值（来自历史），不写入 user_fields。
    返回 { key: value }，供定制脚本或后续逻辑使用。排除用户已填写的 key 及 SMART_COMPLETE_EXCLUDE_KEYS。
    """
    logger.info("【创建工作项-收集智能补充候选字段】开始收集智能补充候选字段（不立即填充）...")
    if not field_items:
        return {}
    # user_field_keys    
    if user_fields:
        # 将 user_fields 中的 key 从中文（或名称）解析为系统 key，便于与历史字段（referenceName）做同一 key 空间比较。
        normalized = _normalize_fields_keys_to_system(list(user_fields), field_items, headers, workspace)
        user_field_keys = {(f.get("key") or "").strip() for f in normalized if (f.get("key") or "").strip()}
    else:
        user_field_keys = set()
    exclude_set = {str(k).strip() for k in SMART_COMPLETE_EXCLUDE_KEYS if (k or "").strip()}
    userid = (headers.get("X-Emp-No") or "").strip()
    token = (headers.get("X-Auth-Value") or "").strip()
    tenant_id = headers.get("X-Tenant-Id") or base_config.get("tenant_id", "ZTE")
    lang_id = headers.get("X-Lang-Id", "zh_CN")
    if not userid or not token:
        logger.warning("缺少认证信息，跳过收集候选")
        return {}
    history_log = query_latest_work_item_history(
        workspace=workspace,
        work_item_type_key=work_item_type_key,
        userid=userid,
        token=token,
        tenant_id=tenant_id,
        lang_id=lang_id,
    )
    if not history_log:
        logger.info("【创建工作项-收集智能补充候选字段】未找到同类工作项创建历史记录，跳过")
        return {}
    history_fields = extract_fields_from_history(history_log)
    if not history_fields:
        logger.info("【创建工作项-收集智能补充候选字段】历史记录解析后无可用字段，跳过")
        return {}
    candidates = {
        k: v
        for k, v in history_fields.items()
        if k not in user_field_keys and k not in exclude_set
    }
    logger.info("【创建工作项-收集智能补充候选字段】从历史创建记录中收集到 %d 个可填充候选字段", len(candidates))
    for key, value in candidates.items():
        name = _get_field_display_name(key, field_items=field_items) or key
        v_str = truncate_for_log(
            json.dumps(value, ensure_ascii=False) if not isinstance(value, (str, int, float, bool)) and value is not None else str(value),
            80,
        )
        logger.info("历史创建记录中候选字段: key=%s, name=%s, value=%s", key, name, v_str)
    return candidates



# ---------- 默认创建流程：分步实现（初始化 → event → 创建 → 销毁 event） ----------


def _step_create_init(workspace: str, work_item_type_key: str, headers: dict) -> tuple:
    """
    步骤一：新建初始化，获取 eventId 与响应体 fields 中 required=true 的字段，按是否有值区分。
    :return: (event_id, virtual_id, required_field_keys_with_value, required_field_keys_without_value, init_fields)
        required_field_keys_with_value: 必填且有值的字段 [{"key": str, "value": Any}, ...]
        required_field_keys_without_value: 必填且无值的字段 key 列表，用于必填补全
        init_fields: 初始化返回的完整字段列表（含 required、key、value 等）
    """
    data = rdcloud_api.fetch_work_item_init_detail(workspace, work_item_type_key, headers)
    code = data.get("code") or {}
    if not isinstance(code, dict) or code.get("code") != "0000":
        raise ValueError(f"新建初始化失败: code={code}")
    bo = data.get("bo") or {}
    event_id = (bo.get("eventId") or "").strip()
    if not event_id:
        raise ValueError("新建初始化未返回 eventId")
    virtual_id = "0." + uuid.uuid4().hex[:12]
    init_fields_raw = bo.get("fields") or bo.get("fieldList") or []
    init_fields = list(init_fields_raw.values()) if isinstance(init_fields_raw, dict) else (init_fields_raw if isinstance(init_fields_raw, list) else [])
    required_field_keys_with_value = []  # 必填且有值
    required_field_keys_without_value = []  # 必填且无值
    if init_fields:
        for item in init_fields:
            if not isinstance(item, dict):
                continue
            if item.get("required") is True:
                k = (item.get("key") or "").strip()
                if not k:
                    continue
                val = item.get("value")
                empty = val is None or (isinstance(val, str) and (val or "").strip() == "") or (isinstance(val, list) and len(val) == 0)
                if empty:
                    required_field_keys_without_value.append(k)
                else:
                    required_field_keys_with_value.append({"key": k, "value": val})
    logger.info(
        "【创建工作项】 新建初始化完成: eventId=%s, virtualId=%s, 必填且有值=%s, 必填且无值=%s, init_fields=%d",
        event_id, virtual_id,
        [r["key"] for r in required_field_keys_with_value],
        required_field_keys_without_value,
        len(init_fields),
    )
    return (event_id, virtual_id, required_field_keys_with_value, required_field_keys_without_value, init_fields)

#
def _ensure_required_fields_filled(
    fields: list,
    required_field_keys: list,
    workspace: str,
    work_item_type_key: str,
    field_items: list,
    headers: dict,
    event_id: str,
    skip_when_has_value: bool = True,
) -> list:
    """
    对必填字段：若 fields 中缺失或值为空，则调用 resolve_field_value 尝试解析；单值直接使用，多值/异常不抛错，收集后返回供创建失败时心智模型使用。
    :param skip_when_has_value: True（默认）时，f 不为空且已有值则跳过；False 时不判断已有值，一律尝试解析补全。
    :return: 补全过程中产生的多值或异常信息列表 [{"field_key": str, "message": str}, ...]
    """
    logger.info(
        "必填补全过程开始: work_item_type_key=%s, required_field_keys=%s",
        work_item_type_key,
        [k for k in (required_field_keys or []) if (k or "").strip()],
    )
    issues: list = []
    if not required_field_keys:
        logger.info("必填补全过程结束: 无必填字段，issues=[]")
        return issues
    key_to_field = {}
    for f in fields:
        if not isinstance(f, dict):
            continue
        k = (f.get("key") or "").strip()
        if k:
            key_to_field[k] = f

    def _is_empty(v):
        if v is None:
            return True
        if isinstance(v, str) and (v or "").strip() == "":
            return True
        if isinstance(v, list) and len(v) == 0:
            return True
        return False

    # BelongProduct、BelongProject 优先补全（与 field_value_resolver 使用同一 reorder_with_priority）
    for key in reorder_with_priority(list(required_field_keys)):
        f = key_to_field.get(key)
        current = (f.get("value") if f else None)
        # f 不为空且 skip_when_has_value 为 True 且已有值则跳过
        if f is not None and skip_when_has_value and not _is_empty(current):
            logger.info(
                "必填字段「%s」（key=%s）当前已有值，跳过必填补全: %s",
                _get_field_display_name(key, field_items=field_items),
                key,
                truncate_for_log(str(current), 80),
            )
            continue
        key_name = _get_field_display_name(key, field_items=field_items)
        try:
            resolved = resolve_field_value(
                key,
                current if current is not None else "",
                workspace,
                work_item_type_key,
                field_items,
                headers,
                scene="创建工作项_必填补全",
                event_id=event_id,
            )
            if _is_empty(resolved):
                # logger.info(
                #     "必填字段「%s」（key=%s）经 resolve_field_value 解析后仍为空，未进行补全",
                #     key_name,
                #     key,
                # )
                continue
            if f is not None:
                f["value"] = resolved
                logger.info("必填字段「%s」（key=%s）经 resolve_field_value 补全: %s", key_name, key, truncate_for_log(str(resolved), 80))
            else:
                fields.append({"key": key, "value": resolved})
                key_to_field[key] = fields[-1]
                logger.info("必填字段「%s」（key=%s）缺失，经 resolve_field_value 补全并加入: %s", key_name, key, truncate_for_log(str(resolved), 80))
        except Exception as e:
            msg = str(e).strip() if e else ""
            issues.append({"field_key": key, "field_name": key_name, "message": msg or "解析失败"})
            logger.info("必填字段「%s」（key=%s）补全未采用（多值或异常，将作为创建失败时心智模型）: %s", key_name, key, msg)
    logger.info("必填补全过程结束: 共处理字段数=%s, issues 数=%s, issues=%s", len(required_field_keys), len(issues), issues)
    return issues


def _is_field_value_empty(v):
    """与 _ensure_required_fields_filled 内判断一致，用于兜底前检查是否仍为空。"""
    if v is None:
        return True
    if isinstance(v, str) and (v or "").strip() == "":
        return True
    if isinstance(v, list) and len(v) == 0:
        return True
    return False

# 仅含 _id/name/nameEn 的候选值，只取 name 作为字段 value
_SMART_CANDIDATE_KEYS_ONLY = frozenset({"_id", "name", "nameEn"})


def _normalize_smart_candidate_value(cand):
    """若 cand 为仅含 _id、name、nameEn 的 dict，则仅取 name 作为 value；否则返回原值。"""
    if not isinstance(cand, dict) or set(cand.keys()) - _SMART_CANDIDATE_KEYS_ONLY:
        return cand
    return cand.get("name")


def _value_to_user_object(value):
    """将 USER 字段的 value 转换为对象格式（含 employeeNo）。转换失败则返回原值。"""
    
    if value is None:
        return value
    try:
        if isinstance(value, str) and (value or "").strip():
            logger.info("_value_to_user_object1 输入 value=%s", truncate_for_log(str(value), 200))
            return {"employeeNo": value.strip()}
        if isinstance(value, list):
            logger.info("_value_to_user_object2 输入 value=%s", truncate_for_log(str(value), 200))
            out = []
            for v in value:
                if isinstance(v, str) and (v or "").strip():
                    out.append({"employeeNo": v.strip()})
                elif isinstance(v, dict):
                    emp = v.get("employeeNo") or v.get("userId") or v.get("longId")
                    if emp and str(emp).strip():
                        out.append(v if v.get("employeeNo") else {"employeeNo": str(emp).strip(), **v})
                    else:
                        return value
                else:
                    return value
            return out if out else value
        if isinstance(value, dict):
            logger.info("_value_to_user_object3 输入 value=%s", truncate_for_log(str(value), 200))
            emp = value.get("employeeNo") or value.get("userId") or value.get("longId")
            if emp and str(emp).strip():
                return value if value.get("employeeNo") else {"employeeNo": str(emp).strip(), **value}
    except Exception:
        logger.info("_value_to_user_object4 输入 value=%s", truncate_for_log(str(value), 200))
        pass
    return value


def _truncate_discovery_version_for_retry(value):
    """
    当 is_retry_from_history=True 且填充 iChangeDiscoveryVersion 时，对 value 做截取：
    - 若包含 SMART_FILL_RETRY_VERSION_TRUNCATE_PATTERNS 中任一字样，则截取到该字样前；
    - 否则截取前 SMART_FILL_RETRY_VERSION_TRUNCATE_LENGTH 位。
    """
    if value is None:
        return value
    s = str(value).strip()
    if not s:
        return s
    for pattern in SMART_FILL_RETRY_VERSION_TRUNCATE_PATTERNS:
        idx = s.find(pattern)
        if idx >= 0:
            return s[:idx].rstrip()
    return s[:SMART_FILL_RETRY_VERSION_TRUNCATE_LENGTH]


# fields 大模型解析出来的字段，key是中文名，value是大模型推测的值
# required_field_keys 必填字段
# smart_complete_candidates 智能补充候选
# field_items 工作项模型中的字段项
def _fill_required_from_smart_candidates(fields, required_field_keys, smart_complete_candidates, field_items, is_retry_from_history=False):
    """
    对 required_field_keys：在 fields 里缺该项或 value 为空时，若 smart_complete_candidates 有该 key 的非空值则写入。
    若候选 value 仅包含 _id、name、nameEn，则仅取 name 作为 field 的 value。
    :param is_retry_from_history: True 表示再次从历史拿，会对 iChangeDiscoveryVersion 做截取；False 表示首次拿。
    :return: 本次从智能填充写入的字段 key 列表（供后续仅对这些字段做 resolve 兜底）。
    """
    if not smart_complete_candidates or not required_field_keys:
        return []
    key_to_field = {((f.get("key") or "").strip()): f for f in fields if isinstance(f, dict) and (f.get("key") or "").strip()}
    filled_from_candidates = []
    for key in required_field_keys:
        key = (key or "").strip()
        if not key:
            continue
        f = key_to_field.get(key)
        # 如果fields中对应key有值，则不需要从历史创建中拉取值
        if f is not None and not _is_field_value_empty(f.get("value")):
            continue
        cand = smart_complete_candidates.get(key)
        value_to_fill = _normalize_smart_candidate_value(cand)
        if _is_field_value_empty(value_to_fill):
            continue
        # 再次拿且 smart_complete_candidates 存在 iChangeDiscoveryVersion 时，对该字段 value 做截取
        if is_retry_from_history and key == DISCOVERY_VERSION_FIELD_KEY and key in smart_complete_candidates:
            value_to_fill = _truncate_discovery_version_for_retry(value_to_fill)
        # 如果fields不存在必填字段required_field_keys，则追加到fields中
        if f is None:
            f = {"key": key, "value": value_to_fill}
            fields.append(f)
            key_to_field[key] = f
        # 如果fields存在必填字段required_field_keys，则更新value
        else:
            f["value"] = value_to_fill
        filled_from_candidates.append((key, value_to_fill))
        logger.info(
            "必填「%s」（%s）← smart_complete_candidates: %s",
            _get_field_display_name(key, field_items=field_items),
            key,
            truncate_for_log(str(value_to_fill), 80),
        )
    if filled_from_candidates:
        summary = [(k, truncate_for_log(str(v), 80)) for k, v in filled_from_candidates]
        logger.info("智能填充兜底：以下 %d 个字段从 smart_complete_candidates 取值: %s", len(filled_from_candidates), summary)
    return [k for k, _ in filled_from_candidates]


def _step_put_event(
    workspace: str,
    event_id: str,
    work_item_type_key: str,
    fields: list,
    field_items: list,
    virtual_id: str,
    headers: dict,
) -> None:
    """
    步骤二：PUT event，提交字段（含 key、value、type）。
    """
    event_fields = []
    for f in fields:
        if not isinstance(f, dict):
            continue
        k = (f.get("key") or "").strip()
        if not k:
            continue
        v = f.get("value")
        type_ = get_field_type_from_items(k, field_items) if field_items else "string"
        event_fields.append({"key": k, "value": v, "type": type_ or "string"})
    body = {
        "workItemTypeKey": (work_item_type_key or "").strip(),
        "fields": event_fields,
        "virtualId": virtual_id,
    }
    rdcloud_api.put_work_item_event(workspace, event_id, body, headers)
    logger.info("步骤二 PUT event 完成: eventId=%s, 字段数=%d", event_id, len(event_fields))


def _step_create_work_item_post(
    workspace: str,
    resolved_type_key: str,
    fields: list,
    base_config: dict,
    headers: dict,
    removed_field_names: list,
    event_id: str,
) -> tuple:
    """
    步骤四：执行创建（按现有 createWorkItem 接口）。
    :return: (result, removed_field_names)
    """
    url = rdcloud_api.URL_WORK_ITEM_CREATE.format(workspace=workspace)
    params = {"byPassRules": base_config.get("byPassRules", 0)}
    payload = {"workItemTypeKey": resolved_type_key, "fields": fields}
    _log_cfg = get_log_config(CONFIG_FILE)
    _max = _log_cfg.get("response_body_max_length", 500)
    _req_body = json.dumps(sanitize_for_log({"url": url, "params": params, "payload": payload}), ensure_ascii=False, indent=2)
    logger.info("步骤四 创建请求 POST url=%s body=%s", url, _req_body)
    response = requests.post(url, params=params, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    result = response.json()
    _rsp_str = json.dumps(result, ensure_ascii=False, indent=2)
    logger.info("响应 status=%s body=%s", response.status_code, truncate_for_log(_rsp_str, _max))
    work_item_id = result.get("bo", {}).get("id") if isinstance(result.get("bo"), dict) else result.get("id")
    logger.info("工作项创建成功: %s", work_item_id or "N/A")
    return (result, removed_field_names)


def _create_work_item_post_with_fallback(
    workspace: str,
    resolved_type_key: str,
    fields: list,
    base_config: dict,
    headers: dict,
    removed_field_names: list,
    event_id: str,
) -> tuple:
    """
    执行创建；若配置启用兜底且首次请求失败，则强制 byPassRules=0 再提交一次。
    :return: (result, removed_field_names, created_as_draft)
      - created_as_draft True 表示本次由兜底（byPassRules=0）创建成功，可视为草稿。
    """
    try:
        result, removed = _step_create_work_item_post(
            workspace, resolved_type_key, fields, base_config, headers, removed_field_names, event_id
        )
        return (result, removed, False)
    except requests.exceptions.RequestException:
        fallback_enabled = base_config.get("fallback_bypass_on_error", True)
        bypass = base_config.get("byPassRules", 0)
        if not fallback_enabled or bypass == 0:
            raise
        fallback_config = {**base_config, "byPassRules": 0}
        logger.warning("创建请求失败，启用兜底：byPassRules 强制为 0 再次提交")
        result, removed = _step_create_work_item_post(
            workspace, resolved_type_key, fields, fallback_config, headers, removed_field_names, event_id
        )
        return (result, removed, True)


def _step_destroy_event(workspace: str, event_id: str, headers: dict) -> None:
    """步骤五：销毁 eventId 缓存。"""
    try:
        rdcloud_api.delete_cache_event(workspace, event_id, headers)
        logger.info("步骤五 销毁 event 完成: eventId=%s", event_id)
    except Exception as e:
        logger.warning("步骤五 销毁 event 失败（不影响创建结果）: eventId=%s, %s", event_id, e)


#  work_item_type_key 工作项类型key
# fields 是用户输入的原始字段，key是中文，包括value，field_items 是工作项模型全量字段列表（元数据），smart_complete_candidates 是智能填充候选字段包括value
# 1. 解析类型、字段 key、校验、解析 value
# 2. 初始化
# 3. PUT event
# 4. 执行创建
# 5. 销毁 event
# 6. 返回结果
# 7. 返回结果
# 8. 返回结果
def _create_work_item_default(work_item_type_key, fields, base_config, headers, field_items=None, smart_complete_candidates=None):
    """
    默认创建工作项。按总分总：准备（解析 key、校验、解析 value）→ 初始化 → PUT event → 执行创建 → 销毁 event。
    field_items 可选；smart_complete_candidates 为可填充候选（定制脚本可先合并再调此方法）。
    """
    logger.info("=" * 100)
    logger.info(".............开始执行默认的创建工作项逻辑....................")
    logger.info("=" * 100)
    workspace = base_config.get("workspace")
    if not workspace:
        raise ValueError("缺少必需参数: workspace (必须在配置文件的 api.workspace 中提供)")

    # ---------- 准备：解析类型、字段 key、校验、解析 value ----------
    # resolved_type_key 工作项模型key
    resolved_type_key = resolve_valid_work_item_type_key_from_root(work_item_type_key, workspace, headers)
    logger.info("【创建工作项-解析工作项模型key】: original_type=%s, resolved_work_item_type_key=%s, workspace=%s", work_item_type_key, resolved_type_key, workspace)

    if field_items is None:
        field_items = fetch_fields(workspace, headers)
    # 最终被采纳创建工作项的字段    
    resolved_fields = []
    # ai解析出来的字段，但是不在工作项模型全量字段列表field_items中的字段，需要移除（记录 key 与 value 供心智模型使用）
    removed_field_names = []
    for f in fields:
        if not isinstance(f, dict) or f.get("key") is None:
            continue
        raw_key = (f.get("key") or "").strip()
        if not raw_key:
            continue
        # 解析字段key，此时key是中文
        field_key = resolve_field_key(
            raw_key,
            field_items,
            headers,
            raise_on_not_found=False,
            scene="默认创建工作项-字段映射-中文key->英文key",
            workspace=workspace,
        )
        if not field_key:
            logger.warning("【默认创建工作项字段映射】字段名「%s」未解析到 key，已移除此字段。", raw_key)
            removed_field_names.append({"key": raw_key, "value": f.get("value")})
            continue
        f["key"] = field_key
        resolved_fields.append(f)
    # 最终被采纳创建工作项的字段，这个时候的fields的key是数据库key，英文
    fields = resolved_fields
    # 无标题时从描述或含「描述」字样的字段取前 100 字作为标题
    title_fallback = _title_fallback_from_description(fields, field_items, max_chars=TITLE_FALLBACK_MAX_CHARS)
    if title_fallback is not None:
        fields.append({"key": TITLE_FIELD_KEY, "value": title_fallback})
        logger.info("【默认创建工作项字段映射】无标题字段，已从描述/含描述字段取前%d字作为标题: %s", TITLE_FALLBACK_MAX_CHARS, truncate_for_log(title_fallback, 60))
    # 若同时有描述与所属产品字段，将描述移到所属产品后面
    _reorder_fields_description_after_product(fields, field_items)

    # 打印最终被采纳创建工作项的字段，key是数据库key，英文
    logger.info("=" * 100)
    logger.info(".............通过大模型识别到用户输入的字段中文key->英文key，以及value，也包括未被采纳的字段....................")
    logger.info("=" * 100)
    for f in fields:
        if isinstance(f, dict):
            k = (f.get("key") or "").strip()
            v = f.get("value")
            v_str = truncate_for_log(json.dumps(v, ensure_ascii=False) if not isinstance(v, (str, int, float, bool)) and v is not None else str(v), 120)
            k_name = _get_field_display_name(k, field_items=field_items)
            logger.info("【默认创建工作项-将中文key-英文】fields 项: key=%s, name=%s, value=%s", k, k_name, v_str)
    if removed_field_names:
        logger.info("【默认创建工作项-已移除字段】removed_field_names 共 %d 个:", len(removed_field_names))
        for r in removed_field_names:
            r_key = r.get("key", r) if isinstance(r, dict) else r
            r_val = r.get("value") if isinstance(r, dict) else None
            r_val_str = truncate_for_log(json.dumps(r_val, ensure_ascii=False) if not isinstance(r_val, (str, int, float, bool)) and r_val is not None else str(r_val), 80)
            logger.info("【默认创建工作项-已移除字段】removed: key=%s, value=%s", r_key, r_val_str)
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



    # -------------------------------------------------------------------------
    # -------------------------------------------------------------------------
    # ---------- 步骤一：新建初始化，获取 eventId 与必填字段（按是否有值区分）----------
    # -------------------------------------------------------------------------
    # -------------------------------------------------------------------------
    event_id, virtual_id, required_field_keys_with_value_init, required_field_keys, init_fields = _step_create_init(workspace, resolved_type_key, headers)
    # 必填字段排除：REQUIRED_FIELD_KEYS_EXCLUDED 中的 key 不参与补全
    excluded_set = {str(k).strip() for k in REQUIRED_FIELD_KEYS_EXCLUDED if (k or "").strip()}
    excluded_from_required = sorted(k for k in required_field_keys if (k or "").strip() in excluded_set)
    required_field_keys = [k for k in required_field_keys if (k or "").strip() not in excluded_set]
    required_field_keys_with_value_init = [r for r in required_field_keys_with_value_init if (r.get("key") or "").strip() not in excluded_set]
    logger.info("【默认创建工作项-通过初始化获取必填字段】被采纳必填字段-无值（已排除系统字段）: %s", required_field_keys)
    logger.info("【默认创建工作项-通过初始化获取必填字段】必填且有值（初始化）: %s", [r["key"] for r in required_field_keys_with_value_init])
    logger.info("【默认创建工作项-通过初始化获取必填字段】已排除的系统字段（排序）: %s", excluded_from_required)

    # 解析字段的value，一般情况下是用户明确指定了字段的value，模型进行了识别并填充，但是需要检查填充的value是否正确
    # 有可能是用户输入不清晰，也有可能是模型识别错误，都需要检查。
    logger.info("【默认创建工作项】开始解析大模型识别的字段的value，一般情况下是用户明确指定了字段的value，模型进行了识别并填充，但是需要检查填充的value是否正确")
    # new_required_fields_from_event 从 PUT event 响应收集到的新必填字段,只取是没有值的，后面进行二次填充或是智能填充
    new_required_fields_from_event, description_template, required_fields_with_value = resolve_fields_values(
        fields,
        workspace,
        resolved_type_key,
        field_items=field_items,
        headers=headers,
        scene="默认创建工作项-解析大模型识别字段的value",
        event_id=event_id,
        virtual_id=virtual_id,
    )
    # 应用描述模版，将描述模版应用到fields中
    #log_description_template_and_field_value(description_template, fields, scene="默认创建工作项-第一次应用描述模版")
    description_template_applied = apply_description_template_to_fields(
        fields, description_template, scene="默认创建工作项-应用描述模版", removed_field_names=removed_field_names
    )
    logger.info("【默认创建工作项-解析大模型识别字段的value】解析后 fields 共 %d 个:", len(fields))
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
                #logger.info("【默认创建工作项】new_required 中的 key=%s 在 required_field_keys_with_value_init 中有值，已使用并移除", k)
            else:
                new_required_to_merge.append(k)
        if new_required_to_merge:
            required_field_keys = list(set(required_field_keys) | set(new_required_to_merge))
    # logger.info("【默认创建工作项-解析字段的value】解析用户输入的字段的value完成，并且完成每一个用户字段的event触。")
    # 记录解析后 fields 的完整信息

    # required_field_keys 中若 key 已存在于 fields，则移除
    fields_keys = {(f.get("key") or "").strip() for f in fields if isinstance(f, dict) and (f.get("key") or "").strip()}
    required_field_keys = [k for k in required_field_keys if (k or "").strip() not in fields_keys]

    # 记录完整的 required_field_keys
    logger.info("【默认创建工作项-综合初始化、解析大模型识别的字段value后event事件收集到无值必填字段】必填字段 共 %d 个: %s", len(required_field_keys), sorted(required_field_keys))
    logger.info("【默认创建工作项-综合初始化、解析大模型识别的字段value后event事件收集到有值必填字段】必填字段 共 %d 个: %s", len(required_fields_with_value), sorted(r["key"] for r in required_fields_with_value))

    # 将 初始化必填有值字段、event 事件收集的有值字段 required_fields_with_value 合并到 fields，若 key 已存在则跳过
    fields_keys = {(f.get("key") or "").strip() for f in fields if isinstance(f, dict) and (f.get("key") or "").strip()}
    for r in required_fields_with_value:
        k = (r.get("key") or "").strip()
        if k and k not in fields_keys:
            fields.append({"key": k, "value": r["value"]})
            fields_keys.add(k)

    logger.info("【默认创建工作项-[完整]-最终合并初始化、解析大模型识别的字段value以及event事件后收集到有值必填字段】 fields 共 %d 个:", len(fields))
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
    logger.info("【默认创建工作项-解析字段的value】解析字段的value完成，下一步执行必填字段补全")
    logger.info("=" * 100)
    logger.info("=" * 100)
    # 通过_step_create_init可以拿到必填字段，若缺失或空则尝试补全；单值直接使用，多值/异常收集后供创建失败时心智模型使用。
    # 当前默认仅使用智能补全产生的 issues，先初始化为空，避免后续引用未赋值。
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
    # 必填补全后仍有必填字段为空时，用 smart_complete_candidates 兜底填充，这一步从smart_complete_candidates获取值
    # -------------------------------------------------------------------------
    # -------------------------------------------------------------------------
    # ---------- 智能填充获取的候选字段（从历史创建中获取）----------
    # -------------------------------------------------------------------------
    # -------------------------------------------------------------------------
    logger.info("---------- 智能填充获取的候选字段（从历史创建中获取）----------")
    smart_filled_keys = _fill_required_from_smart_candidates(
        fields,
        required_field_keys,
        smart_complete_candidates or {},
        field_items,
    )
    # 仅对从智能填充获取的字段做 resolve 兜底，兜底的字段需要解析后再使用，并且会再次产生必填字段
    if smart_filled_keys:
        logger.info("=" * 100)
        logger.info("---------- 解析智能填充获取的候选字段（从历史创建中获取）----------")
        logger.info("=" * 100)
        # 仅针对smart_filled_keys进行解析
        new_fields_required_from_smart, template_from_smart, new_fields_required_value_from_smart = resolve_fields_values(
            fields, #大模型识别的字段、初始化必填有值字段、event必填有值字段
            workspace,
            resolved_type_key,
            field_items=field_items,
            headers=headers,
            scene="创建工作项【智能填充的 字段value解析】",
            event_id=event_id,
            virtual_id=virtual_id,
            keys_to_resolve=smart_filled_keys, # 仅针对smart_filled_keys进行解析
        )
        logger.info("=" * 100)
        logger.info("---------- 智能填充获取的候选字段（从历史创建中获取），列出候选值----------")
        logger.info("=" * 100)
        # 本次 event 收集到的新必填字段做候选补充，返回值并入 required_fill_issues 供创建失败时使用
        if new_fields_required_from_smart:
            # 把 new_fields_required_from_smart 从历史创建字段中再拿一次，并设置到 fields 中
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
            new_required_filtered = [k for k in new_required_filtered if k not in new_smart_filled_keys or k == DISCOVERY_VERSION_FIELD_KEY]
            if new_required_filtered:
                # 仅考虑本次从 event 收集到的新必填字段（排除已从历史填充的）
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
            apply_description_template_to_fields(
                fields, template_from_smart, scene="创建工作项", removed_field_names=removed_field_names
            )
    else:
        # 智能填充没有填充任何值，则直接执行一次必填值候选值的获取
        logger.info("【默认创建工作项】智能填充未命中，直接执行必填字段补全，必填字段: %s", sorted(required_field_keys))
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
        #_step_put_event(workspace, event_id, resolved_type_key, fields, field_items, virtual_id, headers)

        # ---------- 步骤四：执行创建（含兜底：失败时 byPassRules=0 再试一次） ----------
        result, removed, created_as_draft = _create_work_item_post_with_fallback(
            workspace, resolved_type_key, fields, base_config, headers, removed_field_names, event_id
        )
        # 接口返回 code = 0004 视为业务异常，收集错误信息与必填补全 issues，作为心智模型的一部分
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
        logger.info(".............完成默认的创建工作项逻辑....................")
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
        #logger.error("创建工作项失败: %s", error_msg)
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
        _step_destroy_event(workspace, event_id, headers)
    

# 对外保持原名，便于定制脚本内部调用默认实现
create_work_item = _create_work_item_default


def _get_create_work_item_fn(workspace, work_item_type_key):
    """按工作区+工作项类型返回创建模块中的 create_work_item。

    解析顺序：
    1) 精确匹配 (workspace, work_item_type_key)；
    2) 工作项类型为 MR（市场需求）且未命中时，匹配 (COMMON, MR)；
    3) 若仍未命中，匹配 (workspace, COMMON)；
    否则返回默认实现 _create_work_item_default。
    """
    ws = str(workspace or "").strip()
    wk = str(work_item_type_key or "").strip()
    key = (ws, wk)
    module_name = CREATE_CUSTOM_MAP.get(key)
    if not module_name and wk.upper() == "MR":
        module_name = CREATE_CUSTOM_MAP.get(("COMMON", "MR"))
    if not module_name:
        key_common = (ws, "COMMON")
        module_name = CREATE_CUSTOM_MAP.get(key_common)
    if not module_name:
        return _create_work_item_default
    try:
        import importlib.util
        mod_path = SCRIPTS_ROOT / "createCustom" / f"{module_name}.py"
        if not mod_path.exists():
            logger.warning("定制脚本不存在: %s，使用默认创建", mod_path)
            return _create_work_item_default
        spec = importlib.util.spec_from_file_location(f"createCustom.{module_name}", mod_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if not hasattr(mod, "create_work_item"):
            logger.warning("定制模块 %s 无 create_work_item，使用默认创建", module_name)
            return _create_work_item_default
        logger.info("命中定制创建逻辑: workspace=%s, work_item_type_key=%s, module=%s", workspace, work_item_type_key, module_name)
        return mod.create_work_item
    except Exception as e:
        logger.warning("加载定制创建脚本失败 (%s): %s，使用默认创建", module_name, e)
        return _create_work_item_default


def _send_email_after_create(input_data, result, base_config, headers):
    """创建成功后按 is_send_email 发送邮件（内联逻辑）。"""
    try:
        work_item_id = result.get("bo", {}).get("id") if isinstance(result.get("bo"), dict) else result.get("id") or ""
        title = ""
        for f in input_data.get("fields", []):
            if isinstance(f, dict) and f.get("key") == "System_Title":
                title = str(f.get("value", ""))
                break
        body_base = input_data.get("mail_body") or input_data.get("mailBody") or f"已创建工作项：{work_item_id} {title}".strip()
        work_item_url = (
            rdcloud_api.studio_workitem_transmit_url(work_item_id) if work_item_id else ""
        )
        mail_body = f"{body_base}<br/><a href=\"{work_item_url}\">查看工作项</a>" if work_item_url else body_base
        auth = input_data.get("auth") or {}
        auth_token = auth.get("auth_token") or headers.get("X-Auth-Value")
        emp_no = auth.get("emp_no") or headers.get("X-Emp-No", "")
        mail_title = input_data.get("mail_title") or input_data.get("mailTitle") or "工作项已创建"
        mail_to = input_data.get("mail_to") or input_data.get("mailTo") or emp_no
        logger.info("发送邮件: 场景=创建, 标题=%s, 收件人=%s, 正文长度=%d 字符", truncate_for_log(mail_title, 80), truncate_for_log(str(mail_to), 50), len(mail_body))
        result_send = notify_send_mail(
            mail_title=mail_title,
            mail_to=mail_to,
            mail_body=mail_body,
            headers=headers,
            mail_from=input_data.get("mailFrom", "rdc@zte.com.cn"),
            mail_lan=input_data.get("mailLan", "zh_CN"),
        )
        if result_send.get("status") == "error":
            logger.warning("发送邮件未成功: 标题=%s, 错误=%s", truncate_for_log(mail_title, 60), result_send.get("message", ""))
        else:
            logger.info("发送邮件完成: 场景=创建, 标题=%s, 收件人=%s", truncate_for_log(mail_title, 60), truncate_for_log(str(mail_to), 50))
    except Exception as e:
        logger.warning("发送邮件异常（不影响创建结果）: %s", e)


def _send_icenter_after_create(input_data, result, base_config, headers):
    """创建成功后按 is_send_icenter 发送 iCenter 消息（内联逻辑）。"""
    try:
        work_item_id = result.get("bo", {}).get("id") if isinstance(result.get("bo"), dict) else result.get("id") or ""
        title = ""
        for f in input_data.get("fields", []):
            if isinstance(f, dict) and f.get("key") == "System_Title":
                title = str(f.get("value", ""))
                break
        view_url = input_data.get("viewUrl") or input_data.get("view_url")
        if not view_url and work_item_id:
            view_url = rdcloud_api.studio_workitem_transmit_url(work_item_id)
        auth = input_data.get("auth") or {}
        auth_token = auth.get("auth_token") or headers.get("X-Auth-Value")
        emp_no = auth.get("emp_no") or headers.get("X-Emp-No", "")
        to = input_data.get("icenter_to") or emp_no
        if isinstance(to, str) and "," in to:
            to = [x.strip() for x in to.split(",") if x.strip()]
        subject = input_data.get("subject") or input_data.get("mail_title") or input_data.get("mailTitle") or "工作项已创建"
        message_content = input_data.get("message") or input_data.get("mailBody") or f"已创建工作项：{work_item_id} {title}".strip()
        logger.info("发送 iCenter 消息: 场景=创建, 主题=%s, 接收人=%s, view_url=%s", truncate_for_log(subject, 80), truncate_for_log(str(to), 50), "有" if view_url else "无")
        result_send = notify_send_icenter(
            message_content=message_content,
            headers=headers,
            subject=subject,
            to=to,
            view_url=view_url or "",
        )
        if result_send.get("status") == "error":
            logger.warning("发送 iCenter 消息未成功: 主题=%s, 错误=%s", truncate_for_log(subject, 60), result_send.get("message", ""))
        else:
            logger.info("发送 iCenter 消息完成: 场景=创建, 主题=%s, 接收人=%s", truncate_for_log(subject, 60), truncate_for_log(str(to), 50))
    except Exception as e:
        logger.warning("发送 iCenter 消息异常（不影响创建结果）: %s", e)


def _parse_bool(s):
    if s is None:
        return False
    return str(s).strip().lower() in ("true", "1", "yes")


def _build_parser():
    examples = (
        "完整示例:\n"
        "  # 1) 最小创建（按类型中文名）\n"
        "  %(prog)s --work-item-type-name 任务 --fields '[{\"key\":\"System_Title\",\"value\":\"测试任务\"}]'\n\n"
        "  # 2) 按类型 key 创建 + 关闭智能补全\n"
        "  %(prog)s --work-item-type-key Task --fields '[{\"key\":\"System_Title\",\"value\":\"test\"}]' --smart-complete false\n\n"
        "  # 3) 创建后发送邮件（多人逗号分隔）\n"
        "  %(prog)s --work-item-type-name 缺陷 --fields '[{\"key\":\"System_Title\",\"value\":\"登录失败\"}]' --is-send-email true --mail-to \"u1@zte.com.cn,u2@zte.com.cn\"\n\n"
        "  # 4) 创建后发送 iCenter 消息（发给自己）\n"
        "  %(prog)s --work-item-type-key Task --fields '[{\"key\":\"System_Title\",\"value\":\"接口联调\"}]' --is-send-icenter true --icenter-to @me\n\n"
        "  # 5) 创建并关联已有工作项\n"
        "  %(prog)s --work-item-type-name 任务 --fields '[{\"key\":\"System_Title\",\"value\":\"联调任务\"}]' --relate-workitem-ids \"PLM-3292609,PLM-3292610\" --relate-type 相关\n\n"
        "  # 6) 携带用户原始输入（用于日志与语义辅助）\n"
        "  %(prog)s --work-item-type-name 任务 --fields '[{\"key\":\"System_Title\",\"value\":\"脚本创建任务\"}]' --user-input \"创建一个任务，标题是脚本创建任务\""
    ) % {"prog": "create_work_item.py"}

    p = argparse.ArgumentParser(
        prog="create_work_item.py",
        description="创建工作项：指定工作项类型（中文名或 key）与字段；可选创建后发邮件/发 iCenter 消息。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=examples,
    )
    p.add_argument("--work-item-type-name", "--workItemTypeName", dest="work_item_type_name", default=None, metavar="NAME", help="工作项类型中文名，如 任务、缺陷（与 --work-item-type-key 二选一）。")
    p.add_argument("--work-item-type-key", "--workItemTypeKey", dest="work_item_type_key", default=None, metavar="KEY", help="工作项类型 key，如 Task（与 --work-item-type-name 二选一）。")
    p.add_argument("--fields", "-f", required=True, metavar="JSON", help="字段数组 JSON 字符串，如 [{\"key\":\"System_Title\",\"value\":\"标题\"}]。")
    p.add_argument("--is-send-email", "--is_send_email", dest="is_send_email", default="false", metavar="BOOL", help="创建成功后是否发邮件：true/false，默认 false。")
    p.add_argument("--is-send-icenter", "--is_send_icenter", dest="is_send_icenter", default="false", metavar="BOOL", help="创建成功后是否发 iCenter 消息：true/false，默认 false。")
    p.add_argument("--mail-to", "--mailTo", dest="mail_to", default=None, metavar="RECIPIENTS", help="邮件接收人，多人英文逗号分隔。")
    p.add_argument("--icenter-to", "--icenter_to", dest="icenter_to", default=None, metavar="RECIPIENTS", help="iCenter 消息接收人，多人英文逗号分隔。")
    p.add_argument("--smart-complete", "--smartComplete", dest="smart_complete", default="true", metavar="BOOL", help="是否启用智能补充（从历史创建记录学习并补充未填字段），默认 true。")
    p.add_argument("--relate-workitem-ids", "--relateWorkitemIds", dest="relate_workitem_ids", default=None, metavar="IDS", help="创建后要关联的工作项标识，多个以英文逗号分隔；需与 --relate-type 同时使用。")
    p.add_argument("--relate-type", "--relateType", dest="relate_type", default=None, metavar="TYPE", help="关联类型中文名，如 子级、父级、相关；与 --relate-workitem-ids 同时使用。")
    p.add_argument("--user-input", "--userInput", dest="user_input", default="", metavar="TEXT", help="用户的原始输入，必须是用户真实完整的一句话。")
    return p


def _args_to_input_data(args):
    if not args.work_item_type_name and not args.work_item_type_key:
        raise ValueError("必须提供 --work-item-type-name 或 --work-item-type-key 之一")
    data = {"fields": json.loads(args.fields)}
    if args.work_item_type_name:
        data["workItemTypeName"] = args.work_item_type_name
    if args.work_item_type_key:
        data["workItemTypeKey"] = args.work_item_type_key
    data["is_send_email"] = _parse_bool(args.is_send_email)
    data["is_send_icenter"] = _parse_bool(args.is_send_icenter)
    data["smart_complete"] = _parse_bool(args.smart_complete)
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
    except ValueError as e:
        err_out = f"错误: {e}"
        mindset_text = get_mindset_message("create_error")
        if mindset_text:
            err_out = err_out + "\n\n" + mindset_text
        safe_print(err_out, file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        err_out = f"错误: --fields 必须是合法 JSON 数组: {e}"
        mindset_text = get_mindset_message("create_error")
        if mindset_text:
            err_out = err_out + "\n\n" + mindset_text
        safe_print(err_out, file=sys.stderr)
        sys.exit(1)
    logger.info("原始入参: %s", json.dumps(sanitize_for_log(input_data), ensure_ascii=False, default=str))
    raw_user_input = (input_data.get("userInput") or "").strip()
    if raw_user_input:
        logger.info("原始用户输入: %s", raw_user_input[:500] if len(raw_user_input) > 500 else raw_user_input)
    work_item_type_name = input_data.get("workItemTypeName")
    work_item_type_key_input = input_data.get("workItemTypeKey")
    fields = input_data.get("fields", [])
    if not fields:
        logger.error("fields 不能为空数组")
        raise ValueError("fields 不能为空数组")
    try:
        logger.info("=" * 100)
        logger.info("开始创建工作项")
        logger.info(f"日志文件: {LOG_FILE}")
        logger.info(f"输入参数: workItemTypeKey={work_item_type_key_input or 'N/A'}, workItemTypeName={work_item_type_name or 'N/A'}, workspaceSearchName={input_data.get('workspaceSearchName') or 'N/A'}")
        
        base_config = load_base_config()
        headers = get_auth_headers(input_data, base_config, logger=logger)

        # 工作区解析：入参 workspaceSearchName/workspace 优先，否则用配置；解析得到 workspace key
        logger.info("【创建工作项-开始工作区解析】")
        workspace_from_input = (input_data.get("workspace") or input_data.get("workspaceSearchName") or "").strip()
        if not workspace_from_input:
            logger.info("入参未提供 workspace/workspaceSearchName，将使用配置中的工作区或按「最近访问工作区」解析")
        workspace, resolve_err = resolve_workspace(
            workspace_from_input if workspace_from_input else None,
            base_config.get("workspace", ""),
            headers,
            raw_user_input=raw_user_input or None,
        )
        if resolve_err:
            safe_print(resolve_err, file=sys.stderr)
            sys.exit(1)
        base_config["workspace"] = workspace
        logger.info("【创建工作项-完成工作区解析】得到的工作区: %s", base_config.get("workspace", "N/A"))

        # 工作项类型解析：中文名通过 common 解析为 key，直接传入的 key 原样使用
        if work_item_type_name:
            if not workspace:
                raise ValueError("未配置工作区，无法解析工作项类型")
            resolved_keys, resolved_names = resolve_work_item_type_key_by_name(work_item_type_name, workspace, headers)
            if isinstance(resolved_keys, list) and len(resolved_keys) > 1:
                mindset_text = get_mindset_message(
                    "work_item_type_multi_key",
                    work_item_type_name=work_item_type_name,
                    resolved_keys=resolved_keys,
                    key_name_map=resolved_names,
                )
                err_msg = f"工作项类型「{work_item_type_name}」匹配到多个 key，无法自动选择。"
                if mindset_text:
                    err_msg = err_msg + "\n\n" + mindset_text
                raise ValueError(err_msg)
            work_item_type_key = resolved_keys[0] if isinstance(resolved_keys, list) else resolved_keys
            # logger.info(
            #     "通过中文名称 '%s' 获取到 key=%s, name=%s",
            #     work_item_type_name,
            #     work_item_type_key,
            #     resolved_names.get(work_item_type_key, ""),
            # )
        else:
            work_item_type_key = work_item_type_key_input

        # 提前抓取工作项模型全量字段列表：优先用工作项类型详情接口（bo.fields 转列表），供智能填充与创建接口共用
        work_item_type_detail_response = rdcloud_api.fetch_work_item_type_detail(
            workspace, work_item_type_key, headers, work_item_id=""
        )
        code = work_item_type_detail_response.get("code") or {}
        if isinstance(code, dict) and code.get("code") == "0000":
            bo = work_item_type_detail_response.get("bo") or {}
            fields_dict = bo.get("fields")
            if isinstance(fields_dict, dict) and fields_dict:
                field_items = list(fields_dict.values())
            else:
                field_items = fetch_fields(workspace, headers)
        else:
            field_items = fetch_fields(workspace, headers)

        # 获取智能填充候选字段：不立即合并，仅收集可填充的字段及值并保留，由定制脚本或默认逻辑决定是否使用
        if "smart_complete" in input_data:
            smart_complete = bool(input_data["smart_complete"])
        elif "smartComplete" in input_data:
            smart_complete = bool(input_data["smartComplete"])
        else:
            smart_complete = base_config.get("smart_complete", True)
        if smart_complete:
            input_data["smart_complete_candidates"] = collect_smart_complete_candidates(
                work_item_type_key, workspace, headers, base_config, field_items, fields
            )
        else:
            input_data["smart_complete_candidates"] = {}
            logger.info("智能补充已禁用（配置 assets/config.json 的 create.smart_complete 或传 --smart-complete true 启用）")

        # 可定制创建工作项逻辑，未定制的情况下默认执行 _create_work_item_default 方法
        create_fn = _get_create_work_item_fn(workspace, work_item_type_key)
        result, removed_field_names, created_as_draft = create_fn(
            work_item_type_key,
            fields,
            base_config,
            headers,
            field_items=field_items,
            smart_complete_candidates=input_data.get("smart_complete_candidates"),
        )
        
        # 按入参 is_send_email / is_send_icenter 在创建成功后发送邮件或 iCenter 消息
        if input_data.get("is_send_email") is True:
            _send_email_after_create(input_data, result, base_config, headers)
        if input_data.get("is_send_icenter") is True:
            _send_icenter_after_create(input_data, result, base_config, headers)

        # 按入参 relateWorkitemIds + relateType 在创建成功后建立关联
        relate_ids_str = (input_data.get("relateWorkitemIds") or "").strip()
        relate_type = (input_data.get("relateType") or "").strip()
        if relate_ids_str and relate_type:
            new_id = (result.get("bo") or {}).get("id") if isinstance(result.get("bo"), dict) else result.get("id")
            if new_id:
                link_relation_id = rdcloud_api.LINK_RELATION_TYPE_MAP.get((relate_type or "").strip())
                if not link_relation_id:
                    logger.warning("未识别的关联类型「%s」，跳过创建后关联", relate_type)
                else:
                    relate_ids = [x.strip() for x in relate_ids_str.split(",") if x.strip()]
                    body = [{"workItemId": str(new_id), "linkRelationId": link_relation_id, "relatedWorkItemId": rid} for rid in relate_ids]
                    try:
                        rel_resp = rdcloud_api.batch_create_relations(body, headers)
                        failed = (rel_resp.get("bo") or {}).get("failedItems") or []
                        if failed:
                            logger.warning("创建后关联部分失败: %s", failed)
                        else:
                            logger.info("创建后关联成功: new_id=%s, relate_type=%s, relate_ids=%s", new_id, relate_type, relate_ids)
                    except Exception as e:
                        logger.warning("创建后关联请求异常: %s", e)
            else:
                logger.warning("创建结果中未取到工作项 id，跳过关联")
        
        # 根据配置决定输出格式
        format_as_markdown = input_data.get("formatAsMarkdown")
        if format_as_markdown is None:
            format_as_markdown = base_config.get("format_as_markdown", False)
        
        logger.info(f"输出格式: {'Markdown' if format_as_markdown else 'JSON'}")
        
        if format_as_markdown:
            # 格式化为 Markdown
            output = format_result_as_markdown(result)
            logger.info("Markdown 格式化完成")
            logger.info(f"Markdown 输出长度: {len(output)} 字符")
            logger.info(f"Markdown 输出前100字符: {output[:100]}")
        else:
            # 原样返回 JSON
            output = json.dumps(result, ensure_ascii=False, indent=2)
        if removed_field_names:
            suffix = get_mindset_message("create_fields_not_found", removed_field_names=removed_field_names)
            if suffix:
                output = output + "\n\n" + suffix
            else:
                names = [x.get("key", x) if isinstance(x, dict) else x for x in removed_field_names]
                output = output + "\n\n以下字段未找到、已从创建列表移除：" + "、".join(f"「{n}」" for n in names)
        if created_as_draft:
            output = output + "\n\n本次以草稿形式创建成功（byPassRules=0 兜底）。"
        
        # 记录执行结果
        logger.info("=" * 50)
        logger.info("执行结果:")
        logger.info(f"输出格式: {'Markdown' if format_as_markdown else 'JSON'}")
        logger.info(f"输出长度: {len(output)} 字符")
        # 对于多行输出，逐行记录以确保完整记录
        output_lines = output.split('\n')
        for line in output_lines:
            logger.info(line)
        logger.info("=" * 50)
        logger.info("工作项创建流程完成")
        mindset_text = get_mindset_message("create_result")
        parsed_summary = format_parsed_summary(
            {
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
        json_input = sys.argv[1] if len(sys.argv) > 1 else ""
        
        if json_input and hasattr(e, 'pos') and e.pos is not None:
            # 显示错误位置附近的 JSON 内容
            start_pos = max(0, e.pos - 100)
            end_pos = min(len(json_input), e.pos + 100)
            context = json_input[start_pos:end_pos]
            
            # 标记错误位置
            marker_pos = e.pos - start_pos
            marker = " " * marker_pos + "^"
            
            error_msg += f"\n\n错误位置: 字符 {e.pos}"
            if hasattr(e, 'lineno') and e.lineno:
                error_msg += f" (行 {e.lineno}, 列 {e.colno if hasattr(e, 'colno') else 'N/A'})"
            error_msg += f"\n\n上下文:\n{context}\n{marker}"
            
            # 提供修复建议
            if "Expecting ',' delimiter" in str(e):
                error_msg += "\n\n修复建议: 检查是否缺少逗号，或最后一个属性后有多余的逗号"
            elif "Expecting property name" in str(e):
                error_msg += "\n\n修复建议: 检查属性名是否正确，是否缺少引号"
            elif "Unterminated string" in str(e):
                error_msg += "\n\n修复建议: 检查字符串是否未正确闭合，字符串中的引号需要使用 \\\" 转义"
        
        #logger.error(error_msg)
        err_out = f"错误: {error_msg}"
        mindset_text = get_mindset_message("create_error")
        if mindset_text and mindset_text not in error_msg:
            err_out = err_out + "\n\n" + mindset_text
        logger.error(err_out)
        safe_print(err_out, file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        #logger.error(f"执行失败: {str(e)}", exc_info=True)
        err_out = f"错误: {str(e)}"
        mindset_text = get_mindset_message("create_error")
        if mindset_text and mindset_text not in str(e):
            err_out = err_out + "\n\n" + mindset_text
        logger.error(err_out)
        safe_print(err_out, file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()

