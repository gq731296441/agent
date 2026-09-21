#!/usr/bin/env python
"""基于现有工作项复制新工作项脚本。支持命令行参数（--source-work-item-id、--target-work-item-type-name 等）及 -h/--help。"""

import sys
import json
import re
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

# 获取脚本所在目录（atomic/）；技能根目录为 scripts 的上级
SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_ROOT = SCRIPT_DIR.parent
SKILL_DIR = SCRIPTS_ROOT.parent
CONFIG_FILE = SKILL_DIR / "assets" / "config.json"
# 容器内 skill 为只读挂载（/mnt/skills/<id>），日志改写到可写工作区，并按 skill 分目录
LOG_DIR = (Path("/workspace/.skill-logs") / SKILL_DIR.name) if Path("/workspace").is_dir() else (SKILL_DIR / "logs")
LOG_FILE = LOG_DIR / f"copy_work_item_{datetime.now().strftime('%Y%m%d')}.log"

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from common.log_helper import (
    truncate_for_log,
    get_logger,
    ENABLE_LOG,
    safe_print,
    sanitize_for_log,
    setup_logging,
    apply_log_config,
)
from common.config_helper import load_base_config as load_base_config_from_helper
from common.auth_helper import get_auth_headers
from common.field_value_resolver import _get_field_type as get_field_type_from_items
from common.field_value_resolver import _get_field_display_name
from common.field_value_resolver import resolve_field_value
from common.notify_helper import (
    send_mail as notify_send_mail,
    send_icenter_message as notify_send_icenter,
)
from common.field_resolver import resolve_field_key
from common.work_item_type_resolver import (
    resolve_work_item_type_key as resolve_work_item_type_key_by_name,
)
from common.workspace.workspace_resolver import resolve_workspace
from common.mindset_messages import get_mindset_message
from common import rdcloud_api
from create_work_item import _step_create_init, _step_destroy_event

if ENABLE_LOG:
    setup_logging(LOG_FILE, add_stderr=True)
logger = get_logger(__name__)

# 系统字段排除：复制时不复制这些字段（新工作项自动生成）
SYSTEM_EXCLUDE_KEYS = [
    "System_Id",
    "System_CreatedDate",
    "System_CreatedBy",
    "System_ChangedDate",
    "System_ChangedBy",
    "System_Version",
    "System_StateChangeTime",
    "System_StateReason",
    "System_SourceId",
    "System_Source",
    "System_HandledBy",
    "System_Lane",
    "System_Source_Tag",
    "System_Order",
    "System_Followers",
    "System_Release",
    "System_Branch",
    "System_WorkItemTypeKey",
    "System_Title",
    "System_WorkItemType",
    "System_WorkspaceKey",
    "System_State",
    "System_AssignedTo",
    "System_SourceWorkItem",  # 复制源字段（系统会自动设置）
]

# 标题和描述字段
TITLE_FIELD_KEY = "System_Title"
DESCRIPTION_FIELD_KEYS = frozenset({"System_Description", "System_Description_html"})


def _load_field_items(workspace: str, work_item_type_key: str, headers: dict) -> list:
    """加载目标类型的字段配置（字段元信息）。"""
    try:
        fields_resp = rdcloud_api.fetch_fields(workspace, headers)
        if isinstance(fields_resp, list):
            items = fields_resp
        elif isinstance(fields_resp, dict):
            items = (fields_resp.get("bo") or {}).get("items", [])
        else:
            items = []
        if not items:
            logger.warning("获取字段列表为空，工作区=%s", workspace)
            return []
        return items
    except Exception as e:
        logger.warning("加载字段配置失败: %s", e)
        return []


def _get_field_display_name(key: str, field_items: list) -> Optional[str]:
    """从字段配置中获取字段显示名。"""
    for item in field_items:
        if (
            isinstance(item, dict)
            and (item.get("referenceName") or item.get("key")) == key
        ):
            return item.get("name") or item.get("nameZh") or item.get("displayName")
    return None


def _parse_bool(s):
    """解析布尔字符串。"""
    if s is None:
        return False
    return str(s).strip().lower() in ("true", "1", "yes")


