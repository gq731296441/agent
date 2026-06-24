#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
空间页面标签：查询列表、新增（重复则提示不重复提交）、按 tagId 或标签名删除、按标签汇聚搜索同标签页面。

1.0 空间：zte-rd-icenter-extend/content（与 get_page_flower 同属 extend/content）。
2.0 文档空间：zte-rdcloud-space-social/content，新增时需 body 含 type=0000。
标签汇聚（同标签页面）：zte-rd-icenter-advanced/tagConvergence（与接口文档「标签汇聚」一致）。

用法：
    python3 space_page_label.py --action list --url "<页面URL>"
    python3 space_page_label.py --action list --space-id <sid> --page-id <pid> --json-output
    python3 space_page_label.py --action add --space-id <sid> --page-id <pid> --name "标签"
    python3 space_page_label.py --action remove --space-id <sid> --page-id <pid> --tag-id <id>
    python3 space_page_label.py --action remove --space-id <sid> --page-id <pid> --name "标签名"
    python3 space_page_label.py --action search --space-id <sid> --tag "标签A" [--tag "标签B"] [--offset 0 --limit 20]
    python3 space_page_label.py --action search --space-id <sid> --tags-from-page --url "<页面URL>"
    python3 space_page_label.py --action add ... --doc-space
"""

import argparse
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, NoReturn, Optional, Tuple

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from system_param import (
    ICENTER_API_BASE,
    build_system_param,
    get_user_and_token,
)
from _platform_utils import configure_stdio_utf8, get_platform_and_user_agent

TAG_BASE_10 = f"{ICENTER_API_BASE}/zte-rd-icenter-extend/content"
TAG_BASE_20 = f"{ICENTER_API_BASE}/zte-rdcloud-space-social/content"
TAG_CONVERGENCE_URL = f"{ICENTER_API_BASE}/zte-rd-icenter-advanced/tagConvergence"


def _json_log(msg: str) -> None:
    print(f"[space_page_label] {msg}", file=sys.stderr)


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


def _tag_params(space_id: str) -> Dict[str, Any]:
    return {
        "t": int(time.time() * 1000),
        "spaceId": space_id,
        "environmentType": "browser",
        "system": build_system_param("space"),
    }


def _build_headers(
    emp_no: str,
    token: str,
    space_id: str,
    page_id: str,
    *,
    with_json: bool,
) -> Dict[str, str]:
    _, user_agent = get_platform_and_user_agent()
    headers: Dict[str, str] = {
        "Accept": "application/json, text/plain",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Origin": "https://i.zte.com.cn",
        "Referer": "https://i.zte.com.cn/",
        "User-Agent": user_agent,
        "X-Auth-Value": token,
        "X-Cookie-From-Cros": "Y",
        "X-Emp-No": emp_no,
        "X-Lang-Id": "zh_CN",
        "X-Tenant-Id": "ZTE",
    }
    if with_json:
        headers["Content-Type"] = "application/json;charset=UTF-8"
    headers["apm_biz_key"] = json.dumps(
        {
            "serviceName": "ICENTER_zte-km-icenter-ui",
            "spaceId": space_id,
            "contentId": page_id,
            "X-Emp-No": emp_no,
        },
        ensure_ascii=False,
    )
    return headers


def _api_ok(data: dict) -> bool:
    return (data.get("code") or {}).get("code") == "0000"


def _api_err_msg(data: dict, default: str) -> str:
    c = data.get("code") or {}
    return (c.get("msg") or c.get("msgId") or default).strip() or default


def _coerce_tag_rows(bo: Any) -> List[Any]:
    if bo is None:
        return []
    if isinstance(bo, list):
        return bo
    if isinstance(bo, dict):
        for key in ("tags", "rows", "list", "data"):
            v = bo.get(key)
            if isinstance(v, list):
                return v
    return []


def _normalize_tag_entries(bo: Any) -> List[Dict[str, str]]:
    rows = _coerce_tag_rows(bo)
    out: List[Dict[str, str]] = []
    for item in rows:
        if isinstance(item, str):
            name = item.strip()
            if name:
                out.append({"id": "", "name": name})
            continue
        if not isinstance(item, dict):
            continue
        tid = str(item.get("id") or item.get("tagId") or "").strip()
        name = str(item.get("name") or item.get("tagName") or item.get("title") or "").strip()
        if name or tid:
            out.append({"id": tid, "name": name})
    return out


def _tag_base(doc_space: bool) -> str:
    return TAG_BASE_20 if doc_space else TAG_BASE_10


def _convergence_query_params(space_id: str, offset: int, limit: int) -> Dict[str, Any]:
    p = _tag_params(space_id)
    p["offset"] = max(0, int(offset))
    p["limit"] = max(1, min(int(limit), 1000))
    return p


def _extract_convergence_bo(data: dict) -> Optional[dict]:
    bo = data.get("bo")
    if isinstance(bo, dict) and ("rows" in bo or "total" in bo):
        return bo
    if isinstance(bo, dict) and isinstance(bo.get("data"), dict):
        inner = bo["data"]
        if isinstance(inner, dict) and "rows" in inner:
            return inner
    if isinstance(data.get("rows"), list):
        return data
    return None


def _normalize_convergence_rows(bo: dict) -> Tuple[List[Dict[str, Any]], int, int]:
    rows_raw = bo.get("rows")
    if not isinstance(rows_raw, list):
        rows_raw = []
    total = bo.get("total")
    try:
        total_n = int(total) if total is not None else len(rows_raw)
    except (TypeError, ValueError):
        total_n = len(rows_raw)
    cur = bo.get("current")
    try:
        current_n = int(cur) if cur is not None else 0
    except (TypeError, ValueError):
        current_n = 0

    out: List[Dict[str, Any]] = []
    for r in rows_raw:
        if not isinstance(r, dict):
            continue
        cid = str(r.get("contentId") or r.get("id") or "").strip()
        title = str(r.get("contentTitle") or r.get("title") or "").strip()
        sid = str(r.get("spaceId") or "").strip()
        tags_raw = r.get("tags")
        tag_list: List[str] = []
        if isinstance(tags_raw, list):
            tag_list = [str(t).strip() for t in tags_raw if str(t).strip()]
        page_url = ""
        if sid and cid:
            page_url = f"https://i.zte.com.cn/#/shared/{sid}/wiki/page/{cid}/view"
        out.append(
            {
                "content_id": cid,
                "content_title": title,
                "space_id": sid,
                "tags": tag_list,
                "update_by": str(r.get("updateBy") or "").strip(),
                "update_date": str(r.get("updateDate") or "").strip(),
                "page_url": page_url,
            }
        )
    return out, total_n, current_n


def fetch_tag_convergence_raw(
    space_id: str,
    tag_names: List[str],
    offset: int,
    limit: int,
    emp_no: str,
    token: str,
    page_id_for_headers: str = "",
) -> Tuple[Optional[dict], Optional[str]]:
    """POST 标签汇聚：指定空间内包含给定标签的页面（接口文档 #9）。"""
    url = TAG_CONVERGENCE_URL
    params = _convergence_query_params(space_id, offset, limit)
    headers = _build_headers(emp_no, token, space_id, page_id_for_headers or "", with_json=True)
    body = {"spaceIds": [space_id], "tags": tag_names}
    try:
        r = requests.post(url, params=params, headers=headers, json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        if not _api_ok(data):
            return None, _api_err_msg(data, "标签汇聚查询失败")
        bo = _extract_convergence_bo(data)
        if bo is None:
            return None, "标签汇聚查询失败: 响应结构无法解析"
        return bo, None
    except requests.exceptions.RequestException as e:
        return None, f"标签汇聚查询失败: {e}"
    except json.JSONDecodeError:
        return None, "标签汇聚查询失败: 响应非 JSON"


def fetch_tags_raw(
    space_id: str,
    page_id: str,
    emp_no: str,
    token: str,
    doc_space: bool,
) -> Tuple[Optional[dict], Optional[str]]:
    url = f"{_tag_base(doc_space)}/{page_id}/tags"
    headers = _build_headers(emp_no, token, space_id, page_id, with_json=True)
    try:
        r = requests.get(url, params=_tag_params(space_id), headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        if not _api_ok(data):
            return None, _api_err_msg(data, "获取标签失败")
        return data, None
    except requests.exceptions.RequestException as e:
        return None, f"获取标签失败: {e}"
    except json.JSONDecodeError:
        return None, "获取标签失败: 响应非 JSON"


def _names_set(entries: List[Dict[str, str]]) -> Dict[str, str]:
    """去空格后的展示名 -> 原始名（用于提示）。"""
    m: Dict[str, str] = {}
    for e in entries:
        n = (e.get("name") or "").strip()
        if n:
            key = n.casefold()
            m.setdefault(key, n)
    return m


def do_list(
    space_id: str,
    page_id: str,
    json_output: bool,
    doc_space: bool,
) -> Any:
    emp_no, token = get_user_and_token(json_output=json_output, strict=True)
    data, err = fetch_tags_raw(space_id, page_id, emp_no, token, doc_space)
    if err:
        out = {"status": "error", "message": err}
        if json_output:
            _json_log(err)
            return json.dumps(out, ensure_ascii=False)
        print(f"❌ {err}")
        return out

    entries = _normalize_tag_entries(data.get("bo"))
    page_url = f"https://i.zte.com.cn/#/shared/{space_id}/wiki/page/{page_id}/view"
    result = {
        "status": "success",
        "action": "list",
        "space_id": space_id,
        "page_id": page_id,
        "page_url": page_url,
        "doc_space": doc_space,
        "tags": entries,
    }
    if json_output:
        return json.dumps(result, ensure_ascii=False)
    print("\n📌 页面标签")
    print(f"页面链接: {page_url}")
    if not entries:
        print("(当前无标签)")
        return result
    print("-" * 48)
    for i, e in enumerate(entries, 1):
        tid = e.get("id") or "—"
        print(f"{i}. [{tid}] {e.get('name', '')}")
    print("-" * 48)
    return result


def do_add(
    space_id: str,
    page_id: str,
    name: str,
    json_output: bool,
    doc_space: bool,
) -> Any:
    emp_no, token = get_user_and_token(json_output=json_output, strict=True)
    new_name = (name or "").strip()
    if not new_name:
        out = {"status": "error", "message": "标签名称不能为空"}
        if json_output:
            return json.dumps(out, ensure_ascii=False)
        print(f"❌ {out['message']}")
        return out

    data, err = fetch_tags_raw(space_id, page_id, emp_no, token, doc_space)
    if err:
        out = {"status": "error", "message": err}
        if json_output:
            _json_log(err)
            return json.dumps(out, ensure_ascii=False)
        print(f"❌ {err}")
        return out

    entries = _normalize_tag_entries(data.get("bo"))
    name_map = _names_set(entries)
    if new_name.casefold() in name_map:
        display = name_map[new_name.casefold()]
        msg = f"标签「{display}」已存在，未重复提交。"
        result = {
            "status": "success",
            "action": "add",
            "skipped": True,
            "message": msg,
            "space_id": space_id,
            "page_id": page_id,
            "page_url": f"https://i.zte.com.cn/#/shared/{space_id}/wiki/page/{page_id}/view",
            "tag_name": display,
        }
        if json_output:
            return json.dumps(result, ensure_ascii=False)
        print(f"\nℹ️ {msg}")
        return result

    url = f"{_tag_base(doc_space)}/{page_id}/tags"
    headers = _build_headers(emp_no, token, space_id, page_id, with_json=True)
    body: Dict[str, str] = {"name": new_name}
    if doc_space:
        body["type"] = "0000"

    try:
        r = requests.post(
            url,
            params=_tag_params(space_id),
            headers=headers,
            json=body,
            timeout=30,
        )
        r.raise_for_status()
        resp = r.json()
        if not _api_ok(resp):
            em = _api_err_msg(resp, "新增标签失败")
            out = {"status": "error", "message": em, "code": (resp.get("code") or {}).get("code")}
            if json_output:
                return json.dumps(out, ensure_ascii=False)
            print(f"❌ {em}")
            return out
        bo = resp.get("bo")
        new_id = ""
        if isinstance(bo, dict):
            new_id = str(bo.get("id") or bo.get("tagId") or "").strip()
        elif isinstance(bo, str):
            new_id = bo.strip()
        result = {
            "status": "success",
            "action": "add",
            "skipped": False,
            "space_id": space_id,
            "page_id": page_id,
            "tag_name": new_name,
            "tag_id": new_id,
            "page_url": f"https://i.zte.com.cn/#/shared/{space_id}/wiki/page/{page_id}/view",
        }
        if json_output:
            return json.dumps(result, ensure_ascii=False)
        print(f"\n✅ 已添加标签「{new_name}」" + (f" (id={new_id})" if new_id else ""))
        return result
    except requests.exceptions.RequestException as e:
        out = {"status": "error", "message": f"新增标签失败: {e}"}
        if json_output:
            return json.dumps(out, ensure_ascii=False)
        print(f"❌ {out['message']}")
        return out
    except json.JSONDecodeError:
        out = {"status": "error", "message": "新增标签失败: 响应非 JSON"}
        if json_output:
            return json.dumps(out, ensure_ascii=False)
        print(f"❌ {out['message']}")
        return out


def _find_tag_id_by_name(entries: List[Dict[str, str]], name: str) -> Tuple[Optional[str], Optional[str]]:
    """返回 (tag_id, 匹配到的展示名)；同名多条取第一条。"""
    target = name.strip()
    if not target:
        return None, None
    tf = target.casefold()
    for e in entries:
        en = (e.get("name") or "").strip()
        if en.casefold() == tf:
            tid = (e.get("id") or "").strip()
            return (tid or None), en
    return None, None


def do_remove(
    space_id: str,
    page_id: str,
    json_output: bool,
    doc_space: bool,
    tag_id: Optional[str],
    tag_name: Optional[str],
) -> Any:
    emp_no, token = get_user_and_token(json_output=json_output, strict=True)
    tid = (tag_id or "").strip()
    tname = (tag_name or "").strip()

    if not tid and not tname:
        out = {"status": "error", "message": "删除标签需指定 --tag-id 或 --name"}
        if json_output:
            return json.dumps(out, ensure_ascii=False)
        print(f"❌ {out['message']}")
        return out

    if not tid:
        data, err = fetch_tags_raw(space_id, page_id, emp_no, token, doc_space)
        if err:
            out = {"status": "error", "message": err}
            if json_output:
                return json.dumps(out, ensure_ascii=False)
            print(f"❌ {err}")
            return out
        entries = _normalize_tag_entries(data.get("bo"))
        tid, matched = _find_tag_id_by_name(entries, tname)
        if not tid:
            out = {
                "status": "error",
                "message": f"未找到名为「{tname}」的标签，或该标签无 id（请改用 --tag-id）",
            }
            if json_output:
                return json.dumps(out, ensure_ascii=False)
            print(f"❌ {out['message']}")
            return out
        tname = matched or tname

    url = f"{_tag_base(doc_space)}/{page_id}/tags/{tid}"
    headers = _build_headers(emp_no, token, space_id, page_id, with_json=False)
    try:
        r = requests.delete(url, params=_tag_params(space_id), headers=headers, timeout=30)
        r.raise_for_status()
        text = (r.text or "").strip()
        resp = json.loads(text) if text else {"code": {"code": "0000"}}
        if not _api_ok(resp):
            em = _api_err_msg(resp, "删除标签失败")
            out = {"status": "error", "message": em, "code": (resp.get("code") or {}).get("code")}
            if json_output:
                return json.dumps(out, ensure_ascii=False)
            print(f"❌ {em}")
            return out
        result = {
            "status": "success",
            "action": "remove",
            "space_id": space_id,
            "page_id": page_id,
            "tag_id": tid,
            "page_url": f"https://i.zte.com.cn/#/shared/{space_id}/wiki/page/{page_id}/view",
        }
        if tname:
            result["tag_name"] = tname
        if json_output:
            return json.dumps(result, ensure_ascii=False)
        print(f"\n✅ 已删除标签 (id={tid})")
        return result
    except requests.exceptions.RequestException as e:
        out = {"status": "error", "message": f"删除标签失败: {e}"}
        if json_output:
            return json.dumps(out, ensure_ascii=False)
        print(f"❌ {out['message']}")
        return out
    except json.JSONDecodeError:
        out = {"status": "error", "message": "删除标签失败: 响应非 JSON"}
        if json_output:
            return json.dumps(out, ensure_ascii=False)
        print(f"❌ {out['message']}")
        return out


def do_search(
    space_id: str,
    tag_names: List[str],
    json_output: bool,
    offset: int,
    limit: int,
    page_id_for_headers: str = "",
) -> Any:
    """按标签汇聚查询同标签页面（1.0 advanced/tagConvergence）。"""
    names = [n.strip() for n in tag_names if (n or "").strip()]
    if not names:
        out = {"status": "error", "message": "search 需至少一个非空标签名"}
        if json_output:
            return json.dumps(out, ensure_ascii=False)
        print(f"❌ {out['message']}")
        return out

    emp_no, token = get_user_and_token(json_output=json_output, strict=True)
    bo, err = fetch_tag_convergence_raw(
        space_id, names, offset, limit, emp_no, token, page_id_for_headers=page_id_for_headers
    )
    if err:
        out = {"status": "error", "message": err}
        if json_output:
            _json_log(err)
            return json.dumps(out, ensure_ascii=False)
        print(f"❌ {err}")
        return out

    rows, total, current = _normalize_convergence_rows(bo)
    result = {
        "status": "success",
        "action": "search",
        "space_id": space_id,
        "tags": names,
        "offset": offset,
        "limit": limit,
        "total": total,
        "current": current,
        "pages": rows,
    }
    if json_output:
        return json.dumps(result, ensure_ascii=False)
    print("\n🔎 按标签汇聚的页面")
    print(f"空间: {space_id}  标签: {', '.join(names)}  共 {total} 条（本页 {len(rows)} 条）")
    if not rows:
        print("(无匹配页面)")
        return result
    print("-" * 48)
    for i, p in enumerate(rows, 1):
        title = p.get("content_title") or "—"
        cid = p.get("content_id") or "—"
        url = p.get("page_url") or ""
        print(f"{i}. {title}")
        print(f"   contentId={cid}")
        if url:
            print(f"   {url}")
    print("-" * 48)
    return result


def _exit_json_or_print(msg: str, json_output: bool) -> NoReturn:
    if json_output:
        _json_log(msg)
        print(json.dumps({"status": "error", "message": msg}, ensure_ascii=False))
    else:
        print(f"❌ {msg}")
    sys.exit(1)


def _resolve_search_space_page(args: argparse.Namespace) -> Tuple[str, Optional[str]]:
    """解析 search 所需 space_id 与可选 page_id（用于 apm 或 tags-from-page）。"""
    if args.tags_from_page:
        if args.url:
            sid, pid = parse_space_page_url(args.url)
            if not sid or not pid:
                _exit_json_or_print(
                    "无法从 URL 解析 spaceId 与 pageId（tags-from-page 需要完整页面 URL）",
                    args.json_output,
                )
            conv_space = (args.space_id or "").strip() or sid
            return conv_space, pid
        if args.space_id and args.page_id:
            return args.space_id.strip(), args.page_id.strip()
        _exit_json_or_print(
            "search --tags-from-page 需 --url 或同时提供 --space-id 与 --page-id",
            args.json_output,
        )
    if args.url:
        sid, pid = parse_space_page_url(args.url)
        if not sid:
            _exit_json_or_print("无法从 URL 解析 spaceId", args.json_output)
        return sid, (pid or "").strip() or None
    if args.space_id:
        return args.space_id.strip(), (args.page_id or "").strip() or None
    _exit_json_or_print("search 请提供 --space-id 或 --url", args.json_output)


def _collect_tags_from_page_for_search(
    space_id: str,
    page_id: str,
    doc_space: bool,
    json_output: bool,
) -> List[str]:
    emp_no, token = get_user_and_token(json_output=json_output, strict=True)
    data, err = fetch_tags_raw(space_id, page_id, emp_no, token, doc_space)
    if err:
        if json_output:
            _json_log(err)
            print(json.dumps({"status": "error", "message": err}, ensure_ascii=False))
        else:
            print(f"❌ {err}")
        sys.exit(1)
    entries = _normalize_tag_entries(data.get("bo"))
    tag_list = [e["name"] for e in entries if e.get("name")]
    if not tag_list:
        msg = "该页面当前无标签，无法按页面标签汇聚"
        if json_output:
            print(json.dumps({"status": "error", "message": msg}, ensure_ascii=False))
        else:
            print(f"❌ {msg}")
        sys.exit(1)
    return tag_list


def main() -> None:
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(description="空间页面标签：查询、新增（去重）、删除、按标签搜索页面")
    parser.add_argument("--url", type=str, help="页面 URL（list/add/remove 与 space-id/page-id 二选一；search 可仅解析空间）")
    parser.add_argument("--space-id", type=str, help="空间 ID")
    parser.add_argument("--page-id", type=str, help="页面 contentId")
    parser.add_argument(
        "--action",
        choices=("list", "add", "remove", "search"),
        required=True,
        help="list=查询 add=贴标签 remove=删除 search=按标签汇聚查同标签页面",
    )
    parser.add_argument("--name", type=str, help="add：标签名称；remove：按名称删除（需列表中带 id）")
    parser.add_argument("--tag-id", type=str, help="remove：标签 id")
    parser.add_argument(
        "--tag",
        action="append",
        default=[],
        metavar="NAME",
        help="search：标签名，可重复传入多个（与接口 body.tags 一致）",
    )
    parser.add_argument(
        "--tags-from-page",
        action="store_true",
        help="search：从指定页面读取当前标签列表作为搜索条件（需 --url 或 --space-id+--page-id）",
    )
    parser.add_argument("--offset", type=int, default=0, help="search：分页偏移，默认 0")
    parser.add_argument("--limit", type=int, default=20, help="search：每页条数，默认 20，最大 1000")
    parser.add_argument(
        "--doc-space",
        action="store_true",
        help="2.0 文档空间：走 zte-rdcloud-space-social，add 时带 type=0000；tags-from-page 时 list 标签也用此通道",
    )
    parser.add_argument("--json-output", action="store_true", help="stdout 仅输出 JSON")
    args = parser.parse_args()

    space_id: Optional[str] = None
    page_id: Optional[str] = None

    if args.action == "search":
        space_id, page_id = _resolve_search_space_page(args)
        if not args.tags_from_page and not args.tag:
            _exit_json_or_print("search 请提供至少一个 --tag，或使用 --tags-from-page", args.json_output)
    elif args.url:
        space_id, page_id = parse_space_page_url(args.url)
        if not space_id or not page_id:
            _exit_json_or_print("无法从 URL 解析 spaceId 与 pageId", args.json_output)
    elif args.space_id and args.page_id:
        space_id = args.space_id.strip()
        page_id = args.page_id.strip()
    else:
        _exit_json_or_print("请提供 --url 或 --space-id 与 --page-id", args.json_output)

    res: Any = None
    if args.action == "search":
        tag_list: List[str] = (
            _collect_tags_from_page_for_search(space_id, page_id, args.doc_space, args.json_output)
            if args.tags_from_page
            else list(args.tag or [])
        )
        assert page_id is not None or not args.tags_from_page
        hdr_page = (page_id or "").strip()
        res = do_search(
            space_id,
            tag_list,
            args.json_output,
            args.offset,
            args.limit,
            page_id_for_headers=hdr_page,
        )
    elif args.action == "list":
        assert page_id is not None
        res = do_list(space_id, page_id, args.json_output, args.doc_space)
    elif args.action == "add":
        if not args.name:
            msg = "add 需指定 --name"
            if args.json_output:
                print(json.dumps({"status": "error", "message": msg}, ensure_ascii=False))
            else:
                print(f"❌ {msg}")
            sys.exit(1)
        res = do_add(space_id, page_id, args.name, args.json_output, args.doc_space)
    else:
        if args.name and args.tag_id:
            msg = "remove 时请勿同时指定 --name 与 --tag-id"
            if args.json_output:
                print(json.dumps({"status": "error", "message": msg}, ensure_ascii=False))
            else:
                print(f"❌ {msg}")
            sys.exit(1)
        res = do_remove(
            space_id,
            page_id,
            args.json_output,
            args.doc_space,
            args.tag_id,
            args.name,
        )

    if args.json_output and isinstance(res, str):
        print(res)


if __name__ == "__main__":
    main()
