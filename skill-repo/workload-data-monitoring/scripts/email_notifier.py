#!/usr/bin/env python3
"""
邮件通知器
基于异常数据发送提醒邮件
"""

import subprocess
import json
import os
import pandas as pd
from datetime import datetime

from skill_paths import SKILL_OA_ZMAIL, resolve_skill_dir

class EmailNotifier:
    def __init__(self):
        self.zmail_skill_dir = resolve_skill_dir(SKILL_OA_ZMAIL)

    def search_user(self, identifier: str) -> dict:
        """搜索用户邮箱"""
        cmd = [
            'python3',
            f'{self.zmail_skill_dir}/scripts/main.py',
            'search-users',
            '--to', identifier,
            '--type', 'user'
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        try:
            data = json.loads(result.stdout)
            if data.get('status') and data.get('to_searches'):
                users = data['to_searches'][0].get('users', [])
                if len(users) == 1:
                    return users[0]
                elif len(users) > 1:
                    return {'multiple': True, 'users': users}
        except:
            pass
        return {'error': '未找到用户或用户不唯一'}

    def send_email(self, to_emails: list, cc_emails: list = None, project_name: str = "", tables: dict = None) -> dict:
        """发送邮件"""
        if cc_emails is None:
            cc_emails = []

        # 构建邮件正文
        body_lines = [
            f"各位好，内文为{project_name}工作量数据异常任务，请各位及时治理，谢谢！",
            tables.get('estimated_anomaly_table', ''),
            tables.get('actual_anomaly_table', ''),
            "\n--\n数据来源：UMEONE工作区\n统计时间：" + datetime.now().strftime('%Y-%m-%d')
        ]
        body = "\n".join(body_lines)

        # 调用zmail发送（使用参数列表，避免shell编码问题）
        to_str = ','.join(to_emails)
        cc_str = ','.join(cc_emails) if cc_emails else ''
        import json as _json
        title = f"各位好，内文为{project_name}工作量数据异常任务，请各位及时治理，谢谢！"
        cmd = [
            'python3',
            f'{self.zmail_skill_dir}/scripts/main.py',
            'send',
            '--title', title,
            '--to', to_str,
            '--cc', cc_str,
            '--body', body
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            return {'success': False, 'error': result.stderr}

        try:
            data = json.loads(result.stdout)
            if data.get('status'):
                return {'success': True, 'mid': data.get('mid')}
        except:
            pass

        return {'success': False, 'error': '发送失败'}

    def _prepare_detail_dataframe(self, df: pd.DataFrame, value_col: str) -> pd.DataFrame:
        """根据异常DataFrame准备明细DataFrame，按指定列排序"""
        # 如果需要筛选仅工作量为0（旧逻辑），可以在这里控制。现在保留全部传入的df
        display_cols = ['work_item_type', 'id', 'title', 'status', value_col, 'team', 'owner']
        col_names = {
            'work_item_type': '工作项类型',
            'id': '标识',
            'title': '标题',
            'status': '状态',
            'estimated_effort': '初始估计',
            'actual_effort': '实际工作量',
            'team': '团队',
            'owner': '任务负责人'
        }
        available_cols = [c for c in display_cols if c in df.columns]
        display_df = df[available_cols].copy()
        display_df.columns = [col_names.get(c, c) for c in available_cols]
        desired_order = ['工作项类型', '标识', '标题', '状态', '预计工作量', '实际工作量', '团队', '任务负责人']
        existing_order = [col for col in desired_order if col in display_df.columns]
        return display_df[existing_order]

    def format_anomaly_table(self, df: pd.DataFrame, value_col: str, for_email: bool = False) -> str:
        """
        将异常DataFrame格式化为Markdown表格。
        - for_email=False：返回按团队汇总的表格（用于报告文件中的表5/表6）
        - for_email=True：返回明细表格（用于邮件正文），表头：工作项类型、标识、标题、状态、{预计/实际}工作量、团队、任务负责人，包含所有传入的异常记录
        """
        df_filtered = df.copy()
        if len(df_filtered) == 0:
            chinese_title = '预计工作量' if 'estimated' in value_col else '实际工作量'
            return f"**{chinese_title}异常任务列表**（共0条）\n\n暂无异常任务\n"
        if for_email:
            # 邮件明细视图：固定列顺序，直接使用传入的异常DataFrame（已包含所有异常记录）
            display_cols = ['work_item_type', 'id', 'title', 'status', value_col, 'team', 'owner']
            col_names = {
                'work_item_type': '工作项类型',
                'id': '标识',
                'title': '标题',
                'status': '状态',
                'estimated_effort': '初始估计',
                'actual_effort': '实际工作量',
                'team': '团队',
                'owner': '任务负责人'
            }
            available_cols = [c for c in display_cols if c in df_filtered.columns]
            display_df = df_filtered[available_cols].copy()
            display_df.columns = [col_names.get(c, c) for c in available_cols]
            desired_order = ['工作项类型', '标识', '标题', '状态', '预计工作量', '实际工作量', '团队', '任务负责人']
            existing_order = [col for col in desired_order if col in display_df.columns]
            display_df = display_df[existing_order]
        else:
            # 报告汇总视图（原有逻辑）
            display_df = self._prepare_detail_dataframe(df_filtered, value_col)
        headers = display_df.columns.tolist()
        separator = "|" + "|".join(["---"] * len(headers)) + "|"
        header_row = "|" + "|".join(headers) + "|"
        rows = []
        for _, row in display_df.iterrows():
            row_str = "|" + "|".join([str(v) for v in row]) + "|"
            rows.append(row_str)
        table = "\n".join([header_row, separator] + rows)
        title_map = {'estimated_effort': '初始估计', 'actual_effort': '实际工作量'}
        chinese_title = title_map.get(value_col, value_col)
        return f"**{chinese_title}异常任务列表**（共{len(display_df)}条）\n\n{table}\n"

    def format_zero_workload_table(self, df: pd.DataFrame, value_col: str) -> str:
        """
        生成邮件正文用的明细表格：仅包含工作量为0或为空的记录，按团队汇总表的明细字段展示。
        表头：工作项类型、标识、标题、状态、{预计/实际}工作量、团队、任务负责人
        """
        # 筛选工作量为0或为空的记录
        df_zero = df[(df[value_col] == 0) | (df[value_col].isna())].copy()
        if len(df_zero) == 0:
            chinese_title = '预计工作量' if 'estimated' in value_col else '实际工作量'
            return f"**{chinese_title}为0的任务列表**（共0条）\n\n暂无相关任务\n"
        # 准备显示列
        display_cols = ['work_item_type', 'id', 'title', 'status', value_col, 'team', 'owner']
        col_names = {
            'work_item_type': '工作项类型',
            'id': '标识',
            'title': '标题',
            'status': '状态',
            'estimated_effort': '初始估计',
            'actual_effort': '实际工作量',
            'team': '团队',
            'owner': '任务负责人'
        }
        available_cols = [c for c in display_cols if c in df_zero.columns]
        display_df = df_zero[available_cols].copy()
        display_df.columns = [col_names.get(c, c) for c in available_cols]
        desired_order = ['工作项类型', '标识', '标题', '状态', '预计工作量', '实际工作量', '团队', '任务负责人']
        existing_order = [col for col in desired_order if col in display_df.columns]
        display_df = display_df[existing_order]

        headers = display_df.columns.tolist()
        separator = "|" + "|".join(["---"] * len(headers)) + "|"
        header_row = "|" + "|".join(headers) + "|"
        rows = []
        for _, row in display_df.iterrows():
            row_str = "|" + "|".join([str(v) for v in row]) + "|"
            rows.append(row_str)
        table = "\n".join([header_row, separator] + rows)
        chinese_title_map = {'estimated_effort': '初始估计', 'actual_effort': '实际工作量'}
        chinese_title = chinese_title_map.get(value_col, value_col)
        return f"**{chinese_title}为0的任务列表**（共{len(display_df)}条）\n\n{table}\n"

if __name__ == '__main__':
    notifier = EmailNotifier()
    # 测试搜索用户
    result = notifier.search_user("庄静文")
    print(json.dumps(result, ensure_ascii=False, indent=2))
