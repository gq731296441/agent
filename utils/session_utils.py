# utils/session_utils.py
"""
会话工具函数，供 WebSocket 和 SSE 接口共用。
"""
import uuid
from db import storage
from config import get_logger

logger = get_logger(__name__)

SESSION_ACCESS_DENIED_MSG = "无权访问该会话或会话不存在"


async def validate_session_access(
    user_id: str,
    session_id: str,
    *,
    allow_if_missing: bool = False,
) -> bool:
    """校验 (user_id, session_id) 归属，委托 storage 层实现。"""
    return await storage.validate_session_access(
        user_id,
        session_id,
        allow_if_missing=allow_if_missing,
    )


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
        logger.warning(
            f"【会话】标题清洗后为空 session={session_id} "
            f"raw_title={repr(title)}"
        )
        return _build_fallback_session_title(session_id)
    return cleaned[:30]


async def setup_new_session(
    user_id: str,
    session_id: str,
    session_title: str,
    agent_id: str | None = None,
    agent_kind: str = "default",
) -> str | None:
    """创建新 session 记录、绑定 agent_id、初始化 Deep 工作区目录。"""
    from config.config import app_config
    from db import deep_storage
    from deep_agent_server.workspace import ensure_session_dirs

    resolved_agent_id = agent_id
    resolved_kind = agent_kind or "default"
    if resolved_kind == "subscription" and not resolved_agent_id:
        raise ValueError("订阅会话必须提供 agent_id")

    await storage.upsert_user_session(
        user_id=user_id,
        session_id=session_id,
        session_name=session_title,
        agent_id=resolved_agent_id if resolved_kind == "subscription" else None,
        agent_kind=resolved_kind,
    )

    if (app_config.AGENT_RUNTIME or "deep").lower() != "light":
        await ensure_session_dirs(user_id, session_id)
        from deep_agent_server.session.models import SessionStartStatus

        await deep_storage.upsert_session_start_task(
            session_id, user_id, SessionStartStatus.CREATED.value
        )

    return resolved_agent_id if resolved_kind == "subscription" else None


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

    async def _try_generate(llm):
        prompt = TITLE_GENERATION_PROMPT.format(messages=normalized_question[:1000])
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        result = getattr(response, "content", "")
        if isinstance(result, list):
            result = "".join(str(item) for item in result)
        return str(result)

    try:
        raw_title = await _try_generate(title_llm)
        logger.info(
            f"【会话】标题 LLM 原始返回 session={session_id} "
            f"raw={repr(raw_title)} len={len(raw_title)}"
        )
        # 如果 per-user LLM 返回空（CoT 模型 token 不足），用全局 AUX 模型重试
        if not raw_title.strip():
            logger.warning(
                f"【会话】标题 LLM 返回空内容 session={session_id}，"
                f"尝试全局 AUX 模型重试"
            )
            try:
                from agent_server.llm import create_title_llm
                fallback_llm = create_title_llm()
                raw_title = await _try_generate(fallback_llm)
                logger.info(
                    f"【会话】标题全局 AUX 重试 session={session_id} "
                    f"raw={repr(raw_title)} len={len(raw_title)}"
                )
            except Exception as retry_e:
                logger.warning(f"【会话】全局 AUX 重试也失败: {retry_e}")
        return _sanitize_session_title(raw_title, session_id)
    except Exception as e:
        logger.warning(f"【会话】生成会话标题失败，使用兜底标题: {e}")
        return fallback_title