def _build_parser():
    examples = (
        "完整示例:\n"
        "  # 1) 基于 RAN-7402567 复制为新需求，覆盖字段并作为子级\n"
        "  %(prog)s --source-work-item-id RAN-7402567 --target-work-item-type-name 需求 "
        '--override-fields \'[{"key":"System_Title","value":"LTE、NR协议字段全量梳理—用户面二队"},'
        '{"key":"System_AssignedTo","value":"李海洋"},{"key":"团队","value":"用户面二队"}]\' '
        '--child-of RAN-7402567 --is-send-icenter true --icenter-to "李海洋" '
        "--user-input \"基于RAN-7402567复制一个新需求，指派给李海洋，标题为'LTE、NR协议字段全量梳理—用户面二队'，团队为用户面二队，作为子级，完成后发送给李海洋\"\n\n"
        "  # 2) 全量复制（不覆盖任何字段，保持原样）\n"
        "  %(prog)s --source-work-item-id RAN-123 --target-work-item-type-key Task\n\n"
        "  # 3) 仅复制必填字段（与 --override-fields 互斥，二者勿同条命令使用）\n"
        "  %(prog)s --source-work-item-id RAN-123 --target-work-item-type-name 任务 --copy-all false\n"
    ) % {"prog": "copy_work_item.py"}

    p = argparse.ArgumentParser(
        prog="copy_work_item.py",
        description="基于现有工作项复制新工作项：复制源项字段（排除系统字段），可选覆盖特定字段，可选建立父子关联，可选发送通知。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=examples,
    )
    p.add_argument(
        "--source-work-item-id",
        "--sourceWorkItemId",
        dest="source_work_item_id",
        required=True,
        metavar="ID",
        help="源工作项标识，如 RAN-7402567",
    )
    p.add_argument(
        "--target-work-item-type-name",
        "--targetWorkItemTypeName",
        dest="target_work_item_type_name",
        default=None,
        metavar="NAME",
        help="目标工作项类型中文名，如 需求、任务、缺陷（与 --target-work-item-type-key 二选一）",
    )
    p.add_argument(
        "--target-work-item-type-key",
        "--targetWorkItemTypeKey",
        dest="target_work_item_type_key",
        default=None,
        metavar="KEY",
        help="目标工作项类型 key，如 Requirement（与 --target-work-item-type-name 二选一）",
    )

    group = p.add_mutually_exclusive_group(required=False)
    group.add_argument(
        "--override-fields",
        "--overrideFields",
        dest="override_fields",
        default=None,
        metavar="JSON",
        help='需要覆盖的字段数组 JSON，如 [{"key":"System_Title","value":"新标题"}]；不传则默认复制全部字段且不覆盖',
    )
    group.add_argument(
        "--copy-all",
        "--copyAll",
        dest="copy_all",
        default="true",
        metavar="BOOL",
        help="是否复制源工作项全部非系统字段，默认 true；设为 false 则仅复制目标类型必填字段（与 --override-fields 互斥，勿同条命令共用）",
    )

    p.add_argument(
        "--child-of",
        "--childOf",
        dest="child_of",
        default=None,
        metavar="ID",
        help="新工作项作为指定工作项的子级（自动建立子级关联）",
    )
    p.add_argument(
        "--link-relation-id",
        "--linkRelationId",
        dest="link_relation_id",
        default=None,
        metavar="RELATION_ID",
        help="复制时直接建立与源单的关联关系ID（如 linkRelatedType_3）",
    )
    p.add_argument(
        "--is-send-email",
        "--is_send_email",
        dest="is_send_email",
        default="false",
        metavar="BOOL",
        help="创建成功后是否发邮件：true/false，默认 false",
    )
    p.add_argument(
        "--is-send-icenter",
        "--is_send_icenter",
        dest="is_send_icenter",
        default="false",
        metavar="BOOL",
        help="创建成功后是否发 iCenter 消息：true/false，默认 false",
    )
    p.add_argument(
        "--mail-to",
        "--mailTo",
        dest="mail_to",
        default=None,
        metavar="RECIPIENTS",
        help="邮件接收人，多人英文逗号分隔；默认使用新工作项指派给用户的邮箱",
    )
    p.add_argument(
        "--icenter-to",
        "--icenter_to",
        dest="icenter_to",
        default=None,
        metavar="RECIPIENTS",
        help="iCenter 消息接收人，多人英文逗号分隔；默认使用新工作项指派给用户",
    )
    p.add_argument(
        "--smart-complete",
        "--smartComplete",
        dest="smart_complete",
        default="true",
        metavar="BOOL",
        help="是否启用智能补充（从历史创建记录学习并补充未填字段），默认 true（复制场景通常不需要）",
    )
    p.add_argument(
        "--format-as-markdown",
        "--formatAsMarkdown",
        dest="format_as_markdown",
        default=None,
        metavar="BOOL",
        help="输出格式：true=Markdown，false=JSON；不传时复制脚本默认 true（Markdown）",
    )
    p.add_argument(
        "--user-input",
        "--userInput",
        dest="user_input",
        default="",
        metavar="TEXT",
        help="用户的原始输入，必须是用户真实完整的一句话。",
    )
    return p


