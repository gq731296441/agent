from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel

from agent_server.middleware.customMiddleWare.NodeHooksMiddleWare import NodeHooksMiddleWare
from agent_server.middleware.customMiddleWare.ToolMonitoringMiddleWare import ToolMonitoringMiddleWare
from agent_server.middleware.MicroCompactMiddleware import MicroCompactMiddleware
from agent_server.middleware.LoggingSummarizationMiddleware import LoggingSummarizationMiddleware
from agent_server.middleware.RateLimitMiddleware import RateLimitMiddleware
from langchain.agents.middleware import LLMToolSelectorMiddleware
from langgraph.constants import TAG_NOSTREAM
from agent_server.llm import create_summary_llm, create_tool_judge_llm
from agent_server.prompts import HISTORY_SUMMARY_PROMPT, TOOL_SELECTOR_MIDDLEWARE_PROMPT
from config import get_logger

from mcp_server.tool.delete_mrm_doc import delete_mrm_doc

import os

os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
os.environ.pop("all_proxy", None)

logger = get_logger(__name__)

class LangGraphAgent:
    """LangGraph Agent 封装类，负责 Agent 的初始化和运行逻辑。"""
    
    def __init__(
        self,  
        model: str | BaseChatModel=None,
        system_prompt: str = None,
        tools: list = None,
        checkpointer=None,
        store=None,
        summary_llm=None,
        tool_judge_llm=None,
    ):
        """
        Agent 初始化（同步部分）。
        
        注意：由于 Agent 的完整初始化需要异步操作，请使用 create() 类方法创建实例。
        
        Args:
            checkpointer: LangGraph checkpointer 实例（可选），不提供则使用内存保存器
            tools: MCP 工具列表（可选）
            store: LangGraph BaseStore 实例（可选），用于中间件持久化状态
        """
        self.agent = None

        # ==================== 模型初始化 ====================
        self.model = model
        
        # ==================== 工具初始化 ====================
        if tools is None:
            # 默认使用基础工具集
            # self.tools = BASIC_TOOLS
            self.tools = [delete_mrm_doc]
            logger.info(f"【Agent】使用基础工具集 ({len(self.tools)} 个工具)")
        else:
            self.tools = tools or []
            # print(f"【Agent】工具列表: {tools}")
            logger.info(f"【Agent】使用自定义工具集 ({len(self.tools)} 个工具)")
        
        # 打印工具列表
        if self.tools:
            tool_names = [tool.name for tool in self.tools]
            # logger.debug(f"【Agent】工具列表: {', '.join(tool_names)}")
        
        # ==================== 提示词初始化 ====================
        self.system_prompt = system_prompt
        
        # ==================== Checkpointer & Store 初始化 ====================
        self.checkpointer = checkpointer
        self.store = store
        self.summary_llm = summary_llm
        self.tool_judge_llm = tool_judge_llm
        
        try:
            # 工具选择模型：设置 TAG_NOSTREAM 避免其输出泄漏到 messages 流
            # _tool_selector_llm = create_tool_judge_llm()
            # _tool_selector_llm.tags = [TAG_NOSTREAM]

            self.agent = create_agent(
                model=self.model,
                tools=self.tools,
                checkpointer=self.checkpointer,
                store=self.store,
                middleware=[
                    # create_hitl_middleware(),
                    NodeHooksMiddleWare(system_prompt=self.system_prompt),
                    ToolMonitoringMiddleWare(judge_llm=self.tool_judge_llm),
                    # LLMToolSelectorMiddleware(
                    #     model=_tool_selector_llm,
                    #     system_prompt=TOOL_SELECTOR_MIDDLEWARE_PROMPT,
                    # ),
                    MicroCompactMiddleware(
                        gap_threshold_minutes=5.0,  # 距上次活跃超过 5 分钟触发时间压缩
                        count_trigger=10,            # 工具结果超过 10 个触发数量压缩
                        keep_recent=5,              # 始终保留最近 5 个工具结果
                    ),
                    LoggingSummarizationMiddleware(
                        model=self.summary_llm or create_summary_llm(),
                        max_input_tokens=256000,  # 主模型最大输入 token 数，用于动态计算压缩阈值
                        keep=("tokens", 10000),
                        summary_prompt=HISTORY_SUMMARY_PROMPT
                    ),
                    RateLimitMiddleware(
                        max_retries=3,             # 429 最大重试 3 次
                        base_wait_seconds=30.0,    # 首次重试等待 30s
                        max_wait_seconds=120.0,    # 单次等待上限 120s
                    ),
                ],
            )
            logger.info(f"【Agent】✅ Agent构建完成")
        except Exception as e:
            logger.error(f"【Agent】❌ Agent构建失败: {e}")
            raise e


def create_base_agent(
    model: str | BaseChatModel=None,
    system_prompt: str = None,
    tools: list = None,
    checkpointer=None,
    store=None,
    summary_llm=None,
    tool_judge_llm=None,
) -> LangGraphAgent:
    """
    创建 LangGraphAgent 实例的工厂函数。
    
    Args:
        model: 语言模型实例或标识符（可选）
        system_prompt: 系统提示词（可选）
        tools: MCP 工具列表（可选）
        checkpointer: LangGraph checkpointer 实例（可选）
        store: LangGraph BaseStore 实例（可选），用于中间件持久化状态
    
    Returns:
        LangGraphAgent 实例
    """
    
    return LangGraphAgent(
        model=model,
        system_prompt=system_prompt,
        tools=tools,
        checkpointer=checkpointer,
        store=store,
        summary_llm=summary_llm,
        tool_judge_llm=tool_judge_llm,
    )