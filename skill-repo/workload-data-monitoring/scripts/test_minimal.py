#!/usr/bin/env python3
"""
快速测试脚本 - 使用已有数据文件
"""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(__file__))

from workload_analyzer import WorkloadAnalyzer
from skill_paths import latest_export_file, resolve_report_dir

# 使用已有的导出文件
existing_excel = latest_export_file()

if not os.path.exists(existing_excel):
    print(f"错误：找不到数据文件 {existing_excel}")
    print("请先使用studio-rdcloud-workitem技能导出数据")
    sys.exit(1)

analyzer = WorkloadAnalyzer()

# 为了不重复查询，我们临时修改_query_work_items方法直接返回已有文件
class TestAnalyzer(WorkloadAnalyzer):
    def _query_work_items(self, workspace, sprint_filter):
        return existing_excel

test_analyzer = TestAnalyzer()

print("开始数据分析...")
result = test_analyzer.analyze(
    workspace='UMEONE',
    sprint_filter='sprint08,sprint09',
    threshold=30,
    anomaly_status='closed'
)

if result['success']:
    print("✅ 分析成功")
    print(f"总任务数: {result['total_tasks']}")
    print(f"Closed任务数: {result['closed_tasks']}")
    print(f"需求交付计划任务数: {result.get('demand_implementation_planned')}")
    print(f"需求交付完成数: {result.get('demand_implementation_closed')}")

    # 检查返回的表格
    tables = ['table1', 'table2', 'table3', 'table4', 'table5', 'table6']
    for t in tables:
        if t in result:
            print(f"✓ {t} 已生成")
        else:
            print(f"✗ {t} 缺失")

    # 检查anomaly_dfs
    if 'anomaly_dfs' in result:
        est_anomaly = result['anomaly_dfs']['estimated_anomaly']
        act_anomaly = result['anomaly_dfs']['actual_anomaly']
        print(f"\n预计异常任务数: {len(est_anomaly)}")
        print(f"实际异常任务数(Closed): {len(act_anomaly)}")
    else:
        print("\n⚠ anomaly_dfs未包含在结果中")

    # 简单组合一个完整报告
    markdown = "# ZENIC ONE-UME R22 V16.26.20 5月工作量填报报告\n\n"
    markdown += "## 一、团队任务数分析\n\n" + result.get('table1', '') + "\n\n" + result.get('table2', '') + "\n\n"
    markdown += "## 二、团队工作量分析\n\n" + result.get('table3', '') + "\n\n" + result.get('table4', '') + "\n\n"
    markdown += "## 三、异常数据分析\n\n" + result.get('table5', '') + "\n\n" + result.get('table6', '') + "\n"

    output_file = os.path.join(resolve_report_dir(), "workload_report_test.md")
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(markdown)
    print(f"\n完整报告已保存: {output_file}")
else:
    print("❌ 分析失败:", result.get('error'))
