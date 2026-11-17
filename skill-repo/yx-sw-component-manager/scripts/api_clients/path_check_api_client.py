#!/usr/bin/env python3
"""
Gerrit 路径存在性校验 API 客户端
替代原"clone 代码库到本地再检查路径"的方式，通过 Spring Boot 接口远程校验路径是否存在。

接口：POST /v1/gerrit/check_path
请求头：X-Emp-No / X-Auth-Value
请求体：
{
    "repoName": "WirelineTech/Gerrit-access",
    "branch": "authority_sync",
    "path": "source/Gerrit/.gitignore"
}
响应（存在文件/目录）：
{
    "code": 0,
    "msg": "成功",
    "data": { "exists": true, "type": "file", "message": "路径存在(文件)" },
    "status": true
}
{
    "code": 0,
    "msg": "成功",
    "data": { "exists": true, "type": "directory", "message": "目录存在" },
    "status": true
}
"""

import os
import logging
from typing import Optional, Tuple

from .base_client import BaseAPIClient, APIConfig

logger = logging.getLogger(__name__)

# 接口端点（可用环境变量覆盖，默认本地 Spring Boot）
DEFAULT_CHECK_URL = os.environ.get(
    "PATH_CHECK_API_URL", "http://10.57.153.195:8080/v1/gerrit/check_path")

# 校验使用的分支（默认 master，可用环境变量覆盖）
DEFAULT_BRANCH = os.environ.get("GERRIT_CHECK_BRANCH", "master")


class PathCheckAPIClient(BaseAPIClient):
    """Gerrit 路径存在性校验 API 客户端"""

    def __init__(self, base_url: str = None, branch: str = None,
                 timeout: int = 30, max_retries: int = 3):
        """
        初始化 Gerrit 路径校验 API 客户端

        Args:
            base_url: 校验接口完整 URL，默认取环境变量 PATH_CHECK_API_URL
            branch: 校验使用的分支，默认取环境变量 GERRIT_CHECK_BRANCH 或 master
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

        self.branch = branch or DEFAULT_BRANCH

    def check_path(self, repo_name: str, path: str) -> Tuple[bool, str]:
        """
        校验代码库中指定路径是否存在

        Args:
            repo_name: 代码库名称
            path: 代码路径（相对路径，非空）

        Returns:
            (exists, message) 元组
            - 路径存在：message 为接口返回的提示（如"目录存在"/"路径存在(文件)"）
            - 路径不存在/接口异常：message 为原因说明
        """
        if not repo_name:
            return False, '代码库为空'

        # 规范化路径：去首尾空白和斜杠
        path = (path or '').strip().strip('/')
        if not path:
            return True, '路径为空，无需校验'

        payload = {
            "repoName": repo_name,
            "branch": self.branch,
            "path": path
        }

        result = self.post(self.config.url, json=payload)

        if not result:
            logger.warning(f"路径校验接口返回空结果: {repo_name}/{path}")
            return False, f'路径校验接口调用失败: {repo_name}/{path}'

        # 校验业务状态码
        code = result.get('code')
        if code not in (0, '0', None):
            logger.warning(f"路径校验接口业务错误: code={code}, msg={result.get('msg')}")
            return False, f'路径校验接口业务错误: {result.get("msg")}'

        data = result.get('data')
        if isinstance(data, dict):
            exists = bool(data.get('exists', False))
            message = str(data.get('message', '') or '')
            if not message:
                message = '路径存在' if exists else '路径不存在'
            logger.debug(f"路径校验结果: {repo_name}/{path} exists={exists} msg={message}")
            return exists, message

        logger.warning(f"路径校验接口未返回有效数据: {repo_name}/{path}")
        return False, f'路径校验接口未返回有效数据: {repo_name}/{path}'


# 便捷函数（懒加载单例，避免重复创建会话）
_client = None


def create_path_check_api_client() -> PathCheckAPIClient:
    """创建 Gerrit 路径校验 API 客户端实例"""
    global _client
    if _client is None:
        _client = PathCheckAPIClient()
    return _client
