#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
空间模板（2.0 文档空间）：列表、模板详情（内容）、基于模板创建文档。

对应对外接口见仓库 all/接口.md — 内容管理（2.0）子领域 #8 / #9 / #10（/studio-ispace/doc）。
本脚本使用与前端一致的 icenterapi 内部路径（zte-rdcloud-space-content）。

用法示例：
  python3 space_template_page.py list --space-id <spaceId>
  python3 space_template_page.py get --space-id <spaceId> --template-id <id>
  python3 space_template_page.py create --space-id <spaceId> --parent-id <parentId> \\
      --template-id <id> --title "新页面标题"
"""

import argparse
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from system_param import build_icenter_api_url, build_system_param, get_user_and_token
from _platform_utils import configure_stdio_utf8

try:
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

DOC_BASE = build_icenter_api_url("/zte-rdcloud-space-content")

AUTH_ERROR = "缺少 coclaw_empno / coclaw_token，请设置环境变量后重试。"


def _json_log(msg: str) -> None:
    print(f"[space_template_page] {msg}", file=sys.stderr)


def _get_user_token(json_output: bool) -> Tuple[str, str]:
    uid, tok = get_user_and_token(json_output=json_output, strict=False)
    return (uid or "").strip(), (tok or "").strip()


def _http_headers(emp_no: str, token: str) -> Dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://i.zte.com.cn",
        "Referer": "https://i.zte.com.cn/",
        "User-Agent": "co-claw-skill-studio-space-manager",
        "X-Auth-Value": token,
        "X-Emp-No": emp_no,
        "X-Lang-Id": "zh_CN",
        "X-Tenant-Id": "ZTE",
    }


def _parse_parent_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    if not (url or "").strip():
        return None, None
    patterns = [
        r"/shared/([a-f0-9]+)/wiki/page/([a-f0-9]+)/view",
        r"/space/([a-f0-9]+)/wiki/page/([a-f0-9]+)/view",
        r"/ispace/#/space/([a-f0-9]+)/wiki/page/([a-f0-9]+)/view",
        r"#/space/([a-f0-9]+)/wiki/page/([a-f0-9]+)/view",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1), m.group(2)
    return None, None


def _unwrap_templates_list(data: Any) -> List[Any]:
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    bo = data.get("bo")
    if isinstance(bo, list):
        return bo
    if isinstance(bo, dict):
        for key in ("rows", "list", "templates", "contentList"):
            v = bo.get(key)
            if isinstance(v, list):
                return v
    for key in ("rows", "list", "templates"):
        v = data.get(key)
        if isinstance(v, list):
            return v
    return []


def _template_body_fields(bo: Any) -> Tuple[str, Dict[str, Any]]:
    """从模板详情 bo 中提取正文与完整元数据。"""
    if not isinstance(bo, dict):
        return "", {}
    raw = (
        bo.get("rawContent")
        or bo.get("contentBody")
        or bo.get("text")
        or bo.get("html")
        or ""
    )
    if not isinstance(raw, str):
        raw = str(raw) if raw is not None else ""
    return raw, bo


def list_templates(space_id: str, emp_no: str, token: str) -> Dict[str, Any]:
    url = f"{DOC_BASE}/templates/"
    try:
        r = requests.get(
            url,
            headers=_http_headers(emp_no, token),
            params={"spaceId": space_id},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as ex:
        return {"status": "error", "message": f"请求失败: {ex}"}

    code = (data.get("code") or {}).get("code") if isinstance(data, dict) else None
    if code not in (None, "0000"):
        return {
            "status": "error",
            "message": (data.get("code") or {}).get("msg", "接口返回非成功"),
            "code": code,
            "raw": data,
        }

    items = _unwrap_templates_list(data)
    slim = []
    for it in items:
        if not isinstance(it, dict):
            continue
        tid = it.get("id") or it.get("templateId") or ""
        slim.append(
            {
                "id": tid,
                "title": it.get("title") or it.get("name") or "",
                "description": it.get("description") or "",
                "spaceId": it.get("spaceId") or "",
            }
        )
    return {
        "status": "success",
        "space_id": space_id,
        "count": len(slim),
        "templates": slim,
        "raw": data,
    }


def get_template_detail(
    space_id: str,
    template_id: str,
    emp_no: str,
    token: str,
    data_type: str = "HTML",
) -> Dict[str, Any]:
    url = f"{DOC_BASE}/templates/{template_id}/edit"
    params: Dict[str, str] = {
        "spaceId": space_id,
        "dataType": data_type or "HTML",
    }
    try:
        r = requests.get(
            url,
            headers=_http_headers(emp_no, token),
            params=params,
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as ex:
        return {"status": "error", "message": f"请求失败: {ex}"}

    code = (data.get("code") or {}).get("code") if isinstance(data, dict) else None
    if code not in (None, "0000"):
        return {
            "status": "error",
            "message": (data.get("code") or {}).get("msg", "接口返回非成功"),
            "code": code,
            "raw": data,
        }

    bo = data.get("bo", data) if isinstance(data, dict) else data
    body, meta = _template_body_fields(bo)
    tid = template_id
    if isinstance(meta, dict):
        tid = meta.get("id") or meta.get("templateId") or tid
    title = meta.get("title") if isinstance(meta, dict) else ""
    return {
        "status": "success",
        "space_id": space_id,
        "template_id": tid,
        "title": title or "",
        "content": body,
        "raw": data,
    }


def create_from_template(
    space_id: str,
    parent_id: str,
    template_id: str,
    emp_no: str,
    token: str,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    headers = _http_headers(emp_no, token)
    t_ms = int(time.time() * 1000)
    system_param = build_system_param("space")
    url = (
        f"{DOC_BASE}/content"
        f"?spaceId={space_id}"
        f"&environmentType=browser"
        f"&system={system_param}"
        f"&t={t_ms}"
    )
    body: Dict[str, Any] = {
        "parentId": parent_id,
        "spaceId": space_id,
        "templateId": template_id,
    }
    if (title or "").strip():
        body["title"] = title.strip()

    try:
        r = requests.post(url, headers=headers, json=body, timeout=45)
        r.raise_for_status()
        data = r.json()
    except Exception as ex:
        return {"status": "error", "message": f"请求失败: {ex}"}

    code = (data.get("code") or {}).get("code") if isinstance(data, dict) else None
    if code not in (None, "0000"):
        return {
            "status": "error",
            "message": (data.get("code") or {}).get("msg", "创建失败"),
            "code": code,
            "raw": data,
        }

    bo = data.get("bo") if isinstance(data, dict) else None
    page_id = ""
    if isinstance(bo, dict):
        page_id = (bo.get("contentId") or bo.get("id") or "").strip()
    elif isinstance(bo, str):
        page_id = bo.strip()
    link = ""
    if page_id:
        link = (
            f"https://i.zte.com.cn/index/ispace/#/space/{space_id}/wiki/page/"
            f"{page_id}/view"
        )
    return {
        "status": "success",
        "message": "已基于模板创建页面",
        "space_id": space_id,
        "parent_id": parent_id,
        "template_id": template_id,
        "page_id": page_id,
        "page_url": link,
        "raw": data,
    }


def _print_human_list(result: Dict[str, Any]) -> None:
    if result.get("status") != "success":
        print(f"❌ {result.get('message', '未知错误')}")
        return
    print(f"空间 {result.get('space_id')} 模板共 {result.get('count', 0)} 个：")
    for i, t in enumerate(result.get("templates") or [], 1):
        print(f"  {i}. [{t.get('id')}] {t.get('title') or '(无标题)'}")


def _print_human_get(result: Dict[str, Any], max_preview: int = 800) -> None:
    if result.get("status") != "success":
        print(f"❌ {result.get('message', '未知错误')}")
        return
    print(f"模板 ID: {result.get('template_id')}")
    print(f"标题: {result.get('title') or '(无)'}")
    content = result.get("content") or ""
    if len(content) > max_preview:
        print(f"内容（前 {max_preview} 字符）:\n{content[:max_preview]}...")
    else:
        print(f"内容:\n{content}")


def _print_human_create(result: Dict[str, Any]) -> None:
    if result.get("status") != "success":
        print(f"❌ {result.get('message', '未知错误')}")
        return
    print(result.get("message", "成功"))
    if result.get("page_url"):
        print(f"链接: {result['page_url']}")
    if result.get("page_id"):
        print(f"页面 ID: {result['page_id']}")


def _run_create(
    space_id: str,
    parent_id: str,
    parent_url: str,
    template_id: str,
    title: Optional[str],
    emp_no: str,
    token: str,
) -> Dict[str, Any]:
    pid = (parent_id or "").strip()
    if parent_url:
        ps, parsed = _parse_parent_url(parent_url)
        if not parsed:
            return {"status": "error", "message": "无法从 --parent-url 解析父页面 ID"}
        if ps and ps != space_id:
            return {
                "status": "error",
                "message": "URL 中空间 %s 与 --space-id %s 不一致" % (ps, space_id),
            }
        pid = parsed
    if not pid:
        return {"status": "error", "message": "请提供 --parent-id 或 --parent-url"}
    return create_from_template(
        space_id, pid, template_id, emp_no, token, title=title or None
    )


def main() -> int:
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(
        description="2.0 文档空间：模板列表、模板内容、基于模板创建文档"
    )
    parser.add_argument(
        "--json-output",
        action="store_true",
        help="仅向 stdout 输出 JSON",
    )
    sub = parser.add_subparsers(dest="action")

    p_list = sub.add_parser("list", help="获取空间下模板列表")
    p_list.add_argument("--space-id", required=True, help="空间 ID")

    p_get = sub.add_parser("get", help="获取指定模板详情（含模板正文）")
    p_get.add_argument("--space-id", required=True)
    p_get.add_argument("--template-id", required=True, help="模板 ID（列表接口返回的 id）")
    p_get.add_argument(
        "--data-type",
        default="HTML",
        help="模板内容返回类型，默认 HTML",
    )

    p_create = sub.add_parser("create", help="基于模板在指定父节点下创建文档")
    p_create.add_argument("--space-id", required=True)
    p_create.add_argument(
        "--parent-id",
        default="",
        help="父页面 contentId；与 --parent-url 二选一",
    )
    p_create.add_argument(
        "--parent-url",
        default="",
        help="父页面完整链接，解析出 space-id（须与 --space-id 一致）与 parent-id",
    )
    p_create.add_argument("--template-id", required=True)
    p_create.add_argument(
        "--title",
        default="",
        help="新文档标题；不传则由服务端使用模板标题",
    )

    args = parser.parse_args()
    if not getattr(args, "action", None):
        parser.print_help()
        print("\n请指定子命令: list | get | create", file=sys.stderr)
        sys.exit(2)
    emp_no, token = _get_user_token(args.json_output)
    if not emp_no or not token:
        err = {"status": "error", "message": AUTH_ERROR}
        if args.json_output:
            _json_log(AUTH_ERROR)
            print(json.dumps(err, ensure_ascii=False))
        else:
            print(f"❌ {AUTH_ERROR}")
        return 1

    if args.action == "list":
        out = list_templates(args.space_id, emp_no, token)
    elif args.action == "get":
        out = get_template_detail(
            args.space_id,
            args.template_id,
            emp_no,
            token,
            data_type=args.data_type,
        )
    else:
        out = _run_create(
            args.space_id,
            args.parent_id,
            args.parent_url,
            args.template_id,
            args.title or None,
            emp_no,
            token,
        )

    if args.json_output:
        payload = dict(out)
        if payload.get("status") == "success":
            payload.pop("raw", None)
        print(json.dumps(payload, ensure_ascii=False))
    elif args.action == "list":
        _print_human_list(out)
    elif args.action == "get":
        _print_human_get(out)
    else:
        _print_human_create(out)

    return 0 if out.get("status") == "success" else 2


if __name__ == "__main__":
    sys.exit(main())
