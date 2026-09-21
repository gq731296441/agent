#!/usr/bin/env python
"""
根据关键字搜索 ZTE iCenter 人员

功能：
- 根据关键字（姓名、工号等）搜索人员
- 返回匹配的人员列表，包括详细信息

使用方式：
    python user_search.py --emp-no "工号" --token "令牌" --keyword "王伟"
"""
import requests
import json
import sys
import argparse
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_ROOT = SCRIPT_DIR.parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))
from common.log_helper import safe_print
from common.rdcloud_api import URL_ICENTER_USER_PLAIN_SEARCH

API_URL = URL_ICENTER_USER_PLAIN_SEARCH
TIMEOUT = 15

def search_users(emp_no: str, token: str, keyword: str):
    """
    根据关键字搜索人员
    
    Args:
        emp_no: 查询人的工号
        token: 认证令牌
        keyword: 搜索关键字（姓名、工号等）
        
    Returns:
        dict: 包含搜索结果的字典
    """
    headers = {
        'Accept': 'application/json, text/plain',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'Content-Type': 'application/json;charset=UTF-8',
        'Origin': 'https://i.zte.com.cn',
        'Pragma': 'no-cache',
        'Referer': 'https://i.zte.com.cn/',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-site',
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
        'X-Auth-Value': token,
        'X-Emp-No': emp_no,
        'X-Lang-Id': 'zh_CN'
    }
    
    payload = {
        "employeeShortId": emp_no,
        "enableLabel": True,
        "keyword": keyword
    }
    
    try:
        response = requests.post(API_URL, headers=headers, json=payload, timeout=TIMEOUT)
        response.raise_for_status()
        result = response.json()
        
        # 检查API返回的code
        if result.get('code', {}).get('code') == '0000' and 'bo' in result:
            users_data = []
            for user in result['bo']:
                # 提取电话号码，如果存在 contacts 数组
                phone = ''
                if user.get('contacts'):
                    for c in user['contacts']:
                        if c.get('type') == 'phone':
                            phone = c.get('value', '')
                            break
                
                user_item = {
                    "name": user.get('name', ''),              # 中文名
                    "nameEn": user.get('nameEn', ''),          # 英文名
                    "empNo": user.get('employeeNO', ''),       # 工号
                    "email": user.get('email', ''),
                    "deptName": user.get('deptName', ''),      # 部门短名称
                    "deptFullName": user.get('deptFullName', ''),  # 完整部门路径
                    "deptNameEn": user.get('deptNameEn', ''),  # 英文部门名
                    "phone": phone,                            # 电话
                    "headIcon": user.get('headIcon', ''),      # 头像 URL
                    "unionID": user.get('unionID', ''),        # 统一用户ID
                    "signature": user.get('signature', '')     # 个性签名
                }
                users_data.append(user_item)
            
            return {
                "status": "success",
                "keyword": keyword,
                "total": len(users_data),
                "users": users_data
            }
        else:
            error_msg = result.get('code', {}).get('msg', '未知错误')
            return {
                "status": "error",
                "message": error_msg,
                "response": result
            }
    
    except requests.exceptions.RequestException as e:
        return {
            "status": "error",
            "message": f"请求失败: {str(e)}"
        }
    except json.JSONDecodeError:
        return {
            "status": "error",
            "message": "API 返回的不是有效的 JSON"
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"处理失败: {str(e)}"
        }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="根据关键字搜索 ZTE iCenter 人员")
    parser.add_argument("--emp-no", required=True, help="查询人工号（必需）")
    parser.add_argument("--token", required=True, help="认证令牌（必需）")
    parser.add_argument("--keyword", required=True, help="搜索关键字（必需），如姓名、工号等")
    args = parser.parse_args()
    
    result = search_users(args.emp_no, args.token, args.keyword)
    safe_print(json.dumps(result, ensure_ascii=False, indent=2))

