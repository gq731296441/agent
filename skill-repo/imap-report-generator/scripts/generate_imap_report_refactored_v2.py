#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IMAP端到端需求交付分析报告生成器（重构优化版 - API接口调用）

主要改进:
- 提取"分析结论3-关键"用于周期趋势分析TAB页
- 从API接口数据中动态提取实际的时间序列区间
- 确保活动周期按正确顺序排列（MR数据或PR数据的顺序）
- 确保时间序列数据正确传递到JavaScript
- 通过调用AIP接口获取IMAP端到端需求交付分析数据

使用方法:
    python3 generate_imap_report_refactored_v2.py \\
        --data-source-url "https://aip.zte.com.cn/middle/api/remote/call/xxx" \\
        --column-name-source "PR交付周期" \\
        --section-cycle-name "PR分析等待周期" "PR分析周期" "PR开发等待周期" "PR开发周期" "PR验证等待周期" "PR验证周期" "PR系统测试等待周期" "PR系统测试周期" \\
        --category-column "分类1" \\
        --direction "向低" \\
        --indicator-type "周期类" \\
        --html-template /path/to/template.html \\
        --css-template /path/to/style.css \\
        --js-template /path/to/script.js \\
        --output-dir /workspace
"""

import json
import re
import argparse
import requests
from pathlib import Path
from datetime import datetime


# ============================================================================
# API调用函数
# ============================================================================

def call_imap_analysis_api(data_source_url, column_name_source, section_cycle_name, 
                            category_column, direction, indicator_type):
    """
    调用IMAP端到端需求交付分析API接口获取数据
    
    Args:
        data_source_url: 数据源URL
        column_name_source: 列名来源（如"PR交付周期"、"MR交付周期"）
        section_cycle_name: 分段周期名称列表
        category_column: 分类列名
        direction: 方向（如"向低"）
        indicator_type: 指标类型（如"周期类"）
    
    Returns:
        dict: API返回的JSON数据，如果失败则返回None
    """
    api_url = "https://aip.zte.com.cn/imap/api/data_analysis/endtoend_dashboard_analyze"
    
    headers = {
        "Content-Type": "application/json"
    }
    
    payload = {
        "data_source_url": data_source_url,
        "column_name_source": column_name_source,
        "section_cycle_name": section_cycle_name,
        "category_column": category_column,
        "direction": direction,
        "indicator_type": indicator_type
    }
    
    try:
        print(f"🌐 正在调用API接口：{api_url}")
        print(f"📋 请求参数:")
        print(f"   - data_source_url: {data_source_url}")
        print(f"   - column_name_source: {column_name_source}")
        print(f"   - section_cycle_name: {section_cycle_name}")
        print(f"   - category_column: {category_column}")
        print(f"   - direction: {direction}")
        print(f"   - indicator_type: {indicator_type}")
        
        response = requests.post(api_url, json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        
        result = response.json()
        
        # 检查API返回状态
        if isinstance(result, dict):
            # 检查是否有错误信息
            if result.get('code') != 200 and result.get('success') is False:
                error_msg = result.get('message', result.get('msg', '未知错误'))
                print(f"❌ 错误：API调用失败 - {error_msg}")
                return None
        
        print("✅ API调用成功，获取到数据")
        return result
        
    except requests.exceptions.Timeout:
        print(f"❌ 错误：API请求超时（超过60秒）")
        return None
    except requests.exceptions.ConnectionError as e:
        print(f"❌ 错误：网络连接失败 - {e}")
        return None
    except requests.exceptions.HTTPError as e:
        print(f"❌ 错误：HTTP错误 - {e}")
        print(f"   响应状态码：{response.status_code}")
        print(f"   响应内容：{response.text[:500] if response else 'N/A'}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"❌ 错误：请求异常 - {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"❌ 错误：JSON解析失败 - {e}")
        print(f"   响应内容：{response.text[:500] if response else 'N/A'}")
        return None
    except Exception as e:
        print(f"❌ 错误：未知错误 - {e}")
        return None


def read_template(filepath):
    """读取模板文件"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        print(f"❌ 错误：模板文件不存在 - {filepath}")
        return None
    except Exception as e:
        print(f"❌ 错误：读取模板文件失败 - {e}")
        return None


# ============================================================================
# 数据提取函数（统一数据清洗逻辑）
# ============================================================================

def clean_numeric_value(value):
    """
    统一的数据清洗函数：将"nan"字符串、空字符串、None转换为0.0
    
    Args:
        value: 任意类型的值
    
    Returns:
        float: 清洗后的数值
    """
    if isinstance(value, str):
        if value.lower() == 'nan' or value.strip() == '':
            return 0.0
        try:
            return float(value)
        except ValueError:
            return 0.0
    elif value is None:
        return 0.0
    else:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0


def extract_report_title(data):
    """从iCenter数据中提取报告标题的项目组/发布版本名称"""
    basic_info = data.get('基本信息', '')
    
    if not isinstance(basic_info, str):
        return 'IMAP端到端'
    
    # 规则1：优先提取"发布版本"后的内容
    match = re.search(r'发布版本\s*[：:]?\s*([^，,]+?)(?:，|,|$)', basic_info)
    if match:
        return match.group(1).strip()
    
    # 规则2：提取"本次分析针对"后的内容
    match = re.search(r'本次分析针对\s*[：:]?\s*([^，,]+?)(?:，|,|$)', basic_info)
    if match:
        return match.group(1).strip()
    
    return 'IMAP端到端'


def extract_data_time_range(data):
    """从iCenter数据中提取数据时间范围"""
    basic_info = data.get('基本信息', '')
    
    if not isinstance(basic_info, str):
        return None
    
    match = re.search(r'数据时间范围[：:\s]+([^\n，,]+)', basic_info)
    if match:
        return match.group(1).strip()
    
    return None


def extract_demand_types(data):
    """从iCenter数据中提取需求类型"""
    analysis_result = data.get('分析结果', {})
    analysis_conclusion1 = analysis_result.get('分析结论1', {})
    return list(analysis_conclusion1.keys())


def sanitize_variable_name(name):
    """
    清理变量名，确保符合JavaScript变量命名规则：
    1. 不能以数字开头
    2. 只能包含字母、数字、下划线、美元符号
    3. 不能是JavaScript保留字
    
    Args:
        name: 原始名称（如需求类型名称）
    
    Returns:
        str: 合法的JavaScript变量名
    """
    # 替换非法字符：斜杠、连字符、空格等
    var_name = name.replace('/', '_').replace('-', '_').replace(' ', '')
    
    # 如果以数字开头，添加前缀'demand_'
    if var_name and var_name[0].isdigit():
        var_name = 'demand_' + var_name
    
    # 如果为空或包含其他非法字符，使用默认名称
    if not var_name or not re.match(r'^[a-zA-Z_$][\w$]*$', var_name):
        # 使用哈希值生成唯一名称
        var_name = 'demand_' + str(abs(hash(name)) % 10000)
    
    return var_name


def extract_cycle_data(data, demand_type):
    """提取某个需求类型的周期数据（已清洗）"""
    analysis_result = data.get('分析结果', {})
    analysis_conclusion1 = analysis_result.get('分析结论1', {})
    cycles = analysis_conclusion1.get(demand_type, {})
    
    # 使用统一的清洗函数
    return {key: clean_numeric_value(value) for key, value in cycles.items()}


def extract_trend_data(data, demand_type):
    """
    提取某个需求类型的趋势数据（已清洗）
    
    Returns:
        dict: 清洗后的趋势数据，包含每个活动周期的：
              - 趋势方向
              - 均值
              - 中位数
              - 变化幅度
              - 趋势斜率
              - 标准差
              - 数据点数
              - 趋势数据（时间序列）
    """
    analysis_result = data.get('分析结果', {})
    analysis_conclusion3 = analysis_result.get('分析结论3', {})
    trend_data = analysis_conclusion3.get(demand_type, {})
    
    cleaned_trend = {}
    for key, value in trend_data.items():
        if isinstance(value, dict):
            cleaned_cycle = {}
            for sub_key, sub_value in value.items():
                if sub_key == '趋势数据' and isinstance(sub_value, dict):
                    # 清洗时间序列数据
                    cleaned_series = {
                        time_point: clean_numeric_value(time_value)
                        for time_point, time_value in sub_value.items()
                    }
                    cleaned_cycle[sub_key] = cleaned_series
                elif sub_key in ['趋势方向', '变化幅度']:
                    # 保留趋势方向和变化幅度的原始值（字符串）
                    cleaned_cycle[sub_key] = sub_value
                else:
                    cleaned_cycle[sub_key] = clean_numeric_value(sub_value)
            cleaned_trend[key] = cleaned_cycle
        else:
            cleaned_trend[key] = clean_numeric_value(value)
    
    return cleaned_trend


