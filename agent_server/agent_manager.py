import asyncio
from typing import Dict, Any, Optional, Tuple, AsyncGenerator
from agent_server.agents import LangGraphAgent, create_base_agent
from agent_server.llm import create_agent_llm
from agent_server.prompts import build_system_prompt
from mcp_server.mcp_manager import McpToolService
from db import storage
from config import setup_logging, get_logger

# 公共模块导入
from common.enums import MessageType, SenderType, EndReason
from common.schemas import AgentMessage

logger = get_logger(__name__)

class AgentManager:
    """
    AgentManager 负责管理 Agent 的创建和会话交互。
    
    主要功能：
    - 根据 session_id 和 mcp_list 创建 Agent 实例
    - 将 agent 配置持久化到数据库
    - 管理会话的停止事件
    """

    def __init__(self, checkpointer=None):
        """
        初始化 AgentManager
        
        Args:
            checkpointer: LangGraph checkpointer 实例
                         如果不提供，各 Agent 将使用内存保存器
        """
        self.checkpointer = checkpointer
        
        # session_id -> asyncio.Task
        self._active_tasks: Dict[str, asyncio.Task] = {}
        # session_id -> asyncio.Event
        self._stop_events: Dict[str, asyncio.Event] = {}

    async def create_agent(
        self, 
        user_id: str, 
        user_token: str,
        session_id: str,
        mcp_list: list = None
    ) -> Tuple[Optional[LangGraphAgent], Optional[str]]:
        """
        创建新的 Agent 实例，并将配置存储到数据库。
        
        每次调用都会创建新的 Agent 实例（不使用缓存）。
        
        Args:
            user_id: 用户 ID
            user_token: 用户 Token
            session_id: 会话 ID
            mcp_list: MCP 工具配置列表，格式为：
                [{
                    "name": "AI空间搜索",
                    "type": "http",
                    "url": "http://10.57.83.61:6002/mcp",
                    "id": "xxx-xxx-xxx"
                }, ...]
            
        Returns:
            (agent_instance, error_message)
            成功：(agent, None)
            失败：(None, error_msg)
        """

        # 初始化 MCP 工具列表
        if mcp_list is None:
            mcp_list = []
        # 初始化模型
        model = create_agent_llm()
        # 构建系统提示词
        system_prompt = build_system_prompt(user_id, user_token)
            
        mcp_service = McpToolService()

        try:
            # 存储配置到数据库
            if mcp_list:
                db_config = {
                    "selected_mcp_ids": [item.get("id") for item in mcp_list if item.get("id")]
                }
                await storage.save_agent_config(session_id, 'nebulacoder-pro-7.0', db_config)
                logger.info(f"[AgentManager] 已保存 Agent 配置到数据库, session_id: {session_id}")

            # 获取 MCP 工具
            mcp_tools = []
            if mcp_list:
                try:
                    mcp_tools = await mcp_service.get_mcp_toollist(mcp_list)
                    logger.info(f"[AgentManager] 成功加载 {len(mcp_tools)} 个 MCP 工具")
                except Exception as e:
                    logger.error(f"[AgentManager] 加载 MCP 工具失败: {e}")

            # 创建 Agent 实例
            logger.info(f"[AgentManager] 正在创建新 Agent 实例, session_id: {session_id}")

            agent = create_base_agent(
                model=model,
                system_prompt=system_prompt,
                tools=mcp_tools,
                checkpointer=self.checkpointer
            )
            
            return agent, None

        except Exception as e:
            import traceback
            traceback.print_exc()
            logger.error(f"[AgentManager] 创建 Agent 失败: {e}")
            return None, f"创建 Agent 失败: {str(e)}"

    def _create_agent_config(self, user_id: str, user_token: str, session_id: str) -> dict:
        """创建 agent 配置"""
        thread_id = f"{user_id}#{session_id}"
       
        configurable = {
            "thread_id": thread_id,
        }

        return {
            "configurable": configurable
        }

    async def _save_message_to_db(self, message: Dict[str, Any], user_id: str):
        """将消息保存到数据库"""
        session_id = message.get("session_id")
        sender = message.get("sender")
        msg_type = message.get("type")
        content = message.get("content")
        
        # llm_text_final 在存库时转为 chat 类型
        if msg_type == MessageType.LLM_TEXT_FINAL or msg_type == MessageType.LLM_TEXT_FINAL.value:
            msg_type = MessageType.CHAT.value
        
        try:
            db_content = content
            # 确保存库内容为 dict
            if not isinstance(db_content, dict):
                db_content = {"text": str(content)}
            
            await storage.add_history_record(
                session_id=session_id,
                user_id=user_id,
                sender=sender,
                msg_type=msg_type,
                content=db_content
            )
        except Exception as e:
            logger.error(f">>> [History Error] 保存失败: {e}")

    async def get_agent_stream(
        self, 
        user_id: str, 
        user_token: str, 
        session_id: str, 
        input_data: dict,
        mcp_list: list = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        获取 Agent 流式输出的异步生成器。
        
        该方法负责：
        1. 初始化 Agent（调用 get_or_create_agent）
        2. 创建 stop_event 并存入 self._stop_events
        3. 构造 config
        4. 消费 Agent 的原始输出
        5. 在 yield 消息之前，检查 save 字段并保存到数据库
        
        Args:
            user_id: 用户 ID
            user_token: 用户 Token
            session_id: 会话 ID
            input_data: 输入数据
            mcp_list: MCP 工具配置列表
            
        Yields:
            标准化的消息字典（不含 save 字段，直接可发送给前端）
        """
        if mcp_list is None:
            mcp_list = []
            
        stop_event = asyncio.Event()
        self._stop_events[session_id] = stop_event
        
        try:
            # 1. 创建 Agent
            agent, init_err = await self.create_agent(user_id, user_token, session_id, mcp_list)
            
            if init_err:
                yield AgentMessage.error(session_id=session_id, content=init_err).to_frontend_dict()
                yield AgentMessage.end(session_id=session_id, reason=EndReason.STOP).to_frontend_dict()
                return
            
            # 2. 构造 config
            config = self._create_agent_config(user_id, user_token, session_id)
            
            # 3. 消费 Agent 的原始输出
            async for message in agent.run(
                input_data=input_data,
                config=config,
                session_id=session_id,
                stop_event=stop_event
            ):
                # 4. 检查 save 字段，若为 True 则保存到数据库
                should_save = message.pop("save", False)
                if should_save:
                    await self._save_message_to_db(message, user_id)
                
                # llm_text_final 类型仅存储，不推送给前端
                msg_type = message.get("type")
                if msg_type == MessageType.LLM_TEXT_FINAL or msg_type == MessageType.LLM_TEXT_FINAL.value:
                    continue
                if msg_type == MessageType.REASONING_FINAL or msg_type == MessageType.REASONING_FINAL.value:
                    continue
                
                # 5. yield 消息给外部（WebSocket 或 API）
                yield message
                
        except asyncio.CancelledError:
            logger.info(f"[AgentManager] 会话 {session_id} 被取消")
            yield AgentMessage.end(session_id=session_id, reason=EndReason.STOP).to_frontend_dict()
        except Exception as e:
            logger.error(f"[AgentManager] 会话 {session_id} 出错: {e}")
            yield AgentMessage.error(session_id=session_id, content=str(e)).to_frontend_dict()
            yield AgentMessage.end(session_id=session_id, reason=EndReason.STOP).to_frontend_dict()
        finally:
            # 确保清理 stop_event
            self._stop_events.pop(session_id, None)
            self._active_tasks.pop(session_id, None)

    def stop_task(self, session_id: str):
        """触发软停止信号"""
        # 触发软停止信号（event机制）
        if session_id in self._stop_events:
            self._stop_events[session_id].set()
        # 取消正在运行的任务
        if session_id in self._active_tasks:
            self._active_tasks[session_id].cancel()

    def disconnect_connection(self, session_id: str = None):
        """
        断开会话连接时调用，清理相关资源。
        
        Args:
            session_id: 会话 ID
        """
        if session_id:
            self._stop_events.pop(session_id, None)
            self._active_tasks.pop(session_id, None)
            logger.info(f"[AgentManager] 会话 {session_id} 已断开连接")
