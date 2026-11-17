#!/usr/bin/env python3
"""
统一审核报告生成器
融合 wx-sw-component-list-check 与 component-manifest-verify-addDir 的校验结果，
输出一份综合审核报告（多sheet Excel + HTML + JSON）。

报告结构（Excel sheets）：
  - 校验汇总    : 各类校验的通过率统计
  - 逐行校验明细 : wx 规则引擎的逐行校验结果（含修正建议）
  - 文档格式检查 : addDir 的格式检查结果
  - PDM编号校验  : addDir 的 pdm_component 表编号校验
  - 代码库拆分校验: addDir 的代码库拆分 + 路径校验
  - 组件的测试-UT-FT代码目录校验: addDir 的附加代码目录校验
"""

import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


def _to_records(df) -> List[Dict[str, Any]]:
    """将 DataFrame 转为 JSON 可序列化的记录列表"""
    import pandas as pd
    if df is None or df.empty:
        return []
    return df.where(pd.notnull(df), '').to_dict(orient='records')


class UnifiedReportGenerator:
    """统一审核报告生成器"""

    def __init__(self, excel_path: str = None, output_dir: str = None):
        self.excel_path = excel_path
        self.output_dir = output_dir

    # ==================== 汇总统计 ====================
    def _build_summary(self, wx_stats: Dict[str, Any],
                       format_df,
                       pdm_df,
                       code_df,
                       additional_df) -> Dict[str, Any]:
        """构建各类校验的汇总统计"""
        summary = {}

        # 逐行规则校验（wx）
        summary['逐行规则校验'] = {
            '总行数': wx_stats.get('total_rows', 0),
            '通过': wx_stats.get('passed', 0),
            '失败': wx_stats.get('failed', 0),
            '修正建议数': wx_stats.get('correction_suggestions', 0),
        }

        # 文档格式检查（addDir）
        if format_df is not None and not format_df.empty:
            f_row = format_df.iloc[0]
            summary['文档格式检查'] = {
                '结果': f_row.get('格式检查结果', ''),
                '详情': f_row.get('检查详情', ''),
            }

        # 各 DataFrame 的通过率统计
        for key, df, pass_col in [
            ('PDM编号校验', pdm_df, 'PDM校验结果'),
            ('代码库拆分校验', code_df, '代码库拆分校验结果'),
            ('组件的测试-UT-FT代码目录校验', additional_df, '校验结果'),
        ]:
            if df is not None and not df.empty and pass_col in df.columns:
                total = len(df)
                passed = int((df[pass_col] == '通过').sum())
                partial = int((df[pass_col] == '部分不通过').sum())
                failed = int((df[pass_col] == '不通过').sum())
                rate = f"{passed / total * 100:.1f}%" if total else "0%"
                summary[key] = {
                    '总记录数': total,
                    '通过': passed,
                    '部分通过': partial,
                    '不通过': failed,
                    '通过率': rate,
                }

        return summary

    # ==================== 综合报告字典 ====================
    def _collect_adddir_issues(self, format_df, pdm_df, code_df, additional_df):
        """
        将 addDir 各校验中的"不通过/部分不通过"项收集为统一的错误条目
        （与 wx 的 errors 结构对齐：row/type/message），使 JSON/HTML 覆盖 addDir 发现的问题。
        返回: (errors, correction_suggestions)
        """
        errors = []
        corrections = []

        # 文档格式检查：结果为不通过时生成一条错误
        if format_df is not None and not format_df.empty:
            f_row = format_df.iloc[0]
            if f_row.get('格式检查结果') == '不通过':
                detail = str(f_row.get('检查详情', ''))
                errors.append({
                    'row': 1, 'type': 'format_check',
                    'message': f"文档格式检查不通过: {detail}",
                    'source': 'component-manifest-verify-addDir',
                })
                corrections.append({
                    'row': 1, 'field': '模板/格式',
                    'current_value': '不通过',
                    'suggested_value': '使用最新模板并补齐必填项',
                    'reason': detail,
                    'source': 'component-manifest-verify-addDir',
                })

        # PDM编号校验
        if pdm_df is not None and not pdm_df.empty and 'PDM校验结果' in pdm_df.columns:
            for r in _to_records(pdm_df):
                result = r.get('PDM校验结果')
                if result in ('不通过', '部分不通过'):
                    row_no = r.get('行号', '?')
                    msg = str(r.get('校验说明', ''))
                    errors.append({
                        'row': row_no, 'type': 'pdm_number_check',
                        'message': f"PDM编号校验{result}: {msg}",
                        'source': 'component-manifest-verify-addDir',
                    })
                    corrections.append({
                        'row': row_no, 'field': '部件编号',
                        'current_value': str(r.get('部件编号', '')),
                        'suggested_value': '请与PDM确认正确编号',
                        'reason': msg,
                        'source': 'component-manifest-verify-addDir',
                    })

        # 代码库拆分校验
        if code_df is not None and not code_df.empty and '代码库拆分校验结果' in code_df.columns:
            for r in _to_records(code_df):
                result = r.get('代码库拆分校验结果')
                if result in ('不通过', '部分不通过'):
                    row_no = r.get('行号', '?')
                    msg = str(r.get('代码库拆分校验说明', ''))
                    errors.append({
                        'row': row_no, 'type': 'code_repo_check',
                        'message': f"代码库拆分校验{result}: {msg}",
                        'source': 'component-manifest-verify-addDir',
                    })
                    corrections.append({
                        'row': row_no, 'field': '代码库/目录',
                        'current_value': str(r.get('原始代码库/目录', '')),
                        'suggested_value': '请使用存在于view_gerrit_repo_list中的代码库',
                        'reason': msg,
                        'source': 'component-manifest-verify-addDir',
                    })
                # 代码路径校验结果
                path_result = r.get('代码路径校验结果')
                if path_result in ('不通过', '部分不通过'):
                    row_no = r.get('行号', '?')
                    msg = str(r.get('代码路径校验说明', ''))
                    errors.append({
                        'row': row_no, 'type': 'code_path_check',
                        'message': f"代码路径校验{path_result}: {msg}",
                        'source': 'component-manifest-verify-addDir',
                    })
                    corrections.append({
                        'row': row_no, 'field': '代码路径',
                        'current_value': str(r.get('代码路径', '')),
                        'suggested_value': '请填写真实存在于代码库中的路径',
                        'reason': msg,
                        'source': 'component-manifest-verify-addDir',
                    })

        # 组件的测试-UT-FT代码目录校验
        if additional_df is not None and not additional_df.empty and '校验结果' in additional_df.columns:
            for r in _to_records(additional_df):
                result = r.get('校验结果')
                if result in ('不通过', '部分不通过'):
                    row_no = r.get('行号', '?')
                    check_type = str(r.get('校验类型', ''))
                    msg = str(r.get('校验说明', ''))
                    errors.append({
                        'row': row_no, 'type': 'additional_code_check',
                        'message': f"{check_type}{result}: {msg}",
                        'source': 'component-manifest-verify-addDir',
                    })
                    corrections.append({
                        'row': row_no, 'field': check_type,
                        'current_value': str(r.get('原始路径', '')),
                        'suggested_value': '请填写对应代码库路径',
                        'reason': msg,
                        'source': 'component-manifest-verify-addDir',
                    })
                # 附加代码路径校验
                path_result = r.get('代码路径校验结果')
                if path_result in ('不通过', '部分不通过'):
                    row_no = r.get('行号', '?')
                    check_type = str(r.get('校验类型', ''))
                    msg = str(r.get('代码路径校验说明', ''))
                    errors.append({
                        'row': row_no, 'type': 'additional_code_path_check',
                        'message': f"{check_type}路径校验{path_result}: {msg}",
                        'source': 'component-manifest-verify-addDir',
                    })
                    corrections.append({
                        'row': row_no, 'field': f'{check_type}路径',
                        'current_value': str(r.get('代码路径', '')),
                        'suggested_value': '请填写真实存在于代码库中的路径',
                        'reason': msg,
                        'source': 'component-manifest-verify-addDir',
                    })

        return errors, corrections

    def build_report(self, wx_stats: Dict[str, Any],
                     wx_errors: List[Dict[str, Any]],
                     wx_corrections: List[Dict[str, Any]],
                     format_df=None, pdm_df=None, code_df=None, additional_df=None) -> Dict[str, Any]:
        """构建统一报告字典"""
        summary = self._build_summary(wx_stats, format_df, pdm_df, code_df, additional_df)

        # 收集 addDir 的问题，并入 wx 的错误/修正建议，使三者（Excel/JSON/HTML）内容一致
        adddir_errors, adddir_corrections = self._collect_adddir_issues(
            format_df, pdm_df, code_df, additional_df)
        all_errors = list(wx_errors) + adddir_errors
        all_corrections = list(wx_corrections) + adddir_corrections

        # 计算综合结论
        overall_failed = False
        overall_notes = []
        if wx_stats.get('failed', 0) > 0:
            overall_failed = True
            overall_notes.append(f"逐行规则校验有 {wx_stats.get('failed', 0)} 行失败")
        for key, item in summary.items():
            if key in ('PDM编号校验', '代码库拆分校验', '组件的测试-UT-FT代码目录校验'):
                if item.get('不通过', 0) > 0:
                    overall_failed = True
                    overall_notes.append(f"{key}有 {item['不通过']} 条不通过")
            elif key == '文档格式检查':
                if item.get('结果') == '不通过':
                    overall_failed = True
                    overall_notes.append(f"文档格式检查不通过: {item.get('详情', '')}")

        report = {
            'generated_at': datetime.now().isoformat(),
            'source_excel': self.excel_path,
            'overall_status': '不通过' if overall_failed else '通过',
            'overall_notes': overall_notes if overall_notes else ['验证通过，无需修订'],
            'summary': summary,
            'errors': all_errors,
            'correction_suggestions': all_corrections,
            'sections': {
                'format_check': _to_records(format_df),
                'pdm_number_check': _to_records(pdm_df),
                'code_repo_check': _to_records(code_df),
                'additional_code_check': _to_records(additional_df),
            },
        }
        return report

    # ==================== Excel 报告（多sheet） ====================
    def save_excel_report(self, report: Dict[str, Any], wx_stats: Dict[str, Any],
                          format_df=None, pdm_df=None, code_df=None, additional_df=None,
                          error_detail_rows: List[Dict[str, Any]] = None,
                          output_path: str = None) -> Optional[str]:
        """
        保存综合 Excel 审核报告（多 sheet）

        Sheet 顺序（wx 的 sheet 放在第一个，addDir 的 sheet 内容不动、顺序后移）：
          1. 逐行校验明细（wx 规则引擎，含错误信息详情）
          2. 文档格式检查（addDir）
          3. PDM编号校验（addDir）
          4. 代码库拆分校验（addDir）
          5. 组件的测试-UT-FT代码目录校验（addDir）
          6. 校验汇总（addDir 原生汇总）

        Args:
            report: 综合报告字典
            wx_stats: wx 逐行校验统计
            format_df/pdm_df/code_df/additional_df: addDir 各校验 DataFrame
            error_detail_rows: 逐行错误详情记录（含错误信息详情列）
            output_path: 输出路径，默认 {原文件}_审核报告_时间戳.xlsx
        """
        try:
            import pandas as pd
            output_path = output_path or self._default_excel_path()
            if output_path is None:
                logger.error("无法确定输出路径")
                return None

            # 第一步：用 addDir 原生 save_results_to_excel 生成基础 Excel（含 addDir 的 5 个 sheet）
            from scripts.validate.db_and_path_check import save_results_to_excel
            base_ok = save_results_to_excel(
                pdm_df if pdm_df is not None else pd.DataFrame(),
                code_df if code_df is not None else pd.DataFrame(),
                additional_df if additional_df is not None else pd.DataFrame(),
                format_df if format_df is not None else pd.DataFrame(),
                output_path)

            if not base_ok:
                logger.error("addDir 基础Excel生成失败")
                return None

            # 第二步：在第一个位置插入 wx 的逐行校验明细 sheet
            from openpyxl import load_workbook
            from openpyxl.utils import get_column_letter

            if error_detail_rows is None and report.get('errors'):
                # 降级：使用错误列表生成明细
                error_detail_rows = pd.DataFrame(report['errors']).to_dict(orient='records')

            wb = load_workbook(output_path)

            # 将 wx 明细 DataFrame 转为新 sheet 并移动到第一个位置
            detail_df = pd.DataFrame(error_detail_rows) if error_detail_rows else pd.DataFrame()
            if not detail_df.empty:
                from openpyxl.utils.dataframe import dataframe_to_rows
                ws = wb.create_sheet(title='逐行校验明细', index=0)
                for r_idx, row in enumerate(dataframe_to_rows(detail_df, index=False, header=True), 1):
                    for c_idx, value in enumerate(row, 1):
                        ws.cell(row=r_idx, column=c_idx, value=value)

            wb.save(output_path)
            wb.close()

            logger.info(f"综合Excel审核报告已保存: {output_path} (sheet: {', '.join(self._list_sheets(output_path))})")
            return output_path
        except Exception as e:
            logger.error(f"保存综合Excel审核报告失败: {e}", exc_info=True)
            return None

    @staticmethod
    def _list_sheets(xlsx_path: str) -> List[str]:
        """列出 Excel 文件的 sheet 名"""
        from openpyxl import load_workbook
        try:
            wb = load_workbook(xlsx_path, read_only=True)
            names = wb.sheetnames
            wb.close()
            return names
        except Exception:
            return []

    # ==================== HTML 报告 ====================
    def save_html_report(self, report: Dict[str, Any], output_path: str = None) -> Optional[str]:
        """保存 HTML 可视化审核报告"""
        try:
            output_path = output_path or self._default_html_path()
            if output_path is None:
                logger.error("无法确定HTML输出路径")
                return None

            html = self._render_html(report)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(html)
            logger.info(f"HTML审核报告已保存: {output_path}")
            return output_path
        except Exception as e:
            logger.error(f"保存HTML审核报告失败: {e}", exc_info=True)
            return None

    def _render_html(self, report: Dict[str, Any]) -> str:
        """渲染 HTML 报告（现代化美观样式）"""
        import html as _html

        generated_at = report.get('generated_at', '').replace('T', ' ').split('.')[0]
        status = report.get('overall_status', '通过')
        badge_cls = 'success' if status == '通过' else 'danger'
        badge_text = '✅ 通过' if status == '通过' else '❌ 不通过'
        notes = report.get('overall_notes', [])
        notes_html = ''.join(f'<li>⚠️ {_html.escape(str(n))}</li>' for n in notes)

        # ===== 汇总统计（各类校验的通过率统计卡片） =====
        summary = report.get('summary', {})

        # 通过率：以逐行规则校验的通过率作为主指标
        row_stat = summary.get('逐行规则校验', {})
        total_rows = row_stat.get('总行数', 0) or 0
        passed_rows = row_stat.get('通过', 0) or 0
        pass_rate = (passed_rows / total_rows * 100) if total_rows else 0

        # 统计卡片（总行数 / 通过 / 失败 / 修正建议）
        failed_rows = row_stat.get('失败', 0) or 0
        corr_count = len(report.get('correction_suggestions', []))

        stat_cards = f"""
        <div class="stat-card"><div class="stat-num">{total_rows}</div><div class="stat-label">总数据行</div></div>
        <div class="stat-card passed"><div class="stat-num">{passed_rows}</div><div class="stat-label">验证通过</div></div>
        <div class="stat-card failed"><div class="stat-num">{failed_rows}</div><div class="stat-label">验证失败</div></div>
        <div class="stat-card corr"><div class="stat-num">{corr_count}</div><div class="stat-label">修正建议</div></div>
        """

        # 通过率圆环（conic-gradient）
        rate_deg = pass_rate * 3.6
        rate_color = '#27ae60' if pass_rate >= 90 else ('#f39c12' if pass_rate >= 60 else '#e74c3c')

        # ===== 各类校验汇总表 =====
        def _summary_cell(key, item):
            """将各校验类型的统计项转为规整表格行"""
            # 定义统一的列
            if key == '逐行规则校验':
                return f"<td>{item.get('总行数', 0)}</td><td>{item.get('通过', 0)}</td><td>{item.get('失败', 0)}</td><td>-</td><td>-</td>"
            if key == '文档格式检查':
                r = '通过' if item.get('结果') == '通过' else '不通过'
                return f"<td>-</td><td>{r}</td><td>-</td><td>-</td><td>-</td>"
            # PDM编号/代码库/测试目录校验：总记录/通过/不通过/部分/通过率
            return (f"<td>{item.get('总记录数', 0)}</td>"
                    f"<td>{item.get('通过', 0)}</td>"
                    f"<td>{item.get('不通过', 0)}</td>"
                    f"<td>{item.get('部分通过', 0)}</td>"
                    f"<td>{item.get('通过率', '0%')}</td>")

        summary_rows = ''
        for key, item in summary.items():
            cls = 'row-ok' if (key == '文档格式检查' and item.get('结果') == '通过') or \
                             (key != '文档格式检查' and item.get('不通过', 0) == 0 and item.get('失败', 0) == 0) else 'row-fail'
            summary_rows += f'<tr class="{cls}"><td class="type-col">{_html.escape(key)}</td>{_summary_cell(key, item)}</tr>'

        # ===== 错误详情 =====
        errors = report.get('errors', [])
        if errors:
            # 按来源分组显示
            err_items = []
            for e in errors[:200]:
                row = e.get('row', '?')
                msg = e.get('message', e.get('rule', ''))
                src = e.get('source', 'wx')
                src_cls = 'wx' if 'wx' in src else 'adddir'
                src_label = 'wx' if 'wx' in src else 'addDir'
                err_items.append(
                    f'<li class="err {src_cls}"><span class="row-tag">第{row}行</span> '
                    f'<span class="src-tag {src_cls}">{src_label}</span> '
                    f'<span class="err-msg">{_html.escape(str(msg))}</span></li>')
            errors_html = '<div class="list-scroll"><ul>' + ''.join(err_items) + '</ul></div>'
            if len(errors) > 200:
                errors_html += f'<div class="more-hint">… 还有 {len(errors) - 200} 条错误未显示（详见Excel/JSON报告）</div>'
        else:
            errors_html = '<div class="ok">✅ 未发现错误</div>'

        # ===== 修正建议 =====
        corrs = report.get('correction_suggestions', [])
        if corrs:
            corr_items = []
            for c in corrs[:80]:
                row = c.get('row', '?')
                field = c.get('field', '?')
                cur = c.get('current_value', '')
                sug = c.get('suggested_value', '')
                reason = c.get('reason', '')
                src = c.get('source', 'wx')
                src_label = 'wx' if 'wx' in src else 'addDir'
                cur_str = str(cur) if str(cur).strip() else '（空）'
                corr_items.append(
                    f'<li class="corr"><div class="corr-head"><span class="row-tag">第{row}行</span> '
                    f'<span class="field-tag">{_html.escape(str(field))}</span> '
                    f'<span class="src-tag {("wx" if "wx" in src else "adddir")}">{src_label}</span></div>'
                    f'<div class="corr-body">当前值: <s>{_html.escape(cur_str)}</s> '
                    f'→ 建议: <span class="sug">{_html.escape(str(sug))}</span></div>'
                    f'<div class="corr-reason">💡 {_html.escape(str(reason))}</div></li>'
                )
            corr_html = '<div class="list-scroll"><ul>' + ''.join(corr_items) + '</ul></div>'
            if len(corrs) > 80:
                corr_html += f'<div class="more-hint">… 还有 {len(corrs) - 80} 条修正建议未显示（详见Excel/JSON报告）</div>'
        else:
            corr_html = '<div class="ok">✨ 无需修正</div>'

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>有线组件清单审核报告</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'PingFang SC', 'Microsoft YaHei', sans-serif;
       background:linear-gradient(135deg,#667eea 0%,#764ba2 100%); min-height:100vh; padding:30px 20px; }}
.container {{ max-width:1100px; margin:0 auto; }}
.header {{ text-align:center; color:#fff; margin-bottom:28px; }}
.header h1 {{ font-size:2.1rem; font-weight:700; text-shadow:0 2px 6px rgba(0,0,0,.15); }}
.header .sub {{ color:rgba(255,255,255,.9); margin-top:8px; font-size:.95rem; }}
.card {{ background:#fff; border-radius:16px; box-shadow:0 10px 40px rgba(0,0,0,.15); padding:26px 28px; margin-bottom:22px; }}
.card-title {{ display:flex; justify-content:space-between; align-items:center; font-size:1.25rem; font-weight:700; color:#333;
             padding-bottom:14px; border-bottom:2px solid #f0f0f0; margin-bottom:18px; }}
.card-title::before {{ content:''; display:inline-block; width:4px; height:22px; background:linear-gradient(180deg,#667eea,#764ba2);
             border-radius:2px; margin-right:10px; }}
.card-title-wrap {{ display:flex; align-items:center; }}
.badge {{ padding:5px 16px; border-radius:20px; color:#fff; font-weight:600; font-size:.9rem; }}
.badge.success {{ background:#27ae60; }}
.badge.danger {{ background:#e74c3c; }}
.stats-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:18px; margin-bottom:22px; }}
.stat-card {{ background:linear-gradient(135deg,#f5f7fa,#c3cfe2); border-radius:12px; padding:22px; text-align:center; }}
.stat-card.passed {{ background:linear-gradient(135deg,#d4edda,#c3e6cb); }}
.stat-card.failed {{ background:linear-gradient(135deg,#f8d7da,#f5c6cb); }}
.stat-card.corr {{ background:linear-gradient(135deg,#fff3e0,#ffe0b2); }}
.stat-num {{ font-size:2.4rem; font-weight:700; color:#333; line-height:1; margin-bottom:8px; }}
.stat-label {{ font-size:.9rem; color:#666; font-weight:500; }}
.pass-rate {{ text-align:center; margin:26px 0; }}
.pass-rate-circle {{ display:inline-flex; align-items:center; justify-content:center; width:150px; height:150px; border-radius:50%;
        background:conic-gradient({rate_color} {rate_deg}deg, #eef0f5 {rate_deg}deg); position:relative; }}
.pass-rate-inner {{ position:absolute; width:112px; height:112px; border-radius:50%; background:#fff;
        display:flex; flex-direction:column; align-items:center; justify-content:center; }}
.pass-rate-value {{ font-size:2rem; font-weight:700; color:#333; line-height:1; }}
.pass-rate-text {{ font-size:.85rem; color:#999; margin-top:4px; }}
.notes-list {{ list-style:none; }}
.notes-list li {{ background:#fff8e1; border-left:4px solid #f39c12; padding:9px 14px; border-radius:6px; margin:8px 0; color:#7a5b00; }}
table {{ width:100%; border-collapse:collapse; font-size:.92rem; }}
th, td {{ border:1px solid #eee; padding:10px 12px; text-align:left; }}
th {{ background:#f0f2f5; color:#555; font-weight:600; }}
td.type-col {{ font-weight:700; color:#333; }}
tr.row-ok td {{ background:#f8fff9; }}
tr.row-fail td {{ background:#fffafa; }}
tr.row-fail td.type-col {{ color:#c0392b; }}
.list-scroll {{ max-height:420px; overflow-y:auto; }}
ul {{ list-style:none; }}
li {{ margin:8px 0; }}
li.err {{ display:flex; align-items:flex-start; gap:8px; background:#fdecea; border-left:4px solid #e74c3c;
        padding:9px 12px; border-radius:6px; font-size:.92rem; }}
li.err.wx {{ border-left-color:#3498db; background:#eaf6fd; }}
li.err.adddir {{ border-left-color:#e74c3c; background:#fdecea; }}
.row-tag {{ display:inline-block; background:#34495e; color:#fff; padding:1px 8px; border-radius:10px; font-size:.78rem; white-space:nowrap; }}
.src-tag {{ padding:1px 8px; border-radius:10px; font-size:.78rem; white-space:nowrap; color:#fff; }}
.src-tag.wx {{ background:#3498db; }}
.src-tag.adddir {{ background:#e67e22; }}
.err-msg {{ flex:1; color:#333; }}
li.corr {{ background:#fff8e1; border-left:4px solid #ff9800; padding:10px 14px; border-radius:8px; margin:10px 0; }}
.corr-head {{ display:flex; align-items:center; gap:8px; margin-bottom:6px; }}
.field-tag {{ background:#ff9800; color:#fff; padding:1px 10px; border-radius:10px; font-size:.8rem; font-weight:600; }}
.corr-body {{ font-size:.92rem; color:#444; margin-bottom:5px; }}
.corr-body s {{ color:#999; background:#f5f5f5; padding:0 6px; border-radius:4px; }}
.sug {{ color:#27ae60; font-weight:700; background:#e8f5e9; padding:0 8px; border-radius:4px; }}
.corr-reason {{ font-size:.82rem; color:#888; font-style:italic; }}
.ok {{ text-align:center; color:#27ae60; font-weight:600; font-size:1.05rem; padding:30px 0; }}
.more-hint {{ text-align:center; color:#999; font-size:.85rem; margin-top:10px; }}
.footer {{ text-align:center; margin-top:24px; color:rgba(255,255,255,.85); font-size:.85rem; }}
@media (max-width:768px) {{ body {{ padding:15px; }} .header h1 {{ font-size:1.6rem; }} }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>📋 有线组件清单审核报告</h1>
    <div class="sub">生成时间: {generated_at} · 源文件: {_html.escape(str(report.get('source_excel', '')))}</div>
  </div>

  <div class="card">
    <div class="card-title"><span class="card-title-wrap">综合结论</span><span class="badge {badge_cls}">{badge_text}</span></div>
    <div class="stats-grid">{stat_cards}</div>
    <div class="pass-rate">
      <div class="pass-rate-circle">
        <div class="pass-rate-inner">
          <div class="pass-rate-value">{pass_rate:.1f}%</div>
          <div class="pass-rate-text">逐行通过率</div>
        </div>
      </div>
    </div>
    <ul class="notes-list">{notes_html}</ul>
  </div>

  <div class="card">
    <div class="card-title"><span class="card-title-wrap">各类校验汇总</span></div>
    <table>
      <tr><th>校验类型</th><th>总记录</th><th>通过</th><th>不通过</th><th>部分通过</th><th>通过率/结果</th></tr>
      {summary_rows}
    </table>
  </div>

  <div class="card">
    <div class="card-title"><span class="card-title-wrap">错误详情</span><span class="badge danger">{len(errors)} 项</span></div>
    {errors_html}
  </div>

  <div class="card">
    <div class="card-title"><span class="card-title-wrap">修正建议</span><span class="badge info" style="background:#f39c12;">{len(corrs)} 条</span></div>
    {corr_html}
  </div>

  <div class="footer">由 yx-sw-component-manager 技能自动生成 · 仅供参考</div>
</div>
</body>
</html>"""

    # ==================== JSON 报告 ====================
    def save_json_report(self, report: Dict[str, Any], output_path: str = None) -> Optional[str]:
        """保存 JSON 报告"""
        try:
            output_path = output_path or self._default_json_path()
            if output_path is None:
                logger.error("无法确定JSON输出路径")
                return None
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            logger.info(f"JSON审核报告已保存: {output_path}")
            return output_path
        except Exception as e:
            logger.error(f"保存JSON审核报告失败: {e}", exc_info=True)
            return None

    # ==================== 路径辅助 ====================
    def _base_stem(self) -> str:
        import os
        if self.excel_path:
            return os.path.splitext(os.path.basename(self.excel_path))[0]
        return '组件清单'

    def _default_excel_path(self) -> str:
        import os
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        return os.path.join(self.output_dir, f"{self._base_stem()}_审核报告_{ts}.xlsx") if self.output_dir else None

    def _default_html_path(self) -> str:
        import os
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        return os.path.join(self.output_dir, f"{self._base_stem()}_审核报告_{ts}.html") if self.output_dir else None

    def _default_json_path(self) -> str:
        import os
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        return os.path.join(self.output_dir, f"{self._base_stem()}_审核报告_{ts}.json") if self.output_dir else None
