"""
认证管理模块
负责IMAP系统的认证流程：获取公共令牌、登录、缓存管理
"""

import os
import time
import requests
from typing import Optional, Dict, Any


class AuthManager:
    """认证管理器"""
    
    def __init__(self):
        self.public_token: Optional[str] = None
        self.yxdata_token: Optional[str] = None
        self.token_expire: float = 0
        self.base_url = "https://aip.zte.com.cn"
        
    def get_public_token(self, empno: str, token: str) -> str:
        """
        获取公共账户to_user_passwd
        
        Args:
            empno: 用户工号
            token: 用户认证token
            
        Returns:
            to_user_passwd（永久有效，敏感信息不输出）
            
        Raises:
            Exception: 获取失败时抛出
        """
        url = f"{self.base_url}/middle/api/user/middle/api"
        headers = {
            'X-Auth-Value': token,
            'X-Emp-No': empno
        }
        
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            result = resp.json()
            
            if result.get('status'):
                return result['data']['to_user']
            else:
                raise Exception(f"获取公共令牌失败: {result.get('msg', '未知错误')}")
        except requests.RequestException as e:
            raise Exception(f"网络请求失败: {str(e)}")
        except (KeyError, ValueError) as e:
            raise Exception(f"响应解析失败: {str(e)}")
    
    def get_yxdata_token(self, public_token: str) -> str:
        """
        使用公共令牌登录获取yxdata-token
        
        Args:
            public_token: 公共账户to_user_passwd
            
        Returns:
            yxdata-token（8小时有效）
            
        Raises:
            Exception: 登录失败时抛出
        """
        url = f"{self.base_url}/middle/api/login"
        headers = {
            "Content-Type": "application/json"
        }
        data = {
            "username": "YXdata-skill",
            "password": public_token
        }

        
        try:
            # resp = requests.post(url, headers=headers, json=data, timeout=30)
            resp = requests.request("POST", url, json=data, headers=headers)
            resp.raise_for_status()
            result = resp.json()
            
            if result.get('status'):
                token = result['data']['token']
                # 计算过期时间（8小时，预留1小时刷新缓冲）
                self.token_expire = time.time() + 7 * 3600
                return token
            else:
                raise Exception(f"登录失败: {result.get('msg', '未知错误')}")
        except requests.RequestException as e:
            raise Exception(f"登录请求失败: {str(e)}")
        except (KeyError, ValueError) as e:
            raise Exception(f"登录响应解析失败: {str(e)}")
    
    def get_token(self, empno: str, token: str) -> str:
        """
        获取有效的yxdata-token（自动使用缓存）
        
        Args:
            empno: 用户工号
            token: 用户认证token
            
        Returns:
            有效的yxdata-token
            
        Raises:
            Exception: 认证失败时抛出
        """
        now = time.time()
        
        # 检查缓存是否有效
        if self.yxdata_token and now < self.token_expire:
            return self.yxdata_token
        
        # 重新认证
        try:
            public_token = self.get_public_token(empno, token)
            yxdata_token = self.get_yxdata_token(public_token)
            
            # 更新缓存
            self.public_token = public_token
            self.yxdata_token = yxdata_token
            
            return yxdata_token
        except Exception as e:
            raise Exception(f"认证失败: {str(e)}")


def get_auth_headers(yxdata_token: str = None, auth_token: str = None, empno: str = None) -> Dict[str, str]:
    """
    构造认证请求头
    
    Args:
        yxdata_token: yxdata-token
        auth_token: 原始认证token
        empno: 用户工号
        
    Returns:
        请求头字典
    """
    headers = {
    }
    
    if yxdata_token:
        headers['yxdata-token'] = yxdata_token
    if auth_token:
        headers['X-Auth-Value'] = auth_token
    if empno:
        headers['X-Emp-No'] = empno
        
    return headers


if __name__ == "__main__":
    # 测试代码
    empno = os.getenv('coclaw_empno', '12345678')
    token = os.getenv('coclaw_token', 'xxx')
    
    if not token:
        print("请设置环境变量 coclaw_token")
    else:
        try:
            auth = AuthManager()
            yxdata_token = auth.get_token(empno, token)
            print(f"认证成功，yxdata-token: {yxdata_token[:20]}...")
        except Exception as e:
            print(f"认证失败: {e}")