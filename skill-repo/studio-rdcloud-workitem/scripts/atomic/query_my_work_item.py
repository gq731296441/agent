#!/usr/bin/env python
"""查询与我相关的工作项。通过命令行参数传入 SQL、queryType 等；支持结果发邮件/发 iCenter 消息。"""

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
LOG_FILE = LOG_DIR / f"query_my_work_item_{datetime.now().strftime('%Y%m%d')}.log"

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))
from common.log_helper import get_log_config, truncate_for_log, get_logger, ENABLE_LOG, safe_print, sanitize_for_log, truncate_subprocess_output_for_log, apply_log_config, log_raw_argv, log_complete_params, setup_logging, format_parsed_summary
from common.auth_helper import (
    get_auth_headers,
    resolve_auth_token_from_env,
    resolve_emp_no_from_env,
    value_error_missing_emp_no_from_env,
)
from common.rdcloud_api import (
    URL_QUERY_WORK_ITEMS,
    URL_VIEWS,
    URL_SEND_MAIL,
    URL_GROUP_CHAT_SEARCH,
    URL_ICHAT_SHARE,
    URL_MY_RELATED_DEFAULT,
)
from common.workspace import apply_workspace_clause_to_query_condition
from query_work_item import convert_work_item_type_values, format_result_as_markdown
from common.sql_query_parser import build_query_data_from_select_and_conditions
from common.mindset_messages import (
    build_query_context_mindset_extras,
    get_mindset_message,
)

if ENABLE_LOG:
    setup_logging(LOG_FILE)
logger = get_logger(__name__)


def load_base_config():
    base_config = {
        "workspace": "", "tenant_id": "ZTE", "format_as_markdown": True
    }
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
            api = config.get("api", {})
            base_config["workspace"] = api.get("workspace", "")
            base_config["tenant_id"] = api.get("tenant_id", base_config["tenant_id"])
            base_config["format_as_markdown"] = config.get("format", {}).get("as_markdown", True)
        except Exception as e:
            logger.warning(f"配置文件读取失败: {e}")
    return base_config


def get_my_workspace_conditions(query_type, emp_no):
    if not emp_no:
        raise ValueError("缺少用户工号 emp_no")
    conditions_map = {
        "assigned_to_me": [f"(System_AppointedTo IN ('{emp_no}')\n\tOR System_AssignedTo IN ('{emp_no}'))\nAND assignedToMe('{emp_no}')"],
        "my_todo": [f"(System_AppointedTo IN ('{emp_no}')\n\tOR System_AssignedTo IN ('{emp_no}'))\nAND assignedToMe('{emp_no}')"],
        "created_by_me": [f"history('{emp_no}', 'true', 'NULL', 'NULL', 'CREATE')"],
        "edited_by_me": [f"history('{emp_no}', 'true', 'NULL', 'NULL', '!CREATE')"],
        "followed_by_me": [f"System_Followers IN ('{emp_no}')\nAND myFollowed('{emp_no}')"],
    }
    c = conditions_map.get(query_type)
    if not c:
        raise ValueError(f"不支持的 queryType: {query_type}，支持: {list(conditions_map.keys())}")
    return c


def add_time_range_condition(query_condition):
    if not query_condition or not isinstance(query_condition, dict):
        return query_condition
    clauses = query_condition.get("sourceClauses", [])
    if not isinstance(clauses, list):
        return query_condition
    if any(isinstance(c, dict) and c.get("field") == "System_ChangedDate" and c.get("value") == "@Today-365d" for c in clauses):
        return query_condition
    if clauses and clauses[-1].get("logicalOperator") == "":
        clauses[-1]["logicalOperator"] = "AND"
    clauses.append({
        "leftGroup": 0, "rightGroup": 0, "logicalOperator": "AND",
        "field": "System_ChangedDate", "operator": ">", "value": "@Today-365d"
    })
    return query_condition


