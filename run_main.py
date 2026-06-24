import asyncio
import sys

# Windows 默认 ProactorEventLoop 与 psycopg 异步模式不兼容
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn


async def main():
    config = uvicorn.Config(
        "app.main:app",
        host="0.0.0.0",
        port=8002,
        access_log=False,
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    print("\n【提示】请在浏览器访问: http://localhost:8002/\n")
    asyncio.run(main())
