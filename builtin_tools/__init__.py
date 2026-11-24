"""内置工具模块 — 始终可用的本地工具，无需 MCP 配置。

使用方式:
    from builtin_tools import get_builtin_tools
    tools = get_builtin_tools(user_id="10344649", user_token="xxx")

扩展指南:
    1. 在 builtin_tools/ 下新建 .py 文件
    2. 定义 create_xxx_tool(user_id, user_token) 工厂函数，返回 @tool 实例
    3. 在 get_builtin_tools() 中注册
"""

from typing import List


def get_builtin_tools(
    user_id: str,
    user_token: str,
    session_id: str = "",
    capabilities: dict = None,
) -> List:
    """返回当前会话的所有内置工具实例。

    每次会话创建时调用，通过闭包将 user_id/user_token/session_id/capabilities 注入工具实例。
    session_id 用于 read_image 等工具解析会话级工作区路径。
    capabilities 是当前会话已解析的能力快照 {"mcp": [...], "skills": [...]}，
    供 scheduled_task 工具在默认智能体会话里记录能力。
    """
    from builtin_tools.user_info import create_get_employee_info
    from builtin_tools.online_search import create_online_search
    from builtin_tools.image_reader import create_read_image
    from builtin_tools.scheduled_task_tool import create_scheduled_task_tool
    from builtin_tools.html_share_link import create_get_html_share_link

    return [
        create_get_employee_info(user_id=user_id, user_token=user_token),
        create_online_search(user_id=user_id, user_token=user_token),
        create_read_image(user_id=user_id, user_token=user_token, session_id=session_id),
        create_get_html_share_link(user_id=user_id, session_id=session_id),
        create_scheduled_task_tool(
            user_id=user_id,
            user_token=user_token,
            session_id=session_id,
            capabilities=capabilities,
        ),
    ]