def add_exclude_completed_condition(query_condition):
    if not query_condition or not isinstance(query_condition, dict):
        return query_condition
    clauses = query_condition.get("sourceClauses", [])
    if not isinstance(clauses, list):
        return query_condition
    if any(isinstance(c, dict) and "System_State" in (c.get("field") or "") for c in clauses):
        return query_condition
    if clauses and clauses[-1].get("logicalOperator") == "":
        clauses[-1]["logicalOperator"] = "AND"
    clauses.append({
        "leftGroup": 0, "rightGroup": 0, "logicalOperator": "AND",
        "field": "System_State.stateGroup.groupNameZh", "operator": "not in", "value": ["已取消", "处理完成"]
    })
    return query_condition

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
        return {
            "status": "error",
            "message": error_msg
        }
    
    view_api_url = URL_VIEWS.format(workspace=workspace_key)
    
    view_headers = {
        'Content-Type': 'application/json',
        'X-Tenant-Id': headers.get('X-Tenant-Id', 'ZTE'),
        'X-Auth-Value': headers.get('X-Auth-Value', ''),
        'X-Emp-No': headers.get('X-Emp-No', ''),
    }
    
    # 确保 view_data 包含必需的字段
    if 'viewNameEn' not in view_data or 'viewNameZh' not in view_data:
        error_msg = "视图数据缺少必需字段: viewNameEn 或 viewNameZh"
        logger.error(error_msg)
        return {
            "status": "error",
            "message": error_msg
        }
    
    # 设置默认值
    if 'viewType' not in view_data:
        view_data['viewType'] = 'personal'
    if 'queryType' not in view_data:
        view_data['queryType'] = 'filter'
    if 'workItemTypeKeys' not in view_data:
        view_data['workItemTypeKeys'] = []
    
    try:
        logger.info(f"保存视图: workspace={workspace_key}, viewNameZh={view_data.get('viewNameZh')}")
        logger.info(f"请求URL: {view_api_url}")
        logger.info("请求体: %s", json.dumps(sanitize_for_log(view_data), ensure_ascii=False, indent=2))
        
        response = requests.post(view_api_url, headers=view_headers, json=view_data, timeout=30)
        
        logger.info(f"响应状态码: {response.status_code}")
        
        if response.status_code == 200:
            result_data = response.json()
            if isinstance(result_data.get("code"), dict) and result_data["code"].get("code") == "0000":
                view_id = result_data.get("bo", {}).get("id")
                if view_id:
                    logger.info(f"视图保存成功，视图ID: {view_id}")
                    return {
                        "status": "success",
                        "view_id": view_id,
                        "message": "视图保存成功"
                    }
                else:
                    logger.warning("视图保存成功，但未返回视图ID")
                    return {
                        "status": "error",
                        "message": "视图保存成功，但未返回视图ID"
                    }
            else:
                error_msg = f"视图保存失败: {result_data.get('code', {}).get('msg', '未知错误')}"
                logger.error("错误详情: %s", truncate_for_log(json.dumps(sanitize_for_log(result_data), ensure_ascii=False), 500))
                return {
                    "status": "error",
                    "message": error_msg
                }
        else:
            error_msg = f"视图保存失败，状态码: {response.status_code}"
            try:
                error_detail = response.json()
                error_msg += f", 响应: {json.dumps(error_detail, ensure_ascii=False)}"
                logger.error("错误详情: %s", truncate_for_log(json.dumps(sanitize_for_log(error_detail), ensure_ascii=False), 500))
            except:
                error_msg += f", 响应内容: {response.text}"
                logger.error("错误响应: %s", truncate_for_log(response.text or "", 500))
            
            return {
                "status": "error",
                "message": error_msg,
                "status_code": response.status_code
            }
    except requests.exceptions.RequestException as e:
        error_msg = f"视图保存失败: {str(e)}"
        logger.error(error_msg)
        return {
            "status": "error",
            "message": error_msg
        }
    except Exception as e:
        error_msg = f"视图保存失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {
            "status": "error",
            "message": error_msg
        }


