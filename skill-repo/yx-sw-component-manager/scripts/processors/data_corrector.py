#!/usr/bin/env python3
"""
数据修正器
根据验证结果自动修正Excel数据
"""

import logging
from typing import Dict, Any, List
from .excel_processor import ExcelProcessor

logger = logging.getLogger(__name__)

class DataCorrector:
    """数据修正器"""

    def __init__(self, excel_processor: ExcelProcessor):
        """
        初始化数据修正器

        Args:
            excel_processor: Excel处理器实例
        """
        self.processor = excel_processor
        self.corrections = []  # 记录所有修正操作

    def correct_first_row_level(self):
        """修正第一行层次为0"""
        if self.processor.df is None or len(self.processor.df) == 0:
            return

        first_level = self.processor.df.at[0, '层次']
        if first_level != 0:
            old_value = first_level
            self.processor.df.at[0, '层次'] = 0
            self._record_correction(0, '层次', old_value, 0)
            logger.info(f"第1行层次从 {old_value} 修正为 0")

    def apply_consistency_corrections(self, row_index: int, corrections: Dict[str, Any]):
        """
        应用一致性检查发现的修正

        Args:
            row_index: 行索引
            corrections: 字段修正字典 {字段名: 新值}
        """
        for field, new_value in corrections.items():
            old_value = self.processor.df.at[row_index, field]
            self.processor.df.at[row_index, field] = new_value
            self._record_correction(row_index, field, old_value, new_value)
            logger.debug(f"第{row_index + 1}行 {field}: '{old_value}' -> '{new_value}'")

    def _record_correction(self, row_index: int, field: str, old_value: Any, new_value: Any):
        """记录修正操作"""
        correction = {
            'row': row_index + 1,
            'field': field,
            'old_value': old_value,
            'new_value': new_value
        }
        self.corrections.append(correction)

    def get_corrections_summary(self) -> Dict[str, Any]:
        """
        获取修正摘要

        Returns:
            修正统计字典
        """
        if not self.corrections:
            return {
                'total_corrections': 0,
                'corrected_rows': 0,
                'fields_corrected': []
            }

        # 统计修正的行数
        corrected_rows = len(set(c['row'] for c in self.corrections))

        # 统计每个字段的修正次数
        field_counts = {}
        for corr in self.corrections:
            field = corr['field']
            field_counts[field] = field_counts.get(field, 0) + 1

        return {
            'total_corrections': len(self.corrections),
            'corrected_rows': corrected_rows,
            'fields_corrected': [
                {'field': field, 'count': count}
                for field, count in sorted(field_counts.items(), key=lambda x: x[1], reverse=True)
            ],
            'details': self.corrections
        }

    def reset_corrections(self):
        """重置修正记录"""
        self.corrections = []

# 便捷函数
def create_data_corrector(processor: ExcelProcessor) -> DataCorrector:
    """创建数据修正器实例"""
    return DataCorrector(processor)