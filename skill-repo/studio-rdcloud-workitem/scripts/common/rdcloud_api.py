#!/usr/bin/env python
"""
研发云工作项相关接口统一管理。
业务请求的 URL 常量、页面链接模板与封装函数集中在本文件；atomic 脚本应通过本模块引用，
避免在脚本内硬编码 https:// 地址（用户输入中的示例 URL、单元测试数据除外）。
"""

import ast
import json
from pydoc import doc
import re
import requests
from typing import Any, Dict, List, Optional

from .log_helper import get_logger, truncate_for_log, sanitize_for_log

logger = get_logger(__name__)

# 响应体日志保留长度（前后各保留该字符数，中间省略）
_RESPONSE_LOG_MAX_CHARS = 200
_REQUEST_BODY_LOG_MAX_CHARS = 500

# put_work_item_event：仅当 body.fields 含有以下 key 之一时才打请求/响应日志（减少常规创建时的日志量）
PUT_EVENT_LOG_WHEN_KEYS = frozenset({
    "BelongProject",
})

# ---------------------------------------------------------------------------
# 接口基地址（统一维护，脚本仅通过本模块调用）
# ---------------------------------------------------------------------------

BASE_WIC_REST = "https://rdcloud.zte.com.cn/zte-plm-wic-rest"
BASE_WIC_API = "https://rdcloud.zte.com.cn/zte-plm-wic-api"
BASE_WIC_MANAGE = "https://rdcloud.zte.com.cn/zte-plm-wic-manage/api"
BASE_RDC_SERVER = "https://rdcloud.zte.com.cn/zte-rdcloud-rdc-rdcserver"
BASE_WIM_BACKEND = "https://rdcloud.zte.com.cn/zte-rdcloud-rdc-wimbackend"

# 工作区
URL_QUERY_WORKSPACE_ALL = f"{BASE_RDC_SERVER}/rdc/workspace/service/v2/queryWorkSpaceAll"
URL_QUERY_WORKSPACE_ACCESS_LOG = f"{BASE_RDC_SERVER}/rdc/workspace/service/queryWorkSpaceAccessLog"

# 工作项：查询、创建、更新
URL_QUERY_WORK_ITEMS = f"{BASE_WIC_API}/api/workspaces/{{workspace}}/queries/query_work_items"
# 工作项导出（流式，请求体与 query_work_items 一致）
URL_EXPORT_WORK_ITEMS = f"{BASE_WIC_API}/api/workspaces/{{workspace}}/export/excel/v2"
# 24 位 viewId 时先 GET 视图配置，响应 bo 去掉 allWorkItemTypeKey 后作为 query_work_items 的请求体
URL_VIEW_QUERIES = f"{BASE_WIC_API}/api/workspaces/{{workspace}}/view_queries/{{viewId}}"
# 按工作项标识列表拉取详情（请求体为 ID 数组，返回 bo.items，每项含 fields）
URL_QUERY_WORK_ITEMS_DETAIL = f"{BASE_WIC_REST}/api/rest/v1/work_items/query_workitems"
URL_VIEWS = f"{BASE_WIC_REST}/api/rest/workspaces/{{workspace}}/views"
URL_WORK_ITEM_UPDATE = f"{BASE_WIC_API}/api/workspaces/{{workspace}}/AiFunction/update/skill/{{work_item_id}}"
# 创建工作项：走 AiFunction 技能接口
URL_WORK_ITEM_CREATE = f"{BASE_WIC_API}/api/workspaces/{{workspace}}/AiFunction/skill/createWorkItem"
# 复制工作项（走 AiFunction 封装接口）
URL_WORK_ITEM_CHECK_BATCH_COPY = (
    f"{BASE_WIC_API}/api/workspaces/{{workspace}}/AiFunction/skill/check-batchCopy"
)
URL_WORK_ITEM_BATCH_COPY = (
    f"{BASE_WIC_API}/api/workspaces/{{workspace}}/AiFunction/skill/batchCopy"
)
# 新建初始化（获取 eventId）
URL_WORK_ITEM_INIT_DETAIL = f"{BASE_WIC_API}/api/workspaces/{{workspace}}/work_items/init/getInitDetail"
# 更新初始化（获取 eventId、availableStates、fields）
URL_WORK_ITEM_UPDATE_INIT_DETAIL = f"{BASE_WIC_API}/api/workspaces/{{workspace}}/work_items/{{work_item_id}}/getInitWorkItemDetail"
# 工作项 event 更新（PUT 提交字段）
URL_WORK_ITEM_EVENT = f"{BASE_WIC_API}/api/workspaces/{{workspace}}/AiFunction/skill/events/{{event_id}}"
# 销毁 event 缓存
URL_CACHE_EVENT_DELETE = f"{BASE_WIC_API}/api/workspaces/{{workspace}}/cache/event/{{event_id}}"
# 按创建人查询最新工作项历史（智能填充用）

# Transition（流转步骤）查询
URL_TRANSITIONS = f"{BASE_WIC_REST}/api/rest/workspaces/{{workspace}}/work_item_types/{{workItemTypeKey}}/transitions"
# 状态定义查询（nameZh → key 映射）
# GET /workspaces/{workspaceKey}/work_item_types/{workItemTypeKey}/states → PageableResult<StateDef>
# StateManageController: queryStateDefByWorkItemTypeKey
URL_STATES = f"{BASE_WIC_MANAGE}/workspaces/{{workspace}}/work_item_types/{{workItemTypeKey}}/states"
URL_LATEST_LOG_BY_CREATOR = f"{BASE_WIC_REST}/api/rest/workspaces/{{workspace}}/work_items/latestLogByCreator"
# 工作项关联：批量创建关联关系（请求体为数组，无 workspace 占位）
URL_WORK_ITEM_RELATIONS_BATCH_CREATE = f"{BASE_WIC_REST}/api/rest/v1/work_items/relations/batch_create"
# 根据查询链接中的 queryId（URL 中 ? 前最后一个 / 后的片段）拉取该查询结果
URL_QUERY_WORK_ITEM_BY_QUERY_ID = f"{BASE_WIM_BACKEND}/rdc/query/queryWorkItem/{{queryId}}"
# 根据查询名/视图名查找视图：POST 请求体为关键词数组如 ["WODE"]，响应 bo 为树形列表，取第一个节点的第一个 child 的 id 作为 queryId
URL_GET_QUERY_BY_KEYWORD = f"{BASE_WIM_BACKEND}/rdc/query/getQueryByKeyword"

