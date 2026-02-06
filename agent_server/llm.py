from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
import httpx
from httpx import Timeout, Limits
from config import app_config, get_logger
from typing import Any, Optional, Dict

logger = get_logger(__name__)

async_client = httpx.AsyncClient(
    trust_env=False,  # 忽略系统代理
    timeout=Timeout(300.0, read=300.0, write=300.0, connect=60.0),
    limits=Limits(max_connections=100, max_keepalive_connections=20),
    # 某些私有部署后端对 HTTP/2 支持不佳，若报错持续，可尝试强制 HTTP/1.1
    # http2=False 
)

from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from typing import Any, Optional, Dict

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

        # 从原始的 chunk 字典中提取被父类丢弃的 reasoning_content
        try:
            # 这里的路径完全对应你源码中 choices = chunk.get("choices", []) 的逻辑
            choices = chunk.get("choices", [])
            if choices:
                delta = choices[0].get("delta", {})
                reasoning = delta.get("reasoning_content")
                
                if reasoning:
                    # 打印调试信息，确认拦截到数据
                    # print(f"🔥 [拦截成功] 捕获到推理内容: {reasoning}")
                    
                    # 3. 注入到 AIMessageChunk 的 additional_kwargs 中
                    if hasattr(gen_chunk, "message") and isinstance(gen_chunk.message, AIMessageChunk):
                        if gen_chunk.message.additional_kwargs is None:
                            gen_chunk.message.additional_kwargs = {}
                        gen_chunk.message.additional_kwargs["reasoning_content"] = reasoning
        except Exception as e:
            print(f"⚠️ [拦截异常] 提取推理内容出错: {e}")

        return gen_chunk

llm_config = {
    "model": "nebulacoder-cot-v7.0", # nebulacoder-lite-v7.0, nebulacoder-v6.0, nebulacoder-cot-v6.0, nebulacoder-v7.0, nebulacoder-cot-v7.0都可以
    "base_url":"https://nebulacoder-maas.zte.com.cn/v1",
    "api_key": "508d5fbe-df85-4309-b79f-8c96e3f93732",
    # "model": "Qwen3-235B-A22B",
    # "base_url":"https://maas-apigateway.dt.zte.com.cn/model/qwen3-235b-a22b/v1",
    # "api_key": "hy9uux4119wwmcp7gw979k2taklaek7p",
    "temperature": 0.1,
    "streaming": True,
    # "model_kwargs": {"stream_options": {"include_usage": True}},
    "max_retries": 5,
    # 使用 trust_env=False 忽略系统代理
    "http_client": httpx.Client(
        trust_env=False,
        timeout=Timeout(300.0, read=300.0, write=300.0, connect=60.0)
    ),
    "http_async_client": async_client
}

def create_agent_llm():
    # logger.info(
    #     f"🤖 创建聊天模型: {llm_config} "
    #     f"(temperature={temperature}, streaming={streaming})"
    # )
    try:
        llm = ChatOpenAIWithReasoning(**llm_config)
        logger.debug(f"✅ 模型创建成功")
        return llm
    except Exception as e:
        logger.error(f"❌ 模型创建失败, 错误信息: {e}")
        raise

def create_tool_judge_llm():
    tool_judge_llm_config = llm_config.copy()
    tool_judge_llm_config.update({
        "model": "nebulacoder-lite-v7.0",
        "temperature": 0.0,
        "streaming": False,
    })
    try:
        llm = ChatOpenAIWithReasoning(**tool_judge_llm_config)
        logger.debug(f"✅ 工具结果判断模型创建成功")
        return llm
    except Exception as e:
        logger.error(f"❌ 工具结果判断模型创建失败, 错误信息: {e}")
        raise