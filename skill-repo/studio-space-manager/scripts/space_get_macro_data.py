#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通过插件「页面浏览」宏接口获取指定页面的浏览记录（结构化 JSON）。

默认（推荐）：POST .../zte-rdcloud-space-pluginoff/macro/execute
  ?system={build_system_param}&environmentType=browser
  Body: {"macroKey":"PageViewMacro","macroData":{...}}

参考用户提供的抓包说明（get_macro）；返回 bo 含 totalVisitorCount、datas 等。

旧版（HTML 表格）：内容域 #58 macros/execute + 大段 html 片段。
  设置环境变量 SPACE_USE_LEGACY_PAGE_VIEWS_MACRO=1 时使用；
  或用 SPACE_LEGACY_MACRO_EXECUTE_URL 覆盖旧版地址。

环境变量：
  SPACE_PAGE_VIEW_MACRO_URL：覆盖默认插件宏地址。
  SPACE_USE_LEGACY_PAGE_VIEWS_MACRO：设为 1 时使用旧版 HTML 接口。
  SPACE_LEGACY_MACRO_EXECUTE_URL：旧版 macros/execute 完整 URL（可选覆盖）。
  SPACE_MACRO_EXECUTE_URL：兼容旧配置；未设置 SPACE_LEGACY_MACRO_EXECUTE_URL 时用作旧版地址。

用法：
  python3 space_get_macro_data.py --url "https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view"
  python3 space_get_macro_data.py --space-id <spaceId> --page-id <pageId>
  python3 space_get_macro_data.py --space-id <spaceId> --page-id <pageId> --limit 50 --offset 0 --json-output
