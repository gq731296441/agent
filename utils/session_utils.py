# utils/session_utils.py
"""
会话工具函数，供 WebSocket 和 SSE 接口共用。
"""
import uuid
from db import storage
from config import get_logger

logger = get_logger(__name__)


async def get_or_create_session(client_session_id: str | None):
    """
    检查或创建 session_id。
    返回 (session_id, is_new_session)
    """
    if isinstance(client_session_id, str):
        client_session_id = client_session_id.strip()
        if client_session_id.lower() == 'null' or client_session_id == '':
            client_session_id = None

    session_id = None
    is_new_session = False

    try:
        if client_session_id:
            exists = await storage.session_exists(client_session_id)
            if exists:
                session_id = client_session_id
            else:
                session_id = str(uuid.uuid4())
                is_new_session = True
        else:
            session_id = str(uuid.uuid4())
            is_new_session = True
    except Exception as e:
        logger.error(f"【会话】 session_exists check failed: {e}, 生成新的会话...")
        session_id = str(uuid.uuid4())
        is_new_session = True

    return session_id, is_new_session


def _build_fallback_session_title(session_id: str) -> str:
    return f"新会话 {session_id[-8:]}"


def _sanitize_session_title(title: str, session_id: str) -> str:
    """清洗模型生成的标题，避免返回空值或带引号/换行。"""
    cleaned = (title or "").strip().strip('"').strip("'")
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return _build_fallback_session_title(session_id)
    return cleaned[:30]


async def generate_session_title(title_llm, question: str, session_id: str) -> str:
    """基于用户首问生成会话标题，失败时回退到默认标题。

    Args:
        title_llm: 标题生成 LLM 实例（可为 None，此时使用兜底标题）
        question: 用户首条提问
        session_id: 会话 ID
    """
    from langchain_core.messages import HumanMessage
    from agent_server.prompts import TITLE_GENERATION_PROMPT

    fallback_title = _build_fallback_session_title(session_id)
    normalized_question = (question or "").strip()
    if not normalized_question:
        return fallback_title

    if title_llm is None:
        return fallback_title

    try:
        prompt = TITLE_GENERATION_PROMPT.format(messages=normalized_question[:1000])
        response = await title_llm.ainvoke([
            HumanMessage(content=prompt),
        ])
        title = getattr(response, "content", "")
        if isinstance(title, list):
            title = "".join(str(item) for item in title)
        return _sanitize_session_title(str(title), session_id)
    except Exception as e:
        logger.warning(f"【会话】生成会话标题失败，使用兜底标题: {e}")
        return fallback_title
