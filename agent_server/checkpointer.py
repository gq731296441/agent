"""
LangGraph Checkpointer 模块

Checkpointer 现在通过 FastAPI 的 lifespan 管理器进行初始化，
并保存在 app.state.checkpointer 中供其他模块使用。

使用示例:
    from starlette.requests import Request
    
    async def some_endpoint(request: Request):
        checkpointer = request.app.state.checkpointer
        # 使用 checkpointer...
"""

# 该模块现在主要用于文档和类型注解
# 实际的 checkpointer 初始化和生命周期管理见 app/main.py 的 lifespan 函数
