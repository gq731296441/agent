#!/usr/bin/env python3
"""
统一校验编排模块
融合 wx-sw-component-list-check 的逐行规则引擎 + component-manifest-verify-addDir 的
格式检查/PDM编号/代码库拆分/路径校验，输出统一审核报告（Excel + HTML + JSON）。

流程：
  1. 加载 Excel 并智能选择工作表
  2. 表头验证（wx 13 必需列）
  3. 逐行规则引擎校验（wx）
  4. API 一致性检查（wx：PDM部件/iComp第三方组件）
  5. addDir 校验（格式检查、CMDB编号、代码库拆分、Gerrit路径校验）
  6. 生成统一审核报告
"""

import os
import sys
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# 统一导入：先将项目根目录加入 sys.path（兼容直接运行与被 main.py 导入）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from scripts.processors.excel_processor import load_excel_file
from scripts.validators import HeaderValidator, RuleEngine
from scripts.api_clients import PartAPIClient, ComponentAPIClient
from scripts.processors.consistency_checker import ConsistencyChecker
from scripts.processors.data_corrector import DataCorrector
from scripts.processors.report_generator import ReportGenerator
from scripts.validate.report import UnifiedReportGenerator
from scripts.validate import db_and_path_check
from scripts.utils.logger import setup_skill_logger


def load_config(config_path):
    """加载配置文件"""
    import yaml
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except Exception as e:
        logger.error(f"加载配置文件失败: {e}")
        return {}


def validate_excel(processor, required_columns):
    """执行表头验证"""
    validator = HeaderValidator(required_columns)
    columns = processor.get_column_names()
    is_valid, missing, details = validator.validate(columns)
    if not is_valid:
        logger.error(f"表头验证失败，缺少必需列: {missing}")
        return False, details
    logger.info(f"表头验证通过: {len(columns)}列")
    return True, details


def run_row_rules(processor, config, product_model=None, work_group_1=None,
                  work_group_2=None, work_group_3=None):
    """
    执行 wx 的逐行规则引擎 + API 一致性检查（不自动修正，只生成修正建议）
    返回: (statistics, errors, correction_suggestions)
    """
    import pandas as pd
    df = processor.df
    total_rows = len(df)
    logger.info(f"开始逐行规则校验 {total_rows} 行数据")

    rule_engine = RuleEngine(product_model=product_model, work_group_1=work_group_1,
                             work_group_2=work_group_2, work_group_3=work_group_3)
    rule_engine.context['processor'] = processor
    logger.info(f"RuleEngine 已加载规则: {[r.name for r in rule_engine.rules]}")

    check_api = config.get('validation', {}).get('check_api_consistency', True)
    part_client = PartAPIClient() if check_api else None
    comp_client = ComponentAPIClient() if check_api else None
    consistency_checker = ConsistencyChecker(
        processor, product_model=product_model,
        work_group_1=work_group_1, work_group_2=work_group_2, work_group_3=work_group_3)

    statistics = {
        'total_rows': total_rows, 'passed': 0, 'failed': 0,
        'correction_suggestions': 0, 'api_success': 0, 'api_failed': 0, 'warnings': []
    }
    all_errors = []
    all_correction_suggestions = []

    for i in range(total_rows):
        row = processor.get_row_data(i)
        passed, rule_errors = rule_engine.validate_row(row, i)
        if passed:
            statistics['passed'] += 1
        else:
            statistics['failed'] += 1
            all_errors.extend(rule_errors)
            for error in rule_errors:
                all_correction_suggestions.append({
                    'row': error.get('row', i + 1),
                    'field': error.get('field', '该行数据'),
                    'current_value': error.get('current_value', '（验证失败）'),
                    'suggested_value': error.get('suggested_value', '请检查并修正'),
                    'reason': error.get('message', '')
                })

        # API 验证
        category = row.get('部件类别')
        part_number = row.get('部件编号')
        use_component_api = category == '外部第三方组件'
        client = comp_client if use_component_api else part_client
        part_number_valid = part_number is not None and not pd.isna(part_number) and str(part_number).strip() != ''

        if not rule_errors and client and part_number_valid:
            try:
                if use_component_api:
                    api_result = client.search_component(part_number)
                else:
                    api_result = client.query_part(part_number)

                if api_result:
                    statistics['api_success'] += 1
                    if consistency_checker.has_discrepancy(row, api_result):
                        corrections = consistency_checker.get_corrections(row, api_result)
                        for field, suggested_value in corrections.items():
                            current_value = row.get(field)
                            all_correction_suggestions.append({
                                'row': i + 1, 'field': field,
                                'current_value': current_value if not pd.isna(current_value) else '（空）',
                                'suggested_value': suggested_value,
                                'reason': f'与API数据不一致，API返回值为: {suggested_value}'
                            })
                else:
                    statistics['api_failed'] += 1
                    all_errors.append({
                        'row': i + 1, 'type': 'api_no_result',
                        'message': f'API未返回部件 {part_number} 的信息'
                    })
                    all_correction_suggestions.append({
                        'row': i + 1, 'field': '部件编号',
                        'current_value': part_number,
                        'suggested_value': '请确认部件编号是否正确',
                        'reason': f'API未返回部件 {part_number} 的信息'
                    })
            except Exception as e:
                statistics['api_failed'] += 1
                logger.warning(f"第{i+1}行API调用失败: {e}")
                all_errors.append({'row': i + 1, 'type': 'api_exception', 'message': str(e)})
                all_correction_suggestions.append({
                    'row': i + 1, 'field': '部件编号',
                    'current_value': part_number,
                    'suggested_value': '请检查网络或稍后重试',
                    'reason': f'API调用失败: {str(e)}'
                })

        statistics['correction_suggestions'] = len(all_correction_suggestions)
        if (i + 1) % 100 == 0:
            logger.info(f"已处理 {i+1}/{total_rows} 行")

    logger.info(f"逐行规则校验完成: {statistics['passed']}/{total_rows} 通过, {statistics['failed']} 失败")
    return statistics, all_errors, all_correction_suggestions