"""

import argparse
import html as html_lib
import json
import os
import re
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from system_param import build_icenter_api_url, build_system_param, get_user_and_token
from _platform_utils import configure_stdio_utf8, get_platform_and_user_agent

DEFAULT_PAGE_VIEW_MACRO_URL = (
    build_icenter_api_url("/zte-rdcloud-space-pluginoff/macro/execute")
)
DEFAULT_LEGACY_MACRO_EXECUTE_URL = (
    build_icenter_api_url("/zte-rd-icenter-contents/macros/execute")
)
SYSTEM_QUERY_FALLBACKS = ("space-1920485495", "space-1920484530")
TITLE_API_TMPL = (
    build_icenter_api_url("/zte-rd-icenter-contents/content/title/%s")
)
PAGE_VIEWS_MACRO_KEY = "com.zte.wiki.macro.PageViewsMacro"
PAGE_VIEWS_MAPPING = "pageViewMacro"
PAGE_VIEWS_MACRO_INFO: Dict[str, Any] = {
    "categoryCode": "00003",
    "customize": False,
    "description": "以表格的形式，展示页面被浏览情况",
    "id": "0ca9ebaf373711e8b8f70242ac110002",
    "invokeType": "1",
    "macroKey": PAGE_VIEWS_MACRO_KEY,
    "mappingObject": PAGE_VIEWS_MAPPING,
    "onlineHelpLink": "",
    "renderMode": "INLINE",
    "title": "页面浏览",
    "usageLimit": 50,
    "version": "v1",
}


def _json_mode_log(message: str) -> None:
    print(f"[space_get_macro_data] {message}", file=sys.stderr)


def parse_space_page_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    if not url:
        return None, None
    pattern_shared = r"/shared/([a-f0-9]{32})/wiki/page/([a-f0-9]{32})/view"
    m = re.search(pattern_shared, url, flags=re.IGNORECASE)
    if m:
        return m.group(1), m.group(2)
    pattern_space = r"/space/([a-f0-9]{32})/wiki/page/([a-f0-9]{32})/view"
    m = re.search(pattern_space, url, flags=re.IGNORECASE)
    if m:
        return m.group(1), m.group(2)
    return None, None


def _bo_to_plain_preview(bo_html: str, max_len: int = 6000) -> str:
    if not bo_html:
        return ""
    text = re.sub(r"<script[^>]*>.*?</script>", " ", bo_html, flags=re.DOTALL | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


def _new_macro_id() -> str:
    """与抓包类似的 26 位 id（非严格 ULID，仅保证单次调用内唯一）。"""
    return uuid.uuid4().hex[:26]


def _build_macro_html_fragment(
    macro_id: str,
    space_id: str,
    content_id: str,
    emp_no: str,
    macro_parameters: Dict[str, Any],
) -> str:
    img_props = {
        "macroParameters": macro_parameters,
        "macroInfo": PAGE_VIEWS_MACRO_INFO,
        "isUpdate": False,
        "employeeShortId": emp_no,
        "spaceId": space_id,
        "hasContentBody": False,
        "parentContextNode": "",
    }
    props_json = json.dumps(img_props, ensure_ascii=False, separators=(",", ":"))
    escaped = html_lib.escape(props_json, quote=True)
    return (
        '<span class="icenter-macro-backend-parsed-inline icenter-macro-wrapper '
        'icenter-macro-isomorphic-wrapper">'
        '<span class="macro-container">'
        f'<img data-macro-props="{escaped}" id="{macro_id}" '
        f'data-macro-key="{PAGE_VIEWS_MACRO_KEY}" data-icenter-macro-type="original" '
        'class="editor-inline-macro icenter-macro icenter-macro-original '
        'icenter-macro-find" />'
        "</span></span>"
    )


def build_legacy_page_views_macro_body(
    space_id: str,
    content_id: str,
    emp_no: str,
    *,
    content_title: str,
    limit: int,
    offset: int,
    level: str,
    scope: str,
) -> Dict[str, Any]:
    macro_id = "INLINE%d" % (int(time.time() * 1000))
    macro_parameters = {
        "_parentContextNodeHtml": "",
        "contentTitle": content_title or "",
        "contentId": content_id,
        "spaceId": space_id,
        "expandable": True,
        "level": level,
        "scope": scope,
        "pagination": {"limit": int(limit), "offset": int(offset)},
    }
    html_fragment = _build_macro_html_fragment(
        macro_id, space_id, content_id, emp_no, macro_parameters
    )
    return {
        "macroParameters": macro_parameters,
        "innerHtml": "",
        "historyId": None,
        "macroId": macro_id,
        "macroInfo": PAGE_VIEWS_MACRO_INFO,
        "mappingObject": PAGE_VIEWS_MAPPING,
        "macroKey": PAGE_VIEWS_MACRO_KEY,
        "html": html_fragment,
        "currentSpaceId": space_id,
        "currentContentId": content_id,
        "spaceId": space_id,
        "contentId": content_id,
        "contentBody": None,
    }


def build_plugin_page_view_body(
    space_id: str,
    page_id: str,
    *,
    content_name: str,
    limit: int,
    offset: int,
    level: str,
    macro_id: Optional[str] = None,
) -> Dict[str, Any]:
    mid = (macro_id or "").strip() or _new_macro_id()
    return {
        "macroKey": "PageViewMacro",
        "macroData": {
            "spaceId": space_id,
            "currentContentId": page_id,
            "contentId": page_id,
            "contentName": content_name or "",
            "level": level,
            "offset": int(offset),
            "limit": int(limit),
            "macroId": mid,
        },
    }


def _build_headers(emp_no: str, token: str, space_id: str, page_id: str) -> Dict[str, str]:
    _, user_agent = get_platform_and_user_agent()
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Origin": "https://i.zte.com.cn",
        "Referer": "https://i.zte.com.cn/",
        "User-Agent": user_agent,
        "X-Auth-Value": token,
        "X-Cookie-From-Cros": "Y",
        "X-Emp-No": emp_no,
        "X-Lang-Id": "zh_CN",
        "X-Tenant-Id": "ZTE",
        "Content-Type": "application/json;charset=UTF-8",
    }
    headers["apm_biz_key"] = json.dumps(
        {
            "serviceName": "ICENTER_zte-km-icenter-ui",
            "spaceId": space_id,
            "contentId": page_id,
            "X-Emp-No": emp_no,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return headers


def _plugin_macro_url() -> str:
    custom = (os.environ.get("SPACE_PAGE_VIEW_MACRO_URL") or "").strip()
    return custom or DEFAULT_PAGE_VIEW_MACRO_URL


def _legacy_macro_execute_url() -> str:
    custom = (os.environ.get("SPACE_LEGACY_MACRO_EXECUTE_URL") or "").strip()
    if custom:
        return custom
    legacy_from_old_env = (os.environ.get("SPACE_MACRO_EXECUTE_URL") or "").strip()
    if legacy_from_old_env:
        return legacy_from_old_env
    return DEFAULT_LEGACY_MACRO_EXECUTE_URL


def _use_legacy_flow() -> bool:
    return (os.environ.get("SPACE_USE_LEGACY_PAGE_VIEWS_MACRO") or "").strip() == "1"


def _legacy_macro_execute_params(space_id: str, system_value: str) -> Dict[str, Any]:
    return {
        "t": int(time.time() * 1000),
        "spaceId": space_id,
        "mode": "execute",
        "environmentType": "browser",
        "system": system_value,
    }


def _plugin_macro_params(system_value: str) -> Dict[str, Any]:
    return {
        "system": system_value,
        "environmentType": "browser",
    }


def _systems_to_try(explicit: Optional[str]) -> Tuple[str, ...]:
    if explicit and explicit.strip():
        return (explicit.strip(),)
    primary = build_system_param("space")
    out: List[str] = [primary]
    for fb in SYSTEM_QUERY_FALLBACKS:
        if fb not in out:
            out.append(fb)
    return tuple(out)


def fetch_content_title(emp_no: str, token: str, space_id: str, page_id: str) -> str:
    """接口 #27 获取页面标题，供 macroData.contentName / 旧版 contentTitle 使用。"""
    if not space_id or not page_id or not emp_no or not token:
        return ""
    _, user_agent = get_platform_and_user_agent()
    params = {
        "spaceId": space_id,
        "environmentType": "browser",
        "system": build_system_param("space"),
        "t": int(time.time() * 1000),
    }
    headers = {
        "Accept": "application/json",
        "X-Auth-Value": token,
        "X-Emp-No": emp_no,
        "X-Tenant-Id": "ZTE",
        "X-Lang-Id": "zh_CN",
        "Origin": "https://i.zte.com.cn",
        "Referer": "https://i.zte.com.cn/",
        "User-Agent": user_agent,
    }
    url = TITLE_API_TMPL % page_id
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except (requests.exceptions.RequestException, ValueError):
        return ""
    if (data.get("code") or {}).get("code") != "0000":
        return ""
    bo = data.get("bo")
    if isinstance(bo, str):
        return bo.strip()
    if isinstance(bo, dict):
        return (bo.get("title") or bo.get("contentTitle") or "").strip()
    return ""