def extract_bottom3_for_demand_type(data, demand_type):
    """
    提取某个需求类型的所有BOTTOM3领域（从分析结论4中提取）
    
    Args:
        data: iCenter数据
        demand_type: 需求类型
    
    Returns:
        list: BOTTOM3领域列表，每个元素是包含"领域"和"均值"的字典
    """
    analysis_result = data.get('分析结果', {})
    analysis_conclusion4 = analysis_result.get('分析结论4', {})
    
    # 收集所有属于该需求类型的BOTTOM3领域
    all_bottom3 = []
    seen_fields = set()  # 用于去重
    
    for key, value in analysis_conclusion4.items():
        # 键名格式："{需求类型}_{活动周期}"，如"OTN-业务控制_MR分析周期"
        if key.startswith(demand_type + '_'):
            if isinstance(value, dict):
                bottom3_list = value.get('待改进的BOTTOM3领域', [])
                if isinstance(bottom3_list, list):
                    for item in bottom3_list:
                        if isinstance(item, dict):
                            field_name = item.get('领域', '')
                            mean_value = item.get('均值', 0)
                            # 去重：如果领域名称已经存在，跳过
                            if field_name and field_name not in seen_fields:
                                all_bottom3.append({
                                    '领域': field_name,
                                    '均值': mean_value
                                })
                                seen_fields.add(field_name)
    
    # 只保留前3个（BOTTOM3）
    return all_bottom3[:3]


def extract_equity_conclusion(data):
    """提取活动均衡性分析的关键结论"""
    analysis_result = data.get('分析结果', {})
    conclusion_key = analysis_result.get('分析结论2-关键', '')
    
    if isinstance(conclusion_key, str) and conclusion_key.strip():
        return conclusion_key.strip()
    
    return None


def extract_trend_conclusion(data):
    """
    提取周期趋势分析的关键结论（分析结论3-关键）
    
    Returns:
        str: 周期趋势分析的关键结论，如"MR开发周期：OTN-业务控制(15.8天, +111.43%)；..."
    """
    analysis_result = data.get('分析结果', {})
    conclusion_key = analysis_result.get('分析结论3-关键', '')
    
    # 如果conclusion_key是字典，说明包含每个需求类型的详细结论
    if isinstance(conclusion_key, dict):
        # 提取"总体"字段的汇总信息
        overall = conclusion_key.get('总体', '')
        if overall:
            return overall
        
        # 如果没有"总体"字段，则拼接所有需求类型的结论
        conclusions = []
        for demand_type, conclusion in conclusion_key.items():
            if demand_type != '总体' and conclusion:
                conclusions.append(conclusion)
        if conclusions:
            return '；'.join(conclusions)
    
    # 如果conclusion_key是字符串，直接返回
    if isinstance(conclusion_key, str) and conclusion_key.strip():
        return conclusion_key.strip()
    
    return None


def extract_abnormal_requirements(data, demand_type):
    """提取异常需求详单 - 按周期类型分组（包含空列表的周期类型）"""
    analysis_result = data.get('分析结果', {})
    analysis_conclusion5 = analysis_result.get('分析结论5', {})
    
    abnormal_by_cycle = {}
    for key, abnormal_info in analysis_conclusion5.items():
        if key.startswith(demand_type + '_'):
            cycle_type = key.replace(demand_type + '_', '')
            if isinstance(abnormal_info, dict):
                abnormal_list = abnormal_info.get('异常需求列表', [])
                analysis_reason = abnormal_info.get('分析原因', '异常周期')
                
                # 即使abnormal_list为空，也要包含该周期类型
                # 周期字段名就是cycle_type，如"MR分析周期"或"PR分析周期"
                cycle_field_name = cycle_type
                
                abnormal_by_cycle[cycle_type] = {
                    'reason': analysis_reason,  # 使用分析原因字段
                    'cycle_field_name': cycle_field_name,  # 添加周期字段名
                    'requirements': abnormal_list  # 可能为空列表
                }
    
    return abnormal_by_cycle


def extract_time_series_range(data, demand_type):
    """
    从iCenter数据中提取实际的时间序列区间
    
    Args:
        data: iCenter数据
        demand_type: 需求类型
    
    Returns:
        list: 时间序列列表，如['2025-08', '2025-09', ..., '2026-08']
    """
    trend_data = extract_trend_data(data, demand_type)
    
    # 从任意一个活动周期的趋势数据中提取时间序列
    for cycle_name, cycle_info in trend_data.items():
        if isinstance(cycle_info, dict) and '趋势数据' in cycle_info:
            time_series = list(cycle_info['趋势数据'].keys())
            if time_series:
                return time_series
    
    return []


# ============================================================================
# 辅助函数
# ============================================================================

def get_cycle_order(cycle_names):
    """
    根据周期名称确定正确的顺序（支持MR数据和PR数据）
    
    Args:
        cycle_names: 周期名称列表
    
    Returns:
        排序后的周期名称列表
    """
    mr_order = [
        'MR分析等待周期', 'MR分析周期', 'MR计划周期',
        'MR开发等待周期', 'MR开发周期',
        'MR验收等待周期', 'MR验收周期'
    ]
    
    pr_order = [
        'PR分析等待周期', 'PR分析周期',
        'PR开发等待周期', 'PR开发周期',
        'PR验证等待周期', 'PR验证周期',
        'PR系统测试等待周期', 'PR系统测试周期'
    ]
    
    # 判断是MR数据还是PR数据
    if any(name.startswith('MR') for name in cycle_names):
        sorted_cycles = [c for c in mr_order if c in cycle_names]
        sorted_cycles.extend([c for c in cycle_names if c not in sorted_cycles])
        return sorted_cycles
    elif any(name.startswith('PR') for name in cycle_names):
        sorted_cycles = [c for c in pr_order if c in cycle_names]
        sorted_cycles.extend([c for c in cycle_names if c not in sorted_cycles])
        return sorted_cycles
    else:
        return list(cycle_names)


# ============================================================================
# 数据生成函数
# ============================================================================

def generate_key_findings_table(data, demand_types):
    """生成关键发现表格数据"""
    if not demand_types:
        return []
    
    # 获取周期顺序（只获取一次）
    first_cycles = extract_cycle_data(data, demand_types[0])
    ordered_cycles = get_cycle_order(list(first_cycles.keys()))
    non_wait_cycles = [c for c in ordered_cycles if '等待' not in c]
    
    rows = []
    for demand_type in demand_types:
        cycles = extract_cycle_data(data, demand_type)
        
        # 最长非等待周期
        non_wait_values = {c: cycles.get(c, 0) for c in non_wait_cycles}
        if non_wait_values:
            max_cycle = max(non_wait_values.items(), key=lambda x: x[1])
            max_non_wait = f"{max_cycle[0]} ({max_cycle[1]}天)"
        else:
            max_non_wait = "无"
        
        # 超20天的等待周期
        wait_over_20 = [
            f"{c} ({cycles.get(c, 0)}天)"
            for c in ordered_cycles
            if '等待' in c and cycles.get(c, 0) > 20
        ]
        
        rows.append({
            'name': demand_type,
            'max_non_wait': max_non_wait,
            'wait_over_20': '、'.join(wait_over_20) if wait_over_20 else '无'
        })
    
    return rows


def generate_equity_table(data, demand_types):
    """生成均衡性分析表格数据"""
    if not demand_types:
        return []
    
    # 获取周期顺序（只获取一次）
    first_cycles = extract_cycle_data(data, demand_types[0])
    ordered_cycles = get_cycle_order(list(first_cycles.keys()))
    
    # 收集所有需求类型的周期数据
    all_cycles = {dt: extract_cycle_data(data, dt) for dt in demand_types}
    
    # 计算每个活动的均值
    activity_means = {}
    for activity in ordered_cycles:
        values = [all_cycles[dt].get(activity, 0) for dt in demand_types]
        activity_means[activity] = sum(values) / len(values) if values else 0
    
    # 生成表格行
    rows = []
    for activity in ordered_cycles:
        mean = activity_means[activity]
        threshold = mean * 1.3
        
        row = {'activity': activity, 'values': {}}
        for dt in demand_types:
            value = all_cycles[dt].get(activity, 0)
            row['values'][dt] = {
                'value': value,
                'is_abnormal': value > threshold
            }
        row['mean'] = mean
        row['threshold'] = threshold
        rows.append(row)
    
    return rows


