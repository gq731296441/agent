#!/usr/bin/env python3
"""
Excel处理器
负责Excel文件的读取、工作表选择、数据加载
"""

import pandas as pd
import logging
from typing import Optional, List, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)

class ExcelProcessor:
    """Excel处理器"""

    def __init__(self, excel_path: str):
        """
        初始化Excel处理器

        Args:
            excel_path: Excel文件路径
        """
        self.excel_path = excel_path
        self.df = None
        self.sheet_name = None
        self.sheet_names = []
        self.original_df = None

    def load_workbook(self) -> bool:
        """
        加载Excel工作簿，获取工作表列表

        Returns:
            是否成功
        """
        try:
            excel_file = pd.ExcelFile(self.excel_path)
            self.sheet_names = excel_file.sheet_names
            logger.info(f"发现 {len(self.sheet_names)} 个工作表: {self.sheet_names}")
            return True
        except Exception as e:
            logger.error(f"读取Excel文件失败: {str(e)}")
            return False

    def select_best_sheet(self, required_columns: List[str]) -> Optional[str]:
        """
        智能选择工作表

        Args:
            required_columns: 必需列列表

        Returns:
            最佳工作表名称或None
        """
        if not self.sheet_names:
            logger.error("没有可用工作表，请先加载工作簿")
            return None

        best_sheet = None
        best_score = 0
        best_matches = 0

        for sheet_name in self.sheet_names:
            try:
                # 读取表头（前5行）
                df_temp = pd.read_excel(self.excel_path, sheet_name=sheet_name, nrows=5)
                columns = df_temp.columns.tolist()

                # 计算匹配的必需列数量
                matches = sum(1 for col in columns if col in required_columns)
                score = self._calculate_score(matches, len(columns))

                logger.info(f"工作表 '{sheet_name}': {len(columns)}列, 匹配{matches}/{len(required_columns)}个必需列, 分数={score:.1f}")

                if score > best_score:
                    best_score = score
                    best_sheet = sheet_name
                    best_matches = matches

            except Exception as e:
                logger.warning(f"工作表 '{sheet_name}' 读取失败: {str(e)}")
                continue

        if best_sheet:
            logger.info(f"选择工作表: '{best_sheet}' (分数: {best_score:.1f}, 匹配{best_matches}/{len(required_columns)}列)")
        else:
            logger.error("未找到合适的工作表")

        return best_sheet

    def _calculate_score(self, matches: int, total_columns: int) -> float:
        """计算工作表匹配分数"""
        required_count = len(self.required_columns) if hasattr(self, 'required_columns') else 13
        if matches == required_count:
            return 100.0  # 完全匹配
        elif matches >= 9:
            return 50.0 + (matches - 9) * 16.67  # 9-12列: 50-99分
        else:
            return matches * 5.0  # 低匹配

    def load_sheet(self, sheet_name: str) -> bool:
        """
        加载指定工作表

        Args:
            sheet_name: 工作表名称

        Returns:
            是否成功
        """
        try:
            self.df = pd.read_excel(self.excel_path, sheet_name=sheet_name)
            # 转换NaN为None以便于处理
            self.df = self.df.where(pd.notnull(self.df), None)
            self.original_df = self.df.copy()
            self.sheet_name = sheet_name

            logger.info(f"成功加载工作表 '{sheet_name}': {len(self.df)} 行数据, {len(self.df.columns)} 列")
            return True

        except Exception as e:
            logger.error(f"加载工作表 '{sheet_name}' 失败: {str(e)}")
            return False

    def get_column_names(self) -> List[str]:
        """获取当前表的列名列表"""
        if self.df is not None:
            return self.df.columns.tolist()
        return []

    def get_row_data(self, index: int) -> Optional[Dict[str, Any]]:
        """
        获取指定行的数据

        Args:
            index: 行索引（从0开始）

        Returns:
            行数据字典或None
        """
        if self.df is None or index >= len(self.df):
            return None

        return self.df.iloc[index].to_dict()

    def update_row(self, index: int, field: str, value: Any):
        """
        更新指定行的字段值

        Args:
            index: 行索引
            field: 字段名
            value: 新值
        """
        if self.df is not None and field in self.df.columns:
            self.df.at[index, field] = value

    def save_to_file(self, output_path: str) -> bool:
        """
        保存DataFrame到Excel文件

        Args:
            output_path: 输出文件路径

        Returns:
            是否成功
        """
        try:
            self.df.to_excel(output_path, index=False)
            logger.info(f"Excel文件已保存: {output_path}")
            return True
        except Exception as e:
            logger.error(f"保存Excel文件失败: {str(e)}")
            return False

    def get_statistics(self) -> Dict[str, Any]:
        """获取数据统计信息"""
        if self.df is None:
            return {}

        return {
            'total_rows': len(self.df),
            'total_columns': len(self.df.columns),
            'columns': self.get_column_names(),
            'sheet_name': self.sheet_name
        }

# 默认检查的工作表名称：组件清单数据位于"软件结构"sheet，必须优先检查该 sheet，
# 避免智能选 sheet 误选到其他页面（历史教训：AI 曾检查到别的 sheet）
DEFAULT_TARGET_SHEET = "软件结构"


# 便捷函数
def load_excel_file(excel_path: str, required_columns: List[str] = None,
                    preferred_sheet: str = None) -> Optional[ExcelProcessor]:
    """
    便捷函数：加载Excel文件并选择工作表

    Args:
        excel_path: Excel文件路径
        required_columns: 必需列列表
        preferred_sheet: 优先选择的工作表名称（默认"软件结构"）。存在则直接使用，
                         不存在则回退到智能选择并输出警告日志。

    Returns:
        ExcelProcessor实例或None
    """
    processor = ExcelProcessor(excel_path)

    if not processor.load_workbook():
        return None

    preferred_sheet = preferred_sheet or DEFAULT_TARGET_SHEET

    if required_columns:
        processor.required_columns = required_columns
        # 优先使用指定的目标 sheet（如"软件结构"）
        if preferred_sheet in processor.sheet_names:
            sheet = preferred_sheet
            logger.warning(f"使用目标工作表: '{preferred_sheet}'（组件清单数据所在 sheet）")
        else:
            sheet = processor.select_best_sheet(required_columns)
            if sheet:
                logger.warning(
                    f"未找到目标工作表 '{preferred_sheet}'，回退为智能选择: '{sheet}'。"
                    f"请确认检查的是组件清单【软件结构】sheet。")
    else:
        # 如果没有指定必需列，优先使用目标 sheet，否则选择第一个工作表
        if preferred_sheet in processor.sheet_names:
            sheet = preferred_sheet
        elif processor.sheet_names:
            sheet = processor.sheet_names[0]
        else:
            return None

    if sheet and processor.load_sheet(sheet):
        return processor
    return None