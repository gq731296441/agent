#!/usr/bin/env python3
"""
IMAP指标查询 - 快速测试脚本
验证核心功能是否正常
"""

import os
import sys
import json
from datetime import datetime

# 添加模块路径
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(current_dir, 'scripts'))

def test_imports():
    """测试导入是否正常"""
    print("🔍 测试模块导入...")
    try:
        from main import query_indicator, query_by_imap_url, query_indicator_list_export, quick_query
        from auth import AuthManager
        from query_user_auth import query_user_auth, get_user_auth_types
        print("✅ 所有模块导入成功（包括智能查询 quick_query）")
        return True
    except ImportError as e:
        print(f"❌ 导入失败: {e}")
        return False

def test_auth_flow(empno=None, token=None):
    """测试认证流程"""
    print("\n🔐 测试认证流程...")
    
    # 使用提供的认证信息或环境变量
    empno = empno or os.getenv('coclaw_empno')
    token = token or os.getenv('coclaw_token')
    
    if not empno or not token:
        print("⚠️  未提供认证信息，跳过认证测试")
        print("💡 提示: 设置环境变量 coclaw_empno 和 coclaw_token 以进行完整测试")
        return False
    
    try:
        from auth import AuthManager
        auth = AuthManager()
        yxdata_token = auth.get_token(empno, token)
        print(f"✅ 认证成功")
        print(f"   yxdata-token: {yxdata_token[:20]}... (有效期8小时)")
        return True, yxdata_token
    except Exception as e:
        print(f"❌ 认证失败: {e}")
        return False, None

def test_query_user_info(empno, yxdata_token, token):
    """测试查询用户信息"""
    print("\n👤 测试查询用户信息...")
    
    try:
        from query_user_info import query_user_info
        result = query_user_info(empno, yxdata_token, token)
        
        if result.get('status'):
            user_data = result['data']['content'][0]
            print(f"✅ 查询成功")
            print(f"   用户: {user_data['name']} ({empno})")
            print(f"   部门: {user_data['department']}")
            print(f"   项目组: {user_data['projectGroup']}")
            print(f"   分中心: {user_data['subCenter']}")
            return True
        else:
            print(f"❌ 查询失败: {result.get('error')}")
            return False
    except Exception as e:
        print(f"❌ 异常: {e}")
        return False

def test_query_user_auth(empno, yxdata_token, token):
    """测试查询用户权限"""
    print("\n🔑 测试查询用户权限...")
    
    try:
        from query_user_auth import query_user_auth, get_user_auth_types
        result = query_user_auth(empno, yxdata_token, token)
        
        if result.get('status'):
            auth_types = get_user_auth_types(result)
            print("✅ 查询成功")
            for auth_type, names in auth_types.items():
                if names:
                    print(f"   {auth_type}: {', '.join(names[:3])}" + 
                          (f" 等{len(names)}个" if len(names) > 3 else ""))
            return True
        else:
            print(f"❌ 查询失败: {result.get('error')}")
            return False
    except Exception as e:
        print(f"❌ 异常: {e}")
        return False

def test_query_indicator_list(keyword="MR交付", yxdata_token=None, token=None, empno=None):
    """测试获取指标列表"""
    print(f"\n📋 测试获取指标列表 (关键词: {keyword})...")
    
    try:
        from query_indicator_list import query_indicator_list
        result = query_indicator_list(keyword, yxdata_token, token, empno)
        
        if result.get('status'):
            indicators = result['data']['content']
            print(f"✅ 查询成功，找到 {len(indicators)} 个指标")
            for i, ind in enumerate(indicators[:3], 1):
                print(f"   {i}. {ind.get('指示器名称')}")
                print(f"     {ind.get('指示器描述', '')[:60]}...")
            if len(indicators) > 3:
                print(f"   ... 还有 {len(indicators) - 3} 个")
            return True
        else:
            print(f"❌ 查询失败: {result.get('msg')}")
            return False
    except Exception as e:
        print(f"❌ 异常: {e}")
        return False

