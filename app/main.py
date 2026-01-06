# app/main.py
from pathlib import Path
import sys
import json
import uuid
import asyncio
import os
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
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command

from app.websocketManager import WebsocketConnectionManager
from agent_server.agent_manager import AgentManager
from agent_server import storage
from api import sessions, agents
from config import app_config

from utils.stop_manager import create_stop_event, is_stopped, clear_stop_event, stop_task

# 从 config.py 读取数据库配置并构建 DB_URI
db_engine = app_config.DB_ENGINE
db_host = app_config.DB_HOST
db_port = app_config.DB_PORT
db_name = app_config.DB_NAME
db_user = app_config.DB_USER
db_password = app_config.DB_PASSWORD

DB_URI = f"{db_engine}://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}?sslmode=disable"

# 使用 AgentManager 管理 agent 实例和配置（将在 lifespan 中初始化）
manager_agents = None
manager = WebsocketConnectionManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理 - FastAPI 官方推荐方式"""
    global manager_agents
    
    # ==================== 启动阶段 ====================
    print(">>> 应用启动中...")
    
    # 初始化 PostgreSQL 连接池（用于通用数据库操作）
    try:
        await storage.init_db()
        print("✅ PostgreSQL 连接池初始化完成")
    except Exception as e:
        print(f"⚠️ PostgreSQL 初始化失败: {e}")
    
    # 创建并初始化 checkpointer（AsyncPostgresSaver 会自动管理连接）
    async with AsyncPostgresSaver.from_conn_string(DB_URI) as checkpointer:
        try:
            await checkpointer.setup()
            print("✅ LangGraph 数据库表初始化完成")
        except Exception as e:
            print(f"⚠️ LangGraph 初始化警告: {e}（可能已存在表）")
        
        # 将 checkpointer 保存到 app.state，供其他模块使用
        app.state.checkpointer = checkpointer
        print("✅ Checkpointer 已加载到应用状态")
        
        # 初始化 AgentManager，传递 checkpointer
        manager_agents = AgentManager(checkpointer=checkpointer)
        app.state.manager_agents = manager_agents
        print("✅ AgentManager 已初始化")
        print(">>> 应用启动完成")
        
        yield  # 应用运行
        
        # ==================== 关闭阶段 ====================
        print(">>> 应用关闭中...")
        # AsyncPostgresSaver 的 __aexit__ 会自动关闭连接
        print("✅ Checkpointer 已释放")


# 创建 FastAPI 应用，配置生命周期管理
app = FastAPI(lifespan=lifespan)
app.mount('/static', StaticFiles(directory='static'), name='static')  # 挂载静态文件

# 注册 API 路由
app.include_router(sessions.router)
app.include_router(agents.router)


@app.get("/")
async def home():
    return FileResponse('static/index.html')


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    # ==================== 建立连接 ====================
    await manager.connect(websocket)
    
    # 获取应用状态中的 manager_agents
    _manager_agents = websocket.app.state.manager_agents
    
    print(f"[WebSocket] 新连接建立")
    try:
        while True:
            # 等待接收消息
            data = await websocket.receive_text()
            msg = json.loads(data)
            
            # ==================== 每条消息都检查 session_id ====================
            user_id = msg.get('user_id', 'guest')
            client_session_id = msg.get('session_id')
            
            # 规范化 session_id（处理 None, '', 'null' 等情况）
            if isinstance(client_session_id, str):
                client_session_id = client_session_id.strip()
                if client_session_id.lower() == 'null' or client_session_id == '':
                    client_session_id = None
            
            # 判断是新建还是复用 session
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
                        session_id = "session_" + str(uuid.uuid4())
                        is_new_session = True
                else:
                    # 没有提供 session_id，生成新的
                    session_id = "session_" + str(uuid.uuid4())
                    is_new_session = True
            except Exception as e:
                print(f"[Storage Warning] session_exists check failed: {e}, generating new session")
                session_id = "session_" + str(uuid.uuid4())
                is_new_session = True
            
            # 绑定 session_id 到当前 ws（支持多对一）
            manager.bind_session(websocket, session_id)
            
            # 初始化或获取 agent
            agent, err = await _manager_agents.get_or_create_agent(user_id, session_id)
            
            if err:
                await manager.send({"type": "error", "content": err}, session_id)
                manager.disconnect(ws=websocket)
                continue
                    
            
            # 如果是新会话，发送 session_allocated 消息
            if is_new_session:
                await manager.send({
                    "type": "session_allocated",
                    "session_id": session_id,
                    "session_title": f"新会话 {session_id[-8:]}"
                }, session_id)
            
            print(f"[Message] 用户: {user_id}, 会话id: {session_id} {'(new)' if is_new_session else '(existing)'}, action={msg.get('action')}")
            
            # ==================== 处理消息 ====================
            # 实时向前端推送消息的回调
            async def on_status(message):
                if message.get("type") == "assistant_message":
                    await manager.send({"type": "assistant_message", "content": message["content"]}, session_id)
                elif message.get('type') == 'tool_result':
                    await manager.send({
                        "type": "tool_result",
                        "tool_name": message["tool_name"],
                        "content": message["content"],
                        "tool_call_id": message["tool_call_id"]
                    }, session_id)
                elif message.get("type") == "tool_call":
                    await manager.send({"type": "tool_call", "content": {"tool_name": message["tool_name"], "args": message["args"], "tool_call_id": message["tool_id"]}}, session_id)
                elif message.get("type") == "interrupt":
                    await manager.send({
                        "type": "interrupt",
                        "request_id": message["request_id"],
                        "actions": message["actions"],
                        "total_count": message["total_count"],
                    }, session_id)
                elif message.get("type") == "status":
                    await manager.send({"type": "status", "content": message["content"]}, session_id)
                elif message.get("type") == "end":
                    await manager.send({"type": "end", "content": message["content"]}, session_id)

            task_id = str(uuid.uuid4())
            create_stop_event(task_id)

            if msg.get('action') == 'ask':
                question = msg.get('question', '')
                await manager.send({"type": "user", "content": question}, session_id)

                try:
                    await agent.run({"type": "ask", "content": question}, user_id, session_id, on_status)
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    error = f"【错误】{str(e)}"
                    await manager.send({"type": "assistant_message", "content": error}, session_id)
                finally:
                    clear_stop_event(task_id)
            
            elif msg.get('action') == 'resume_hitl':
                request_id = msg["request_id"]
                user_decisions = msg.get("decisions", [])

                info = agent.pending_interrupts.pop(request_id, None)

                if not info: 
                    await manager.send({"type": "status", "content": "错误：中断请求无效或已处理。"}, session_id)
                    continue

                if len(user_decisions) != info["num_pending"]:
                    await manager.send({"type": "error", "content": "决策数量不匹配"}, session_id)
                    continue

                decisions = []

                for i, dec in enumerate(user_decisions):
                    dec_type = dec["type"]
                    if dec_type == 'approve':
                        decisions.append({"type": dec_type})
                    elif dec_type == 'reject':
                        decisions.append({
                            "type": dec_type,
                            "message": "用户拒绝该工具的调用，无需再次发起调用请求"
                        })
                    elif dec_type == 'edit':
                        decisions.append({
                            "type": dec_type,
                            "edited_action": dec["edited_action"]
                        })
                
                resume_cmd = Command(resume={"decisions": decisions})
                await agent.run({"command": resume_cmd}, user_id, session_id, on_status)

            elif msg.get("action") == "stop":
                stop_task(msg.get("task_id", ""))
                await manager.send({"type": "status", "message": "已停止生成"}, session_id)
    
    except WebSocketDisconnect:
        # ==================== 连接断开 ====================
        # 从物理连接管理器中移除 ws，并拿回所有受影响的 session_id
        affected_sessions = manager.disconnect(websocket)
        # 通知 AgentManager 清理这些会话的路由映射,即便 Agent 还在内存中（TTL 还没到），它也不再占用活跃会话名额
        for sid in affected_sessions:
            _manager_agents.disconnect_connection(sid)
            print(f"[Cleanup] 已注销会话路由: {sid}")
            
        print(f"[Disconnect] 物理连接已断开，共清理 {len(affected_sessions)} 个关联会话")

    except Exception as e:
        print(f"[Connect Error] {str(e)}")
        import traceback
        traceback.print_exc()
        # 异常情况执行上述清理过程
        affected_sessions = manager.disconnect(websocket)
        for sid in affected_sessions:
            _manager_agents.disconnect_connection(sid)
        return


async def main():
    # quick local test using guest user/session
    a = manager_agents.get('guest', '1')
    if not a.is_initialized:
        cfg = await storage.get_agent_config('guest', '1')
        if cfg:
            await a.initialize_from_config(cfg)
        else:
            await a.initialize_from_config({"tools": ["mcp"], "messages": []})
    result = await a.run({'type': 'ask', 'content': '帮我查询id为test-001的单据'}, 'guest', '1')
    print(result)


if __name__ == '__main__':
    asyncio.run(main())



