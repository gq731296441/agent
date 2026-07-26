# app/main.py
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from pathlib import Path
import sys
import json
import asyncio
import os
import time
from typing import Dict
from contextlib import asynccontextmanager

# ============= 代理配置 ============
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
os.environ.pop("all_proxy", None)

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from app.websocketManager import WebsocketConnectionManager
from app.security import SecurityMiddleware
from agent_server.agent_manager import AgentManager
from db import storage
from db.database import db_manager
from api import session_api, agent_api, mcp_api, search_api, stats_api
from api import agent_run_api, artifact_api, user_agent_api, skill_api, skill_internal_api, workspace_api, sandbox_api
from api import user_llm_api, subscription_agent_api, user_memory_api, logs_api
from api import admin_api, skill_upload_api, upload_api
from api.search_api import process_agent_stream
from deep_agent_server.session.readiness import SessionReadinessService
from config import setup_logging, get_logger, app_config
from utils.session_utils import (
    get_or_create_session,
    validate_session_access,
    setup_new_session,
    SESSION_ACCESS_DENIED_MSG,
)

# 公共模块导入
from common.enums import ActionType
from common.schemas import AgentMessage

setup_logging()
logger = get_logger(__name__)

# 业务数据库 URI（asyncpg 连接池用）
DB_URI = app_config.get_db_uri()
# LangGraph checkpointer/store 专用 URI（仅基础连接参数，keepalive 在 pool kwargs 中设置）
LANGGRAPH_DB_URI = app_config.get_langgraph_db_uri()

