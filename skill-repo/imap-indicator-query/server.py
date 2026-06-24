"""
IMAP指标查询 - Web演示服务器
提供REST API和Web界面
"""

import os
import sys
import json
import argparse
from datetime import datetime
from flask import Flask, request, jsonify, send_file, render_template_string

# 确保能导入scripts目录下的模块
current_dir = os.path.dirname(os.path.abspath(__file__))
scripts_dir = os.path.join(current_dir, 'scripts')
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

# 导入技能核心模块
try:
    from main import (
        query_indicator, 
        query_by_imap_url, 
        query_indicator_list_export,
        query_user_auth,
        get_user_auth_types,
        query_user_info,
        extract_user_org_info
    )
    from query_user_auth import check_permission
    from query_name_id import query_name_to_id, extract_dimension_info
except ImportError as e:
    print(f"⚠️  导入核心模块失败: {e}")
    print(f"  请确保当前目录为: {current_dir}")
    print(f"  Python路径: {sys.path}")
    raise

app = Flask(__name__)

# 读取HTML模板
HTML_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), 'demo.html')

def get_auth_from_request():
    """从请求中提取认证信息"""
    # 优先使用环境变量
    empno = os.getenv('coclaw_empno')
    token = os.getenv('coclaw_token')
    
    # 其次检查请求头
    if not empno:
        empno = request.headers.get('X-Emp-No')
    if not token:
        token = request.headers.get('X-Auth-Value')
    
    # 最后检查JSON body
    if request.is_json:
        data = request.get_json() or {}
        empno = data.get('empno', empno)
        token = data.get('token', token)
    
    return empno, token


@app.route('/')
def index():
    """主页 - Web界面"""
    try:
        with open(HTML_TEMPLATE_PATH, 'r', encoding='utf-8') as f:
            html = f.read()
        return render_template_string(html)
    except FileNotFoundError:
        return jsonify({
            "status": False,
            "error": "demo.html not found",
            "suggestion": "请确保 demo.html 文件存在"
        }), 404


@app.route('/api/imap/query', methods=['POST'])
def api_query_indicator():
    """查询指标数据API"""
    try:
        data = request.get_json() or {}
        
        # 提取参数
        indicator_name = data.get('indicator_name')
        query_type = data.get('query_type')
        query_name = data.get('query_name')
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        
        # 获取认证信息
        empno, token = get_auth_from_request()
        
        # 调用核心功能
        result = query_indicator(
            indicator_name=indicator_name,
            query_type=query_type,
            query_name=query_name,
            empno=empno,
            token=token,
            start_date=start_date,
            end_date=end_date
        )
        
        return jsonify(result)
    
    except Exception as e:
        return jsonify({
            "status": False,
            "error": str(e),
            "suggestion": "请检查参数是否正确"
        }), 500


@app.route('/api/imap/query-by-url', methods=['POST', 'GET'])
def api_query_by_url():
    """通过URL查询API"""
    try:
        if request.method == 'POST':
            data = request.get_json() or {}
            imap_url = data.get('imap_url')
            is_encoded = data.get('is_encoded', False)
        else:  # GET
            imap_url = request.args.get('url')
            is_encoded = request.args.get('encoded', 'false').lower() == 'true'
        
        if not imap_url:
            return jsonify({
                "status": False,
                "error": "缺少参数: imap_url",
                "suggestion": "请提供有效的IMAP URL"
            }), 400
        
        result = query_by_imap_url(imap_url, is_encoded)
        return jsonify(result)
    
    except Exception as e:
        return jsonify({
            "status": False,
            "error": str(e)
        }), 500


@app.route('/api/imap/list', methods=['GET', 'POST'])
def api_list_indicators():
    """获取指标列表API"""
    try:
        keyword = ''
        empno, token = get_auth_from_request()
        
        if request.method == 'POST':
            data = request.get_json() or {}
            keyword = data.get('keyword', '')
        else:
            keyword = request.args.get('keyword', '')
        
        result = query_indicator_list_export(keyword, empno, token)
        return jsonify(result)
    
    except Exception as e:
        return jsonify({
            "status": False,
            "error": str(e)
        }), 500


@app.route('/api/imap/auth', methods=['GET', 'POST'])
def api_user_auth():
    """查询用户权限API"""
    try:
        empno, token = get_auth_from_request()
        
        if not empno or not token:
            return jsonify({
                "status": False,
                "error": "缺少认证信息",
                "suggestion": "请提供empno和token"
            }), 401
        
        result = query_user_auth(empno, auth_token=token)
        
        if not result.get('status'):
            return jsonify(result), 500
        
        # 返回格式化权限列表
        return jsonify({
            "status": True,
            "user_permissions": get_user_auth_types(result),
            "raw": result
        })
    
    except Exception as e:
        return jsonify({
            "status": False,
            "error": str(e)
        }), 500