def _api_code_ok(data: Dict[str, Any]) -> bool:
    code = (data.get("code") or {}).get("code")
    return code == "0000"


def _api_error_message(data: Dict[str, Any], fallback: str) -> str:
    code_block = data.get("code") or {}
    return (code_block.get("msg") or code_block.get("msgId") or fallback).strip() or fallback


def _error_hint_for_code(code: str, *, legacy: bool) -> str:
    if code == "0001":
        if legacy:
            return (
                "服务端 0001：已尝试补全页面标题与多种 query.system。"
                "若为 2.0 文档空间，本 1.0 宏接口可能不可用；也可设置 "
                "SPACE_LEGACY_MACRO_EXECUTE_URL 指向正确的 macros/execute。"
            )
        return (
            "服务端 0001：已尝试多种 query.system。"
            "可设置环境变量 SPACE_USE_LEGACY_PAGE_VIEWS_MACRO=1 回退到旧版 HTML 宏接口，"
            "或检查 SPACE_PAGE_VIEW_MACRO_URL。"
        )
    return ""


def _page_url(space_id: str, page_id: str) -> str:
    return "https://i.zte.com.cn/#/shared/%s/wiki/page/%s/view" % (space_id, page_id)


def _post_json(
    url: str,
    headers: Dict[str, str],
    params: Dict[str, Any],
    body: Dict[str, Any],
    timeout: int,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        resp = requests.post(url, headers=headers, params=params, json=body, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.Timeout:
        return None, "请求超时"
    except requests.exceptions.RequestException as e:
        return None, "请求失败: %s" % str(e)
    except ValueError:
        return None, "返回数据不是合法 JSON"
    return data, None


def _success_plugin_payload(
    space_id: str,
    page_id: str,
    resolved_name: str,
    sys_val: str,
    limit: int,
    offset: int,
    data: Dict[str, Any],
) -> Dict[str, Any]:
    bo = data.get("bo")
    totals: Dict[str, Any] = {}
    rows: List[Any] = []
    pg_limit, pg_offset = int(limit), int(offset)
    if isinstance(bo, dict):
        totals = {
            "approximateTotalVisitorCount": bo.get("approximateTotalVisitorCount"),
            "totalVisitorCount": bo.get("totalVisitorCount"),
            "approximateTotalViewCount": bo.get("approximateTotalViewCount"),
            "totalViewCount": bo.get("totalViewCount"),
        }
        rows = list(bo.get("datas") or [])
        if bo.get("limit") is not None:
            pg_limit = int(bo["limit"])
        if bo.get("offset") is not None:
            pg_offset = int(bo["offset"])
    return {
        "status": "success",
        "api_mode": "plugin",
        "space_id": space_id,
        "page_id": page_id,
        "page_url": _page_url(space_id, page_id),
        "content_name_used": resolved_name,
        "system_query_used": sys_val,
        "totals": totals,
        "pagination": {"limit": pg_limit, "offset": pg_offset},
        "datas": rows,
        "raw_response": data,
    }


def _success_legacy_payload(
    space_id: str,
    page_id: str,
    resolved_title: str,
    sys_val: str,
    limit: int,
    offset: int,
    data: Dict[str, Any],
) -> Dict[str, Any]:
    bo = data.get("bo")
    bo_html = bo if isinstance(bo, str) else ""
    return {
        "status": "success",
        "api_mode": "legacy_html",
        "space_id": space_id,
        "page_id": page_id,
        "page_url": _page_url(space_id, page_id),
        "content_title_used": resolved_title,
        "system_query_used": sys_val,
        "pagination": {"limit": int(limit), "offset": int(offset)},
        "macro_html": bo_html,
        "plain_text_preview": _bo_to_plain_preview(bo_html),
        "raw_response": data,
    }


def fetch_page_views_macro_data(
    space_id: str,
    page_id: str,
    emp_no: str,
    token: str,
    *,
    content_title: str = "",
    limit: int = 20,
    offset: int = 0,
    level: str = "current",
    scope: str = "Current",
    fetch_title: bool = True,
    system_query: Optional[str] = None,
    json_output: bool = False,
    force_legacy: bool = False,
) -> Dict[str, Any]:
    if not space_id or not page_id:
        return {"status": "error", "message": "缺少 space_id 或 page_id"}

    use_legacy = force_legacy or _use_legacy_flow()
    resolved_name = (content_title or "").strip()
    if fetch_title and not resolved_name:
        resolved_name = fetch_content_title(emp_no, token, space_id, page_id) or ""

    headers = _build_headers(emp_no, token, space_id, page_id)
    systems = _systems_to_try(system_query)

    if json_output:
        _json_mode_log(
            "mode=%s url=%s space_id=%s page_id=%s limit=%s offset=%s name_len=%s systems=%s"
            % (
                "legacy" if use_legacy else "plugin",
                _legacy_macro_execute_url() if use_legacy else _plugin_macro_url(),
                space_id,
                page_id,
                limit,
                offset,
                len(resolved_name),
                ",".join(systems),
            )
        )

    if use_legacy:
        return _fetch_legacy_loop(
            space_id,
            page_id,
            emp_no,
            resolved_name,
            limit,
            offset,
            level,
            scope,
            headers,
            systems,
            json_output,
        )

    macro_id = _new_macro_id()
    body = build_plugin_page_view_body(
        space_id,
        page_id,
        content_name=resolved_name,
        limit=limit,
        offset=offset,
        level=level,
        macro_id=macro_id,
    )
    base_url = _plugin_macro_url()
    last_data: Optional[Dict[str, Any]] = None
    for sys_val in systems:
        params = _plugin_macro_params(sys_val)
        data, err = _post_json(base_url, headers, params, body, timeout=60)
        if err:
            return {"status": "error", "message": err}
        last_data = data
        assert data is not None
        if _api_code_ok(data):
            return _success_plugin_payload(
                space_id, page_id, resolved_name, sys_val, limit, offset, data
            )
        err_code = (data.get("code") or {}).get("code", "")
        if err_code != "0001":
            break

    if last_data is None:
        return {"status": "error", "message": "未发起宏执行请求"}
    err_code = (last_data.get("code") or {}).get("code", "")
    out: Dict[str, Any] = {
        "status": "error",
        "message": _api_error_message(last_data, "获取页面浏览宏数据失败"),
        "code": err_code,
        "response": last_data,
        "content_name_used": resolved_name,
    }
    hint = _error_hint_for_code(err_code, legacy=False)
    if hint:
        out["hint"] = hint
    return out


def _fetch_legacy_loop(
    space_id: str,
    page_id: str,
    emp_no: str,
    resolved_title: str,
    limit: int,
    offset: int,
    level: str,
    scope: str,
    headers: Dict[str, str],
    systems: Tuple[str, ...],
    json_output: bool,
) -> Dict[str, Any]:
    body = build_legacy_page_views_macro_body(
        space_id,
        page_id,
        emp_no,
        content_title=resolved_title,
        limit=limit,
        offset=offset,
        level=level,
        scope=scope,
    )
    base_url = _legacy_macro_execute_url()
    last_data: Optional[Dict[str, Any]] = None
    for sys_val in systems:
        params = _legacy_macro_execute_params(space_id, sys_val)
        data, err = _post_json(base_url, headers, params, body, timeout=60)
        if err:
            return {"status": "error", "message": err}
        last_data = data
        assert data is not None
        if _api_code_ok(data):
            return _success_legacy_payload(
                space_id, page_id, resolved_title, sys_val, limit, offset, data
            )
        err_code = (data.get("code") or {}).get("code", "")
        if err_code != "0001":
            break

    if last_data is None:
        return {"status": "error", "message": "未发起宏执行请求"}
    err_code = (last_data.get("code") or {}).get("code", "")
    out: Dict[str, Any] = {
        "status": "error",
        "message": _api_error_message(last_data, "获取页面浏览宏数据失败"),
        "code": err_code,
        "response": last_data,
        "content_title_used": resolved_title,
    }
    hint = _error_hint_for_code(err_code, legacy=True)
    if hint:
        out["hint"] = hint
    return out


def _resolve_ids(args: argparse.Namespace) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    if getattr(args, "url", None):
        sid, pid = parse_space_page_url(args.url)
        if not sid or not pid:
            return None, None, "无法从 URL 解析 spaceId 与 pageId"
        return sid, pid, None
    if args.space_id and args.page_id:
        return args.space_id.strip(), args.page_id.strip(), None
    return None, None, "请提供 --url，或同时提供 --space-id 与 --page-id"


def _print_human(result: Dict[str, Any]) -> None:
    if result.get("status") != "success":
        print("❌ %s" % result.get("message", "未知错误"))
        if result.get("code"):
            print("错误码: %s" % result["code"])
        return

    mode = result.get("api_mode") or "plugin"
    print("\n📊 页面浏览记录")
    print("页面链接: %s" % result.get("page_url", ""))
    pg = result.get("pagination") or {}
    print("分页: limit=%s offset=%s" % (pg.get("limit"), pg.get("offset")))

    if mode == "plugin":
        totals = result.get("totals") or {}
        if any(totals.get(k) is not None for k in totals):
            print(
                "访客: %s（约 %s）  浏览: %s（约 %s）"
                % (
                    totals.get("totalVisitorCount", "-"),
                    totals.get("approximateTotalVisitorCount", "-"),
                    totals.get("totalViewCount", "-"),
                    totals.get("approximateTotalViewCount", "-"),
                )
            )
        rows = result.get("datas") or []
        if rows:
            print("-" * 72)
            for i, row in enumerate(rows, 1):
                if not isinstance(row, dict):
                    continue
                print(
                    "%2d. %s  最近: %s  次数: %s"
                    % (
                        i,
                        row.get("user") or row.get("userId") or "-",
                        row.get("latestViewTime") or "-",
                        row.get("viewCount", "-"),
                    )
            )
            print("-" * 72)
        print("说明: 完整 JSON 见 --json-output（datas / totals / raw_response）。")
        return

    preview = (result.get("plain_text_preview") or "").strip()
    if preview:
        print("-" * 72)
        print(preview)
        print("-" * 72)
    print(
        "说明: 完整表格为服务端返回的 HTML，已包含在 --json-output 的 macro_html / raw_response 字段中。"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="获取指定页面的页面浏览宏数据（默认插件 JSON 接口；可选旧版 HTML）"
    )
    parser.add_argument("--url", type=str, help="iCenter 页面 URL")
    parser.add_argument("--space-id", type=str, help="空间 ID")
    parser.add_argument("--page-id", type=str, help="页面 contentId")
    parser.add_argument(
        "--content-title",
        type=str,
        default="",
        help="可选，传入 macroData.contentName（旧模式下为 contentTitle；通常可留空）",
    )
    parser.add_argument("--limit", type=int, default=20, help="分页条数，默认 20")
    parser.add_argument("--offset", type=int, default=0, help="分页偏移，默认 0")
    parser.add_argument(
        "--level",
        type=str,
        default="current",
        help='macroData.level / macroParameters.level，默认 current',
    )
    parser.add_argument(
        "--scope",
        type=str,
        default="Current",
        help='仅旧版 HTML 接口：macroParameters.scope，默认 Current',
    )
    parser.add_argument(
        "--no-fetch-title",
        action="store_true",
        help="不调用标题接口补全名称（默认会自动补全）",
    )
    parser.add_argument(
        "--system-query",
        type=str,
        default="",
        help="仅使用指定的 query.system（不传则按 build_system_param 与若干备用值依次重试）",
    )
    parser.add_argument(
        "--legacy-html",
        action="store_true",
        help="强制走旧版 zte-rd-icenter-contents/macros/execute（HTML 表格）",
    )
    parser.add_argument(
        "--json-output",
        action="store_true",
        help="输出 JSON（stdout 仅 JSON，日志在 stderr）",
    )
    args = parser.parse_args()

    space_id, page_id, err = _resolve_ids(args)
    if err:
        if args.json_output:
            _json_mode_log("错误: %s" % err)
            print(json.dumps({"status": "error", "message": err}, ensure_ascii=False))
        else:
            print("❌ %s" % err)
        sys.exit(1)

    emp_no, token = get_user_and_token(json_output=args.json_output, strict=False)
    emp_no = (emp_no or "").strip()
    token = (token or "").strip()
    if not emp_no or not token:
        msg = "缺少 coclaw_empno / coclaw_token，无法认证"
        if args.json_output:
            _json_mode_log("错误: %s" % msg)
            print(json.dumps({"status": "error", "message": msg}, ensure_ascii=False))
        else:
            print("❌ %s" % msg)
        sys.exit(1)

    result = fetch_page_views_macro_data(
        space_id,
        page_id,
        emp_no,
        token,
        content_title=(args.content_title or "").strip(),
        limit=args.limit,
        offset=args.offset,
        level=args.level,
        scope=args.scope,
        fetch_title=not args.no_fetch_title,
        system_query=(args.system_query or "").strip() or None,
        json_output=args.json_output,
        force_legacy=args.legacy_html,
    )

    if args.json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("status") != "success":
            sys.exit(1)
    else:
        _print_human(result)
        if result.get("status") != "success":
            sys.exit(1)


if __name__ == "__main__":
    configure_stdio_utf8()
    main()
