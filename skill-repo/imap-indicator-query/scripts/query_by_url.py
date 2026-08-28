"""
通过URL直接查询IMAP数据
使用免鉴权的dashboard接口直接获取指标数据
"""

import urllib.parse
import requests
from typing import Dict, Any, Optional
import os


def query_by_url(
    imap_url: str,
    is_encoded: bool = False
) -> Dict[str, Any]:
    """
    通过IMAP URL直接查询数据
    
    Args:
        imap_url: IMAP dashboard URL
        is_encoded: URL是否已经编码
        
    Returns:
        查询结果
        
    Example:
        >>> query_by_url("https://aip.zte.com.cn/dashboard/...")
        {'status': True, 'data': {...}}
        
        >>> query_by_url(
        ...     "https://aip.zte.com.cn/dashboard/api/mcp/get_indicator_data_by_url?single_indicator_url=...",
        ...     is_encoded=False
        ... )
    """
    base_api = "https://aip.zte.com.cn/dashboard/api/mcp/get_indicator_data_by_url"
    
    try:
        if is_encoded:
            # URL已经是编码后的，可能是完整的API URL或需要拼接
            if imap_url.startswith(base_api):
                final_url = imap_url
            else:
                # 编码后拼接
                encoded = urllib.parse.quote(imap_url, safe='')
                final_url = f"{base_api}?single_indicator_url={encoded}"
        else:
            # 检查是否已经是指标数据URL
            if 'single_indicator_url=' in imap_url:
                final_url = imap_url
            else:
                # 需要编码
                encoded = urllib.parse.quote(imap_url, safe='')
                final_url = f"{base_api}?single_indicator_url={encoded}"
        
        # 接口6免鉴权：不需要 yxdata-token
        # 根据技能要求，在所有REST请求中增加 yxdata-token, X-Auth-Value, X-Emp-No 三个字段（若存在）
        headers = {}
        empno = os.getenv('coclaw_empno')
        auth_token = os.getenv('coclaw_token')
        if empno:
            headers['X-Emp-No'] = empno
        if auth_token:
            headers['X-Auth-Value'] = auth_token
        yxdata_token = os.getenv('coclaw_yxdata_token')
        if not yxdata_token:
            try:
                from auth import AuthManager
                auth_mgr = AuthManager()
                yxdata_token = auth_mgr.get_token(empno, auth_token) if empno and auth_token else None
            except Exception:
                yxdata_token = None
        if yxdata_token:
            headers['yxdata-token'] = yxdata_token
        resp = requests.get(final_url, headers=headers, timeout=30)
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


def extract_indicator_info_from_url(url: str) -> Optional[Dict[str, str]]:
    """
    从URL中提取指标信息
    
    Args:
        url: IMAP URL
        
    Returns:
        指标信息字典
    """
    try:
        if 'single_indicator_url=' in url:
            # 提取编码后的indicator_url
            parts = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            encoded_url = parts.get('single_indicator_url', [''])[0]
            if encoded_url:
                decoded_url = urllib.parse.unquote(encoded_url)
                return _parse_imap_url(decoded_url)
        else:
            return _parse_imap_url(url)
    except Exception:
        return None
    
    return None


def _parse_imap_url(url: str) -> Optional[Dict[str, str]]:
    """
    解析IMAP URL提取指标信息
    
    Args:
        url: IMAP dashboard URL
        
    Returns:
        指标信息
    """
    # 示例URL格式：
    # https://aip.zte.com.cn/dashboard/#!/kanban/window/2-10-63-64/template/menu/32/sub-menu/MzItLy0v6ZyA5rGC/d2e4b78811f54f8e9775a76b11663a6a?matchedIndicatorId=d2e4b78811f54f8e9775a76b11663a6a&startTime=2026-01-01&endTime=2026-01-31%2023%3A59%3A59
    
    result = {}
    
    try:
        parsed = urllib.parse.urlparse(url)
        query_params = urllib.parse.parse_qs(parsed.query)
        
        # 提取指标ID
        indicator_id = query_params.get('matchedIndicatorId', [None])[0]
        if indicator_id:
            result['indicator_id'] = indicator_id
        
        # 提取时间范围
        start_time = query_params.get('startTime', [None])[0]
        end_time = query_params.get('endTime', [None])[0]
        
        if start_time:
            result['start_date'] = start_time.split(' ')[0]
        if end_time:
            result['end_date'] = end_time.split(' ')[0]
        
        # 从fragment中提取其他信息（如果需要）
        fragment = parsed.fragment
        if fragment:
            # 提取项目组/部门等信息
            # 典型片段: #/kanban/window/{window_id}/template/menu/{menu_id}/sub-menu/{encoded_name}/{indicator_id}
            parts = fragment.split('/')
            if len(parts) >= 7:
                result['window_id'] = parts[2] if parts[2] != 'kanban' else None
                result['menu_id'] = parts[4]
                result['sub_menu_id'] = parts[6]
        
    except Exception as e:
        result['parse_error'] = str(e)
    
    return result if result else None


if __name__ == "__main__":
    # 测试代码
    import json
    
    # 测试1: 完整URL
    test_url1 = "https://aip.zte.com.cn/dashboard/#!/kanban/window/2-10-63-64/template/menu/32/sub-menu/MzItLy0v6ZyA5rGC/d2e4b78811f54f8e9775a76b11663a6a?matchedIndicatorId=d2e4b78811f54f8e9775a76b11663a6a&startTime=2026-01-01&endTime=2026-01-31%2023%3A59%3A59"
    
    print("测试1: 解析URL提取信息")
    info = extract_indicator_info_from_url(test_url1)
    print(json.dumps(info, ensure_ascii=False, indent=2))
    
    print("\n测试2: 直接查询（需要有效的URL）")
    result = query_by_url(test_url1, is_encoded=False)
    print(json.dumps(result, ensure_ascii=False, indent=2))