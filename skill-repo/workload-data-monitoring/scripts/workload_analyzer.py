#!/usr/bin/env python3
"""
工作量数据分析器
调用studio-rdcloud-workitem查询数据，生成统计表格
"""

import subprocess
import json
import tempfile
import os
import pandas as pd
from typing import Dict, Any

from skill_paths import (
    SKILL_STUDIO_RDCLOUD,
    resolve_export_dir,
    resolve_skill_dir,
)

class WorkloadAnalyzer:
    def __init__(self, skill_dir: str = None):
        self.skill_dir = skill_dir or os.path.dirname(os.path.dirname(__file__))
        self.workitem_skill_dir = resolve_skill_dir(SKILL_STUDIO_RDCLOUD)

        # 需求交付关键词列表
        self.demand_keywords = [
            '需求交付', '需求分析', '系统方案设计', '开发实现', '业务代码开发',
            '组件设计', '系统架构设计', '需求定义', '需求预分析',
            '测试工具开发', '测试分析与设计', '制定测试策略',
            '系统测试执行', '领域测试执行', 'UT/FT代码开发'
        ]

    def analyze(self, workspace: str, sprint_filter: str, threshold: int = 30, anomaly_status: str = 'closed', project: str = None) -> Dict[str, Any]:
        """执行数据分析"""
        try:
            # 1. 查询数据
            export_path = self._query_work_items(workspace, sprint_filter, project)

            # 2. 加载数据
            df = self._load_data(export_path)

            # 3. 数据预处理
            df = self._preprocess_data(df, sprint_filter, project)

            # 4. 分类统计
            stats = self._calculate_statistics(df)

            # 5. 生成表格
            tables = self._generate_tables(df, stats, threshold, anomaly_status)

            # 6. 获取异常数据详情
            anomaly_dfs = self.get_anomaly_details(df, threshold, anomaly_status)

            return {
                'success': True,
                'total_tasks': len(df),
                'closed_tasks': len(df[df['status'] == 'Closed']),
                'demand_implementation_planned': stats['demand_planned_count'],
                'demand_implementation_closed': stats['demand_closed_count'],
                **tables,
                'anomaly_dfs': anomaly_dfs,
                'export_path': export_path
            }

        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _query_work_items(self, workspace: str, sprint_filter: str, project: str = None) -> str:
        """获取工作项数据：优先使用已有导出文件，否则按条件导出"""
        log_dir = resolve_export_dir(SKILL_STUDIO_RDCLOUD)
        
        # 1) 尝试使用最近的导出文件
        if os.path.exists(log_dir):
            files = sorted([f for f in os.listdir(log_dir) 
                          if f.startswith('export_work_item_') and f.endswith('.xlsx')], 
                          reverse=True)
            if files:
                export_path = os.path.join(log_dir, files[0])
                import time
                file_age = time.time() - os.path.getmtime(export_path)
                if file_age < 86400:  # 24小时内视为有效
                    print(f"[信息] 使用已有导出文件: {files[0]}")
                    # 尝试读取文件，检查是否包含所需字段及筛选字段
                    try:
                        df = pd.read_excel(export_path)
                        # 新增：检查数据行数，避免使用空文件
                        if len(df) == 0:
                            print(f"[警告] 文件为空（无数据行），将重新导出")
                            raise ValueError("空文件")
                        cols = set(df.columns)
                        base_required = {'团队', '状态', '预计工作量', '实际工作量', '任务负责人', '工作项类型'}
                        if not base_required.issubset(cols):
                            print(f"[警告] 文件缺少基础必要字段，将重新导出")
                            raise ValueError("缺少基础字段")
                        # 检查项目字段（如果需要）
                        if project:
                            if 'BelongProject' not in cols and '所属项目' not in cols:
                                print(f"[警告] 需要项目筛选但文件缺少项目字段，将重新导出")
                                raise ValueError("缺少项目字段")
                        # 根据 sprint_filter 类型决定是否需要额外字段
                        import re
                        f = sprint_filter.strip()
                        if re.match(r'^\d{4}-\d{2}$', f):
                            # 月份查询需要计划结束日期
                            if '计划结束日期' not in cols and 'plan_end_date' not in cols:
                                print(f"[警告] 月份查询缺少计划结束日期字段，将重新导出")
                                raise ValueError("缺少计划结束日期")
                        else:
                            # 迭代查询需要迭代字段
                            if '迭代' not in cols and 'iteration' not in cols:
                                print(f"[警告] 迭代查询缺少迭代字段，将重新导出")
                                raise ValueError("缺少迭代字段")
                        # 检查通过
                        print(f"[信息] 文件字段满足筛选需求，共 {len(df)} 条数据")
                        # 如果存在项目筛选，在后端再过滤（因为导出文件可能包含其他项目）
                        return export_path
                    except Exception as e:
                        print(f"[警告] 文件检查失败: {e}，将重新导出")

        # 2) 需要按条件导出新数据
        date_iter_cond = self._build_conditions(sprint_filter)
        # 构建条件：工作区（动态）AND 工作项类型='任务' AND ((迭代条件)) AND BelongProject='...' AND 状态 != '已废弃'
        conditions_parts = [f"工作区={workspace}", "工作项类型='任务'"]
        if date_iter_cond:
            conditions_parts.append(f"({date_iter_cond})")
        if project:
            conditions_parts.append(f"BelongProject='{project}'")
        conditions_parts.append("状态 != '已废弃'")
        full_conditions = ' AND '.join(conditions_parts)
        export_path = self._export_work_items(workspace, full_conditions)
        return export_path

    def _build_conditions(self, sprint_filter: str) -> str:
        """构建查询条件：只支持迭代写法，逗号分隔；若某个迭代项带有年份前缀（如2026-），则默认将其应用于所有不带年份的迭代项"""
        import re
        f = sprint_filter.strip()
        parts = [p.strip() for p in f.split(',') if p.strip()]
        # 提取默认年份：取第一个形如 YYYY- 的部分的年份
        default_year = None
        for p in parts:
            m = re.match(r'^\d{4}-', p)
            if m:
                default_year = p.split('-', 1)[0]
                break
        clauses = []
        for p in parts:
            if re.match(r'^\d{4}-', p):
                # 带有年份前缀（如 2026-sprint08）
                # 实际迭代字段格式：sprint08(20260427~20260510) 或 sprint08
                # 因此拆分为两部分：迭代编号如 sprint08，年份如 2026
                year_part = p.split('-', 1)[0]
                sprint_part = p.split('-', 1)[1]
                clause = f"迭代 like '%{sprint_part}%' AND 迭代 like '%{year_part}%"
                clauses.append(clause)
            else:
                if default_year:
                    clauses.append(f"迭代 like '%{default_year}-{p}%'")
                else:
                    clauses.append(f"迭代 like '%{p}%'")
        if len(clauses) == 1:
            return clauses[0]
        else:
            return ' OR '.join([f'({c})' for c in clauses])

    def _export_work_items(self, workspace: str, conditions: str) -> str:
        """调用 export_work_item.py 导出数据"""
        script = os.path.join(self.workitem_skill_dir, "scripts/atomic/export_work_item.py")
        
        # 构造临时文件名
        import time
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        # 与 studio-rdcloud-workitem 的 export_work_item.py 写入位置保持一致
        log_dir = resolve_export_dir(SKILL_STUDIO_RDCLOUD)
        
        # 导出文件路径由脚本内部生成，我们只需要等待脚本完成
        import subprocess
        cmd = [
            'python3', script,
            '--select-items', '标识,标题,团队,状态,研发活动类型,预计工作量,实际工作量,任务负责人,工作项类型,更新时间,迭代,计划结束日期,所属项目',
            '--conditions', conditions,
            '--user-input', f'导出工作项：工作区={workspace}，条件={conditions}',
            '--limit', '10000'
        ]
        print(f"[信息] 执行导出: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        if result.returncode != 0:
            raise Exception(f"导出失败: {result.stderr}")
        
        # 查找最新导出的文件
        files = sorted([f for f in os.listdir(log_dir) 
                      if f.startswith('export_work_item_') and f.endswith('.xlsx')], 
                      reverse=True)
        if files:
            export_path = os.path.join(log_dir, files[0])
            print(f"[信息] 导出完成: {files[0]}")
            return export_path
        else:
            raise Exception("导出未生成文件")

    def _load_data(self, excel_path: str) -> pd.DataFrame:
        """加载Excel数据"""
        return pd.read_excel(excel_path)

    def _preprocess_data(self, df: pd.DataFrame, sprint_filter: str = None, project: str = None) -> pd.DataFrame:
        """数据预处理"""
        import re
        # 标准化列名
        column_mapping = {
            '团队': 'team',
            '所属团队': '所属团队',
            '状态': 'status',
            '研发活动类型': 'rd_activity',
            '预计工作量': 'estimated_effort',  # 保留兼容
            '初始估计': 'estimated_effort',      # 优先使用‘初始估计’
            '实际工作量': 'actual_effort',
            '任务负责人': 'owner',
            '标识': 'id',
            '标题': 'title',
            '工作项类型': 'work_item_type',
            '更新时间': 'update_time',
            '迭代': 'iteration',
            '计划结束日期': 'plan_end_date'
        }

        df = df.rename(columns={k: v for k, v in column_mapping.items() if k in df.columns})

        # 确保数值列
        for col in ['estimated_effort', 'actual_effort']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            else:
                df[col] = pd.NA

        # 项目筛选（支持 BelongProject 或 所属项目 字段）
        if project:
            proj_col = None
            if 'BelongProject' in df.columns:
                proj_col = 'BelongProject'
            elif '所属项目' in df.columns:
                proj_col = '所属项目'
            if proj_col:
                before = len(df)
                df = df[df[proj_col] == project]
                after = len(df)
                print(f"[调试] 项目筛选: {proj_col}='{project}'，过滤前 {before} 条，过滤后 {after} 条")
            else:
                print(f"[警告] 数据中缺少项目字段，无法进行项目筛选")

        # 排除已废弃状态
        if 'status' in df.columns:
            df = df[df['status'] != '已废弃']
        else:
            print(f"[警告] 数据中缺少状态字段，无法排除已废弃")

        # 根据 sprint_filter 进行数据过滤
        if sprint_filter:
            f = sprint_filter.strip()
            import re
            # 月份格式：YYYY-MM
            if re.match(r'^\d{4}-\d{2}$', f):
                if 'plan_end_date' in df.columns:
                    df['plan_end_date'] = pd.to_datetime(df['plan_end_date'], errors='coerce')
                    target_period = pd.Period(f)
                    df = df[df['plan_end_date'].dt.to_period('M') == target_period]
                else:
                    print(f"[警告] 计划结束日期字段不存在，无法按月份筛选: {f}")
            else:
                # 迭代筛选
                if 'iteration' in df.columns:
                    parts = [p.strip() for p in f.split(',') if p.strip()]
                    # 构建掩码：每个 part 若为 YYYY-sprint 则拆分为两个 contains（AND），否则单个 contains
                    mask = pd.Series([False] * len(df))
                    series = df['iteration'].astype(str)
                    for p in parts:
                        m = re.match(r'^(\d{4})-(.+)$', p)
                        if m:
                            year, sprint = m.group(1), m.group(2)
                            part_mask = series.str.contains(sprint, na=False) & series.str.contains(year, na=False)
                        else:
                            part_mask = series.str.contains(p, na=False)
                        mask = mask | part_mask
                    before = len(df)
                    df = df[mask]
                    after = len(df)
                    print(f"[调试] 迭代筛选: {f}，过滤前 {before} 条，过滤后 {after} 条")
                else:
                    print(f"[警告] 迭代字段不存在，无法按迭代筛选: {f}")

        return df

    def _is_demand_implementation(self, rd_activity: str) -> bool:
        """判断是否属于需求交付类"""
        if pd.isna(rd_activity):
            return False
        activity = str(rd_activity)
        return any(keyword in activity for keyword in self.demand_keywords)

    def _calculate_statistics(self, df: pd.DataFrame) -> Dict[str, Any]:
        """计算统计数据"""
        # 计划任务数（所有非空标识的任务）
        planned_total = len(df)

        # 实际完成数（状态为Closed）
        closed_total = len(df[df['status'] == 'Closed'])

        # 需求交付分类
        df['is_demand'] = df['rd_activity'].apply(self._is_demand_implementation)

        demand_planned = df[df['is_demand'] == True]
        demand_closed = demand_planned[demand_planned['status'] == 'Closed']
        nondemand_planned = df[df['is_demand'] == False]
        nondemand_closed = nondemand_planned[nondemand_planned['status'] == 'Closed']

        stats = {
            'demand_planned_count': len(demand_planned),
            'demand_closed_count': len(demand_closed),
            'nondemand_planned_count': len(nondemand_planned),
            'nondemand_closed_count': len(nondemand_closed),
            'demand_planned_effort': demand_planned['estimated_effort'].sum(),
            'demand_closed_effort': demand_closed['actual_effort'].sum(),
            'nondemand_planned_effort': nondemand_planned['estimated_effort'].sum(),
            'nondemand_closed_effort': nondemand_closed['actual_effort'].sum(),
            'total_planned_effort': df['estimated_effort'].sum(),
            'total_closed_effort': df[df['status'] == 'Closed']['actual_effort'].sum()
        }

        return stats

    def _generate_tables(self, df: pd.DataFrame, stats: Dict, threshold: int, anomaly_status: str) -> Dict[str, str]:
        """生成所有表格的Markdown"""
        tables = {}

        # 按团队分组
        teams = sorted(df['team'].unique())

        # === 表1：任务数统计 ===
        table1_rows = []
        for team in teams:
            team_df = df[df['team'] == team]
            closed_team = team_df[team_df['status'] == 'Closed']

            demand_plan = len(team_df[team_df['is_demand'] == True])
            nondemand_plan = len(team_df[team_df['is_demand'] == False])
            demand_close = len(closed_team[closed_team['is_demand'] == True])
            nondemand_close = len(closed_team[closed_team['is_demand'] == False])

            table1_rows.append([
                team,
                len(team_df),
                len(closed_team),
                demand_plan,
                nondemand_plan,
                demand_close,
                nondemand_close
            ])

        table1_header = ["团队名称", "计划总任务数", "实际完成总任务数", "需求交付计划任务数", "非需求交付计划任务数", "需求交付实际任务数", "非需求交付实际任务数"]
        tables['table1'] = self._format_markdown_table(table1_header, table1_rows)

        # === 表2：任务数占比 ===
        table2_rows = []
        for row in table1_rows:
            team = row[0]
            team_total_plan = row[1] or 1
            team_total_close = row[2] or 1
            demand_plan = row[3] or 0
            nondemand_plan = row[4] or 0
            demand_close = row[5] or 0
            nondemand_close = row[6] or 0

            table2_rows.append([
                team,
                f"{row[2]/team_total_plan*100:.2f}" if team_total_plan > 0 else "0.00",
                f"{demand_plan/team_total_plan*100:.2f}" if team_total_plan > 0 else "0.00",
                f"{nondemand_plan/team_total_plan*100:.2f}" if team_total_plan > 0 else "0.00",
                f"{demand_close/team_total_close*100:.2f}" if team_total_close > 0 else "0.00",
                f"{nondemand_close/team_total_close*100:.2f}" if team_total_close > 0 else "0.00"
            ])

        table2_header = ["团队名称", "团队完成任务数比例", "需求交付计划任务数占比", "非需求交付计划任务数占比", "需求交付实际任务数占比", "非需求交付实际任务数占比"]
        tables['table2'] = self._format_markdown_table(table2_header, table2_rows)

        # === 表3：工作量统计 ===
        table3_rows = []
        for team in teams:
            team_df = df[df['team'] == team]
            closed_team = team_df[team_df['status'] == 'Closed']

            demand_plan_effort = team_df[team_df['is_demand'] == True]['estimated_effort'].sum()
            nondemand_plan_effort = team_df[team_df['is_demand'] == False]['estimated_effort'].sum()
            demand_close_effort = closed_team[closed_team['is_demand'] == True]['actual_effort'].sum()
            nondemand_close_effort = closed_team[closed_team['is_demand'] == False]['actual_effort'].sum()

            table3_rows.append([
                team,
                f"{team_df['estimated_effort'].sum():.1f}",
                f"{closed_team['actual_effort'].sum():.1f}",
                f"{demand_plan_effort:.1f}",
                f"{nondemand_plan_effort:.1f}",
                f"{demand_close_effort:.1f}",
                f"{nondemand_close_effort:.1f}"
            ])

        table3_header = ["团队名称", "计划总工作量", "实际完成总工作量", "需求交付计划工作量", "非需求交付计划工作量", "需求交付实际工作量", "非需求交付实际工作量"]
        tables['table3'] = self._format_markdown_table(table3_header, table3_rows)

        # === 表4：工作量占比 ===
        table4_rows = []
        for row in table3_rows:
            team = row[0]
            total_plan = float(row[1]) if row[1] not in ['NaN','nan'] else 0
            total_close = float(row[2]) if row[2] not in ['NaN','nan'] else 0
            demand_plan = float(row[3])
            nondemand_plan = float(row[4])
            demand_close = float(row[5])
            nondemand_close = float(row[6])

            table4_rows.append([
                team,
                f"{demand_plan/total_plan*100:.2f}" if total_plan > 0 else "0.00",
                f"{nondemand_plan/total_plan*100:.2f}" if total_plan > 0 else "0.00",
                f"{demand_close/total_close*100:.2f}" if total_close > 0 else "0.00",
                f"{nondemand_close/total_close*100:.2f}" if total_close > 0 else "0.00"
            ])

        table4_header = ["团队名称", "需求交付计划工作量占比", "非需求交付计划工作量占比", "需求交付实际工作量占比", "非需求交付实际工作量占比"]
        tables['table4'] = self._format_markdown_table(table4_header, table4_rows)

        # === 表5：预计工作量异常 ===
        table5_rows = []
        for team in teams:
            team_df = df[df['team'] == team]

            # 预计工作量为空
            estimated_null = team_df['estimated_effort'].isna().sum()
            # 预计工作量超过阈值
            estimated_over = len(team_df[team_df['estimated_effort'] > threshold])
            # 负责人列表（超过阈值的任务负责人）
            over_tasks = team_df[team_df['estimated_effort'] > threshold]
            owners = over_tasks['owner'].dropna().unique()
            owners_str = ', '.join(owners) if len(owners) > 0 else '-'

            table5_rows.append([
                team,
                str(estimated_null),
                str(estimated_over),
                owners_str
            ])

        table5_header = ["团队名称", "预计工作量为空", f"预计工作量超过{threshold}", "任务负责人（超过阈值）"]
        tables['table5'] = self._format_markdown_table(table5_header, table5_rows)

        # === 表6：实际工作量异常（按指定状态过滤）===
        table6_rows = []
        status_filter = anomaly_status.lower()
        if status_filter == 'all':
            status_df = df.copy()
        else:
            status_df = df[df['status'].str.lower() == status_filter].copy()

        for team in teams:
            team_status_df = status_df[status_df['team'] == team]

            actual_null = team_status_df['actual_effort'].isna().sum()
            actual_over = len(team_status_df[team_status_df['actual_effort'] > threshold])
            over_tasks = team_status_df[team_status_df['actual_effort'] > threshold]
            owners = over_tasks['owner'].dropna().unique()
            owners_str = ', '.join(owners) if len(owners) > 0 else '-'

            table6_rows.append([
                team,
                str(actual_null),
                str(actual_over),
                owners_str
            ])

        status_desc = "Closed" if anomaly_status == 'closed' else anomaly_status
        table6_header = ["团队名称", "实际工作量为空", f"实际工作量超过{threshold}", f"任务负责人（超过{threshold}）"]
        tables['table6'] = self._format_markdown_table(table6_header, table6_rows)

        return tables

    def _format_markdown_table(self, headers: list, rows: list) -> str:
        """格式化Markdown表格"""
        # 列宽自适应
        col_widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                col_widths[i] = max(col_widths[i], len(str(cell)))

        # 构建分隔行
        separator = "|" + "|".join(["---" * (w // 3 + 1) for w in col_widths]) + "|"

        # 构建表头行
        header_row = "|" + "|".join([h.ljust(col_widths[i]) for i, h in enumerate(headers)]) + "|"

        # 构建数据行
        data_rows = []
        for row in rows:
            data_row = "|" + "|".join([str(cell).ljust(col_widths[i]) for i, cell in enumerate(row)]) + "|"
            data_rows.append(data_row)

        return "\n".join([header_row, separator] + data_rows)

    def get_anomaly_details(self, df: pd.DataFrame, threshold: int, anomaly_status: str) -> Dict[str, pd.DataFrame]:
        """获取异常数据详情（用于邮件）
        规则：在 closed 状态下，预计工作量/实际工作量为空的统计仅包含 NaN，不包含 0 值；超过阈值正常统计。
        """
        # 预计工作量异常（按状态过滤）
        status_filter = anomaly_status.lower()
        if status_filter == 'all':
            estimated_df = df.copy()
        else:
            estimated_df = df[df['status'].str.lower() == status_filter].copy()

        estimated_anomaly = estimated_df[
            (estimated_df['estimated_effort'].isna()) |
            (estimated_df['estimated_effort'] > threshold)
        ]

        # 实际工作量异常（按状态过滤）
        if status_filter == 'all':
            actual_df = df.copy()
        else:
            actual_df = df[df['status'].str.lower() == status_filter].copy()

        actual_anomaly = actual_df[
            (actual_df['actual_effort'].isna()) |
            (actual_df['actual_effort'] > threshold)
        ]

        return {
            'estimated_anomaly': estimated_anomaly,
            'actual_anomaly': actual_anomaly
        }

if __name__ == '__main__':
    # 测试用例
    analyzer = WorkloadAnalyzer()
    result = analyzer.analyze(
        workspace='UMEONE',
        sprint_filter='sprint08,sprint09',
        threshold=30,
        anomaly_status='closed'
    )
    print(json.dumps(result, ensure_ascii=False, indent=2)[:2000])
