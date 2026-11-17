#!/usr/bin/env python3
"""
一致性检查器
检查Excel数据与API返回结果的一致性
"""

import logging
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

class ConsistencyChecker:
    """一致性检查器"""

    # 字段映射：Excel字段名 -> API字段名
    # 注意：新API返回的name字段包含组件的规范名称
    # API1: name字段, API2: compName字段（已在client中标准化为name）
    FIELD_MAPPING = {
        '中文名称': ['name'],
        '英文名称': ['name'],
        '英文代号': ['name'],
        '部件类别': ['opsTypeIdName', 'typeName', 'PART_TYPE']
    }

    def __init__(self):
        """初始化一致性检查器"""
        pass

    def check_fields(self, excel_row: Dict[str, Any], api_data: Dict[str, Any],
                    fields_to_check: List[str] = None) -> List[Dict[str, Any]]:
        """
        检查字段一致性

        Args:
            excel_row: Excel行数据
            api_data: API返回的数据
            fields_to_check: 要检查的字段列表，默认使用所有映射字段

        Returns:
            不一致的字段列表
        """
        discrepancies = []

        if fields_to_check is None:
            fields_to_check = list(self.FIELD_MAPPING.keys())

        for excel_field in fields_to_check:
            api_fields = self.FIELD_MAPPING.get(excel_field, [excel_field])

            # 获取Excel值
            excel_value = excel_row.get(excel_field)
            if excel_value is None:
                continue

            # 规范化Excel值
            excel_value_norm = self._normalize_value(excel_value)

            # 从API数据中获取对应的值
            api_value = None
            for api_field in api_fields:
                if api_field in api_data:
                    api_value = api_data[api_field]
                    break

            if api_value is None:
                logger.debug(f"API数据中未找到字段 {excel_field} 对应的值 (尝试: {api_fields})")
                continue

            # 规范化API值
            api_value_norm = self._normalize_value(api_value)

            # 比较
            if excel_value_norm != api_value_norm:
                discrepancy = {
                    'field': excel_field,
                    'excel_value': excel_value,
                    'api_value': api_value,
                    'excel_value_normalized': excel_value_norm,
                    'api_value_normalized': api_value_norm
                }
                discrepancies.append(discrepancy)
                logger.debug(f"发现不一致: {excel_field} -> Excel: {excel_value!r} vs API: {api_value!r}")

        return discrepancies

    def _normalize_value(self, value: Any) -> str:
        """
        规范化值用于比较

        Args:
            value: 任意值

        Returns:
            规范化后的字符串
        """
        if value is None:
            return ''
        return str(value).strip()

    def has_discrepancy(self, excel_row: Dict[str, Any], api_data: Dict[str, Any],
                       fields_to_check: List[str] = None) -> bool:
        """
        检查是否存在不一致

        Args:
            excel_row: Excel行数据
            api_data: API返回数据
            fields_to_check: 要检查的字段列表

        Returns:
            是否存在不一致
        """
        discrepancies = self.check_fields(excel_row, api_data, fields_to_check)
        return len(discrepancies) > 0

    def get_corrections(self, excel_row: Dict[str, Any], api_data: Dict[str, Any],
                       fields_to_check: List[str] = None) -> Dict[str, Any]:
        """
        获取需要修正的字段

        Args:
            excel_row: Excel行数据
            api_data: API返回数据
            fields_to_check: 要检查的字段列表

        Returns:
            需要修正的字段字典 {字段名: API值}
        """
        corrections = {}
        discrepancies = self.check_fields(excel_row, api_data, fields_to_check)

        for disc in discrepancies:
            field = disc['field']
            api_value = disc['api_value']
            corrections[field] = api_value

        return corrections

    def apply_corrections(self, df, row_index: int, corrections: Dict[str, Any]) -> Dict[str, Any]:
        """
        应用修正到DataFrame

        Args:
            df: pandas DataFrame
            row_index: 行索引
            corrections: 修正字典 {字段名: 新值}

        Returns:
            修正记录
        """
        applied = {}

        for field, new_value in corrections.items():
            if field in df.columns:
                old_value = df.at[row_index, field]
                df.at[row_index, field] = new_value
                applied[field] = {
                    'old_value': old_value,
                    'new_value': new_value
                }
                logger.info(f"第{row_index + 1}行 {field}: '{old_value}' -> '{new_value}'")

        return applied

# 便捷函数
def check_consistency(excel_row: Dict[str, Any], api_data: Dict[str, Any], fields: List[str] = None) -> bool:
    """便捷函数：检查一致性"""
    checker = ConsistencyChecker()
    return not checker.has_discrepancy(excel_row, api_data, fields)

def get_corrections_from_api(excel_row: Dict[str, Any], api_data: Dict[str, Any], fields: List[str] = None) -> Dict[str, Any]:
    """便捷函数：获取修正"""
    checker = ConsistencyChecker()
    return checker.get_corrections(excel_row, api_data, fields)