from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
import httpx
from httpx import Timeout, Limits
from config import app_config, get_logger
from typing import Any, Optional, Dict
from langchain_core.language_models.chat_models import BaseChatModel

logger = get_logger(__name__)

async_client = httpx.AsyncClient(
    trust_env=False,  # 忽略系统代理
    timeout=Timeout(300.0, read=300.0, write=300.0, connect=60.0),
    limits=Limits(max_connections=100, max_keepalive_connections=20),
    # 某些私有部署后端对 HTTP/2 支持不佳，若报错持续，可尝试强制 HTTP/1.1
    # http2=False 
)

class ChatOpenAIWithReasoning(ChatOpenAI):
   def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict, # 源码中明确是 dict
        default_chunk_class: type,
        base_generation_info: Optional[dict],
    ) -> Optional[ChatGenerationChunk]:
        
        # 调用父类方法，获取初步转换的 generation_chunk
        # 此时 generation_chunk.message 里的 additional_kwargs 还没有推理内容
        gen_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        
        if gen_chunk is None:
            return None

        # 从原始 chunk 中提取推理内容（兼容 reasoning_content / reasoning）
        try:
            reasoning = None
            choices = chunk.get("choices", [])
            if choices:
                delta = choices[0].get("delta", {})
                reasoning = delta.get("reasoning_content") or delta.get("reasoning")

            if not reasoning:
                model_extra = chunk.get("model_extra", {})
                if isinstance(model_extra, dict):
                    reasoning = model_extra.get("reasoning")

            if reasoning and hasattr(gen_chunk, "message") and isinstance(gen_chunk.message, AIMessageChunk):
                if gen_chunk.message.additional_kwargs is None:
                    gen_chunk.message.additional_kwargs = {}
                # 统一向下游暴露 reasoning_content，兼容现有消费逻辑
                gen_chunk.message.additional_kwargs["reasoning_content"] = reasoning
        except Exception as e:
            logger.debug(f"⚠️ [拦截异常] 提取推理内容出错: {e}")

        return gen_chunk

def _require_api_key(api_key: Optional[str], name: str) -> str:
    if not api_key or not str(api_key).strip():
        raise ValueError(f"未配置 {name}，请在 .env 中设置对应环境变量")
    return api_key

def _base_llm_kwargs() -> Dict[str, Any]:
    return {
        "temperature": 0.1,
        "streaming": True,
        "max_retries": 5,
        "http_client": httpx.Client(
            trust_env=False,
            timeout=Timeout(300.0, read=300.0, write=300.0, connect=60.0)
        ),
        "http_async_client": async_client,
    }

def _agent_llm_config() -> Dict[str, Any]:
    config = _base_llm_kwargs()
    config.update({
        "model": app_config.LLM_AGENT_MODEL,
        "base_url": app_config.LLM_AGENT_BASE_URL,
        "api_key": _require_api_key(app_config.LLM_AGENT_API_KEY, "LLM_AGENT_API_KEY"),
    })
    return config

def _aux_llm_config(**overrides: Any) -> Dict[str, Any]:
    config = _base_llm_kwargs()
    config.update({
        "model": app_config.LLM_AUX_MODEL,
        "base_url": app_config.LLM_AUX_BASE_URL,
        "api_key": _require_api_key(app_config.LLM_AUX_API_KEY, "LLM_AUX_API_KEY"),
    })
    config.update(overrides)
    return config

def create_agent_llm():
    try:
        llm = ChatOpenAIWithReasoning(**_agent_llm_config())
        logger.debug(f"✅ 模型创建成功")
        return llm
    except Exception as e:
        logger.error(f"❌ 模型创建失败, 错误信息: {e}")
        raise

def create_tool_judge_llm():
    try:
        llm = ChatOpenAIWithReasoning(**_aux_llm_config(
            temperature=0.0,
            streaming=False,
        ))
        logger.debug(f"✅ 工具结果判断模型创建成功")
        return llm
    except Exception as e:
        logger.error(f"❌ 工具结果判断模型创建失败, 错误信息: {e}")
        raise

def create_summary_llm():
    try:
        llm = ChatOpenAI(**_aux_llm_config(
            temperature=0.1,
            streaming=False,
            max_tokens=1500,
        ))
        logger.debug(f"✅ 历史总结模型创建成功")
        return llm
    except Exception as e:
        logger.error(f"❌ 历史总结模型创建失败, 错误信息: {e}")
        raise