def generate_trend_summary(data, demand_types, ordered_cycles):
    """
    生成趋势分析汇总（按正确的活动周期顺序排列）
    
    Args:
        data: iCenter数据
        demand_types: 需求类型列表
        ordered_cycles: 按正确顺序排列的活动周期列表
    
    Returns:
        dict: 趋势分析汇总，包含每个需求类型的上升趋势周期列表
    """
    trend_summary = {}
    
    for demand_type in demand_types:
        trend_data = extract_trend_data(data, demand_type)
        rising_cycles = []
        
        # 按ordered_cycles的顺序遍历活动周期
        for cycle_name in ordered_cycles:
            if cycle_name in trend_data:
                cycle_info = trend_data[cycle_name]
                if isinstance(cycle_info, dict):
                    trend_direction = cycle_info.get('趋势方向', '')
                    # 处理趋势方向：可能是字符串"上升"或数字1（表示上升）
                    is_rising = (trend_direction == '上升') or (trend_direction == 1) or (isinstance(trend_direction, float) and trend_direction > 0.5)
                    
                    if is_rising:
                        rising_cycles.append({
                            'cycle_name': cycle_name,
                            'mean': cycle_info.get('均值', 0),
                            'median': cycle_info.get('中位数', 0),
                            'change': cycle_info.get('变化幅度', '0%'),
                            'slope': cycle_info.get('趋势斜率', 0),
                            'std': cycle_info.get('标准差', 0),
                            'count': cycle_info.get('数据点数', 0)
                        })
        
        trend_summary[demand_type] = rising_cycles
    
    return trend_summary


def find_max_change(trend_summary):
    """
    从趋势分析汇总中找到最大的变化幅度
    
    Args:
        trend_summary: 趋势分析汇总字典
    
    Returns:
        str: 最大变化幅度字符串，如"+111.43%"
    """
    max_change = 0.0
    max_change_str = "--"
    
    for demand_type, cycles in trend_summary.items():
        for cycle in cycles:
            change_str = cycle.get('change', '0%')
            # 提取变化幅度的数值部分
            try:
                # 处理"+111.43%"或"-10.5%"等格式
                change_value = float(change_str.replace('%', '').replace('+', '').replace('-', ''))
                if change_value > max_change:
                    max_change = change_value
                    max_change_str = change_str
                    # 确保有+号
                    if not max_change_str.startswith('+') and not max_change_str.startswith('-'):
                        max_change_str = '+' + max_change_str
            except (ValueError, AttributeError):
                continue
    
    return max_change_str


def generate_js_data(data, demand_types, cycle_data_for_chart, trend_chart_data, ordered_cycles):
    """
    生成JavaScript图表数据
    
    Returns:
        (cycle_comparison_js, trend_charts_js, legend_html, time_series)
    """
    predefined_colors = [
        '#2563eb', '#7c3aed', '#059669', '#dc2626', '#ea580c',
        '#0891b2', '#db2777', '#4f46e5', '#0ea5e9', '#65a30d'
    ]
    
    # 1. 活动周期对比图数据
    js_lines = [f"  const activities = {list(ordered_cycles)};"]
    legend_items = []
    
    for i, demand_type in enumerate(demand_types):
        var_name = sanitize_variable_name(demand_type)
        cycles = cycle_data_for_chart[demand_type]
        cycle_values = [cycles.get(cycle, 0) for cycle in ordered_cycles]
        numeric_data = [float(x) for x in cycle_values]
        
        js_lines.append(f"  const {var_name}Data = {numeric_data};")
        
        color = predefined_colors[i % len(predefined_colors)]
        legend_items.append(
            f'<div class="legend-item"><div class="legend-color" style="background: {color}"></div>'
            f'<span>{demand_type}</span></div>'
        )
    
    legend_html = ''.join(legend_items)
    
    # 2. 趋势图数据 - 同时提取时间序列
    trend_js_lines = []
    time_series_list = []
    
    # 从第一个需求类型中提取时间序列（所有需求类型的时间序列应该相同）
    if demand_types:
        time_series_list = extract_time_series_range(data, demand_types[0])
    
    for i, demand_type in enumerate(demand_types):
        var_name = sanitize_variable_name(demand_type)
        series_data = trend_chart_data.get(demand_type, {})
        
        series_items = []
        # 按ordered_cycles的顺序遍历活动周期，确保图例顺序正确
        for cycle_name in ordered_cycles:
            if cycle_name in series_data:
                cycle_info = series_data[cycle_name]
                if isinstance(cycle_info, dict) and '趋势数据' in cycle_info:
                    data_array = list(cycle_info['趋势数据'].values())
                else:
                    data_array = []
                # 为每个周期分配不同的颜色（使用预定义颜色循环）
                # 找到cycle_name在ordered_cycles中的索引，用于分配颜色
                cycle_index = ordered_cycles.index(cycle_name)
                color = predefined_colors[cycle_index % len(predefined_colors)]
                series_items.append(f"'{cycle_name}': {{data: {data_array}, color: '{color}'}}")
        
        series_str = '{' + ', '.join(series_items) + '}'
        trend_js_lines.append(f"  const {var_name}TrendData = {series_str};")
    
    return '\n'.join(js_lines), '\n'.join(trend_js_lines), legend_html, time_series_list


# ============================================================================
# 模板修改函数
# ============================================================================

