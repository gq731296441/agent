#!/usr/bin/env python3
"""
代码库存在性校验 API 客户端
替代原 CMDB view_gerrit_repo_list 表全量查询，通过 Spring Boot 接口批量校验代码库是否存在。

接口：POST /v1/gerrit/check_repo
请求头：X-Emp-No / X-Auth-Value
请求体：
{
    "repoNames": ["pol_pon_d/PON_D_elephants/packages/common/oss",
                  "pol_pon_d/PON_D_elephants/packages/common/oss/lob"]
}
响应：
{
    "code": 0,
    "msg": "成功",
    "data": [
        { "repoName": "pol_pon_d/.../oss", "exists": true },
        { "repoName": "pol_pon_d/.../oss/lob", "exists": false }
    ],
    "status": true
}
"""

import os
import logging
from typing import Dict, List

from .base_client import BaseAPIClient, APIConfig

logger = logging.getLogger(__name__)

# 接口端点（可用环境变量覆盖，默认本地 Spring Boot）
DEFAULT_CHECK_URL = os.environ.get(
    "REPO_CHECK_API_URL", "http://10.57.153.195:8080/v1/gerrit/check_repo")

# 单次批量校验的最大代码库数量，避免单次请求体过大
BATCH_SIZE = 100


class RepoCheckAPIClient(BaseAPIClient):
    """代码库存在性校验 API 客户端"""

    def __init__(self, base_url: str = None, timeout: int = 30, max_retries: int = 3):
        """
        初始化代码库校验 API 客户端

        Args:
            base_url: 校验接口完整 URL，默认取环境变量 REPO_CHECK_API_URL
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
            url=base_url or DEFAULT_CHECK_URL,
            timeout=timeout,
            max_retries=max_retries,
            headers=headers
        )
        super().__init__(config)

    def check_repos(self, repo_names: List[str]) -> Dict[str, bool]:
        """
        批量校验代码库是否存在

        Args:
            repo_names: 代码库名称列表（自动去重、分批）

        Returns:
            repoName -> exists 的映射字典
        """
        # 去重并过滤空值
        unique_names = [str(n).strip() for n in repo_names if n and str(n).strip()]
        if not unique_names:
            return {}

        exists_map: Dict[str, bool] = {}
        for i in range(0, len(unique_names), BATCH_SIZE):
            batch = unique_names[i:i + BATCH_SIZE]
            batch_map = self._check_batch(batch)
            exists_map.update(batch_map)

        return exists_map

    def _check_batch(self, repo_names: List[str]) -> Dict[str, bool]:
        """调用接口校验单批代码库"""
        result = self.post(self.config.url, json={"repoNames": repo_names})

        if not result:
            logger.warning(f"代码库校验接口返回空结果: {repo_names}")
            return {}

        # 校验业务状态码
        code = result.get('code')
        if code not in (0, '0', None):
            logger.warning(f"代码库校验接口业务错误: code={code}, msg={result.get('msg')}")
            return {}

        data = result.get('data')
        batch_map: Dict[str, bool] = {}
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    repo_name = str(item.get('repoName', '') or '').strip()
                    if repo_name:
                        batch_map[repo_name] = bool(item.get('exists', False))

        logger.debug(f"代码库校验结果: {len(batch_map)}/{len(repo_names)} 条")
        return batch_map


# 便捷函数（懒加载单例，避免重复创建会话）
_client = None


def create_repo_check_api_client() -> RepoCheckAPIClient:
    """创建代码库校验 API 客户端实例"""
    global _client
    if _client is None:
        _client = RepoCheckAPIClient()
    return _client