# 使用 AgentManager 管理 agent 实例（将在 lifespan 中初始化）
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

    # 初始化业务数据库连接池（asyncpg）
    try:
        await storage.init_db()
        logger.info("【数据库】✅ PostgreSQL 连接池初始化完成")
    except Exception as e:
        logger.error(f"【数据库】⚠️ PostgreSQL 初始化失败: {e}")

    # 创建共享 psycopg AsyncConnectionPool
    # checkpointer 和 store 共享同一个池，池自动管理：健康检查、故障恢复、连接回收
    pool_kwargs = app_config.get_pool_kwargs()
    pool = AsyncConnectionPool(
        LANGGRAPH_DB_URI,
        min_size=app_config.PSYCOPG_POOL_MIN,
        max_size=app_config.PSYCOPG_POOL_MAX,
        max_idle=300.0,           # 5 分钟空闲后回收
        max_lifetime=3600.0,      # 1 小时强制轮换
        reconnect_timeout=300.0,  # 5 分钟内自动重连（覆盖网络瞬时抖动）
        kwargs=pool_kwargs,
        open=False,
    )
    await pool.open()
    app.state.psycopg_pool = pool

    try:
        # 用共享池构造 checkpointer 和 store（不再使用 from_conn_string）
        checkpointer = AsyncPostgresSaver(conn=pool)
        await checkpointer.setup()
        app.state.checkpointer = checkpointer
        logger.info("【数据库】✅ Checkpointer 已就绪（共享池）")

        from agent_server.llm import create_global_embedding_fn
        from deep_agent_server.store.setup import create_kv_store

        embed_fn = create_global_embedding_fn()
        kv_store, semantic_search_enabled = await create_kv_store(pool, embed_fn)
        app.state.kv_store = kv_store
        app.state.store_semantic_search_enabled = semantic_search_enabled
        if app_config.STORE_TTL_SWEEPER_ENABLED:
            await kv_store.start_ttl_sweeper()
            logger.info("【Store】✅ TTL sweeper 已启动")
        logger.info("【数据库】✅ AsyncPostgresStore 已就绪（共享池）")

        # 初始化 AgentManager（辅助 LLM 按用户配置在运行时创建）
        manager_agents = AgentManager(
            checkpointer=checkpointer,
            store=kv_store,
        )
        app.state.manager_agents = manager_agents
        logger.info("✅ AgentManager 已初始化")

        from deep_agent_server.events import register_processors
        from deep_agent_server.events.bus import get_event_bus
        register_processors()
        await get_event_bus().start()
        logger.info("✅ EventBus 已启动")

        from deep_agent_server.sandbox import is_docker_sdk_available, start_sandbox_cleanup_loop
        from deep_agent_server.sandbox.docker_client import format_sandbox_docker_host_for_log
        from deep_agent_server.workspace import get_sandbox_workspace_root

        docker_host_log = format_sandbox_docker_host_for_log(
            app_config.SANDBOX_DOCKER_HOST or ""
        )
        logger.info(
            f"【Sandbox】SANDBOX_ENABLED={app_config.SANDBOX_ENABLED}, "
            f"docker_sdk={is_docker_sdk_available()}, "
            f"docker_host={docker_host_log}, "
            f"workspace_root={app_config.get_workspace_root()}, "
            f"sandbox_bind_root={get_sandbox_workspace_root()}, "
            f"python={sys.executable}"
        )
        await start_sandbox_cleanup_loop()

        if app_config.SKILL_CATALOG_AUTO_SYNC:
            try:
                # 第一步：将项目 skill-repo/ 非内置 skill 迁移到 VM .skill-repo/
                from deep_agent_server.skills.vm_repo import migrate_non_builtin_skills

                migration_result = await migrate_non_builtin_skills()
                logger.info(f"【SkillCatalog】VM 迁移: {migration_result}")

                # 第二步：从 VM .skill-repo/ 同步 catalog
                from deep_agent_server.skills.catalog_sync import sync_catalog_from_repo

                vm_root = app_config.get_vm_skill_repo_root()
                logger.info(f"【SkillCatalog】开始同步, vm_root={vm_root}")
                sync_result = await sync_catalog_from_repo(repo_root=vm_root)
                logger.info(f"【SkillCatalog】启动同步: {sync_result}")
            except Exception as e:
                logger.warning(f"【SkillCatalog】启动同步失败: {e}")

        # 确保所有已有订阅智能体的 .agents/{agent_id}/skills/ 目录已初始化
        try:
            from deep_agent_server.workspace import get_agent_skills_dir
            from deep_agent_server.skills.installer import sync_skills_to_agent
            from db import deep_storage as _ds

            agent_ids = await _ds.list_all_subscription_agent_ids()
            migrated = 0
            errors = 0
            for aid in agent_ids:
                try:
                    root = get_agent_skills_dir(aid)
                    if not root.is_dir():
                        market_ids = await _ds.get_agent_market_skill_ids(aid)
                        if market_ids:
                            sync_skills_to_agent(aid, market_ids)
                            migrated += 1
                except Exception as e:
                    errors += 1
                    logger.warning(
                        f"【AgentSkill】迁移单个智能体失败 agent_id={aid}: {e}"
                    )
            if migrated or errors:
                logger.info(
                    f"【AgentSkill】启动迁移完成: migrated={migrated} errors={errors} "
                    f"total={len(agent_ids)}"
                )
            else:
                logger.debug(
                    f"【AgentSkill】所有 {len(agent_ids)} 个智能体的 .agents/ 目录已就绪"
                )
        except Exception as e:
            logger.warning(f"【AgentSkill】启动迁移失败: {e}")

        # 内置默认技能：启动时为所有用户主动安装到 skills/ 目录
        try:
            from deep_agent_server.skills.selector import ensure_builtin_skills_for_all_users
            builtin_result = ensure_builtin_skills_for_all_users()
            logger.info(f"【BuiltinSkill】启动安装完成: {builtin_result}")
        except Exception as e:
            logger.warning(f"【BuiltinSkill】启动安装失败: {e}")

        from deep_agent_server.sandbox.status_notify import set_sandbox_status_notifier
        from deep_agent_server.hitl.sweeper import (
            set_hitl_ws_notifier,
            start_hitl_sweeper,
            stop_hitl_sweeper,
        )

        set_sandbox_status_notifier(manager.broadcast_sandbox_status)

        from deep_agent_server.file_parse_notify import set_file_parse_notifier
        set_file_parse_notifier(manager.broadcast_file_parse_status)
        logger.info("【FileParser】已注册 WS 推送回调")

        if app_config.HITL_ENABLED:
            set_hitl_ws_notifier(manager.send)
            await start_hitl_sweeper(kv_store)
            logger.info("【HITL】已启动 sweeper")
        else:
            logger.info("【HITL】HITL_ENABLED=False，跳过 sweeper 启动")

        # 会话标题在 ask 时按用户 LLM 配置生成
        app.state.title_llm = None

        logger.info("=" * 60)
        logger.info("【app】✅ 应用启动完成")
        logger.info("=" * 60)

        yield

    finally:
        # ==================== 关闭阶段 ====================
        logger.info("【app】👋 应用关闭中...")
        try:
            from deep_agent_server.events.bus import get_event_bus
            await get_event_bus().stop()
            logger.info("✅ EventBus 已停止")
        except Exception as e:
            logger.warning(f"【app】⚠️ EventBus 关闭失败: {e}")
        try:
            from deep_agent_server.sandbox import stop_sandbox_cleanup_loop
            await stop_sandbox_cleanup_loop()
            logger.info("✅ Sandbox TTL 清理任务已停止")
        except Exception as e:
            logger.warning(f"【app】⚠️ Sandbox 清理任务关闭失败: {e}")
        try:
            from deep_agent_server.hitl.sweeper import stop_hitl_sweeper

            await stop_hitl_sweeper()
            logger.info("✅ HITL sweeper 已停止")
        except Exception as e:
            logger.warning(f"【app】⚠️ HITL sweeper 关闭失败: {e}")
        try:
            from deep_agent_server.sandbox.status_notify import set_sandbox_status_notifier
            from deep_agent_server.hitl.sweeper import set_hitl_ws_notifier

            set_sandbox_status_notifier(None)
            set_hitl_ws_notifier(None)

            from deep_agent_server.file_parse_notify import set_file_parse_notifier
            set_file_parse_notifier(None)
        except Exception:
            pass
        try:
            kv_store = getattr(app.state, "kv_store", None)
            if kv_store and app_config.STORE_TTL_SWEEPER_ENABLED:
                await kv_store.stop_ttl_sweeper()
                logger.info("✅ Store TTL sweeper 已停止")
        except Exception as e:
            logger.warning(f"【app】⚠️ Store TTL sweeper 关闭失败: {e}")
        try:
            await db_manager.close()
            logger.info("✅ asyncpg 连接池已释放")
        except Exception as e:
            logger.warning(f"【app】⚠️ asyncpg 连接池关闭失败: {e}")
        await pool.close()
        logger.info("✅ 共享连接池已释放")


