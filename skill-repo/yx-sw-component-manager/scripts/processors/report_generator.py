#!/usr/bin/env python3
"""
报告生成器
生成验证报告、汇总摘要等
"""

import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

class ReportGenerator:
    """报告生成器"""

    def __init__(self):
        """初始化报告生成器"""
        pass

    def generate_validation_report(self, statistics: Dict[str, Any],
                                  errors: List[Dict[str, Any]],
                                  correction_suggestions: List[Dict[str, Any]] = None,
                                  original_df=None,
                                  corrected_df=None,
                                  **extra) -> Dict[str, Any]:
        """
        生成验证报告

        Args:
            statistics: 统计数据
            errors: 错误列表
            correction_suggestions: 修正建议列表
            original_df: 原始DataFrame（用于比较）
            corrected_df: 修正后DataFrame
            **extra: 额外信息

        Returns:
            完整的报告字典
        """
        if correction_suggestions is None:
            correction_suggestions = []

        report = {
            'generated_at': datetime.now().isoformat(),
            'statistics': statistics,
            'errors': errors,
            'correction_suggestions': correction_suggestions,
            'summary': self._generate_summary_text(statistics, errors, correction_suggestions)
        }

        # 添加额外信息
        report.update(extra)

        return report

    def _generate_summary_text(self, statistics: Dict[str, Any],
                              errors: List[Dict[str, Any]],
                              correction_suggestions: List[Dict[str, Any]]) -> str:
        """生成汇总文本（包含修正建议列表）"""
        lines = [
            "=" * 50,
            f"组件清单验证报告",
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 50,
            f"总行数: {statistics.get('total_rows', 0)}",
            f"验证通过: {statistics.get('passed', 0)}",
            f"验证失败: {statistics.get('failed', 0)}",
            f"修正建议: {statistics.get('correction_suggestions', 0)}",
            f"API调用成功: {statistics.get('api_success', 0)}",
            f"API调用失败: {statistics.get('api_failed', 0)}",
            "=" * 50
        ]

        if errors:
            # 生成简化错误列表
            formatted_errors = self._format_simple_error_list(errors)

            if formatted_errors:
                lines.append("错误列表:")
                lines.extend([f"  {err}" for err in formatted_errors])
                lines.append("=" * 50)

        # 错误类型统计
        if errors:
            lines.append("\n错误类型统计:")
            error_counts = {}
            for error in errors:
                error_type = error.get('type') or error.get('rule', 'unknown')
                error_counts[error_type] = error_counts.get(error_type, 0) + 1

            for error_type, count in sorted(error_counts.items()):
                lines.append(f"  {error_type}: {count} 次")

        if correction_suggestions:
            lines.append(f"\n修正建议数: {len(correction_suggestions)}")

            # 按字段分组修正建议
            field_counts = {}
            for corr in correction_suggestions:
                field = corr.get('field', 'unknown')
                field_counts[field] = field_counts.get(field, 0) + 1

            lines.append("修正建议字段分布:")
            for field, count in sorted(field_counts.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"  {field}: {count} 处")

        lines.append("=" * 50)
        return "\n".join(lines)

    def _compare_dataframes(self, original_df, corrected_df) -> Dict[str, Any]:
        """比较两个DataFrame"""
        try:
            # 计算有多少行被修改
            diff_mask = (original_df != corrected_df).any(axis=1)
            corrected_rows = int(diff_mask.sum())

            # 计算修改的单元格数量
            total_cells_changed = int((original_df != corrected_df).sum().sum())

            return {
                'corrected_rows': corrected_rows,
                'total_rows': len(original_df),
                'total_cells_changed': total_cells_changed,
                'rows_unchanged': len(original_df) - corrected_rows
            }
        except Exception as e:
            logger.warning(f"比较DataFrame时出错: {str(e)}")
            return {}

    def save_report_to_json(self, report: Dict[str, Any], filepath: str) -> bool:
        """
        保存报告为JSON文件

        Args:
            report: 报告字典
            filepath: 文件路径

        Returns:
            是否成功
        """
        try:
            # 确保报告是可序列化的
            serializable_report = self._make_serializable(report)

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(serializable_report, f, ensure_ascii=False, indent=2)

            logger.info(f"JSON报告已保存: {filepath}")
            return True

        except Exception as e:
            logger.error(f"保存JSON报告失败: {str(e)}")
            return False

    def save_summary_to_text(self, summary_text: str, filepath: str) -> bool:
        """
        保存汇总摘要为文本文件

        Args:
            summary_text: 汇总文本
            filepath: 文件路径

        Returns:
            是否成功
        """
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(summary_text)

            logger.info(f"汇总摘要已保存: {filepath}")
            return True

        except Exception as e:
            logger.error(f"保存汇总摘要失败: {str(e)}")
            return False

    def generate_html_report(self, report: Dict[str, Any], template_path: str = None) -> Optional[str]:
        """
        生成HTML报告（现代化美观样式）

        Args:
            report: 报告字典
            template_path: HTML模板路径

        Returns:
            HTML字符串或None
        """
        if template_path:
            # TODO: 实现模板渲染
            pass

        stats = report.get('statistics', {})
        errors = report.get('errors', [])
        correction_suggestions = report.get('correction_suggestions', [])

        # 计算通过率
        total = stats.get('total_rows', 0)
        passed = stats.get('passed', 0)
        pass_rate = (passed / total * 100) if total > 0 else 0

        # 状态徽章颜色
        if stats.get('failed', 0) == 0:
            status_badge = '<span class="badge success">全部通过</span>'
        elif stats.get('passed', 0) == 0:
            status_badge = '<span class="badge danger">全部失败</span>'
        else:
            status_badge = '<span class="badge warning">部分通过</span>'

        # 格式化生成时间
        generated_at = report.get('generated_at', '')
        if 'T' in generated_at:
            generated_at = generated_at.replace('T', ' ').split('.')[0]

        html = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>软件组件清单验证报告</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 30px;
        }}

        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}

        .header {{
            text-align: center;
            margin-bottom: 30px;
        }}

        .header h1 {{
            color: #fff;
            font-size: 2.5rem;
            font-weight: 600;
            text-shadow: 0 2px 4px rgba(0,0,0,0.1);
            margin-bottom: 10px;
        }}

        .header .subtitle {{
            color: rgba(255,255,255,0.9);
            font-size: 1.1rem;
        }}

        .card {{
            background: #fff;
            border-radius: 16px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.15);
            padding: 30px;
            margin-bottom: 24px;
            overflow: hidden;
        }}

        .card-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 24px;
            padding-bottom: 16px;
            border-bottom: 2px solid #f0f0f0;
        }}

        .card-title {{
            font-size: 1.4rem;
            font-weight: 600;
            color: #333;
            display: flex;
            align-items: center;
            gap: 10px;
        }}

        .card-title::before {{
            content: '';
            display: inline-block;
            width: 4px;
            height: 24px;
            background: linear-gradient(180deg, #667eea, #764ba2);
            border-radius: 2px;
        }}

        .badge {{
            padding: 6px 16px;
            border-radius: 20px;
            font-size: 0.85rem;
            font-weight: 500;
        }}

        .badge.success {{
            background: #d4edda;
            color: #155724;
        }}

        .badge.warning {{
            background: #fff3cd;
            color: #856404;
        }}

        .badge.danger {{
            background: #f8d7da;
            color: #721c24;
        }}

        .badge.info {{
            background: #e3f2fd;
            color: #1565c0;
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}

        .stat-card {{
            background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
            border-radius: 12px;
            padding: 24px;
            text-align: center;
            transition: transform 0.3s ease;
        }}

        .stat-card:hover {{
            transform: translateY(-5px);
        }}

        .stat-card.passed {{
            background: linear-gradient(135deg, #d4edda 0%, #c3e6cb 100%);
        }}

        .stat-card.failed {{
            background: linear-gradient(135deg, #f8d7da 0%, #f5c6cb 100%);
        }}

        .stat-card.corrected {{
            background: linear-gradient(135deg, #fff3e0 0%, #ffe0b2 100%);
        }}

        .stat-number {{
            font-size: 3rem;
            font-weight: 700;
            color: #333;
            line-height: 1;
            margin-bottom: 8px;
        }}

        .stat-label {{
            font-size: 0.95rem;
            color: #666;
            font-weight: 500;
        }}

        .pass-rate {{
            text-align: center;
            margin: 30px 0;
        }}

        .pass-rate-circle {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 160px;
            height: 160px;
            border-radius: 50%;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            box-shadow: 0 8px 30px rgba(102, 126, 234, 0.4);
        }}

        .pass-rate-inner {{
            text-align: center;
            color: #fff;
        }}

        .pass-rate-value {{
            font-size: 2.5rem;
            font-weight: 700;
            line-height: 1;
        }}

        .pass-rate-text {{
            font-size: 0.9rem;
            opacity: 0.9;
        }}

        .info-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 16px;
        }}

        .info-item {{
            display: flex;
            justify-content: space-between;
            padding: 12px 16px;
            background: #f8f9fa;
            border-radius: 8px;
        }}

        .info-label {{
            color: #666;
            font-weight: 500;
        }}

        .info-value {{
            color: #333;
            font-weight: 600;
        }}

        .section {{
            margin-top: 30px;
        }}

        .section-title {{
            font-size: 1.2rem;
            font-weight: 600;
            color: #333;
            margin-bottom: 16px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .error-list, .correction-list {{
            max-height: 400px;
            overflow-y: auto;
        }}

        .error-list ul, .correction-list ul {{
            list-style: none;
        }}

        .error-item, .correction-item {{
            padding: 14px 18px;
            margin-bottom: 10px;
            border-radius: 10px;
            font-size: 0.95rem;
            line-height: 1.5;
        }}

        .error-item {{
            background: #fff5f5;
            border-left: 4px solid #e74c3c;
            color: #c0392b;
        }}

        .correction-item {{
            background: linear-gradient(135deg, #fff8e1 0%, #ffecb3 100%);
            border-left: 4px solid #ff9800;
            color: #e65100;
        }}

        .correction-item .correction-content {{
            display: flex;
            align-items: center;
            margin: 8px 0;
        }}

        .correction-item .current-label {{
            font-weight: 500;
            margin-right: 8px;
            color: #666;
        }}

        .correction-item .current-value {{
            text-decoration: line-through;
            color: #999;
            background: #f5f5f5;
            padding: 2px 8px;
            border-radius: 4px;
        }}

        .correction-item .correction-arrow {{
            text-align: center;
            color: #ff9800;
            font-weight: 600;
            font-size: 0.85rem;
            margin: 8px 0;
        }}

        .correction-item .suggested-value {{
            color: #27ae60;
            font-weight: 600;
            font-size: 1.1rem;
            background: #e8f5e9;
            padding: 8px 16px;
            border-radius: 6px;
            text-align: center;
        }}

        .correction-item .correction-reason {{
            margin-top: 10px;
            font-size: 0.85rem;
            color: #666;
            font-style: italic;
        }}

        .row-info {{
            font-weight: 600;
            color: #333;
            margin-right: 8px;
        }}

        .empty-state {{
            text-align: center;
            padding: 40px;
            color: #999;
            font-size: 1.1rem;
        }}

        .empty-state .icon {{
            font-size: 3rem;
            margin-bottom: 16px;
            opacity: 0.5;
        }}

        .scroll-hint {{
            text-align: center;
            color: #999;
            font-size: 0.85rem;
            margin-top: 10px;
        }}

        .footer {{
            text-align: center;
            margin-top: 30px;
            color: rgba(255,255,255,0.8);
            font-size: 0.9rem;
        }}

        @media (max-width: 768px) {{
            body {{
                padding: 15px;
            }}

            .header h1 {{
                font-size: 1.8rem;
            }}

            .stat-number {{
                font-size: 2rem;
            }}

            .pass-rate-circle {{
                width: 120px;
                height: 120px;
            }}

            .pass-rate-value {{
                font-size: 2rem;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📋 软件组件清单验证报告</h1>
            <p class="subtitle">生成时间: {generated_at}</p>
        </div>

        <div class="card">
            <div class="card-header">
                <span class="card-title">总体概览</span>
                {status_badge}
            </div>

            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-number">{stats.get('total_rows', 0)}</div>
                    <div class="stat-label">总行数</div>
                </div>
                <div class="stat-card passed">
                    <div class="stat-number">{stats.get('passed', 0)}</div>
                    <div class="stat-label">验证通过</div>
                </div>
                <div class="stat-card failed">
                    <div class="stat-number">{stats.get('failed', 0)}</div>
                    <div class="stat-label">验证失败</div>
                </div>
                <div class="stat-card corrected">
                    <div class="stat-number">{stats.get('correction_suggestions', 0)}</div>
                    <div class="stat-label">修正建议</div>
                </div>
            </div>

            <div class="pass-rate">
                <div class="pass-rate-circle">
                    <div class="pass-rate-inner">
                        <div class="pass-rate-value">{pass_rate:.1f}%</div>
                        <div class="pass-rate-text">通过率</div>
                    </div>
                </div>
            </div>

            <div class="info-grid">
                <div class="info-item">
                    <span class="info-label">API调用成功</span>
                    <span class="info-value">{stats.get('api_success', 0)}</span>
                </div>
                <div class="info-item">
                    <span class="info-label">API调用失败</span>
                    <span class="info-value">{stats.get('api_failed', 0)}</span>
                </div>
            </div>
        </div>

        <div class="card">
            <div class="card-header">
                <span class="card-title">错误详情</span>
                <span class="badge {'danger' if len(errors) > 0 else 'success'}">{len(errors)} 个错误</span>
            </div>
            <div class="error-list">
                {self._format_errors_as_html(errors)}
            </div>
            <div class="scroll-hint">向上滚动查看更多</div>
        </div>

        <div class="card">
            <div class="card-header">
                <span class="card-title">📝 修正建议</span>
                <span class="badge {'info' if len(correction_suggestions) > 0 else 'success'}">{len(correction_suggestions)} 条建议</span>
            </div>
            <div class="correction-list">
                {self._format_correction_suggestions_as_html(correction_suggestions)}
            </div>
            <div class="scroll-hint">向上滚动查看更多</div>
        </div>

        <div class="footer">
            <p>由 wx-sw-component-list-check 技能自动生成 · 仅供参考，请用户手动修订</p>
        </div>
    </div>
</body>
</html>
        """

        return html

    def _format_simple_error_list(self, errors: List[Dict[str, Any]]) -> List[str]:
        """
        生成用户要求的简单错误格式列表 - 直接显示原始错误信息
        """
        formatted_errors = []

        for error in errors:
            row = error.get('row', '?')
            part_number = error.get('part_number', '')
            message = error.get('message', '')

            # 直接使用原始错误信息，保持与JSON报告一致
            if message:
                if part_number:
                    formatted_errors.append(f"第{row}行，部件编号：{part_number}，{message}")
                else:
                    formatted_errors.append(f"第{row}行，{message}")
            else:
                # 没有message时，尝试构建基本错误信息
                rule = error.get('rule', 'unknown')
                formatted_errors.append(f"第{row}行，规则：{rule}，验证失败")

        return formatted_errors

    def _format_errors_as_html(self, errors: List[Dict[str, Any]]) -> str:
        """格式化错误为HTML列表（使用简化的用户友好格式）"""
        if not errors:
            return '''
            <div class="empty-state">
                <div class="icon">✅</div>
                <p>恭喜！没有发现错误</p>
            </div>
            '''

        formatted_errors = self._format_simple_error_list(errors)

        if not formatted_errors:
            return '''
            <div class="empty-state">
                <div class="icon">✅</div>
                <p>没有符合条件的关键错误</p>
            </div>
            '''

        html = "<ul>"
        for error_text in formatted_errors[:100]:
            html += f'<li class="error-item"><span class="row-info">⚠️</span> {error_text}</li>'
        html += "</ul>"

        if len(formatted_errors) > 100:
            html += f'<div class="scroll-hint">... 还有 {len(formatted_errors) - 100} 个错误未显示</div>'

        return html

    def _format_correction_suggestions_as_html(self, correction_suggestions: List[Dict[str, Any]]) -> str:
        """格式化修正建议为HTML列表"""
        if not correction_suggestions:
            return '''
            <div class="empty-state">
                <div class="icon">✨</div>
                <p>没有需要修正的数据</p>
            </div>
            '''

        html = "<ul>"
        for sugg in correction_suggestions[:50]:
            row = sugg.get('row', '?')
            field = sugg.get('field', '?')
            current_val = sugg.get('current_value', '（空）')
            suggested_val = sugg.get('suggested_value', '')
            reason = sugg.get('reason', '')
            html += f'''<li class="correction-item">
                <span class="row-info">📋 第{row}行 - <strong>{field}</strong></span>
                <div class="correction-content">
                    <span class="current-label">当前值:</span>
                    <span class="current-value">{current_val}</span>
                </div>
                <div class="correction-arrow">↓ 建议修改为 ↓</div>
                <div class="suggested-value">{suggested_val}</div>
                <div class="correction-reason">💡 原因: {reason}</div>
            </li>'''
        html += "</ul>"

        if len(correction_suggestions) > 50:
            html += f'<div class="scroll-hint">... 还有 {len(correction_suggestions) - 50} 条修正建议未显示</div>'

        return html

    def _make_serializable(self, obj):
        """递归将对象转换为JSON可序列化"""
        import numpy as np
        import pandas as pd

        if isinstance(obj, (np.integer,)):
            return int(obj)
        elif isinstance(obj, (np.floating,)):
            return float(obj)
        elif isinstance(obj, (np.ndarray,)):
            return obj.tolist()
        elif isinstance(obj, pd.DataFrame):
            return obj.to_dict(orient='records')
        elif isinstance(obj, pd.Series):
            return obj.to_dict()
        elif isinstance(obj, dict):
            return {k: self._make_serializable(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._make_serializable(v) for v in obj]
        elif hasattr(obj, 'isoformat'):  # datetime
            return obj.isoformat()
        else:
            return obj

# 便捷函数
def create_report_generator() -> ReportGenerator:
    """创建报告生成器实例"""
    return ReportGenerator()