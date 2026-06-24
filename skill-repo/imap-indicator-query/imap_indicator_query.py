"""
对外 API 入口模块

为了让用户能够使用文档里的方式：
`from imap_indicator_query import quick_query, query_indicator ...`
本模块把 `scripts/main.py` 的能力重新导出。
"""

import os
import sys

# 确保 scripts/ 目录能被 scripts/main.py 的“非相对导入”正确解析
_this_dir = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.join(_this_dir, "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from main import (  # noqa: F401
    quick_query,
    list_all_indicators,
    query_indicator,
    query_indicator_list_export,
    query_by_imap_url,
    query_indicator_detail_by_url,
    query_user_auth,
    get_user_auth_types,
    query_user_info,
    extract_user_org_info,
    get_milestone_retrospective_data,
)