def add_default_logical_operator(query_condition):
    if not query_condition or not isinstance(query_condition, dict):
        return query_condition
    clauses = query_condition.get("sourceClauses", [])
    if not isinstance(clauses, list):
        return query_condition
    for i, c in enumerate(clauses):
        if isinstance(c, dict) and (c.get("logicalOperator") is None or c.get("logicalOperator") == "") and i > 0:
            c["logicalOperator"] = "AND"
    return query_condition


def _extract_work_item_type_keys_from_query_condition(query_condition):
    """
    从 queryCondition.sourceClauses 中提取工作项类型条件为 workItemTypeKeys，
    并从 sourceClauses 中移除这些条件（仅用于与我相关查询）。
    """
    if not query_condition or not isinstance(query_condition, dict):
        return query_condition, []
    clauses = query_condition.get("sourceClauses")
    if not isinstance(clauses, list):
        return query_condition, []
    type_fields = {
        "System_WorkItemType",
        "System_WorkItemTypeKey",
        "System_WorkItemType.name",
        "System_WorkItemType.nameZh",
    }
    new_clauses = []
    type_keys = []
    for c in clauses:
        if not isinstance(c, dict):
            new_clauses.append(c)
            continue
        f = (c.get("field") or "").strip()
        v = c.get("value")
        if f not in type_fields or not v:
            new_clauses.append(c)
            continue
        # 提取 value（此时已通过 convert_work_item_type_values 转成类型 key）
        if isinstance(v, list):
            for x in v:
                if x:
                    type_keys.append(str(x).strip())
        else:
            type_keys.append(str(v).strip())
        # 不再把该条件放回 new_clauses，相当于从 where 中移除类型条件
    if not type_keys:
        return query_condition, []
    # 去重并保持顺序
    type_keys = list(dict.fromkeys(type_keys))
    qc = dict(query_condition)
    qc["sourceClauses"] = new_clauses
    # 修正第一个条件的 logicalOperator（如果存在的话）
    for i, c in enumerate(qc["sourceClauses"]):
        if isinstance(c, dict):
            c["logicalOperator"] = "" if i == 0 else (c.get("logicalOperator") or "")
    return qc, type_keys

def _parse_bool(s):
    if s is None:
        return False
    return str(s).strip().lower() in ("true", "1", "yes")


