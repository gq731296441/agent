#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
工作量数据监控技能 - 工具函数（已修复：需求交付判断基于研发活动类型）
"""

import re
from datetime import datetime, date
from typing import Tuple, Dict, List, Any
import os
import subprocess
import json
import sys

from skill_paths import (
    SKILL_OA_ZMAIL,
    SKILL_STUDIO_RDCLOUD,
    resolve_skill_dir,
)


def _safe_float(value, default=0.0):
    """安全转换为浮点数；转换失败或空值返回默认值"""
    if value is None or value == '':
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def parse_month(month_str: str) -> Tuple[str, str]:
    """
    解析月份字符串，返回起始日期和结束日期
    支持格式：'5月份'、'2026-05'、'2026年5月'、'2026-05-01'等
    返回：(start_date, end_date) 格式：'2026-05-01', '2026-06-01'
    """
    now = datetime.now()
    year = now.year
    month = None

    # 提取年份（匹配独立的4位数字，避免匹配到月份或日期）
    year_match = re.search(r'(?<!\d)(\d{4})(?!\d)', month_str)
    if year_match:
        year = int(year_match.group(1))
        # 移除年份，避免后续重复匹配
        month_str = month_str.replace(year_match.group(1), '')

    # 提取月份（1-2位数字）
    month_match = re.search(r'(?<!\d)(\d{1,2})(?!\d)', month_str)
    if month_match:
        month = int(month_match.group(1))
    else:
        # 默认当月
        month = now.month

    # 边界处理
    if month < 1 or month > 12:
        month = now.month

    # 构建日期范围
    start_date = f"{year}-{month:02d}-01"
    if month == 12:
        end_date = f"{year+1}-01-01"
    else:
        end_date = f"{year}-{month+1:02d}-01"

    return start_date, end_date


def analyze_task_counts(work_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    分析团队任务数统计（清洗团队名称）
    返回：包含表1和表2数据的字典
    """
    team_stats = {}

    for item in work_items:
        team = item.get('team', '')
        # 清洗无效团队名
        invalid_teams = {'', 'Unknown', 'closed', 'Closed', '2', '0', 'null', 'NULL', 'None', 'none'}
        if not str(team).strip() or team in invalid_teams or str(team).strip().isdigit():
            team = '未分类团队'
        if team not in team_stats:
            team_stats[team] = {
                'plan_total': 0,
                'actual_total': 0,
                'plan_delivery': 0,
                'plan_non_delivery': 0,
                'actual_delivery': 0,
                'actual_non_delivery': 0
            }

        stats = team_stats[team]

        # 计划任务数（每条约为1）
        stats['plan_total'] += 1

        # 判断是否需求交付：基于 研发活动类型 字段（包含"需求交付"即视为需求交付）
        activity_type = str(item.get('研发活动类型', ''))
        is_delivery = '需求交付' in activity_type

        if is_delivery:
            stats['plan_delivery'] += 1
        else:
            stats['plan_non_delivery'] += 1

        # 实际完成任务数（基于状态）
        if item.get('state', '') in ['Closed', '已完成', '已关闭']:
            stats['actual_total'] += 1
            if is_delivery:
                stats['actual_delivery'] += 1
            else:
                stats['actual_non_delivery'] += 1

    # 计算总计
    totals = {
        'plan_total': sum(v['plan_total'] for v in team_stats.values()),
        'actual_total': sum(v['actual_total'] for v in team_stats.values()),
        'plan_delivery': sum(v['plan_delivery'] for v in team_stats.values()),
        'plan_non_delivery': sum(v['plan_non_delivery'] for v in team_stats.values()),
        'actual_delivery': sum(v['actual_delivery'] for v in team_stats.values()),
        'actual_non_delivery': sum(v['actual_non_delivery'] for v in team_stats.values())
    }

    # 构建表1数据
    table1 = []
    for team in sorted(team_stats.keys()):
        stats = team_stats[team]
        table1.append({
            '团队名称': team,
            '计划总任务数': stats['plan_total'],
            '实际完成总任务数': stats['actual_total'],
            '需求交付计划任务数': stats['plan_delivery'],
            '非需求交付计划任务数': stats['plan_non_delivery'],
            '需求交付实际任务数': stats['actual_delivery'],
            '非需求交付实际任务数': stats['actual_non_delivery']
        })

    # 添加总计行
    table1.append({
        '团队名称': '**总计**',
        '计划总任务数': totals['plan_total'],
        '实际完成总任务数': totals['actual_total'],
        '需求交付计划任务数': totals['plan_delivery'],
        '非需求交付计划任务数': totals['plan_non_delivery'],
        '需求交付实际任务数': totals['actual_delivery'],
        '非需求交付实际任务数': totals['actual_non_delivery']
    })

    # 构建表2数据（占比）
    table2 = []
    for team in sorted(team_stats.keys()):
        stats = team_stats[team]
        table2.append({
            '团队名称': team,
            '团队完成任务数比例': f"{(stats['actual_total']/stats['plan_total']*100):.2f}%" if stats['plan_total'] > 0 else "0.00%",
            '需求交付计划任务数占比': f"{(stats['plan_delivery']/stats['plan_total']*100):.2f}%" if stats['plan_total'] > 0 else "0.00%",
            '非需求交付计划任务数占比': f"{(stats['plan_non_delivery']/stats['plan_total']*100):.2f}%" if stats['plan_total'] > 0 else "0.00%",
            '需求交付实际任务数占比': f"{(stats['actual_delivery']/stats['actual_total']*100):.2f}%" if stats['actual_total'] > 0 else "0.00%",
            '非需求交付实际任务数占比': f"{(stats['actual_non_delivery']/stats['actual_total']*100):.2f}%" if stats['actual_total'] > 0 else "0.00%"
        })

    return {
        'table1': table1,
        'table2': table2,
        'totals': totals,
        'valid_records': len(work_items),
        'team_count': len(team_stats)
    }


