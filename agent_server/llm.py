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
    timeout=Timeout(1200.0, read=1200.0, write=1200.0, connect=60.0),
    limits=Limits(max_connections=100, max_keepalive_connections=20),
    # 某些私有部署后端对 HTTP/2 支持不佳，若报错持续，可尝试强制 HTTP/1.1
    # http2=False 
)

# 流式 chunk 诊断计数器（模块级变量，避免 Pydantic ModelPrivateAttr 问题）
_llm_chunk_counters = {"total": 0, "tool_args_chunks": 0}

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

        # 诊断：统计 tool_calls 参数的流式 chunk
        # try:
        #     choices = chunk.get("choices", [])
        #     if choices:
        #         delta = choices[0].get("delta", {})
        #         tool_calls = delta.get("tool_calls") or []
        #         if tool_calls:
        #             _llm_chunk_counters["tool_args_chunks"] += 1
        #             for tc in tool_calls:
        #                 fn = tc.get("function") or {}
        #                 args = fn.get("arguments") or ""
        #                 _llm_chunk_counters["total"] += 1
        #                 args_preview = args[:300] + ("..." if len(args) > 300 else "")
        #                 logger.info(
        #                     f"[LLM_CHUNK] #{_llm_chunk_counters['total']} "
        #                     f"(tc_batch #{_llm_chunk_counters['tool_args_chunks']}), "
        #                     f"index={tc.get('index')}, name={fn.get('name')}, "
        #                     f"args_len={len(args)}, id={repr(tc.get('id'))}"
        #                     f"\n  args_preview={repr(args_preview)}"
        #                 )
        # except Exception as e:
        #     logger.debug(f"⚠️ [LLM_CHUNK诊断] 提取 tool_calls 参数出错: {e}")

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

def normalize_base_url(base_url: str) -> str:
    """把 Base URL 规范化为 OpenAI 客户端要求的「版本前缀」形式。

    OpenAI SDK 会在 base_url 后自行拼接 `/chat/completions`。用户如果直接填了
    完整接口路径（如 `.../v1/chat/completions`），实际请求会变成
    `.../v1/chat/completions/chat/completions` 而 404。这里统一去掉误填的
    接口后缀，仅保留到 `/v1` 这一层。
    """
    url = (base_url or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/completions", "/responses"):
        if url.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
            break
    return url


def _require_api_key(api_key: Optional[str], name: str) -> str:
    if not api_key or not str(api_key).strip():
        raise ValueError(f"未配置 {name}，请在 .env 中设置对应环境变量")
    return api_key

def _base_llm_kwargs() -> Dict[str, Any]:
    return {
        "temperature": 0.1,
        "streaming": True,
        "max_retries": 10,  # 5→10，429 短期退避约 55s 总等待
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
        "base_url": normalize_base_url(app_config.LLM_AGENT_BASE_URL),
        "api_key": _require_api_key(app_config.LLM_AGENT_API_KEY, "LLM_AGENT_API_KEY"),
    })
    return config

def _aux_llm_config(**overrides: Any) -> Dict[str, Any]:
    config = _base_llm_kwargs()
    config.update({
        "model": app_config.LLM_AUX_MODEL,
        "base_url": normalize_base_url(app_config.LLM_AUX_BASE_URL),
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
        "base_url": normalize_base_url(base_url),
        "api_key": _require_api_key(api_key, "llm_api_key"),
        "streaming": streaming,
        "temperature": temperature,
    })
    if max_tokens is not None:
        config["max_tokens"] = max_tokens
    else:
        # 不传 max_tokens 时 API 用默认上限（实测约 16K token），长工具参数
        # （如 write 大文件）可能被截断导致 JSON 不完整执行失败。
        # 显式覆盖为更大的默认值；若模型真实上限低于此值，API 会报 400，再调低即可。
        config["max_tokens"] = 32768
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
        use_reasoning=False,
    )
    return {"tool_judge": tool_judge, "summary": summary, "title": title}


async def create_title_llm_for_user(user_id: str):
    """创建 per-user 标题生成 LLM（仅创建 title，不浪费创建 tool_judge/summary）。"""
    from db import deep_storage

    model, base_url, api_key = await deep_storage.resolve_user_aux_llm_credentials(user_id)
    return create_llm_from_config(
        model=model,
        base_url=base_url,
        api_key=api_key,
        streaming=False,
        temperature=0.1,
        use_reasoning=False,
    )


async def create_agent_llm_for_user(user_id: str, agent_kind: str = "default"):
    """默认与订阅 Agent 均使用用户当前生效的 LLM 配置（user_llm_profiles）。"""
    from db import deep_storage

    _ = agent_kind  # 保留参数供调用方区分会话类型；LLM 来源统一
    await deep_storage.ensure_user_llm_profile(user_id)
    profile = await deep_storage.get_active_user_llm_profile(user_id)
    if not profile or not profile["model"] or not profile["base_url"] or not profile["api_key"]:
        raise ValueError("请先配置模型 API Key 与 Base URL")
    return create_llm_from_config(
        model=profile["model"],
        base_url=profile["base_url"],
        api_key=profile["api_key"],
    )


def llm_config_fingerprint(model: str, base_url: str) -> str:
    import hashlib
    # 与实际构造 LLM 时使用的 base_url 保持一致，避免同一配置产生两个指纹
    payload = f"{model}|{normalize_base_url(base_url)}"
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
