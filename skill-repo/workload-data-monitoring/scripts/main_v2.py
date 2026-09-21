#!/usr/bin/env python3
"""
工作量数据监控技能 - 主入口
提供指定工作区的工作项数据查询、统计分析、异常监控和报告生成
"""

import sys
import os
import json
import argparse
from datetime import datetime
from pathlib import Path
import pandas as pd

# 添加技能目录到路径
skill_dir = Path(__file__).parent.parent
sys.path.insert(0, str(skill_dir))

from workload_analyzer import WorkloadAnalyzer
from wiki_publisher import WikiPublisher
from email_notifier import EmailNotifier
from skill_paths import resolve_export_dir, resolve_report_dir

class WorkloadMonitor:
    def __init__(self, email_action: str = None, preview_email: bool = False):
        self.context = {
            'workspace': None,
            'project_name': None,
            'sprint_filter': None,
            'query_type': None,
            'threshold': 30,
            'anomaly_status': 'closed',
            'threshold_set': False,
            'wiki_url': None,
            'analysis_completed': False,
            'email_sent': False,
            'last_result': None,
            'email_choice': email_action,
            'preview_email': preview_email
        }
        self.analyzer = WorkloadAnalyzer()

    def run(self, user_input: str = None, context_messages: list = None, context: dict = None):
        """主运行入口，支持多轮对话"""
        # 恢复上下文
        if context:
            self.context.update(context)
        
        # CLI 模式：一次性执行所有参数（无用户交互输入）
        # 条件：有workspace、project_name、query_type、sprint_filter，且无user_input显式提供
        if (user_input is None and 
            self.context.get('workspace') and 
            self.context.get('project_name') and 
            self.context.get('query_type') and 
            self.context.get('sprint_filter')):
            # 对于类型3，确保阈值已设置
            if self.context['query_type'] == '3':
                self.context['threshold'] = self.context.get('threshold', 30)
                self.context['anomaly_status'] = self.context.get('anomaly_status', 'closed')
                self.context['threshold_set'] = True
                # 如果请求邮件预览，直接进入预览分支
                if self.context.get('preview_email'):
                    return self._execute_state('needs_email_action')
            # 对于类型4，允许后续由命令行处理 Wiki 发布
            return self._execute_query_internal()
        
        # 交互模式：根据状态机处理
        state = self._get_state()

        if user_input and state not in ['complete', 'execute_only', 'needs_email_action_after_analysis']:
            self._handle_input(user_input, state)
            state = self._get_state()

        return self._execute_state(state)

    def _get_state(self, user_input=None):
        """状态机"""
        # 检查是否具备基本查询条件（工作区、项目、迭代）
        if not self.context.get('workspace') or not self.context.get('project_name') or not self.context.get('sprint_filter'):
            return 'needs_info'
        if not self.context.get('query_type'):
            return 'needs_query_type'
        
        # 类型3：如果阈值未设置，需要询问（除非是CLI模式且有其他后续操作）
        if self.context.get('query_type') == '3' and not self.context.get('threshold_set'):
            # 如果是CLI模式且没有user_input，且不是显式等待，则使用默认值
            if user_input is None and not self.context.get('interactive'):
                self.context['threshold'] = 30
                self.context['anomaly_status'] = 'closed'
                self.context['threshold_set'] = True
                return 'needs_email_action_after_analysis'
            return 'needs_threshold_status'
            
        # 类型4：如果没有Wiki URL且是CLI模式，直接执行而不等待（但可能后续需要邮件）
        if self.context.get('query_type') == '4' and not self.context.get('wiki_url'):
            if user_input is None and not self.context.get('interactive'):
                return 'execute_only'
            return 'needs_wiki_url'
            
        if self.context.get('query_type') == '3' and self.context.get('analysis_completed') and not self.context.get('email_sent'):
            return 'needs_email_action'
            
        return 'complete'

    def _handle_input(self, user_input, state):
        """处理用户输入"""
        user_input = user_input.strip()

        if state == 'needs_workspace':
            self.context['workspace'] = user_input.strip()

        if state == 'needs_info':
            # 期望三行输入：工作区、项目名称、查询迭代
            lines = [line.strip() for line in user_input.strip().split('\n') if line.strip()]
            if len(lines) >= 3:
                # 第一行：项目工作区
                workspace = lines[0]
                # 第二行：项目名称
                project_name = lines[1]
                # 第三行：查询迭代
                sprint_filter = lines[2]
                self.context['workspace'] = workspace
                self.context['project_name'] = project_name
                self.context['sprint_filter'] = sprint_filter
            else:
                # 尝试用分号或逗号分隔
                parts = user_input.replace(';', ',').split(',')
                if len(parts) >= 3:
                    self.context['workspace'] = parts[0].strip()
                    self.context['project_name'] = parts[1].strip()
                    self.context['sprint_filter'] = parts[2].strip()

        elif state == 'needs_query_type':
            if user_input in ['1', '2', '3', '4']:
                self.context['query_type'] = user_input
            else:
                # 如果输入的是描述，提取数字
                for ch in user_input:
                    if ch in '1234':
                        self.context['query_type'] = ch
                        break

        elif state == 'needs_threshold_status':
            # 格式: 阈值,状态 或分开两次输入
            if ',' in user_input:
                parts = user_input.split(',')
                if len(parts) >= 2:
                    try:
                        self.context['threshold'] = int(parts[0].strip())
                        self.context['anomaly_status'] = parts[1].strip().lower()
                        self.context['threshold_set'] = True
                    except:
                        pass
            else:
                # 单次输入，假设只改阈值，状态保持默认
                try:
                    self.context['threshold'] = int(user_input)
                    self.context['threshold_set'] = True
                except:
                    pass

        elif state == 'needs_wiki_url':
            if user_input.startswith('http'):
                self.context['wiki_url'] = user_input.strip()

        elif state == 'needs_email_action':
            # 如果正在等待邮件预览确认，将用户输入作为邮件发送选择
            if self.context.get('_awaiting_email_preview_confirm'):
                self.context['_awaiting_email_preview_confirm'] = False  # 清除一次性标志
                if user_input in ['1', '确认', 'confirm', 'yes', '是']:
                    self.context['email_choice'] = 'all'
                elif user_input in ['2', '不发送', 'no', '否']:
                    self.context['email_choice'] = 'none'
                else:
                    self.context['email_choice'] = user_input
            else:
                # 兼容旧流程：直接作为邮件操作选择
                self.context['email_choice'] = user_input

    def _execute_state(self, state):
        """执行状态对应的动作"""

        if state == 'needs_info':
            return {
                "status": "input_required",
                "prompt": "请提供以下信息（三行，每行一个）：\n项目工作区 - 例如：UMEONE\n项目名称 - 例如：ZENIC ONE-UME R22 V16.26.20\n查询迭代 - 例如：sprint09 或 2026-sprint08,2026-sprint09",
                "context": self.context
            }

        elif state == 'needs_query_type':
            options = {
                '1': '任务数目',
                '2': '任务工作量',
                '3': '异常数据',
                '4': '生成项目级报告'
            }
            return {
                "status": "input_required",
                "prompt": "请选择查询内容：\n" + "\n".join([f"{k}、{v}" for k, v in options.items()]),
                "options": options,
                "context": self.context
            }

        elif state == 'needs_threshold_status':
            return {
                "status": "input_required",
                "prompt": f"设置异常参数（格式：阈值,状态）\n当前默认：阈值=30，状态=closed\n状态可选：closed, active, new, all\n示例：30,closed",
                "context": {**self.context, 'threshold_set': False}
            }

        elif state == 'needs_wiki_url':
            return {
                "status": "input_required",
                "prompt": "请提供要发布到的Wiki页面URL（父页面链接）：",
                "context": self.context
            }

        elif state == 'needs_email_action':
            # CLI 预览模式：仅生成预览，不自动发送
            if self.context.get('preview_email'):
                if not self.context.get('last_result'):
                    result = self._execute_query_internal()
                    # 兼容性检查：成功返回 status='success'，失败可能返回 {'success': False, 'error': ...}
                    if result.get('status') != 'success' and result.get('success') is False:
                        return {"status": "error", "message": result.get('error', '查询失败')}
                    self.context['last_result'] = result
                else:
                    result = self.context['last_result']
                anomaly_dfs = self._get_anomaly_dataframes()
                estimated_table = ''
                actual_table = ''
                if anomaly_dfs:
                    notifier = EmailNotifier()
                    estimated_table = notifier.format_anomaly_table(anomaly_dfs.get('estimated_anomaly', pd.DataFrame()), 'estimated_effort', for_email=True)
                    actual_table = notifier.format_anomaly_table(anomaly_dfs.get('actual_anomaly', pd.DataFrame()), 'actual_effort', for_email=True)
                project_name = self.context.get('project_name', '')
                mail_title = f"各位好，内文为{project_name}工作量数据异常任务，请各位及时治理，谢谢！"
                mail_body = f"{mail_title}\n\n{estimated_table}\n{actual_table}\n\n--\n数据来源：{self.context['workspace']}工作区\n统计时间：{datetime.now().strftime('%Y-%m-%d')}"
                return {
                    "status": "success",
                    "message": "邮件预览已生成（未发送）",
                    "mail_preview": mail_body,
                    "prompt": "使用 --email all 可发送给全部责任人，或使用 --recipients <邮箱列表> 发送给指定人员。",
                    "context": self.context
                }
            # CLI 自动邮件：如果已指定 email_choice，直接发送
            if self.context.get('email_choice'):
                choice = self.context['email_choice']
                recipients = []
                if choice == 'all':
                    recipients = self._extract_recipients_from_anomaly()
                elif choice != 'none':
                    recipients = [r.strip() for r in choice.split(',')]
                if recipients:
                    email_result = self._send_anomaly_email(recipients)
                    self.context['email_sent'] = True
                    base = self.context.get('last_result', {})
                    return {**base, **email_result}
                else:
                    return {"status": "success", "message": "邮件已跳过（不发送）"}
            # 交互模式：先获取分析结果和异常数据
            if not self.context.get('last_result'):
                result = self._execute_query_internal()
                # 兼容性检查：_execute_query_internal 成功时返回 status='success'，失败时可能返回 {'success': False, 'error': ...}
                if result.get('status') != 'success' and result.get('success') is False:
                    return {"status": "error", "message": result.get('error', '查询失败')}
            else:
                result = self.context['last_result']
            anomaly_dfs = self._get_anomaly_dataframes()
            estimated_table = ''
            actual_table = ''
            if anomaly_dfs:
                notifier = EmailNotifier()
                estimated_table = notifier.format_anomaly_table(anomaly_dfs.get('estimated_anomaly', pd.DataFrame()), 'estimated_effort', for_email=True)
                actual_table = notifier.format_anomaly_table(anomaly_dfs.get('actual_anomaly', pd.DataFrame()), 'actual_effort', for_email=True)
            project_name = self.context.get('project_name', '')
            mail_title = f"各位好，内文为{project_name}工作量数据异常任务，请各位及时治理，谢谢！"
            mail_body_preview = f"{mail_title}\n\n{estimated_table}\n{actual_table}\n\n--\n数据来源：{self.context['workspace']}工作区\n统计时间：{datetime.now().strftime('%Y-%m-%d')}"
            preview = (
                f"【邮件预览】\n"
                f"标题：{mail_title}\n\n"
                f"正文：\n{mail_body_preview}\n\n"
                f"请确认是否发送该邮件？\n"
                f"1. 确认发送（发送给所有统计到的责任人）\n"
                f"2. 不发送\n"
                f"3. 指定收件人（提供邮箱/工号）"
            )
            self.context['_awaiting_email_preview_confirm'] = True
            return {
                "status": "input_required",
                "prompt": preview,
                "context": self.context
            }
            
        elif state == 'execute_only':
            # 直接执行查询，无交互
            result = self._execute_query_internal()
            if not result['success']:
                return result
            self.context['last_result'] = result
            self.context['analysis_completed'] = True
            # 根据类型决定是否还需要后续步骤
            query_type = self.context['query_type']
            if query_type == '4':
                return {**result, "next_step": "wiki_publish", "prompt": "请提供Wiki URL"}
            elif query_type == '3':
                self.context['threshold_set'] = True
                return {**result, "next_step": "email_notify", "prompt": "请选择邮件操作"}
            else:
                return result

        elif state == 'needs_email_action_after_analysis':
            # 分析已完成，直接进入邮件选择
            return {
                "status": "input_required",
                "prompt": "请选择邮件操作：\n1. 发送给所有统计到的责任人\n2. 不发送\n3. 指定收件人（提供邮箱/工号）",
                "context": self.context
            }

        elif state == 'complete':
            if self.context.get('last_result'):
                return self.context['last_result']
            return {"status": "complete", "message": "已完成"}

        else:
            # 默认：执行查询
            result = self._execute_query_internal()
            if result['success']:
                self.context['last_result'] = result
                self.context['analysis_completed'] = True
                # CLI 自动邮件（类型3且已提供 email_choice）
                if (self.context.get('query_type') == '3' and 
                    self.context.get('email_choice') and 
                    not self.context.get('email_sent')):
                    self.context['email_sent'] = True
                    choice = self.context['email_choice']
                    recipients = []
                    if choice == 'all':
                        recipients = self._extract_recipients_from_anomaly()
                    elif choice != 'none':
                        recipients = [r.strip() for r in choice.split(',')]
                    if recipients:
                        email_result = self._send_anomaly_email(recipients)
                        result = {**result, **email_result}
                    else:
                        result = {**result, "message": result.get('message', '') + "\n邮件已跳过（不发送）"}
                return result
            return result

    def _get_anomaly_dataframes(self):
        """获取异常数据的DataFrame字典（estimated_anomaly, actual_anomaly）"""
        log_dir = resolve_export_dir()
        if os.path.exists(log_dir):
            files = sorted([f for f in os.listdir(log_dir) if f.startswith('export_work_item_') and f.endswith('.xlsx')], reverse=True)
            if files:
                export_path = os.path.join(log_dir, files[0])
                try:
                    df = self.analyzer._load_data(export_path)
                    df = self.analyzer._preprocess_data(df, sprint_filter=self.context.get('sprint_filter'), project=self.context.get('project_name'))
                    anomaly_dfs = self.analyzer.get_anomaly_details(
                        df,
                        self.context['threshold'],
                        self.context['anomaly_status']
                    )
                    return anomaly_dfs
                except Exception as e:
                    print(f"[警告] 读取异常数据失败: {e}")
        return None

    def _extract_recipients_from_anomaly(self):
        """从最新导出的异常数据中提取所有责任人（工号）列表"""
        anomaly_dfs = self._get_anomaly_dataframes()
        if anomaly_dfs:
            owners = set()
            for key in ['estimated_anomaly', 'actual_anomaly']:
                if key in anomaly_dfs and not anomaly_dfs[key].empty:
                    if 'owner' in anomaly_dfs[key].columns:
                        owners.update(anomaly_dfs[key]['owner'].dropna().astype(str).unique())
            return list(owners)
        return []

    def _execute_query_internal(self):
        """执行数据分析（内部）"""
        query_type = self.context['query_type']
        threshold = self.context.get('threshold', 30)
        anomaly_status = self.context.get('anomaly_status', 'closed')

        result = self.analyzer.analyze(
            workspace=self.context['workspace'],
            sprint_filter=self.context['sprint_filter'],
            threshold=threshold,
            anomaly_status=anomaly_status,
            project=self.context.get('project_name')
        )

        if not result['success']:
            return result

        # 组合markdown
        output_lines = []
        tables_generated = []

        # 添加报告头部信息（工作区+项目+迭代）
        workspace = self.context.get('workspace', '未知工作区')
        project = self.context.get('project_name', '未知项目')
        sprint = self.context.get('sprint_filter', '未知迭代')
        output_lines.append(f"# 工作量数据监控报告\n")
        output_lines.append(f"**工作区**: {workspace}  \n")
        output_lines.append(f"**项目**: {project}  \n")
        output_lines.append(f"**迭代**: {sprint}  \n")
        output_lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  \n\n")

        if query_type in ['1', '4']:
            output_lines.append("## 一、团队任务数分析\n")
            output_lines.append("### 表1：各团队任务统计（任务数）\n")
            output_lines.append(result['table1'])
            output_lines.append("\n### 表2：各团队任务完成比例（%）\n")
            output_lines.append(result['table2'])
            tables_generated.extend(['table1', 'table2'])

        if query_type in ['2', '4']:
            output_lines.append("\n## 二、团队工作量分析\n")
            output_lines.append("### 表3：各团队工作量统计（人天）\n")
            output_lines.append(result['table3'])
            output_lines.append("\n### 表4：各团队工作量占比（%）\n")
            output_lines.append(result['table4'])
            tables_generated.extend(['table3', 'table4'])

        if query_type in ['3', '4']:
            output_lines.append("\n## 三、异常数据分析\n")
            output_lines.append("### 表5：预计工作量数据异常表\n")
            output_lines.append(result['table5'])
            output_lines.append("\n### 表6：实际工作量数据异常统计\n")
            output_lines.append(result['table6'])
            tables_generated.extend(['table5', 'table6'])

        markdown_output = "\n".join(output_lines)

        # 保存文件
        safe_sprint = self.context['sprint_filter'].replace(' ', '_').replace(',', '_')
        output_file = os.path.join(
            resolve_report_dir(), f"workload_report_{safe_sprint}.md"
        )
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(markdown_output)

        response = {
            "status": "success",
            "message": "数据查询完成",
            "output_file": output_file,
            "markdown": markdown_output,
            "summary": {
                "total_tasks": result['total_tasks'],
                "closed_tasks": result['closed_tasks'],
                "demand_implementation_planned": result['demand_implementation_planned'],
                "demand_implementation_closed": result['demand_implementation_closed']
            },
            "tables_generated": tables_generated
        }

        # 如果是异常数据或完整报告，保存异常详情供后续使用
        if query_type in ['3', '4']:
            response['anomaly_tables_preview'] = f"表5（预计工作量异常）：\n{result['table5']}\n\n表6（实际工作量异常）：\n{result['table6'][:500]}..."
            response['_internal_anomaly_data'] = True  # 标记有异常数据

        return response

    def _execute_wiki_publish(self, wiki_url):
        """执行Wiki发布"""
        if not self.context.get('last_result'):
            result = self._execute_query_internal()
            if not result['success']:
                return result
        else:
            result = self.context['last_result']

        publisher = WikiPublisher()
        try:
            workspace = self.context.get('workspace', '未知工作区')
            project = self.context.get('project_name', '未知项目')
            wiki_result = publisher.publish(
                content=result['markdown'],
                title=f"{workspace} - {project} 工作量填报报告",
                parent_url=wiki_url,
                project_name=project
            )
            result['wiki_result'] = wiki_result
            if wiki_result.get('success'):
                result['wiki_link'] = wiki_result.get('page_link')
                result['message'] = f"报告已发布到Wiki: {wiki_result.get('page_link')}"
                self.context['wiki_url'] = wiki_url
                return result
            else:
                result['status'] = 'error'
                result['message'] = f"Wiki发布失败: {wiki_result.get('error')}"
                return result
        except Exception as e:
            result['status'] = 'error'
            result['message'] = f"Wiki发布异常: {str(e)}"
            result['wiki_error'] = str(e)
            return result

    def _send_anomaly_email(self, recipients, cc_emails=None):
        """发送异常邮件（仅广播工作量为0的任务）"""
        if cc_emails is None:
            cc_emails = []
        # 获取异常数据
        log_dir = resolve_export_dir()
        anomaly_dfs = None

        if os.path.exists(log_dir):
            files = sorted([f for f in os.listdir(log_dir) if f.startswith('export_work_item_') and f.endswith('.xlsx')], reverse=True)
            if files:
                export_path = os.path.join(log_dir, files[0])
                try:
                    df = self.analyzer._load_data(export_path)
                    df = self.analyzer._preprocess_data(df, sprint_filter=self.context.get('sprint_filter'), project=self.context.get('project_name'))
                    anomaly_dfs = self.analyzer.get_anomaly_details(
                        df,
                        self.context['threshold'],
                        self.context['anomaly_status']
                    )
                except Exception as e:
                    return {"status": "error", "message": f"读取异常数据失败: {str(e)}"}

        if not anomaly_dfs:
            return {"status": "error", "message": "未找到异常数据"}

        notifier = EmailNotifier()
        estimated_table = notifier.format_anomaly_table(anomaly_dfs['estimated_anomaly'], 'estimated_effort', for_email=True)
        actual_table = notifier.format_anomaly_table(anomaly_dfs['actual_anomaly'], 'actual_effort', for_email=True)

        # 实际发送邮件
        try:
            send_result = notifier.send_email(
                to_emails=recipients,
                cc_emails=cc_emails,
                project_name=self.context['project_name'],
                tables={
                    'estimated_anomaly_table': estimated_table,
                    'actual_anomaly_table': actual_table
                }
            )
            self.context['email_sent'] = True
            if send_result.get('success'):
                return {
                    "status": "success",
                    "message": "邮件已成功发送",
                    "email_result": send_result
                }
            else:
                return {
                    "status": "error",
                    "message": f"邮件发送失败: {send_result.get('error', '未知错误')}"
                }
        except Exception as e:
            self.context['email_sent'] = False
            return {"status": "error", "message": f"邮件发送异常: {str(e)}"}


