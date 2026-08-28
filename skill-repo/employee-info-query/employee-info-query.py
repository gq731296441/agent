#!/usr/bin/env python3
"""
Employee Info Query - Cross-Platform Version

跨平台员工信息查询脚本，支持：
- Linux (python3/python)
- Windows (py/python/python3)
- macOS (python3/python)

解释器兼容性：
- 自动探测可用解释器：python3 > python > py
- 版本要求：Python 3.6+
- 支持 Windows 原生 py.exe

依赖：
- Python 3.6+
- curl (Linux/macOS) 或 requests (跨平台推荐)
"""

import os
import sys
import json
import argparse
import subprocess
import tempfile
from typing import Optional

# ============================================================================
# Python 解释器版本检查
# ============================================================================
def check_python_version():
    """
    检查 Python 版本是否满足要求 (3.6+)
    在脚本开头调用，如果版本不满足则退出
    """
    version_info = sys.version_info
    major, minor = version_info.major, version_info.minor
    
    if major < 3 or (major == 3 and minor < 6):
        print(f"错误: Python 版本不满足要求 (需要 3.6+, 当前: {major}.{minor})", file=sys.stderr)
        sys.exit(1)

check_python_version()

# ============================================================================
# 跨平台工具函数
# ============================================================================

def get_platform():
    """
    检测当前运行的操作系统平台
    
    Returns:
        'linux', 'windows', 'darwin' (macOS), 或 'unknown'
    """
    return sys.platform.lower()


