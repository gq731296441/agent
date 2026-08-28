"""
查询用户信息
获取用户所属的部门、项目组、分中心等组织架构信息
"""

import requests
from typing import Dict, Any, Optional
from auth import get_auth_headers, AuthManager


def query_user_info(empno: str, yxdata_token: str = None,
                   auth_token: str = None) -> Dict[str, Any]:
    """
    查询用户信息
    
    Args:
        empno: 用户工号
        yxdata_token: yxdata-token（可选）
        auth_token: 原始认证token（可选）
        
    Returns:
        用户详细信息
        
    Example:
        >>> query_user_info("12345678")
        {'status': True, 'data': {'content': [{
            'employee_id': '12345678',
            'department': '软件平台开发六部',
            'projectGroup': '有线效能工具',
            'subCenter': '软件平台研发中心',
            ...
        }]}}
    """
    url = "https://aip.zte.com.cn/middle/api/remote/call/d6e24b8e49d19eda/detail"
    headers = get_auth_headers(yxdata_token, auth_token, empno)
    
    params = {'employee_id': empno,'pageSize':'0'}
    
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


def extract_user_org_info(user_info: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """
    从用户信息中提取组织架构信息

    Args:
        user_info: 用户信息数据

    Returns:
        组织信息字典,包含department, projectGroup, subCenter等
    """
    if not user_info.get('status'):
        return None

    content = user_info.get('data', {}).get('content', [])
    if not content:
        return None

    user = content[0]
    return {
        'employee_id': user.get('employee_id'),
        'name': user.get('name'),
        'department': user.get('department'),
        'projectGroup': user.get('projectGroup'),
        'subCenter': user.get('subCenter'),
        'team': user.get('team'),
        'domain': user.get('domain'),
        'departFullName': user.get('departFullName'),
        'zmail': user.get('zmail'),
        'role1': user.get('role1'),
        'role2': user.get('role2')
    }


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
        print(f"查询用户 {empno} 的信息:")
        result = query_user_info(empno,yxdata_token=yxdata_token, auth_token=token)
        print(json.dumps(result, ensure_ascii=False, indent=2))

        if result.get('status'):
            org_info = extract_user_org_info(result)
            print("\n组织信息:")
            for key, value in org_info.items():
                if value:
                    print(f"  {key}: {value}")