def test_smart_query(query: str, empno=None, token=None):
    """测试智能查询（新特性）"""
    print(f"\n🤖 测试智能查询: {query}")
    
    try:
        from main import quick_query
        
        result = quick_query(query, empno=empno, token=token)
        
        if result.get('status'):
            print("✅ 智能查询成功")
            print(f"   识别意图: {result['_meta']['intent']}")
            print(f"   消息: {result.get('message')}")
            
            # 根据不同类型显示不同信息
            result_type = result.get('type')
            if result_type == 'indicator_data':
                chart = result.get('chart', {})
                print(f"   图表字段: X={chart.get('x_field')}, Y={chart.get('series_fields')}")
                print(f"   数据行数: {len(chart.get('rows', []))}")
            elif result_type == 'indicator_list':
                print(f"   指标总数: {result.get('total')}")
            elif result_type == 'user_auth':
                print(f"   权限总数: {result.get('total')}")
            
            # 打印简洁结果（可选）
            if result.get('chart', {}).get('rows'):
                print("\n   首条数据样本:")
                sample = result['chart']['rows'][0]
                for k, v in list(sample.items())[:5]:
                    print(f"      {k}: {v}")
            
            return True
        else:
            print("❌ 智能查询失败")
            print(f"   错误: {result.get('error')}")
            if result.get('suggestions'):
                print("   建议:")
                for s in result['suggestions']:
                    print(f"     - {s}")
            return False
    except Exception as e:
        print(f"❌ 异常: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_demo_query_indicator(indicator_name="MR交付周期（天）", query_type="项目组", query_name="BVB", 
                             start_date=None, end_date=None):
    """示例：完整查询流程（需要真实权限）"""
    print(f"\n🎯 测试完整查询流程...")
    print(f"   指标: {indicator_name}")
    print(f"   查询: {query_type} - {query_name}")
    
    try:
        from main import query_indicator
        
        # 使用传入的参数或默认使用环境变量
        result = query_indicator(
            indicator_name=indicator_name,
            query_type=query_type,
            query_name=query_name,
            start_date=start_date,
            end_date=end_date
        )
        
        if result.get('status'):
            print("✅ 查询成功")
            
            # 显示指标信息
            indicator = result.get('indicator', {})
            print(f"   指标名称: {indicator.get('name')}")
            print(f"   图表类型: {indicator.get('chart_type')}")
            
            # 显示查询条件
            condition = result.get('query_condition', {})
            print(f"   查询条件: {condition.get('type')} = {condition.get('name')}")
            print(f"   系统ID: {condition.get('id')}")
            
            # 显示数据统计
            data = result.get('data', {})
            count = data.get('count', 0)
            print(f"   返回记录数: {count}")
            
            # 显示图表建议
            chart = result.get('chart', {})
            if chart:
                print(f"   图表配置: X轴={chart.get('x_field')}, Y轴={chart.get('series_fields')}")
            
            # 显示首条数据
            if data.get('content'):
                first_row = data['content'][0]
                print(f"   首条数据: {list(first_row.keys())} = {list(first_row.values())}")
            
            return True
        else:
            print("❌ 查询失败")
            print(f"   错误: {result.get('error')}")
            print(f"   建议: {result.get('suggestion')}")
            return False
    except Exception as e:
        print(f"❌ 异常: {e}")
        import traceback
        traceback.print_exc()
        return False

def show_help():
    """显示帮助信息"""
    help_text = """
📊 IMAP指标查询 - 快速测试脚本 v2.0（支持智能查询）

使用方法:
    python test_quick.py [自然语言查询] [选项]
    python test_quick.py "查询ZXUFP的MR交付周期"

选项:
    --empno <工号>             指定用户工号（默认从环境变量获取）
    --token <token>           指定认证token（默认从环境变量获取）
    --test-all                执行所有可用测试（包括智能查询和传统API）
    --test-smart             仅测试智能查询（无需token，测试意图识别）
    --test-auth              仅测试认证流程
    --test-user-info         仅测试查询用户信息
    --test-user-auth         仅测试查询用户权限
    --test-indicator-list   仅测试获取指标列表
    --demo-query             示例查询（需要token）
    --help                   显示此帮助信息

智能查询示例:
    python test_quick.py "列出所有指标"
    python test_quick.py "查询ZXUFP项目组的MR交付周期" --empno 12345678
    python test_quick.py "我的权限有哪些" --empno 12345678

传统API测试示例:
    python test_quick.py --test-all --empno 12345678 --token your_token

环境变量:
    coclaw_empno             用户工号
    coclaw_token             认证token

✨ 新版本亮点:
    - 支持自然语言查询（智能识别意图和参数）
    - 一键测试意图识别（--test-smart 无需token）
    - 返回结构化的图表数据，可直接用于可视化

💡 注意: 
    - 智能查询的意图识别无需token即可测试
    - 实际数据查询需要有效的企业认证token
    - 请确保已设置 coclaw_empno 和 coclaw_token 环境变量
"""
    print(help_text)

