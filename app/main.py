# app/main.py
from pathlib import Path
import sys
import json
import uuid
import asyncio
import os
from typing import Any
from contextlib import asynccontextmanager

# ============= 代理配置 ============
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
os.environ.pop("all_proxy", None)

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.websocketManager import WebsocketConnectionManager
from agent_server.agent_manager import AgentManager
from db import storage
from api import session_api, agent_api, mcp_api, search_api
from api.search_api import process_agent_stream
from config import setup_logging, get_logger, app_config

# 公共模块导入
from common.enums import MessageType, SenderType, ActionType
from common.schemas import AgentMessage

setup_logging()
logger = get_logger(__name__)

# 从 config.py 读取数据库DB_URI
DB_URI = app_config.get_db_uri()

# 使用 AgentManager 管理 agent 实例 and 配置（将在 lifespan 中初始化）
manager_agents = None
manager = WebsocketConnectionManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理 - FastAPI 官方推荐方式"""
    global manager_agents
    
    # ==================== 启动阶段 ====================
    logger.info("=" * 60)
    logger.info("【app】🚀 应用启动中...")
    logger.info("=" * 60)
    
    # 初始化 PostgreSQL 连接池（用于通用数据库操作）
    try:
        await storage.init_db()
        logger.info("【数据库】✅ PostgreSQL 连接池初始化完成")
    except Exception as e:
        logger.error(f"【数据库】⚠️ PostgreSQL 初始化失败: {e}")
    
    # 创建并初始化 checkpointer（AsyncPostgresSaver 会自动管理连接）
    async with AsyncPostgresSaver.from_conn_string(DB_URI) as checkpointer:
        try:
            await checkpointer.setup()
            logger.info("【数据库】✅ LangGraph 数据库表初始化完成")
        except Exception as e:
            logger.warning(f"【数据库】⚠️ LangGraph 初始化警告: {e}（可能已存在表）")
        
        # 将 checkpointer 保存到 app.state，供其他模块使用
        app.state.checkpointer = checkpointer
        logger.info("【数据库】✅ Checkpointer 已加载到应用状态")
        
        # 初始化 AgentManager，传递 checkpointer
        manager_agents = AgentManager(checkpointer=checkpointer)
        app.state.manager_agents = manager_agents
        logger.info("✅ AgentManager 已初始化")

        logger.info("=" * 60)
        logger.info("【app】✅ 应用启动完成")
        logger.info("=" * 60)
        
        yield
        
        # ==================== 关闭阶段 ====================
        logger.info("【app】👋 应用关闭中...")
        # AsyncPostgresSaver 的 __aexit__ 会自动关闭连接
        logger.info("✅ Checkpointer 已释放")


# ==================== 创建 FastAPI 应用 ====================

app = FastAPI(lifespan=lifespan)
app.mount('/static', StaticFiles(directory='static'), name='static')  # 挂载静态文件

# ==================== 中间件配置 ====================

# CORS 中间件 - 允许前端跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== 路由注册 ====================
# 注册会话路由
app.include_router(session_api.router)
# 注册agent路由
app.include_router(agent_api.router)
# 注册mcp路由
app.include_router(mcp_api.router)
# 注册search路由
app.include_router(search_api.router)

@app.get("/")
async def home():
    return FileResponse('static/index.html')

# ==================== Websocket ====================
async def get_or_create_session(client_session_id: str):
    """
    检查或创建 session_id。
    返回 (session_id, is_new_session)
    """
    # 规范化 session_id（处理 None, '', 'null' 等情况）
    if isinstance(client_session_id, str):
        client_session_id = client_session_id.strip()
        if client_session_id.lower() == 'null' or client_session_id == '':
            client_session_id = None

    session_id = None
    is_new_session = False

    try:
        # 检查 session_id 是否存在
        if client_session_id:
            exists = await storage.session_exists(client_session_id)
            if exists:
                session_id = client_session_id
            else:
                # session_id 不存在，生成新的
                session_id = str(uuid.uuid4())
                is_new_session = True
        else:
            # 没有提供 session_id，生成新的
            session_id = str(uuid.uuid4())
            is_new_session = True
    except Exception as e:
        logger.error(f"【会话】 session_exists check failed: {e}, 生成新的会话...")
        session_id = str(uuid.uuid4())
        is_new_session = True

    return session_id, is_new_session


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    # ==================== 建立连接 ====================
    await manager.connect(websocket)
    
    # 获取应用状态中的 manager_agents
    _manager_agents = websocket.app.state.manager_agents
    try:
        while True:
            # 等待接收消息
            data = await websocket.receive_text()
            msg = json.loads(data)

            # ==================== 处理心跳 ====================
            if msg.get("type") == "pong":
                manager._update_pong(websocket)
                logger.debug(f"【websocket】 收到 pong from ws {id(websocket)}")
                continue
            
            # ==================== 获取用户发送内容 ====================
            user_id = msg.get('user_id', 'guest')
            user_token = msg.get('user_token', '')
            question = msg.get('question', '')
            client_session_id = msg.get('session_id')

            logger.info(f"【WebSocket】📨 收到用户{user_id}聊天请求: {question[:50]}...")
            
            # 获取或创建 session_id
            session_id, is_new_session = await get_or_create_session(client_session_id)

            # 绑定 session_id 到当前 ws（支持多对一）
            manager.bind_session(websocket, session_id)
            
            if is_new_session:
                try:
                    # 如果是新会话，先在 user_sessions 表中创建记录（父表）
                    await storage.upsert_user_session(
                        user_id=user_id,
                        session_id=session_id,
                        session_name=f"新会话 {session_id[-8:]}"
                    )
                    # 发送 session_allocated 消息
                    await manager.send(
                        AgentMessage.session_allocated(
                            session_id=session_id,
                            content={
                                "session_id": session_id,
                                "session_title": f"新会话 {session_id[-8:]}"
                            }
                        ).to_frontend_dict(), 
                        session_id
                    )
                    
                    logger.info(f"【WebSocket】用户: {user_id}, 会话id: {session_id} {'(新会话)' if is_new_session else '(已存在会话)'}, action={msg.get('action')}")
                except Exception as e:
                    logger.error(f"【WebSocket】创建新会话失败: {e}")
                    await manager.send(
                        AgentMessage.error(
                            session_id=session_id,
                            content=f"会话创建失败: {str(e)}"
                        ).to_frontend_dict(), 
                        session_id
                    )
                    manager.disconnect(ws=websocket)
                    continue

            # ==================== 处理消息 ====================
            action = msg.get('action', ActionType.ASK.value)
            
            # 如果是 ask 类型，先推送用户消息给前端（回显）
            if action == ActionType.ASK or action == ActionType.ASK.value:
                await manager.send(
                    AgentMessage.chat(
                        session_id=session_id,
                        content=question,
                        sender=SenderType.USER,
                        save=False
                    ).to_frontend_dict(), 
                    session_id
                )
            
            # 统一使用 process_agent_stream 处理所有 action
            async for message in process_agent_stream(
                manager_agents=_manager_agents,
                action=action,
                user_id=user_id,
                user_token=user_token,
                session_id=session_id,
                payload=msg
            ):
                await manager.send(message, session_id)
    
    except WebSocketDisconnect:
        # ==================== 连接断开 ====================
        # 从物理连接管理器中移除 ws，并拿回所有受影响的 session_id
        affected_sessions = manager.disconnect(websocket)
        # 通知 AgentManager 清理这些会话的路由映射
        for sid in affected_sessions:
            # 停止正在运行的异步协程
            _manager_agents.stop_task(sid)
            # 清理 AgentManager 中的会话信息
            _manager_agents.disconnect_connection(sid)
            logger.info(f"【WebSocket】Cleanup，已注销会话路由: {sid}")
            
        logger.info(f"【WebSocket】Disconnect，物理连接已断开，共清理 {len(affected_sessions)} 个关联会话")

    except Exception as e:
        logger.error(f"【WebSocket】Connect Error: {str(e)}")
        import traceback
        traceback.print_exc()
        # 异常情况执行上述清理过程
        affected_sessions = manager.disconnect(websocket)
        for sid in affected_sessions:
            _manager_agents.disconnect_connection(sid)
        return


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)



