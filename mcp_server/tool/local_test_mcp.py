"""本地测试 MCP：提供几个无副作用的小工具，供本机 Agent 联调。

启动：
    python mcp_server/tool/local_test_mcp.py
    python mcp_server/tool/local_test_mcp.py --port 6010

端点：http://127.0.0.1:6010/mcp
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime

from fastmcp import FastMCP

os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
os.environ.pop("all_proxy", None)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 6010

mcp = FastMCP("local-test")


@mcp.tool()
def echo_text(text: str) -> str:
    """原样返回传入的文本。用于确认 MCP 工具调用是否打到本机测试服务。"""
    return text


@mcp.tool()
def add_numbers(a: float, b: float) -> float:
    """计算两个数的和。"""
    return a + b


@mcp.tool()
def current_time() -> str:
    """返回本机当前时间，格式为 YYYY-MM-DD HH:MM:SS。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@mcp.tool()
def reverse_text(text: str) -> str:
    """把文本按字符反转后返回。"""
    return text[::-1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启动本地测试 MCP")
    parser.add_argument("--host", default=DEFAULT_HOST, help="监听地址")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口")
    parser.add_argument(
        "--transport",
        default="http",
        choices=["http", "sse"],
        help="MCP 传输：http=Streamable HTTP（默认），sse=旧版 SSE",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(
        f"local-test MCP 启动: http://{args.host}:{args.port}/mcp "
        f"(transport={args.transport})"
    )
    mcp.run(transport=args.transport, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