def main():
    """主函数"""
    print("=" * 60)
    print("IMAP指标查询 - 快速测试脚本")
    print(f"当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    # 解析命令行参数
    args = sys.argv[1:]
    
    if not args or '--help' in args:
        show_help()
        return
    
    # 解析参数
    empno = None
    token = None
    test_all = False
    test_smart = False
    test_auth = False
    test_user_info = False
    test_user_auth = False
    test_indicator_list = False
    demo_query = False
    custom_query = None
    
    i = 0
    while i < len(args):
        if args[i] == '--empno' and i + 1 < len(args):
            empno = args[i + 1]
            i += 2
        elif args[i] == '--token' and i + 1 < len(args):
            token = args[i + 1]
            i += 2
        elif args[i] == '--test-all':
            test_all = True
            i += 1
        elif args[i] == '--test-smart':
            test_smart = True
            i += 1
        elif args[i] == '--test-auth':
            test_auth = True
            i += 1
        elif args[i] in ['--test-user-info', '--test-userinfo']:
            test_user_info = True
            i += 1
        elif args[i] == '--test-user-auth':
            test_user_auth = True
            i += 1
        elif args[i] == '--test-indicator-list':
            test_indicator_list = True
            i += 1
        elif args[i] == '--demo-query':
            demo_query = True
            i += 1
        elif not args[i].startswith('--'):
            # 假设这是自定义查询语句
            custom_query = args[i]
            i += 1
        else:
            i += 1
    
    # 如果只有自定义查询，直接执行
    if custom_query:
        print(f"💬 执行智能查询: {custom_query}\n")
        if not test_smart:
            test_smart = True
    
    # 如果没有明确指定测试，默认为测试所有
    if not any([test_smart, test_auth, test_user_info, test_user_auth, test_indicator_list, demo_query]):
        test_smart = test_auth = test_user_info = test_user_auth = test_indicator_list = demo_query = True
    
    # 测试1：导入检查
    if not test_imports():
        print("\n❌ 模块导入失败，请检查文件结构")
        return
    
    # 如果有自定义查询，直接执行并退出
    if custom_query:
        test_smart_query(custom_query, empno, token)
        print("\n" + "=" * 60)
        print("查询完成")
        print("=" * 60)
        return
    
    # 测试2：认证流程
    auth_success = False
    yxdata_token = None
    if test_auth:
        auth_success, yxdata_token = test_auth_flow(empno, token)
    
    # 💡 新增：测试智能查询（不需要完整认证也可以测试意图识别）
    if test_smart:
        test_smart_query("列出所有指标", empno, token)
        test_smart_query("查询ZXUFP项目组的MR交付周期", empno, token)
        test_smart_query("我的权限有哪些", empno, token)
    
    # 如果有有效token，继续其他测试
    if auth_success and yxdata_token:
        
        # 测试3：查询用户信息
        if test_user_info:
            test_query_user_info(empno, yxdata_token, token)
        
        # 测试4：查询用户权限
        if test_user_auth:
            test_query_user_auth(empno, yxdata_token, token)
        
        # 测试5：获取指标列表
        if test_indicator_list:
            test_query_indicator_list("MR交付", yxdata_token, token, empno)
        
        # 测试6：示例查询（需要用户有相应权限）
        if demo_query:
            # 尝试使用BVB项目组（通常有权限）
            test_demo_query_indicator(
                indicator_name="MR交付周期（天）",
                query_type="项目组",
                query_name="BVB"
            )
    else:
        print("\n⚠️  由于认证未通过，跳过需要token的传统API测试")
        print("💡 智能查询测试已在上一步完成（无需token即可测试意图识别）")
        print("💡 建议: 设置有效的 coclaw_empno 和 coclaw_token 环境变量进行完整测试")
    
    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)

if __name__ == "__main__":
    main()