# ==================== 创建 FastAPI 应用 ====================

app = FastAPI(lifespan=lifespan)

# ==================== 中间件配置 ====================

_PARAMS_MAX_CHARS = 500


def _extract_request_params(request) -> str:
    """从请求中提取参数摘要（限制字符数），用于失败日志排查。"""
    parts = []

    # query 参数 (如 ?user_id=xxx&page=1)
    qp = dict(request.query_params)
    if qp:
        parts.append(f"query={qp}")

    # 路径参数 (如 /users/{user_id}/...)
    pp = dict(request.path_params)
    if pp:
        parts.append(f"path={pp}")

    # 请求体 (仅 JSON，且仅在已被 Starlette 缓存时可用)
    try:
        body_bytes = getattr(request, "_body", None)
        if body_bytes:
            body_str = body_bytes.decode("utf-8", errors="replace")
            parts.append(f"body={body_str}")
    except Exception:
        pass

    raw = " | ".join(parts)
    if len(raw) > _PARAMS_MAX_CHARS:
        raw = raw[:_PARAMS_MAX_CHARS] + "..."
    return raw


@app.middleware("http")
async def request_logging_middleware(request, call_next):
    """仅记录失败请求（≥400）的路径、参数、状态码与耗时。"""
    start_time = time.perf_counter()
    method = request.method
    path = request.url.path

    try:
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start_time) * 1000
        if response.status_code >= 400:
            params = _extract_request_params(request)
            logger.warning(
                f"【HTTP】{method} {path} -> {response.status_code} "
                f"| duration={duration_ms:.2f}ms"
                f"{' | ' + params if params else ''}"
            )
        return response
    except Exception:
        duration_ms = (time.perf_counter() - start_time) * 1000
        params = _extract_request_params(request)
        logger.exception(
            f"【HTTP】{method} {path} -> 500 "
            f"| duration={duration_ms:.2f}ms"
            f"{' | ' + params if params else ''}"
        )
        raise

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
# 注册运营统计路由
app.include_router(stats_api.router)
# Deep Agent 扩展路由
app.include_router(agent_run_api.router)
app.include_router(artifact_api.router)
app.include_router(user_memory_api.router)
app.include_router(user_agent_api.router)
app.include_router(user_llm_api.router)
app.include_router(subscription_agent_api.router)
app.include_router(skill_api.router)
app.include_router(skill_internal_api.router)
app.include_router(skill_upload_api.router)
app.include_router(admin_api.router)
app.include_router(logs_api.router)
app.include_router(workspace_api.router)
app.include_router(upload_api.router)
app.include_router(sandbox_api.router)

