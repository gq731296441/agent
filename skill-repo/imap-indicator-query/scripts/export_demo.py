#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sys
import os
import json

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

# 设置环境变量认证（请改为真实值）
os.environ['EMPNO'] = '00005581'   # 工号
os.environ['coclaw_***'] = 'test'  # 认证令牌（注意变量名是 coclaw_***，供AuthManager使用）

from main import ImapQueryOrchestrator

# 调用详单导出处理器（不显式传递第二个参数，由环境变量提供）
orchestrator = ImapQueryOrchestrator(empno='00005581')
params = {
    'indicator_name': 'MR交付周期（天）',
    'query_type': '项目组',
    'query_name': 'ZXROSNG',
    'export_excel': True
}
result = orchestrator._handle_query_indicator_detail('', params)

print("\n=== 导出结果 ===")
print(f"状态: {result.get('status')}")
print(f"消息: {result.get('message', result.get('error', 'N/A'))}")
indicator = result.get('indicator', {})
print(f"指标: {indicator.get('name', 'N/A')}")
print(f"指标类型: {indicator.get('indicator_type', [])}")

if result.get('detail_data'):
    print("\n📋 详单数据:")
    for key, val in result['detail_data'].items():
        if val:
            print(f"  - {key}: {val.get('count')} 条 (字段: {val.get('field')})")
        else:
            print(f"  - {key}: 获取失败")
else:
    print("\n❌ 未获取到详单数据")

if result.get('excel_export'):
    exp = result['excel_export']
    if exp['available']:
        print(f"\n✅ Excel已导出: {exp['filename']}")
        print(f"   路径: {exp['path']}")
    else:
        print(f"\n⚠️ Excel导出失败: {exp.get('reason')}")
else:
    print("\n⚠️ 未请求Excel导出")

print("\n=== 完整JSON（截断）===")
print(json.dumps(result, ensure_ascii=False, indent=2)[:2000] + "...")
