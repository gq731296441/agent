#!/usr/bin/env python3
"""
工作量数据监控技能 - 主入口
提供UMOONE工作区的工作项数据查询、统计分析、异常监控和报告生成
"""

import sys
import os
import json
import argparse
from datetime import datetime
from pathlib import Path

# 添加技能目录到路径
skill_dir = Path(__file__).parent.parent
sys.path.insert(0, str(skill_dir))

from workload_analyzer import WorkloadAnalyzer
from wiki_publisher import WikiPublisher
from email_notifier import EmailNotifier
from skill_paths import resolve_report_dir

class WorkloadMonitor:
    def __init__(self):
        self.context = {
            'workspace': 'UMEONE',
            'project_name': None,
            'sprint_filter': None,
            'query_type': None,
            'threshold': 30,
            'anomaly_status': 'closed'
        }
        self.analyzer = WorkloadAnalyzer()

    def run(self, user_input: str = None, context_messages: list = None):
        """主运行入口"""
        # 简化的状态机
        if self.context['project_name'] is None:
            return self._ask_for_prerequisites(user_input)
        elif self.context['query_type'] is None:
            return self._ask_for_query_type(user_input)
        elif self.context['query_type'] in ['1', '2', '3', '4']:
            return self._execute_query(user_input)
        else:
            return {"status": "error", "message": "未知的查询类型"}

    def _ask_for_prerequisites(self, user_input):
        return {
            "status": "input_required",
            "prompt": "请提供以下信息：\n1. 项目名称（如：ZENIC ONE-UME R22 V16.26.20）\n2. 查询迭代（如：sprint08,sprint09 或 5月）\n格式：项目名称|迭代名称",
            "context": self.context
        }

    def _ask_for_query_type(self, user_input):
        options = {
            '1': '任务数目（表1、表2）',
            '2': '任务工作量（表3、表4）',
            '3': '异常数据（表5、表6）',
            '4': '生成项目级报告（全部表格 + Wiki发布）'
        }
        return {
            "status": "input_required",
            "prompt": "请选择查询内容：\n" + "\n".join([f"{k}. {v}" for k, v in options.items()]),
            "options": options,
            "context": self.context
        }

    def _execute_query(self, user_input):
        query_type = self.context['query_type']

        # 执行数据分析
        result = self.analyzer.analyze(
            workspace=self.context['workspace'],
            sprint_filter=self.context['sprint_filter'],
            threshold=self.context['threshold'],
            anomaly_status=self.context['anomaly_status']
        )

        if not result['success']:
            return {"status": "error", "message": result.get('error', '查询失败')}

        # 组合markdown输出
        output_lines = []
        if query_type in ['1', '4']:
            output_lines.append("## 一、团队任务数分析\n")
            output_lines.append("### 表1：各团队任务统计（任务数）\n")
            output_lines.append(result['table1'])
            output_lines.append("\n### 表2：各团队任务完成比例（%）\n")
            output_lines.append(result['table2'])

        if query_type in ['2', '4']:
            output_lines.append("## 二、团队工作量分析\n")
            output_lines.append("### 表3：各团队工作量统计（人天）\n")
            output_lines.append(result['table3'])
            output_lines.append("\n### 表4：各团队工作量占比（%）\n")
            output_lines.append(result['table4'])

        if query_type in ['3', '4']:
            output_lines.append("## 三、异常数据分析\n")
            output_lines.append("### 表5：预计工作量数据异常表\n")
            output_lines.append(result['table5'])
            output_lines.append("\n### 表6：实际工作量数据异常统计\n")
            output_lines.append(result['table6'])

        markdown_output = "\n".join(output_lines)

        # 保存结果
        output_file = os.path.join(
            resolve_report_dir(),
            f"workload_report_{self.context['sprint_filter'].replace(' ', '_')}.md",
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
            "anomaly_dfs_exists": 'anomaly_dfs' in result
        }

        if query_type == '4':
            response["next_step"] = "wiki_publish"
            response["prompt"] = "请提供要发布到的Wiki页面URL（父页面链接）"

        elif query_type == '3':
            response["next_step"] = "email_notify"
            response["prompt"] = "是否给异常数据中的责任人发送提醒邮件？\n回复：1.是（全部责任人） 2.否 3.指定收件人"

        return response


