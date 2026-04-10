# app/main.py
from pathlib import Path
import sys
import json
import uuid
import asyncio
import os
from typing import Any, Dict
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
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore

from app.websocketManager import WebsocketConnectionManager
from app.security import SecurityMiddleware
from agent_server.agent_manager import AgentManager
from agent_server.llm import create_title_llm
from agent_server.prompts import TITLE_GENERATION_PROMPT
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
title_llm = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理 - FastAPI 官方推荐方式"""
    global manager_agents, title_llm
    
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
        
        # 初始化 AsyncPostgresStore（PostgreSQL 持久化中间件状态，如熔断器计数）
        # 与 checkpointer 共享同一个数据库，重启后状态不丢失
        async with AsyncPostgresStore.from_conn_string(DB_URI) as kv_store:
            await kv_store.setup()
            app.state.kv_store = kv_store
            logger.info("【数据库】✅ AsyncPostgresStore 已初始化（持久化中间件状态）")

            # 初始化 AgentManager，传递 checkpointer 和 store
            manager_agents = AgentManager(checkpointer=checkpointer, store=kv_store)
            app.state.manager_agents = manager_agents
            logger.info("✅ AgentManager 已初始化")

            # 初始化会话标题生成模型
            try:
                title_llm = create_title_llm()
                app.state.title_llm = title_llm
                logger.info("✅ 会话标题生成模型已初始化")
            except Exception as e:
                title_llm = None
                app.state.title_llm = None
                logger.warning(f"【app】⚠️ 会话标题生成模型初始化失败，将使用兜底标题: {e}")

            logger.info("=" * 60)
            logger.info("【app】✅ 应用启动完成")
            logger.info("=" * 60)
            
            yield
            
            # ==================== 关闭阶段 ====================
            logger.info("【app】👋 应用关闭中...")
            # AsyncPostgresStore 和 AsyncPostgresSaver 的 __aexit__ 会自动关闭连接
            logger.info("✅ Store & Checkpointer 已释放")


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

# 安全防护中间件（后注册 = 先执行：安全检查在所有业务逻辑之前）
app.add_middleware(SecurityMiddleware)

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


def _build_fallback_session_title(session_id: str) -> str:
    return f"新会话 {session_id[-8:]}"


def _sanitize_session_title(title: str, session_id: str) -> str:
    """清洗模型生成的标题，避免返回空值或带引号/换行。"""
    cleaned = (title or "").strip().strip('"').strip("'")
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return _build_fallback_session_title(session_id)
    return cleaned[:30]


async def generate_session_title(app: FastAPI, question: str, session_id: str) -> str:
    """基于用户首问生成会话标题，失败时回退到默认标题。"""
    fallback_title = _build_fallback_session_title(session_id)
    normalized_question = (question or "").strip()
    if not normalized_question:
        return fallback_title

    title_model = getattr(app.state, "title_llm", None)
    if title_model is None:
        return fallback_title

    try:
        prompt = TITLE_GENERATION_PROMPT.format(messages=normalized_question[:1000])
        response = await title_model.ainvoke([
            HumanMessage(content=prompt),
        ])
        title = getattr(response, "content", "")
        if isinstance(title, list):
            title = "".join(str(item) for item in title)
        return _sanitize_session_title(str(title), session_id)
    except Exception as e:
        logger.warning(f"【会话】生成会话标题失败，使用兜底标题: {e}")
        return fallback_title


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    # ==================== 建立连接 ====================
    await manager.connect(websocket)
    
    # 获取应用状态中的 manager_agents
    _manager_agents = websocket.app.state.manager_agents

    # 追踪当前 ws 上正在运行的 Agent 流任务（session_id -> Task）
    _stream_tasks: Dict[str, asyncio.Task] = {}

    async def _run_agent_stream(session_id: str, action: str, user_id: str, user_token: str, payload: dict):
        """
        后台任务：消费 Agent 流并通过 WebSocket 发送消息。
        与主接收循环解耦，避免阻塞 pong 处理。
        """
        try:
            async for message in process_agent_stream(
                manager_agents=_manager_agents,
                action=action,
                user_id=user_id,
                user_token=user_token,
                session_id=session_id,
                payload=payload
            ):
                await manager.send(message, session_id)
        except asyncio.CancelledError:
            logger.info(f"【WebSocket】Agent 流任务被取消, session_id: {session_id}")
        except Exception as e:
            logger.error(f"【WebSocket】Agent 流任务异常, session_id: {session_id}, error: {e}")
        finally:
            _stream_tasks.pop(session_id, None)

    try:
        while True:
            # 等待接收消息（主循环不再被 Agent 流阻塞，可及时处理 pong）
            data = await websocket.receive_text()
            msg = json.loads(data)

            # ==================== 处理心跳 ====================
            if msg.get("type") == "pong":
                manager._update_pong(websocket)
                # logger.info(f"【websocket】 收到 pong from ws {id(websocket)}")
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
                    session_title = await generate_session_title(websocket.app, question, session_id)
                    # 如果是新会话，先在 user_sessions 表中创建记录（父表）
                    await storage.upsert_user_session(
                        user_id=user_id,
                        session_id=session_id,
                        session_name=session_title
                    )
                    # 发送 session_allocated 消息
                    await manager.send(
                        AgentMessage.session_allocated(
                            session_id=session_id,
                            content={
                                "session_id": session_id,
                                "session_title": session_title
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

            # 如果该 session 已有正在运行的流任务，先停止旧任务
            old_task = _stream_tasks.get(session_id)
            if old_task and not old_task.done():
                _manager_agents.stop_task(session_id)
                old_task.cancel()
                logger.info(f"【WebSocket】取消 session {session_id} 的旧 Agent 流任务")

            # 将 Agent 流处理放入后台任务，主循环继续接收消息（保证 pong 及时处理）
            task = asyncio.create_task(
                _run_agent_stream(session_id, action, user_id, user_token, msg)
            )
            _stream_tasks[session_id] = task
    
    except WebSocketDisconnect:
        # ==================== 连接断开 ====================
        # 取消所有正在运行的 Agent 流任务
        for sid, task in _stream_tasks.items():
            if not task.done():
                task.cancel()
        _stream_tasks.clear()

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
        # 取消所有正在运行的 Agent 流任务
        for sid, task in _stream_tasks.items():
            if not task.done():
                task.cancel()
        _stream_tasks.clear()
        # 异常情况执行清理过程
        affected_sessions = manager.disconnect(websocket)
        for sid in affected_sessions:
            _manager_agents.disconnect_connection(sid)
        return


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)



