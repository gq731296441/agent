#!/usr/bin/env python3
"""
Tingyun 听云埋点上报：向 log.dt.zte.com.cn 发送 iCenter 页面访问等事件。
对应前端 fetch 的 POST /itpdcs/tingyun/event 请求。
"""

import json
import os
import time
import requests
import urllib.parse
from typing import Any, Dict, Optional, Tuple

from config import APP_VERSION
from request_client import request_post

# 以下由调用方传入时的默认值（仅用于 main 等未显式传参时）
_DEFAULT_EVENT_ID = "iCenter_zMail_Skills"
_DEFAULT_TAG = ""

_BASE_URL = "https://log.dt.zte.com.cn/itpdcs/tingyun/event"
_DEFAULT_APP_CODE = "130000198208"
_DEFAULT_APP_NAME = "zMailSkills"
# 注意：每次更新版本号时，必须保证与 `oa-zmail-manage/SKILL.md` 的 metadata.version 完全一致
_DEFAULT_APP_VERSION = APP_VERSION
_DEFAULT_KEY = "100000432200"


def _build_log_properties(props: Dict[str, Any]) -> Dict[str, Any]:
    """构建上报属性：params 记录参数名集合；status/duration_ms/failure_reason 独立保留原值。"""
    if not props:
        return {}
    result = {}
    if "status" in props:
        result["status"] = props.get("status")
    if "duration_ms" in props:
        result["duration_ms"] = props.get("duration_ms")
    # 执行失败原因：截断避免埋点 body 过大
    if "failure_reason" in props and props.get("failure_reason") is not None:
        fr = str(props.get("failure_reason") or "")
        result["failure_reason"] = fr[:512] if len(fr) > 512 else fr
    _skip_params = ("command", "status", "duration_ms", "failure_reason")
    keys = [
        str(k) for k in props.keys()
        if str(k).strip() and str(k) not in _skip_params
    ]
    result["params"] = ",".join(keys)
    return result


def _default_headers() -> Dict[str, str]:
    """请求头：仅保留服务端调用所需。sec-* / accept-language 等为浏览器自动添加，非必须。"""
    return {
        "content-type": "text/plain;charset=UTF-8",  # 必选：POST body 为 JSON 文本
        "accept": "*/*",
    }


def send_page_load_event(
    event_id: Optional[str] = None,
    tag: str = _DEFAULT_TAG,
    custom_properties: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Optional[str]]:
    """
    发送 iCenter 页面加载 PV 事件。
    工号从环境变量 coclaw_empno 获取，获取不到则不执行。内部异常不抛出，统一返回 (False, 错误信息)。

    :param event_id: 事件 ID，不传则使用默认值 iCenter_Framework_Skills
    :param tag: 事件标签，调用方传入，不能为空
    :param custom_properties: 调用侧参数字典。上报 params（参数名集合），并单独保留 status、duration_ms、failure_reason 的值
    :return: (是否成功, 错误信息)
    """
    zmail_host = os.environ.get("ZMAIL_HOST", "zmail.zte.com.cn")
    if zmail_host != "zmail.zte.com.cn":
        return True, None

    try:
        emp_no = (os.getenv("coclaw_empno") or "").strip()
        if not emp_no:
            return False, "未配置 coclaw_empno，不执行上报"
        event_id = (event_id or _DEFAULT_EVENT_ID).strip() or _DEFAULT_EVENT_ID
        if not (tag and str(tag).strip()):
            return False, "tag 不能为空"

        optime = int(time.time() * 1000)
        properties = {
            "empNo": emp_no,
            "app_code": _DEFAULT_APP_CODE,
            "app_name": _DEFAULT_APP_NAME,
            "app_version": _DEFAULT_APP_VERSION,
            "optime": optime,
        }
        if custom_properties:
            properties.update(_build_log_properties(custom_properties))
        body = {
            "event_id": event_id.strip(),
            "tag": tag.strip(),
            "properties": properties,
        }

        url = f"{_BASE_URL}?{urllib.parse.urlencode({'key': _DEFAULT_KEY})}"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        resp = request_post(url, data=data, headers=_default_headers(), timeout=10)
        if 200 <= resp.status_code < 300:
            return True, None
        return False, f"HTTP {resp.status_code}"
    except requests.exceptions.RequestException as e:
        reason = getattr(e, "reason", None) or str(e)
        return False, str(reason)
    except Exception as e:
        return False, str(e)

