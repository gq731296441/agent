#!/usr/bin/env python3
"""
PDM 文档信息 API 客户端
替代原直接调用 pdmweb PDM 文档接口（含硬编码 token），通过 Spring Boot 接口获取文档信息。

接口1：GET /v1/pdm/doc_detail?docNo=xxx
响应：
{
    "code": 0, "msg": "成功",
    "data": { "found": true, "docNo": "...", "version": "D",
              "docName": "...", "lifecycleStatus": "审批完成" },
    "status": true
}

接口2：GET /v1/pdm/doc_info?docNo=xxx
响应：
{
    "code": 0, "msg": "成功",
    "data": { "product": "...", "team1": "...", "team2": "...", "team3": "...",
              "projectName": "...", "lastUpdatedDate": "...", "nameCn": "...",
              "version": "D", "type1": "...", "type2": "...", "type3": "...",
              "lastUpdatedBy": "..." },
    "status": true
}
"""

import os
import logging
from typing import Dict, Optional

from .base_client import BaseAPIClient, APIConfig

logger = logging.getLogger(__name__)

# 接口端点（可用环境变量覆盖，默认本地 Spring Boot）
DEFAULT_DETAIL_URL = os.environ.get(
    "PDM_DOC_DETAIL_API_URL", "http://10.57.153.195:8080/v1/pdm/doc_detail")
DEFAULT_INFO_URL = os.environ.get(
    "PDM_DOC_INFO_API_URL", "http://10.57.153.195:8080/v1/pdm/doc_info")


class PdmDocAPIClient(BaseAPIClient):
    """PDM 文档信息 API 客户端"""

    def __init__(self, detail_url: str = None, info_url: str = None,
                 timeout: int = 30, max_retries: int = 3):
        """
        初始化 PDM 文档 API 客户端

        Args:
            detail_url: doc_detail 接口完整 URL，默认取环境变量 PDM_DOC_DETAIL_API_URL
            info_url: doc_info 接口完整 URL，默认取环境变量 PDM_DOC_INFO_API_URL
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
            url=detail_url or DEFAULT_DETAIL_URL,
            timeout=timeout,
            max_retries=max_retries,
            headers=headers
        )
        super().__init__(config)

        self.detail_url = detail_url or DEFAULT_DETAIL_URL
        self.info_url = info_url or DEFAULT_INFO_URL

    def get_doc_detail(self, doc_no: str) -> Optional[Dict]:
        """
        查询文档详情（是否存在、生命周期状态等）

        Args:
            doc_no: 文档编号

        Returns:
            文档详情 data 字典；文档不存在或调用失败时返回 None
        """
        if not doc_no:
            return None

        result = self.get(self.detail_url, params={'docNo': doc_no})
        if not result:
            logger.warning(f"PDM文档详情接口返回空结果: {doc_no}")
            return None

        code = result.get('code')
        if code not in (0, '0', None):
            logger.warning(f"PDM文档详情接口业务错误: code={code}, msg={result.get('msg')}")
            return None

        data = result.get('data')
        if isinstance(data, dict) and data.get('found'):
            return data

        return None

    def get_doc_info(self, doc_no: str) -> Optional[Dict]:
        """
        查询文档详细信息

        Args:
            doc_no: 文档编号

        Returns:
            文档信息 data 字典；查询失败时返回 None
        """
        if not doc_no:
            return None

        result = self.get(self.info_url, params={'docNo': doc_no})
        if not result:
            logger.warning(f"PDM文档信息接口返回空结果: {doc_no}")
            return None

        code = result.get('code')
        if code not in (0, '0', None):
            logger.warning(f"PDM文档信息接口业务错误: code={code}, msg={result.get('msg')}")
            return None

        data = result.get('data')
        if isinstance(data, dict):
            return data

        logger.warning(f"PDM文档信息接口未返回有效数据: {doc_no}")
        return None


# 便捷函数（懒加载单例，避免重复创建会话）
_client = None


def create_pdm_doc_api_client() -> PdmDocAPIClient:
    """创建 PDM 文档 API 客户端实例"""
    global _client
    if _client is None:
        _client = PdmDocAPIClient()
    return _client