def modify_html_template(html_template, data, demand_types, key_findings, equity_table, 
                         trend_summary, bottom3_data, abnormal_data, cycle_data_for_chart, 
                         trend_chart_data, equity_conclusion=None, trend_conclusion=None,
                         time_series=None,
                         output_css_name='imap_style.css', output_js_name='imap_script.js'):
    """修改HTML模板，填入IMAP数据"""
    
    html = html_template
    
    # 0. 修改CSS和JS引用路径
    html = re.sub(r'href="style\.css"', f'href="{output_css_name}"', html)
    html = re.sub(r'href=\'style\.css\'', f'href=\'{output_css_name}\'', html)
    html = re.sub(r'src="script\.js"', f'src="{output_js_name}"', html)
    html = re.sub(r'src=\'script\.js\'', f'src=\'{output_js_name}\'', html)
    
    # 1. 修改报告标题
    report_project = extract_report_title(data)
    html = re.sub(
        r'<h1 class="report-title">.*?</h1>',
        f'<h1 class="report-title">{report_project}需求交付分析报告</h1>',
        html
    )
    
    # 1.5. 动态生成趋势图容器（支持任意数量的需求类型）
    trend_chart_wrappers = []
    demand_type_icons = ['🚀', '📋', '🍃', '⚡', '🎯', '💡', '🔧', '📊', '🌟', '✨']
    
    for i, demand_type in enumerate(demand_types):
        icon = demand_type_icons[i % len(demand_type_icons)]
        var_name = sanitize_variable_name(demand_type)
        canvas_id = f'{var_name}TrendChart'
        legend_id = f'{var_name}Legend'
        
        trend_chart_wrappers.append(f'''      <!-- {demand_type}趋势图 -->
      <div class="trend-chart-wrapper">
        <h4 class="trend-chart-title">{icon} {demand_type} - 各周期时间序列趋势</h4>
        <canvas id="{canvas_id}"></canvas>
        <div class="chart-legend" id="{legend_id}"></div>
      </div>''')
    
    new_trend_charts_html = '\n'.join(trend_chart_wrappers)
    
    # 动态生成时间范围描述
    if time_series and len(time_series) >= 2:
        time_range_desc = f"{time_series[0]}至{time_series[-1]}"
    else:
        time_range_desc = "2025-07至2026-07"  # 默认值
    
    # 使用更精确的正则表达式匹配trend-charts-container部分
    # 匹配从<div class="trend-charts-container">到</div>（trend-charts-container的结束），后面紧跟trend-summary-card
    html = re.sub(
        r'<div class="trend-charts-container">[\s\S]*?</div>\s*<div class="trend-summary-card">',
        f'<div class="trend-charts-container">\n      <h3 class="section-title">📈 时间序列趋势图</h3>\n      <p class="chart-desc">每种需求类型一个图表，横轴为时间（{time_range_desc}），纵轴为周期天数，每种周期类型一条折线</p>\n      \n{new_trend_charts_html}\n    </div>\n    <div class="trend-summary-card">',
        html,
        flags=re.DOTALL
    )
    
    # 2. 修改数据时间范围（如果存在）
    time_range = extract_data_time_range(data)
    if time_range:
        demand_types_meta = f'<span class="meta-item">📅 数据时间范围：{time_range}</span>'
        html = re.sub(
            r'<div class="report-meta">\s*<span class="meta-item">📊 需求类型：',
            f'<div class="report-meta">\n      {demand_types_meta}\n      <span class="meta-item">📊 需求类型：',
            html
        )
    
    # 3. 修改需求类型元数据
    demand_types_str = ' | '.join(demand_types)
    html = re.sub(
        r'<span class="meta-item">📊 需求类型：.*?</span>',
        f'<span class="meta-item">📊 需求类型：{demand_types_str}</span>',
        html
    )
    
    # 4. 修改关键发现表格
    key_findings_rows = ''.join([
        f'''          <tr>
            <td><strong>{finding['name']}</strong></td>
            <td>{finding['max_non_wait']}</td>
            <td>{finding['wait_over_20']}</td>
          </tr>'''
        for finding in key_findings
    ])
    
    html = re.sub(
        r'<table class="key-findings-table">\s*<thead>.*?<tbody>.*?</tbody>',
        f'<table class="key-findings-table">\n        <thead>\n          <tr>\n            <th>需求类型</th>\n            <th>最长非等待周期</th>\n            <th>超20天异常周期</th>\n          </tr>\n        </thead>\n        <tbody>\n{key_findings_rows}\n        </tbody>\n      </table>',
        html,
        flags=re.DOTALL,
        count=1
    )
    
    # 5. 修改详细周期数据表格
    if demand_types:
        first_cycles = extract_cycle_data(data, demand_types[0])
        ordered_cycles = get_cycle_order(list(first_cycles.keys()))
        
        header_cols = ''.join([f'<th class="col-{i}">{dt}</th>' for i, dt in enumerate(demand_types)])
        data_rows = ''.join([
            f'''          <tr>
            <td>{activity}</td>
            {''.join([f'<td>{cycle_data_for_chart[dt][activity]}</td>' for dt in demand_types])}
          </tr>'''
            for activity in ordered_cycles
        ])
        
        html = re.sub(
            r'<div class="data-table-container">.*?<table class="data-table">.*?</table>',
            f'<div class="data-table-container">\n      <h3 class="table-title">📋 详细周期数据（单位：天）</h3>\n      <table class="data-table">\n        <thead>\n          <tr>\n            <th class="activity-col">活动类型</th>{header_cols}\n          </tr>\n        </thead>\n        <tbody>\n{data_rows}\n        </tbody>\n      </table>\n    </div>',
            html,
            flags=re.DOTALL,
            count=1
        )
    
    # 6. 修改均衡性分析表格
    if demand_types:
        first_cycles = extract_cycle_data(data, demand_types[0])
        ordered_cycles = get_cycle_order(list(first_cycles.keys()))
        
        equity_header = f'<th>活动类型</th>' + ''.join([f'<th>{dt}</th>' for dt in demand_types])
        equity_header += '<th>均值</th><th>30%阈值上限</th><th>异常识别</th>'
        
        equity_rows = ''.join([
            f'''          <tr>
            <td>{row['activity']}</td>
            {''.join([f'<td>{row["values"][dt]["value"]}</td>' for dt in demand_types])}
            <td>{row['mean']:.2f}</td>
            <td>{row['threshold']:.2f}</td>
            <td class="{'abnormal' if any(row['values'][dt]['is_abnormal'] for dt in demand_types) else 'normal'}">
                {'❌ 异常' if any(row['values'][dt]['is_abnormal'] for dt in demand_types) else '✅ 正常'}
            </td>
          </tr>'''
            for row in equity_table
        ])
        
        html = re.sub(
            r'<table class="equity-table">.*?</table>',
            f'<table class="equity-table">\n        <thead>\n          <tr>\n            {equity_header}\n          </tr>\n        </thead>\n        <tbody>\n{equity_rows}\n        </tbody>\n      </table>',
            html,
            flags=re.DOTALL,
            count=1
        )
    
    # 7. 修改活动均衡性分析结论
    if equity_conclusion:
        formatted_conclusion = equity_conclusion.replace('； ', '</li>\n      <li>')
        html = re.sub(
            r'<p class="conclusion-text">\s*<strong>.*?</strong>\s*</p>',
            f'<ul class="conclusion-list">\n      <li>{formatted_conclusion}</li>\n    </ul>',
            html,
            flags=re.DOTALL
        )
        html = re.sub(r'<p class="conclusion-detail">.*?</p>', '', html, flags=re.DOTALL)
    
    # 8.5. 修改周期趋势分析结论（使用"分析结论3-关键"）
    if trend_conclusion:
        # 将"； "替换为列表项，分点展示
        formatted_trend_conclusion = trend_conclusion.replace('； ', '</li>\n      <li>')
        
        # 计算最大变化幅度
        max_change = find_max_change(trend_summary)
        
        # 替换分析规则部分，添加实际结论
        html = re.sub(
            r'<div class="method-content" style="margin-bottom: 20px;">\s*<p><strong>分析规则</strong>：.*?</p>\s*</div>',
            f'<div class="method-content" style="margin-bottom: 20px;">\n        <p><strong>分析规则</strong>：统计不同类型需求中，各研发活动周期近{len(time_series) if time_series else 13}个月（{time_series[0] if time_series and len(time_series) > 0 else "2025-07"} ~ {time_series[-1] if time_series and len(time_series) > 0 else "2026-07"}）的时间序列趋势数据。识别呈现上升趋势的分段周期，汇总关键指标。</p>\n      </div>',
            html,
            flags=re.DOTALL
        )
        
        # 替换总体趋势卡片中的overview-stats部分和添加结论卡片
        # 匹配从overview-stats开始到trend-overview-card结束的部分（包含两个</div>）
        # 注意：overview-stats后面有两个</div>，然后才是trend-charts-container
        html = re.sub(
            r'<div class="overview-stats">[\s\S]*?</div>\s*</div>\s*</div>\s*<div class="trend-charts-container">',
            f'<div class="overview-stats">\n        <div class="stat-item">\n          <span class="stat-number">{sum(len(v) for v in trend_summary.values())}</span>\n          <span class="stat-label">分段周期呈现上升趋势</span>\n        </div>\n        <div class="stat-item">\n          <span class="stat-number">{len(demand_types)}</span>\n          <span class="stat-label">涉及分类（{"、".join(demand_types)}）</span>\n        </div>\n        <div class="stat-item highlight">\n          <span class="stat-number">{max_change}</span>\n          <span class="stat-label">最大变化幅度</span>\n        </div>\n        <div class="stat-item">\n          <span class="stat-number">{len(time_series) if time_series else 13}</span>\n          <span class="stat-label">月时间序列数据点</span>\n        </div>\n      </div>\n    </div>\n    \n    <!-- 周期趋势分析结论 -->\n    <div class="trend-conclusion-card" style="background: linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%); border-left: 4px solid #0284c7; padding: 20px; margin: 20px 0; border-radius: 8px;">\n      <h3 class="conclusion-title" style="color: #0c4a6e; margin-bottom: 12px; font-size: 18px;">🎯 周期趋势分析结论</h3>\n      <ul class="conclusion-list" style="list-style: none; padding: 0; margin: 0;">\n        <li style="padding: 8px 0; border-bottom: 1px solid #e0f2fe; color: #1e40af;">{formatted_trend_conclusion}</li>\n      </ul>\n    </div>\n    \n    <div class="trend-charts-container">',
            html,
            flags=re.DOTALL
        )
        
        # 如果上面的替换没有成功（可能是因为HTML结构不同），尝试另一种模式
        if '<span class="stat-number">{max_change}</span>' not in html:
            # 尝试只匹配一个</div>的情况
            html = re.sub(
                r'<div class="overview-stats">[\s\S]*?</div>\s*</div>\s*<div class="trend-charts-container">',
                f'<div class="overview-stats">\n        <div class="stat-item">\n          <span class="stat-number">{sum(len(v) for v in trend_summary.values())}</span>\n          <span class="stat-label">分段周期呈现上升趋势</span>\n        </div>\n        <div class="stat-item">\n          <span class="stat-number">{len(demand_types)}</span>\n          <span class="stat-label">涉及分类（{"、".join(demand_types)}）</span>\n        </div>\n        <div class="stat-item highlight">\n          <span class="stat-number">{max_change}</span>\n          <span class="stat-label">最大变化幅度</span>\n        </div>\n        <div class="stat-item">\n          <span class="stat-number">{len(time_series) if time_series else 13}</span>\n          <span class="stat-label">月时间序列数据点</span>\n        </div>\n      </div>\n    </div>\n    \n    <!-- 周期趋势分析结论 -->\n    <div class="trend-conclusion-card" style="background: linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%); border-left: 4px solid #0284c7; padding: 20px; margin: 20px 0; border-radius: 8px;">\n      <h3 class="conclusion-title" style="color: #0c4a6e; margin-bottom: 12px; font-size: 18px;">🎯 周期趋势分析结论</h3>\n      <ul class="conclusion-list" style="list-style: none; padding: 0; margin: 0;">\n        <li style="padding: 8px 0; border-bottom: 1px solid #e0f2fe; color: #1e40af;">{formatted_trend_conclusion}</li>\n      </ul>\n    </div>\n    \n    <div class="trend-charts-container">',
                html,
                flags=re.DOTALL
            )
    
    # 9. 修改"待改进领域（BOTTOM3）"TAB页 - 完全重写整个TAB页内容
    bottom3_sections_html = generate_bottom3_sections_html(data, demand_types)
    
    # 生成完整的待改进领域TAB页HTML
    new_tab4_html = f'''    <!-- TAB 4: 待改进领域（优化版） -->
  <section id="tab4" class="tab-panel">
    <div class="panel-header">
      <h2 class="panel-title">待改进领域（BOTTOM3）</h2>
      <p class="panel-desc">针对需求交付瓶颈诊断和周期趋势分析中识别的异常周期，深入识别各需求类型中表现最差的3个领域</p>
    </div>

    <div class="analysis-rule-card">
      <h3 class="rule-title">📐 分析规则</h3>
      <div class="rule-content">
        <p>针对"<strong>需求交付瓶颈诊断-关键</strong>"和"<strong>周期趋势分析-关键</strong>"中识别到的<strong>各需求类型</strong>中有异常的研发活动，识别表现最差的3个领域，从而实现对异常周期进行更深层的短板领域识别。</p>
      </div>
    </div>

{bottom3_sections_html}
  </section>'''
    
    # 替换整个TAB 4部分
    html = re.sub(
        r'<!-- TAB 4:.*?</section>\s*<!-- TAB 5:',
        new_tab4_html + '\n    <!-- TAB 5:',
        html,
        flags=re.DOTALL
    )
    
    # 10. 修改"上升趋势周期汇总"表格
    trend_summary_table_html = generate_trend_summary_table_html(trend_summary, demand_types)
    
    html = re.sub(
        r'<table class="trend-summary-table">.*?</table>',
        f'<table class="trend-summary-table">\n        <thead>\n          <tr>\n            <th>需求类型</th>\n            <th>上升趋势周期</th>\n            <th>均值</th>\n            <th>中位数</th>\n            <th>变化幅度</th>\n            <th>趋势斜率</th>\n            <th>标准差</th>\n            <th>数据点数</th>\n          </tr>\n        </thead>\n{trend_summary_table_html}\n      </table>',
        html,
        flags=re.DOTALL,
        count=1
    )
    
    # 11. 修改异常需求详单部分
    abnormal_sections = generate_abnormal_sections(demand_types, abnormal_data)
    
    html = re.sub(
        r'<!-- TAB 5: 异常需求详单 -->\s*<section id="tab5" class="tab-panel">.*?</section>\s*</main>',
        f'<!-- TAB 5: 异常需求详单 -->\n    <section id="tab5" class="tab-panel">\n    <div class="panel-header">\n      <h2 class="panel-title">异常需求详单</h2>\n      <p class="panel-desc">针对需求交付瓶颈诊断和周期趋势分析中识别的异常周期，定位具体的异常需求单据</p>\n    </div>\n{abnormal_sections}\n  </section>\n  </main>',
        html,
        flags=re.DOTALL
    )
    
    return html


