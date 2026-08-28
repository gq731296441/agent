"""
查询指标数据
根据指示器API地址和查询条件获取实际的指标数据
"""

import requests
from datetime import datetime
from typing import Dict, Any, Optional
from auth import get_auth_headers, AuthManager


def query_indicator_data(
    api_url: str,
    dimension_param: str,
    dimension_value: str,
    yxdata_token: str,
    auth_token: str,
    empno: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page_size: int = 2000
) -> Dict[str, Any]:
    """
    查询指标数据（自动分页，返回全量数据）
    
    Args:
        api_url: 指示器的API接口地址
        dimension_param: 查询维度参数名（如projectGroup_id、department_id）
        dimension_value: 查询维度值（ID）
        yxdata_token: yxdata-token
        auth_token: 原始认证token
        empno: 用户工号
        start_date: 开始日期（可选），格式：YYYY-MM-DD
        end_date: 结束日期（可选），格式：YYYY-MM-DD
        page_size: 每页大小，默认2000
        
    Returns:
        指标数据（包含全量content）
    """
    import time
    
    # 组装完整URL
    if api_url.endswith('/detail'):
        base_url = api_url
    else:
        base_url = f"{api_url}/detail"
    
    headers = get_auth_headers(yxdata_token, auth_token, empno)

    # 先请求第1页，获取总数和内容
    params = {
        dimension_param: dimension_value,
        'pageSize': str(page_size),
        'page': '1'
    }
    
    # 添加时间范围参数
    if start_date:
        params['startTime'] = f"{start_date} 00:00:00"
    if end_date:
        params['endTime'] = f"{end_date} 23:59:59"
    
    try:
        # 第一页
        resp = requests.get(base_url, headers=headers, params=params, timeout=60)
        resp.raise_for_status()
        result = resp.json()
        
        if not result.get('status'):
            return result
        
        data = result.get('data', {})
        if isinstance(data, dict):
            content = data.get('content', [])
            total = data.get('total', 0)
        else:
            content = data if isinstance(data, list) else []
            total = len(content)
        
        all_content = content.copy()
        
        # 如果需要翻页，则循环获取剩余页
        if total > page_size:
            total_pages = (total + page_size - 1) // page_size
            for page in range(2, total_pages + 1):
                try:
                    params['page'] = str(page)
                    resp = requests.get(base_url, headers=headers, params=params, timeout=60)
                    resp.raise_for_status()
                    page_result = resp.json()
                    if page_result.get('status'):
                        page_data = page_result.get('data', {})
                        page_content = page_data.get('content', []) if isinstance(page_data, dict) else (page_data if isinstance(page_data, list) else [])
                        all_content.extend(page_content)
                    time.sleep(0.2)  # 礼貌性延迟，避免压垮服务端
                except Exception as e:
                    # 某页失败则停止翻页，保留已获取数据
                    print(f"[Warning] 第{page}页获取失败: {e}")
                    break
        
        # 构造合并后的返回结果（保持原结构）
        result['data'] = {
            'content': all_content,
            'total': total,
            'pageSize': page_size,
            'pageCount': (total + page_size - 1) // page_size if total else 1
        }
        
        return result
        
    except requests.RequestException as e:
        return {
            "status": False,
            "error": f"网络请求失败: {str(e)}"
        }
    except ValueError as e:
        return {
            "status": False,
            "error": f"响应解析失败: {str(e)}"
        }


def format_data_for_visualization(data_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    格式化数据用于可视化展示
    
    Args:
        data_result: 查询返回的原始数据
        
    Returns:
        符合图表结构的格式化数据
    """
    if not data_result.get('status'):
        return data_result
    
    data = data_result.get('data', {})
    content = data.get('content', [])
    
    # 生成图表结构
    chart_data = {
        "status": True,
        "chart": {
            "x_field": "",
            "series_fields": [],
            "rows": content
        },
        "raw": {
            "count": len(content),
            "content": content
        }
    }
    
    if content:
        # 获取所有字段
        sample = content[0]
        fields = list(sample.keys())
        
        # 识别时间字段（x轴）
        time_field_candidates = ['周期', '时间', '日期', '月份', '年份', 'year', 'month', 'date']
        x_field = None
        
        # 优先匹配常见时间字段名
        for candidate in time_field_candidates:
            if candidate in fields:
                x_field = candidate
                break
        
        # 如果没有匹配，使用第一个看起来像时间的字段
        if not x_field:
            for field in fields:
                value = sample.get(field)
                if isinstance(value, str) and ('-' in value or '年' in value or '月' in value):
                    x_field = field
                    break
        
        # 如果还是没有，使用第一个字段
        if not x_field and fields:
            x_field = fields[0]
        
        # 提取数值字段（系列字段）
        series_fields = []
        for field in fields:
            if field == x_field:
                continue
            value = sample.get(field)
            if isinstance(value, (int, float)) or (isinstance(value, str) and value.replace('.', '').isdigit()):
                series_fields.append(field)
        
        # 如果没有检测到数值字段，使用除x_field外的所有字段
        if not series_fields:
            series_fields = [f for f in fields if f != x_field]
        
        chart_data["chart"]["x_field"] = x_field
        chart_data["chart"]["series_fields"] = series_fields
    
    return chart_data


if __name__ == "__main__":
    # 测试代码
    import os
    import json

    empno = os.getenv('coclaw_empno', '12345678')
    token = os.getenv('coclaw_token', 'xxxx')
    auth = AuthManager()
    yxdata_token = auth.get_token(empno, token)
    if not token:
        print("请设置环境变量 coclaw_token")
    else:
        # 模拟数据
        print("测试查询指标数据:")
        result = query_indicator_data(
            api_url="https://aip.zte.com.cn/middle/api/remote/call/81185dacaae0ad00",
            dimension_param="projectGroup_id",
            dimension_value="48",
            yxdata_token=yxdata_token,
            auth_token=token,
            empno=empno,
            start_date="2026-01-01",
            end_date="2026-03-31"
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))