STATIC_DIR = ROOT_DIR / "static"
app.mount("/css", StaticFiles(directory=str(STATIC_DIR / "css")), name="css")
app.mount("/js", StaticFiles(directory=str(STATIC_DIR / "js")), name="js")

@app.get("/health")
async def health_check():
    """健康检查：asyncpg 业务池 + LangGraph psycopg 共享池。"""
    asyncpg_ok = False
    psycopg_ok = False
    errors = []

    try:
        pool = await db_manager.get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        asyncpg_ok = True
    except Exception as e:
        errors.append(f"asyncpg: {e}")

    try:
        psycopg_pool = app.state.psycopg_pool
        async with psycopg_pool.connection() as conn:
            await conn.execute("SELECT 1")
        psycopg_ok = True
    except Exception as e:
        errors.append(f"psycopg: {e}")

    status = "ok" if asyncpg_ok and psycopg_ok else "degraded"
    payload = {
        "status": status,
        "asyncpg": asyncpg_ok,
        "psycopg": psycopg_ok,
    }
    if errors:
        payload["errors"] = errors
    code = 200 if status == "ok" else 503
    return JSONResponse(content=payload, status_code=code)

@app.get("/")
async def home():
    return FileResponse('static/index.html')

# ==================== Websocket ====================

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    # ==================== 建立连接 ====================
    await manager.connect(websocket)

    # 获取应用状态中的 manager_agents
    _manager_agents = websocket.app.state.manager_agents

    # 追踪当前 ws 上正在运行的 Agent 流任务（session_id -> Task）
    _stream_tasks: Dict[str, asyncio.Task] = {}

    async def _deferred_title_update(
        session_id: str,
        user_id: str,
        question: str,
        reply: str | None = None,
    ):
        """后台任务：生成标题 → 更新 DB → 推送 SESSION_TITLE_UPDATED。异常安全。"""
        try:
            from utils.session_utils import deferred_generate_and_update_title

            new_title = await deferred_generate_and_update_title(
                session_id=session_id,
                user_id=user_id,
                question=question,
                reply=reply,
            )
            # 推送标题更新到前端
            await manager.send(
                AgentMessage.session_title_updated(
                    session_id=session_id,
                    content={
                        "session_id": session_id,
                        "session_title": new_title,
                    }
                ).to_frontend_dict(),
                session_id,
            )
            logger.info(
                f"【WebSocket】延迟标题推送完成 session={session_id} "
                f"title={new_title}"
            )
        except Exception as e:
            logger.warning(
                f"【WebSocket】延迟标题推送失败 session={session_id} error={e}"
            )

    async def _run_agent_stream(
        session_id: str,
        action: str,
        user_id: str,
        user_token: str,
        payload: dict,
        *,
        allow_if_missing: bool = False,
        _is_first_message: bool = False,
        _user_first_question: str = "",
    ):
        """
        后台任务：消费 Agent 流并通过 WebSocket 发送消息。
        与主接收循环解耦，避免阻塞 pong 处理。
        """
        end_reason = None
        # 在流消费过程中积累助手 CHAT 文本，供标题生成直接使用（避免 DB 查询）
        _reply_parts: list = []
        try:
            async for message in process_agent_stream(
                manager_agents=_manager_agents,
                action=action,
                user_id=user_id,
                user_token=user_token,
                session_id=session_id,
                payload=payload,
                allow_if_missing=allow_if_missing,
                wait_on_queue=True,
            ):
                # 积累助手 CHAT 文本用于标题生成
                msg_type = message.get("type", "")
                if msg_type == "chat":
                    content = message.get("content", "")
                    if isinstance(content, str) and content.strip():
                        _reply_parts.append(content.strip())

                # 追踪 END 消息的 reason，用于延迟标题生成判断
                if msg_type == "end":
                    raw = message.get("content", "")
                    if isinstance(raw, dict):
                        end_reason = raw.get("text", "")
                    else:
                        end_reason = str(raw)
                    # 立即清理 _stream_tasks，避免下一条消息到达时误判旧任务仍在运行
                    _stream_tasks.pop(session_id, None)
                    # 收到 END 消息时立即启动标题生成（与收尾清理并行）
                    if _is_first_message and end_reason in ("completed", "stop", "error"):
                        reply_text = "\n".join(_reply_parts) if _reply_parts else None
                        asyncio.create_task(
                            _deferred_title_update(
                                session_id=session_id,
                                user_id=user_id,
                                question=_user_first_question,
                                reply=reply_text,
                            )
                        )

                await manager.send(message, session_id)
        except asyncio.CancelledError:
            logger.info(f"【WebSocket】Agent 流任务被取消, session_id: {session_id}")
        except Exception as e:
            logger.error(f"【WebSocket】Agent 流任务异常, session_id: {session_id}, error: {e}")
        finally:
            _stream_tasks.pop(session_id, None)
            # 兜底：如果 END 消息未被检测到（如中断/异常），仍尝试生成标题
            if _is_first_message and not end_reason:
                reply_text = "\n".join(_reply_parts) if _reply_parts else None
                asyncio.create_task(
                    _deferred_title_update(
                        session_id=session_id,
                        user_id=user_id,
                        question=_user_first_question,
                        reply=reply_text,
                    )
                )

    try:
        while True:
            # 等待接收消息（主循环不再被 Agent 流阻塞，可及时处理 pong）
            data = await websocket.receive_text()
            msg = json.loads(data)

            # ==================== 处理心跳 ====================
            if msg.get("type") == "pong":
                manager._update_pong(websocket)
                pong_uid = (msg.get("user_id") or "").strip()
                if pong_uid and pong_uid != "guest":
                    manager.bind_user(websocket, pong_uid)
                continue

            # ==================== 沙箱状态查询（替代 HTTP 轮询） ====================
            if msg.get("type") == "get_sandbox_status":
                user_id = (msg.get("user_id") or "").strip()
                if user_id:
                    manager.bind_user(websocket, user_id)
                    await manager.send_sandbox_status(websocket, user_id)
                continue

            # ==================== 获取用户发送内容 ====================
            user_id = msg.get('user_id', 'guest')
            user_token = msg.get('user_token', '')
            question = msg.get('question', '')
            client_session_id = msg.get('session_id')
            agent_id = (msg.get("agent_id") or "").strip() or None

            manager.bind_user(websocket, user_id)

            logger.info(f"【WebSocket】📨 收到用户{user_id}聊天请求: {question[:50]}...")

            # 获取或创建 session_id
            resolved_agent_kind = "default"
            if client_session_id:
                session_id, is_new_session = await get_or_create_session(client_session_id)
            elif agent_id:
                from db import deep_storage as _ds

                result = await _ds.ensure_subscription_session(user_id, agent_id)
                session_id = result["session_id"]
                is_new_session = result.get("is_new", False)
                resolved_agent_kind = "subscription"
            else:
                session_id, is_new_session = await get_or_create_session(None)

            # 绑定 session_id 到当前 ws（支持多对一）
            manager.bind_session(websocket, session_id)

            # 判断是否为首条消息：新会话 或 预创建但无聊天记录的空会话
            is_first_message = is_new_session
            if not is_first_message:
                from db.storage import count_total_by_session
                from uuid import UUID as _UUID
                try:
                    cnt = await count_total_by_session(_UUID(session_id))
                    is_first_message = (cnt == 0)
                    logger.info(
                        f"[Session] WS session={session_id} is_new=False, "
                        f"chat_history_count={cnt}, is_first_message={is_first_message}"
                    )
                except Exception:
                    pass

            if is_first_message:
                try:
                    from utils.session_utils import _build_fallback_session_title
                    session_title = _build_fallback_session_title(session_id)
                    resolved_agent_id = await setup_new_session(
                        user_id=user_id,
                        session_id=session_id,
                        session_title=session_title,
                        agent_id=agent_id,
                        agent_kind=resolved_agent_kind,
                    )
                    # session_allocated 仅真正新建时推送，预创建复用时不推送
                    if is_new_session:
                        await manager.send(
                            AgentMessage.session_allocated(
                                session_id=session_id,
                                content={
                                    "session_id": session_id,
                                    "session_title": session_title,
                                    "agent_id": resolved_agent_id,
                                }
                            ).to_frontend_dict(),
                            session_id,
                        )

                    capabilities = msg.get("capabilities", {})
                    mcp_list = capabilities.get("mcp", [])
                    selected_skill_ids = capabilities.get("skills")
                    if selected_skill_ids is not None and isinstance(selected_skill_ids, list):
                        selected_skill_ids = [
                            str(s).strip() for s in selected_skill_ids if str(s).strip()
                        ]
                    else:
                        selected_skill_ids = None

                    async def _on_session_ready(sid: str):
                        async def _process_pending(_sid, uid, pl):
                            act = pl.get("action", ActionType.ASK.value)
                            tok = pl.get("user_token", user_token)
                            old = _stream_tasks.get(_sid)
                            if old and not old.done():
                                old.cancel()
                            t = asyncio.create_task(
                                _run_agent_stream(
                                    _sid, act, uid, tok, pl,
                                    _is_first_message=True,
                                    _user_first_question=question,
                                )
                            )
                            _stream_tasks[_sid] = t
                            await t

                        await SessionReadinessService.flush_pending(
                            sid, user_id, _process_pending
                        )

                    SessionReadinessService.start_warmup(
                        session_id,
                        user_id,
                        user_token,
                        resolved_agent_id,
                        mcp_list,
                        _manager_agents,
                        on_ready=_on_session_ready,
                        send_fn=manager.send,
                        selected_skill_ids=selected_skill_ids,
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
            elif not await validate_session_access(user_id, session_id):
                await manager.send(
                    AgentMessage.error(
                        session_id=session_id,
                        content=SESSION_ACCESS_DENIED_MSG,
                    ).to_frontend_dict(),
                    session_id,
                )
                continue

            # ==================== 处理消息 ====================
            action = msg.get('action', ActionType.ASK.value)

            # 首批消息（新会话或预创建空会话）：等待预热完成。
            # on_ready → flush_pending 已处理排队消息，
            # 若其已启动 _run_agent_stream 则跳过，避免重复处理。
            if is_first_message:
                ready = await SessionReadinessService.wait_until_ready(session_id)
                if not ready:
                    logger.warning(
                        f"【WebSocket】新会话 {session_id} 预热超时，跳过"
                    )
                    await manager.send(
                        AgentMessage.error(
                            session_id=session_id,
                            content="会话预热超时，请稍后重试",
                        ).to_frontend_dict(),
                        session_id,
                    )
                    continue
                # on_ready 可能已通过 flush_pending 启动了流任务
                existing = _stream_tasks.get(session_id)
                if existing and not existing.done():
                    logger.info(
                        f"【WebSocket】新会话 {session_id} 已由 on_ready 启动流任务，跳过"
                    )
                    continue

            # 如果该 session 已有正在运行的流任务，先停止旧任务
            old_task = _stream_tasks.get(session_id)
            if old_task and not old_task.done():
                _manager_agents.stop_task(session_id, user_id=user_id)
                old_task.cancel()
                logger.info(f"【WebSocket】取消 session {session_id} 的旧 Agent 流任务")

            # 将 Agent 流处理放入后台任务，主循环继续接收消息（保证 pong 及时处理）
            task = asyncio.create_task(
                _run_agent_stream(
                    session_id,
                    action,
                    user_id,
                    user_token,
                    msg,
                    allow_if_missing=is_new_session,
                    _is_first_message=is_first_message,
                    _user_first_question=question,
                )
            )
            _stream_tasks[session_id] = task

    except WebSocketDisconnect:
        # ==================== 连接断开 ====================
        # WS 断开后 agent 继续后台运行：不 cancel 任务，数据完整存入 DB
        # manager.send() 自动静默失败，_finalize_tokens() 正常执行
        affected_sessions = manager.disconnect(websocket)
        # 清理 AgentManager 中的会话路由映射（不调用 stop_task，agent 继续运行）
        for sid in affected_sessions:
            _manager_agents.disconnect_connection(sid)
            logger.info(f"【WebSocket】Cleanup，已注销会话路由（agent 继续后台运行）: {sid}")

        logger.info(f"【WebSocket】Disconnect，物理连接已断开，{len(affected_sessions)} 个会话继续后台运行")

    except Exception as e:
        logger.error(f"【WebSocket】Connect Error: {str(e)}")
        import traceback
        traceback.print_exc()
        # 异常情况下清理：取消任务 + 断开连接
        for sid, task in _stream_tasks.items():
            if not task.done():
                task.cancel()
        _stream_tasks.clear()
        affected_sessions = manager.disconnect(websocket)
        for sid in affected_sessions:
            _manager_agents.stop_task(sid)
            _manager_agents.disconnect_connection(sid)
        return


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