def generate_bottom3_sections_html(data, demand_types):
    """
    生成"待改进领域（BOTTOM3）"的HTML章节（每个需求类型一张表，按周期类型分组）
    
    Args:
        data: iCenter数据
        demand_types: 需求类型列表
    
    Returns:
        str: HTML章节
    """
    sections = []
    
    # 获取周期顺序（用于排序）
    if demand_types:
        first_cycles = extract_cycle_data(data, demand_types[0])
        ordered_cycles = get_cycle_order(list(first_cycles.keys()))
    else:
        ordered_cycles = []
    
    for demand_type in demand_types:
        # 从分析结论4中提取该需求类型的所有BOTTOM3数据
        analysis_result = data.get('分析结果', {})
        analysis_conclusion4 = analysis_result.get('分析结论4', {})
        
        # 收集该需求类型的所有周期类型及其BOTTOM3领域
        cycle_bottom3_data = {}
        for key, value in analysis_conclusion4.items():
            # 键名格式："{需求类型}_{周期类型}"，如"一站式_PR分析周期"
            if key.startswith(demand_type + '_'):
                cycle_type = key.replace(demand_type + '_', '')
                if isinstance(value, dict):
                    analysis_reason = value.get('分析原因', '')
                    bottom3_list = value.get('待改进的BOTTOM3领域', [])
                    if isinstance(bottom3_list, list) and bottom3_list:
                        cycle_bottom3_data[cycle_type] = {
                            'reason': analysis_reason,
                            'bottom3': bottom3_list
                        }
        
        if not cycle_bottom3_data:
            # 如果没有BOTTOM3数据，显示空表或提示
            sections.append(f"""    <!-- {demand_type} -->
    <div class="demand-type-section">
      <h3 class="demand-type-title">{demand_type} - 待改进领域</h3>
      <div class="bottom3-table-container">
        <table class="bottom3-table">
          <thead>
            <tr>
              <th>周期类型</th>
              <th>领域</th>
              <th>均值（天）</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td colspan="3" class="no-data-cell">无待改进领域数据</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>""")
        else:
            # 有BOTTOM3数据，生成表格行
            rows = []
            
            # 按ordered_cycles的顺序遍历周期类型
            for cycle_type in ordered_cycles:
                if cycle_type not in cycle_bottom3_data:
                    continue
                
                cycle_info = cycle_bottom3_data[cycle_type]
                bottom3_list = cycle_info['bottom3']
                analysis_reason = cycle_info['reason']
                
                # 根据实际的BOTTOM3领域数量设置rowspan
                num_bottom3 = len(bottom3_list)
                
                # 第一个BOTTOM3领域需要显示周期类型名称（跨n行，n为BOTTOM3数量）
                for i, bottom3_item in enumerate(bottom3_list):
                    if isinstance(bottom3_item, dict):
                        field_name = bottom3_item.get('领域', '无')
                        mean_value = bottom3_item.get('均值', 0)
                        
                        if i == 0:
                            # 第一个领域：添加周期类型单元格（跨n行，n为实际BOTTOM3数量）
                            rows.append(f"""            <tr class="cycle-row">
              <td rowspan="{num_bottom3}" class="cycle-name">{cycle_type}<br><span class="cycle-reason">{analysis_reason}</span></td>
              <td>{field_name}</td>
              <td class="mean-value">{mean_value:.2f}</td>
            </tr>
""")
                        else:
                            # 后续领域：只显示领域和均值
                            rows.append(f"""            <tr>
              <td>{field_name}</td>
              <td class="mean-value">{mean_value:.2f}</td>
            </tr>
""")
            
            sections.append(f"""    <!-- {demand_type} -->
    <div class="demand-type-section">
      <h3 class="demand-type-title">{demand_type} - 待改进领域</h3>
      <div class="bottom3-table-container">
        <table class="bottom3-table">
          <thead>
            <tr>
              <th>周期类型</th>
              <th>领域</th>
              <th>均值（天）</th>
            </tr>
          </thead>
          <tbody>
{''.join(rows)}
          </tbody>
        </table>
      </div>
    </div>""")
    
    if not sections:
        return """    <div class="no-data-card">
      <p class="no-data-text">无待改进领域数据</p>
    </div>"""
    
    return '\\n'.join(sections)


