#!/usr/bin/env python3
"""
字段验证器
验证Excel行中各个字段的值
"""

import logging
from typing import Dict, Any, List, Optional, Tuple
from enum import Enum

logger = logging.getLogger(__name__)

class ValidationError(Exception):
    """验证错误异常"""
    def __init__(self, error_type: str, message: str, row: int, field: str = None):
        self.error_type = error_type
        self.message = message
        self.row = row
        self.field = field
        super().__init__(f"第{row}行{f' ({field})' if field else ''}: {message}")

class FieldValidator:
    """字段验证器"""

    # 部件类别枚举值
    VALID_COMPONENT_CATEGORIES = [
        '系统', '软件', '软件子系统', '软件模块', '组件', '外部第三方组件'
    ]

    def __init__(self):
        """初始化字段验证器"""
        pass

    def validate_row(self, row_data: Dict[str, Any], row_index: int) -> Tuple[bool, List[Dict]]:
        """
        验证单行数据

        Args:
            row_data: 行数据字典
            row_index: 行索引（从0开始）

        Returns:
            (是否通过验证, 错误列表)
        """
        errors = []

        try:
            # 1. 必填字段检查
            self._check_required_fields(row_data, row_index, errors)

            # 2. 第一行层次修正检查
            self._check_first_row_level(row_data, row_index, errors)

            # 3. 部件类别枚举验证
            self._validate_component_category(row_data, row_index, errors)

            if row_index == 0:
                # 第一行层次修正，不算错误，但需要记录信息
                if errors:
                    errors = [e for e in errors if e.get('type') != 'required_field_correction']

        except Exception as e:
            logger.error(f"验证第{row_index + 1}行时发生异常: {str(e)}")
            errors.append({
                'type': 'validation_exception',
                'message': str(e),
                'row': row_index + 1,
                'field': None,
                'warning': False
            })

        is_valid = len(errors) == 0
        return is_valid, errors

    def _check_required_fields(self, row: Dict[str, Any], row_index: int, errors: List[Dict]):
        """检查必填字段"""
        required_fields = {
            '层次': '层次字段必填',
            '部件类别': '部件类别字段必填'
        }

        for field, message in required_fields.items():
            value = row.get(field)
            if self._is_empty(value):
                errors.append({
                    'type': 'required_field_error',
                    'message': message,
                    'row': row_index + 1,
                    'field': field,
                    'warning': False
                })

    def _check_first_row_level(self, row: Dict[str, Any], row_index: int, errors: List[Dict]):
        """检查第一行层次是否应为0"""
        if row_index == 0:
            level = row.get('层次')
            if not self._is_empty(level) and level != 0:
                errors.append({
                    'type': 'first_row_level',
                    'message': f'第一行层次应为0，实际为{level}（将自动修正）',
                    'row': row_index + 1,
                    'field': '层次',
                    'warning': True,  # 警告，可自动修正
                    'old_value': level,
                    'correction': 0
                })

    def _validate_component_category(self, row: Dict[str, Any], row_index: int, errors: List[Dict]):
        """验证部件类别枚举值"""
        category = row.get('部件类别')
        if self._is_empty(category):
            return  # 必填字段检查已处理

        if category not in self.VALID_COMPONENT_CATEGORIES:
            errors.append({
                'type': 'enum_error',
                'message': f'部件类别"{category}"无效，应为: {", ".join(self.VALID_COMPONENT_CATEGORIES)}',
                'row': row_index + 1,
                'field': '部件类别',
                'warning': False
            })

    @staticmethod
    def _is_empty(value: Any) -> bool:
        """判断值是否为空"""
        if value is None:
            return True
        if isinstance(value, str) and value.strip() == '':
            return True
        # 使用pandas.isna处理NaN、NaT等缺失值
        try:
            import pandas as pd
            if pd.isna(value):
                return True
        except (TypeError, ValueError):
            pass
        return False

    def validate_external_component_requirements(self, row: Dict[str, Any], row_index: int) -> Tuple[bool, Optional[str]]:
        """
        验证外部第三方组件的特殊要求

        Args:
            row: 行数据
            row_index: 行索引

        Returns:
            (是否有效, 错误消息)
        """
        component_category = row.get('部件类别')
        if component_category != '外部第三方组件':
            return True, None

        part_number = row.get('部件编号')
        if self._is_empty(part_number):
            return False, '外部第三方组件必须填写部件编号'

        return True, None

    def validate_normal_component_requirements(self, row: Dict[str, Any], row_index: int) -> Tuple[bool, Optional[str]]:
        """
        验证常规组件的必填字段要求

        Args:
            row: 行数据
            row_index: 行索引

        Returns:
            (是否有效, 错误消息)
        """
        component_category = row.get('部件类别')
        if component_category == '外部第三方组件':
            return True, None

        part_number = row.get('部件编号')
        # 部件编号为空时，需要中文名称、英文名称、英文代号都填写
        if self._is_empty(part_number):
            missing_fields = []
            for field in ['中文名称', '英文名称', '英文代号']:
                if self._is_empty(row.get(field)):
                    missing_fields.append(field)

            if missing_fields:
                return False, f'部件编号为空时，以下字段必填: {", ".join(missing_fields)}'

        return True, None

# 便捷函数
def validate_row_fields(row_data: Dict[str, Any], row_index: int) -> Tuple[bool, List[Dict]]:
    """
    便捷函数：验证单行数据

    Args:
        row_data: 行数据
        row_index: 行索引

    Returns:
        (是否通过, 错误列表)
    """
    validator = FieldValidator()
    return validator.validate_row(row_data, row_index)