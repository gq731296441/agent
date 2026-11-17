#!/usr/bin/env python3
"""
PDM 部件编号校验 API 客户端
替代原 CMDB pdm_component 表全量查询，通过 Spring Boot 接口批量校验部件编号是否存在。

接口：POST /v1/pdm/verify_part
请求头：X-Emp-No / X-Auth-Value
请求体：
{
    "partNos": ["130000233321", "130000233374"]
}
响应：
{
    "code": 0,
    "msg": "成功",
    "data": [
        { "partNo": "130000233321", "exists": false },
        { "partNo": "130000233374", "exists": true }
    ],
    "status": true
}
"""

import os
import logging
from typing import Dict, List, Optional, Set

from .base_client import BaseAPIClient, APIConfig

logger = logging.getLogger(__name__)

# 接口端点（可用环境变量覆盖，默认本地 Spring Boot）
DEFAULT_VERIFY_URL = os.environ.get(
    "PDM_VERIFY_API_URL", "http://10.57.153.195:8080/v1/pdm/verify_part")

# 单次批量校验的最大编号数量，避免单次请求体过大
BATCH_SIZE = 100


class PdmVerifyAPIClient(BaseAPIClient):
    """PDM 部件编号校验 API 客户端"""

    def __init__(self, base_url: str = None, timeout: int = 30, max_retries: int = 3):
        """
        初始化 PDM 部件校验 API 客户端

        Args:
            base_url: 校验接口完整 URL，默认取环境变量 PDM_VERIFY_API_URL
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
            url=base_url or DEFAULT_VERIFY_URL,
            timeout=timeout,
            max_retries=max_retries,
            headers=headers
        )
        super().__init__(config)

    def verify_parts(self, part_nos: List[str]) -> Dict[str, bool]:
        """
        批量校验部件编号是否存在

        Args:
            part_nos: 部件编号列表（自动去重、分批）

        Returns:
            partNo -> exists 的映射字典
        """
        # 去重并过滤空值
        unique_nos = [str(n).strip() for n in part_nos if n and str(n).strip()]
        if not unique_nos:
            return {}

        exists_map: Dict[str, bool] = {}
        for i in range(0, len(unique_nos), BATCH_SIZE):
            batch = unique_nos[i:i + BATCH_SIZE]
            batch_map = self._verify_batch(batch)
            exists_map.update(batch_map)

        return exists_map

    def _verify_batch(self, part_nos: List[str]) -> Dict[str, bool]:
        """调用接口校验单批编号"""
        result = self.post(self.config.url, json={"partNos": part_nos})

        if not result:
            logger.warning(f"PDM校验接口返回空结果: {part_nos}")
            return {}

        # 校验业务状态码
        code = result.get('code')
        if code not in (0, '0', None):
            logger.warning(f"PDM校验接口业务错误: code={code}, msg={result.get('msg')}")
            return {}

        data = result.get('data')
        batch_map: Dict[str, bool] = {}
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    part_no = str(item.get('partNo', '') or '').strip()
                    if part_no:
                        batch_map[part_no] = bool(item.get('exists', False))

        logger.debug(f"PDM校验结果: {len(batch_map)}/{len(part_nos)} 条")
        return batch_map


# 便捷函数（懒加载单例，避免重复创建会话）
_client = None


def create_pdm_verify_api_client() -> PdmVerifyAPIClient:
    """创建 PDM 部件校验 API 客户端实例"""
    global _client
    if _client is None:
        _client = PdmVerifyAPIClient()
    return _client
