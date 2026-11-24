"""本机真实 MCP 目录。条目指向已启动的测试服务，不是虚构的工具列表。"""

from typing import Any, Dict, List

from config.config import app_config


def get_local_test_mcp_list() -> List[Dict[str, Any]]:
    return [
        {
            "id": app_config.LOCAL_TEST_MCP_ID,
            "componentId": app_config.LOCAL_TEST_MCP_ID,
            "name": app_config.LOCAL_TEST_MCP_NAME,
            "description": (
                "本地测试 MCP（mcp_server/tool/local_test_mcp.py，"
                "工具：echo_text、add_numbers、current_time、reverse_text）"
            ),
            "type": "http",
            "url": app_config.LOCAL_TEST_MCP_URL,
        }
    ]