# 工作项类型、字段
# 工作项类型 key 解析：AiFunction 能力接口，按名称返回「根类型 → {key: 显示名}」映射
URL_WORK_ITEM_TYPE_KEYS = f"{BASE_WIC_API}/api/workspaces/{{workspace}}/AiFunction/skill/workItemType/keys"
URL_WORK_ITEM_TYPES = f"{BASE_WIC_REST}/api/rest/workspaces/{{workspace}}/work_item_types"
URL_FIELDS = f"{BASE_WIC_MANAGE}/workspaces/{{workspace}}/fields/withUsageDistinct"
# 备选：跨工作区字段列表，请求体含 isCrossWorkspace；响应 bo.items 与 fetch_fields 一致
URL_FIELDS_V2_WITH_USAGE_DISTINCT = (
    f"{BASE_WIC_MANAGE}/workspaces/{{workspace}}/fields/v2/withUsageDistinct"
)
# 字段候选（如 advancedData 远程模糊查询）
URL_FIELDS_CANDIDATE = f"{BASE_WIC_API}/api/workspaces/{{workspace}}/fields/candidate"
# 远程数据源实时查询（advancedData sourceType=remoteRealTime）
URL_REMOTE_DATA_SOURCE_REALTIME = f"{BASE_WIC_API}/api/workspaces/{{workspace}}/remoteDataSource/realTime"
# 工作项类型详情（含该类型下字段完整配置，bo.fields 为 key→字段配置）
URL_WORK_ITEM_TYPE_DETAIL = f"{BASE_WIC_MANAGE}/workspaces/{{workspace}}/work_item_types/{{work_item_type_key}}/workItemTypeDetail"
URL_WORK_ITEM_ROOT_TYPES = f"{BASE_WIC_REST}/api/rest/workspaces/{{workspace}}/work_item_root_types"

# 工作项评论（GET 查询树形列表 / POST 添加或回复）；business_type 通常为 wic
URL_WORK_ITEM_COMMENTS = f"{BASE_RDC_SERVER}/comments/api/{{business_type}}/{{business_id}}"

# 邮件、iCenter、用户搜索
URL_SEND_MAIL = f"{BASE_RDC_SERVER}/rdc/mail/sendMail"
URL_SEND_ICHAT = f"{BASE_RDC_SERVER}/rdc/chat/sendChatMessage"
URL_USER_SEARCH = f"{BASE_WIC_API}/api/users/{{workspace}}/search"
# 群聊搜索（PLM 用户/群组搜索，searchFor=groupChat）
URL_GROUP_CHAT_SEARCH = f"{BASE_WIC_API}/api/users/PLM/search"
# iCenter 分享消息（icenterapi 域名）
URL_ICHAT_SHARE = "https://icenterapi.zte.com.cn/zte-km-icenter-console/chat/shareMessage"
# 与我相关默认跳转页
URL_MY_RELATED_DEFAULT = "https://rdcloud.zte.com.cn/uportal/rd-uportal-my-related/my/related"

# 研发云门户基址（前端页面链接）
BASE_RDCLOUD_PORTAL = "https://rdcloud.zte.com.cn/rdcloud"

# Studio 前端：工作项打开链接（Markdown 表格、邮件、iCenter view 等）
BASE_STUDIO = "https://studio.zte.com.cn/rdcloud"

# iCenter：Markdown 转 HTML（通知正文）
URL_MARKDOWN2HTML = (
    "https://icenterapi.zte.com.cn/zte-rdcloud-space-wconvert/markdown2html"
    "?environmentType=browser"
)

# iCenter：人员关键字搜索（user_search 模块）
URL_ICENTER_USER_PLAIN_SEARCH = (
    "https://icenterapi.zte.com.cn/zte-km-icenter-addresearch/user/plain/docs/search"
)

# AiFunction 接口（给 AI-Skill 使用，上线后切换）
# URL_RULES = f"{BASE_WIC_API}/api/workspaces/{{workspace}}/AiFunction/skill/rules?workItemTypeKey={{work_item_type_key}}"
# URL_RULE_LOGS = f"{BASE_WIC_API}/api/workspaces/{{workspace}}/AiFunction/skill/rule_logs"
# URL_AFTER_TRIGGER_LOG = f"{BASE_WIC_API}/api/workspaces/{{workspace}}/AiFunction/skill/after_trigger_log/{{eventId}}"
# URL_TRANSITIONS = f"{BASE_WIC_API}/api/workspaces/{{workspace}}/AiFunction/skill/transitions?workItemTypeKey={{workItemTypeKey}}"
# URL_STATES = f"{BASE_WIC_API}/api/workspaces/{{workspace}}/AiFunction/skill/states?workItemTypeKey={{workItemTypeKey}}"

# 临时接口（AiFunction 未上线前使用）
URL_RULES = f"{BASE_WIC_REST}/api/rest/workspaces/{{workspace}}/work_item_types/{{work_item_type_key}}/rules"
URL_RULE_LOGS = f"{BASE_WIC_API}/api/workspaces/{{workspace}}/rule_logs"
URL_AFTER_TRIGGER_LOG = f"{BASE_WIC_REST}/api/rest/afterTriggerLog/{{eventId}}"

# uportal：WIM 工作项详情页模板（超期列表等跳转）
URL_UPORTAL_WIM_WORKITEM = (
    "https://rdcloud.zte.com.cn/uportal/rd-uportal/workspaces/{workspace}"
    "/apps/wim/allWorkItems/{work_item_id}"
)


def studio_workitem_transmit_url(work_item_id: str) -> str:
    """工作项在 Studio 中打开的 transmit URL；标识为空则返回空串。"""
    wid = (work_item_id or "").strip()
    return f"{BASE_STUDIO}/workitem/pc/transmit/{wid}" if wid else ""


def uportal_wim_workitem_url(workspace: str, work_item_id: str) -> str:
    """uportal 下某工作区 WIM 工作项页；工作区或标识为空则返回空串。"""
    ws = (workspace or "").strip()
    wid = (work_item_id or "").strip()
    if not ws or not wid:
        return ""
    return URL_UPORTAL_WIM_WORKITEM.format(workspace=ws, work_item_id=wid)


# 根据查询链接查询：支持的 URL 模式（用于意图识别与脚本入参校验）
# 模式 1：iChange 工作区查询
#   1.1 .../workspaces/{workspaceKey}/apps/ichange/workspacequery/{queryId} — queryId 为纯数字。
#   1.2 .../workspaces/{workspaceKey}/apps/ichange/workspacequery?wicViewId={queryId} — 24 位十六进制 wicViewId。
# 模式 2：WIM 视图查询
#   2.1 .../workspaces/{workspaceKey}/apps/wim/queries?wicViewId={viewId}[&...] — viewId 为 24 位十六进制。
#   2.2 .../workspaces/{workspaceKey}/apps/wim/queries/{queryId}?...[&...] — queryId 为纯数字。
# 模式 3：subject 视图（ireq/iecApp）
#   3.1 https://rdcloud.zte.com.cn/uportal/ireq/subject/?viewId={viewId}&...
#   3.2 https://rdcloud.zte.com.cn/uportal/iecApp/subject/?viewId={viewId}&...
#   其中 viewId 为 24 位 ID。
# iChange：workspacequery/{queryId} — 纯数字 queryId
QUERY_URL_PATTERN_ICHANGE_PATH = (
    r"https?://rdcloud\.zte\.com\.cn/uportal/rd-uportal/workspaces/([^/]+)/apps/ichange/workspacequery/(\d+)"
)
# iChange：workspacequery?wicViewId= — 24 位 wicViewId
QUERY_URL_PATTERN_ICHANGE_WICVIEW = (
    r"https?://rdcloud\.zte\.com\.cn/uportal/rd-uportal/workspaces/([^/]+)/apps/ichange/workspacequery\?wicViewId=([0-9a-fA-F]{24})"
)
# WIM：queries?wicViewId= — 24 位 wicViewId
QUERY_URL_PATTERN_WIM = (
    r"https?://rdcloud\.zte\.com\.cn/uportal/rd-uportal/workspaces/([^/]+)/apps/wim/queries\?wicViewId=([0-9a-fA-F]{24})"
)
# WIM：queries/{queryId} — 纯数字 queryId
QUERY_URL_PATTERN_WIM_ID_PATH = (
    r"https?://rdcloud\.zte\.com\.cn/uportal/rd-uportal/workspaces/([^/]+)/apps/wim/queries/(\d+)"
)
QUERY_URL_PATTERN_SUBJECT_IREQ = (
    r"https?://rdcloud\.zte\.com\.cn/uportal/ireq/subject/\?viewId=([0-9a-fA-F]{24})"
)
QUERY_URL_PATTERN_SUBJECT_IECAPP = (
    r"https?://rdcloud\.zte\.com\.cn/uportal/iecApp/subject/\?viewId=([0-9a-fA-F]{24})"
)