@app.route('/api/imap/user', methods=['GET', 'POST'])
def api_user_info():
    """查询用户信息API"""
    try:
        empno, token = get_auth_from_request()
        
        if not empno or not token:
            return jsonify({
                "status": False,
                "error": "缺少认证信息"
            }), 401
        
        result = query_user_info(empno, auth_token=token)
        return jsonify(result)
    
    except Exception as e:
        return jsonify({
            "status": False,
            "error": str(e)
        }), 500


@app.route('/api/imap/name-to-id', methods=['POST'])
def api_name_to_id():
    """名称转ID API"""
    try:
        data = request.get_json() or {}
        query_type = data.get('type')
        query_name = data.get('name')
        
        if not query_type or not query_name:
            return jsonify({
                "status": False,
                "error": "缺少参数: type 和 name",
                "suggestion": "请提供查询类型和名称"
            }), 400
        
        empno, token = get_auth_from_request()
        
        result = query_name_to_id(query_type, query_name, auth_token=token, empno=empno)
        return jsonify(result)
    
    except Exception as e:
        return jsonify({
            "status": False,
            "error": str(e)
        }), 500


@app.route('/api/imap/health', methods=['GET'])
def health_check():
    """健康检查"""
    return jsonify({
        "status": True,
        "service": "imap-indicator-query",
        "version": "2.0.0",
        "timestamp": datetime.now().isoformat()
    })


@app.route('/api/imap/doc', methods=['GET'])
def api_doc():
    """API文档"""
    doc = {
        "service": "IMAP指标查询API",
        "version": "2.0.0",
        "endpoints": [
            {
                "path": "/api/imap/query",
                "method": "POST",
                "description": "查询指标数据（完整工作流）",
                "parameters": {
                    "indicator_name": "string (必填) 指标名称",
                    "query_type": "string (必填) 查询类型: 项目组/部门/中心/分中心级",
                    "query_name": "string (必填) 查询条件名称",
                    "start_date": "string (可选) 开始日期 YYYY-MM-DD",
                    "end_date": "string (可选) 结束日期 YYYY-MM-DD"
                }
            },
            {
                "path": "/api/imap/query-by-url",
                "method": "POST/GET",
                "description": "通过IMAP URL直接查询（免鉴权）",
                "parameters": {
                    "imap_url": "string (必填) IMAP dashboard URL",
                    "is_encoded": "boolean (可选) URL是否已编码"
                }
            },
            {
                "path": "/api/imap/list",
                "method": "GET/POST",
                "description": "获取可用指标列表",
                "parameters": {
                    "keyword": "string (可选) 搜索关键词"
                }
            },
            {
                "path": "/api/imap/auth",
                "method": "GET/POST",
                "description": "查询用户权限列表"
            },
            {
                "path": "/api/imap/user",
                "method": "GET/POST",
                "description": "查询用户信息"
            },
            {
                "path": "/api/imap/name-to-id",
                "method": "POST",
                "description": "根据类型和名称查询系统ID",
                "parameters": {
                    "type": "string (必填) 类型: 项目组/部门/中心/分中心级",
                    "name": "string (必填) 名称"
                }
            }
        ],
        "authentication": {
            "method": "HTTP Header 或环境变量",
            "headers": ["X-Emp-No", "X-Auth-Value"],
            "environment": ["coclaw_empno", "coclaw_token"]
        }
    }
    return jsonify(doc)


def main():
    parser = argparse.ArgumentParser(description='IMAP指标查询 - Web演示服务器')
    parser.add_argument('--host', default='127.0.0.1', help='监听地址 (默认: 127.0.0.1)')
    parser.add_argument('--port', type=int, default=5000, help='监听端口 (默认: 5000)')
    parser.add_argument('--debug', action='store_true', help='启用调试模式')
    
    args = parser.parse_args()
    
    print(f"\n🚀 IMAP指标查询服务器启动")
    print(f"   📍 地址: http://{args.host}:{args.port}")
    print(f"   🌐 界面: http://{args.host}:{args.port}/")
    print(f"   📚 API文档: http://{args.host}:{args.port}/api/imap/doc")
    print(f"   🔍 健康检查: http://{args.host}:{args.port}/api/imap/health")
    print(f"\n💡 提示: 设置环境变量 coclaw_empno 和 coclaw_token 以启用完整功能\n")
    
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
