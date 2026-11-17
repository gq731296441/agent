#!/usr/bin/env python3
"""
PDM 部件编号查询 API 客户端
替代原直接调用 pdmweb PDM GBOM 查询接口，通过 Spring Boot 接口按名称/代号查询部件编号。

接口：POST /v1/pdm/query_part
请求头：X-Emp-No / X-Auth-Value
请求体：
{
    "nameCn": "XSP5产品intfirb组件",
    "nameEn": "xsp5-intfirb",
    "enCode": "5960X-HN-intfirb-l1"
}
响应：
{
    "code": 0, "msg": "成功",
    "data": [
        {
            "partNo": "130000249711",
            "nameCn": "XSP5产品intfirb组件",
            "nameEn": "xsp5-intfirb",
            "enCode": "5960X-HN-intfirb-l1",
            "version": "AB",
            "lifecycleStatus": "审批完成",
            "productNo": "100000455962",
            "product": "NEP",
            "project": "装备软件NEP平台V3.0(310001257272)"
        }
    ],
    "status": true
}
"""

import os
import logging
from typing import Dict, List, Optional

from .base_client import BaseAPIClient, APIConfig

logger = logging.getLogger(__name__)

# 接口端点（可用环境变量覆盖，默认本地 Spring Boot）
DEFAULT_QUERY_URL = os.environ.get(
    "PDM_QUERY_API_URL", "http://10.57.153.195:8080/v1/pdm/query_part")


class PdmQueryAPIClient(BaseAPIClient):
    """PDM 部件编号查询 API 客户端"""

    def __init__(self, base_url: str = None, timeout: int = 60, max_retries: int = 3):
        """
        初始化 PDM 查询 API 客户端

        Args:
            base_url: 查询接口完整 URL，默认取环境变量 PDM_QUERY_API_URL
            timeout: 请求超时时间（秒），默认 60（查询响应较慢）
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
            url=base_url or DEFAULT_QUERY_URL,
            timeout=timeout,
            max_retries=max_retries,
            headers=headers
        )
        super().__init__(config)

    def query_part(self, name_cn: str = None, name_en: str = None,
                   en_code: str = None) -> Optional[List[Dict]]:
        """
        按名称/代号查询部件

        Args:
            name_cn: 中文名称
            name_en: 英文名称
            en_code: 英文代号

        Returns:
            部件列表（data 数组）；查询失败时返回 None
        """
        payload = {}
        if name_cn and str(name_cn).strip():
            payload['nameCn'] = str(name_cn).strip()
        if name_en and str(name_en).strip():
            payload['nameEn'] = str(name_en).strip()
        if en_code and str(en_code).strip():
            payload['enCode'] = str(en_code).strip()

        if not payload:
            return []

        result = self.post(self.config.url, json=payload)
        if not result:
            logger.warning(f"PDM查询接口返回空结果: {payload}")
            return None

        code = result.get('code')
        if code not in (0, '0', None):
            logger.warning(f"PDM查询接口业务错误: code={code}, msg={result.get('msg')}")
            return None

        data = result.get('data')
        if isinstance(data, list):
            return data

        logger.warning(f"PDM查询接口未返回有效数据: {payload}")
        return None


# 便捷函数（懒加载单例，避免重复创建会话）
_client = None


def create_pdm_query_api_client() -> PdmQueryAPIClient:
    """创建 PDM 查询 API 客户端实例"""
    global _client
    if _client is None:
        _client = PdmQueryAPIClient()
    return _client
