"""本地 MCP：校验并回显内部鉴权请求头 x-emp-no / x-auth-value。

启动：
    python mcp_server/tool/auth_echo_mcp.py
    python mcp_server/tool/auth_echo_mcp.py --host 127.0.0.1 --port 6003

Agent 侧会把 SSE/WS 的 user_id、user_token 默认注入为这两个请求头。
缺少任一请求头时，本服务返回 HTTP 401。
"""

from __future__ import annotations

import argparse
import os
from typing import Any, Dict

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
os.environ.pop("all_proxy", None)

HEADER_EMP_NO = "x-emp-no"
HEADER_AUTH_VALUE = "x-auth-value"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 6003

mcp = FastMCP("auth-echo")


class RequireInternalAuthMiddleware(BaseHTTPMiddleware):
    """HTTP 层强制校验内部鉴权头，覆盖 initialize / tools/list / tools/call。"""

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method == "OPTIONS":
            return await call_next(request)

        emp_no = (request.headers.get(HEADER_EMP_NO) or "").strip()
        auth_value = (request.headers.get(HEADER_AUTH_VALUE) or "").strip()
        if not emp_no or not auth_value:
            return JSONResponse(
                status_code=401,
                content={
                    "error": "unauthorized",
                    "message": "缺少请求头 x-emp-no 或 x-auth-value",
                    "received": {
                        HEADER_EMP_NO: bool(emp_no),
                        HEADER_AUTH_VALUE: bool(auth_value),
                    },
                },
            )
        return await call_next(request)


def _auth_from_request() -> Dict[str, str]:
    headers = get_http_headers(include_all=True)
    return {
        HEADER_EMP_NO: (headers.get(HEADER_EMP_NO) or "").strip(),
        HEADER_AUTH_VALUE: (headers.get(HEADER_AUTH_VALUE) or "").strip(),
    }


@mcp.tool()
def ping() -> str:
    """连通性探测。鉴权通过后返回 pong。"""
    return "pong"


@mcp.tool()
def whoami() -> Dict[str, Any]:
    """回显当前请求头中的 x-emp-no / x-auth-value，用于确认 Agent 已透传会话身份。"""
    auth = _auth_from_request()
    return {
        "ok": bool(auth[HEADER_EMP_NO] and auth[HEADER_AUTH_VALUE]),
        HEADER_EMP_NO: auth[HEADER_EMP_NO],
        HEADER_AUTH_VALUE: auth[HEADER_AUTH_VALUE],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启动本地鉴权回显 MCP")
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
        f"auth-echo MCP 启动: http://{args.host}:{args.port}/mcp "
        f"(transport={args.transport})，需要请求头 {HEADER_EMP_NO} + {HEADER_AUTH_VALUE}"
    )
    mcp.run(
        transport=args.transport,
        host=args.host,
        port=args.port,
        middleware=[Middleware(RequireInternalAuthMiddleware)],
    )


if __name__ == "__main__":
    main()