def parse_query_url(url: str) -> Optional[Dict[str, Any]]:
    """
    解析用户提供的查询链接，判断是否为本技能支持的「根据 URL 查询」模式。
    :param url: 用户输入中的 URL 字符串（可含前后空白）。
    :return: 若匹配 iChange 或 WIM 模式，返回 {"workspace": str, "type": "ichange"|"wim", "query_id": str} 或
             {"workspace": str, "type": "wim", "view_id": str}；否则返回 None。
    """
    if not url or not isinstance(url, str):
        return None
    s = url.strip()
    if not s:
        return None
    # iChange: 路径型 workspacequery/{queryId}
    mo = re.search(QUERY_URL_PATTERN_ICHANGE_PATH, s)
    if mo:
        return {"workspace": mo.group(1), "type": "ichange", "query_id": mo.group(2)}
    # iChange: 查询参数型 workspacequery?wicViewId={queryId}
    mo = re.search(QUERY_URL_PATTERN_ICHANGE_WICVIEW, s)
    if mo:
        return {"workspace": mo.group(1), "type": "ichange", "query_id": mo.group(2)}
    # WIM：queries?wicViewId={viewId}[&...]
    mo = re.search(QUERY_URL_PATTERN_WIM, s)
    if mo:
        return {"workspace": mo.group(1), "type": "wim", "view_id": mo.group(2)}
    # WIM：queries/{viewId}?...
    mo = re.search(QUERY_URL_PATTERN_WIM_ID_PATH, s)
    if mo:
        return {"workspace": mo.group(1), "type": "wim", "view_id": mo.group(2)}
    # subject 视图：ireq/subject/?viewId=...
    mo = re.search(QUERY_URL_PATTERN_SUBJECT_IREQ, s)
    if mo:
        return {"workspace": "", "type": "wim", "view_id": mo.group(1)}
    # subject 视图：iecApp/subject/?viewId=...
    mo = re.search(QUERY_URL_PATTERN_SUBJECT_IECAPP, s)
    if mo:
        return {"workspace": "", "type": "wim", "view_id": mo.group(1)}
    return None


def is_supported_query_url(url: str) -> bool:
    """判断给定字符串是否为本技能支持的查询链接（用于意图识别）。"""
    return parse_query_url(url) is not None


# 工作区语义解析 AI 接口（候选列表 + 用户输入 → 解析出工作区 key）
WORKSPACE_RESOLVE_AI_URL = "https://studio.zte.com.cn/zte-plm-wic-api/api/wic/chat/{workspace}/resolve"

# Studio Chat 兜底：字段名 0 条/多条匹配时请求该接口得到规范字段名
STUDIO_CHAT_API_URL = "https://studio.zte.com.cn/zte-plm-wic-api/api/wic/chat/{workspace}/resolve"

# 默认超时（秒）
DEFAULT_TIMEOUT = 30


def _headers_for_request(headers: Dict[str, Any]) -> Dict[str, str]:
    """从技能认证 headers 中提取请求头，保证包含 Content-Type。"""
    h = {
        "Content-Type": "application/json",
        "X-Tenant-Id": headers.get("X-Tenant-Id", "ZTE"),
        "X-Auth-Value": headers.get("X-Auth-Value", ""),
        "X-Emp-No": headers.get("X-Emp-No", ""),
    }
    if headers.get("X-Lang-Id"):
        h["X-Lang-Id"] = headers["X-Lang-Id"]
    return h


def request_get(
    url: str,
    headers: Dict[str, Any],
    params: Optional[Dict] = None,
    timeout: int = DEFAULT_TIMEOUT,
    log_request: bool = False,
    log_response: bool = False,
) -> Dict[str, Any]:
    """
    发起 GET 请求，返回 JSON。
    :param url: 完整 URL
    :param headers: 技能层传入的认证头（会在此内转为请求头）
    :param params: 查询参数（可为空）
    :param timeout: 超时秒数
    :param log_request: 是否打印请求 URL 与查询参数
    :param log_response: 是否打印响应体（脱敏、截断）
    :return: 响应 JSON
    """
    h = _headers_for_request(headers)
    if log_request:
        params_for_log = params if params is not None else {}
        params_safe = sanitize_for_log(params_for_log) if params_for_log else {}
        params_str = json.dumps(params_safe, ensure_ascii=False, sort_keys=True)
        logger.info(
            "GET 请求 url=%s params=%s",
            url,
            truncate_for_log(params_str, _REQUEST_BODY_LOG_MAX_CHARS),
        )
    r = requests.get(url, headers=h, params=params, timeout=timeout)
    r.raise_for_status()
    resp = r.json()
    if log_response:
        rsp_str = (
            json.dumps(sanitize_for_log(resp), ensure_ascii=False, indent=2)
            if isinstance(resp, dict)
            else str(resp)
        )
        logger.info("GET 响应 url=%s body=%s", url, truncate_for_log(rsp_str, _RESPONSE_LOG_MAX_CHARS))
    return resp


def request_post(
    url: str,
    headers: Dict[str, Any],
    json_body: Any = None,
    timeout: int = DEFAULT_TIMEOUT,
    log_request: bool = True,
    log_response: bool = True,
) -> Dict[str, Any]:
    """
    发起 POST 请求，返回 JSON。
    :param url: 完整 URL
    :param headers: 技能层传入的认证头
    :param json_body: 请求体（字典或列表）
    :param timeout: 超时秒数
    :param log_request: 是否打印请求体，默认 True
    :param log_response: 是否打印响应体，默认 True
    :return: 响应 JSON
    """
    h = _headers_for_request(headers)
    if log_request:
        body_for_log = json_body if json_body is None else sanitize_for_log(json_body)
        req_str = json.dumps(body_for_log, ensure_ascii=False, indent=2) if body_for_log is not None else "{}"
        logger.info("POST 请求 url=%s body=%s", url, truncate_for_log(req_str, _REQUEST_BODY_LOG_MAX_CHARS))
    r = requests.post(url, headers=h, json=json_body or {}, timeout=timeout)
    r.raise_for_status()
    resp = r.json()
    if log_response:
        rsp_str = json.dumps(sanitize_for_log(resp), ensure_ascii=False, indent=2) if isinstance(resp, dict) else str(resp)
        logger.info("POST 响应 url=%s body=%s", url, truncate_for_log(rsp_str, _RESPONSE_LOG_MAX_CHARS))
    return resp


