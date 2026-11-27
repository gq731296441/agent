from collections.abc import AsyncIterator, Iterator
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from pydantic import Field
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

_THINKING_REJECT_HINTS = ("chat_template_kwargs", "thinking", "extra_body")


def _reasoning_extra_body(model: str) -> Optional[Dict[str, Any]]:
    """按总开关 + 模型名白名单生成 thinking extra_body；不匹配返回 None。

    REASONING_REQUEST_MODELS:
      - "none"：关闭注入
      - 空串：对所有模型生效
      - 逗号分隔子串：模型名包含任一子串即注入（默认 deepseek）
    """
    if not getattr(app_config, "REASONING_REQUEST_ENABLED", True):
        return None
    raw = str(getattr(app_config, "REASONING_REQUEST_MODELS", "deepseek") or "")
    stripped = raw.strip()
    if stripped.lower() == "none":
        return None
    model_l = (model or "").lower()
    if stripped:
        tokens = [t.strip().lower() for t in stripped.split(",") if t.strip()]
        if tokens and not any(token in model_l for token in tokens):
            return None
    return {"chat_template_kwargs": {"thinking": True}}


def _apply_thinking(llm: BaseChatModel, model: str) -> BaseChatModel:
    """把 thinking 参数挂到 LLM 实例 extra_body 上，并记录 thinking_extra_body。"""
    extra = _reasoning_extra_body(model)
    if not extra:
        return llm
    current = dict(getattr(llm, "extra_body", None) or {})
    chat_kwargs = dict(current.get("chat_template_kwargs") or {})
    chat_kwargs.update(extra.get("chat_template_kwargs") or {})
    current["chat_template_kwargs"] = chat_kwargs
    llm.extra_body = current
    if isinstance(llm, ChatOpenAIWithReasoning):
        llm.thinking_extra_body = extra
    logger.info(
        "[LLM] 已为模型 {} 注入 thinking extra_body={}",
        getattr(llm, "model_name", model) or "?",
        extra,
    )
    return llm


def _http_status_code(exc: BaseException) -> Optional[int]:
    seen: set[int] = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        status = getattr(cur, "status_code", None)
        if isinstance(status, int):
            return status
        resp = getattr(cur, "response", None)
        if resp is not None:
            sc = getattr(resp, "status_code", None)
            if isinstance(sc, int):
                return sc
        cur = cur.__cause__ or getattr(cur, "__context__", None)
    return None


def _error_text(exc: BaseException) -> str:
    parts = [str(exc), type(exc).__name__]
    message = getattr(exc, "message", None)
    if message:
        parts.append(str(message))
    return " ".join(parts).lower()


def _is_thinking_param_rejected(exc: BaseException, thinking_attached: bool) -> bool:
    """上游 400 且报错提到 thinking / extra_body 时，才允许摘参重试。"""
    if not thinking_attached:
        return False
    status = _http_status_code(exc)
    text = _error_text(exc)
    if status is None:
        if "error code: 400" in text or "badrequesterror" in text or "bad request" in text:
            status = 400
    if status != 400:
        return False
    return any(hint in text for hint in _THINKING_REJECT_HINTS)


def _extra_body_has_thinking(extra_body: Any) -> bool:
    if not isinstance(extra_body, dict):
        return False
    chat_kwargs = extra_body.get("chat_template_kwargs")
    return isinstance(chat_kwargs, dict) and bool(chat_kwargs.get("thinking"))


class ChatOpenAIWithReasoning(ChatOpenAI):
    # 记录本次注入的 thinking extra_body；上游 400 拒绝后据此摘参重试一次。
    # 不用 PrivateAttr：langchain ChatOpenAI 在部分版本上会踩 ModelPrivateAttr。
    thinking_extra_body: Optional[Dict[str, Any]] = Field(default=None, exclude=True)

    def _thinking_attached(self) -> bool:
        return bool(self.thinking_extra_body) or _extra_body_has_thinking(self.extra_body)

    def _strip_thinking_extra_body(self) -> None:
        extra = dict(self.extra_body or {})
        chat_kwargs = dict(extra.get("chat_template_kwargs") or {})
        chat_kwargs.pop("thinking", None)
        if chat_kwargs:
            extra["chat_template_kwargs"] = chat_kwargs
        else:
            extra.pop("chat_template_kwargs", None)
        self.extra_body = extra or None
        self.thinking_extra_body = None

    def _should_retry_without_thinking(self, exc: BaseException) -> bool:
        if not _is_thinking_param_rejected(exc, self._thinking_attached()):
            return False
        model_name = getattr(self, "model_name", "?")
        logger.warning(
            "[LLM] 上游拒绝 thinking 参数，已摘除 extra_body 后重试一次 model={} err={}",
            model_name,
            exc,
        )
        self._strip_thinking_extra_body()
        return True

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

    def _stream(self, *args: Any, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        started = False
        try:
            for chunk in super()._stream(*args, **kwargs):
                started = True
                yield chunk
        except Exception as exc:
            if started or not self._should_retry_without_thinking(exc):
                raise
            yield from super()._stream(*args, **kwargs)

    async def _astream(self, *args: Any, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        started = False
        try:
            async for chunk in super()._astream(*args, **kwargs):
                started = True
                yield chunk
        except Exception as exc:
            if started or not self._should_retry_without_thinking(exc):
                raise
            async for chunk in super()._astream(*args, **kwargs):
                yield chunk

    def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
        try:
            return super()._generate(*args, **kwargs)
        except Exception as exc:
            if not self._should_retry_without_thinking(exc):
                raise
            return super()._generate(*args, **kwargs)

    async def _agenerate(self, *args: Any, **kwargs: Any) -> ChatResult:
        try:
            return await super()._agenerate(*args, **kwargs)
        except Exception as exc:
            if not self._should_retry_without_thinking(exc):
                raise
            return await super()._agenerate(*args, **kwargs)


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
        _apply_thinking(llm, app_config.LLM_AGENT_MODEL)
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
    enable_thinking: bool = False,
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
    llm = ChatOpenAIWithReasoning(**config) if use_reasoning else ChatOpenAI(**config)
    if enable_thinking:
        _apply_thinking(llm, model)
    return llm


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
        enable_thinking=True,
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
