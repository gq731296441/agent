#!/usr/bin/env python3
"""
API客户端基类
提供重试机制、认证管理、错误处理等通用功能
"""

import requests
import time
import logging
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class APIConfig:
    """API配置类"""
    url: str
    timeout: int = 30
    max_retries: int = 3
    retry_delay: float = 1.0
    headers: Optional[Dict[str, str]] = None

class BaseAPIClient:
    """API客户端基类"""

    def __init__(self, config: APIConfig):
        self.config = config
        self.session = requests.Session()
        if config.headers:
            self.session.headers.update(config.headers)

    def request(self, method: str, endpoint: str, **kwargs) -> Optional[Dict]:
        """
        发送HTTP请求，包含重试逻辑

        Args:
            method: HTTP方法 (GET, POST等)
            endpoint: 端点URL或路径
            **kwargs: 传递给requests的参数

        Returns:
            响应JSON字典或None
        """
        url = endpoint if endpoint.startswith('http') else f"{self.config.url.rstrip('/')}/{endpoint.lstrip('/')}"

        for attempt in range(self.config.max_retries):
            try:
                logger.debug(f"API请求 (尝试 {attempt + 1}): {method} {url}")
                response = self.session.request(
                    method=method,
                    url=url,
                    timeout=self.config.timeout,
                    **kwargs
                )

                if response.status_code == 200:
                    return response.json()
                else:
                    logger.warning(f"API返回非200状态码: {response.status_code} - {response.text}")

            except requests.RequestException as e:
                logger.warning(f"API调用异常 (尝试 {attempt + 1}): {str(e)}")
                if attempt < self.config.max_retries - 1:
                    time.sleep(self.config.retry_delay * (2 ** attempt))  # 指数退避
                    continue

        return None

    def post(self, endpoint: str, json: Any = None, **kwargs) -> Optional[Dict]:
        """POST请求"""
        return self.request('POST', endpoint, json=json, **kwargs)

    def get(self, endpoint: str, params: Any = None, **kwargs) -> Optional[Dict]:
        """GET请求"""
        return self.request('GET', endpoint, params=params, **kwargs)

    def close(self):
        """关闭会话"""
        self.session.close()

class RetryHandler:
    """重试处理器"""

    @staticmethod
    def execute_with_retry(func, max_retries: int = 3, delay: float = 1.0, *args, **kwargs):
        """
        带重试执行函数

        Args:
            func: 要执行的函数
            max_retries: 最大重试次数
            delay: 重试延迟（秒）
            *args, **kwargs: 传递给func的参数

        Returns:
            func的返回值
        """
        last_exception = None
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                if attempt < max_retries - 1:
                    logger.warning(f"函数 {func.__name__} 执行失败 (尝试 {attempt + 1}): {str(e)}")
                    time.sleep(delay * (2 ** attempt))
                else:
                    logger.error(f"函数 {func.__name__} 执行失败，已达最大重试次数")

        raise last_exception if last_exception else RuntimeError("未知错误")