def find_python_executable():
    """
    跨平台查找 Python 可执行文件路径 (支持 python3, python, py)
    
    优先级顺序：
    - Linux/macOS: python3 > python
    - Windows: py > python3 > python
    
    Returns:
        Python 可执行文件路径 (str)
    """
    platform = get_platform()
    
    # 根据平台设置不同的优先级
    if platform == 'windows':
        priority = ['py', 'python3', 'python']
    else:
        priority = ['python3', 'python']
    
    # 按优先级查找可执行文件
    for py in priority:
        try:
            result = subprocess.run(
                [py, '--version'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=5
            )
            if result.returncode == 0:
                # 验证版本
                version = subprocess.run(
                    [py, '-c', 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=5
                )
                if version.returncode == 0:
                    try:
                        major, minor = map(int, version.stdout.strip().split('.'))
                        if major >= 3 and (major > 3 or minor >= 6):
                            return py
                    except (ValueError, IndexError):
                        continue
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    
    # 如果都没找到，尝试常见路径
    common_paths = [
        '/usr/bin/python3',
        '/usr/bin/python',
        '/usr/local/bin/python3',
        '/usr/local/bin/python',
        '/c/Windows/py.exe',
    ]
    
    for path in common_paths:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            try:
                version = subprocess.run(
                    [path, '-c', 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=5
                )
                if version.returncode == 0:
                    try:
                        major, minor = map(int, version.stdout.strip().split('.'))
                        if major >= 3 and (major > 3 or minor >= 6):
                            return path
                    except (ValueError, IndexError):
                        continue
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
    
    return 'python3'  # 回退默认值


def get_user_info_from_env():
    """
    从环境变量获取用户认证信息（跨平台兼容）
    
    Returns:
        tuple: (emp_no, token) 或 (None, None)
    """
    # 优先级：coclaw_empno/coclaw_token > 原始环境变量名
    emp_no = os.environ.get('coclaw_empno') or os.environ.get('IGPT_EMP_NO') or ''
    token = os.environ.get('coclaw_token') or os.environ.get('IGPT_AUTH_VALUE') or ''
    
    return emp_no if emp_no else None, token if token else None


def make_http_request(url: str, headers: dict, data: dict, timeout: int = 40) -> tuple:
    """
    跨平台 HTTP 请求函数
    
    优先使用 curl (Linux/macOS)，回退到 requests (Windows)
    
    Returns:
        tuple: (http_code, response_body)
    """
    platform = get_platform()
    
    # 准备请求
    payload_json = json.dumps(data, ensure_ascii=False)
    
    if platform != 'windows':
        # Linux/macOS: 使用 curl
        # 构建 curl 命令
        cmd = [
            'curl', '-k', '-s',
            '--location',
            '--max-time', str(timeout),
            '-H', 'Content-Type: application/json',
            '-H', 'Access-Control-Request-Method: GET,POST',
        ]
        
        for key, value in headers.items():
            cmd.extend(['-H', f'{key}: {value}'])
        
        cmd.extend([
            '--data', payload_json,
            '-w', '%{http_code}',
            '-o', '-',  # 输出到 stdout
            url
        ])
        
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=timeout + 5
            )
            
            # 分离 HTTP code 和响应体
            output = result.stdout
            # curl -w 输出到 stdout，-o 输出也到 stdout，需要分割
            # 使用最后一个换行符分割
            if '\n' in output:
                last_newline = output.rfind('\n')
                http_code = output[last_newline + 1:].strip()
                response_body = output[:last_newline]
            else:
                http_code = '000'
                response_body = output
            
            return http_code, response_body
            
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            raise Exception(f"CURL 请求失败: {e}")
    
    else:
        # Windows: 尝试使用 requests 库
        try:
            import requests
            
            response = requests.post(
                url,
                headers=headers,
                data=payload_json,
                timeout=timeout,
                verify=False  # 类似 curl -k
            )
            
            return str(response.status_code), response.text
            
        except ImportError:
            # requests 未安装，尝试使用 urllib
            try:
                import urllib.request
                import urllib.error
                
                req = urllib.request.Request(
                    url,
                    data=payload_json.encode('utf-8'),
                    headers=headers
                )
                
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    return str(response.status), response.read().decode('utf-8')
                    
            except Exception as e:
                raise Exception(f"Windows urllib 请求失败: {e}")
        
        except Exception as e:
            raise Exception(f"Windows HTTP 请求失败: {e}")


def generate_chat_uuid() -> str:
    """
    生成 32 字符的十六进制 UUID（兼容所有 Python 版本）
    
    Returns:
        str: 32 字符 UUID
    """
    try:
        import uuid
        return uuid.uuid4().hex
    except ImportError:
        # Python 3.6 兼容的回退方案
        import hashlib
        import time
        raw = f"{time.time()}{id({})}".encode()
        return hashlib.md5(raw).hexdigest()


# ============================================================================
# Markdown 表格渲染
# ============================================================================

def render_markdown_from_json(table):
    """
    从 table_data_json 渲染 Markdown 表格
    
    Args:
        table: table_data_json 结构
    
    Returns:
        str: Markdown 表格 或 None
    """
    if not isinstance(table, list) or not table:
        return None
    
    first_row = table[0]
    if not isinstance(first_row, list) or not first_row:
        return None
    
    headers = []
    for cell in first_row:
        if isinstance(cell, dict) and cell:
            key = list(cell.keys())[0]
            headers.append(key)
    
    if not headers:
        return None
    
    lines = []
    header_line = "| " + " | ".join(headers) + " |"
    sep_line = "| " + " | ".join(["---"] * len(headers)) + " |"
    lines.append(header_line)
    lines.append(sep_line)
    
    for row in table:
        if not isinstance(row, list):
            continue
        values = []
        for idx, cell in enumerate(row[:len(headers)]):
            value = ""
            if isinstance(cell, dict) and cell:
                key = headers[idx]
                v = cell.get(key)
                if v is None:
                    v = ""
                value = str(v)
            values.append(value)
        
        while len(values) < len(headers):
            values.append("")
        
        line = "| " + " | ".join(values) + " |"
        lines.append(line)
    
    return "\n".join(lines)


# ============================================================================
# 主查询函数
# ============================================================================

def query_employee_info(text: str, chat_uuid: Optional[str] = None, 
                       raw: bool = False, debug: bool = False) -> str:
    """
    执行员工信息查询
    
    Args:
        text: 查询文本
        chat_uuid: 可选的 chat UUID
        raw: 是否返回原始 JSON
        debug: 是否开启调试模式
    
    Returns:
        str: Markdown 表格或诊断信息
    """
    # 获取认证信息
    emp_no, token = get_user_info_from_env()
    
    if debug:
        print(f"DEBUG: Platform={get_platform()}", file=sys.stderr)
        print(f"DEBUG: Python={find_python_executable()}", file=sys.stderr)
        print(f"DEBUG: emp_no={'set' if emp_no else 'not set'}", file=sys.stderr)
        print(f"DEBUG: token={'set' if token else 'not set'}", file=sys.stderr)
    
    if not emp_no or not token:
        error_msg = (
            "错误: 无法获取认证信息。\n"
            "请确保环境变量已设置:\n"
            "  - coclaw_empno 或 IGPT_EMP_NO (员工编号)\n"
            "  - coclaw_token 或 IGPT_AUTH_VALUE (认证令牌)"
        )
        print(error_msg, file=sys.stderr)
        sys.exit(1)
    
    # 配置
    tenant_id = "10001"
    base_url = os.environ.get(
        'IGPT_MARKDOWN_URL',
        "https://api.sdp.zte.com.cn/zte-dmt-sdp-databff/zte-dmt-sdp-dataagent/api/igpt/chat/markdown"
    )
    
    # 生成 chat_uuid（如果未提供）
    if not chat_uuid:
        chat_uuid = generate_chat_uuid()
    
    # 构建请求
    payload = {
        "chatUuid": chat_uuid,
        "text": text
    }
    
    headers = {
        "Content-Type": "application/json",
        "Access-Control-Request-Method": "GET,POST",
        "X-Auth-Value": token,
        "X-Emp-No": emp_no,
        "X-Tenant-Id": tenant_id
    }
    
    # 发送请求
    if debug:
        print(f"DEBUG: URL={base_url}", file=sys.stderr)
        print(f"DEBUG: chatUuid={chat_uuid}", file=sys.stderr)
    
    try:
        http_code, response_body = make_http_request(base_url, headers, payload)
    except Exception as e:
        print(f"请求错误: {e}", file=sys.stderr)
        sys.exit(1)
    
    # 检查响应
    if not response_body:
        print(f"iGPT 错误: 空响应 (http={http_code})", file=sys.stderr)
        sys.exit(1)
    
    if http_code != "200":
        print(f"iGPT 错误: http={http_code}", file=sys.stderr)
        print(response_body[:2000], file=sys.stderr)
        sys.exit(1)
    
    # 返回原始响应（如果需要）
    if raw:
        print(response_body)
        return
    
    # 解析 JSON
    try:
        data = json.loads(response_body)
    except json.JSONDecodeError as e:
        print(f"iGPT 错误: 无效的 JSON 响应: {e}", file=sys.stderr)
        print(f"响应预览: {response_body[:2000]}", file=sys.stderr)
        sys.exit(1)
    
    # 检查返回码
    code_info = data.get("code") or {}
    code = code_info.get("code") if isinstance(code_info, dict) else code_info
    msg = code_info.get("msg") if isinstance(code_info, dict) else ""
    
    if code != "0000":
        print(f"iGPT 错误: code={code} msg={msg}", file=sys.stderr)
        print(json.dumps(data, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)
    
    # 提取表格数据
    bo = data.get("bo") or {}
    results = bo.get("data_result") or []
    
    tables_md = []
    
    # 优先解析 table_data_json
    for r in results:
        if not isinstance(r, dict):
            continue
        json_table = r.get("table_data_json")
        if isinstance(json_table, list) and json_table:
            if debug:
                row_count = len(json_table)
                col_count = len(json_table[0]) if json_table else 0
                print(f"DEBUG: table_data_json rows={row_count} cols={col_count}", file=sys.stderr)
            
            md = render_markdown_from_json(json_table)
            if md and md.strip():
                tables_md.append(md.strip())
    
    # 如果有表格，输出并返回
    if tables_md:
        output = "\n\n".join(tables_md) + "\n"
        print(output)
        return
    
    # 回退到 table_data_markdown（旧格式）
    tables_legacy = []
    for r in results:
        if not isinstance(r, dict):
            continue
        t = r.get("table_data_markdown")
        if isinstance(t, str) and t.strip():
            tables_legacy.append(t.strip())
    
    if tables_legacy:
        if debug:
            print(f"DEBUG: 使用 legacy table_data_markdown", file=sys.stderr)
        output = "\n\n".join(tables_legacy) + "\n"
        print(output)
        return
    
    # 没有表格数据
    print("未返回表格数据。")
    chat_uuid_out = bo.get("chat_uuid") or bo.get("chatUuid") or ""
    text_out = bo.get("text") or ""
    req_id = bo.get("request_id") or ""
    
    if chat_uuid_out:
        print(f"chatUuid: {chat_uuid_out}")
    if text_out:
        print(f"text: {text_out}")
    if req_id:
        print(f"request_id: {req_id}")
    print(f"result_count: {len(results)}")


# ============================================================================
# CLI 入口点
# ============================================================================

def main():
    """命令行入口点"""
    parser = argparse.ArgumentParser(
        description="员工信息查询 - 跨平台版本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --text "查询数据管理团队有多少人"
  %(prog)s --text "查询我的个人信息" --debug
  %(prog)s --text "查询2025年出差记录" --raw

环境变量:
  coclaw_empno / IGPT_EMP_NO     - 员工编号
  coclaw_token / IGPT_AUTH_VALUE - 认证令牌
  IGPT_MARKDOWN_URL             - API 地址 (可选)
        """
    )
    
    parser.add_argument('--text', '-t', required=True,
                        help='查询文本')
    parser.add_argument('--chat', '-c',
                        help='Chat UUID (可选)')
    parser.add_argument('--json', action='store_true',
                        help='JSON 输出模式 (兼容旧版本，仍输出表格)')
    parser.add_argument('--raw', action='store_true',
                        help='输出原始 JSON 响应 (仅调试)')
    parser.add_argument('--debug', '-d', action='store_true',
                        help='开启调试模式')
    parser.add_argument('--platform', action='store_true',
                        help='显示当前平台信息并退出')
    
    args = parser.parse_args()
    
    # 平台检测
    if args.platform:
        platform = get_platform()
        py_path = find_python_executable()
        print(f"平台: {platform}")
        print(f"Python: {py_path}")
        print(f"版本: {sys.version}")
        return
    
    # 执行查询
    query_employee_info(
        text=args.text,
        chat_uuid=args.chat,
        raw=args.raw,
        debug=args.debug
    )


if __name__ == '__main__':
    main()
