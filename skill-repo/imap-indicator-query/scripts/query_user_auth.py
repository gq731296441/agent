"""
查询用户权限
获取当前用户在各级别(分中心级、部门级、项目组级)的访问权限
"""

import requests
from typing import Dict, Any, Optional, List
from auth import get_auth_headers, AuthManager


def query_user_auth(empno: str, yxdata_token: str = None,
                   auth_token: str = None) -> Dict[str, Any]:
    """
    查询用户权限
    
    Args:
        empno: 用户工号
        yxdata_token: yxdata-token（可选）
        auth_token: 原始认证token（可选）
        
    Returns:
        用户权限列表
        
    Example:
        >>> query_user_auth("12345678")
        {'status': True, 'data': {'content': [
            {'auth_type': '分中心级', 'key_name': 'FM产品研发中心', ...},
            {'auth_type': '部门级', 'key_name': 'FM开发二部', ...},
            {'auth_type': '项目组级', 'key_name': 'BVB', ...}
        ]}}
    """
    url = "https://aip.zte.com.cn/middle/api/remote/call/e98f8b3f7d733935/detail"
    headers = get_auth_headers(yxdata_token, auth_token, empno)
    
    params = {'empno': empno,'pageSize':'0'}
    
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


def check_permission(auth_data: Dict[str, Any], query_type: str,
                    query_name: str) -> bool:
    """
    检查用户是否有权限访问指定的资源

    Args:
        auth_data: 用户权限数据(query_user_auth的返回值)
        query_type: 查询类型(项目组/部门/中心/分中心级)
        query_name: 查询的资源名称

    Returns:
        是否有权限
    """
    if not auth_data.get('status'):
        return False

    content = auth_data.get('data', {}).get('content', [])

    # 类型映射
    type_mapping = {
        '项目组': '项目组级',
        '部门': '部门级',
        '中心': '中心级',
        '分中心级': '分中心级',
        '院': '院级'
    }

    expected_auth_type = type_mapping.get(query_type, query_type)

    for item in content:
        if item.get('auth_type') == expected_auth_type and item.get('key_name') == query_name:
            return True

    return False


def get_user_auth_types(auth_data: Dict[str, Any]) -> Dict[str, List[str]]:
    """
    提取用户各类权限的列表

    Args:
        auth_data: 用户权限数据

    Returns:
        按级别分类的权限名称列表
    """
    result = {
        '分中心级': [],
        '部门级': [],
        '项目组级': [],
        '中心级': [],
        '院级': []
    }

    if not auth_data.get('status'):
        return result

    for item in auth_data.get('data', {}).get('content', []):
        auth_type = item.get('auth_type')
        key_name = item.get('key_name')
        if auth_type and key_name and auth_type in result:
            result[auth_type].append(key_name)

    return result


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
        print(f"查询用户 {empno} 的权限:")
        result = query_user_auth(empno,yxdata_token=yxdata_token, auth_token=token)
        print(json.dumps(result, ensure_ascii=False, indent=2))

        if result.get('status'):
            auth_summary = get_user_auth_types(result)
            print("\n权限汇总:")
            for level, names in auth_summary.items():
                if names:
                    print(f"  {level}: {', '.join(names)}")