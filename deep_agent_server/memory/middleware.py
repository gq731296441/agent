"""Deep run 前注入 User Memory。"""

from typing import Any, Optional

from langchain.messages import SystemMessage

from config.config import app_config
from deep_agent_server.memory.injection import build_memory_prompt_block
from deep_agent_server.memory.user_memory import get_user_memory_service


async def inject_user_memory_into_stream_input(
    stream_input: Any,
    *,
    store: Any,
    user_id: str,
    query: str,
) -> Any:
    if not app_config.USER_MEMORY_INJECT_ENABLED or not query:
        return stream_input

    svc = get_user_memory_service(store)
    memories = await svc.search(user_id, query=query)
    block = build_memory_prompt_block(memories)
    if not block:
        return stream_input

    if isinstance(stream_input, dict) and "messages" in stream_input:
        msgs = list(stream_input["messages"])
        msgs.insert(0, SystemMessage(content=block))
        return {**stream_input, "messages": msgs}
    return stream_input