def generate_trend_summary_table_html(trend_summary, demand_types):
    """
    生成"上升趋势周期汇总"表格的HTML
    
    Args:
        trend_summary: 趋势分析汇总字典
        demand_types: 需求类型列表
    
    Returns:
        str: 表格HTML
    """
    rows = []
    
    for demand_type in demand_types:
        rising_cycles = trend_summary.get(demand_type, [])
        
        if not rising_cycles:
            continue
        
        # 第一个周期需要显示需求类型名称（跨行）
        for i, cycle in enumerate(rising_cycles):
            if i == 0:
                # 第一个周期：添加需求类型单元格（跨行）
                # 处理change字段：可能是字符串"+111.43%"或浮点数111.43
                change_val = cycle['change']
                if isinstance(change_val, (int, float)):
                    change_str = f"+{change_val:.2f}%"
                else:
                    change_str = change_val if change_val.startswith('+') else f"+{change_val}"
                
                rows.append(f'''          <tr>
            <td rowspan="{len(rising_cycles)}" class="demand-type-cell"><strong>{demand_type}</strong></td>
            <td class="cycle-name-cell">{cycle['cycle_name']}</td>
            <td class="value-cell">{cycle['mean']:.2f}天</td>
            <td class="value-cell">{cycle['median']:.2f}天</td>
            <td class="increase-cell">{change_str}</td>
            <td class="value-cell">{cycle['slope']:.2f}</td>
            <td class="value-cell">{cycle['std']:.2f}</td>
            <td class="count-cell">{int(cycle['count'])}</td>
          </tr>''')
            else:
                # 后续周期：只显示周期名称和数据
                change_val = cycle['change']
                if isinstance(change_val, (int, float)):
                    change_str = f"+{change_val:.2f}%"
                else:
                    change_str = change_val if change_val.startswith('+') else f"+{change_val}"
                
                rows.append(f'''          <tr>
            <td class="cycle-name-cell">{cycle['cycle_name']}</td>
            <td class="value-cell">{cycle['mean']:.2f}天</td>
            <td class="value-cell">{cycle['median']:.2f}天</td>
            <td class="increase-cell">{change_str}</td>
            <td class="value-cell">{cycle['slope']:.2f}</td>
            <td class="value-cell">{cycle['std']:.2f}</td>
            <td class="count-cell">{int(cycle['count'])}</td>
          </tr>''')
    
    if not rows:
        return '''        <tbody>
          <tr>
            <td colspan="8" class="no-data-cell">✅ 无上升趋势周期</td>
          </tr>
        </tbody>'''
    
    return '<tbody>\n' + '\n'.join(rows) + '\n        </tbody>'


def generate_abnormal_sections(demand_types, abnormal_data):
    """生成异常需求详单的HTML部分"""
    sections = []
    
    for demand_type in demand_types:
        abnormal_by_cycle = abnormal_data.get(demand_type, {})
        
        if not abnormal_by_cycle:
            continue
        
        # 按周期类型分组生成表格
        rows = []
        for cycle_type, abnormal_info in sorted(abnormal_by_cycle.items()):
            reason = abnormal_info.get('reason', '异常周期')
            cycle_field_name = abnormal_info.get('cycle_field_name', cycle_type)
            requirements = abnormal_info.get('requirements', [])
            
            # 即使requirements为空，也要显示该周期类型（显示"无异常需求"）
            if not requirements:
                # 异常需求列表为空，显示"无异常需求"行
                rows.append(f'''            <tr class="req-row no-data-row">
              <td rowspan="1" class="cycle-type-cell">
                <span class="cycle-name">{cycle_type}</span>
                <span class="cycle-reason">{reason}</span>
              </td>
              <td colspan="3" class="no-data-cell">✅ 该周期类型无异常需求</td>
            </tr>''')
            else:
                # 有异常需求，正常显示
                # 每个需求只取第一个（原因相同）
                first_req = requirements[0]
                # 注意：API返回的数据中，'标识'字段实际存储的是需求标题（长文本），'标题'字段存储的是需求标识（短文本）
                # 因此需要交换取值，确保req_id是短文本（需求标识），req_title是长文本（需求标题）
                req_title_temp = first_req.get('标识', '')  # 实际是长文本（需求标题）
                req_id_temp = first_req.get('标题', '')     # 实际是短文本（需求标识）
                # 交换赋值
                req_id = req_id_temp if req_id_temp else req_title_temp  # 优先使用短文本作为需求标识
                req_title = req_title_temp if req_title_temp else req_id_temp  # 优先使用长文本作为需求标题
                # 如果两个字段都有值且长度明显不同，根据长度判断
                if req_id_temp and req_title_temp and len(req_id_temp) != len(req_title_temp):
                    if len(req_id_temp) > len(req_title_temp):
                        # req_id_temp更长，应该是需求标题
                        req_title = req_id_temp
                        req_id = req_title_temp
                    else:
                        # req_title_temp更长，应该是需求标题
                        req_id = req_id_temp
                        req_title = req_title_temp
                # 使用正确的周期字段名提取周期值（如"MR分析周期"字段）
                req_cycle = first_req.get(cycle_field_name, first_req.get('周期', 0))
                
                rows.append(f'''            <tr class="req-row">
              <td rowspan="{len(requirements)}" class="cycle-type-cell">
                <span class="cycle-name">{cycle_type}</span>
                <span class="cycle-reason">{reason}</span>
              </td>
              <td class="req-id">{req_id}</td>
              <td class="req-title">{req_title}</td>
              <td class="req-cycle">{req_cycle}</td>
            </tr>''')
                
                # 同一周期类型的其他需求
                for req in requirements[1:]:
                    # 注意：API返回的数据中，'标识'字段实际存储的是需求标题（长文本），'标题'字段存储的是需求标识（短文本）
                    # 因此需要交换取值，确保req_id是短文本（需求标识），req_title是长文本（需求标题）
                    req_title_temp = req.get('标识', '')  # 实际是长文本（需求标题）
                    req_id_temp = req.get('标题', '')     # 实际是短文本（需求标识）
                    # 交换赋值
                    req_id = req_id_temp if req_id_temp else req_title_temp
                    req_title = req_title_temp if req_title_temp else req_id_temp
                    # 如果两个字段都有值且长度明显不同，根据长度判断
                    if req_id_temp and req_title_temp and len(req_id_temp) != len(req_title_temp):
                        if len(req_id_temp) > len(req_title_temp):
                            # req_id_temp更长，应该是需求标题
                            req_title = req_id_temp
                            req_id = req_title_temp
                        else:
                            # req_title_temp更长，应该是需求标题
                            req_id = req_id_temp
                            req_title = req_title_temp
                    # 使用正确的周期字段名提取周期值
                    req_cycle = req.get(cycle_field_name, req.get('周期', 0))
                    rows.append(f'''            <tr class="req-row">
              <td class="req-id">{req_id}</td>
              <td class="req-title">{req_title}</td>
              <td class="req-cycle">{req_cycle}</td>
            </tr>''')
        
        if rows:
            sections.append(f'''    <!-- {demand_type} -->
    <div class="demand-type-section">
      <h3 class="demand-type-title">📊 {demand_type} - 异常需求详单</h3>
      <div class="single-table-container">
        <table class="single-requirements-table">
          <thead>
            <tr>
              <th>周期类型</th>
              <th>需求标识</th>
              <th>需求标题</th>
              <th>周期天数</th>
            </tr>
          </thead>
          <tbody>
{''.join(rows)}
          </tbody>
        </table>
      </div>
    </div>''')
    
    if not sections:
        return '''    <div class="no-data-card">
      <p class="no-data-text">✅ 所有需求类型均无异常需求</p>
    </div>'''
    
    return '\n'.join(sections)


