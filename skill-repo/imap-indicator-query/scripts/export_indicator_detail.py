#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
指标详单导出工具

用法:
  python export_indicator_detail.py "<指标查询语句>" [--export-excel]

示例:
  python export_indicator_detail.py "系统测试缺陷关闭占比项目组ZXROSNG" --export-excel
"""

import os
import sys
import json
import re
import argparse
import logging

# 将脚本目录加入路径，以便导入同级模块
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

# 导入 quick_query, 注意它需要 empno 和 ***
try:
    from main import quick_query
except ImportError as e:
    print(f"导入 main 模块失败: {e}")
    print("请确保在当前虚拟环境或正确路径下运行。")
    sys.exit(1)

import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def export_excel(data_dict: dict, filename: str):
    """导出数据到Excel，多个sheet"""
    try:
        import pandas as pd
        from datetime import datetime

        safe_name = re.sub(r'[^\w\u4e00-\u9fa5]', '_', filename)[:50]
        filepath = os.path.join(os.getcwd(), f"{safe_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")

        with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
            # 数据统计sheet
            for key, val in data_dict.items():
                if isinstance(val, dict) and val.get('data'):
                    df = pd.DataFrame(val['data'])
                    sheet_name = key[:31]  # Excel sheet name 最长31字符
                    df.to_excel(writer, sheet_name=sheet_name, index=False)

            # 汇总信息sheet
            summary = []
            for key, val in data_dict.items():
                if isinstance(val, dict):
                    summary.append({
                        '类型': key,
                        '字段': val.get('field', ''),
                        '记录数': val.get('count', 0),
                        'URL': val.get('url', '')
                    })
            if summary:
                pd.DataFrame(summary).to_excel(writer, sheet_name='导出信息', index=False)

        logger.info(f"✅ Excel文件已生成: {filepath}")
        return filepath
    except ImportError:
        logger.error("❌ 未安装 pandas 和 openpyxl，无法导出Excel。请运行: pip install pandas openpyxl")
        return None
    except Exception as e:
        logger.error(f"❌ 导出Excel失败: {e}")
        return None


def fetch_detail_data(detail_config: dict, dimension_param: str, dimension_id: str, empno: str = None, token: str = None):
    """根据detail_config获取详单数据
    
    Args:
        detail_config: 详单配置字典
        dimension_param: 维度参数名
        dimension_id: 维度ID
        empno: 工号（从环境变量EMPNO获取）
        token: 认证令牌（从环境变量***获取）
    """
    # 准备认证头（两步认证）
    headers = {}
    _empno = empno or os.getenv('EMPNO') or os.getenv('coclaw_empno')
    _token = token or os.getenv('***') or os.getenv('coclaw_token')
    if _empno:
        headers['X-Emp-No'] = _empno
    if _token:
        headers['X-Auth-Value'] = _token
    try:
        from auth import AuthManager
        if _empno and _token:
            auth_mgr = AuthManager()
            yxdata_token = auth_mgr.get_token(_empno, _token)
            headers['yxdata-token'] = yxdata_token
    except Exception as e:
        logger.warning(f"获取认证令牌失败，详单请求可能无权限: {e}")
    
    results = {}

    def fetch_one(field_name: str, api_url: str):
        try:
            resp = requests.get(
                api_url,
                headers=headers,
                params={dimension_param: dimension_id, 'pageSize': 1000},
                timeout=30
            )
            resp.raise_for_status()
            result = resp.json()

            data = []
            if isinstance(result, dict):
                if result.get('status') is True or result.get('code') == 200:
                    data = result.get('data', [])
                    if isinstance(data, dict) and 'content' in data:
                        data = data['content']
            elif isinstance(result, list):
                data = result

            return {
                'field': field_name,
                'url': f"{api_url}?{dimension_param}={dimension_id}",
                'count': len(data),
                'data': data
            }
        except Exception as e:
            logger.error(f"获取详单失败[{field_name}]: {e}")
            return None

    return results


def process_export(query: str, export_excel: bool = False, empno=None, token=None):
    """处理导出请求
    
    Args:
        query: 自然语言查询
        export_excel: 是否导出Excel
        empno: 工号（可选，缺省则从环境变量EMPNO获取）
        token: 认证令牌（可选，缺省则从环境变量***获取）
    """
    logger.info(f"🔍 查询: {query}")

    # 注入环境变量供 quick_query 使用
    if empno:
        os.environ['EMPNO'] = empno
    if token:
        os.environ['***'] = token

    # 1. 调用主查询（使用快速查询）
    result = quick_query(query)

    if not result.get('status'):
        logger.error(f"❌ 查询失败: {result.get('error')}")
        return result

    indicator = result.get('indicator', {})
    detail_info_raw = indicator.get('detail_info')
    raw_metadata = indicator.get('raw_metadata', {})
    detail_info = detail_info_raw or raw_metadata.get('详单信息')

    if not detail_info:
        logger.warning("❌ 该指标无可用的详单配置")
        result['detail_data'] = {}
        result['excel_export'] = {'available': False, 'reason': '无可用的详单配置'}
        return result

    # 解析detail_info
    if isinstance(detail_info, str):
        try:
            detail_config = json.loads(detail_info)
        except json.JSONDecodeError:
            logger.error("❌ 详单配置JSON解析失败")
            return result
    elif isinstance(detail_info, dict):
        detail_config = detail_info
    else:
        logger.warning("❌ 详单配置格式异常")
        return result

    # 获取维度参数
    qc = result.get('query_condition', {})
    dimension_param = qc.get('param', 'projectGroup_id')
    dimension_id = qc.get('id', '')

    if not dimension_id:
        logger.warning("❌ 缺少维度ID参数")
        return result

    # 构造认证 headers（根据技能要求添加 yxdata-token, X-Auth-Value, X-Emp-No）
    headers = {}
    _empno = empno or os.getenv('EMPNO') or os.getenv('coclaw_empno')
    _token = token or os.getenv('***') or os.getenv('coclaw_token')
    if _empno:
        headers['X-Emp-No'] = _empno
    if _token:
        headers['X-Auth-Value'] = _token
    yxdata_token = os.getenv('coclaw_yxdata_token')
    if not yxdata_token:
        try:
            from auth import AuthManager
            auth_mgr = AuthManager()
            yxdata_token = auth_mgr.get_token(_empno, _token) if _empno and _token else None
        except Exception:
            yxdata_token = None
    if yxdata_token:
        headers['yxdata-token'] = yxdata_token

    # 确定字段：率值类还是数量类
    indicator_type = indicator.get('indicator_type', [])
    indicator_name = indicator.get('name', '')
    y_axis = indicator.get('y_axis', '')

    is_ratio = False
    if isinstance(indicator_type, str) and ('率值' in indicator_type or '占比' in indicator_type):
        is_ratio = True
    if '占比' in indicator_name or '率' in indicator_name:
        is_ratio = True

    def fetch_one(field_name: str):
        info = detail_config.get(field_name)
        if not info or not isinstance(info, dict):
            return None
        api_url = info.get('all_api') or info.get('association_api')
        if not api_url:
            return None
        try:
            resp = requests.get(api_url, headers=headers, params={dimension_param: dimension_id, 'pageSize': 1000}, timeout=30)
            resp.raise_for_status()
            res_json = resp.json()
            data = []
            if isinstance(res_json, dict):
                if res_json.get('status') is True or res_json.get('code') == 200:
                    data = res_json.get('data', [])
                    if isinstance(data, dict) and 'content' in data:
                        data = data['content']
            elif isinstance(res_json, list):
                data = res_json

            return {
                'field': field_name,
                'url': f"{api_url}?{dimension_param}={dimension_id}",
                'count': len(data),
                'data': data
            }
        except Exception as e:
            logger.error(f"详单获取失败[{field_name}]: {e}")
            return None

    detail_data = {}

    if is_ratio:
        # 率值类：分子分母
        y_fields = [f.strip() for f in y_axis.split(',')] if y_axis else []
        numerator_name = y_fields[0] if len(y_fields) >= 2 else None
        denominator_name = y_fields[1] if len(y_fields) >= 2 else None

        if not numerator_name or not denominator_name:
            keys = list(detail_config.keys())
            for k in keys:
                if any(kw in k for kw in ['关闭', '完成', '达标', '交付']):
                    numerator_name = k
                    break
            for k in keys:
                if any(kw in k for kw in ['发现', '新增', '总数']) and k != numerator_name:
                    denominator_name = k
                    break

        if numerator_name and numerator_name in detail_config:
            detail_data['numerator'] = fetch_one(numerator_name)
        if denominator_name and denominator_name in detail_config:
            detail_data['denominator'] = fetch_one(denominator_name)
    else:
        # 数量/周期类：匹配指标名称
        main_field = None
        clean_name = re.sub(r'[（()）]', '', indicator_name).lower().replace(' ', '')
        scores = {}
        for field in detail_config.keys():
            score = 0
            f_low = field.lower()
            if clean_name in f_low or f_low in clean_name:
                score += 100
            keywords = ['mr', 'pr', 'us', '交付周期', '吞吐量', '缺陷', '关闭', '发现']
            for kw in keywords:
                if kw in clean_name and kw in f_low:
                    score += 50
            scores[field] = score
        if scores:
            main_field = max(scores, key=scores.get)
            if scores[main_field] > 0:
                detail_data['main'] = fetch_one(main_field)

    result['detail_data'] = detail_data

    # Excel导出
    if detail_data and export_excel:
        excel_path = export_excel(detail_data, indicator.get('name', '指标详情'))
        if excel_path:
            result['excel_export'] = {
                'available': True,
                'filename': os.path.basename(excel_path),
                'path': excel_path,
                'sheets': list(detail_data.keys())
            }
        else:
            result['excel_export'] = {'available': False, 'reason': '导出失败'}
    else:
        result['excel_export'] = None

    return result


def main():
    parser = argparse.ArgumentParser(description='IMAP指标详单导出工具')
    parser.add_argument('query', help='指标查询语句（如："系统测试缺陷关闭占比项目组ZXROSNG"）')
    parser.add_argument('--export-excel', action='store_true', help='导出Excel文件')
    parser.add_argument('--empno', help='工号（可选，默认从环境变量EMPNO读取）')
    parser.add_argument('--token', help='认证令牌（可选，默认从环境变量***读取）')
    args = parser.parse_args()

    result = process_export(args.query, export_excel=args.export_excel, empno=args.empno, token=args.token)

    # 输出结果摘要
    print("\n" + "="*60)
    print("📊 查询结果摘要")
    print("="*60)
    print(f"指标名称: {result.get('indicator', {}).get('name', 'N/A')}")
    print(f"查询条件: {result.get('query_condition', {}).get('name', 'N/A')}")
    print(f"统计数据量: {result.get('data', {}).get('count', 0)} 条")

    if result.get('detail_data'):
        print("\n📋 详单数据:")
        for key, val in result['detail_data'].items():
            if val:
                print(f"  - {key}: {val.get('count', 0)} 条")
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
            print(f"\n⚠️ Excel导出不可用: {exp.get('reason', '未知原因')}")

    print("\n输出完整JSON结果到控制台...")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
