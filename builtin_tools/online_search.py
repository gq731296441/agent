"""内置工具：联网搜索（通过 nebulacoder MCP Server）。

适用于项目部署环境无法直接访问互联网的场景，
通过内网 nebulacoder MCP Server 发起联网搜索请求，
获取实时信息（天气、新闻、股价等）。
"""

from langchain.tools import tool
from pydantic import BaseModel, Field
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from config import get_logger
from config.config import app_config

logger = get_logger(__name__)

# 模块级常量：MCP Transport（仅 URL，每次调用创建 Client 实例）
_SEARCH_TRANSPORT = None


def _get_transport():
    """懒加载 StreamableHttpTransport，避免导入时立即建连。"""
    global _SEARCH_TRANSPORT
    if _SEARCH_TRANSPORT is None:
        _SEARCH_TRANSPORT = StreamableHttpTransport(
            url=app_config.ONLINE_SEARCH_MCP_URL
        )
    return _SEARCH_TRANSPORT


class _OnlineSearchInput(BaseModel):
    """联网搜索的输入参数。"""
    query: str = Field(
        description=(
            "搜索查询内容，用自然语言描述你想查询的信息。"
            "例如：'今天深圳的天气'、'2024年诺贝尔物理学奖得主是谁'、"
            "'最新的AI行业新闻' 等。"
        ),
    )
    deep_thinking: bool = Field(
        default=False,
        description="是否启用深度思考模式。复杂分析类问题建议开启，简单查询保持关闭以获得更快响应。",
    )


def create_online_search(user_id: str, user_token: str = ""):  # noqa: ARG001
    """工厂函数：创建绑定了当前用户身份的 online_search 工具。

    通过闭包注入 user_id，工具调用时自动传入，无需 LLM 额外提供。
    与 user_info.py 的 create_get_employee_info 模式一致。

    Args:
        user_id: 当前登录用户的工号
        user_token: 用户认证 Token（本工具暂不使用，保留以兼容工厂签名）
    """
    _current_user_id = user_id
    _ = user_token  # 保留参数以兼容工厂签名，暂未使用

    @tool("online_search", args_schema=_OnlineSearchInput)
    async def online_search(query: str, deep_thinking: bool = False) -> str:
        """联网搜索工具：搜索互联网上的实时信息。

        当需要获取实时信息时使用此工具
        例如天气、新闻、股价、最新资讯等任何需要联网查询的内容。

        使用场景：
        - 天气查询：'今天北京天气如何'
        - 新闻搜索：'最近AI行业有什么大新闻'
        - 实时信息：'当前比特币价格'
        - 知识查询：'2026年诺贝尔奖得主是谁'

        注意：
        - 此工具会发起网络请求，耗时可能较长（通常 5-30 秒）
        - deep_thinking=True 适用于需要推理分析的复杂问题
        - 如搜索无结果，可尝试简化或改写查询词
        """
        if not query or not query.strip():
            return "错误：搜索查询内容不能为空，请提供有效的搜索词。"

        query = query.strip()
        logger.info(
            f"online_search 被调用 user_id={_current_user_id} "
            f"query={query[:100]} deep_thinking={deep_thinking}"
        )

        transport = _get_transport()

        try:
            fastmcp_client = Client(transport)
            async with fastmcp_client:
                data = {
                    "user_req": {
                        "messages": [{
                            "role": "user",
                            "content": query,
                        }],
                        "user_id": _current_user_id,
                        "isDeepThinking": deep_thinking,
                        "isOnlineSearch": True,  # 始终启用联网搜索
                    }
                }
                result = await fastmcp_client.call_tool("chat", data)

            # fastmcp content 可能是 TextContent / ImageContent 等联合类型
            # 使用 getattr 安全获取 text 属性，避免类型检查警告
            first_content = result.content[0] if result.content else None
            response_text = getattr(first_content, "text", "") or ""

            if not response_text:
                logger.warning(
                    f"online_search 返回空结果 user_id={_current_user_id} query={query[:100]}"
                )
                return "联网搜索未返回有效结果，请尝试简化查询词或稍后重试。"

            logger.info(
                f"online_search 成功 user_id={_current_user_id} "
                f"query={query[:100]} response_len={len(response_text)}"
            )
            return response_text

        except Exception as e:
            logger.error(
                f"online_search 调用失败 user_id={_current_user_id} "
                f"query={query[:100]} error={e}"
            )
            return (
                f"联网搜索失败：{e}\n\n"
                f"可能原因：\n"
                f"1. 搜索服务暂时不可用，请稍后重试\n"
                f"2. 查询内容包含敏感词被拦截\n"
                f"3. 网络连接异常\n\n"
                f"建议：尝试简化查询词或过一段时间再试。"
            )

    return online_search
