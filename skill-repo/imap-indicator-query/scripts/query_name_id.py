"""
查询名称对应的ID
根据类型（项目组/部门/中心等）和名称查询对应的系统ID
"""

import requests
from typing import Dict, Any, Optional
from auth import get_auth_headers, AuthManager


def query_name_to_id(query_type: str, name: str, yxdata_token: str = None, 
                    auth_token: str = None, empno: str = None) -> Dict[str, Any]:
    """
    根据类型和名称查询ID
    
    Args:
        query_type: 查询类型，如：项目组、部门、中心、分中心级
        name: 查询的名称
        yxdata_token: yxdata-token（可选）
        auth_token: 原始认证token（可选）
        empno: 用户工号（可选）
        
    Returns:
        包含ID和param字段的结果
        
    Example:
        >>> query_name_to_id("项目组", "ZXUFP")
        {'status': True, 'data': {'content': [{
            'type': '项目组',
            'id': '48',
            'name': 'ZXUFP',
            'param': 'projectGroup_id'
        }]}}
    """
    url = "https://aip.zte.com.cn/middle/api/remote/call/8ca5daa07b878eaf/detail"
    headers = get_auth_headers(yxdata_token, auth_token, empno)
    
    params = {
        'type': query_type,
        'name': name
    }
    
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


def extract_dimension_info(name_id_result: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """
    提取维度信息（ID和参数名）
    
    Args:
        name_id_result: query_name_to_id的返回值
        
    Returns:
        维度信息字典，包含dimension_value和dimension_param
    """
    if not name_id_result.get('status'):
        return None
    
    content = name_id_result.get('data', {}).get('content', [])
    if not content:
        return None
    
    item = content[0]
    return {
        'id': item.get('id'),
        'param': item.get('param'),
        'type': item.get('type'),
        'name': item.get('name')
    }


if __name__ == "__main__":
    # 测试代码
    import os
    import json
    
    token = os.getenv('coclaw_token', '')
    empno = os.getenv('coclaw_empno', '12345678')
    token = os.getenv('coclaw_token', 'xxxx')
    auth = AuthManager()
    yxdata_token = auth.get_token(empno, token)
    if not token:
        print("请设置环境变量 coclaw_token")
    else:
        print("测试查询项目组ID:")
        result = query_name_to_id("项目组", "ZXUFP", auth_token=token, empno=empno, yxdata_token=yxdata_token)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        
        if result.get('status'):
            dim_info = extract_dimension_info(result)
            if dim_info:
                print(f"\n维度信息:")
                for key, value in dim_info.items():
                    print(f"  {key}: {value}")