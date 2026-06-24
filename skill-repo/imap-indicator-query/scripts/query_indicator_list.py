"""
查询指示器列表
获取IMAP系统中所有可用的指标（指示器）信息
"""

import requests
from typing import Dict, Any, Optional
from auth import get_auth_headers, AuthManager


def query_indicator_list(keyword: str = "", yxdata_token: str = None, 
                        auth_token: str = None, empno: str = None) -> Dict[str, Any]:
    """
    查询指示器列表，可按名称筛选
    
    Args:
        keyword: 指示器名称关键词，为空则返回所有
        yxdata_token: yxdata-token（可选）
        auth_token: 原始认证token（可选）
        empno: 用户工号（可选）
        
    Returns:
        指示器列表数据
        
    Example:
        >>> query_indicator_list("MR交付周期")
        {'status': True, 'data': {'content': [...]}}
    """
    url = "https://aip.zte.com.cn/middle/api/remote/call/f3a0787995c0a06f/detail"
    headers = get_auth_headers(yxdata_token, auth_token, empno)
    
    params = {}
    if keyword:
        params['title'] = keyword
    
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()
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


def find_indicator_by_name(indicator_name: str, yxdata_token: str = None, 
                          auth_token: str = None, empno: str = None) -> Optional[Dict[str, Any]]:
    """
    精确查找指示器
    
    Args:
        indicator_name: 指示器完整名称
        yxdata_token: yxdata-token
        auth_token: 原始认证token
        empno: 用户工号
        
    Returns:
        匹配的指示器详情，未找到返回None
    """
    result = query_indicator_list(indicator_name, yxdata_token, auth_token, empno)
    
    if not result.get('status'):
        return None
    
    for indicator in result.get('data', {}).get('content', []):
        if indicator.get('指示器名称') == indicator_name:
            return indicator
    
    return None


def fuzzy_find_indicator(keyword: str, yxdata_token: str = None, 
                        auth_token: str = None, empno: str = None) -> list:
    """
    模糊查找指示器
    
    Args:
        keyword: 关键词
        yxdata_token: yxdata-token
        auth_token: 原始认证token
        empno: 用户工号
        
    Returns:
        匹配的指示器列表（按匹配度排序）
    """
    result = query_indicator_list("", yxdata_token, auth_token, empno)
    
    if not result.get('status'):
        return []
    
    all_indicators = result.get('data', {}).get('content', [])
    matches = []
    
    for indicator in all_indicators:
        name = indicator.get('指示器名称', '')
        if keyword.lower() in name.lower():
            # 计算匹配度（简单的前缀匹配优于中间匹配）
            if name.startswith(keyword):
                score = 100
            elif keyword in name:
                score = 50
            else:
                score = 10
            matches.append((score, indicator))
    
    # 按匹配度排序
    matches.sort(key=lambda x: x[0], reverse=True)
    return [indicator for _, indicator in matches]


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
        print("测试查询指示器列表（带关键词）:")
        result = query_indicator_list("MR交付周期",yxdata_token=yxdata_token, auth_token=token, empno=empno)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        
        print("\n测试查找指示器:")
        indicator = find_indicator_by_name("MR交付周期（天）",yxdata_token=yxdata_token, auth_token=token, empno=empno)
        if indicator:
            print(f"找到指示器: {indicator.get('指示器名称')}")
            print(f"API地址: {indicator.get('api_接口地址')}")