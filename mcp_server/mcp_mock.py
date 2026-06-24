"""本地开发用 MCP Mock 数据（外部 cmtool 接口不可用时）。"""

from typing import Any, Dict, List

from config.config import app_config


def get_default_mock_mcp_list() -> List[Dict[str, Any]]:
    """返回默认 Mock MCP 列表，含本地 mrm_doc_mcp.py 测试服务。"""
    return [
        {
            "id": app_config.MCP_MRM_MOCK_ID,
            "componentId": app_config.MCP_MRM_MOCK_ID,
            "name": app_config.MCP_MRM_MOCK_NAME,
            "description": (
                "MRM 单据系统（本地测试服务 mcp_server/tool/mrm_doc_mcp.py，"
                "需先启动: python mcp_server/tool/mrm_doc_mcp.py）"
            ),
            "type": "http",
            "url": app_config.MCP_MRM_SERVER_URL,
        },
    ]


def merge_mock_mcp_list(external_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """将默认 Mock MCP 合并进外部列表（按 id 去重，Mock 项优先保留）。"""
    mock_items = get_default_mock_mcp_list()
    mock_ids = {item["id"] for item in mock_items}
    merged = list(mock_items)
    for item in external_list or []:
        if item.get("id") not in mock_ids:
            merged.append(item)
    return merged