def build_error_detail_rows(processor, errors):
    """
    构建逐行校验明细行（在原始数据基础上追加 错误信息详情 列），
    供统一 Excel 报告的"逐行校验明细"sheet 使用。
    """
    import pandas as pd
    df = processor.original_df.copy()
    df['错误信息详情'] = ''

    errors_by_row = {}
    for error in errors:
        row_num = error.get('row', 0)
        errors_by_row.setdefault(row_num, [])
        info = f"[{error.get('type', '警告')}] {error.get('message', '')}"
        if error.get('warning'):
            info += ' (警告)'
        errors_by_row[row_num].append(info)

    for row_num, messages in errors_by_row.items():
        df_index = row_num - 2  # Excel第1行表头，数据从第2行开始
        if 0 <= df_index < len(df):
            df.at[df_index, '错误信息详情'] = '\n'.join(messages)

    return df.where(pd.notnull(df), '').to_dict(orient='records')


def run_validate(excel_path, config_path=None, output_dir=None, product_model=None,
                 work_group_1=None, work_group_2=None, work_group_3=None,
                 enable_path_check=None, log_level='INFO'):
    """
    统一校验入口

    Args:
        excel_path: Excel文件路径
        config_path: 配置文件路径
        output_dir: 输出目录（默认 Excel 同目录）
        product_model/work_group_*: 跨产品一致性检查参数
        enable_path_check: 是否启用 Gerrit 路径校验
        log_level: 日志级别

    Returns:
        dict: 结果字典（含报告文件路径）
    """
    from scripts.utils.logger import LoggerConfig

    logger = LoggerConfig.setup_logger(
        name='yx_sw_component_manager', level=getattr(logging, log_level.upper(), logging.INFO),
        log_dir=os.path.join(os.getcwd(), 'logs'), detailed=True, console=True)

    excel_path = str(excel_path)
    if not os.path.exists(excel_path):
        logger.error(f"Excel文件不存在: {excel_path}")
        return {'status': 'error', 'message': f'Excel文件不存在: {excel_path}'}

    config_path = config_path or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                              'assets', 'config', 'default_config.yaml')
    config = load_config(config_path)

    output_dir = output_dir or str(Path(excel_path).parent)
    os.makedirs(output_dir, exist_ok=True)

    try:
        # 1. 加载 Excel
        logger.info(f"正在处理: {excel_path}")
        processor = load_excel_file(str(excel_path), config.get('validation', {}).get('required_columns'))
        if not processor:
            return {'status': 'error', 'message': '无法加载Excel文件'}

        # 2. 表头验证
        required_columns = config.get('validation', {}).get('required_columns')
        if not required_columns:
            return {'status': 'error', 'message': '配置文件中缺少 required_columns 定义'}
        valid, details = validate_excel(processor, required_columns)
        if not valid:
            return {'status': 'error', 'message': f'表头验证失败，缺少必需列: {details.get("missing_columns")}'}

        # 3. 逐行规则校验 + API 一致性（wx）
        statistics, errors, corrections = run_row_rules(
            processor, config,
            product_model=product_model, work_group_1=work_group_1,
            work_group_2=work_group_2, work_group_3=work_group_3)

        # 4. addDir 校验（格式/PDM编号/代码库/路径）
        logger.info("执行 addDir 综合校验（格式/PDM编号/代码库/路径）...")
        try:
            db_result = db_and_path_check.run_checks(
                str(excel_path), enable_path_check=enable_path_check)
        except Exception as e:
            logger.error(f"addDir 校验失败: {e}")
            db_result = {
                'format_df': None, 'pdm_df': None, 'code_df': None,
                'additional_df': None, 'format_check_result': str(e), 'doc_info': {},
                'filename': os.path.basename(str(excel_path)),
            }

        # 5. 构建统一报告
        report_gen = UnifiedReportGenerator(excel_path=excel_path, output_dir=output_dir)
        report = report_gen.build_report(
            statistics, errors, corrections,
            format_df=db_result.get('format_df'),
            pdm_df=db_result.get('pdm_df'),
            code_df=db_result.get('code_df'),
            additional_df=db_result.get('additional_df'))

        # 6. 生成报告文件（Excel + HTML + JSON）
        error_detail_rows = build_error_detail_rows(processor, errors)
        excel_report = report_gen.save_excel_report(
            report, statistics,
            format_df=db_result.get('format_df'),
            pdm_df=db_result.get('pdm_df'),
            code_df=db_result.get('code_df'),
            additional_df=db_result.get('additional_df'),
            error_detail_rows=error_detail_rows)
        html_report = report_gen.save_html_report(report)
        json_report = report_gen.save_json_report(report)

        result = {
            'status': 'success',
            'mode': 'unified',
            'total_rows': statistics['total_rows'],
            'passed': statistics['passed'],
            'failed': statistics['failed'],
            'correction_suggestions': statistics['correction_suggestions'],
            'overall_status': report['overall_status'],
            'excel_report': excel_report,
            'html_report': html_report,
            'json_report': json_report,
            'errors': errors,
            'correction_suggestions_list': corrections,
        }
        logger.info(f"统一审核报告已生成: {excel_report}")
        return result

    except KeyboardInterrupt:
        logger.warning("用户中断处理")
        return {'status': 'error', 'message': '用户中断'}
    except Exception as e:
        logger.error(f"校验过程异常: {e}", exc_info=True)
        return {'status': 'error', 'message': str(e)}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='有线组件清单统一校验')
    parser.add_argument('excel_path', help='Excel文件路径')
    parser.add_argument('--config', help='配置文件路径')
    parser.add_argument('--output-dir', help='输出目录')
    parser.add_argument('--product-model', help='产品规格型号')
    parser.add_argument('--work-group-1', help='工作组类1编号')
    parser.add_argument('--work-group-2', help='工作组类2编号')
    parser.add_argument('--work-group-3', help='工作组类3编号')
    parser.add_argument('--no-path-check', action='store_true', help='禁用Gerrit路径校验')
    parser.add_argument('--log-level', default='INFO', help='日志级别')
    args = parser.parse_args()

    result = run_validate(
        args.excel_path,
        config_path=args.config,
        output_dir=args.output_dir,
        product_model=args.product_model,
        work_group_1=args.work_group_1,
        work_group_2=args.work_group_2,
        work_group_3=args.work_group_3,
        enable_path_check=not args.no_path_check,
        log_level=args.log_level)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get('status') == 'success' and result.get('failed', 0) == 0 else 1)
