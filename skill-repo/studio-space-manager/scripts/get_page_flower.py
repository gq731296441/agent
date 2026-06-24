#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
查询空间页面点赞明细（flower detail），以及点赞/取消点赞（非切换：已赞再点赞不会取消，未赞取消会提示）。

用法：
    python3 get_page_flower.py --url "https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view"
    python3 get_page_flower.py --space-id <spaceId> --page-id <pageId>
    python3 get_page_flower.py --space-id <spaceId> --page-id <pageId> --offset 0 --limit 20 --json-output
    python3 get_page_flower.py --space-id <spaceId> --page-id <pageId> --like
    python3 get_page_flower.py --space-id <spaceId> --page-id <pageId> --unlike
"""

import argparse
import json
import os
import re
import sys
import time

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from system_param import build_icenter_api_url, build_system_param, get_user_and_token
from _platform_utils import configure_stdio_utf8, get_platform_and_user_agent

FLOWER_API_BASE = build_icenter_api_url("/zte-rd-icenter-extend/content")


def _json_mode_log(message: str) -> None:
    """--json-output 时向 stderr 打一行日志，避免污染 stdout 的纯 JSON。"""
    print(f"[get_page_flower] {message}", file=sys.stderr)


def parse_space_page_url(url: str):
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


def _flower_query_string(space_id: str) -> str:
    timestamp = int(time.time() * 1000)
    system_param = build_system_param("space")
    return (
        f"t={timestamp}&spaceId={space_id}&environmentType=browser&system={system_param}"
    )


def _build_flower_headers(
    emp_no: str, token: str, space_id: str, page_id: str, *, with_json_content_type: bool
):
    _, user_agent = get_platform_and_user_agent()
    headers = {
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
    if with_json_content_type:
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


def _flower_detail_url(page_id: str, space_id: str, offset: int, limit: int) -> str:
    qs = _flower_query_string(space_id)
    return (
        f"{FLOWER_API_BASE}/{page_id}/flower/detail"
        f"?offset={int(offset)}&limit={int(limit)}&{qs}"
    )


def _flower_toggle_url(page_id: str, space_id: str) -> str:
    qs = _flower_query_string(space_id)
    return f"{FLOWER_API_BASE}/{page_id}/flower/?{qs}"


def _api_code_ok(data: dict) -> bool:
    return (data.get("code") or {}).get("code") == "0000"


def _api_error_message(data: dict, default: str) -> str:
    return (data.get("code") or {}).get("msgId") or default


def _auth_error_output(json_output: bool):
    msg = (
        "无法获取用户账号和Token，请确保已设置 coclaw_empno 和 coclaw_token 环境变量，"
        "如仍报错请重启 Co-Claw 服务后重试"
    )
    out = {"status": "error", "message": msg}
    if json_output:
        _json_mode_log(f"错误: {msg}")
        return json.dumps(out, ensure_ascii=False)
    print(f"❌ {msg}")
    return out


def _ensure_auth(json_output: bool):
    emp_no, token = get_user_and_token(strict=False)
    emp_no = (emp_no or "").strip()
    token = (token or "").strip()
    if not emp_no or not token:
        return None, None, _auth_error_output(json_output)
    return emp_no, token, None


def _current_user_in_flower_list(bo: dict, emp_no: str) -> bool:
    """结合接口 isFlower 与点赞列表 rows 判断当前用户是否已点赞。"""
    if bo.get("isFlower"):
        return True
    emp = (emp_no or "").strip()
    for row in bo.get("rows") or []:
        if str((row.get("employeeShortId") or "")).strip() == emp:
            return True
    return False


def _handle_request_exception(
    exc: Exception, prefix: str, json_output: bool, is_timeout: bool = False
):
    if is_timeout or isinstance(exc, requests.exceptions.Timeout):
        msg = f"{prefix}: 请求超时"
    elif isinstance(exc, requests.exceptions.RequestException):
        msg = f"{prefix}: 网络请求错误 - {exc}"
    else:
        msg = f"{prefix}: {exc}"
    out = {"status": "error", "message": msg}
    if json_output:
        _json_mode_log(f"错误: {msg}")
        return json.dumps(out, ensure_ascii=False)
    print(f"❌ {msg}")
    return out


def _flower_skip_result(
    space_id: str,
    page_id: str,
    *,
    intent: str,
    had_flower: bool,
    skip_message: str,
    json_output: bool,
):
    """已满足目标状态时直接返回，不调 POST/PUT。"""
    result = {
        "status": "success",
        "space_id": space_id,
        "page_id": page_id,
        "intent": intent,
        "skipped": True,
        "message": skip_message,
        "had_flower_before": had_flower,
        "is_flower_after": had_flower,
        "page_url": f"https://i.zte.com.cn/#/shared/{space_id}/wiki/page/{page_id}/view",
    }
    if json_output:
        _json_mode_log(f"跳过: intent={intent} {skip_message}")
        return json.dumps(result, ensure_ascii=False)
    print(f"\nℹ️ {skip_message}")
    print(f"页面链接: {result['page_url']}")
    print(f"当前用户是否已点赞: {'是' if had_flower else '否'}")
    return result


def _sync_page_flower_state(
    space_id: str, page_id: str, *, target_liked: bool, json_output: bool = False
):
    """
    按意图同步点赞状态（非切换）：
    - target_liked=True（点赞）：已赞则提示并保持已赞，不调 PUT；未赞则 POST。
    - target_liked=False（取消）：未赞则提示不调 POST；已赞则 PUT 取消。
    """
    intent = "like" if target_liked else "unlike"
    auth = _ensure_auth(json_output)
    emp_no, token, err = auth
    if err is not None:
        return err

    if not space_id or not page_id:
        out = {"status": "error", "message": "缺少参数：space_id 或 page_id"}
        if json_output:
            _json_mode_log(f"错误: {out['message']}")
            return json.dumps(out, ensure_ascii=False)
        print(f"❌ {out['message']}")
        return out

    detail_headers = _build_flower_headers(
        emp_no, token, space_id, page_id, with_json_content_type=True
    )
    toggle_headers = _build_flower_headers(
        emp_no, token, space_id, page_id, with_json_content_type=False
    )

    if json_output:
        _json_mode_log(
            f"点赞操作 intent={intent}: 查询 flower/detail space_id={space_id} page_id={page_id}"
        )

    try:
        detail_url = _flower_detail_url(page_id, space_id, 0, 1)
        resp = requests.get(detail_url, headers=detail_headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not _api_code_ok(data):
            msg = _api_error_message(data, "查询点赞状态失败")
            code = (data.get("code") or {}).get("code") or ""
            out = {"status": "error", "message": msg, "code": code}
            if json_output:
                _json_mode_log(f"错误: {msg} code={code}")
                return json.dumps(out, ensure_ascii=False)
            print(f"❌ {msg}")
            return out

        bo = data.get("bo") or {}
        had_flower = _current_user_in_flower_list(bo, emp_no)

        if target_liked and had_flower:
            return _flower_skip_result(
                space_id,
                page_id,
                intent=intent,
                had_flower=True,
                skip_message="您已点过赞，未取消点赞，当前仍为已点赞状态。",
                json_output=json_output,
            )
        if not target_liked and not had_flower:
            return _flower_skip_result(
                space_id,
                page_id,
                intent=intent,
                had_flower=False,
                skip_message="您尚未点赞，无需取消。",
                json_output=json_output,
            )

        method = "POST" if target_liked else "PUT"
        api_action = "like" if target_liked else "cancel"
        if json_output:
            _json_mode_log(
                f"当前已点赞={had_flower}，将执行 {method} flower api_action={api_action}"
            )

        toggle_url = _flower_toggle_url(page_id, space_id)
        if target_liked:
            toggle_resp = requests.post(toggle_url, headers=toggle_headers, timeout=30)
        else:
            toggle_resp = requests.put(toggle_url, headers=toggle_headers, timeout=30)
        toggle_resp.raise_for_status()
        toggle_data = toggle_resp.json()
        if not _api_code_ok(toggle_data):
            msg = _api_error_message(toggle_data, "点赞操作失败")
            code = (toggle_data.get("code") or {}).get("code") or ""
            out = {
                "status": "error",
                "message": msg,
                "code": code,
                "had_flower_before": had_flower,
                "intent": intent,
                "http_method": method,
            }
            if json_output:
                _json_mode_log(f"错误: {msg} code={code}")
                return json.dumps(out, ensure_ascii=False)
            print(f"❌ {msg}")
            return out

        new_is_flower = target_liked
        result = {
            "status": "success",
            "space_id": space_id,
            "page_id": page_id,
            "intent": intent,
            "skipped": False,
            "http_method": method,
            "had_flower_before": had_flower,
            "is_flower_after": new_is_flower,
            "page_url": f"https://i.zte.com.cn/#/shared/{space_id}/wiki/page/{page_id}/view",
        }
        if json_output:
            _json_mode_log(
                f"成功: intent={intent} is_flower_after={new_is_flower}"
            )
            return json.dumps(result, ensure_ascii=False)

        verb = "已点赞" if target_liked else "已取消点赞"
        print(f"\n✅ {verb}")
        print(f"页面链接: {result['page_url']}")
        print(f"当前用户是否已点赞: {'是' if new_is_flower else '否'}")
        return result
    except requests.exceptions.Timeout:
        return _handle_request_exception(
            None, "点赞操作", json_output, is_timeout=True
        )
    except requests.exceptions.RequestException as e:
        return _handle_request_exception(e, "点赞操作", json_output)
    except json.JSONDecodeError:
        out = {"status": "error", "message": "点赞操作失败: 返回数据解析错误"}
        if json_output:
            _json_mode_log(f"错误: {out['message']}")
            return json.dumps(out, ensure_ascii=False)
        print(f"❌ {out['message']}")
        return out


def like_page_flower(space_id: str, page_id: str, json_output: bool = False):
    """点赞：已赞则仅提示并保持已赞，不会改为未赞。"""
    return _sync_page_flower_state(
        space_id, page_id, target_liked=True, json_output=json_output
    )


def unlike_page_flower(space_id: str, page_id: str, json_output: bool = False):
    """取消点赞：未赞则仅提示，不会误点赞。"""
    return _sync_page_flower_state(
        space_id, page_id, target_liked=False, json_output=json_output
    )


def get_page_flower_detail(
    space_id: str,
    page_id: str,
    offset: int = 0,
    limit: int = 10,
    json_output: bool = False,
):
    auth = _ensure_auth(json_output)
    emp_no, token, err = auth
    if err is not None:
        return err

    if not space_id or not page_id:
        out = {"status": "error", "message": "缺少参数：space_id 或 page_id"}
        if json_output:
            _json_mode_log(f"错误: {out['message']}")
            return json.dumps(out, ensure_ascii=False)
        print(f"❌ {out['message']}")
        return out

    headers = _build_flower_headers(
        emp_no, token, space_id, page_id, with_json_content_type=True
    )
    url = _flower_detail_url(page_id, space_id, offset, limit)

    if json_output:
        _json_mode_log(
            f"调用 flower/detail space_id={space_id} page_id={page_id} "
            f"offset={int(offset)} limit={int(limit)}"
        )

    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not _api_code_ok(data):
            msg = _api_error_message(data, "查询点赞明细失败")
            code = (data.get("code") or {}).get("code") or ""
            out = {"status": "error", "message": msg, "code": code}
            if json_output:
                _json_mode_log(f"错误: {msg} code={code or ''}")
                return json.dumps(out, ensure_ascii=False)
            print(f"❌ {msg}")
            return out

        bo = data.get("bo") or {}
        result = {
            "status": "success",
            "space_id": space_id,
            "page_id": page_id,
            "count": int(bo.get("count") or 0),
            "is_flower": bool(bo.get("isFlower")),
            "rows": bo.get("rows") or [],
            "page_url": f"https://i.zte.com.cn/#/shared/{space_id}/wiki/page/{page_id}/view",
        }
        if json_output:
            _json_mode_log(
                f"成功: count={result['count']} is_flower={result['is_flower']} "
                f"rows={len(result['rows'])}"
            )
            return json.dumps(result, ensure_ascii=False)

        print("\n📋 页面点赞信息")
        print(f"页面链接: {result['page_url']}")
        print(f"点赞总数: {result['count']}")
        print(f"当前用户是否已点赞: {'是' if result['is_flower'] else '否'}")
        if result["rows"]:
            print("-" * 72)
            for i, row in enumerate(result["rows"], 1):
                print(
                    f"{i}. {(row.get('employeeName') or '').strip()} ({(row.get('employeeShortId') or '').strip()})"
                )
                print(f"   部门: {(row.get('department') or '').strip()}")
                print(f"   点赞时间: {(row.get('favTime') or '').strip()}")
                print(f"   头像: {(row.get('headIcon') or '').strip()}")
                print("-" * 72)
        return result
    except requests.exceptions.Timeout:
        return _handle_request_exception(
            None, "查询点赞明细", json_output, is_timeout=True
        )
    except requests.exceptions.RequestException as e:
        return _handle_request_exception(e, "查询点赞明细", json_output)
    except json.JSONDecodeError:
        out = {"status": "error", "message": "查询点赞明细失败: 返回数据解析错误"}
        if json_output:
            _json_mode_log(f"错误: {out['message']}")
            return json.dumps(out, ensure_ascii=False)
        print(f"❌ {out['message']}")
        return out


def main():
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(description="查询页面点赞明细，或点赞/取消点赞（--like / --unlike）")
    parser.add_argument("--url", type=str, help="页面URL")
    parser.add_argument("--space-id", type=str, help="空间ID")
    parser.add_argument("--page-id", type=str, help="页面ID")
    parser.add_argument("--offset", type=int, default=0, help="分页起始偏移，默认0")
    parser.add_argument("--limit", type=int, default=10, help="分页条数，默认10")
    parser.add_argument(
        "--like",
        action="store_true",
        help="点赞：未赞则点赞；已赞则提示已点过赞且不取消",
    )
    parser.add_argument(
        "--unlike",
        action="store_true",
        help="取消点赞：已赞则取消；未赞则提示尚未点赞",
    )
    parser.add_argument("--json-output", action="store_true", help="JSON格式输出（用于程序调用）")
    args = parser.parse_args()

    if args.url:
        space_id, page_id = parse_space_page_url(args.url)
        if not space_id or not page_id:
            msg = "无法从URL解析spaceId和pageId"
            if args.json_output:
                _json_mode_log(f"错误: {msg}")
                print(
                    json.dumps({"status": "error", "message": msg}, ensure_ascii=False)
                )
            else:
                print(f"❌ {msg}")
            sys.exit(1)
    elif args.space_id and args.page_id:
        space_id = args.space_id.strip()
        page_id = args.page_id.strip()
    else:
        msg = "请提供 --url 或 --space-id + --page-id 参数"
        if args.json_output:
            _json_mode_log(f"错误: {msg}")
            print(json.dumps({"status": "error", "message": msg}, ensure_ascii=False))
        else:
            print(f"❌ {msg}")
        sys.exit(1)

    if args.like and args.unlike:
        msg = "不能同时使用 --like 与 --unlike"
        if args.json_output:
            _json_mode_log(f"错误: {msg}")
            print(json.dumps({"status": "error", "message": msg}, ensure_ascii=False))
        else:
            print(f"❌ {msg}")
        sys.exit(1)
    if args.like:
        result = like_page_flower(
            space_id=space_id, page_id=page_id, json_output=args.json_output
        )
    elif args.unlike:
        result = unlike_page_flower(
            space_id=space_id, page_id=page_id, json_output=args.json_output
        )
    else:
        result = get_page_flower_detail(
            space_id=space_id,
            page_id=page_id,
            offset=args.offset,
            limit=args.limit,
            json_output=args.json_output,
        )
    if args.json_output and isinstance(result, str):
        print(result)


if __name__ == "__main__":
    main()
