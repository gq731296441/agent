#!/usr/bin/env python3
"""
第三方组件API客户端
查询iComp系统中的外部组件信息
支持两个API：
1. outer/component/list - 两段式组件（如0013-0004）
2. third/comp/list - 三段式组件（如0202-0000-0829）
"""

import os
import logging
from typing import Optional, Dict, Any, List
from .base_client import BaseAPIClient, APIConfig

logger = logging.getLogger(__name__)

# API端点
API1_URL = "https://icomp.zte.com.cn/zte-plm-icomp-gateway/zte-plm-icomp-comp/outer/component/list"
API2_URL = "https://icomp.zte.com.cn/zte-plm-icomp-gateway/zte-plm-icomp-comp/third/comp/list"


class ComponentAPIClient(BaseAPIClient):
    """第三方组件API客户端"""

    def __init__(self, timeout: int = 30, max_retries: int = 3):
        """
        初始化第三方组件API客户端

        Args:
            timeout: 请求超时时间（秒）
            max_retries: 最大重试次数
        """
        token = os.getenv("coclaw_token")
        empno = os.getenv("coclaw_empno")

        headers = {'Content-Type': 'application/json'}
        if token:
            headers['X-Auth-Value'] = token
        else:
            logger.warning("未找到 X-Auth-Value")

        if empno:
            headers['X-Emp-No'] = empno
        else:
            logger.warning("未找到 X-Emp-No")

        # 使用第一个API的URL作为base_url，实际请求时会动态选择
        config = APIConfig(
            url=API1_URL,
            timeout=timeout,
            max_retries=max_retries,
            headers=headers
        )
        super().__init__(config)

        self.token = token
        self.empno = empno

    def _is_three_segment(self, part_number: str) -> bool:
        """
        判断部件编号是否为三段式

        三段式格式：XXXX-XXXX-XXXX（如0202-0000-0829）
        两段式格式：XXXX-XXXX（如0013-0004）
        """
        if not part_number:
            return False
        parts = part_number.split('-')
        return len(parts) == 3

    def _call_api1(self, part_number: str, page_no: int = 1, page_size: int = 10) -> Optional[Dict]:
        """
        调用第一个API（outer/component/list）- 用于两段式组件

        请求格式：{"pageNo":1,"pageSize":10,"queryType":0,"strName":"0356-0044",...}
        响应格式：bo.list[]，list的第一条数据就是
        """
        payload = {
            "pageNo": page_no,
            "pageSize": page_size,
            "queryType": 0,
            "strName": part_number,
            "opsName": "",
            "stName": "",
            "btName": "",
            "projHomepage": "",
            "guardProject": "",
            "strGuarder": "",
            "countryCode": [],
            "libraryType": ""
        }

        result = self.post(API1_URL, json=payload)
        return result

    def _call_api2(self, part_number: str, page_no: int = 1, page_size: int = 10) -> Optional[Dict]:
        """
        调用第二个API（third/comp/list）- 用于三段式组件

        请求格式：{"specialFilter":1,"pageNo":1,"pageSize":10,"compIdOrName":"0202-0000-0829"}
        响应格式：bo.list[]，list的第一条数据就是
        """
        payload = {
            "specialFilter": 1,
            "pageNo": page_no,
            "pageSize": page_size,
            "compIdOrName": part_number
        }

        result = self.post(API2_URL, json=payload)
        return result

    def _extract_components_from_response(self, response: Dict, part_number: str, use_api2: bool = False) -> Optional[List[Dict]]:
        """
        从API响应中提取组件列表

        响应格式：bo.list[]，list的第一条数据就是
        use_api2: 是否使用API2的字段格式（compName, availableStatus）
        """
        if not response:
            return None

        # 尝试从 bo.list 获取数据
        bo = response.get('bo', {})
        if isinstance(bo, dict):
            data_list = bo.get('list', [])
        elif isinstance(bo, list):
            data_list = bo
        else:
            logger.warning(f"响应中bo字段格式异常: {type(bo)}")
            return None

        if not isinstance(data_list, list) or len(data_list) == 0:
            logger.debug(f"bo.list为空或不存在: {part_number}")
            return None

        # 获取第一条数据
        item = data_list[0]

        # 标准化字段名，统一返回格式
        if use_api2:
            # API2: compName, availableStatus
            item = {
                'name': item.get('compName', ''),
                'status': item.get('availableStatus', ''),
                'externalId': item.get('compId', item.get('compCode', '')),
                'opsTypeIdName': item.get('compTypeName', ''),
                'guardProjectName': item.get('projName', ''),
                'guarder': item.get('compManager', ''),
                'country': item.get('compNation', ''),
                'tags': item.get('tagList', []),
                'createdTime': item.get('createTime', ''),
                'updatedTime': item.get('updateTime', ''),
            }
        else:
            # API1: name, statusName
            item = {
                'name': item.get('name', ''),
                'status': item.get('statusName', ''),
                'externalId': item.get('externalId', ''),
                'opsTypeIdName': item.get('opsTypeIdName', ''),
                'guardProjectName': item.get('guardProjectName', ''),
                'guarder': item.get('guarder', ''),
                'country': item.get('country', ''),
                'tags': item.get('tags', []),
                'createdTime': item.get('createdTime', ''),
                'updatedTime': item.get('updatedTime', ''),
            }

        logger.debug(f"找到数据: {part_number}, name={item.get('name')}, status={item.get('status')}")
        return [item]

    def search_component(self, part_number: str, page_no: int = 1, page_size: int = 10) -> Optional[List[Dict]]:
        """
        搜索第三方组件

        Args:
            part_number: 部件编号
            page_no: 页码，默认1
            page_size: 每页大小，默认10

        Returns:
            组件列表或None
        """
        is_three_segment = self._is_three_segment(part_number)

        if is_three_segment:
            logger.debug(f"三段式组件，使用API2查询: {part_number}")
            result = self._call_api2(part_number, page_no, page_size)
        else:
            logger.debug(f"两段式组件，使用API1查询: {part_number}")
            result = self._call_api1(part_number, page_no, page_size)

        if not result:
            logger.warning(f"第三方组件API返回空结果: {part_number}")
            return None

        # 提取组件列表
        components = self._extract_components_from_response(result, part_number, use_api2=is_three_segment)
        if components:
            logger.debug(f"找到 {len(components)} 个匹配的第三方组件: {part_number}")
        else:
            logger.warning(f"未找到匹配的第三方组件: {part_number}")

        return components

    def get_component_by_id(self, part_number: str) -> Optional[Dict]:
        """
        根据部件编号获取单个组件信息

        Args:
            part_number: 部件编号

        Returns:
            组件字典或None
        """
        components = self.search_component(part_number, page_size=1)
        if components and len(components) > 0:
            return components[0]
        return None

    def validate_component_exists(self, part_number: str) -> bool:
        """
        验证第三方组件是否存在

        Args:
            part_number: 部件编号

        Returns:
            组件是否存在
        """
        component = self.get_component_by_id(part_number)
        return component is not None

    def get_component_status(self, part_number: str) -> Optional[str]:
        """
        获取第三方组件状态

        Args:
            part_number: 部件编号

        Returns:
            组件状态字符串或None
        """
        component = self.get_component_by_id(part_number)
        return component.get('status') if component else None


# 便捷函数
def create_component_api_client() -> ComponentAPIClient:
    """创建第三方组件API客户端实例"""
    return ComponentAPIClient()