def modify_js_template(js_template, demand_types, cycle_comparison_js, trend_charts_js, legend_html, ordered_cycles, time_series):
    """
    修改JavaScript模板，替换图表数据
    
    策略：完全重写initCycleComparisonChart函数和initTrendCharts函数以支持任意数量的需求类型
    并且使用实际的时间序列数据
    """
    js_content = js_template
    
    # 1. 生成新的initCycleComparisonChart函数，支持任意数量的需求类型
    new_init_cycle_comparison_chart = generate_dynamic_cycle_comparison_function(
        demand_types, cycle_comparison_js, legend_html
    )
    
    # 替换整个initCycleComparisonChart函数
    js_content = re.sub(
        r"function initCycleComparisonChart\(\) \{[\s\S]*?^}",
        new_init_cycle_comparison_chart,
        js_content,
        flags=re.MULTILINE
    )
    
    # 2. 生成新的initTrendCharts函数，支持任意数量的需求类型和实际时间序列
    new_init_trend_charts = generate_dynamic_trend_charts_function(
        demand_types, trend_charts_js, time_series
    )
    
    # 替换整个initTrendCharts函数
    js_content = re.sub(
        r"function initTrendCharts\(\) \{[\s\S]*?^}",
        new_init_trend_charts,
        js_content,
        flags=re.MULTILINE
    )
    
    # 3. 修改drawTrendChart函数中的月份数据
    if time_series:
        # 将硬编码的月份替换为实际的时间序列
        months_array_str = str(time_series).replace("'", "'")
        js_content = re.sub(
            r"const months = \[.*?\];",
            f"const months = {months_array_str};",
            js_content,
            flags=re.DOTALL
        )
    
    return js_content


def generate_dynamic_trend_charts_function(demand_types, trend_charts_js, time_series):
    """
    生成支持任意数量需求类型的initTrendCharts函数
    
    Args:
        demand_types: 需求类型列表
        trend_charts_js: 生成的趋势图JavaScript代码
        time_series: 实际的时间序列数据
    
    Returns:
        str: 完整的函数代码
    """
    # 提取所有趋势数据
    trend_data_calls = []
    
    for demand_type in demand_types:
        var_name = sanitize_variable_name(demand_type)
        match = re.search(rf"const {var_name}TrendData = (\{{.*?\}});", trend_charts_js, flags=re.DOTALL)
        if match:
            series_str = match.group(1)
            canvas_id = f'{var_name}TrendChart'
            legend_id = f'{var_name}Legend'
            trend_data_calls.append(f"  drawTrendChart('{canvas_id}', '{legend_id}', {series_str});")
    
    # 生成函数代码
    function_code = '''function initTrendCharts() {
  // 动态绘制所有需求类型的趋势图
''' + '\n'.join(trend_data_calls) + '''
}'''
    
    return function_code


def generate_dynamic_cycle_comparison_function(demand_types, cycle_comparison_js, legend_html):
    """
    生成支持任意数量需求类型的initCycleComparisonChart函数
    
    Args:
        demand_types: 需求类型列表
        cycle_comparison_js: 生成的活动周期对比图JavaScript代码
        legend_html: 图例HTML
    
    Returns:
        str: 完整的函数代码
    """
    # 解析生成的JavaScript数据
    activities_match = re.search(r"const activities = (\[.*?\]);", cycle_comparison_js)
    activities = activities_match.group(1) if activities_match else "[]"
    
    # 提取所有数据数组
    data_arrays = []
    colors = []
    predefined_colors = [
        '#2563eb', '#7c3aed', '#059669', '#dc2626', '#ea580c',
        '#0891b2', '#db2777', '#4f46e5', '#0ea5e9', '#65a30d'
    ]
    
    for i, demand_type in enumerate(demand_types):
        var_name = sanitize_variable_name(demand_type)
        match = re.search(rf"const {var_name}Data = (\[.*?\]);", cycle_comparison_js)
        if match:
            data_arrays.append(match.group(1))
            colors.append(predefined_colors[i % len(predefined_colors)])
    
    # 生成函数代码
    function_code = '''function initCycleComparisonChart() {
  const canvas = document.getElementById('cycleComparisonChart');
  if (!canvas) return;
  
  const ctx = canvas.getContext('2d');
  const container = canvas.parentElement;
  const rect = container.getBoundingClientRect();
  canvas.width = rect.width - 40;
  canvas.height = 400;
  
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  
  // 动态设置活动名称和数据
  const activities = ''' + activities + ''';
  
'''
    
    # 添加数据数组和颜色
    for i, (demand_type, data_array) in enumerate(zip(demand_types, data_arrays)):
        var_name = sanitize_variable_name(demand_type)
        function_code += f"  const {var_name}Data = {data_array};\n"
    
    function_code += '''
  const margin = { top: 40, right: 30, bottom: 60, left: 60 };
  const chartWidth = canvas.width - margin.left - margin.right;
  const chartHeight = canvas.height - margin.top - margin.bottom;
  
  // 收集所有数据找最大值
  let maxValue = 0;
'''
    
    # 添加最大值计算
    for i, demand_type in enumerate(demand_types):
        var_name = sanitize_variable_name(demand_type)
        function_code += f"  maxValue = Math.max(maxValue, ...{var_name}Data);\n"
    
    function_code += '''
  const xStep = chartWidth / (activities.length - 1);
  const yScale = chartHeight / maxValue;
  
  // 绘制网格线
  ctx.strokeStyle = '#e2e8f0';
  ctx.lineWidth = 1;
  ctx.setLineDash([5, 5]);
  
  const yTicks = 5;
  for (let i = 0; i <= yTicks; i++) {
    const y = margin.top + (chartHeight / yTicks) * i;
    ctx.beginPath();
    ctx.moveTo(margin.left, y);
    ctx.lineTo(margin.left + chartWidth, y);
    ctx.stroke();
    
    const value = Math.round(maxValue - (maxValue / yTicks) * i);
    ctx.fillStyle = '#64748b';
    ctx.font = '12px sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText(value.toString(), margin.left - 10, y + 4);
  }
  
  ctx.setLineDash([]);
  
  // 绘制X轴标签
  ctx.fillStyle = '#475569';
  ctx.font = '12px sans-serif';
  ctx.textAlign = 'center';
  activities.forEach((activity, index) => {
    const x = margin.left + index * xStep;
    ctx.fillText(activity, x, margin.top + chartHeight + 20);
  });
  
  // 绘制每条折线
  function drawLine(data, color, pointRadius) {
    ctx.strokeStyle = color;
    ctx.lineWidth = 3;
    ctx.setLineDash([]);
    
    ctx.beginPath();
    data.forEach((value, index) => {
      const x = margin.left + index * xStep;
      const y = margin.top + chartHeight - value * yScale;
      
      if (index === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
    });
    ctx.stroke();
    
    data.forEach((value, index) => {
      const x = margin.left + index * xStep;
      const y = margin.top + chartHeight - value * yScale;
      
      ctx.beginPath();
      ctx.arc(x, y, pointRadius, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 2;
      ctx.stroke();
      
      ctx.fillStyle = color;
      ctx.font = 'bold 11px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(value.toFixed(1), x, y - 12);
    });
  }
  
'''
    
    # 添加绘制调用
    for i, demand_type in enumerate(demand_types):
        var_name = sanitize_variable_name(demand_type)
        function_code += f"  drawLine({var_name}Data, '{colors[i]}', 5);\n"
    
    # 添加图例
    function_code += '''
  // 动态生成图例
  const legendContainer = document.getElementById('chartLegend1');
  if (legendContainer) {
    legendContainer.innerHTML = `
'''
    
    for i, demand_type in enumerate(demand_types):
        function_code += f"      <div class=\"legend-item\">\n        <div class=\"legend-color\" style=\"background: {colors[i]}\"></div>\n        <span>{demand_type}</span>\n      </div>\n"
    
    function_code += '''    `;
  }
  
  ctx.fillStyle = '#1e293b';
  ctx.font = 'bold 14px sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText('各需求类型活动周期对比（单位：天）', canvas.width / 2, 20);
}'''
    
    return function_code


