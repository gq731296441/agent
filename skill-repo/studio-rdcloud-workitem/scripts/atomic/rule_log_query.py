#!/usr/bin/env python
"""
规则执行日志查询：从 ES 和 MongoDB 中查询规则执行日志，辅助定位规则执行失败的根本原因。

核心逻辑：
1. 通过 eventId 或工作项 ID 查询 ES 中的规则执行日志
2. 根据 ES 日志中的 eventId 关联查询 MongoDB 中的事后规则日志
3. 输出两组日志的关联信息，展示完整的规则执行链路

用法:
  # 查询指定单据的错误日志
  python scripts/atomic/rule_log_query.py --work-item-id PLM-123456

  # 查询指定工作区的错误日志
  python scripts/atomic/rule_log_query.py --workspace PLM --log-count 5

  # 查询指定 eventId 的日志
  python scripts/atomic/rule_log_query.py --event-id 53a1395bbf384054959e6f36d277b1c3
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_ROOT = SCRIPT_DIR.parent
SKILL_DIR = SCRIPTS_ROOT.parent
CONFIG_FILE = SKILL_DIR / "assets" / "config.json"

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from common.log_helper import get_logger, safe_print, apply_log_config, log_raw_argv, log_complete_params, setup_logging
from common.config_helper import load_base_config
from common.auth_helper import get_auth_headers
from common.rdcloud_api import query_rule_logs, query_after_trigger_logs

# 容器内 skill 为只读挂载（/mnt/skills/<id>），日志改写到可写工作区，并按 skill 分目录
LOG_DIR = (Path("/workspace/.skill-logs") / SKILL_DIR.name) if Path("/workspace").is_dir() else (SKILL_DIR / "logs")
LOG_FILE = LOG_DIR / f"rule_log_query_{datetime.now().strftime('%Y%m%d')}.log"
logger = get_logger(__name__)


def query_es_rule_logs(
    auth_headers: Dict,
    workspace: str,
    limit: int = 3,
    work_item_id: str = "",
    event_id: str = ""
) -> List[Dict]:
    """
    查询 ES 规则执行日志中的错误记录（按时间倒序）

    Args:
        auth_headers: 认证头
        workspace: 工作区标识
        limit: 查询条数
        work_item_id: 工作项 ID（可选）
        event_id: 事件 ID（可选，指定后忽略其他参数）

    Returns:
        错误日志列表
    """
    params = {"pageNo": 1, "pageSize": limit, "resTimeOrder": "DESC"}
    # 不强制过滤 result=False，让用户可以看到所有日志

    if event_id:
        params["eventId"] = event_id
    elif work_item_id:
        params["workItemId"] = work_item_id

    es_resp = query_rule_logs(auth_headers, workspace, params)
    bo = es_resp.get("bo") or {}
    logs = []

    for item in (bo.get("items") or bo.get("list") or []):
        if isinstance(item, dict):
            logs.append(item)

    return logs[:limit]


def query_mongo_after_trigger_logs(
    auth_headers: Dict,
    workspace: str,
    event_id: str
) -> List[Dict]:
    """
    查询 MongoDB 事后规则日志

    Args:
        auth_headers: 认证头
        workspace: 工作区标识
        event_id: 事件 ID

    Returns:
        事后规则日志列表
    """
    logs = query_after_trigger_logs(auth_headers, workspace, event_id)
    return logs if logs else []


def format_es_log_entry(log: Dict) -> str:
    """格式化 ES 日志条目"""
    lines = []
    lines.append(f"- 规则：{log.get('ruleName', '')}({log.get('ruleKey', '')})")
    lines.append(f"  eventId: {log.get('eventId', '')} | 类型：{log.get('contextTypeDesc', '')}")
    lines.append(f"  耗时：{log.get('timeConsuming', 0)}ms | 结果：{log.get('resultDesc', '')}")
    return "\n".join(lines)


def format_mongo_log_entry(log: Dict) -> str:
    """格式化 MongoDB 日志条目"""
    lines = []
    lines.append(f"  → 事后规则日志:")
    lines.append(f"     触发类型：{log.get('afterTriggerType', '')}")
    lines.append(f"     工作项：{log.get('workItemId', '')}")
    call_res = log.get('callRes', '')
    if call_res:
        lines.append(f"     执行结果：{call_res}")
    return "\n".join(lines)


def run():
    """主运行函数"""
    parser = _build_arg_parser()
    args = parser.parse_args()

    # 临时覆盖日志截断配置
    import common.rdcloud_api as rdcloud_api
    rdcloud_api._RESPONSE_LOG_MAX_CHARS = 0
    rdcloud_api._REQUEST_BODY_LOG_MAX_CHARS = 0

    log_raw_argv(logger, sys.argv)
    log_complete_params(logger, "参数解析完成", vars(args))

    base_config = load_base_config(CONFIG_FILE)
    auth_headers = get_auth_headers(None, base_config, logger)

    # 确定工作区
    workspace = args.workspace
    if not workspace and args.work_item_id:
        # 从工作项 ID 提取工作区
        i = args.work_item_id.rfind("-")
        workspace = args.work_item_id[:i] if i > 0 else args.work_item_id

    if not workspace:
        safe_print("错误：必须指定 --workspace 或 --work-item-id")
        sys.exit(1)

    # 查询 ES 日志
    safe_print(f"## ES 规则执行日志（工作区：{workspace}）\n")

    es_logs = query_es_rule_logs(
        auth_headers,
        workspace,
        args.log_count,
        args.work_item_id,
        args.event_id
    )

    if not es_logs:
        safe_print("未查询到规则执行日志。\n")
        return

    safe_print(f"查询条数：{args.log_count}，命中：{len(es_logs)} 条\n")

    # 输出 ES 日志并关联 MongoDB 日志
    for log in es_logs:
        safe_print(format_es_log_entry(log))

        # 关联查询 MongoDB 日志
        if log.get("eventId"):
            mongo_logs = query_mongo_after_trigger_logs(
                auth_headers,
                workspace,
                log["eventId"]
            )
            if mongo_logs:
                for mongo_log in mongo_logs:
                    safe_print(format_mongo_log_entry(mongo_log))

        safe_print()

    # 输出统计信息
    safe_print(f"## 统计信息\n")
    safe_print(f"- 总记录数：{len(es_logs)}")
    failed_count = sum(1 for l in es_logs if l.get("result") == False)
    safe_print(f"- 执行失败：{failed_count}")
    safe_print(f"- 执行成功：{len(es_logs) - failed_count}")


def _build_arg_parser():
    """构建命令行参数解析器"""
    p = argparse.ArgumentParser(
        description="规则执行日志查询：从 ES 和 MongoDB 中查询规则执行日志，辅助定位问题",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "使用示例:\n"
            "  # 查询指定单据的错误日志\n"
            "  python scripts/atomic/rule_log_query.py --work-item-id PLM-123456\n"
            "  \n"
            "  # 查询指定工作区的错误日志\n"
            "  python scripts/atomic/rule_log_query.py --workspace PLM --log-count 5\n"
            "  \n"
            "  # 查询指定 eventId 的日志\n"
            "  python scripts/atomic/rule_log_query.py --event-id 53a1395bbf384054959e6f36d277b1c3\n"
        ),
    )

    p.add_argument("--workspace", "-ws", metavar="KEY",
                   help="工作区标识，如 PLM、DT_TP 等")
    p.add_argument("--work-item-id", "-wi", metavar="ID",
                   help="工作项 ID，如 PLM-123456；自动解析工作区")
    p.add_argument("--event-id", "-ei", metavar="ID",
                   help="事件 ID，指定后查询该事件的所有日志")
    p.add_argument("--log-count", "-n", type=int, default=3,
                   help="查询日志条数（默认 3）")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="显示详细信息")

    return p


def main():
    """入口函数"""
    apply_log_config(CONFIG_FILE)
    setup_logging(LOG_FILE, add_stderr=True)

    try:
        run()
    except ValueError as e:
        safe_print(f"错误：{e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        logger.exception("执行失败")
        safe_print(f"错误：{e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
