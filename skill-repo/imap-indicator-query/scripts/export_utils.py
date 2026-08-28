#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据导出工具模块（简化版：仅 CSV）
为 IMAP 指标查询提供 CSV 导出功能，避免依赖 pandas
"""
import os
import csv
import json
import datetime
from typing import Dict, List, Any, Optional

def generate_filename(prefix: str, indicator_name: str = None, project_group: str = None) -> str:
    """生成带时间戳的文件名"""
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    if indicator_name and project_group:
        safe_name = indicator_name.replace(' ', '_').replace('/', '_').replace('\\', '_')[:50]
        safe_group = project_group.replace(' ', '_').replace('/', '_').replace('\\', '_')[:30]
        return f"{prefix}_{safe_group}_{safe_name}_{timestamp}"
    else:
        return f"{prefix}_{timestamp}"

def export_table_to_csv(data: Dict[str, Any], filepath: str) -> bool:
    """将表格数据导出为 CSV"""
    if not isinstance(data, dict):
        return False
    
    columns = data.get('column', [])
    rows = data.get('row', [])
    
    if not columns or not rows:
        return False
    
    try:
        with open(f"{filepath}.csv", 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            for row in rows:
                writer.writerow([str(item) if item is not None else '' for item in row])
        return True
    except Exception as e:
        print(f"导出 CSV 失败: {e}")
        return False

def export_indicator_details(data: Dict[str, Any], output_dir: str = None) -> Dict[str, List[str]]:
    """
    导出指标详单数据为 CSV 文件（多表格）
    
    Args:
        data: 包含 '明细数据' 的完整查询结果
        output_dir: 输出目录，默认当前工作目录
    
    Returns:
        {'csv': [文件路径列表]}
    """
    if output_dir is None:
        output_dir = os.getcwd()
    
    indicator_name = data.get('查询指标', '指标')
    project_group = data.get('项目组', '项目')
    
    base_name = generate_filename("IMAP", indicator_name, project_group)
    base_path = os.path.join(output_dir, base_name)
    
    details = data.get('明细数据', {})
    exported = {'csv': []}
    
    for table_name, table_data in details.items():
        if isinstance(table_data, dict) and 'row' in table_data and 'column' in table_data:
            csv_path = f"{base_path}_{table_name.replace(' ', '_')}"
            if export_table_to_csv(table_data, csv_path):
                exported['csv'].append(f"{csv_path}.csv")
    
    return exported

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        with open(sys.argv[1], 'r', encoding='utf-8') as f:
            data = json.load(f)
        result = export_indicator_details(data)
        print(json.dumps(result, ensure_ascii=False, indent=2))