def main():
    parser = argparse.ArgumentParser(description='工作量数据监控')
    parser.add_argument('--input', type=str, help='用户输入')
    parser.add_argument('--context', type=str, help='序列化上下文JSON')
    parser.add_argument('--project', type=str, help='项目名称')
    parser.add_argument('--sprint', type=str, help='迭代筛选')
    parser.add_argument('--type', type=str, choices=['1','2','3','4'], help='查询类型')
    parser.add_argument('--workspace', type=str, help='工作区名称')
    parser.add_argument('--threshold', type=int, default=30, help='异常阈值')
    parser.add_argument('--status', type=str, default='closed', help='实际工作量异常状态')
    parser.add_argument('--wiki-url', type=str, help='Wiki父页面URL')
    parser.add_argument('--email-yes', action='store_true', help='确认发送邮件')
    parser.add_argument('--recipients', type=str, help='邮件收件人，逗号分隔')
    parser.add_argument('--cc', type=str, help='邮件抄送人，逗号分隔')
    parser.add_argument('--email', type=str, help='邮件操作: all=发送给所有责任人, none=不发送, 或直接提供邮箱/工号(逗号分隔)')
    parser.add_argument('--preview-email', action='store_true', help='仅预览邮件内容，不自动发送')
    args = parser.parse_args()

    monitor = WorkloadMonitor(email_action=args.email, preview_email=args.preview_email)

    # 恢复上下文
    if args.context:
        try:
            ctx = json.loads(args.context)
            monitor.context.update(ctx)
        except Exception as e:
            print(f"[警告] 上下文恢复失败: {e}")

    # 命令行参数填充
    if args.project: monitor.context['project_name'] = args.project
    if args.sprint: monitor.context['sprint_filter'] = args.sprint
    if args.type: monitor.context['query_type'] = args.type
    if args.workspace: monitor.context['workspace'] = args.workspace
    if args.threshold: monitor.context['threshold'] = args.threshold
    if args.status: monitor.context['anomaly_status'] = args.status
    if args.wiki_url: monitor.context['wiki_url'] = args.wiki_url

    # 执行
    result = monitor.run(user_input=args.input)

    # 自动后续步骤
    if result.get('status') == 'success':
        # Wiki发布
        if result.get('next_step') == 'wiki_publish' and args.wiki_url:
            print("\n[自动Wiki发布]")
            wiki_result = monitor._execute_wiki_publish(args.wiki_url)
            result = {**result, **wiki_result}

        # 邮件发送（CLI 自动模式）
        if (monitor.context.get('query_type') == '3' and 
            monitor.context.get('email_choice') and 
            not monitor.context.get('email_sent')):
            print("\n[自动邮件处理]")
            choice = monitor.context['email_choice']
            recipients = []
            cc_emails = []
            if choice == 'all':
                recipients = monitor._extract_recipients_from_anomaly()
            elif choice != 'none':
                recipients = [r.strip() for r in choice.split(',')]
            if hasattr(args, 'cc') and args.cc:
                cc_emails = [r.strip() for r in args.cc.split(',')]
            if recipients:
                email_result = monitor._send_anomaly_email(recipients, cc_emails)
                result = {**result, **email_result}
            else:
                result = {**result, "message": "邮件已跳过（不发送）"}
        # 邮件发送（原有交互流程的next_step）
        elif result.get('next_step') == 'email_notify' and (args.email_yes or args.recipients):
            print("\n[自动邮件处理]")
            recipients = []
            cc_emails = []
            if args.recipients:
                recipients = [r.strip() for r in args.recipients.split(',')]
            else:
                recipients = monitor._extract_recipients_from_anomaly()
            if hasattr(args, 'cc') and args.cc:
                cc_emails = [r.strip() for r in args.cc.split(',')]
            email_result = monitor._send_anomaly_email(recipients, cc_emails)
            result = {**result, **email_result}

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
