#!/usr/bin/env python3
"""
技能测试脚本
模拟完整运行流程
"""

import sys
import os
import json

# 添加技能目录
sys.path.insert(0, os.path.dirname(__file__))

from workload_analyzer import WorkloadAnalyzer
from wiki_publisher import WikiPublisher
from email_notifier import EmailNotifier

def test_analyzer():
    """测试数据分析器"""
    print("=" * 60)
    print("测试1: WorkloadAnalyzer数据分析")
    print("=" * 60)

    analyzer = WorkloadAnalyzer()

    # 模拟参数
    workspace = 'UMEONE'
    sprint_filter = 'sprint08,sprint09'  # 5月对应迭代
    threshold = 30
    anomaly_status = 'closed'

    result = analyzer.analyze(
        workspace=workspace,
        sprint_filter=sprint_filter,
        threshold=threshold,
        anomaly_status=anomaly_status
    )

    if result['success']:
        print("✅ 数据分析成功")
        print(f"总任务数: {result['total_tasks']}")
        print(f"Closed任务数: {result['closed_tasks']}")
        print(f"需求交付计划任务数: {result['demand_implementation_planned']}")
        print(f"需求交付完成数: {result['demand_implementation_closed']}")
        print(f"输出文件: {result.get('output_file')}")
        return result
    else:
        print("❌ 数据分析失败:", result.get('error'))
        return None

def test_wiki_publisher(content: str, project_name: str):
    """测试Wiki发布器（不实际执行，仅模拟）"""
    print("\n" + "=" * 60)
    print("测试2: WikiPublisher（模拟，不实际发布）")
    print("=" * 60)

    publisher = WikiPublisher()
    test_url = "https://i.zte.com.cn/index/ispace/#/space/09726afd250f479a915ebb26c17cded5/wiki/page/5dc78949565611f185572b17345bf18e/view"

    print(f"测试URL解析: {test_url}")
    try:
        parsed = publisher.parse_wiki_url(test_url)
        print(f"✅ 解析成功: space_id={parsed['space_id']}, parent_id={parsed['parent_page_id']}")
    except Exception as e:
        print(f"❌ URL解析失败: {e}")

    print("\n模拟发布内容（截断预览）:")
    print(content[:500] + "...\n")

    return True

def test_email_notifier(anomaly_df_estimated, anomaly_df_actual, project_name: str):
    """测试邮件通知器"""
    print("\n" + "=" * 60)
    print("测试3: EmailNotifier（模拟，不实际发送）")
    print("=" * 60)

    notifier = EmailNotifier()

    # 测试搜索用户
    print("测试搜索用户: 庄静文")
    user = notifier.search_user("庄静文00351023")
    if 'error' not in user and 'multiple' not in user:
        print(f"✅ 找到用户: {user.get('N')} ({user.get('UNO')}) <{user.get('E')}>")
    else:
        print(f"⚠️ 用户搜索结果: {user}")

    # 生成异常表格预览
    estimated_table = notifier.format_anomaly_table(anomaly_df_estimated, '预计工作量')
    actual_table = notifier.format_anomaly_table(anomaly_df_actual, '实际工作量')

    print("\n邮件正文预览:")
    print(estimated_table)
    print(actual_table)

    return True

def main():
    """主测试流程"""
    print("开始测试『工作量数据监控』技能\n")

    # 1. 测试数据分析
    analysis_result = test_analyzer()

    if not analysis_result:
        print("\n⚠️ 分析失败，后续测试使用模拟数据")
        return

    # 2. 测试Wiki发布（预览）
    full_content = ""
    if 'markdown' in analysis_result:
        full_content = analysis_result['markdown']
    test_wiki_publisher(full_content, analysis_result.get('project_name', 'PROJECT'))

    # 3. 测试邮件通知（预览）
    if 'anomaly_dfs' in analysis_result:
        anomaly_dfs = analysis_result['anomaly_dfs']
        test_email_notifier(
            anomaly_dfs.get('estimated_anomaly', pd.DataFrame()),
            anomaly_dfs.get('actual_anomaly', pd.DataFrame()),
            'ZENIC ONE-UME R22 V16.26.20'
        )

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)

if __name__ == '__main__':
    main()
