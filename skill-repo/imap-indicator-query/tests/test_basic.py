"""
IMAP指标查询技能测试
"""

import os
import sys
import json
import unittest
from unittest.mock import patch, Mock

# 添加脚本目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from auth import AuthManager, get_auth_headers
from query_indicator_list import query_indicator_list, find_indicator_by_name
from query_user_auth import query_user_auth, check_permission
from query_user_info import query_user_info, extract_user_org_info
from query_name_id import query_name_to_id, extract_dimension_info
from query_indicator_data import query_indicator_data, format_data_for_visualization
from query_by_url import query_by_url, extract_indicator_info_from_url


class TestAuthManager(unittest.TestCase):
    """认证管理测试"""
    
    def setUp(self):
        self.auth = AuthManager()
        self.empno = "12345678"
        self.token = "test_token_12345"
        self.public_token = "xxxx"
        self.yxdata_token = "xxxx"
    
    @patch('requests.get')
    def test_get_public_token_success(self, mock_get):
        """测试获取公共令牌成功"""
        mock_response = Mock()
        mock_response.json.return_value = {
            "status": True,
            "data": {"to_user": self.public_token}
        }
        mock_get.return_value = mock_response
        
        result = self.auth.get_public_token(self.empno, self.token)
        self.assertEqual(result, self.public_token)
    
    @patch('requests.get')
    def test_get_public_token_failure(self, mock_get):
        """测试获取公共令牌失败"""
        mock_response = Mock()
        mock_response.json.return_value = {
            "status": False,
            "msg": "认证失败"
        }
        mock_get.return_value = mock_response
        
        with self.assertRaises(Exception) as context:
            self.auth.get_public_token(self.empno, self.token)
        self.assertIn("获取公共令牌失败", str(context.exception))
    
    @patch('requests.post')
    def test_get_yxdata_token_success(self, mock_post):
        """测试登录获取yxdata-token成功"""
        mock_response = Mock()
        mock_response.json.return_value = {
            "status": True,
            "data": {"token": self.yxdata_token}
        }
        mock_post.return_value = mock_response
        
        result = self.auth.get_yxdata_token(self.public_token)
        self.assertEqual(result, self.yxdata_token)
        # 验证缓存时间设置
        self.assertGreater(self.auth.token_expire, 0)
    
    def test_get_token_with_cache(self):
        """测试使用缓存token"""
        # 设置有效缓存
        self.auth.yxdata_token = self.yxdata_token
        self.auth.token_expire = float('inf')  # 永不过期
        
        result = self.auth.get_token(self.empno, self.token)
        self.assertEqual(result, self.yxdata_token)
    
    def test_get_auth_headers(self):
        """测试请求头构造"""
        headers = get_auth_headers(
            yxdata_token="xxxx",
            auth_token="xxxx",
            empno="12345678"
        )
        
        self.assertEqual(headers['yxdata-token'], "xxxx")
        self.assertEqual(headers['X-Auth-Value'], "xxxx")
        self.assertEqual(headers['X-Emp-No'], "12345678")
        self.assertIn('User-Agent', headers)


class TestQueryIndicatorList(unittest.TestCase):
    """指示器列表查询测试"""
    
    @patch('requests.get')
    def test_query_indicator_list_success(self, mock_get):
        """测试查询指示器列表成功"""
        mock_response = Mock()
        mock_response.json.return_value = {
            "status": True,
            "data": {
                "total": 2,
                "content": [
                    {
                        "指示器名称": "MR交付周期（天）",
                        "api_接口地址": "https://aip.zte.com.cn/middle/api/remote/call/81185dacaae0ad00"
                    },
                    {
                        "指示器名称": "需求交付速率",
                        "api_接口地址": "https://aip.zte.com.cn/middle/api/remote/call/another"
                    }
                ]
            }
        }
        mock_get.return_value = mock_response
        
        result = query_indicator_list(keyword="MR", auth_token="xxxx")
        self.assertTrue(result['status'])
        self.assertEqual(result['data']['total'], 2)
    
    def test_find_indicator_by_name(self):
        """测试精确查找指示器"""
        test_data = {
            "status": True,
            "data": {
                "content": [
                    {"指示器名称": "MR交付周期（天）", "id": "1"},
                    {"指示器名称": "需求交付速率", "id": "2"}
                ]
            }
        }
        
        result = find_indicator_by_name("MR交付周期（天）", auth_token="xxxx")
        self.assertIsNotNone(result)
        self.assertEqual(result['id'], '1')
    
    def test_fuzzy_find_indicator(self):
        """测试模糊查找指示器"""
        test_data = {
            "status": True,
            "data": {
                "content": [
                    {"指示器名称": "MR交付周期（天）"},
                    {"指示器名称": "MR实现周期"},
                    {"指示器名称": "需求交付速率"}
                ]
            }
        }
        
        with patch('query_indicator_list.query_indicator_list', return_value=test_data):
            matches = fuzzy_find_indicator("MR", auth_token="xxxx")
            self.assertEqual(len(matches), 2)  # 匹配MR交付周期和MR实现周期


