"""本地鉴权 MCP 测试：验证 Agent 默认注入的 x-emp-no / x-auth-value。

用法：
    # 终端 1：启动 MCP
    python mcp_server/tool/auth_echo_mcp.py

    # 终端 2：跑测试（走与 Agent 相同的 McpToolService 建连路径）
    python mcp_server/tool/test_auth_echo_mcp.py

    # 一条命令：自动拉起 MCP 再测
    python mcp_server/tool/test_auth_echo_mcp.py --spawn
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from mcp_server.mcp_manager import (  # noqa: E402
    MCP_HEADER_AUTH_VALUE,
    MCP_HEADER_EMP_NO,
    McpToolService,
)

DEFAULT_URL = "http://127.0.0.1:6003/mcp"
DEFAULT_EMP_NO = "10344649"
DEFAULT_TOKEN = "local-test-token"


def parse_host_port(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    if not parsed.hostname:
        raise ValueError(f"无法解析 URL: {url}")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.hostname, port


def wait_for_port(host: str, port: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.2)
    return False


async def assert_unauthorized_without_headers(url: str) -> None:
    async with httpx.AsyncClient(timeout=8.0, trust_env=False) as client:
        response = await client.post(
            url,
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
    if response.status_code != 401:
        raise AssertionError(
            f"缺请求头时期望 HTTP 401，实际 {response.status_code}: {response.text[:300]}"
        )
    print("✅ 无鉴权头 → HTTP 401")


def _parse_tool_payload(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, list) and raw:
        return _parse_tool_payload(raw[0])
    if isinstance(raw, dict):
        text = raw.get("text")
        if isinstance(text, str):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                return {"text": text}
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        return {"text": raw}
    return {"value": raw}


async def assert_agent_path_injects_headers(
    url: str, emp_no: str, token: str
) -> None:
    mcp_list = [
        {
            "id": 1002,
            "name": "auth-echo",
            "type": "http",
            "url": url,
        }
    ]
    service = McpToolService()
    service._validation_cache.clear()
    service._server_validation_cache.clear()

    missing = await service.validate_mcp_servers(mcp_list, force_refresh=True)
    status = (missing[0].get("status") if missing else "unavailable")
    if status == "available":
        raise AssertionError("未注入会话身份时 MCP 不应可用（服务端会 401）")
    print("✅ 未传 user_id/user_token 时连通性探测失败（符合预期）")

    tools = await service.get_mcp_toollist(
        mcp_list, user_id=emp_no, user_token=token
    )
    if not tools:
        raise AssertionError("注入鉴权头后未加载到任何 MCP 工具")

    whoami = next((t for t in tools if "whoami" in (t.name or "")), None)
    if whoami is None:
        names = [t.name for t in tools]
        raise AssertionError(f"未找到 whoami 工具，实际: {names}")

    raw = await whoami.ainvoke({})
    payload = _parse_tool_payload(raw)
    got_emp = str(payload.get(MCP_HEADER_EMP_NO) or "")
    got_token = str(payload.get(MCP_HEADER_AUTH_VALUE) or "")
    if got_emp != emp_no or got_token != token:
        raise AssertionError(
            f"whoami 回显不匹配: emp={got_emp!r} token={got_token!r}, "
            f"期望 emp={emp_no!r} token={token!r}, raw={raw!r}"
        )
    print(f"✅ Agent 建连路径透传成功: {MCP_HEADER_EMP_NO}={got_emp}")
    print(f"✅ whoami 回显 {MCP_HEADER_AUTH_VALUE} 与会话 token 一致")


def spawn_server(host: str, port: int) -> subprocess.Popen:
    script = Path(__file__).with_name("auth_echo_mcp.py")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    return subprocess.Popen(
        [sys.executable, str(script), "--host", host, "--port", str(port)],
        cwd=str(ROOT),
        env=env,
    )


async def run_tests(url: str, emp_no: str, token: str) -> None:
    print(f"测试地址: {url}")
    print(f"模拟 SSE/WS 身份: {MCP_HEADER_EMP_NO}={emp_no}")
    await assert_unauthorized_without_headers(url)
    await assert_agent_path_injects_headers(url, emp_no, token)
    print("\n全部通过：本地 MCP 已校验内部鉴权头，Agent 默认注入生效。")


def main() -> None:
    parser = argparse.ArgumentParser(description="测试本地鉴权回显 MCP")
    parser.add_argument("--url", default=DEFAULT_URL, help="MCP 地址")
    parser.add_argument("--emp-no", default=DEFAULT_EMP_NO, help="模拟 x-emp-no / user_id")
    parser.add_argument("--token", default=DEFAULT_TOKEN, help="模拟 x-auth-value / user_token")
    parser.add_argument(
        "--spawn",
        action="store_true",
        help="自动启动 auth_echo_mcp.py，测完后退出",
    )
    args = parser.parse_args()

    proc: Optional[subprocess.Popen] = None
    try:
        if args.spawn:
            host, port = parse_host_port(args.url)
            proc = spawn_server(host, port)
            if not wait_for_port(host, port, timeout=20.0):
                raise SystemExit(f"MCP 启动超时: {host}:{port}")
            print(f"已启动 auth-echo MCP pid={proc.pid} @ {host}:{port}")
        asyncio.run(run_tests(args.url, args.emp_no, args.token))
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    main()