def request_put(
    url: str,
    headers: Dict[str, Any],
    json_body: Any = None,
    timeout: int = DEFAULT_TIMEOUT,
    log_request: bool = True,
    log_response: bool = True,
) -> Dict[str, Any]:
    """
    发起 PUT 请求，返回 JSON。
    :param url: 完整 URL
    :param headers: 技能层传入的认证头
    :param json_body: 请求体
    :param timeout: 超时秒数
    :param log_request: 是否打印请求体，默认 True
    :param log_response: 是否打印响应体，默认 True
    :return: 响应 JSON
    """
    h = _headers_for_request(headers)
    if log_request:
        body_for_log = json_body if json_body is None else sanitize_for_log(json_body)
        req_str = json.dumps(body_for_log, ensure_ascii=False, indent=2) if body_for_log is not None else "{}"
        logger.info("PUT 请求 url=%s body=%s", url, truncate_for_log(req_str, _REQUEST_BODY_LOG_MAX_CHARS))
    r = requests.put(url, headers=h, json=json_body or {}, timeout=timeout)
    r.raise_for_status()
    resp = r.json()
    if log_response:
        rsp_str = json.dumps(sanitize_for_log(resp), ensure_ascii=False, indent=2) if isinstance(resp, dict) else str(resp)
        logger.info("PUT 响应 url=%s body=%s", url, truncate_for_log(rsp_str, _RESPONSE_LOG_MAX_CHARS))
    return resp


