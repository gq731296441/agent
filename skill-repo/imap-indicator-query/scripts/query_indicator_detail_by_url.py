"""
单指示器详单数据获取
通过 iMAP URL 获取单指示器的详单数据（明细数据）

此功能需要用户提供:
1. single_indicator_url: iMAP单指示器的URL链接
2. column_name: 需要详单下钻的字段名称

调用API: GET https://aip.zte.com.cn/imap/api/mcp/get_indicator_detail_data_by_url

注意：此接口返回的是data部分的内容，直接展示给用户查看详单明细。
"""

import urllib.parse
import requests
import sys
import os
from typing import Dict, Any, Optional
from export_utils import export_table_to_csv, generate_filename
from auth import get_auth_headers
from workspace_paths import resolve_export_dir

# 导出文件输出目录：会话工作区 /workspace/exports/<skill_id>/（沙箱内技能只读挂载，
# 产物必须写到工作区才能被用户看到与下载）；无工作区时回退到技能目录
WORKSPACE_DIR = resolve_export_dir()


def query_indicator_detail_data_by_url(
    single_indicator_url: str,
    column_name: str
) -> Dict[str, Any]:
    """
    通过 iMAP URL 获取单指示器的详单数据

    Args:
        single_indicator_url: iMAP单指示器的URL链接
        column_name: 需要详单下钻的字段名称

    Returns:
        详单数据查询结果

    Example:
        >>> query_indicator_detail_data_by_url(
        ...     "https://aip.zte.com.cn/dashboard/#!/kanban/window/2-10-76-75/template/menu/107/sub-menu/MTA3LS8tL-WfuuehgOaVsOaNrg/5dd1e3ef2c674aa6bd54d259aaf840d0",
        ...     "数量"
        ... )
        {'status': True, 'data': {...}}

    API:
        GET https://aip.zte.com.cn/imap/api/mcp/get_indicator_detail_data_by_url
        ?single_indicator_url={encoded_url}&column_name={column_name}
    """
    base_api = "https://aip.zte.com.cn/imap/api/mcp/get_indicator_detail_data_by_url"

    try:
        # 编码URL参数
        encoded_url = urllib.parse.quote(single_indicator_url, safe='')
        encoded_column = urllib.parse.quote(column_name, safe='')

        # 构造完整请求URL（免鉴权接口）
        final_url = f"{base_api}?single_indicator_url={encoded_url}&column_name={encoded_column}"

        # GET请求（免鉴权接口，但根据技能要求仍添加认证头部）
        empno = os.getenv('coclaw_empno')
        auth_token = os.getenv('coclaw_token')
        yxdata_token = os.getenv('coclaw_yxdata_token')
        if not yxdata_token and empno and auth_token:
            try:
                from auth import AuthManager
                auth_mgr = AuthManager()
                yxdata_token = auth_mgr.get_token(empno, auth_token)
            except Exception:
                yxdata_token = None
        headers = get_auth_headers(yxdata_token, auth_token, empno)
        # 过滤掉 None 值，避免发送空字段
        headers = {k: v for k, v in headers.items() if v is not None}
        resp = requests.get(final_url, headers=headers, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        # DEBUG: 打印API原始返回的关键字段
        print(f"[DEBUG] API原始返回: status={result.get('status')} (type={type(result.get('status'))}), message={result.get('message')}, data_len={len(result.get('data', [])) if isinstance(result.get('data'), list) else 'N/A'}")

        # 直接返回API原始响应（保持原始格式：{"status": true, "data": [...]}）
        # 如果需要转换为标准格式code/message/data，可在此处转换
        if isinstance(result, dict):
            # 更通用的成功判断逻辑：检测data字段存在且有内容，或status为True，或code为200
            has_data = 'data' in result and result['data'] is not None
            is_success = (
                result.get('status') is True or
                result.get('code') == 200 or
                (has_data and result.get('message') == 'success')
            )
            
            if is_success:
                standardized = {
                    "code": 200,
                    "message": result.get('message', 'success'),
                    "data": result.get('data', []),
                    "export": {"csv": []}
                }
                # 自动导出为 CSV
                try:
                    data_content = result.get('data')
                    if data_content:
                        # 构造表格结构
                        if isinstance(data_content, list) and len(data_content) > 0:
                            # 从数据中提取表头（保持顺序）
                            if isinstance(data_content[0], dict):
                                columns = list(data_content[0].keys())
                                table = {'column': columns, 'row': [[item.get(col) for col in columns] for item in data_content]}
                            else:
                                table = None
                        elif isinstance(data_content, dict) and 'row' in data_content:
                            table = data_content
                        else:
                            table = None
                        
                        if table:
                            safe_col = column_name.replace(' ', '_').replace('/', '_').replace('\\', '_')[:50]
                            base_name = generate_filename("indicator_detail", safe_col)
                            base_path = os.path.join(WORKSPACE_DIR, base_name)
                            
                            if export_table_to_csv(table, base_path):
                                standardized['export']['csv'].append(f"{base_path}.csv")
                except Exception as e:
                    standardized['export_error'] = str(e)
            else:
                standardized = {
                    "code": 500,
                    "message": result.get('message', result.get('error', 'failed')),
                    "data": [],
                    "error_type": "api_error"
                }
            
            # 如果数据很大，也保留原始结构供调试
            if '--debug' in sys.argv:
                standardized["raw_response"] = result
            return standardized
        else:
            return {
                "code": 500,
                "message": "API返回格式错误",
                "data": [],
                "raw_response": result
            }

    except requests.RequestException as e:
        return {
            "code": 500,
            "message": f"网络请求失败: {str(e)}",
            "data": [],
            "error_type": "network",
            "suggestion": "请检查网络连接或URL是否正确"
        }
    except ValueError as e:
        return {
            "code": 500,
            "message": f"响应解析失败: {str(e)}",
            "data": [],
            "error_type": "parse",
            "suggestion": "API返回的不是有效的JSON格式"
        }
    except Exception as e:
        return {
            "code": 500,
            "message": f"查询异常: {str(e)}",
            "data": [],
            "error_type": "unknown"
        }


def _extract_total_count(data_section: Any) -> int:
    """
    从data部分提取总记录数

    Args:
        data_section: API返回的data字段内容

    Returns:
        总记录数
    """
    try:
        if isinstance(data_section, dict):
            # 尝试常见字段名
            for key in ['total', 'totalCount', 'total_count', 'count', 'size']:
                if key in data_section:
                    return int(data_section[key])
            # 如果有content数组，返回数组长度
            if 'content' in data_section and isinstance(data_section['content'], list):
                return len(data_section['content'])
        elif isinstance(data_section, list):
            return len(data_section)
    except (ValueError, TypeError):
        pass
    return 0


def format_detail_data_for_display(data_section: Any) -> str:
    """
    将详单数据格式化为易读的文本（表格形式）

    Args:
        data_section: data字段内容

    Returns:
        格式化后的表格文本
    """
    try:
        items = []
        if isinstance(data_section, dict):
            # 有content字段的情况
            if 'content' in data_section and isinstance(data_section['content'], list):
                items = data_section['content']
            else:
                # 字典转列表
                items = [data_section]
        elif isinstance(data_section, list):
            items = data_section
        else:
            return str(data_section)

        if not items:
            return "详单数据为空"

        # 提取表头 - 保持API返回的字段顺序
        # 优先使用第一条记录的实际键顺序
        headers = []
        if items and isinstance(items[0], dict):
            headers = list(items[0].keys())
        
        # 如果第一条记录没有字段，尝试收集所有记录的全部字段（按字母排序作为fallback）
        if not headers:
            headers_set = set()
            for item in items[:10]:
                if isinstance(item, dict):
                    headers_set.update(item.keys())
            headers = sorted(list(headers_set))
        
        if not headers:
            return "数据格式异常：无法提取字段"
        
        # 计算每列的最大宽度（考虑中文字符）
        col_widths = {h: len(h) for h in headers}
        for item in items[:10]:  # 只计算前10条数据的宽度
            if isinstance(item, dict):
                for h in headers:
                    value = str(item.get(h, ''))
                    col_widths[h] = max(col_widths[h], len(value))
        
        # 为每列增加额外空间，确保可读性
        for h in col_widths:
            col_widths[h] += 2

        # 构建表格
        lines = []
        
        # 表头行
        header_line = " | ".join(h.ljust(col_widths[h]) for h in headers)
        lines.append(header_line)
        
        # 分隔线
        separator = "-+-".join("-" * col_widths[h] for h in headers)
        lines.append(separator)
        
        # 数据行（最多显示10条）
        for item in items[:10]:
            if isinstance(item, dict):
                row = []
                for h in headers:
                    value = str(item.get(h, ''))
                    row.append(value.ljust(col_widths[h]))
                lines.append(" | ".join(row))
        
        return "\n".join(lines)

    except Exception as e:
        return f"格式化失败: {str(e)}\n原始数据: {str(data_section)[:500]}"


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 3:
        print("用法: python query_indicator_detail_by_url.py <single_indicator_url> <column_name>")
        print("示例: python query_indicator_detail_by_url.py 'https://aip.zte.com.cn/dashboard/...' '数量'")
        sys.exit(1)

    single_indicator_url = sys.argv[1]
    column_name = sys.argv[2]

    print(f"🔍 查询单指示器详单数据")
    print(f"   URL: {single_indicator_url[:80]}..." if len(single_indicator_url) > 80 else f"   URL: {single_indicator_url}")
    print(f"   下钻字段: {column_name}\n")

    result = query_indicator_detail_data_by_url(single_indicator_url, column_name)

    # DEBUG: 只打印关键字段，避免大量数据输出
    print("【DEBUG - 关键字段】")
    print(f"  result type: {type(result)}")
    if isinstance(result, dict):
        print(f"  code: {result.get('code')}")
        print(f"  message: {result.get('message')}")
        print(f"  has data: {'data' in result}")
        data = result.get('data')
        if isinstance(data, list):
            print(f"  data length: {len(data)}")
        elif data is not None:
            print(f"  data type: {type(data)}")
    else:
        print(f"  result: {result}")
    print()

    print("【结果】")
    # 使用新的标准化格式：code/message/data
    if result.get('code') == 200:
        data = result.get('data', [])
        total = len(data) if isinstance(data, list) else 0
        print(f"✅ 查询成功，共 {total} 条记录，显示前10条：\n")
        
        # 以表格形式展示前10条数据
        display_data = data[:10]
        if display_data:
            # 提取表头 - 保持API返回的字段顺序
            # 优先使用第一条记录的实际键顺序
            headers = []
            if display_data and isinstance(display_data[0], dict):
                headers = list(display_data[0].keys())
            
            # 如果第一条记录没有字段，尝试收集所有字段
            if not headers:
                all_headers = set()
                for item in display_data:
                    if isinstance(item, dict):
                        all_headers.update(item.keys())
                headers = sorted(list(all_headers))
            
            if headers:
                
                # 计算每列的最大宽度
                col_widths = {h: len(h) for h in headers}
                for item in display_data:
                    if isinstance(item, dict):
                        for h in headers:
                            value = str(item.get(h, ''))
                            col_widths[h] = max(col_widths[h], len(value))
                
                # 打印表头
                header_line = " | ".join(h.ljust(col_widths[h]) for h in headers)
                separator_line = "-+-".join("-" * col_widths[h] for h in headers)
                print(header_line)
                print(separator_line)
                
                # 打印数据行
                for item in display_data:
                    if isinstance(item, dict):
                        row = []
                        for h in headers:
                            value = str(item.get(h, ''))
                            row.append(value.ljust(col_widths[h]))
                        print(" | ".join(row))
                
                print(f"\n💡 提示：完整数据共 {total} 条，可通过导出功能获取全部记录。")
            else:
                print("数据格式异常：无法提取字段")
        else:
            print("详单数据为空")
    else:
        print(f"❌ 查询失败: {result.get('message')}")
        if result.get('error'):
            print(f"💡 错误详情: {result.get('error')}")

    # 可选：打印完整JSON便于调试
    if '--debug' in sys.argv:
        print("\n【完整响应】")
        print(json.dumps(result, ensure_ascii=False, indent=2))