def _args_to_input_data(args):
    if not args.source_work_item_id:
        raise ValueError("必须提供 --source-work-item-id")
    data = {
        "sourceWorkItemId": args.source_work_item_id.strip(),
        "copyAll": _parse_bool(args.copy_all),
    }
    if args.target_work_item_type_name:
        data["targetWorkItemTypeName"] = args.target_work_item_type_name.strip()
    if args.target_work_item_type_key:
        data["targetWorkItemTypeKey"] = args.target_work_item_type_key.strip()
    if args.override_fields:
        try:
            data["overrideFields"] = json.loads(args.override_fields)
        except json.JSONDecodeError as e:
            raise ValueError(f"--override-fields JSON 格式无效: {e}")
    if args.child_of:
        data["childOf"] = args.child_of.strip()
    if args.link_relation_id:
        data["linkRelationId"] = args.link_relation_id.strip()
    data["is_send_email"] = _parse_bool(args.is_send_email)
    data["is_send_icenter"] = _parse_bool(args.is_send_icenter)
    if args.mail_to:
        data["mail_to"] = args.mail_to.strip()
    if args.icenter_to:
        data["icenter_to"] = args.icenter_to.strip()
    data["smart_complete"] = _parse_bool(args.smart_complete)
    if args.format_as_markdown is not None:
        data["format_as_markdown"] = _parse_bool(args.format_as_markdown)
    if getattr(args, "user_input", None):
        data["userInput"] = (args.user_input or "").strip()
    return data


def _get_work_item_type_key(
    workspace: str, type_name_or_key: str, field_items: list, headers: dict
) -> str:
    """解析工作项类型 key（支持中文名或 key）。"""
    type_str = (type_name_or_key or "").strip()
    if not type_str:
        raise ValueError("工作项类型为空")
    # 如果已经是 key 格式（不含中文），直接返回
    if not re.search(r"[\u4e00-\u9fa5]", type_str):
        return type_str
    # 先通过类型专用解析器解析（更稳定）
    try:
        resolved, _km = resolve_work_item_type_key_by_name(type_str, workspace, headers)
        if isinstance(resolved, list):
            keys = [str(x).strip() for x in resolved if str(x).strip()]
            if keys:
                if len(keys) > 1:
                    logger.warning(
                        "工作项类型「%s」解析到多个 key，默认取第一个: %s", type_str, keys
                    )
                return keys[0]
        elif isinstance(resolved, str) and resolved.strip():
            return resolved.strip()
    except Exception as e:
        logger.warning("类型专用解析器未命中，回退字段映射: type=%s, err=%s", type_str, e)

    # 回退：通过字段配置映射查找 key
    for item in field_items:
        if isinstance(item, dict):
            name = item.get("name") or item.get("nameZh") or ""
            if name == type_str:
                key = item.get("referenceName") or item.get("key")
                if key:
                    return key
    # 未找到则尝试通过类型专用接口（本技能没有，暂不处理）
    raise ValueError(f"未找到工作项类型「{type_str}」对应的 key")


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
        name = _get_field_display_name(k, field_items=field_items) or ""
        return "描述" in name

    def is_project(f):
        if not isinstance(f, dict):
            return False
        k = (f.get("key") or "").strip()
        name = _get_field_display_name(k, field_items=field_items) or ""
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


