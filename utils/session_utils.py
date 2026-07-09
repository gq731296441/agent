# utils/session_utils.py
"""
会话工具函数，供 WebSocket 和 SSE 接口共用。
"""
import json
import uuid
from uuid import UUID

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


async def generate_session_title(
    title_llm,
    question: str,
    session_id: str,
    reply: str | None = None
) -> str:
    """基于用户首问（可选加助手回复）生成会话标题，失败时回退到默认标题。

    Args:
        title_llm: 标题生成 LLM 实例（可为 None，此时使用兜底标题）
        question: 用户首条提问
        session_id: 会话 ID
        reply: 首轮助手回复（延迟生成时传入，提供更准确的标题）
    """
    from langchain_core.messages import HumanMessage
    from agent_server.prompts import TITLE_GENERATION_PROMPT

    fallback_title = _build_fallback_session_title(session_id)
    normalized_question = (question or "").strip()

    # 构造 messages_text：有 reply 时同时包含用户问题和助手回复
    if reply:
        normalized_reply = (reply or "").strip()
        if normalized_reply:
            messages_text = (
                f"用户问题:\n{normalized_question[:500]}\n\n"
                f"助手回复:\n{normalized_reply[:500]}"
            )
        else:
            messages_text = normalized_question[:1000]
    else:
        messages_text = normalized_question[:1000]

    if not messages_text.strip():
        return fallback_title

    if title_llm is None:
        return fallback_title

    async def _try_generate(llm):
        prompt = TITLE_GENERATION_PROMPT.format(messages=messages_text)
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


async def deferred_generate_and_update_title(
    session_id: str,
    user_id: str,
    question: str,
    reply: str | None = None,
) -> str:
    """首轮 Agent 完成后延迟生成会话标题。

    优先使用调用方传入的 reply（已在流消费过程中积累，避免 DB 查询），
    未传入时回退到从 chat_history 提取。全程异常安全，失败时返回兜底标题。

    Args:
        session_id: 会话 ID
        user_id: 用户 ID
        question: 用户首条提问
        reply: 助手首轮回复文本（可选，由调用方在流消费过程中积累）

    Returns:
        str: 新标题（生成失败时返回兜底标题）
    """
    from agent_server.llm import create_title_llm_for_user, create_title_llm
    from db import storage

    fallback = _build_fallback_session_title(session_id)

    try:
        # 1. 获取助手回复：优先使用调用方传入的（已在内存中积累），否则从 DB 查询
        if not reply:
            recent = await storage.fetch_history_by_session(
                UUID(session_id) if isinstance(session_id, str) else session_id,
                limit=20,
                offset=0,
            )
            reply_parts = []
            for record in recent:
                content = record.get("content")
                if isinstance(content, str):
                    try:
                        content = json.loads(content)
                    except (json.JSONDecodeError, TypeError):
                        pass
                text = ""
                if isinstance(content, dict):
                    text = content.get("text", "") or content.get("content", "")
                elif isinstance(content, str):
                    text = content
                if (
                    str(record.get("sender", "")).lower() == "assistant"
                    and str(record.get("type", "")).lower() == "chat"
                    and text
                ):
                    reply_parts.append(text)
            reply_parts.reverse()
            reply = "\n".join(reply_parts) or None

        # 2. 创建标题 LLM（per-user，失败时回退全局）
        title_llm = None
        try:
            title_llm = await create_title_llm_for_user(user_id)
        except (ValueError, Exception):
            try:
                title_llm = create_title_llm()
            except Exception:
                pass

        if title_llm is None:
            logger.info(f"【会话】无法创建标题 LLM session={session_id}，使用兜底标题")
            return fallback

        # 3. 生成标题（有 reply 时质量更高，无 reply 时仅用 question）
        new_title = await generate_session_title(
            title_llm, question, session_id, reply=reply
        )

        # 4. 更新 DB
        await storage.update_session_title(user_id, session_id, new_title)

        logger.info(
            f"【会话】延迟标题生成完成 session={session_id} title={new_title}"
        )
        return new_title

    except Exception as e:
        logger.warning(
            f"【会话】延迟标题生成失败 session={session_id} error={e}"
        )
        return fallback