def analyze_task_workload(work_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    分析团队工作量统计（清洗团队名称）
    返回：包含表3和表4数据的字典
    """
    team_stats = {}

    for item in work_items:
        team = item.get('team', '')
        # 清洗无效团队名
        invalid_teams = {'', 'Unknown', 'closed', 'Closed', '2', '0', 'null', 'NULL', 'None', 'none'}
        if not str(team).strip() or team in invalid_teams or str(team).strip().isdigit():
            team = '未分类团队'
        if team not in team_stats:
            team_stats[team] = {
                'plan_total': 0.0,
                'actual_total': 0.0,
                'plan_delivery': 0.0,
                'plan_non_delivery': 0.0,
                'actual_delivery': 0.0,
                'actual_non_delivery': 0.0
            }

        stats = team_stats[team]

        # 获取工作量：使用 预计工作量 作为计划，实际工作量 作为实际
        planned = _safe_float(item.get('预计工作量', item.get('planned_workload')))
        actual = _safe_float(item.get('实际工作量', item.get('actual_workload')))
        stats['plan_total'] += planned

        # 判断是否需求交付：基于 研发活动类型 字段
        activity_type = str(item.get('研发活动类型', ''))
        is_delivery = '需求交付' in activity_type

        if is_delivery:
            stats['plan_delivery'] += planned
        else:
            stats['plan_non_delivery'] += planned

        # 实际完成工作量
        if item.get('state', '') in ['Closed', '已完成', '已关闭']:
            stats['actual_total'] += actual
            if is_delivery:
                stats['actual_delivery'] += actual
            else:
                stats['actual_non_delivery'] += actual

    # 计算总计
    totals = {
        'plan_total': sum(v['plan_total'] for v in team_stats.values()),
        'actual_total': sum(v['actual_total'] for v in team_stats.values()),
        'plan_delivery': sum(v['plan_delivery'] for v in team_stats.values()),
        'plan_non_delivery': sum(v['plan_non_delivery'] for v in team_stats.values()),
        'actual_delivery': sum(v['actual_delivery'] for v in team_stats.values()),
        'actual_non_delivery': sum(v['actual_non_delivery'] for v in team_stats.values())
    }

    # 构建表3数据
    table3 = []
    for team in sorted(team_stats.keys()):
        stats = team_stats[team]
        table3.append({
            '团队名称': team,
            '计划总工作量': round(stats['plan_total'], 1),
            '实际完成总工作量': round(stats['actual_total'], 1),
            '需求交付计划工作量': round(stats['plan_delivery'], 1),
            '非需求交付计划工作量': round(stats['plan_non_delivery'], 1),
            '需求交付实际工作量': round(stats['actual_delivery'], 1),
            '非需求交付实际工作量': round(stats['actual_non_delivery'], 1)
        })

    # 添加总计行
    table3.append({
        '团队名称': '**总计**',
        '计划总工作量': round(totals['plan_total'], 1),
        '实际完成总工作量': round(totals['actual_total'], 1),
        '需求交付计划工作量': round(totals['plan_delivery'], 1),
        '非需求交付计划工作量': round(totals['plan_non_delivery'], 1),
        '需求交付实际工作量': round(totals['actual_delivery'], 1),
        '非需求交付实际工作量': round(totals['actual_non_delivery'], 1)
    })

    # 构建表4数据（占比）
    table4 = []
    for team in sorted(team_stats.keys()):
        stats = team_stats[team]
        table4.append({
            '团队名称': team,
            '需求交付计划工作量占比': f"{(stats['plan_delivery']/stats['plan_total']*100):.2f}%" if stats['plan_total'] > 0 else "0.00%",
            '非需求交付计划工作量占比': f"{(stats['plan_non_delivery']/stats['plan_total']*100):.2f}%" if stats['plan_total'] > 0 else "0.00%",
            '需求交付实际工作量占比': f"{(stats['actual_delivery']/stats['actual_total']*100):.2f}%" if stats['actual_total'] > 0 else "0.00%",
            '非需求交付实际工作量占比': f"{(stats['actual_non_delivery']/stats['actual_total']*100):.2f}%" if stats['actual_total'] > 0 else "0.00%"
        })

    return {
        'table3': table3,
        'table4': table4,
        'totals': totals
    }


def analyze_abnormal_data(work_items: List[Dict[str, Any]],
                          planned_threshold: int = 30,
                          actual_threshold: int = 30,
                          actual_status: str = 'closed') -> Dict[str, Any]:
    """
    分析异常数据（清洗团队名称）
    返回：包含表5和表6数据的字典
    """
    planned_empty_teams = {}
    planned_exceed_teams = {}
    planned_empty_persons = {}
    planned_exceed_persons = {}

    actual_empty_teams = {}
    actual_exceed_teams = {}
    actual_empty_persons = {}
    actual_exceed_persons = {}

    for item in work_items:
        team = item.get('team', '')
        # 清洗无效团队名
        invalid_teams = {'', 'Unknown', 'closed', 'Closed', '2', '0', 'null', 'NULL', 'None', 'none'}
        if not str(team).strip() or team in invalid_teams or str(team).strip().isdigit():
            team = '未分类团队'

        planned = _safe_float(item.get('预计工作量', item.get('planned_workload')))
        actual = _safe_float(item.get('实际工作量', item.get('actual_workload')))
        state = item.get('state', '')
        person = item.get('负责人', '')

        # 预计工作量异常分析
        if planned == 0:
            planned_empty_teams[team] = planned_empty_teams.get(team, 0) + 1
            persons = planned_empty_persons.setdefault(team, set())
            if person:
                persons.add(person)
        elif planned > planned_threshold:
            planned_exceed_teams[team] = planned_exceed_teams.get(team, 0) + 1
            persons = planned_exceed_persons.setdefault(team, set())
            if person:
                persons.add(person)

        # 实际工作量异常分析（仅Closed状态）
        if actual_status.lower() == 'closed':
            if state not in ['Closed', '已完成', '已关闭']:
                continue

        if actual == 0:
            actual_empty_teams[team] = actual_empty_teams.get(team, 0) + 1
            persons = actual_empty_persons.setdefault(team, set())
            if person:
                persons.add(person)
        elif actual > actual_threshold:
            actual_exceed_teams[team] = actual_exceed_teams.get(team, 0) + 1
            persons = actual_exceed_persons.setdefault(team, set())
            if person:
                persons.add(person)

    # 构建表5数据
    table5 = []
    for team in sorted(set(list(planned_empty_teams.keys()) + list(planned_exceed_teams.keys()))):
        empty_count = planned_empty_teams.get(team, 0)
        exceed_count = planned_exceed_teams.get(team, 0)
        if empty_count > 0 or exceed_count > 0:
            persons_set = planned_empty_persons.get(team, set()) | planned_exceed_persons.get(team, set())
            persons_list = sorted(p for p in persons_set if p)
            persons_str = ', '.join(persons_list) if persons_list else ''
            table5.append({
                '团队名称': team,
                '预计工作量为空': empty_count,
                '预计工作量超过阈值': exceed_count,
                '任务负责人': persons_str
            })

    # 构建表6数据
    table6 = []
    for team in sorted(set(list(actual_empty_teams.keys()) + list(actual_exceed_teams.keys()))):
        empty_count = actual_empty_teams.get(team, 0)
        exceed_count = actual_exceed_teams.get(team, 0)
        if empty_count > 0 or exceed_count > 0:
            persons_set = actual_empty_persons.get(team, set()) | actual_exceed_persons.get(team, set())
            persons_list = sorted(p for p in persons_set if p)
            persons_str = ', '.join(persons_list) if persons_list else ''
            table6.append({
                '团队名称': team,
                '实际工作量为空': empty_count,
                '实际工作量超过阈值': exceed_count,
                '任务负责人': persons_str
            })

    return {
        'table5': table5,
        'table6': table6,
        'planned_threshold': planned_threshold,
        'actual_threshold': actual_threshold,
        'actual_status': actual_status
    }


def generate_markdown_table(data: List[Dict[str, Any]]) -> str:
    """将字典列表转换为 Markdown 表格"""
    if not data:
        return ""

    headers = list(data[0].keys())
    header_line = "| " + " | ".join(headers) + " |"
    separator_line = "| " + " | ".join(["---"] * len(headers)) + " |"

    rows = []
    for row in data:
        row_values = []
        for header in headers:
            value = row.get(header, '')
            value_str = str(value).replace('|', '\\|')
            row_values.append(value_str)
        rows.append("| " + " | ".join(row_values) + " |")

    return "\n".join([header_line, separator_line] + rows)


def generate_full_report(analysis1: Dict, analysis2: Dict, analysis3: Dict,
                        workspace: str, iterations: str) -> str:
    """生成完整的报告（包含所有6个表格）"""
    report_lines = []
    current_month = datetime.now().strftime('%Y-%m')
    report_lines.append(f"# {workspace} 工作量填报报告（{current_month}）")
    report_lines.append(f"## 迭代：{iterations}")
    report_lines.append("")

    # 一、团队任务数分析
    report_lines.append("## 一、团队任务数分析")
    report_lines.append("")
    report_lines.append("### 表1：团队任务数统计")
    report_lines.append(generate_markdown_table(analysis1['table1']))
    report_lines.append("")

    report_lines.append("### 表2：团队任务数占比分析")
    report_lines.append(generate_markdown_table(analysis1['table2']))
    report_lines.append("")

    # 二、团队工作量分析
    report_lines.append("## 二、团队工作量分析")
    report_lines.append("")
    report_lines.append("### 表3：团队工作量统计（单位：人日）")
    report_lines.append(generate_markdown_table(analysis2['table3']))
    report_lines.append("")

    report_lines.append("### 表4：团队工作量占比分析")
    report_lines.append(generate_markdown_table(analysis2['table4']))
    report_lines.append("")

    # 三、异常数据分析
    report_lines.append("## 三、异常数据分析")
    report_lines.append("")

    planned_threshold = analysis3.get('planned_threshold', 30)
    actual_threshold = analysis3.get('actual_threshold', 30)

    report_lines.append(f"### 表5：预计工作量数据异常表（阈值：>{planned_threshold}人日）")
    report_lines.append(generate_markdown_table(analysis3['table5']))
    report_lines.append("")

    report_lines.append(f"### 表6：实际工作量数据异常表（仅Closed状态，阈值：>{actual_threshold}人日）")
    report_lines.append(generate_markdown_table(analysis3['table6']))
    report_lines.append("")

    # 数据来源说明
    report_lines.append("---")
    report_lines.append("**数据来源说明**：")
    report_lines.append(f"- 迭代条件：{iterations}")
    report_lines.append(f"- 查询条件：工作区={workspace}，工作项类型=任务，状态≠已废弃")
    report_lines.append(f"- 有效统计记录数：{analysis1['valid_records']}条")
    report_lines.append(f"- 统计日期：{datetime.now().strftime('%Y-%m-%d')}")
    report_lines.append("")

    # 关键指标
    totals1 = analysis1['totals']
    totals2 = analysis2['totals']
    report_lines.append("**关键指标**：")
    report_lines.append(f"- 计划总任务数：{totals1['plan_total']}个")
    plan_total1 = totals1['plan_total'] if totals1['plan_total'] > 0 else 1
    plan_total2 = totals2['plan_total'] if totals2['plan_total'] > 0 else 1
    report_lines.append(f"- 实际完成总任务数：{totals1['actual_total']}个（完成率{totals1['actual_total']/plan_total1*100:.1f}%）")
    report_lines.append(f"- 需求交付类计划任务：{totals1['plan_delivery']}个（占比{totals1['plan_delivery']/plan_total1*100:.1f}%）")
    report_lines.append(f"- 非需求交付类计划任务：{totals1['plan_non_delivery']}个（占比{totals1['plan_non_delivery']/plan_total1*100:.1f}%）")
    report_lines.append(f"- 计划总工作量：{totals2['plan_total']:.1f}人日")
    if totals2['plan_total'] > 0:
        plan_delivery_ratio = totals2['plan_delivery'] / totals2['plan_total'] * 100
        plan_non_delivery_ratio = totals2['plan_non_delivery'] / totals2['plan_total'] * 100
    else:
        plan_delivery_ratio = plan_non_delivery_ratio = 0.0
    report_lines.append(f"  - 需求交付计划工作量：{totals2['plan_delivery']:.1f}人日（占比{plan_delivery_ratio:.1f}%）")
    report_lines.append(f"  - 非需求交付计划工作量：{totals2['plan_non_delivery']:.1f}人日（占比{plan_non_delivery_ratio:.1f}%）")
    report_lines.append(f"- 实际完成总工作量：{totals2['actual_total']:.1f}人日（完成率{totals2['actual_total']/plan_total2*100:.1f}%）")
    if totals2['actual_total'] > 0:
        actual_delivery_ratio = totals2['actual_delivery'] / totals2['actual_total'] * 100
        actual_non_delivery_ratio = totals2['actual_non_delivery'] / totals2['actual_total'] * 100
    else:
        actual_delivery_ratio = actual_non_delivery_ratio = 0.0
    report_lines.append(f"  - 需求交付实际工作量：{totals2['actual_delivery']:.1f}人日（占比{actual_delivery_ratio:.1f}%）")
    report_lines.append(f"  - 非需求交付实际工作量：{totals2['actual_non_delivery']:.1f}人日（占比{actual_non_delivery_ratio:.1f}%）")
    report_lines.append("")

    # 异常提醒
    total_empty = sum(len(table5) for table5 in [analysis3['table5']])
    total_actual_empty = sum(len(table6) for table6 in [analysis3['table6']])
    if total_empty > 0 or total_actual_empty > 0:
        report_lines.append("**异常数据提醒**：")
        if total_empty > 0:
            report_lines.append(f"- 预计工作量为空：需关注")
        if total_actual_empty > 0:
            report_lines.append(f"- 实际工作量为空（仅Closed状态）：需关注")
        report_lines.append("- 建议相关负责人及时补充完善工作量数据。")

    return "\n".join(report_lines)


# 以下函数保持不变（parse_work_items_file, fetch_work_items, simulate_work_items, extract_responsible_persons, build_email_content, send_abnormal_mail）
# 为节省空间，此处从原文件复制保持不变的部分...

def parse_work_items_file(filepath: str, project_filter: str = None) -> List[Dict[str, Any]]:
    """（保持不变）"""
    work_items = []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        header_idx = -1
        for i, line in enumerate(lines):
            stripped = line.strip()
            if '| 标题' in stripped and '工作项类型' in stripped and stripped.count('|') >= 6:
                header_idx = i
                break
        if header_idx == -1:
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith('|') and stripped.endswith('|') and stripped.count('|') >= 5:
                    header_idx = i
                    break
        if header_idx == -1:
            print(f"无法在文件中找到表格头：{filepath}", file=sys.stderr)
            return []

        raw_headers = [h.strip() for h in lines[header_idx].strip('| \\n').split('|')]
        header_indices = {name: idx for idx, name in enumerate(raw_headers)}

        FIELD_MAP = {
            '标题': ['标题'],
            '标识': ['标识'],
            '工作项类型': ['工作项类型'],
            'state': ['状态'],
            '预计工作量': ['预计工作量'],
            '实际工作量': ['实际工作量'],
            '研发活动类型': ['研发活动类型', '类型'],
            'team': ['团队', '所属团队'],
            '负责人': ['任务负责人', '负责人'],
            '创建时间': ['创建时间'],
            '所属项目': ['所属项目', '项目名称'],
        }

        data_start = header_idx + 2
        for line in lines[data_start:]:
            line = line.strip()
            if not line or not line.startswith('|'):
                continue
            if line.replace('|', '').replace('-', '').strip() == '':
                continue
            cells = [c.strip() for c in line.strip('| \\n').split('|')]
            if len(cells) < len(raw_headers):
                cells = cells + [''] * (len(raw_headers) - len(cells))
            item = {}
            for field, possible_names in FIELD_MAP.items():
                for name in possible_names:
                    if name in header_indices:
                        idx = header_indices[name]
                        if idx < len(cells):
                            cell = cells[idx]
                            import re
                            match = re.search(r'\[(.+?)\]\((.+?)\)', cell)
                            if match:
                                item[field] = match.group(1)
                            else:
                                item[field] = cell
                        break
            if not item.get('标识'):
                continue
            item['team'] = item.get('team', item.get('团队', item.get('所属团队', 'Unknown')))
            item['state'] = item.get('state', item.get('状态', ''))
            item['负责人'] = item.get('负责人', item.get('任务负责人', ''))
            def parse_float(value, default=0.0):
                try:
                    if isinstance(value, (int, float)):
                        return float(value)
                    if isinstance(value, str):
                        cleaned = value.strip()
                        if not cleaned:
                            return default
                        return float(cleaned)
                    return default
                except (ValueError, TypeError):
                    return default
            item['planned_workload'] = parse_float(item.get('预计工作量', 0))
            item['actual_workload'] = parse_float(item.get('实际工作量', 0))
            work_items.append(item)
        print(f"成功解析 {len(work_items)} 条工作项记录")
        return work_items
    except Exception as e:
        print(f"解析文件失败：{e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return []


def fetch_work_items_by_iterations(workspace: str, iterations: str, user_input: str = None) -> List[Dict[str, Any]]:
    """按迭代条件查询工作项（支持多个迭代）
    
    Args:
        workspace: 工作区名称
        project: 项目名称
        iterations: 迭代列表字符串，格式如："sprint09(20260511-20260524),sprint08(20260427-20260510)"
        user_input: 用户输入摘要（用于日志）
    
    Returns:
        工作项列表
    """
    # 解析迭代列表
    iteration_specs = []
    for spec in iterations.split(','):
        spec = spec.strip()
        if not spec:
            continue
        # 匹配格式：sprint09(20260511-20260524)
        match = re.match(r'(.*?)\((\d{8})-(\d{8})\)', spec)
        if match:
            iter_name = match.group(1).strip()
            start_dt = match.group(2)
            end_dt = match.group(3)
            iteration_specs.append({
                'name': iter_name,
                'start': start_dt,
                'end': end_dt
            })
        else:
            # 如果只有迭代名称，视为模糊匹配
            iteration_specs.append({
                'name': spec,
                'start': None,
                'end': None
            })
    
    if not iteration_specs:
        raise ValueError("迭代列表格式无效，请使用格式如：sprint09(20260511-20260524),sprint08(20260427-20260510)")
    
    # 构建查询条件
    conditions = (
        f"工作区={workspace} "
        f"AND 工作项类型=任务 "
        f"AND 状态!=已废弃 "
    )
    
    # 添加迭代条件（多个迭代用 OR 连接）
    iter_conditions = []
    for spec in iteration_specs:
        if spec['start'] and spec['end']:
            # 使用日期范围匹配迭代时间
            iter_conditions.append(f"(迭代 like '%{spec['name']}%' AND 创建时间>={spec['start']} AND 创建时间<{spec['end']})")
        else:
            # 仅匹配迭代名称包含
            iter_conditions.append(f"迭代 like '%{spec['name']}%'")
    
    if iter_conditions:
        conditions += "AND (" + " OR ".join(iter_conditions) + ")"
    
    # 查询字段：工作项类型、标识、标题、状态、预计工作量、实际工作量、研发活动类型、团队、任务负责人、创建时间、所属项目、迭代
    select_items = "工作项类型,标识,标题,状态,预计工作量,实际工作量,研发活动类型,团队,任务负责人,创建时间,所属项目,迭代"
    
    skill_dir = resolve_skill_dir(SKILL_STUDIO_RDCLOUD)
    cmd = (
        f"cd {skill_dir} && "
        f"python3 scripts/atomic/query_work_item.py "
        f"--select-items \"{select_items}\" "
        f"--conditions \"{conditions}\" "
        f"--limit 10000 "
        f"--order-by \"创建时间 desc\" "
        f"2>&1"
    )
    
    print(f"正在调用 studio-rdcloud-workitem 查询数据（按迭代）...")
    print(f"条件：{conditions}")
    
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            print(f"查询失败：{result.stderr}", file=sys.stderr)
            return []
        output = result.stdout.strip()
        if not output:
            print("未获取到查询结果", file=sys.stderr)
            return []
        lines_out = output.split(chr(10))
        table_lines = []
        in_table = False
        for line in lines_out:
            stripped = line.strip()
            if '|' in stripped and '标题' in stripped and stripped.count('|') >= 5:
                in_table = True
                table_lines.append(stripped)
            elif in_table:
                if stripped.startswith('|') and stripped.endswith('|'):
                    table_lines.append(stripped)
                else:
                    break
        if not table_lines:
            print("未在输出中找到表格数据", file=sys.stderr)
            print(f"输出前500字符：{output[:500]}", file=sys.stderr)
            return []
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.md', delete=False) as tmp:
            tmp.write(chr(10).join(table_lines))
            tmp_path = tmp.name
        try:
            work_items = parse_work_items_file(tmp_path)
        finally:
            os.unlink(tmp_path)
        print(f"成功查询到 {len(work_items)} 条工作项记录")
        return work_items
    except subprocess.TimeoutExpired:
        print("查询超时（60秒）", file=sys.stderr)
        return []
    except Exception as e:
        print(f"查询过程异常：{e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return []


def fetch_work_items(workspace: str, project: str, start_date: str, end_date: str, user_input: str = None) -> List[Dict[str, Any]]:
    """（保持不变）"""
    conditions = (
        f"工作区={workspace} "
        f"AND 工作项类型=任务 "
        f"AND 所属项目={project} "
        f"AND 状态!=已废弃 "
        f"AND 创建时间>={start_date} "
        f"AND 创建时间<{end_date}"
    )
    select_items = "工作项类型,标识,标题,状态,预计工作量,实际工作量,研发活动类型,团队,任务负责人,创建时间,所属项目"
    skill_dir = resolve_skill_dir(SKILL_STUDIO_RDCLOUD)
    cmd = (
        f"cd {skill_dir} && "
        f"python3 scripts/atomic/query_work_item.py "
        f"--select-items \"{select_items}\" "
        f"--conditions \"{conditions}\" "
        f"--limit 10000 "
        f"--order-by \"创建时间 desc\" "
        f"2>&1"
    )
    print(f"正在调用 studio-rdcloud-workitem 查询数据...")
    print(f"条件：{conditions}")
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            print(f"查询失败：{result.stderr}", file=sys.stderr)
            return []
        output = result.stdout.strip()
        if not output:
            print("未获取到查询结果", file=sys.stderr)
            return []
        lines_out = output.split(chr(10))
        table_lines = []
        in_table = False
        for line in lines_out:
            stripped = line.strip()
            if '|' in stripped and '标题' in stripped and stripped.count('|') >= 5:
                in_table = True
                table_lines.append(stripped)
            elif in_table:
                if stripped.startswith('|') and stripped.endswith('|'):
                    table_lines.append(stripped)
                else:
                    break
        if not table_lines:
            print("未在输出中找到表格数据", file=sys.stderr)
            print(f"输出前500字符：{output[:500]}", file=sys.stderr)
            return []
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.md', delete=False) as tmp:
            tmp.write(chr(10).join(table_lines))
            tmp_path = tmp.name
        try:
            work_items = parse_work_items_file(tmp_path)
        finally:
            os.unlink(tmp_path)
        print(f"成功查询到 {len(work_items)} 条工作项记录")
        return work_items
    except subprocess.TimeoutExpired:
        print("查询超时（60秒）", file=sys.stderr)
        return []
    except Exception as e:
        print(f"查询过程异常：{e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return []


def simulate_work_items(workspace: str, project: str) -> List[Dict[str, Any]]:
    """（保持不变）"""
    return []


def extract_responsible_persons(work_items: List[Dict[str, Any]], abnormal_params: dict) -> List[str]:
    """（保持不变）"""
    recipients_set = set()
    planned_threshold = abnormal_params.get('planned_threshold', 30)
    actual_threshold = abnormal_params.get('actual_threshold', 30)
    actual_status = abnormal_params.get('actual_status', 'closed')
    for item in work_items:
        team = item.get('team', '')
        planned = _safe_float(item.get('预计工作量', item.get('planned_workload')))
        actual = _safe_float(item.get('实际工作量', item.get('actual_workload')))
        state = item.get('state', '')
        responsible = item.get('负责人', '')
        is_abnormal = False
        if planned == 0 or planned > planned_threshold:
            is_abnormal = True
        if actual_status.lower() == 'closed':
            if state not in ['Closed', '已完成', '已关闭']:
                pass
            else:
                if actual == 0 or actual > actual_threshold:
                    is_abnormal = True
        else:
            if actual == 0 or actual > actual_threshold:
                is_abnormal = True
        if is_abnormal and responsible:
            recipients_set.add(responsible)
    return list(recipients_set)


def build_email_content(prereq: dict, abnormal_params: dict, report: str, recipients: List[str]) -> str:
    """构建邮件内容（基于迭代查询）"""
    lines = []
    lines.append(f"您好，{prereq['workspace']} 工作量填报异常数据提醒")
    lines.append("")
    lines.append(f"工作区：{prereq['workspace']}")
    lines.append(f"查询迭代：{prereq['iterations']}")
    lines.append(f"异常阈值：预计>{abnormal_params['planned_threshold']}人日，实际>{abnormal_params['actual_threshold']}人日（状态：{abnormal_params['actual_status']}）")
    lines.append(f"统计到责任人：{', '.join(recipients) if recipients else '无'}")
    lines.append("")
    lines.append("详细报告如下：")
    lines.append("---")
    lines.append(report)
    lines.append("---")
    lines.append("")
    lines.append("请及时登录 RDC 研发云补充完善工作量数据。")
    lines.append("")
    lines.append(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    return "\n".join(lines)


def send_abnormal_mail(prereq: dict, abnormal_params: dict, report: str, recipients: List[str]):
    """（保持不变）"""
    print(f"准备给 {len(recipients)} 位责任人发送异常提醒邮件...")
    body = build_email_content(prereq, abnormal_params, report, recipients)
    title = f"【异常提醒】{prereq['project']} 工作量填报异常数据（{prereq['month']}）"
    to_emails = ",".join(recipients)
    skill_dir = resolve_skill_dir(SKILL_OA_ZMAIL)
    cmd = f"cd {skill_dir} && python3 scripts/main.py send --title \"{title}\" --to \"{to_emails}\" --body \"{body}\""
    print("正在调用邮件技能发送...")
    result = os.system(cmd)
    if result == 0:
        print("✅ 异常提醒邮件已发送！")
    else:
        print("❌ 邮件发送失败，请检查 oa-zmail-manage 技能配置。")
        print("可手动复制以下内容发送：")
        print(f"标题：{title}")
        print(f"收件人：{to_emails}")
        print(f"正文：\n{body}")
