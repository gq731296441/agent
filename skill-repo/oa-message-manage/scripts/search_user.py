#!/usr/bin/env python3
"""
根据关键字搜索 ZTE iCenter 人员

功能：
- 根据关键字（姓名、工号等）搜索人员
- 返回匹配的人员列表，包括详细信息

使用方式：
    python search_user.py --keyword "张三"
"""
import os
import urllib3
import requests
import json
import logging
from logging.handlers import RotatingFileHandler
from typing import Dict, Any

from util import get_api_base_url

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import sys
sys.stdout.reconfigure(encoding='utf-8')
import argparse

_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
if not os.path.exists(_log_dir):
    os.makedirs(_log_dir)

_log_file = os.path.join(_log_dir, "search_user.log")
_file_handler = RotatingFileHandler(
    _log_file,
    maxBytes=20 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
_file_handler.setLevel(logging.DEBUG)
_formatter = logging.Formatter("%(asctime)s - [%(name)s] - %(levelname)s - %(message)s")
_file_handler.setFormatter(_formatter)

_console_handler = logging.StreamHandler(sys.stderr)
_console_handler.setLevel(logging.DEBUG)
_console_handler.setFormatter(_formatter)

_app_logger = logging.getLogger("search_user")
_app_logger.setLevel(logging.DEBUG)
_app_logger.propagate = False
_app_logger.addHandler(_file_handler)
_app_logger.addHandler(_console_handler)

API_URL = f"{get_api_base_url()}/zte-km-icenter-addresearch/user/plain/docs/search"
TIMEOUT = 15

def search_users(emp_no: str, token: str, keyword: str) -> Dict[str, Any]:
    """
    根据关键字搜索 ZTE iCenter 人员。

    Args:
        emp_no: 查询人的工号
        token:  认证令牌
        keyword: 搜索关键字（姓名、工号等）

    Returns:
        包含搜索结果的字典：
        - status: "success" 或 "error"
        - keyword: 搜索关键字
        - total: 匹配人数
        - users: 用户列表（包含 name、nameEn、empNo、deptName、deptFullName）
        - message: 错误信息（仅 error 时）
        - response: 原始响应（仅 error 时）
    """
    headers = {
        'Content-Type': 'application/json;charset=UTF-8',
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
        response = requests.post(
            API_URL,
            headers=headers,
            json=payload,
            timeout=TIMEOUT,
            proxies={"http": None, "https": None},
            verify=False,
        )
        response.raise_for_status()
        result = response.json()
        
        # 检查API返回的code
        if result.get('code', {}).get('code') == '0000' and 'bo' in result:
            users_data = []
            for user in result['bo']:
                user_item = {
                    "name": user.get('name', ''),              # 中文名
                    "nameEn": user.get('nameEn', ''),          # 英文名
                    "empNo": user.get('employeeNO', ''),       # 工号
                    "deptName": user.get('deptName', ''),      # 部门短名称
                    "deptFullName": user.get('deptFullName', ''),  # 完整部门路径
                }
                users_data.append(user_item)
            
            # 如果结果数量大于1，进行二次精确匹配过滤
            # 已按需求关闭精确匹配，保留API原始返回结果。
            # if len(users_data) > 1:
            #     filtered_users = []
            #     keyword_lower = keyword.lower()  # 转换为小写以便不区分大小写匹配
            #
            #     for user in users_data:
            #         name = user.get('name', '')
            #         name_en = user.get('nameEn', '')
            #
            #         # 检查中文名或英文名是否包含关键字（不区分大小写）
            #         if keyword_lower in name.lower() or keyword_lower in name_en.lower():
            #             filtered_users.append(user)
            #
            #     # 如果过滤后还有结果，使用过滤后的结果；否则保留原始结果
            #     if len(filtered_users) > 0:
            #         users_data = filtered_users
            
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
    emp_no = os.getenv("coclaw_empno")
    token = os.getenv("coclaw_token")
    
    # 如果 coclaw_empno 或 coclaw_token 为空，尝试从 EMP_NO 和 AUTH_VALUE 读取
    if not emp_no or not token:
        emp_no = emp_no or os.getenv("EMP_NO")
        token = token or os.getenv("AUTH_VALUE")
    
    if not emp_no or not token:
        _app_logger.error("请设置环境变量 coclaw_empno 和 coclaw_token（或 EMP_NO 和 AUTH_VALUE）")
        print(json.dumps({
            "status": "error",
            "message": "请设置环境变量 coclaw_empno 和 coclaw_token（或 EMP_NO 和 AUTH_VALUE）"
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(description="根据关键字搜索 ZTE iCenter 人员")
    parser.add_argument("--keyword", required=True, help="搜索关键字（必需），如姓名、工号等")
    args = parser.parse_args()

    result = search_users(emp_no, token, args.keyword)
    print(json.dumps(result, ensure_ascii=False, indent=2))