class TestQueryUserAuth(unittest.TestCase):
    """用户权限查询测试"""
    
    @patch('requests.get')
    def test_query_user_auth_success(self, mock_get):
        """测试查询用户权限成功"""
        mock_response = Mock()
        mock_response.json.return_value = {
            "status": True,
            "data": {
                "content": [
                    {"auth_type": "项目组级", "key_name": "ZXUFP", "key_id": "48"}
                ]
            }
        }
        mock_get.return_value = mock_response
        
        result = query_user_auth("12345678", auth_token="xxxx")
        self.assertTrue(result['status'])
        self.assertEqual(len(result['data']['content']), 1)
    
    def test_check_permission(self):
        """测试权限检查"""
        auth_data = {
            "status": True,
            "data": {
                "content": [
                    {"auth_type": "项目组级", "key_name": "ZXUFP", "key_id": "48"},
                    {"auth_type": "部门级", "key_name": "FM开发二部", "key_id": "26"}
                ]
            }
        }
        
        # 测试有权限的情况
        self.assertTrue(check_permission(auth_data, "项目组", "ZXUFP"))
        self.assertTrue(check_permission(auth_data, "部门", "FM开发二部"))
        
        # 测试无权限的情况
        self.assertFalse(check_permission(auth_data, "项目组", "Other"))
        self.assertFalse(check_permission(auth_data, "部门", "Other"))
    
    def test_get_user_auth_types(self):
        """测试提取用户权限分类"""
        auth_data = {
            "status": True,
            "data": {
                "content": [
                    {"auth_type": "项目组级", "key_name": "ZXUFP"},
                    {"auth_type": "项目组级", "key_name": "BVB"},
                    {"auth_type": "部门级", "key_name": "FM开发二部"}
                ]
            }
        }
        
        result = get_user_auth_types(auth_data)
        self.assertEqual(len(result['项目组级']), 2)
        self.assertEqual(len(result['部门级']), 1)


class TestQueryUserInfo(unittest.TestCase):
    """用户信息查询测试"""
    
    @patch('requests.get')
    def test_query_user_info_success(self, mock_get):
        """测试查询用户信息成功"""
        mock_response = Mock()
        mock_response.json.return_value = {
            "status": True,
            "data": {
                "content": [{
                    "employee_id": "12345678",
                    "name": "吴俊琴",
                    "department": "软件平台开发六部",
                    "projectGroup": "有线效能工具"
                }]
            }
        }
        mock_get.return_value = mock_response
        
        result = query_user_info("12345678", auth_token="xxxx")
        self.assertTrue(result['status'])
    
    def test_extract_user_org_info(self):
        """测试提取组织信息"""
        user_info = {
            "status": True,
            "data": {
                "content": [{
                    "employee_id": "12345678",
                    "name": "吴俊琴",
                    "department": "软件平台开发六部",
                    "projectGroup": "有线效能工具",
                    "subCenter": "软件平台研发中心"
                }]
            }
        }
        
        org_info = extract_user_org_info(user_info)
        self.assertEqual(org_info['employee_id'], "12345678")
        self.assertEqual(org_info['department'], "软件平台开发六部")
        self.assertEqual(org_info['projectGroup'], "有线效能工具")


class TestQueryNameId(unittest.TestCase):
    """名称ID查询测试"""
    
    @patch('requests.get')
    def test_query_name_to_id_success(self, mock_get):
        """测试查询名称ID成功"""
        mock_response = Mock()
        mock_response.json.return_value = {
            "status": True,
            "data": {
                "content": [{
                    "type": "项目组",
                    "id": "48",
                    "name": "ZXUFP",
                    "param": "projectGroup_id"
                }]
            }
        }
        mock_get.return_value = mock_response
        
        result = query_name_to_id("项目组", "ZXUFP", auth_token="xxxx")
        self.assertTrue(result['status'])
        self.assertEqual(result['data']['content'][0]['id'], "48")
    
    def test_extract_dimension_info(self):
        """测试提取维度信息"""
        name_id_result = {
            "status": True,
            "data": {
                "content": [{
                    "type": "项目组",
                    "id": "48",
                    "name": "ZXUFP",
                    "param": "projectGroup_id"
                }]
            }
        }
        
        info = extract_dimension_info(name_id_result)
        self.assertEqual(info['id'], '48')
        self.assertEqual(info['param'], 'projectGroup_id')