def main():
    parser = argparse.ArgumentParser(description='工作量数据监控')
    parser.add_argument('--input', type=str, help='用户输入')
    parser.add_argument('--context', type=str, help='上下文JSON')
    parser.add_argument('--project', type=str, help='项目名称')
    parser.add_argument('--sprint', type=str, help='迭代筛选 (如 sprint08,sprint09)')
    parser.add_argument('--type', type=str, choices=['1','2','3','4'], help='查询类型')
    parser.add_argument('--threshold', type=int, default=30, help='异常阈值')
    parser.add_argument('--status', type=str, default='closed', help='实际工作量异常状态')
    parser.add_argument('--wiki-url', type=str, help='Wiki父页面URL (type=4时使用)')
    parser.add_argument('--email-yes', action='store_true', help='确认发送邮件 (type=3时使用)')
    parser.add_argument('--recipients', type=str, help='邮件收件人，逗号分隔 (type=3时使用)')
    args = parser.parse_args()

    monitor = WorkloadMonitor()

    # 恢复上下文
    if args.context:
        try:
            ctx = json.loads(args.context)
            monitor.context.update(ctx)
        except:
            pass

    # 命令行参数填充
    if args.project: monitor.context['project_name'] = args.project
    if args.sprint: monitor.context['sprint_filter'] = args.sprint
    if args.type: monitor.context['query_type'] = args.type
    if args.threshold: monitor.context['threshold'] = args.threshold
    if args.status: monitor.context['anomaly_status'] = args.status

    # 检查必需参数
    required = [('project_name', '--project'), ('sprint_filter', '--sprint'), ('query_type', '--type')]
    missing = [name for name, arg in required if not monitor.context.get(name)]
    if missing:
        print(json.dumps({
            "status": "input_required",
            "missing_params": missing,
            "prompt": f"请提供以下参数: {', '.join([arg for name, arg in required if name in missing])}"
        }, ensure_ascii=False))
        sys.exit(1)

    # 执行查询
    result = monitor.run(user_input=args.input)

    # 自动后续步骤
    if result.get('status') == 'success':
        # Wiki发布
        if result.get('next_step') == 'wiki_publish' and args.wiki_url:
            print("\n[自动Wiki发布]")
            publisher = WikiPublisher()
            try:
                wiki_result = publisher.publish(
                    content=result['markdown'],
                    title=monitor.context['project_name'],
                    parent_url=args.wiki_url,
                    project_name=monitor.context['project_name']
                )
                result['wiki_result'] = wiki_result
                if wiki_result.get('success'):
                    result['wiki_link'] = wiki_result.get('page_link')
                    print(f"✅ Wiki页面已创建: {wiki_result.get('page_link')}")
                else:
                    print(f"❌ Wiki发布失败: {wiki_result.get('error')}")
            except Exception as e:
                result['wiki_error'] = str(e)
                print(f"❌ Wiki发布异常: {e}")

        # 邮件发送
        elif result.get('next_step') == 'email_notify' and (args.email_yes or args.recipients):
            print("\n[自动邮件发送]")
            # 获取异常数据
            anomaly_dfs = None
            if 'anomaly_dfs' in result:
                # WorkloadAnalyzer返回的anomaly_dfs包含DataFrame，但在JSON序列化时不能直接传递
                # 需要重新读取导出文件来获取
                anomaly_dfs = None
            # 重新查询异常数据
            analyzer2 = WorkloadAnalyzer()
            log_dir = os.path.join(analyzer2.workitem_skill_dir, "logs")
            if os.path.exists(log_dir):
                files = sorted([f for f in os.listdir(log_dir) if f.startswith('export_work_item_') and f.endswith('.xlsx')], reverse=True)
                if files:
                    export_path = os.path.join(log_dir, files[0])
                    try:
                        df = analyzer2._load_data(export_path)
                        df = analyzer2._preprocess_data(df)
                        anomaly_dfs = analyzer2.get_anomaly_details(df, monitor.context['threshold'], monitor.context['anomaly_status'])
                    except Exception as e:
                        print(f"❌ 读取异常数据失败: {e}")

            if anomaly_dfs:
                notifier = EmailNotifier()
                to_emails = []
                if args.recipients:
                    to_emails = [r.strip() for r in args.recipients.split(',')]
                else:
                    to_emails = ['zhuang.jingwen@zte.com.cn']

                estimated_table = notifier.format_anomaly_table(
                    anomaly_dfs['estimated_anomaly'],
                    '预计工作量',
                )
                actual_table = notifier.format_anomaly_table(
                    anomaly_dfs['actual_anomaly'],
                    '实际工作量',
                )

                body = f"各位好，内文为{monitor.context['project_name']}工作量数据异常任务，请各位及时治理，谢谢！\n\n" + estimated_table + "\n" + actual_table + "\n--\n数据来源：UMEONE工作区\n统计时间：" + datetime.now().strftime('%Y-%m-%d')

                # 为避免误发，这里仅打印邮件内容预览和收件人信息
                print(f"📧 邮件预览:")
                print(f"收件人: {', '.join(to_emails)}")
                print(f"标题: 各位好，内文为{monitor.context['project_name']}+工作量数据异常任务，请各位及时治理，谢谢！")
                print("正文预览（前500字符）:")
                print(body[:500] + "...")
                print("\n⚠ 邮件未实际发送。如需发送，请使用oa-zmail-manage技能或取消代码中的安全限制。")
                result['email_preview'] = {
                    'to': to_emails,
                    'body_preview': body[:500] + '...',
                    'subject': f"各位好，内文为{monitor.context['project_name']}+工作量数据异常任务，请各位及时治理，谢谢！"
                }
            else:
                print("⚠ 无异常数据，跳过邮件发送")

    # 输出结果
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