def request_delete(
    url: str,
    headers: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """
    发起 DELETE 请求，返回 JSON。
    :param url: 完整 URL
    :param headers: 技能层传入的认证头
    :param timeout: 超时秒数
    :return: 响应 JSON
    """
    h = _headers_for_request(headers)
    r = requests.delete(url, headers=h, timeout=timeout)
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        return {"code": {"code": "0000"}, "bo": None}


# ---------------------------------------------------------------------------
# 工作区
# ---------------------------------------------------------------------------


def query_workspaces_by_name(keyword: str, headers: Dict[str, Any]) -> List[dict]:
    """
    按名称或 key 模糊搜索工作区。
    :param keyword: 搜索关键字
    :param headers: 认证头
    :return: workSpaceDetailList，失败或空返回 []
    """
    if not (keyword or "").strip():
        return []
    body = {"strSearchName": keyword.strip(), "pageNo": 1, "pageSize": 30}
    data = request_post(URL_QUERY_WORKSPACE_ALL, headers, body, timeout=30, log_request=True, log_response=True)
    if data.get("code") != "1":
        return []
    bo = data.get("bo") or {}
    lst = bo.get("workSpaceDetailList")
    return lst if isinstance(lst, list) else []


def query_recent_workspaces(headers: Dict[str, Any]) -> List[dict]:
    """
    查询用户最近访问的工作区列表（GET）。
    :param headers: 认证头
    :return: workSpaceAccessLogList，失败或空返回 []
    """
    h = _headers_for_request(headers)
    r = requests.get(URL_QUERY_WORKSPACE_ACCESS_LOG, headers=h, timeout=30)
    r.raise_for_status()
    data = r.json()
    code_obj = data.get("code")
    if isinstance(code_obj, dict) and code_obj.get("code") != "0000":
        return []
    bo = data.get("bo") or {}
    lst = bo.get("workSpaceAccessLogList")
    return lst if isinstance(lst, list) else []


# ---------------------------------------------------------------------------
# 根据查询名/视图名查视图（getQueryByKeyword）
# ---------------------------------------------------------------------------


def _innermost_first_child_id(node: Any) -> Optional[int]:
    """
    从当前节点起，沿第一个 child 递归到最里层（无 children 的节点），返回其 id。
    """
    if not node or not isinstance(node, dict):
        return None
    children = node.get("children") or []
    if not children:
        vid = node.get("id")
        return vid if vid is not None else None
    return _innermost_first_child_id(children[0])


def first_query_id_from_keyword_bo(bo: Any) -> Optional[int]:
    """
    从 getQueryByKeyword 响应的 bo（树形列表）中取最里层 child 的 id。
    从第一个根节点起，沿第一个 child 递归到无 children 的节点，返回该节点的 id。
    """
    if not bo or not isinstance(bo, list):
        return None
    first_root = bo[0] if bo else None
    if not first_root or not isinstance(first_root, dict):
        return None
    return _innermost_first_child_id(first_root)


def get_query_by_keyword(
    workspace: str,
    keywords: List[str],
    headers: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """
    根据查询名/视图名查找视图。POST 请求体为关键词数组，响应 bo 为树形列表。
    :param workspace: 工作区，用于 query 参数 workspaceKey
    :param keywords: 关键词列表，如 ["WODE"]、["我的查询"]
    :param headers: 认证头
    :param timeout: 超时
    :return: 接口原始响应 { "code": {...}, "bo": [...] }
    """
    workspace = _normalize_workspace_single(workspace)
    url = f"{URL_GET_QUERY_BY_KEYWORD}?workspaceKey={workspace}"
    body = list(keywords) if isinstance(keywords, list) else [str(keywords)]
    return request_post(url, headers, body, timeout=timeout, log_request=True, log_response=True)


# ---------------------------------------------------------------------------
# 工作项查询
# ---------------------------------------------------------------------------


def get_view_query_config(
    workspace: str,
    view_id: str,
    headers: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """
    24 位 viewId 时第一步：GET 视图配置。
    响应含 code 与 bo；bo 去掉 allWorkItemTypeKey 后可作为 query_work_items 的请求体。
    :param workspace: 工作区
    :param view_id: 24 位视图 ID
    :param headers: 认证头
    :param timeout: 超时
    :return: 接口原始响应 { "code": {...}, "bo": {...} }
    """
    workspace = _normalize_workspace_single(workspace)
    url = URL_VIEW_QUERIES.format(workspace=workspace, viewId=view_id)
    return request_get(url, headers, timeout=timeout)


def query_work_items(
    workspace: str,
    query_data: Dict[str, Any],
    headers: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """
    按条件查询工作项列表。
    :param workspace: 工作区
    :param query_data: 查询体（queryCondition、selectItems、onlyResult 等）
    :param headers: 认证头
    :param timeout: 超时
    :return: 接口原始响应
    """
    workspace = _normalize_workspace_single(workspace)
    url = URL_QUERY_WORK_ITEMS.format(workspace=workspace)
    return request_post(url, headers, query_data, timeout=timeout, log_request=True, log_response=True)


def export_work_items_stream(
    workspace: str,
    query_data: Dict[str, Any],
    headers: Dict[str, Any],
    flap: str = "false",
    timeout: int = 120,
) -> requests.Response:
    """
    工作项导出（流式接口）。请求体与 query_work_items 一致。
    :param workspace: 工作区
    :param query_data: 查询体（与 query_work_items 相同）
    :param headers: 认证头
    :param flap: false=列表导出，true=层级导出，默认 false
    :param timeout: 超时秒数（导出可能较久，默认 120）
    :return: Response 对象（stream=True），调用方需根据 Content-Type 解析：JSON 或 Excel 流
    """
    workspace = _normalize_workspace_single(workspace)
    url = URL_EXPORT_WORK_ITEMS.format(workspace=workspace)
    params = {"flap": "true" if str(flap).strip().lower() == "true" else "false"}
    h = _headers_for_request(headers)
    query_data_safe = sanitize_for_log(query_data or {})
    logger.info("POST 导出请求 url=%s params=%s query_data=%s", url, params, json.dumps(query_data_safe, ensure_ascii=False, indent=2))
    r = requests.post(url, headers=h, params=params, json=query_data or {}, timeout=timeout, stream=True)
    r.raise_for_status()
    return r


def query_work_item_details(
    work_item_ids: List[str],
    headers: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """
    按工作项标识列表查询详情（REST 详情接口）。
    请求体为 ID 数组，如 ["DT_ITOW-795491"]；响应为 { "bo": { "items": [...] }, "code": {...} }。
    :param work_item_ids: 工作项标识列表
    :param headers: 认证头
    :param timeout: 超时
    :return: 接口原始响应
    """
    ids = [str(i).strip() for i in work_item_ids if (i or "").strip()]
    if not ids:
        return {"bo": {"items": []}, "code": {"code": "0000", "msg": "", "msgId": "SUCCESS"}}
    return request_post(URL_QUERY_WORK_ITEMS_DETAIL, headers, ids, timeout=timeout, log_request=True, log_response=True)


# ---------------------------------------------------------------------------
# 工作项创建 / 更新
# ---------------------------------------------------------------------------


def create_work_item(
    workspace: str,
    payload: Dict[str, Any],
    headers: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """
    创建工作项。
    :param workspace: 工作区
    :param payload: 请求体（含 workItemTypeKey、fields 等）
    :param headers: 认证头
    :return: 接口原始响应
    """
    url = URL_WORK_ITEM_CREATE.format(workspace=workspace)
    return request_post(url, headers, payload, timeout=timeout, log_request=True, log_response=True)


def update_work_item(
    work_item_id: str,
    fields: List[Dict[str, Any]],
    headers: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """
    更新工作项。
    :param work_item_id: 工作项标识（如 PLM-12345）
    :param fields: 字段列表 [{"key":"...", "value":"..."}]
    :param headers: 认证头
    :return: 接口原始响应
    """
    url = URL_WORK_ITEM_UPDATE.format(work_item_id=work_item_id)
    return request_put(url, headers, {"fields": fields}, timeout=timeout, log_request=True, log_response=True)


# ---------------------------------------------------------------------------
# 工作项关联：关联类型中文 -> referenceName（linkRelationId）
# ---------------------------------------------------------------------------
# 一共30种关联类型
LINK_RELATION_TYPE_MAP = {
    "相关": "linkRelatedType_1",
    "父级": "linkRelatedType_2",
    "子级": "linkRelatedType_3",
    "前置": "linkRelatedType_12",
    "源": "linkRelatedType_10",
    "测试": "linkRelatedType_4",
    "被测试": "linkRelatedType_5",
    "影响": "linkRelatedType_7",
    "影响者": "linkRelatedType_6",
    "下游": "linkRelatedType_9",
    "上游": "linkRelatedType_8",
    "副本": "linkRelatedType_11",
    "后续": "linkRelatedType_13",
    "被计划": "plan_in",
    "计划": "plan_out",
    "依赖": "depend_out",
    "被依赖": "depend_in",
    "输入输出": "IO",
    "说明": "explain_out",
    "被说明": "explain_in",

    "被参考": "reference_in",
    "参考": "reference_out",
    "使用": "use_out",
    "被使用": "use_in",
    "归属": "zte_belongto",
    "被归属": "zte_include",
    "被包含": "zte_contained",
    "包含": "zte_contain",
    "子计划": "sub_plan_in",
    "被子计划": "sub_plan_out",
}


def batch_create_relations(
    body: List[Dict[str, Any]],
    headers: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """
    批量创建工作项关联关系。
    :param body: 请求体数组，每项 {"workItemId": "...", "linkRelationId": "...", "relatedWorkItemId": "..."}
    :param headers: 认证头
    :return: 响应，含 bo.succeededItems、bo.failedItems；failedItems 非空表示有失败
    """
    url = URL_WORK_ITEM_RELATIONS_BATCH_CREATE
    return request_post(url, headers, body, timeout=timeout, log_request=True, log_response=True)


# ---------------------------------------------------------------------------
# 元数据：工作项类型、字段
# ---------------------------------------------------------------------------


def fetch_work_item_type_keys(
    workspace: str,
    type_names: List[str],
    headers: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """
    根据类型名称列表解析为类型 key 映射。
    :param workspace: 工作区
    :param type_names: 类型名称列表（如 ["任务", "缺陷"]）
    :param headers: 认证头
    :return: 接口 bo 原始映射，如 {"需求": {"PR": "产品需求", "AiTechReq": "技术需求", ...}}。
    """
    workspace = _normalize_workspace_single(workspace)
    url = URL_WORK_ITEM_TYPE_KEYS.format(workspace=workspace)
    body = type_names if len(type_names) > 1 else [type_names[0]]
    resp = request_post(url, headers, body, timeout=timeout, log_request=True, log_response=True)
    if isinstance(resp, dict) and isinstance(resp.get("bo"), dict):
        return resp["bo"]
    return resp if isinstance(resp, dict) else {}


def _normalize_workspace_single(workspace: Any) -> str:
    """若 workspace 为数组或数组字符串（如 ['PLM','LCAP']），取第一个并转为 str；否则返回 str(workspace)。"""
    if isinstance(workspace, (list, tuple)):
        return str(workspace[0]) if workspace else ""
    if isinstance(workspace, str):
        s = workspace.strip()
        if s.startswith("[") and "]" in s:
            try:
                parsed = ast.literal_eval(s)
                if isinstance(parsed, (list, tuple)) and parsed:
                    return str(parsed[0])
            except (ValueError, SyntaxError):
                pass
    return str(workspace) if workspace is not None else ""


def get_wim_query_view_url(workspace: Any, view_id: str) -> str:
    """生成 WIM 查询视图页链接（保存视图后跳转用）。workspace 会经 _normalize_workspace_single 规范化。"""
    ws = _normalize_workspace_single(workspace)
    return f"{BASE_RDCLOUD_PORTAL}/workspaces/{ws}/apps/wim/queries?wicViewId={view_id}"


def fetch_fields(
    workspace: str,
    headers: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
) -> List[dict]:
    """
    获取工作区字段列表（withUsageDistinct）。
    :param workspace: 工作区（可为单个 key、数组或数组字符串，数组时取第一个）
    :param headers: 认证头
    :return: bo.items，失败或空返回 []
    """
    workspace = _normalize_workspace_single(workspace)
    url = URL_FIELDS.format(workspace=workspace)
    data = request_post(url, headers, {}, timeout=timeout, log_request=False, log_response=False)
    code = (data.get("code") or {})
    if isinstance(code, dict) and code.get("code") != "0000":
        return []
    bo = data.get("bo") or {}
    items = bo.get("items")
    return items if isinstance(items, list) else []


def fetch_fields_v2_with_usage_distinct(
    workspace: str,
    headers: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
    cross_workspace_keys: Optional[List[str]] = None,
) -> List[dict]:
    """
    备选字段列表接口：POST .../fields/v2/withUsageDistinct，body 含 isCrossWorkspace、crossWorkspaceKeys。
    与 fetch_fields 相同返回 bo.items；业务 code 非 0000 或异常时返回 []。
    """
    workspace = _normalize_workspace_single(workspace)
    url = URL_FIELDS_V2_WITH_USAGE_DISTINCT.format(workspace=workspace)
    body = {
        "isCrossWorkspace": True,
        "crossWorkspaceKeys": list(cross_workspace_keys) if cross_workspace_keys else [],
    }
    try:
        data = request_post(url, headers, body, timeout=timeout, log_request=False, log_response=False)
    except Exception as e:
        logger.warning("fetch_fields_v2_with_usage_distinct 请求异常 workspace=%s: %s", workspace, e)
        return []
    code = data.get("code") or {}
    if isinstance(code, dict) and code.get("code") != "0000":
        return []
    bo = data.get("bo") or {}
    items = bo.get("items")
    return items if isinstance(items, list) else []


def fetch_work_item_type_detail(
    workspace: str,
    work_item_type_key: str,
    headers: Dict[str, Any],
    work_item_id: str = "",
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """
    获取工作项类型详情（含该类型下字段完整配置）。
    GET .../workspaces/{workspace}/work_item_types/{work_item_type_key}/workItemTypeDetail?workItemId=
    :param workspace: 工作区
    :param work_item_type_key: 工作项类型 key（如 chgRequest）
    :param headers: 认证头
    :param work_item_id: 可选，workItemId 查询参数
    :return: 接口原始响应，含 bo、bo.fields（key→字段配置的字典）
    """
    workspace = _normalize_workspace_single(workspace)
    url = URL_WORK_ITEM_TYPE_DETAIL.format(workspace=workspace, work_item_type_key=(work_item_type_key or "").strip())
    params = {"workItemId": work_item_id} if work_item_id else None
    return request_get(url, headers, params=params, timeout=timeout)


def fetch_work_item_init_detail(
    workspace: str,
    work_item_type_key: str,
    headers: Dict[str, Any],
    copied_fields: Optional[List[Any]] = None,
    operate_type: str = "defaultOperate",
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """
    新建初始化：获取 eventId，供后续 event 与创建使用。
    POST .../work_items/init/getInitDetail?workItemTypeKey=xxx
    :param workspace: 工作区
    :param work_item_type_key: 工作项类型 key
    :param headers: 认证头
    :param copied_fields: 可选，复制来源字段
    :param operate_type: 操作类型，默认 defaultOperate
    :return: 接口原始响应，含 bo.eventId、bo.fields 等
    """
    workspace = _normalize_workspace_single(workspace)
    wtk = (work_item_type_key or "").strip()
    url = URL_WORK_ITEM_INIT_DETAIL.format(workspace=workspace) + "?workItemTypeKey=" + (wtk or "")
    body = {"copiedFields": copied_fields if copied_fields is not None else [], "operateType": operate_type}
    return request_post(url, headers, body, timeout=timeout, log_request=False, log_response=False)


def fetch_update_work_item_init_detail(
    workspace: str,
    work_item_id: str,
    work_item_type_key: str,
    headers: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """
    更新初始化：获取 eventId、availableStates、fields（含 required/persistentValue/value），供更新校验与 event 使用。
    GET .../work_items/{workItemId}/getInitWorkItemDetail?workItemTypeKey=xxx
    :param workspace: 工作区
    :param work_item_id: 工作项标识（如 PLM-12345）
    :param work_item_type_key: 工作项类型 key
    :param headers: 认证头
    :return: 接口原始响应，含 bo.eventId、bo.availableStates、bo.fields 等
    """
    workspace = _normalize_workspace_single(workspace)
    wid = (work_item_id or "").strip()
    wtk = (work_item_type_key or "").strip()
    url = URL_WORK_ITEM_UPDATE_INIT_DETAIL.format(workspace=workspace, work_item_id=wid)
    return request_get(url, headers, params={"workItemTypeKey": wtk}, timeout=timeout)


def put_work_item_event(
    workspace: str,
    event_id: str,
    body: Dict[str, Any],
    headers: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """
    提交工作项 event（PUT 更新字段等）。
    PUT .../work_items/events/{eventId}
    :param workspace: 工作区
    :param event_id: 初始化返回的 eventId
    :param body: 请求体，含 workItemTypeKey、fields（[{key, value, type}]）、virtualId
    :param headers: 认证头
    :return: 接口原始响应
    """
    workspace = _normalize_workspace_single(workspace)
    url = URL_WORK_ITEM_EVENT.format(workspace=workspace, event_id=(event_id or "").strip())
    #fields = body.get("fields") or []
    #field_keys = {(f.get("key") or "").strip() for f in fields if isinstance(f, dict) and (f.get("key") or "").strip()}
    #do_log = bool(field_keys & PUT_EVENT_LOG_WHEN_KEYS)
    do_log = False
    resp = request_put(url, headers, body, timeout=timeout, log_request=do_log, log_response=do_log)
    # 从 bo.fields 中获取 key=System_Description_html 的完整信息并打印
    # bo = resp.get("bo") or {}
    # flist = bo.get("fields") or bo.get("fieldList") or []
    # for f in flist:
    #     if isinstance(f, dict) and (f.get("key") or "").strip() == "System_Description_html":
    #         logger.info(
    #             "put_work_item_event 响应 bo.fields 中 System_Description_html 完整信息: %s",
    #             json.dumps(sanitize_for_log(f), ensure_ascii=False, indent=2),
    #         )
    #         break
    return resp


def delete_cache_event(
    workspace: str,
    event_id: str,
    headers: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """
    销毁 event 缓存。
    DELETE .../cache/event/{eventId}
    :param workspace: 工作区
    :param event_id: 初始化返回的 eventId
    :param headers: 认证头
    :return: 接口原始响应
    """
    workspace = _normalize_workspace_single(workspace)
    url = URL_CACHE_EVENT_DELETE.format(workspace=workspace, event_id=(event_id or "").strip())
    return request_delete(url, headers, timeout=timeout)


def fetch_fields_candidate(
    workspace: str,
    body: Dict[str, Any],
    headers: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
    field: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    请求字段候选接口（如 advancedData 远程模糊查询）。
    :param workspace: 工作区
    :param body: 请求体，如 {"type": "advancedData", "workItemTypeKey": "...", "advancedData": {...}, "baseDataKey": "...", "searchKeyWord": "...", "searchField": "...", "workItemSessionId": "", "currentValue": ""}
    :param headers: 认证头
    :param field: 当前字段配置（可选），用于日志打印
    :return: 接口原始响应，含 bo.baseDataValueNodeList 等
    """
    if field is not None:
        #logger.info("【fetch_fields_candidate】当前字段: key=%s, type=%s", field.get("key"), field.get("type"))
        pass
    workspace = _normalize_workspace_single(workspace)
    url = URL_FIELDS_CANDIDATE.format(workspace=workspace)
    return request_post(url, headers, body, timeout=timeout, log_request=False, log_response=False)


def fetch_remote_data_source_realtime(
    workspace: str,
    body: Dict[str, Any],
    headers: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
    field: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    请求远程数据源实时查询（advancedData sourceType=remoteRealTime）。
    :param workspace: 工作区
    :param body: 请求体，如 {"workItemTypeKey": "...", "key": "BelongProject", "eventId": "...", "remoteDataConfig": "", "keywords": "...", "currentValue": ""}
    :param headers: 认证头
    :param field: 当前字段配置（可选），用于日志打印
    :return: 接口原始响应，bo 为数组，每项含 value/label/baseDataValue
    """
    if field is not None:
        #logger.info("【fetch_remote_data_source_realtime】当前字段: key=%s, type=%s", field.get("key"), field.get("type"))
        pass
    workspace = _normalize_workspace_single(workspace)
    url = URL_REMOTE_DATA_SOURCE_REALTIME.format(workspace=workspace)
    return request_post(url, headers, body, timeout=timeout, log_request=False, log_response=False)


def fetch_work_item_root_types(
    workspace: str,
    headers: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
) -> List[dict]:
    """
    获取工作项根类型列表（备用，用于类型名称解析）。
    :param workspace: 工作区
    :param headers: 认证头
    :return: bo.items；同时打日志：GET 的 url、查询参数（本接口无 query 时记录为空对象）及响应体摘要（脱敏截断）。
    """
    workspace = _normalize_workspace_single(workspace)
    url = URL_WORK_ITEM_ROOT_TYPES.format(workspace=workspace)
    data = request_get(
        url,
        headers,
        params=None,
        timeout=timeout,
        log_request=True,
        log_response=True,
    )
    code = data.get("code") or {}
    if isinstance(code, dict) and code.get("code") != "0000":
        return []
    bo = data.get("bo") or {}
    items = bo.get("items")
    return items if isinstance(items, list) else []


def fetch_work_item_types_by_root_key(
    workspace: str,
    work_item_type_root_key: str,
    headers: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
) -> List[dict]:
    """
    按工作项类型根 key 查询工作区下的工作项类型列表（用于取 valid=true 的模型 key）。
    :param workspace: 工作区
    :param work_item_type_root_key: 根类型 key（如 Task、US）
    :param headers: 认证头
    :return: bo.items
    """
    workspace = _normalize_workspace_single(workspace)
    url = URL_WORK_ITEM_TYPES.format(workspace=workspace)
    data = request_get(url, headers, params={"workItemTypeRootKey": work_item_type_root_key}, timeout=timeout)
    code = data.get("code") or {}
    if isinstance(code, dict) and code.get("code") != "0000":
        return []
    bo = data.get("bo") or {}
    items = bo.get("items")
    return items if isinstance(items, list) else []


# ---------------------------------------------------------------------------
# 通知与用户
# ---------------------------------------------------------------------------


def send_mail(
    payload: Dict[str, Any],
    headers: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
) -> requests.Response:
    """发送邮件。payload 由调用方按研发云邮件接口格式组装。"""
    h = _headers_for_request(headers)
    return requests.post(URL_SEND_MAIL, headers=h, data=json.dumps(payload), timeout=timeout)


def send_chat_message(
    payload: Dict[str, Any],
    headers: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
) -> requests.Response:
    """发送 iCenter 消息。payload 由调用方按研发云 chat 接口格式组装。"""
    h = _headers_for_request(headers)
    return requests.post(URL_SEND_ICHAT, headers=h, data=json.dumps(payload), timeout=timeout)


def user_search(
    workspace: str,
    keyword: str,
    headers: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """
    按关键字搜索用户（用于解析 @me、姓名→工号）。
    :param workspace: 工作区
    :param keyword: 关键字
    :param headers: 认证头
    :return: 接口 JSON，通常含 users 列表
    """
    url = URL_USER_SEARCH.format(workspace=workspace)
    return request_get(url, headers, params={"keyword": keyword}, timeout=timeout)


def copy_work_item_direct(
    workspace: str,
    source_work_item_id: str,
    source_type_key: str,
    target_type_key: str,
    fields: list,
    headers: Dict[str, Any],
    mapping_fields: Optional[list] = None,
    link_relation_id: str = "",
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """
    【直接复制】调用 check-batchCopy + batchCopy 完成工作项复制。

    核心优势：
    1. 无需 eventId 机制
    2. 无需逐字段 PUT
    3. 一次 HTTP 请求完成复制

    :param workspace: 工作区
    :param source_work_item_id: 源工作项 ID（如 sst-123）
    :param source_type_key: 源工作项类型 key
    :param target_type_key: 目标工作项类型 key
    :param fields: 字段列表 [{key, value, type}]
    :param mapping_fields: 覆盖映射字段（来自解析后的用户覆盖字段）
    :param link_relation_id: 复制时关联关系类型ID（如 linkRelatedType_3）
    :param headers: 认证头
    :return: 创建结果
    """
    workspace = _normalize_workspace_single(workspace)
    check_url = URL_WORK_ITEM_CHECK_BATCH_COPY.format(workspace=workspace)
    batch_url = URL_WORK_ITEM_BATCH_COPY.format(workspace=workspace)

    related_root_key = (target_type_key or "").strip().split("_")[0]
    field_related_ref = []
    effective_mapping_fields = mapping_fields if mapping_fields is not None else (fields or [])
    for idx, f in enumerate(effective_mapping_fields):
        if not isinstance(f, dict):
            continue
        key = (f.get("key") or "").strip()
        if not key:
            continue
        field_related_ref.append(
            {
                "key": "",
                "from": "value",
                "relatedKey": key,
                "syncUpdateAble": False,
                "value": f.get("value"),
                "required": False,
                "conditionId": f"condition_{idx}",
                "type": f.get("type") or "string",
            }
        )

    body = {
        "relatedWorkspaceKey": workspace,
        "relatedWorkItemTypeRootKey": related_root_key,
        "relatedWorkItemTypeKey": target_type_key,
        "linkRelationId": (link_relation_id or "").strip(),
        "fieldRelatedRef": field_related_ref,
        "workItems": [
            {
                "id": source_work_item_id,
                "workspaceKey": workspace,
                "workItemTypeKey": source_type_key,
            }
        ],
        "copyRelatedWorkItem": {
            "copy": False,
            "selectedLinkRelationIds": [],
            "copyRelatedWorkItemToOriginalSpace": False,
        },
    }

    # 先校验批量复制请求体
    request_post(check_url, headers, body, timeout=timeout)
    raw_resp = request_post(batch_url, headers, body, timeout=timeout)

    # 兼容批量复制返回：归一化为单条结构（bo.id/bo.fields），保持上层逻辑不变
    if not isinstance(raw_resp, dict):
        return raw_resp
    bo = raw_resp.get("bo")
    if isinstance(bo, list) and bo:
        first = bo[0] if isinstance(bo[0], dict) else None
        if first is not None:
            normalized = dict(raw_resp)
            normalized["bo"] = first
            return normalized
    if isinstance(bo, dict):
        succeeded_items = bo.get("succeededItems")
        if (
            isinstance(succeeded_items, list)
            and succeeded_items
            and isinstance(succeeded_items[0], dict)
        ):
            first = succeeded_items[0]
            new_id = (
                first.get("relatedWorkItemId")
                or first.get("newWorkItemId")
                or first.get("id")
                or ""
            )
            normalized = dict(raw_resp)
            normalized_bo = dict(bo)
            if new_id and not normalized_bo.get("id"):
                normalized_bo["id"] = new_id
            normalized["bo"] = normalized_bo
            return normalized
        items = bo.get("items")
        if isinstance(items, list) and items and isinstance(items[0], dict):
            normalized = dict(raw_resp)
            normalized["bo"] = items[0]
            return normalized
        vo_list = bo.get("workItemVOList")
        if isinstance(vo_list, list) and vo_list and isinstance(vo_list[0], dict):
            normalized = dict(raw_resp)
            normalized["bo"] = vo_list[0]
            return normalized
    return raw_resp


def query_rule_logs(
    headers: Dict[str, Any],
    workspace: str,
    params: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """
    查询规则执行日志（ES：meta_zte_rule_log）。
    POST /api/workspaces/{workspace}/rule_logs
    :param headers: 技能层认证头
    :param workspace: 工作区 key
    :param params: 查询参数（eventId, workItemId, result, pageNo, pageSize 等）
    :param timeout: 超时秒数
    :return: 接口响应（含 ruleLogs 列表）
    """
    url = URL_RULE_LOGS.format(workspace=workspace)
    body = {"pageNo": 1, "pageSize": 10, **params}
    try:
        resp = request_post(url, headers, body, timeout=timeout, log_request=True, log_response=True)
        return resp
    except Exception as e:
        logger.warning("查询规则执行日志失败 workspace=%s: %s", workspace, e)
        return {"bo": {"items": [], "total": 0}}


def query_after_trigger_logs(
    headers: Dict[str, Any],
    workspace: str,
    event_id: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> List[Dict[str, Any]]:
    """
    查询事后规则日志（MongoDB：zte_after_trigger_simplified_log）。
    临时接口：GET /rest/afterTriggerLog/{eventId}（eventId 包含租户信息）
    上线后切换：GET /api/workspaces/{workspace}/AiFunction/skill/after_trigger_log/{eventId}
    :param headers: 技能层认证头
    :param workspace: 工作区 key（临时接口暂不使用，保留兼容）
    :param event_id: 事件ID
    :param timeout: 超时秒数
    :return: 日志列表
    """
    url = URL_AFTER_TRIGGER_LOG.format(eventId=event_id)
    try:
        resp = request_get(url, headers, timeout=timeout, log_request=True, log_response=True)
        if isinstance(resp, list):
            return resp
        return resp.get("bo") if isinstance(resp, dict) and isinstance(resp.get("bo"), list) else []
    except Exception as e:
        logger.warning("查询事后规则日志失败 eventId=%s: %s", event_id, e)
        return []


def fetch_rules(
    headers: Dict[str, Any],
    workspace: str,
    work_item_type_key: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> List[Dict[str, Any]]:
    """
    获取指定工作项类型下的所有规则配置。
    临时接口：GET /rest/workspaces/{workspace}/work_item_types/{work_item_type_key}/rules
    上线后切换：GET /api/workspaces/{workspace}/AiFunction/skill/rules?workItemTypeKey={type}
    :param headers: 技能层认证头
    :param workspace: 工作区 key
    :param work_item_type_key: 工作项类型 key
    :param timeout: 超时秒数
    :return: 规则列表（每个规则为 dict，含 key/nameZh/description/condition/actions 等字段）
    """
    url = URL_RULES.format(workspace=workspace, work_item_type_key=work_item_type_key)
    logger.info("请求规则接口：url=%s, workspace=%s, work_item_type_key=%s", url, workspace, work_item_type_key)
    try:
        resp = request_get(url, headers, log_request=True, log_response=False, timeout=timeout)
        logger.info("规则接口响应：code=%s, bo 类型=%s", resp.get("code") if isinstance(resp, dict) else "N/A", type(resp.get("bo") if isinstance(resp, dict) else resp).__name__)
        rules = resp.get("bo") if isinstance(resp, dict) else resp
        if isinstance(rules, list):
            logger.info("获取到 %d 条规则", len(rules))
            return rules
        logger.warning("规则接口返回格式异常：bo 不是列表，实际类型=%s", type(rules).__name__)
        return []
    except Exception as e:
        logger.exception("获取规则失败 workspace=%s type=%s: %s", workspace, work_item_type_key, e)
        return []


def fetch_transitions(
    workspace: str,
    work_item_type_key: str,
    headers: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
) -> List[dict]:
    """
    获取工作项类型的所有流转步骤（Transition）。
    【临时接口】当前使用REST API
    临时接口：GET /rest/workspaces/{workspace}/work_item_types/{work_item_type_key}/transitions
    【上线后切换】
    正式接口：GET /api/workspaces/{workspace}/AiFunction/skill/transitions?workItemTypeKey={type}

    :param workspace: 工作区
    :param work_item_type_key: 工作项类型 key
    :param headers: 认证头
    :param timeout: 超时秒数
    :return: bo.items，失败或空返回 []
    """
    url = URL_TRANSITIONS.format(workspace=workspace, workItemTypeKey=work_item_type_key)
    data = request_get(url, headers, params={"pageNo": 1, "pageSize": 1000}, timeout=timeout, log_request=False, log_response=False)
    code = (data.get("code") or {})
    if isinstance(code, dict) and code.get("code") != "0000":
        return []
    bo = data.get("bo") or {}
    items = bo.get("items")
    return items if isinstance(items, list) else []


def fetch_states(
    workspace: str,
    work_item_type_key: str,
    headers: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
) -> List[dict]:
    """
    获取工作项类型的状态定义列表（nameZh → key 映射）。

    对应数据库：db.meta_state_def.find({workspaceKey:..., workItemTypeKey:...})
    对应Controller：StateManageController.queryStateDefByWorkItemTypeKey

    【临时接口】当前使用 Manage API（StateManageController）
    GET .../manage/api/workspaces/{workspace}/work_item_types/{workItemTypeKey}/states

    【正式接口】AiFunctionController.getStates()
    GET .../api/workspaces/{workspace}/AiFunction/skill/states?workItemTypeKey={type}

    两种接口的响应规范化后统一返回 list[dict]，切换时只需改 URL_STATES。

    :param workspace: 工作区
    :param work_item_type_key: 工作项类型 key
    :param headers: 认证头
    :param timeout: 超时秒数
    :return: list[dict]，每项含 nameZh、key 等字段；失败或空返回 []
    """
    url = URL_STATES.format(workspace=workspace, workItemTypeKey=work_item_type_key)
    try:
        data = request_get(url, headers, timeout=timeout, log_request=True, log_response=True)
        code = (data.get("code") or {})
        if isinstance(code, dict) and code.get("code") != "0000":
            logger.warning("获取状态定义失败：code=%s", code)
            return []
        bo = data.get("bo")
        # 统一返回 list[dict]：
        #   AiFunction 格式：bo = [{nameZh, key, ...}]
        #   Manage API 格式：bo = {items: [{nameZh, key, ...}]}
        if isinstance(bo, list):
            return bo
        if isinstance(bo, dict):
            items = bo.get("items")
            if isinstance(items, list):
                return items
        return []
    except Exception as e:
        logger.warning("获取状态定义异常 workspace=%s type=%s: %s", workspace, work_item_type_key, e)
        return []