class TestQueryIndicatorData(unittest.TestCase):
    """指标数据查询测试"""
    
    @patch('requests.get')
    def test_query_indicator_data_success(self, mock_get):
        """测试查询指标数据成功"""
        mock_response = Mock()
        mock_response.json.return_value = {
            "status": True,
            "data": {
                "content": [
                    {"周期": "2026-04", "平均数": 51},
                    {"周期": "2026-05", "平均数": 345}
                ]
            }
        }
        mock_get.return_value = mock_response
        
        result = query_indicator_data(
            api_url="https://aip.zte.com.cn/middle/api/remote/call/81185dacaae0ad00",
            dimension_param="projectGroup_id",
            dimension_value="48",
            yxdata_token="xxxx",
            auth_token="xxxx",
            empno="12345678"
        )
        
        self.assertTrue(result['status'])
        self.assertEqual(len(result['data']['content']), 2)
    
    def test_format_data_for_visualization(self):
        """测试数据格式化"""
        data_result = {
            "status": True,
            "data": {
                "content": [
                    {"周期": "2026-04", "平均数": 51, "中位数": 50},
                    {"周期": "2026-05", "平均数": 345, "中位数": 340}
                ]
            }
        }
        
        formatted = format_data_for_visualization(data_result)
        self.assertIn('fields', formatted)
        self.assertIn('time_fields', formatted)
        self.assertIn('value_fields', formatted)
        self.assertIn('周期', formatted['time_fields'])
        self.assertIn('平均数', formatted['value_fields'])


class TestQueryByUrl(unittest.TestCase):
    """URL查询测试"""
    
    @patch('requests.get')
    def test_query_by_url_success(self, mock_get):
        """测试URL查询成功"""
        mock_response = Mock()
        mock_response.json.return_value = {
            "status": True,
            "data": {"value": "test"}
        }
        mock_get.return_value = mock_response
        
        result = query_by_url("https://aip.zte.com.cn/dashboard/test")
        self.assertTrue(result['status'])
    
    def test_extract_indicator_info_from_url(self):
        """测试从URL提取信息"""
        test_url = "https://aip.zte.com.cn/dashboard/#!/kanban/window/2-10-63-64/template/menu/32/sub-menu/encoded/d2e4b78811f54f8e9775a76b11663a6a?matchedIndicatorId=d2e4b78811f54f8e9775a76b11663a6a&startTime=2026-01-01&endTime=2026-01-31"
        
        info = extract_indicator_info_from_url(test_url)
        self.assertIsNotNone(info)
        self.assertEqual(info['indicator_id'], 'd2e4b78811f54f8e9775a76b11663a6a')


class TestIntegration(unittest.TestCase):
    """集成测试"""
    
    @patch('requests.get')
    @patch('requests.post')
    def test_full_query_workflow(self, mock_post, mock_get):
        """测试完整查询工作流"""
        # 模拟认证响应
        mock_auth_get = Mock()
        mock_auth_get.json.return_value = {
            "status": True,
            "data": {"to_user": "test_public_token"}
        }
        
        mock_login_post = Mock()
        mock_login_post.json.return_value = {
            "status": True,
            "data": {"token": "xxxx"}
        }
        
        # 模拟查询响应
        mock_query_get = Mock()
        mock_query_get.json.side_effect = [
            # 用户信息
            {
                "status": True,
                "data": {
                    "content": [{
                        "employee_id": "12345678",
                        "department": "Test Dept",
                        "projectGroup": "Test Group"
                    }]
                }
            },
            # 用户权限
            {
                "status": True,
                "data": {
                    "content": [
                        {"auth_type": "项目组级", "key_name": "TestGroup", "key_id": "99"}
                    ]
                }
            },
            # 指示器列表
            {
                "status": True,
                "data": {
                    "content": [{
                        "指示器名称": "Test Indicator",
                        "api_接口地址": "https://aip.zte.com.cn/api/test",
                        "图表x轴": "月份",
                        "图表y轴": "数值",
                        "图表类型": "柱状图"
                    }]
                }
            },
            # 名称ID查询
            {
                "status": True,
                "data": {
                    "content": [{
                        "type": "项目组",
                        "id": "99",
                        "name": "TestGroup",
                        "param": "projectGroup_id"
                    }]
                }
            },
            # 指标数据
            {
                "status": True,
                "data": {
                    "content": [
                        {"月份": "2026-01", "数值": 100},
                        {"月份": "2026-02", "数值": 200}
                    ]
                }
            }
        ]
        
        mock_get.side_effect = [mock_auth_get, mock_query_get(), mock_query_get(), mock_query_get(), mock_query_get()]
        mock_post.return_value = mock_login_post
        
        # 导入main模块并执行查询
        from main import query_indicator
        
        with patch('main.AuthManager') as mock_auth_class:
            mock_auth_instance = Mock()
            mock_auth_instance.get_token.return_value = "xxxx"
            mock_auth_class.return_value = mock_auth_instance
            
            result = query_indicator(
                indicator_name="Test Indicator",
                query_type="项目组",
                query_name="TestGroup",
                empno="12345678",
                token="test_token"
            )
        
        self.assertTrue(result['status'])
        self.assertEqual(result['indicator']['name'], "Test Indicator")
        self.assertEqual(result['query_condition']['name'], "TestGroup")


if __name__ == '__main__':
    # 设置环境变量用于测试
    os.environ['coclaw_empno'] = '12345678'
    os.environ['coclaw_token'] = 'test_token_12345'
    
    # 运行测试
    unittest.main(verbosity=2)