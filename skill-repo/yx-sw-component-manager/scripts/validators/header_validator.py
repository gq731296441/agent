#!/usr/bin/env python3
"""
表头验证器
检查Excel文件是否包含所有必需的列
"""

import logging
from typing import List, Tuple, Dict, Any

logger = logging.getLogger(__name__)

class HeaderValidator:
    """表头验证器"""

    # 必需的13个列
    REQUIRED_COLUMNS = [
        '层次', '部件类别', '中文名称', '英文名称', '英文代号',
        '组件版本', '组件资产类型', '服务制品类型', '服务等级',
        '部件编号', '组件功能说明', '组件接口说明', '备注'
    ]

    def __init__(self, required_columns: List[str] = None):
        """
        初始化表头验证器

        Args:
            required_columns: 必需的列名列表，默认使用标准13列
        """
        self.required_columns = required_columns or self.REQUIRED_COLUMNS

    def validate(self, columns: List[str]) -> Tuple[bool, List[str], Dict[str, Any]]:
        """
        验证表头

        Args:
            columns: 实际的列名列表

        Returns:
            (是否通过, 缺失的列列表, 详细信息字典)
        """
        missing_columns = [col for col in self.required_columns if col not in columns]
        extra_columns = [col for col in columns if col not in self.required_columns]

        is_valid = len(missing_columns) == 0

        details = {
            'total_required': len(self.required_columns),
            'total_actual': len(columns),
            'missing_count': len(missing_columns),
            'extra_count': len(extra_columns),
            'missing_columns': missing_columns,
            'extra_columns': extra_columns,
            'match_count': len([col for col in self.required_columns if col in columns])
        }

        if is_valid:
            logger.info(f"表头验证通过: 找到全部 {len(self.required_columns)} 个必需列")
        else:
            logger.error(f"表头验证失败: 缺少 {len(missing_columns)} 个必需列: {missing_columns}")

        return is_valid, missing_columns, details

    def get_matching_score(self, columns: List[str]) -> float:
        """
        计算工作表匹配分数

        Args:
            columns: 工作表列名列表

        Returns:
            匹配分数 (0-100)
        """
        matches = sum(1 for col in columns if col in self.required_columns)
        total = len(self.required_columns)

        if matches == total:
            return 100.0  # 完全匹配
        elif matches >= 9:
            # 9-12列: 50-99分
            return 50.0 + (matches - 9) * 16.67
        else:
            # 低匹配: 按列数计分
            return matches * 5.0

# 便捷函数
def validate_headers(columns: List[str], required_columns: List[str] = None) -> Tuple[bool, List[str]]:
    """
    便捷函数：验证表头

    Args:
        columns: 实际的列名列表
        required_columns: 必需的列名列表

    Returns:
        (是否通过, 缺失的列列表)
    """
    validator = HeaderValidator(required_columns)
    is_valid, missing, _ = validator.validate(columns)
    return is_valid, missing