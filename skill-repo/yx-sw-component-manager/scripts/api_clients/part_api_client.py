#!/usr/bin/env python3
"""
部件详情API客户端
查询PDM系统中的部件详细信息
"""

import os
import logging
from typing import Optional, Dict, Any
from .base_client import BaseAPIClient, APIConfig

logger = logging.getLogger(__name__)

class PartAPIClient(BaseAPIClient):
    """部件详情API客户端"""

    def __init__(self, base_url: str = None, timeout: int = 30, max_retries: int = 3):
        """
        初始化部件API客户端

        Args:
            base_url: API基础URL，默认从环境变量或使用默认值
            timeout: 请求超时时间（秒）
            max_retries: 最大重试次数
        """
        base_url = base_url or "http://pdmweb.zte.com.cn/zte-plm-iproduct-gbom/gbom/v2"
        headers = {
            'X-System-Code': '100000461002',
            'Content-Type': 'application/json'
        }

        config = APIConfig(
            url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            headers=headers
        )
        super().__init__(config)

    def query_part(self, part_number: str, version: str = "latest", all_lifecycle: str = "1") -> Optional[Dict]:
        """
        查询部件详情

        Args:
            part_number: 部件编号
            version: 版本，默认latest
            all_lifecycle: 是否查询所有生命周期状态，默认"1"

        Returns:
            部件详情字典或None
        """
        payload = {
            "NOs": [
                {
                    "no": part_number,
                    "version": version,
                    "allLifecycle": all_lifecycle
                }
            ],
            "infoBlocks": [
                {
                    "block": "G0000",
                    "ver": "v2"
                }
            ]
        }

        result = self.post('/query', json=payload)
        if not result:
            logger.warning(f"部件API返回空结果: {part_number}")
            return None

        # 解析响应，提取部件数据
        component = self._extract_component_from_response(result)
        if component:
            logger.debug(f"成功获取部件信息: {part_number}")
        else:
            logger.warning(f"无法从响应中提取部件数据: {result}")

        return component

    def _extract_component_from_response(self, response: Dict) -> Optional[Dict]:
        """
        从API响应中提取部件数据

        API可能返回的结构：
        - { "code": {"code": "0000"}, "bo": [{ "PART_NO": "...", "basic": [{详情}] }] }
        - { "code": {"code": "0000"}, "data": {...} }
        - { "success": true, "bo": [...] }
        """
        # 检查响应是否成功
        code = response.get('code', {}).get('code') if isinstance(response.get('code'), dict) else None
        success = response.get('success', code in ['0000', 0])

        if not success and code not in ['0000', 0, None]:
            logger.warning(f"API返回错误码: {code}")
            return None

        # 尝试提取部件数据
        if 'bo' in response:
            bo = response['bo']
            if isinstance(bo, list) and len(bo) > 0:
                component_wrapper = bo[0]
                # 检查是否有 basic 字段（嵌套的部件详情）
                if 'basic' in component_wrapper:
                    basic = component_wrapper['basic']
                    if isinstance(basic, list) and len(basic) > 0:
                        # 返回 basic 中的第一个元素（实际的部件详情）
                        return basic[0]
                    elif isinstance(basic, dict):
                        return basic
                # 没有 basic 字段，返回原始对象
                return component_wrapper
            elif isinstance(bo, dict):
                return bo
        elif 'data' in response:
            return response['data']
        elif 'basic' in response:
            return response

        # 如果响应本身看起来就是部件数据
        if all(k in response for k in ('NAME_CN', 'NAME_EN', 'EN_CODE')):
            return response

        return None

    def validate_part_exists(self, part_number: str) -> bool:
        """
        验证部件是否存在且有效

        Args:
            part_number: 部件编号

        Returns:
            部件是否存在且有效
        """
        component = self.query_part(part_number)
        if not component:
            return False

        status = component.get('STATUS')
        if status == '失效':
            logger.info(f"部件 {part_number} 状态为失效")
            return False

        return True

    def get_part_status(self, part_number: str) -> Optional[str]:
        """
        获取部件状态

        Args:
            part_number: 部件编号

        Returns:
            部件状态字符串或None
        """
        component = self.query_part(part_number)
        return component.get('STATUS') if component else None

# 便捷函数
def create_part_api_client() -> PartAPIClient:
    """创建部件API客户端实例"""
    return PartAPIClient()