# ============================================================================
# 主函数
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description='IMAP端到端需求交付分析报告生成器（重构优化版 - API接口调用）')
    parser.add_argument('--data-source-url', required=True, help='数据源URL（如：https://aip.zte.com.cn/middle/api/remote/call/xxx?projectGroup_id=14&beginTime=2026-01-01&endTime=2026-07-01）')
    parser.add_argument('--column-name-source', required=True, help='列名来源（如："PR交付周期"、"MR交付周期"）')
    parser.add_argument('--section-cycle-name', nargs='+', required=True, help='分段周期名称列表（如："PR分析等待周期" "PR分析周期" "PR开发等待周期" ...）')
    parser.add_argument('--category-column', required=True, help='分类列名（如："分类1"）')
    parser.add_argument('--direction', required=True, help='方向（如："向低"）')
    parser.add_argument('--indicator-type', required=True, help='指标类型（如："周期类"）')
    parser.add_argument('--html-template', required=True, help='HTML模板文件路径')
    parser.add_argument('--css-template', required=True, help='CSS模板文件路径')
    parser.add_argument('--js-template', required=True, help='JavaScript模板文件路径')
    parser.add_argument('--output-dir', default='/workspace', help='输出目录（默认：/workspace）')
    parser.add_argument('--output-html', help='输出HTML文件名（默认：imap_report.html）')
    parser.add_argument('--output-css', help='输出CSS文件名（默认：imap_style.css）')
    parser.add_argument('--output-js', help='输出JavaScript文件名（默认：imap_script.js）')
    
    args = parser.parse_args()
    
    # ========== 新增：生成带日期时间戳的文件名（先定义默认值） ==========
    # 使用报告名称+时间戳（带时分秒）作为文件名，避免覆盖旧报告
    current_datetime = datetime.now().strftime("%Y%m%d_%H%M%S")  # 格式：20260814_093045
    
    # 清理报告标题中的非法字符，用于文件名（先用默认名称，后面再从数据中提取实际名称）
    safe_project_name = 'imap_report'
    
    # 生成带时间戳的默认文件名
    output_html_name = f"{safe_project_name}_{current_datetime}.html"
    output_css_name = f"{safe_project_name}_{current_datetime}_style.css"
    output_js_name = f"{safe_project_name}_{current_datetime}_script.js"
    # ===============================================
    
    # 设置输出路径
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 如果用户通过命令行参数指定了文件名，则使用用户指定的；否则使用默认文件名（后面会根据实际报告名称更新）
    output_html = output_dir / (args.output_html or output_html_name)
    output_css = output_dir / (args.output_css or output_css_name)
    output_js = output_dir / (args.output_js or output_js_name)
    
    # 调用API获取数据
    print("🌐 正在调用API接口获取IMAP端到端需求交付分析数据...")
    data = call_imap_analysis_api(
        data_source_url=args.data_source_url,
        column_name_source=args.column_name_source,
        section_cycle_name=args.section_cycle_name,
        category_column=args.category_column,
        direction=args.direction,
        indicator_type=args.indicator_type
    )
    
    if not data:
        print("❌ 无法从API获取数据")
        return
    print("✅ 成功从API获取数据")
    
    # 提取基本信息
    report_project = extract_report_title(data)
    time_range = extract_data_time_range(data)
    demand_types = extract_demand_types(data)
    
    print(f"📝 报告标题：{report_project}需求交付分析报告")
    if time_range:
        print(f"📅 数据时间范围：{time_range}")
    print(f"📊 识别到{len(demand_types)}种需求类型: {', '.join(demand_types)}")
    
    # ========== 更新：使用实际提取的报告名称重新生成文件名 ==========
    # 清理报告标题中的非法字符，用于文件名
    # 只保留中文、英文、数字、下划线、连字符
    safe_project_name = re.sub(r'[^\w\u4e00-\u9fff\-_]', '', report_project)
    if not safe_project_name or safe_project_name.strip() == '':
        safe_project_name = 'imap_report'
    
    # 重新生成带时间戳的文件名
    output_html_name = f"{safe_project_name}_{current_datetime}.html"
    output_css_name = f"{safe_project_name}_{current_datetime}_style.css"
    output_js_name = f"{safe_project_name}_{current_datetime}_script.js"
    
    # 如果用户没有通过命令行参数指定文件名，则使用新生成的文件名
    if not args.output_html:
        output_html = output_dir / output_html_name
    if not args.output_css:
        output_css = output_dir / output_css_name
    if not args.output_js:
        output_js = output_dir / output_js_name
    
    print(f"📁 输出文件名:")
    print(f"   - HTML: {output_html}")
    print(f"   - CSS: {output_css}")
    print(f"   - JS: {output_js}")
    # ===============================================
    
    # 获取周期顺序
    if demand_types:
        first_cycles = extract_cycle_data(data, demand_types[0])
        ordered_cycles = get_cycle_order(list(first_cycles.keys()))
        print(f"📈 识别到{len(ordered_cycles)}个活动周期: {', '.join(ordered_cycles)}")
    
    # 读取模板
    print("\n📖 正在读取模板文件...")
    html_template = read_template(args.html_template)
    css_template = read_template(args.css_template)
    js_template = read_template(args.js_template)
    
    if not all([html_template, css_template, js_template]):
        print("❌ 无法读取模板文件")
        return
    print("✅ 成功读取所有模板文件")
    
    # 提取数据
    print("\n🔧 正在提取数据...")
    key_findings = generate_key_findings_table(data, demand_types)
    equity_table = generate_equity_table(data, demand_types)
    
    # 获取周期顺序（用于趋势分析）
    if demand_types:
        first_cycles = extract_cycle_data(data, demand_types[0])
        ordered_cycles = get_cycle_order(list(first_cycles.keys()))
        print(f"📈 活动周期顺序: {', '.join(ordered_cycles)}")
    else:
        ordered_cycles = []
    
    trend_summary = generate_trend_summary(data, demand_types, ordered_cycles)
    
    # bottom3_data不再需要，因为generate_bottom3_table_html直接从data中提取
    # bottom3_data = {dt: extract_bottom3_for_demand_type(data, dt) for dt in demand_types}
    abnormal_data = {dt: extract_abnormal_requirements(data, dt) for dt in demand_types}
    
    cycle_data_for_chart = {dt: extract_cycle_data(data, dt) for dt in demand_types}
    trend_chart_data = {dt: extract_trend_data(data, dt) for dt in demand_types}
    
    equity_conclusion = extract_equity_conclusion(data)
    trend_conclusion = extract_trend_conclusion(data)  # 新增：提取周期趋势分析结论
    
    # 提取时间序列（用于图表横轴）
    time_series = []
    if demand_types:
        time_series = extract_time_series_range(data, demand_types[0])
        print(f"📅 提取到时间序列：{time_series[0] if time_series else 'N/A'} ~ {time_series[-1] if time_series and len(time_series) > 0 else 'N/A'}，共{len(time_series)}个月")
    
    print(f"✅ 提取到{len(key_findings)}条关键发现")
    print(f"✅ 提取到{len(equity_table)}个活动周期均值")
    if equity_conclusion:
        print(f"✅ 提取到均衡性分析结论: {equity_conclusion[:50]}...")
    if trend_conclusion:
        print(f"✅ 提取到周期趋势分析结论: {trend_conclusion[:50]}...")
    
    # 生成JavaScript数据
    print("\n🔧 正在生成JavaScript图表数据...")
    cycle_comparison_js, trend_charts_js, legend_html, time_series_from_data = generate_js_data(
        data, demand_types, cycle_data_for_chart, trend_chart_data, ordered_cycles
    )
    
    # 如果time_series_from_data为空，使用之前提取的time_series
    if not time_series_from_data and time_series:
        time_series_from_data = time_series
    
    # 修改模板
    print("🔧 正在修改HTML模板...")
    html_content = modify_html_template(
        html_template, data, demand_types, key_findings, equity_table,
        trend_summary, None, abnormal_data, cycle_data_for_chart,
        trend_chart_data, equity_conclusion, trend_conclusion, time_series_from_data,
        output_css_name=output_css.name,
        output_js_name=output_js.name
    )
    
    print("🔧 正在修改JavaScript模板...")
    js_content = modify_js_template(
        js_template, demand_types, cycle_comparison_js, trend_charts_js,
        legend_html, ordered_cycles, time_series_from_data
    )
    
    # 保存文件
    print(f"\n💾 正在保存文件到 {output_dir}...")
    with open(output_html, 'w', encoding='utf-8') as f:
        f.write(html_content)
    with open(output_css, 'w', encoding='utf-8') as f:
        f.write(css_template)
    with open(output_js, 'w', encoding='utf-8') as f:
        f.write(js_content)
    
    print(f"\n✅ IMAP端到端需求交付分析报告生成成功！")
    print(f"\n📁 生成文件:")
    print(f"   - HTML报告: {output_html}")
    print(f"   - CSS样式: {output_css}")
    print(f"   - JavaScript: {output_js}")


if __name__ == '__main__':
    main()
