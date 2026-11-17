#!/usr/bin/env python3
"""
Gerrit 代码库/目录拆分 API 客户端
替代原 CMDB view_gerrit_repo_list 表查询，通过 Spring Boot 接口完成拆分。

接口：GET /v1/gerrit/split_repo_path?repoPath=<url-encoded>
请求头：X-Emp-No / X-Auth-Value
响应：
{
    "code": 0,
    "msg": "成功",
    "data": [
        { "repo": "<最长匹配repo名>", "path": "<剩余路径>" }
    ],
    "status": true
}
语义：
- 传入整个"代码库/目录"字符串，接口自动拆分为最长匹配 repo + 剩余 path
- 匹配不到时 data 内 repo 为空字符串，整个字符串放在 path 中
"""

import os
import logging
from typing import Tuple

from .base_client import BaseAPIClient, APIConfig

logger = logging.getLogger(__name__)

# 接口端点（可用环境变量覆盖，默认本地 Spring Boot）
DEFAULT_SPLIT_URL = os.environ.get(
    "GERRIT_SPLIT_API_URL", "http://10.57.153.195:8080/v1/gerrit/split_repo_path")


class GerritAPIClient(BaseAPIClient):
    """Gerrit 代码库/目录拆分 API 客户端"""

    def __init__(self, base_url: str = None, timeout: int = 30, max_retries: int = 3):
        """
        初始化 Gerrit 拆分 API 客户端

        Args:
            base_url: 拆分接口完整 URL，默认取环境变量 GERRIT_SPLIT_API_URL
            timeout: 请求超时时间（秒）
            max_retries: 最大重试次数
        """
        # 依次尝试多个认证环境变量别名，兼容不同运行环境
        token = (os.getenv("coclaw_token") or os.getenv("RDC_AUTH_TOKEN")
                 or os.getenv("X_Auth_Value") or os.getenv("X_AUTH_VALUE"))
        empno = (os.getenv("coclaw_empno") or os.getenv("RDC_EMP_NO")
                 or os.getenv("X_Emp-No") or os.getenv("X_EMP_NO"))

        headers = {'Content-Type': 'application/json'}
        if token:
            headers['X-Auth-Value'] = token
        else:
            logger.warning("未找到 X-Auth-Value（环境变量 coclaw_token）")

        if empno:
            headers['X-Emp-No'] = empno
        else:
            logger.warning("未找到 X-Emp-No（环境变量 coclaw_empno）")

        config = APIConfig(
            url=base_url or DEFAULT_SPLIT_URL,
            timeout=timeout,
            max_retries=max_retries,
            headers=headers
        )
        super().__init__(config)

    def split_repo_path(self, repo_path: str) -> Tuple[str, str]:
        """
        拆分"代码库/目录"字符串为 repo + path

        Args:
            repo_path: 完整的"代码库/目录"字符串（单个条目，不含分隔符）

        Returns:
            (repo, path) 元组；匹配不到时 repo 为空字符串，整个字符串在 path 中
        """
        if not repo_path or not str(repo_path).strip():
            return '', ''

        # requests 的 params 会自动做 URL 编码
        result = self.get(self.config.url, params={'repoPath': repo_path})

        if not result:
            logger.warning(f"Gerrit拆分接口返回空结果: {repo_path}")
            return '', str(repo_path)

        # 校验业务状态码
        code = result.get('code')
        if code not in (0, '0', None):
            logger.warning(f"Gerrit拆分接口业务错误: code={code}, msg={result.get('msg')}")
            return '', str(repo_path)

        data = result.get('data')
        if isinstance(data, list) and len(data) > 0:
            item = data[0]
            repo = str(item.get('repo', '') or '').strip()
            path = str(item.get('path', '') or '').strip()
            logger.debug(f"Gerrit拆分结果: repo={repo!r}, path={path!r}")
            return repo, path

        logger.warning(f"Gerrit拆分接口未返回有效数据: {repo_path}")
        return '', str(repo_path)


# 便捷函数（懒加载单例，避免重复创建会话）
_client = None


def create_gerrit_api_client() -> GerritAPIClient:
    """创建 Gerrit 拆分 API 客户端实例"""
    global _client
    if _client is None:
        _client = GerritAPIClient()
    return _client
