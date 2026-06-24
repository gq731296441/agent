#!/usr/bin/env python3
"""
统一 HTTP 请求入口：集中管理 requests 调用与重试策略。

- 仅对网络异常/超时等 RequestException 做重试（默认 3 次尝试，含首次）。
- 不对 HTTP 非 2xx 自动重试；由上层根据 status_code / raise_for_status 决定行为。
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

import requests


_SESSION: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    """
    获取模块级共享 Session（惰性创建）。

    说明：
    - Session 用于连接复用与 cookie 自动管理（按 domain 规则发送）。
    - 若未来需要多账号/多配置隔离，可将 session 下沉为显式入参。
    """
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
        # 默认请求头：由 Session 统一维护，避免各模块重复配置
        _SESSION.headers.update({
            "Referer": "https://zmail.zte.com.cn/zte-km-zmail-webui/indexSimplePlus.html",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        })
    return _SESSION


def send_request(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    data: Any = None,
    json: Any = None,
    timeout: Optional[float] = None,
    verify: Optional[bool] = None,
    allow_redirects: Optional[bool] = None,
    stream: bool = False,
    retry_times: int = 3,
    retry_backoff_s: Optional[list] = None,
) -> requests.Response:
    """
    统一发起 HTTP 请求，失败按策略重试。

    :param retry_times: 总尝试次数（含首次），默认 3；小于 1 将按 1 处理。
    :param retry_backoff_s: 每次重试前的等待秒数数组；未传则默认 [0.2, 0.5, 1.0]。
    :return: requests.Response（不在此处调用 raise_for_status）。
    :raises: requests.exceptions.RequestException（最后一次失败时原样抛出）。
    """
    m = (method or "").strip().upper()
    if not m:
        raise ValueError("method 不能为空")
    if not (url and str(url).strip()):
        raise ValueError("url 不能为空")

    tries = int(retry_times) if retry_times is not None else 3
    if tries < 1:
        tries = 1

    backoffs = retry_backoff_s if retry_backoff_s is not None else [0.2, 0.5, 1.0]
    if not isinstance(backoffs, list):
        backoffs = [0.2, 0.5, 1.0]

    request_kwargs: Dict[str, Any] = {
        "method": m,
        "url": str(url).strip(),
        "headers": headers,
        "params": params,
        "data": data,
        "json": json,
        "timeout": timeout,
        "stream": stream,
    }
    # 仅当显式传入时才覆盖 requests 默认行为
    if verify is not None:
        request_kwargs["verify"] = verify
    if allow_redirects is not None:
        request_kwargs["allow_redirects"] = allow_redirects

    session = _get_session()
    last_exc: Optional[Exception] = None
    for attempt in range(tries):
        try:
            return session.request(**request_kwargs)
        except requests.exceptions.RequestException as e:
            last_exc = e
            if attempt >= tries - 1:
                raise
            # 退避：按 attempt 取 backoff，超出则使用最后一个
            if backoffs:
                idx = attempt if attempt < len(backoffs) else (len(backoffs) - 1)
                wait_s = backoffs[idx]
                try:
                    if wait_s and float(wait_s) > 0:
                        time.sleep(float(wait_s))
                except Exception:
                    # 退避异常不影响重试
                    pass

    # 理论上不可达：for 循环里 attempt==last 会 raise；这里兜底
    if last_exc:
        raise last_exc
    raise RuntimeError("send_request 未能发起请求")


def request_get(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    timeout: Optional[float] = None,
    verify: Optional[bool] = None,
    allow_redirects: Optional[bool] = None,
    stream: bool = False,
    retry_times: int = 3,
    retry_backoff_s: Optional[list] = None,
) -> requests.Response:
    """便捷 GET：默认按 send_request 的策略重试 3 次。"""
    return send_request(
        "GET",
        url,
        headers=headers,
        params=params,
        timeout=timeout,
        verify=verify,
        allow_redirects=allow_redirects,
        stream=stream,
        retry_times=retry_times,
        retry_backoff_s=retry_backoff_s,
    )


def request_post(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    data: Any = None,
    json: Any = None,
    timeout: Optional[float] = None,
    verify: Optional[bool] = None,
    allow_redirects: Optional[bool] = None,
    stream: bool = False,
    retry_times: int = 3,
    retry_backoff_s: Optional[list] = None,
) -> requests.Response:
    """便捷 POST：默认按 send_request 的策略重试 3 次。"""
    return send_request(
        "POST",
        url,
        headers=headers,
        params=params,
        data=data,
        json=json,
        timeout=timeout,
        verify=verify,
        allow_redirects=allow_redirects,
        stream=stream,
        retry_times=retry_times,
        retry_backoff_s=retry_backoff_s,
    )
