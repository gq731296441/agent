"""默认（非订阅）智能体运行时配置 — 不落 user_agents 表。"""

from agent_server.prompts import build_system_prompt

DEFAULT_BUILTIN_TOOLS = [
    "write_todos", "read_file", "write_file", "edit_file",
    "ls", "glob", "grep", "task",
]


def build_default_agent_dict(user_id: str, user_token: str = None) -> dict:
    return {
        "id": None,
        "user_id": user_id,
        "name": "默认智能体",
        "description": "",
        "system_prompt": build_system_prompt(user_id=user_id, user_token=user_token),
        "config": {
            "enabled_builtin_tools": list(DEFAULT_BUILTIN_TOOLS),
            "timeout_seconds": 300,
        },
        "config_version": 0,
        "visibility": "public",
        "agent_kind": "default",
    }