def create_title_llm():
    try:
        llm = ChatOpenAIWithReasoning(**_agent_llm_config())
        logger.debug(f"✅ 标题生成模型创建成功")
        return llm
    except Exception as e:
        logger.error(f"❌ 标题生成模型创建失败, 错误信息: {e}")
        raise


def create_llm_from_config(
    *,
    model: str,
    base_url: str,
    api_key: str,
    streaming: bool = True,
    temperature: float = 0.1,
    max_tokens: Optional[int] = None,
    use_reasoning: bool = True,
) -> BaseChatModel:
    config = _base_llm_kwargs()
    config.update({
        "model": model,
        "base_url": base_url,
        "api_key": _require_api_key(api_key, "llm_api_key"),
        "streaming": streaming,
        "temperature": temperature,
    })
    if max_tokens is not None:
        config["max_tokens"] = max_tokens
    if use_reasoning:
        return ChatOpenAIWithReasoning(**config)
    return ChatOpenAI(**config)


async def create_user_aux_llms(user_id: str) -> Dict[str, Any]:
    """工具判断 / 历史摘要 / 会话标题共用的快速模型（未配置 fast 时回退主模型）。"""
    from db import deep_storage

    model, base_url, api_key = await deep_storage.resolve_user_aux_llm_credentials(user_id)
    tool_judge = create_llm_from_config(
        model=model,
        base_url=base_url,
        api_key=api_key,
        streaming=False,
        temperature=0.0,
        use_reasoning=True,
    )
    summary = create_llm_from_config(
        model=model,
        base_url=base_url,
        api_key=api_key,
        streaming=False,
        temperature=0.1,
        max_tokens=1500,
        use_reasoning=False,
    )
    title = create_llm_from_config(
        model=model,
        base_url=base_url,
        api_key=api_key,
        streaming=False,
        temperature=0.1,
        use_reasoning=True,
    )
    return {"tool_judge": tool_judge, "summary": summary, "title": title}


async def create_title_llm_for_user(user_id: str):
    aux = await create_user_aux_llms(user_id)
    return aux["title"]


async def create_agent_llm_for_user(user_id: str, agent_kind: str = "default"):
    """默认与订阅 Agent 均使用用户界面配置的 user_secrets。"""
    from db import deep_storage

    _ = agent_kind  # 保留参数供调用方区分会话类型；LLM 来源统一
    model = await deep_storage.get_user_secret(user_id, "llm_model")
    base_url = await deep_storage.get_user_secret(user_id, "llm_base_url")
    api_key = await deep_storage.get_user_secret(user_id, "llm_api_key")
    if not model or not base_url or not api_key:
        raise ValueError("请先配置模型 API Key 与 Base URL")
    return create_llm_from_config(model=model, base_url=base_url, api_key=api_key)


def llm_config_fingerprint(model: str, base_url: str) -> str:
    import hashlib
    payload = f"{model}|{base_url}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def create_global_embedding_fn():
    """为 AsyncPostgresStore index 创建全局 embedding 函数。"""
    from config.config import app_config

    if not app_config.STORE_INDEX_ENABLED:
        return None
    model = (app_config.STORE_EMBEDDING_MODEL or "").strip()
    if not model:
        logger.warning("[Embedding] STORE_EMBEDDING_MODEL 未配置，禁用 Store 索引")
        return None

    base_url = app_config.STORE_EMBEDDING_BASE_URL or app_config.LLM_AUX_BASE_URL
    api_key = app_config.STORE_EMBEDDING_API_KEY or app_config.LLM_AUX_API_KEY
    try:
        api_key = _require_api_key(api_key, "STORE_EMBEDDING_API_KEY 或 LLM_AUX_API_KEY")
    except ValueError as e:
        logger.warning(f"[Embedding] {e}，禁用 Store 索引")
        return None

    from langchain_openai import OpenAIEmbeddings

    embeddings = OpenAIEmbeddings(
        model=model,
        base_url=base_url,
        api_key=api_key,
        http_client=httpx.Client(trust_env=False, timeout=Timeout(60.0)),
        http_async_client=async_client,
    )
    return embeddings
