from langchain_openai import ChatOpenAI

import httpx

llm_config = {
    "model": "nebulacoder-pro-v7.0", # nebulacoder-lite-v7.0, nebulacoder-v6.0, nebulacoder-cot-v6.0, nebulacoder-v7.0, nebulacoder-cot-v7.0都可以
    "base_url":"http://nebulacoder.dev.zte.com.cn:40081/v1",
    "api_key": "10344649",
    "temperature": 0.1,
    "streaming": True,
    # 使用 trust_env=False 忽略系统代理
    "http_client": httpx.Client(trust_env=False)
}

def get_llm():
    llm = ChatOpenAI(**llm_config)
    return llm