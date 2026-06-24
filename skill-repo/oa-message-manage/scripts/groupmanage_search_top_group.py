#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
iCenter 获取自动筛选 TOP 群组脚本

功能：调用接口获取自动筛选的 TOP 群组列表

用法：
    python search_top_group.py

输出：
    - success: True/False
    - groups: 群组列表（adminID/groupName/groupUri）
"""

import os
import sys
sys.stdout.reconfigure(encoding='utf-8')
import json
import argparse
import logging
from logging.handlers import RotatingFileHandler
import urllib3
import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from datetime import datetime, timedelta
from typing import Dict, Any, List

from util import get_msg_base_url


# ============== 配置常量 ==============
API_BASE_URL = get_msg_base_url()
SELECT_TOP_GROUP_ENDPOINT = "/zte-icenter-msg-bff/services/extMessage/public-group/selectTopGroup"

# API Headers
DEFAULT_TERMINAL_TYPE = "Web@Desktop_999_ID1004"

MAX_DAYS = 7

# 参考 icenter-msg.py：文件 + 控制台双通道日志
_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
if not os.path.exists(_log_dir):
    os.makedirs(_log_dir)

_log_file = os.path.join(_log_dir, "groupmanage-search-top-group.log")
_file_handler = RotatingFileHandler(
    _log_file,
    maxBytes=20 * 1024 * 1024,  # 20MB
    backupCount=5,
    encoding="utf-8",
)
_file_handler.setLevel(logging.DEBUG)
_formatter = logging.Formatter("%(asctime)s - [%(name)s] - %(levelname)s - %(message)s")
_file_handler.setFormatter(_formatter)

_console_handler = logging.StreamHandler(sys.stderr)
_console_handler.setLevel(logging.DEBUG)
_console_handler.setFormatter(_formatter)

_logger = logging.getLogger("groupmanage-top-group")
_logger.setLevel(logging.DEBUG)
_logger.propagate = False
if not _logger.handlers:
    _logger.addHandler(_file_handler)
    _logger.addHandler(_console_handler)


class SearchTopGroupTool:
    """TOP 群组查询工具类"""

    def __init__(self):
        self.session = requests.Session()
        self.session.proxies = {"http": None, "https": None}
        self.session.verify = False
        self.emp_no = None
        self.token = None
        self.lang = None
        self._load_user_info()

    def _load_user_info(self):
        """加载用户认证信息"""
        self.emp_no = os.getenv("coclaw_empno") or os.getenv("X_EMP_NO")
        self.token = os.getenv("coclaw_token") or os.getenv("X_AUTH_VALUE")
        self.lang = os.getenv("coclaw_lang") or os.getenv("X_LANG_ID", "zh_CN")

        if not self.emp_no:
            print(json.dumps({
                "success": False,
                "error": "MISSING_EMP_NO",
                "message": "无法获取用户工号，请检查环境变量配置"
            }, ensure_ascii=False))
            sys.exit(1)

        if not self.token:
            print(json.dumps({
                "success": False,
                "error": "MISSING_TOKEN",
                "message": "无法获取用户认证Token，请检查环境变量配置"
            }, ensure_ascii=False))
            sys.exit(1)

    @staticmethod
    def _normalize_datetime(value: str, is_start: bool) -> str:
        """
        将输入时间规范化为 YYYYMMDDHHMMSS 格式。

        Args:
            value:   输入的时间字符串
            is_start: 是否为起始时间（True时补全为 000000，False时补全为 235959）

        Returns:
            规范化后的时间字符串，格式为 YYYYMMDDHHMMSS

        Raises:
            ValueError: 日期格式不正确
        """
        raw = (value or "").strip()
        if not raw:
            raise ValueError("日期不能为空")

        if len(raw) == 14 and raw.isdigit():
            return raw

        if len(raw) == 8 and raw.isdigit():
            return raw + ("000000" if is_start else "235959")

        if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
            date_part = raw.replace("-", "")
            return date_part + ("000000" if is_start else "235959")

        raise ValueError("日期格式不正确，支持 YYYY-MM-DD / YYYYMMDD / YYYYMMDDHHMMSS")

    def search(
        self,
        start: str,
        end: str,
        mode: str = "summary",
        ignore_mute: int = 0,
        ignore_at_all: int = 0,
        topn: int = None
    ) -> Dict[str, Any]:
        """
        获取 TOP 群组列表（按最近消息活跃度排序）。

        Args:
            start:         起始时间，格式 yyyy-MM-dd HH:mm:ss
            end:           截止时间，格式 yyyy-MM-dd HH:mm:ss
            mode:          查询模式: "summary" / "detail" / "atMe" / "send" / "recv" / "enter"
            ignore_mute:   是否忽略免打扰群组，1=忽略，0=不忽略
            ignore_at_all: 是否忽略@全体消息，1=忽略，0=不忽略
            topn:          返回条数上限，None 表示全部

        Returns:
            包含 success 状态、message、total 和 groups 列表的字典
        """
        url = f"{API_BASE_URL}{SELECT_TOP_GROUP_ENDPOINT}"
        headers = {
            "Content-Type": "application/json",
            "authKey": "msgSkill",
            "Terminal-Type": DEFAULT_TERMINAL_TYPE,
            "X-Emp-No": self.emp_no,
            "X-Auth-Value": self.token
        }
        if self.lang:
            headers["X-Lang-Id"] = self.lang
        try:
            payload = {
                "mode": mode or "summary",
                "ignoreMute": ignore_mute,
                "ignoreAtAll": ignore_at_all,
                "start": self._normalize_datetime(start, is_start=True),
                "end": self._normalize_datetime(end, is_start=False)
            }
        except ValueError as e:
            return {
                "success": False,
                "error": "INVALID_DATE",
                "message": str(e)
            }

        if topn is not None:
            payload["topn"] = topn
        _logger.info(
            "查询TOP群入参 mode=%s ignoreMute=%s ignoreAtAll=%s start=%s end=%s topn=%s emp_no=%s",
            payload.get("mode"),
            payload.get("ignoreMute"),
            payload.get("ignoreAtAll"),
            payload.get("start"),
            payload.get("end"),
            payload.get("topn"),
            self.emp_no
        )
        try:
            response = self.session.post(
                url,
                json=payload,
                headers=headers,
                timeout=30
            )
            response.raise_for_status()
            result = response.json()
            code = result.get("code", {}).get("code")
            msg = result.get("code", {}).get("msg")
            bo = result.get("bo")
            bo_count = len(bo) if isinstance(bo, list) else 0
            _logger.info("查询TOP群接口返回 code=%s msg=%s bo_count=%s", code, msg, bo_count)

            if code == "200":
                groups_raw: List[Dict[str, Any]] = bo if isinstance(bo, list) else []

                groups = [{
                    "adminID": g.get("adminID", ""),
                    "groupName": g.get("groupName", ""),
                    "groupUri": g.get("groupUri", ""),
                } for g in groups_raw]

                if len(groups) == 0:
                    return {
                        "success": True,
                        "message": "无可推送群组",
                        "total": 0,
                        "groups": []
                    }

                return {
                    "success": True,
                    "message": result.get("code", {}).get("msg", "Success"),
                    "total": len(groups),
                    "groups": groups
                }
            else:
                _logger.error(
                    "查询TOP群 API业务错误 code=%s msg=%s resp_headers=%s",
                    code,
                    msg,
                    json.dumps(dict(response.headers) if response.headers else {}, ensure_ascii=False),
                )
                return {
                    "success": False,
                    "message": result.get("code", {}).get("msg", "获取TOP群组失败")
                }

        except requests.exceptions.Timeout:
            return {
                "success": False,
                "error": "TIMEOUT",
                "message": "获取TOP群组超时，请稍后重试"
            }
        except requests.exceptions.RequestException as e:
            resp_headers = {}
            if getattr(e, "response", None) is not None:
                resp_headers = dict(e.response.headers) if e.response.headers else {}
            _logger.error(
                "查询TOP群 API请求异常 err=%s resp_headers=%s",
                str(e),
                json.dumps(resp_headers, ensure_ascii=False),
                exc_info=True
            )
            return {
                "success": False,
                "error": "API_ERROR",
                "message": f"获取TOP群组失败：{str(e)}"
            }


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="iCenter 获取自动一批重要群聊",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--start",
                        help="开始日期（格式：YYYY-MM-DD，如 2026-03-01）")
    parser.add_argument("--end",
                        help="结束日期（格式：YYYY-MM-DD，可选）")
    parser.add_argument("--day", type=int, choices=range(1, 8),
                        help=f"往前天数（1-{MAX_DAYS}天），与start/end互斥")
    parser.add_argument("--mode", default="summary",
                        choices=["summary", "atMe", "send", "recv", "enter"],
                        help="查询模式：summary/atMe/send/recv/enter，默认summary")
    parser.add_argument("--ignoreMute", type=int, choices=[0, 1], default=0,
                        help="是否忽略免扰：0/1，默认0")
    parser.add_argument("--ignoreAtAll", type=int, choices=[0, 1], default=0,
                        help="是否忽略@所有人：0/1，默认0")
    parser.add_argument("--topn", type=int,
                        help="返回TOPN群数量（可选）")
    args = parser.parse_args()

    tool = SearchTopGroupTool()
    if args.start and args.end:
        start = args.start
        end = args.end
    elif args.day:
        start = (datetime.now() - timedelta(days=args.day)).strftime("%Y-%m-%d")
        end = datetime.now().strftime("%Y-%m-%d")
    else:
        print(json.dumps({
            "success": False,
            "message": "必须指定开始日期或结束日期或往前天数"
        }, ensure_ascii=False, indent=2))
        sys.exit(1)
    result = tool.search(
        start=start,
        end=end,
        mode=args.mode,
        ignore_mute=args.ignoreMute,
        ignore_at_all=args.ignoreAtAll,
        topn=args.topn
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