def _format_result_as_markdown(result, action="复制"):
    """将结果格式化为 Markdown。"""
    if not result or not isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False, indent=2)
    bo = result.get("bo") or result
    work_item_id = bo.get("id", "")
    title = ""
    for f in bo.get("fields", []) if isinstance(bo, dict) else []:
        if isinstance(f, dict) and f.get("key") == "System_Title":
            title = str(f.get("value", ""))
            break
    if not title:
        title = bo.get("System_Title", "")
    work_item_type = ""
    wt = bo.get("System_WorkItemType")
    if isinstance(wt, dict):
        work_item_type = wt.get("name", "") or wt.get("nameZh", "")
    elif isinstance(wt, str):
        work_item_type = wt
    link_url = (
        rdcloud_api.studio_workitem_transmit_url(work_item_id) if work_item_id else ""
    )
    title_link = f"[{title}]({link_url})" if title and link_url else title
    id_link = (
        f"[{work_item_id}]({link_url})" if work_item_id and link_url else work_item_id
    )
    md_lines = [
        f"# 工作项{action}成功\n\n",
        "| 字段 | 值 |\n",
        "|------|------|\n",
        f"| 标题 | {title_link} |\n",
        f"| 标识 | {id_link} |\n",
        f"| 类型 | {work_item_type} |\n",
    ]
    return "".join(md_lines)


