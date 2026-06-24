import argparse
import asyncio
import json
import os
import traceback
from urllib.parse import urlparse
from typing import Dict

import httpx


from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

# os.environ.pop("http_proxy", None)
# os.environ.pop("https_proxy", None)
# os.environ.pop("all_proxy", None)

# 默认请求头（可直接修改）
DEFAULT_HEADERS: Dict[str, str] = {
    "X-Auth-Value": "6703f7c49b7976f570ff7dfd84e756d4",
    "X-Emp-No": "10344649",
}


def create_no_proxy_async_client(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    """创建忽略系统代理的 AsyncClient。"""
    kwargs: dict = {
        "follow_redirects": True,
        "trust_env": False,
        "timeout": timeout or httpx.Timeout(30.0),
    }
    if headers is not None:
        kwargs["headers"] = headers
    if auth is not None:
        kwargs["auth"] = auth
    return httpx.AsyncClient(**kwargs)


def parse_headers(header_args: list[str]) -> Dict[str, str]:
    """解析命令行请求头，支持 Key:Value 格式。"""
    headers: Dict[str, str] = {}
    for raw in header_args:
        if ":" not in raw:
            raise ValueError(f"无效请求头格式: {raw}，应为 Key:Value")
        key, value = raw.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"无效请求头键: {raw}")
        headers[key] = value
    return headers


def parse_host_port(url: str) -> tuple[str, int]:
    """从 URL 中提取 host/port，用于 TCP 直连探测。"""
    parsed = urlparse(url)
    if not parsed.hostname:
        raise ValueError(f"无法从 URL 解析主机名: {url}")

    if parsed.port:
        port = parsed.port
    elif parsed.scheme == "https":
        port = 443
    else:
        port = 80
    return parsed.hostname, port


async def test_tcp_connectivity(url: str, timeout: float) -> bool:
    """纯 TCP 连接探测，判断网络层是否可达。"""
    try:
        host, port = parse_host_port(url)
    except Exception as e:
        print(f"❌ TCP 探测失败: URL 解析异常: {e}")
        return False

    print(f"TCP 探测目标: {host}:{port}")
    writer = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        _ = reader
        print("✅ TCP 直连成功（网络层可达）")
        return True
    except Exception as e:
        print(f"❌ TCP 直连失败: {type(e).__name__}: {e}")
        return False
    finally:
        if writer is not None:
            writer.close()
            await writer.wait_closed()


async def test_mcp_with_sdk(url: str, timeout: float, headers: Dict[str, str]) -> bool:
    """使用 MCP 官方 SDK 进行初始化和工具列表获取。"""
    print(f"测试地址: {url}")
    print(f"请求头: {json.dumps(headers, ensure_ascii=False)}")

    try:
        async with streamablehttp_client(
            url=url,
            headers=headers,
            timeout=timeout,
            sse_read_timeout=max(timeout, 30),
            httpx_client_factory=create_no_proxy_async_client,
        ) as (read_stream, write_stream, get_session_id):
            async with ClientSession(read_stream, write_stream) as session:
                init_result = await session.initialize()
                print("✅ initialize 成功")
                print(f"  - 协议版本: {init_result.protocolVersion}")
                print(f"  - 会话ID: {get_session_id()}")

                tools_result = await session.list_tools()
                tools = tools_result.tools or []
                print(f"✅ tools/list 成功，工具数量: {len(tools)}")

                if tools:
                    print("工具列表（前10个）:")
                    for idx, tool in enumerate(tools[:10], start=1):
                        print(f"  {idx}. {tool.name}")
        return True
    except Exception as e:
        print(f"❌ MCP SDK 测试失败: {type(e).__name__}: {e}")
        print("详细堆栈：")
        print("".join(traceback.format_exception(e)).strip())
        return False


async def main() -> None:
    parser = argparse.ArgumentParser(description="使用 MCP 官方 SDK 测试 MCP 鉴权与工具列表")
    parser.add_argument(
        "--url",
        default="http://10.54.31.99:9984",
        help="MCP 服务地址",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="超时时间（秒）",
    )
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        help="自定义请求头，格式 Key:Value，可重复传参",
    )
    args = parser.parse_args()

    headers = DEFAULT_HEADERS.copy()
    if args.header:
        headers.update(parse_headers(args.header))

    print("==== 第1步：TCP 直连探测 ====")
    tcp_ok = await test_tcp_connectivity(args.url, args.timeout)

    print("\n==== 第2步：MCP 协议探测（SDK） ====")
    mcp_ok = await test_mcp_with_sdk(args.url, args.timeout, headers)

    print("\n====== 分层测试结果 ======")
    print(f"TCP 网络层: {'OK' if tcp_ok else 'FAIL'}")
    print(f"MCP 协议层: {'OK' if mcp_ok else 'FAIL'}")

    if not mcp_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