def _build_parser():
    p = argparse.ArgumentParser(
        prog="query_my_work_item.py",
        description="查询与我相关的工作项（指派给我、我创建的、我编辑的、我关注的等）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  基础（指派给我，默认 query-type）:
    %(prog)s --select-items "标题,状态" --query-type assigned_to_me
  单条件:
    %(prog)s --select-items "标识,标题,状态" --conditions "状态=进行中" --query-type assigned_to_me
  多条件 AND:
    %(prog)s --select-items "标题,状态" --conditions "状态=进行中 AND 标题 like 测试" --query-type created_by_me
  多条件 OR:
    %(prog)s --select-items "标题,状态" --conditions "状态=进行中 OR 状态=待处理" --query-type assigned_to_me
  括号分组 (OR 再 AND):
    %(prog)s --select-items "标题,状态,优先级" --conditions "(状态=进行中 OR 状态=待处理) AND 优先级=P0" --query-type assigned_to_me
  括号嵌套 (AND 再 OR):
    %(prog)s --select-items "标题,状态" --conditions "(状态=进行中 AND 指派给=@me) OR (状态=待处理 AND 优先级=P0)" --query-type assigned_to_me
  排序与条数:
    %(prog)s --select-items "标题,状态,更新时间" --conditions "状态=进行中" --query-type assigned_to_me --order-by "更新时间 desc" --limit 10
  发消息给 @me:
    %(prog)s --select-items "标识,标题,状态" --conditions "状态=进行中" --query-type created_by_me --is-send-icenter true --icenter-to @me
""" % {"prog": "query_my_work_item.py"},
    )
    p.add_argument("--select-items", "--selectItems", dest="select_items", default="", metavar="ITEMS", help="查询列，英文逗号分隔的字段名称，如 标题,状态,标识；不传则使用默认列。")
    p.add_argument("--conditions", "-c", dest="conditions", default="", metavar="CONDITIONS", help="条件组合：支持 AND/OR、括号 () 组合与嵌套，如 状态=进行中、(状态=进行中 OR 状态=待处理) AND 优先级=P0、(状态=进行中 AND 指派给=@me) OR (状态=待处理 AND 优先级=P0)；不传则无过滤。")
    p.add_argument("--order-by", "--orderBy", dest="order_by", default="", metavar="ORDER", help="排序，如 更新时间 desc、标题 asc, 状态 desc；不传则默认按更新时间倒序。")
    p.add_argument("--limit", "-n", dest="limit", default=None, metavar="N", type=int, help="返回条数上限，正整数，最大 10000；不传则默认 20。")
    p.add_argument("--query-type", "--queryType", "-q", dest="query_type", default="assigned_to_me", metavar="TYPE", choices=["assigned_to_me", "my_todo", "created_by_me", "edited_by_me", "followed_by_me"], help="与我相关类型：assigned_to_me=指派给我（默认）, my_todo=我的待办, created_by_me=我创建的, edited_by_me=我编辑的, followed_by_me=我关注的。")
    p.add_argument("--is-send-email", "--is_send_email", dest="is_send_email", default="false", metavar="BOOL", help="是否发送邮件：true/false，默认 false。")
    p.add_argument("--is-send-icenter", "--is_send_icenter", dest="is_send_icenter", default="false", metavar="BOOL", help="是否发送 iCenter 消息：true/false，默认 false。")
    p.add_argument("--mail-to", "--mailTo", dest="mail_to", default=None, metavar="RECIPIENTS", help="邮件接收人，多人英文逗号分隔。")
    p.add_argument("--icenter-to", "--icenter_to", dest="icenter_to", default=None, metavar="RECIPIENTS", help="iCenter 消息接收人，多人英文逗号分隔。")
    p.add_argument("--user-input", "--userInput", dest="user_input", default="", metavar="TEXT", help="用户的原始输入，必须是用户真实完整的一句话。")
    return p


def _args_to_input_data(args):
    data = {
        "select_items": (args.select_items or "").strip(),
        "conditions": (args.conditions or "").strip(),
        "order_by": (args.order_by or "").strip() if getattr(args, "order_by", None) else "",
        "limit": getattr(args, "limit", None),
        "queryType": args.query_type,
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



def extract_workspace_key(work_item_id):
    if not work_item_id:
        return ""
    i = work_item_id.rfind('-')
    return work_item_id[:i] if i != -1 else work_item_id


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
        return {"status": "error", "message": (result.get("message") if isinstance(result, dict) else "未找到用户") or "未找到用户"}
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
    url = URL_SEND_MAIL
    data = {"mailFrom": mail_from or "rdc@zte.com.cn", "mailLan": mail_lan, "mailTitle": mail_title, "mailTo": processed_to, "mailBody": mail_body}
    req_headers = {"Content-Type": "application/json", "X-Auth-Value": auth_token, "X-Emp-No": emp_no, "X-Tenant-Id": "ZTE"}
    try:
        r = requests.post(url, headers=req_headers, data=json.dumps(data), timeout=60)
        return {"status": "success"} if r.status_code == 200 else {"status": "error", "message": f"邮件发送失败，状态码: {r.status_code}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _search_group_chat_for_send(group_name, auth_token, emp_no):
    if not auth_token or not emp_no:
        return {"status": "error", "message": "缺少认证信息"}
    url = f"{URL_GROUP_CHAT_SEARCH}?keyword={urllib.parse.quote(group_name)}&searchFor=groupChat"
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
    url = URL_ICHAT_SHARE
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


def _send_email_after_query(input_data, output_text, base_config, headers, view_url=None):
    """查询成功后按 is_send_email 发送邮件（内联逻辑）。"""
    try:
        body = input_data.get("mail_body") or input_data.get("mailBody") or output_text or "查询完成"
        if len(body) > 50000:
            body = body[:50000] + "\n...(内容已截断)"
        auth = input_data.get("auth") or {}
        auth_token = auth.get("auth_token") or headers.get("X-Auth-Value")
        emp_no = auth.get("emp_no") or headers.get("X-Emp-No", "")
        mail_title = input_data.get("mail_title") or input_data.get("mailTitle") or "与我相关的工作项查询结果"
        mail_to = input_data.get("mail_to") or input_data.get("mailTo") or emp_no
        logger.info("发送邮件: 场景=与我相关查询, 标题=%s, 收件人=%s, 正文长度=%d 字符", truncate_for_log(mail_title, 80), truncate_for_log(str(mail_to), 50), len(body))
        result_send = _send_mail_inline(
            mail_title=mail_title,
            mail_to=mail_to,
            mail_body=body,
            mail_from=input_data.get("mailFrom", "rdc@zte.com.cn"),
            mail_lan=input_data.get("mailLan", "zh_CN"),
            auth_token=auth_token,
            emp_no=emp_no,
        )
        if result_send.get("status") == "error":
            logger.warning("发送邮件未成功: 标题=%s, 错误=%s", truncate_for_log(mail_title, 60), result_send.get("message", ""))
        else:
            logger.info("发送邮件完成: 场景=与我相关查询, 标题=%s, 收件人=%s", truncate_for_log(mail_title, 60), truncate_for_log(str(mail_to), 50))
    except Exception as e:
        logger.warning("发送邮件异常（不影响查询结果）: %s", e)


def _send_icenter_after_query(input_data, output_text, base_config, headers, view_url=None):
    """查询成功后按 is_send_icenter 发送 iCenter 消息（内联逻辑）。"""
    try:
        message = input_data.get("message") or input_data.get("mailBody") or output_text or "查询完成"
        if len(message) > 10000:
            message = message[:10000] + "\n...(内容已截断)"
        default_my_related_url = URL_MY_RELATED_DEFAULT
        auth = input_data.get("auth") or {}
        auth_token = auth.get("auth_token") or headers.get("X-Auth-Value")
        emp_no = auth.get("emp_no") or headers.get("X-Emp-No", "")
        to = input_data.get("icenter_to") or emp_no
        if isinstance(to, str) and "," in to:
            to = [x.strip() for x in to.split(",") if x.strip()]
        subject = input_data.get("subject") or input_data.get("mail_title") or input_data.get("mailTitle") or "与我相关的工作项查询结果"
        view_url_val = view_url or input_data.get("viewUrl") or input_data.get("view_url") or default_my_related_url
        logger.info("发送 iCenter 消息: 场景=与我相关查询, 主题=%s, 接收人=%s, view_url=%s, 正文长度=%d 字符", truncate_for_log(subject, 80), truncate_for_log(str(to), 50), "有" if view_url_val else "无", len(message))
        result_send = _send_chat_message_inline(
            message_content=message,
            subject=subject,
            to=to,
            view_url=view_url_val,
            auth_token=auth_token,
            emp_no=emp_no,
        )
        if result_send.get("status") == "error":
            logger.warning("发送 iCenter 消息未成功: 主题=%s, 错误=%s", truncate_for_log(subject, 60), result_send.get("message", ""))
        else:
            logger.info("发送 iCenter 消息完成: 场景=与我相关查询, 主题=%s, 接收人=%s", truncate_for_log(subject, 60), truncate_for_log(str(to), 50))
    except Exception as e:
        logger.warning("发送 iCenter 消息异常（不影响查询结果）: %s", e)


def query_work_items(query_data, base_config, headers, workspace=None):
    workspace1 = "SYSMySpace";
    url = URL_QUERY_WORK_ITEMS.format(workspace=workspace1)
    _log_cfg = get_log_config(CONFIG_FILE)
    _max = _log_cfg.get("response_body_max_length", 500)
    _req_str = json.dumps({"url": url, "body": query_data}, ensure_ascii=False, indent=2)
    logger.info("query_work_items 请求: %s", _req_str)

    r = requests.post(url, headers=headers, json=query_data, timeout=30)
    r.raise_for_status()
    out = r.json()
    _rsp_str = json.dumps(out, ensure_ascii=False, indent=2)
    logger.info("query_work_items 响应: %s", truncate_for_log(_rsp_str, _max))
    _raise_on_business_error(out, action="与我相关查询")

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


def main():
    apply_log_config(CONFIG_FILE)
    log_raw_argv(logger, sys.argv)
    parser = _build_parser()
    args = parser.parse_args()
    query_type = args.query_type
    input_data = _args_to_input_data(args)
    log_complete_params(logger, "参数解析完成", input_data)
    try:
        base_config = load_base_config()
        headers = get_auth_headers(input_data, base_config, logger=logger)
        # 工作区优先从语义/最近访问解析，配置仅作兜底
        from common.workspace import resolve_workspace_with_source
        raw_user_input = (input_data.get("userInput") or "").strip() or None
        api_workspace, workspace_source, resolve_err = resolve_workspace_with_source(
            None, base_config.get("workspace", ""), headers, raw_user_input=raw_user_input
        )
        if resolve_err or not api_workspace:
            api_workspace = base_config.get("workspace", "") or ""
        api_workspace_single = api_workspace[0] if isinstance(api_workspace, list) else api_workspace
        if not api_workspace_single:
            raise ValueError("未解析到工作区，且配置中无 api.workspace。请指明工作区或配置 api.workspace。")
        emp_no = headers.get("X-Emp-No", "")
        if not emp_no:
            raise ValueError(value_error_missing_emp_no_from_env())
        if str(SCRIPTS_ROOT) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_ROOT))
        select_items_str = input_data.get("select_items") or input_data.get("selectItems") or ""
        conditions_str = input_data.get("conditions") or ""
        order_by_str = input_data.get("order_by") or input_data.get("orderBy") or ""
        limit = input_data.get("limit")
        query_data = build_query_data_from_select_and_conditions(
            select_items_str or None,
            conditions_str or None,
            api_workspace_single,
            headers,
            for_my_workspace=True,
            order_by_str=order_by_str or None,
            limit=limit,
            raw_user_input=(input_data.get("userInput") or "").strip() or None,
        )
        query_data.pop("_multi_match_fields", None)
        query_data["crossWorkspace"] = True
        query_data["myWorkspaceConditions"] = get_my_workspace_conditions(query_type, emp_no)
        # 解析大模型识别的工作项类型名并将其转换为工作项类型key；保留原始用语供心智模型
        _qc, _wit_orig = convert_work_item_type_values(
            query_data["queryCondition"], api_workspace_single, headers
        )
        query_data["queryCondition"] = _qc
        # 将工作项类型条件从 where 中提取到最外层 workItemTypeKeys
        qc_after_type, type_keys = _extract_work_item_type_keys_from_query_condition(
            query_data.get("queryCondition") or {}
        )
        query_data["queryCondition"] = qc_after_type
        if type_keys:
            existing = query_data.get("workItemTypeKeys") or []
            # 兼容 sql_query_parser 中 from 表已经设置的 workItemTypeKeys，做一次合并去重
            merged = list(dict.fromkeys(list(existing) + list(type_keys)))
            query_data["workItemTypeKeys"] = merged
        # 如果发现非第一个查询条件中的logicalOperator没有值，则补充logicalOperator=AND
        query_data["queryCondition"] = add_default_logical_operator(query_data["queryCondition"])
        query_data["queryCondition"] = add_time_range_condition(query_data["queryCondition"])
        # 如果大模型语义中没有解析到状态相关条件，则添加默认状态（不显示已关闭的）
        query_data["queryCondition"] = add_exclude_completed_condition(query_data["queryCondition"])
        query_data["onlyResult"] = True
        # 工作区来自 AI 时：合并进已有工作区条件或补充 System_WorkspaceKey（与通用查询、导出、统计一致）
        try:
            apply_workspace_clause_to_query_condition(
                query_data.setdefault("queryCondition", {}),
                api_workspace,
                workspace_source,
            )
        except Exception as e:
            logger.warning("自动合并/补充工作区条件时发生异常（忽略，不影响查询）: %s", e)
        result = query_work_items(query_data, base_config, headers, workspace="SYSMySpace")
        data = result.get("data")
        bo_res = (result.get("bo") or {}).get("result") if isinstance(result.get("bo"), dict) else {}
        if isinstance(data, dict):
            total = data.get("total") if data.get("total") is not None else len(data.get("items", []))
        elif isinstance(bo_res, dict):
            pd = bo_res.get("pageDetail")
            total = pd.get("totalRow") if isinstance(pd, dict) else bo_res.get("totalRow") or len(bo_res.get("items", []))
        else:
            total = 0
        logger.info("查询完成，共 %s 条", total)
        if base_config.get("format_as_markdown", True):
            try:
                out = format_result_as_markdown(
                    result,
                    query_data,
                    base_config=base_config,
                    workspace=api_workspace_single,
                    headers=headers,
                )
            except TypeError:
                # 兼容旧版 query_work_item.format_result_as_markdown：
                # - 仅支持 (result, query_data)，无 base_config/workspace/headers
                # - 仅支持 (result)
                try:
                    out = format_result_as_markdown(result, query_data)
                except TypeError:
                    out = format_result_as_markdown(result)
        else:
            out = json.dumps(result, ensure_ascii=False, indent=2)
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
        mindset_text = get_mindset_message("my_related_query_result", total=total)
        extra_mindset = build_query_context_mindset_extras(
            query_data=query_data,
            raw_user_input=(input_data.get("userInput") or "").strip() or None,
            conditions_str=conditions_str,
            work_item_type_original_names=_wit_orig,
        )
        if extra_mindset:
            mindset_text = (
                (mindset_text + "\n\n" + extra_mindset)
                if mindset_text
                else extra_mindset
            )
        parsed_summary = format_parsed_summary(
            {
                "query_type": query_type,
                "workspace": api_workspace,
                "select_items": select_items_str,
                "conditions": conditions_str,
                "order_by": order_by_str,
                "limit": limit,
            },
            title="本次语义解析结果",
        )
        out_with_hint = out + notify_skip_hint if notify_skip_hint else out
        body_with_mindset = (
            (out_with_hint + "\n\n" + mindset_text) if mindset_text else out_with_hint
        )
        out_for_display = (parsed_summary + "\n" + body_with_mindset) if parsed_summary else body_with_mindset
        if requested_send_email and not skip_notify_on_empty:
            _send_email_after_query(input_data, out, base_config, headers, view_url=None)
        if requested_send_icenter and not skip_notify_on_empty:
            _send_icenter_after_query(input_data, out, base_config, headers, view_url=None)
        #logger.info(out_for_display)
        safe_print(out_for_display)
    except ValueError as e:
        logger.warning("执行终止: %s", e)
        err_out = f"错误: {e}"
        mindset_text = get_mindset_message("query_error")
        if mindset_text and mindset_text not in err_out:
            err_out = err_out + "\n\n" + mindset_text
        safe_print(err_out, file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        logger.exception("执行失败")
        err_out = f"错误: {e}"
        mindset_text = get_mindset_message("query_error")
        if mindset_text and mindset_text not in err_out:
            err_out = err_out + "\n\n" + mindset_text
        safe_print(err_out, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