def _send_email_after_create(input_data, result, base_config, headers):
    """发送邮件通知（复用 create 脚本逻辑）。"""
    try:
        work_item_id = (
            result.get("bo", {}).get("id")
            if isinstance(result.get("bo"), dict)
            else result.get("id") or ""
        )
        title = ""
        for f in (
            result.get("bo", {}).get("fields", [])
            if isinstance(result.get("bo"), dict)
            else []
        ):
            if isinstance(f, dict) and f.get("key") == "System_Title":
                title = str(f.get("value", ""))
                break
        body_base = (
            input_data.get("mail_body")
            or f"已创建工作项：{work_item_id} {title}".strip()
        )
        work_item_url = (
            rdcloud_api.studio_workitem_transmit_url(work_item_id)
            if work_item_id
            else ""
        )
        mail_body = (
            f'{body_base}<br/><a href="{work_item_url}">查看工作项</a>'
            if work_item_url
            else body_base
        )
        auth = input_data.get("auth") or {}
        emp_no = auth.get("emp_no") or headers.get("X-Emp-No", "")
        mail_title = input_data.get("mail_title") or "工作项已复制"
        mail_to = input_data.get("mail_to") or emp_no
        logger.info(
            "发送邮件: 场景=复制, 标题=%s, 收件人=%s",
            truncate_for_log(mail_title, 80),
            truncate_for_log(str(mail_to), 50),
        )
        result_send = notify_send_mail(
            mail_title=mail_title,
            mail_to=mail_to,
            mail_body=mail_body,
            headers=headers,
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
            logger.info("发送邮件完成")
    except Exception as e:
        logger.warning("发送邮件异常（不影响复制结果）: %s", e)


def _send_icenter_after_create(input_data, result, base_config, headers):
    """发送 iCenter 消息（复用 create 脚本逻辑）。"""
    try:
        work_item_id = (
            result.get("bo", {}).get("id")
            if isinstance(result.get("bo"), dict)
            else result.get("id") or ""
        )
        title = ""
        for f in (
            result.get("bo", {}).get("fields", [])
            if isinstance(result.get("bo"), dict)
            else []
        ):
            if isinstance(f, dict) and f.get("key") == "System_Title":
                title = str(f.get("value", ""))
                break
        view_url = (
            input_data.get("viewUrl")
            or rdcloud_api.studio_workitem_transmit_url(work_item_id)
            if work_item_id
            else ""
        )
        to = input_data.get("icenter_to") or headers.get("X-Emp-No", "")
        if isinstance(to, str) and "," in to:
            to = [x.strip() for x in to.split(",") if x.strip()]
        subject = input_data.get("subject") or "工作项已复制"
        message_content = (
            input_data.get("message") or f"已创建工作项：{work_item_id} {title}".strip()
        )
        logger.info(
            "发送 iCenter 消息: 主题=%s, 接收人=%s",
            truncate_for_log(subject, 80),
            truncate_for_log(str(to), 50),
        )
        result_send = notify_send_icenter(
            message_content=message_content,
            headers=headers,
            subject=subject,
            to=to,
            view_url=view_url or "",
        )
        if result_send.get("status") == "error":
            logger.warning(
                "发送 iCenter 消息未成功: %s", result_send.get("message", "")
            )
        else:
            logger.info("发送 iCenter 消息完成")
    except Exception as e:
        logger.warning("发送 iCenter 消息异常（不影响复制结果）: %s", e)


def _extract_source_copy_data(source_item: dict) -> tuple[dict, str]:
    """从源单据提取可复制字段映射和源标题。"""
    source_fields = source_item.get("fields") or []
    source_title = ""
    source_map: Dict[str, Any] = {}
    for f in source_fields:
        if not isinstance(f, dict):
            continue
        k = (f.get("key") or "").strip()
        if k == TITLE_FIELD_KEY and not source_title:
            v = f.get("value")
            source_title = (str(v).strip() if v is not None else "")
        if not k or k in SYSTEM_EXCLUDE_KEYS:
            continue
        source_map[k] = f.get("value")
    return source_map, source_title


def _merge_source_into_init_fields(fields: list, source_map: dict, source_title: str) -> list:
    """将源单字段值回填到初始化字段，并做标题基础兜底。"""
    for f in fields:
        if not isinstance(f, dict):
            continue
        k = (f.get("key") or "").strip()
        if k in source_map:
            f["value"] = source_map[k]
    if source_title:
        has_title = False
        for f in fields:
            if not isinstance(f, dict):
                continue
            if (f.get("key") or "").strip() != TITLE_FIELD_KEY:
                continue
            has_title = True
            cur = f.get("value")
            if cur is None or (isinstance(cur, str) and cur.strip() == ""):
                f["value"] = source_title
            break
        if not has_title:
            fields.append({"key": TITLE_FIELD_KEY, "value": source_title})
    return fields


def _normalize_override_fields(
    override_fields: List[Dict], field_items: list, headers: dict, workspace: str
) -> List[Dict[str, Any]]:
    """将覆盖字段 key 统一解析为系统 key。"""
    normalized: List[Dict[str, Any]] = []
    for override in (override_fields or []):
        if not isinstance(override, dict):
            continue
        raw_key = (override.get("key") or "").strip()
        if not raw_key:
            continue
        resolved_key = resolve_field_key(
            raw_key,
            field_items,
            headers=headers,
            raise_on_not_found=True,
            scene="复制工作项_覆盖字段映射",
            workspace=workspace,
        )
        normalized.append({"key": resolved_key, "value": override.get("value")})
    return normalized


def _apply_override_fields(
    fields: list,
    normalized_override_fields: List[Dict[str, Any]],
    smart_complete: bool,
    workspace: str,
    target_type_key: str,
    field_items: list,
    headers: dict,
    event_id: str,
) -> list:
    """应用覆盖字段（更新已有字段或新增字段）。"""
    if not normalized_override_fields:
        return fields
    key_to_field = {f["key"]: f for f in fields if isinstance(f, dict) and f.get("key")}
    for override in normalized_override_fields:
        key = override.get("key")
        raw_value = override.get("value")
        if not key:
            continue
        resolved_value = raw_value
        if smart_complete:
            try:
                resolved_value = resolve_field_value(
                    key,
                    raw_value if raw_value is not None else "",
                    workspace,
                    target_type_key,
                    field_items,
                    headers,
                    scene="复制工作项_覆盖字段",
                    event_id=event_id,
                )
            except Exception as e:
                logger.warning("字段「%s」值解析失败，使用原始值: %s", key, e)
                resolved_value = raw_value
        if key in key_to_field:
            key_to_field[key]["value"] = resolved_value
            logger.info("覆盖字段: %s = %s", key, truncate_for_log(str(resolved_value), 80))
        else:
            field_type = get_field_type_from_items(key, field_items) or "string"
            fields.append({"key": key, "value": resolved_value, "type": field_type})
            logger.info(
                "新增字段: %s = %s (type=%s)",
                key,
                truncate_for_log(str(resolved_value), 80),
                field_type,
            )
    return fields


def _create_parent_relation(new_id: str, child_of: str, headers: dict) -> None:
    """建立父子关联（new_id 作为 child_of 的子级）。"""
    link_relation_id = rdcloud_api.LINK_RELATION_TYPE_MAP.get("父级")
    if not link_relation_id:
        raise ValueError("未找到关联类型「父级」映射，父子关联未执行")
    body_relation = [
        {
            "workItemId": new_id,
            "linkRelationId": link_relation_id,
            "relatedWorkItemId": child_of,
        }
    ]
    try:
        rel_resp = rdcloud_api.batch_create_relations(body_relation, headers)
        failed = (rel_resp.get("bo") or {}).get("failedItems") or []
        if failed:
            first_failed = failed[0] if isinstance(failed[0], dict) else {}
            err_msg = str(first_failed.get("errorMsg") or "")
            if "已有父关联" in err_msg:
                raise ValueError(
                    f"父子关联失败：新单据已存在父关联，无法再设置父级为 {child_of}。"
                    "请先确认源单据是否已带父级关系，或改为不传 --child-of。"
                )
            raise ValueError(f"父子关联失败: {failed}")
        logger.info("父子关联成功: 新项=%s, 父项=%s", new_id, child_of)
    except Exception as e:
        raise ValueError(f"父子关联请求异常: {e}")


def _filter_fields_before_submit(fields: list, keep_keys: Optional[set] = None) -> list:
    """提交前统一过滤排除字段（可保留白名单 key）。"""
    keep_keys = keep_keys or set()
    filtered = []
    for f in fields:
        if not isinstance(f, dict):
            continue
        k = (f.get("key") or "").strip()
        if not k:
            continue
        if k in SYSTEM_EXCLUDE_KEYS and k not in keep_keys:
            continue
        filtered.append(f)
    return filtered


def _copy_work_item_default(
    workspace: str,
    source_work_item_id: str,
    target_type_key: Optional[str],
    copy_all: bool,
    smart_complete: bool,
    override_fields: List[Dict],
    child_of: Optional[str],
    link_relation_id: Optional[str],
    send_email: bool,
    send_icenter: bool,
    mail_to: Optional[str],
    icenter_to: Optional[str],
    headers: dict,
    base_config: dict,
    user_input: str,
    format_as_markdown: bool,
    field_items: list,
):
    """核心复制流程（优化版：使用直接复制接口，1 次 HTTP 请求）。"""
    logger.info(
        "开始复制工作项（直接复制模式）: 源=%s, 目标类型=%s, 工作区=%s",
        source_work_item_id,
        target_type_key,
        workspace,
    )

    if not target_type_key:
        raise ValueError("目标工作项类型不能为空")

    # 1. 查询源工作项详情
    source_detail = rdcloud_api.query_work_item_details([source_work_item_id], headers)
    source_item = (
        ((source_detail.get("bo") or {}).get("items") or [{}])[0]
        if isinstance(source_detail, dict)
        else {}
    )
    if not source_item:
        raise ValueError(f"未找到源工作项：{source_work_item_id}")

    # 2. 提取源字段（排除系统字段）
    source_fields = source_item.get("fields") or []
    fields = []
    source_title = ""
    for f in source_fields:
        if not isinstance(f, dict):
            continue
        k = (f.get("key") or "").strip()
        if k in SYSTEM_EXCLUDE_KEYS:
            continue
        if k == "System_Title":
            source_title = str(f.get("value", ""))
        fields.append(
            {"key": k, "value": f.get("value"), "type": f.get("type", "string")}
        )

    logger.info("从源工作项提取字段数=%d", len(fields))

    # 3. copy_all=false 时仅保留必填字段（按目标类型字段配置）
    if not copy_all and field_items:
        required_keys = {
            (item.get("referenceName") or item.get("key") or "").strip()
            for item in field_items
            if isinstance(item, dict)
            and (item.get("required") is True or item.get("isRequired") is True)
        }
        fields = [
            f
            for f in fields
            if isinstance(f, dict)
            and (f.get("key") or "").strip()
            and (f.get("key") or "").strip() in required_keys
        ]
        logger.info("copy_all=false：仅保留目标类型必填字段，数量=%d", len(fields))

    # 4. 处理覆盖字段（支持中文字段名映射）
    normalized_override_fields = _normalize_override_fields(
        override_fields, field_items, headers, workspace
    )
    override_event_id = ""
    if smart_complete and normalized_override_fields:
        try:
            override_event_id, _virtual_id, _req_with_value, _req_without_value, _init_fields = _step_create_init(
                workspace, target_type_key, headers
            )
            logger.info("覆盖字段解析已启用 event 上下文: eventId=%s", override_event_id)
        except Exception as e:
            logger.warning("初始化 event 上下文失败，覆盖字段解析回退无 event 模式: %s", e)
            override_event_id = ""
    try:
        fields = _apply_override_fields(
            fields=fields,
            normalized_override_fields=normalized_override_fields,
            smart_complete=smart_complete,
            workspace=workspace,
            target_type_key=target_type_key,
            field_items=field_items,
            headers=headers,
            event_id=override_event_id,
        )
    finally:
        if override_event_id:
            _step_destroy_event(workspace, override_event_id, headers)
    if normalized_override_fields:
        logger.info("应用覆盖字段数=%d", len(normalized_override_fields))

    # batchCopy 的 fieldRelatedRef 需要使用“解析后的覆盖字段”，不是整单字段
    mapping_fields = None
    if normalized_override_fields:
        mapping_fields = []
        key_to_field = {
            (f.get("key") or "").strip(): f
            for f in fields
            if isinstance(f, dict) and (f.get("key") or "").strip()
        }
        for item in normalized_override_fields:
            k = (item.get("key") or "").strip()
            if not k:
                continue
            fv = key_to_field.get(k, {})
            mapping_fields.append(
                {
                    "key": k,
                    "value": fv.get("value", item.get("value")),
                    "type": fv.get("type") or get_field_type_from_items(k, field_items) or "string",
                }
            )

    # 5. 使用直接复制接口一次性完成
    source_type_key = ""
    for f in source_fields:
        if not isinstance(f, dict):
            continue
        if (f.get("key") or "").strip() == "System_WorkItemTypeKey":
            source_type_key = str(f.get("value") or "").strip()
            break
    logger.info("调用直接复制接口...")
    result = rdcloud_api.copy_work_item_direct(
        workspace=workspace,
        source_work_item_id=source_work_item_id,
        source_type_key=source_type_key or target_type_key,
        target_type_key=target_type_key,
        fields=fields,
        mapping_fields=mapping_fields,
        link_relation_id=link_relation_id or "",
        headers=headers,
    )
    logger.info("直接复制完成")

    # 6. 建立父子关联（可选）
    new_id = (
        (result.get("bo") or {}).get("id")
        if isinstance(result.get("bo"), dict)
        else result.get("id")
    )
    if new_id and child_of:
        _create_parent_relation(new_id, child_of, headers)

    # 6.1 复制接口返回通常不含完整标题/类型，补查一次详情用于展示
    if new_id:
        try:
            detail_resp = rdcloud_api.query_work_item_details([new_id], headers)
            detail_items = (
                ((detail_resp.get("bo") or {}).get("items") or [])
                if isinstance(detail_resp, dict)
                else []
            )
            if detail_items and isinstance(detail_items[0], dict):
                detail_item = detail_items[0]
                bo = result.get("bo") if isinstance(result, dict) else None
                if not isinstance(bo, dict):
                    bo = {}
                    if isinstance(result, dict):
                        result["bo"] = bo
                bo["id"] = new_id
                fields = detail_item.get("fields") or []
                if isinstance(fields, list):
                    bo["fields"] = fields
                for f in fields:
                    if (
                        isinstance(f, dict)
                        and (f.get("key") or "").strip() == "System_Title"
                    ):
                        bo["System_Title"] = f.get("value") or ""
                        break
                for f in fields:
                    if (
                        isinstance(f, dict)
                        and (f.get("key") or "").strip() == "System_WorkItemType"
                    ):
                        bo["System_WorkItemType"] = f.get("value")
                        break
        except Exception as e:
            logger.warning("补查新单详情失败（不影响复制结果）: %s", e)

    # 7. 发送通知（可选）
    if send_email:
        try:
            _send_email_after_create(
                {"mail_to": mail_to, "mail_body": f"已复制工作项：{new_id}"},
                result,
                base_config,
                headers,
            )
        except Exception as e:
            logger.warning("发送邮件异常: %s", e)
    if send_icenter:
        try:
            _send_icenter_after_create(
                {"icenter_to": icenter_to, "message": f"已复制工作项：{new_id}"},
                result,
                base_config,
                headers,
            )
        except Exception as e:
            logger.warning("发送 iCenter 消息异常: %s", e)

    # 8. 格式化输出
    output = (
        _format_result_as_markdown(result, action="复制")
        if format_as_markdown
        else json.dumps(result, ensure_ascii=False, indent=2)
    )
    safe_print(output)
    logger.info("工作项复制完成: 新工作项=%s", new_id)
    return result


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
        err_out = f"错误: --override-fields 必须是合法 JSON 数组: {e}"
        mindset_text = get_mindset_message("create_error")
        if mindset_text:
            err_out = err_out + "\n\n" + mindset_text
        safe_print(err_out, file=sys.stderr)
        sys.exit(1)

    logger.info(
        "原始入参: %s",
        json.dumps(sanitize_for_log(input_data), ensure_ascii=False, default=str),
    )
    raw_user_input = (input_data.get("userInput") or "").strip()
    if raw_user_input:
        logger.info(
            "原始用户输入: %s",
            raw_user_input[:500] if len(raw_user_input) > 500 else raw_user_input,
        )

    source_work_item_id = input_data.get("sourceWorkItemId")
    target_type_name = input_data.get("targetWorkItemTypeName")
    target_type_key_input = input_data.get("targetWorkItemTypeKey")
    override_fields = input_data.get("overrideFields", [])
    copy_all = input_data.get("copyAll", True)
    child_of = input_data.get("childOf")
    link_relation_id = input_data.get("linkRelationId")
    send_email = input_data.get("is_send_email", False)
    send_icenter = input_data.get("is_send_icenter", False)
    mail_to = input_data.get("mail_to")
    icenter_to = input_data.get("icenter_to")
    smart_complete = input_data.get("smart_complete", True)
    format_as_markdown = input_data.get("format_as_markdown")
    if format_as_markdown is None:
        format_as_markdown = input_data.get("formatAsMarkdown")
    user_input = input_data.get("userInput", "")

    try:
        if not source_work_item_id:
            raise ValueError("--source-work-item-id 不能为空")

        base_config = load_base_config_from_helper()
        input_data_for_auth = {"auth": None, "userInput": user_input}
        headers = get_auth_headers(input_data_for_auth, base_config, logger=logger)

        cfg_workspace = (base_config.get("workspace") or "").strip()
        if cfg_workspace:
            workspace = cfg_workspace
        else:
            workspace, ws_err = resolve_workspace(
                None, cfg_workspace, headers, raw_user_input=user_input
            )
            if ws_err:
                raise ValueError(ws_err)
        if not workspace:
            raise ValueError("配置文件中未指定 workspace")

        # 解析目标工作项类型
        if target_type_name or target_type_key_input:
            field_items = _load_field_items(workspace, "", headers)  # 加载通用字段
            target_type_key = _get_work_item_type_key(
                workspace, target_type_name or target_type_key_input, field_items, headers
            )
        else:
            # 默认同源工作项类型（需提前查询源工作项）
            source_detail = rdcloud_api.query_work_item_details(
                [source_work_item_id], headers
            )
            bo = (source_detail or {}).get("bo") if isinstance(source_detail, dict) else None
            items = (bo or {}).get("items") if isinstance(bo, dict) else None
            if not isinstance(items, list) or not items:
                raise ValueError(
                    f"未查询到源单据 `{source_work_item_id}`。\n"
                    "请确认：\n"
                    "1) 单据号是否正确；\n"
                    "2) 当前账号是否有该单据查看权限；\n"
                    "3) 如需继续，可显式指定目标类型参数："
                    "`--target-work-item-type-name` 或 `--target-work-item-type-key`。"
                )
            source_bo = items[0] if isinstance(items[0], dict) else {}
            source_fields = source_bo.get("fields", [])
            for f in source_fields:
                if f.get("key") == "System_WorkItemTypeKey":
                    target_type_key = f.get("value")
                    break
            else:
                raise ValueError(
                    "未获取到源工作项类型，请指定 --target-work-item-type-name/key"
                )
            logger.info("使用源工作项类型: %s", target_type_key)
            # 加载目标类型的字段配置
            field_items = _load_field_items(workspace, target_type_key, headers)

        # 输出格式：未显式传入时，复制默认走 Markdown 展示（包含可点击链接）
        if format_as_markdown is None:
            format_as_markdown = True

        _copy_work_item_default(
            workspace=workspace,
            source_work_item_id=source_work_item_id,
            target_type_key=target_type_key,
            copy_all=copy_all,
            smart_complete=smart_complete,
            override_fields=override_fields,
            child_of=child_of,
            link_relation_id=link_relation_id,
            send_email=send_email,
            send_icenter=send_icenter,
            mail_to=mail_to,
            icenter_to=icenter_to,
            headers=headers,
            base_config=base_config,
            user_input=user_input,
            format_as_markdown=format_as_markdown,
            field_items=field_items,
        )
    except ValueError as e:
        err_msg = f"错误: {str(e)}"
        mindset_text = get_mindset_message("create_error")
        if mindset_text:
            err_msg = err_msg + "\n\n" + mindset_text
        safe_print(err_msg, file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        logger.exception("复制工作项失败")
        err_msg = f"复制失败: {str(e)}"
        mindset_text = get_mindset_message("create_error")
        if mindset_text:
            err_msg = err_msg + "\n\n" + mindset_text
        safe_print(err_msg, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    apply_log_config(CONFIG_FILE)
    if ENABLE_LOG:
        setup_logging(LOG_FILE)
    main()
