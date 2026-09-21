#!/usr/bin/env python
"""
关联工作项：将指定工作项与其它工作项建立关联关系（如子级、父级、相关等）。
支持纯关联场景（仅建立关联）以及创建/更新工作项时附带关联。
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_ROOT = SCRIPT_DIR.parent
SKILL_DIR = SCRIPTS_ROOT.parent
CONFIG_FILE = SKILL_DIR / "assets" / "config.json"

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from common.log_helper import get_logger, safe_print, apply_log_config, log_raw_argv, log_complete_params, setup_logging, ENABLE_LOG, format_parsed_summary
from common.config_helper import load_base_config
from common.auth_helper import get_auth_headers
from common.rdcloud_api import batch_create_relations, LINK_RELATION_TYPE_MAP

# 容器内 skill 为只读挂载（/mnt/skills/<id>），日志改写到可写工作区，并按 skill 分目录
LOG_DIR = (Path("/workspace/.skill-logs") / SKILL_DIR.name) if Path("/workspace").is_dir() else (SKILL_DIR / "logs")
LOG_FILE = LOG_DIR / f"relate_work_item_{datetime.now().strftime('%Y%m%d')}.log"
logger = get_logger(__name__)


def parse_args():
    p = argparse.ArgumentParser(
        description="关联工作项：建立工作项之间的关联关系（如将 A 作为 B 的子级）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例: %(prog)s --work-item-ids PLM-3292542 --relate-workitem-ids PLM-3292544 --relate-type 子级\n"
        "  %(prog)s --work-item-ids PLM-1,PLM-2 --relate-workitem-ids PLM-100 --relate-type 子级\n"
        "支持的关联类型: " + "、".join(LINK_RELATION_TYPE_MAP.keys()),
    )
    p.add_argument("--work-item-ids", "--workItemIds", dest="work_item_ids", required=True, metavar="IDS", help="主工作项标识，多个以英文逗号分隔（作为关联主体的一方）")
    p.add_argument("--relate-workitem-ids", "--relateWorkitemIds", dest="relate_workitem_ids", required=True, metavar="IDS", help="被关联的工作项标识，多个以英文逗号分隔")
    p.add_argument("--relate-type", "--relateType", dest="relate_type", required=True, metavar="TYPE", help="关联类型中文名，如 子级、父级、相关、前置、后续 等")
    p.add_argument("--user-input", "--userInput", dest="user_input", default="", metavar="TEXT", help="用户原始输入。")
    return p.parse_args()


def resolve_link_relation_id(relate_type: str) -> str:
    """将关联类型中文映射为 linkRelationId（referenceName）。"""
    t = (relate_type or "").strip()
    ref = LINK_RELATION_TYPE_MAP.get(t)
    if ref:
        return ref
    for zh, ref_name in LINK_RELATION_TYPE_MAP.items():
        if zh == t or ref_name == t:
            return ref_name
    raise ValueError(f"不支持的关联类型「{relate_type}」，支持: " + "、".join(LINK_RELATION_TYPE_MAP.keys()))


def run(work_item_ids: list, relate_workitem_ids: list, relate_type: str, headers: dict) -> dict:
    """
    执行关联：构建 body 并调用 batch_create_relations。
    主工作项与被关联工作项均支持多个，笛卡尔积生成所有 (workItemId, relatedWorkItemId) 对。
    :return: 接口响应；若 bo.failedItems 非空则视为有失败
    """
    link_relation_id = resolve_link_relation_id(relate_type)
    body = []
    for wid in work_item_ids:
        wid = (wid or "").strip()
        if not wid:
            continue
        for rid in relate_workitem_ids:
            rid = (rid or "").strip()
            if not rid:
                continue
            body.append({
                "workItemId": wid,
                "linkRelationId": link_relation_id,
                "relatedWorkItemId": rid,
            })
    if not body:
        raise ValueError("work_item_ids 或 relate_workitem_ids 解析后为空")
    logger.info("关联请求 body=%s", json.dumps(body, ensure_ascii=False))
    resp = batch_create_relations(body, headers)
    return resp


def main():
    apply_log_config(CONFIG_FILE)
    if ENABLE_LOG:
        setup_logging(LOG_FILE)
    args = parse_args()
    log_raw_argv(logger, sys.argv)
    try:
        base_config = load_base_config()
        input_data = {
            "auth": None,
            "userInput": getattr(args, "user_input", "") or "",
        }
        headers = get_auth_headers(input_data, base_config, logger=logger)
        work_item_ids_str = (args.work_item_ids or "").strip()
        work_item_ids = [x.strip() for x in work_item_ids_str.split(",") if x.strip()]
        relate_ids_str = (args.relate_workitem_ids or "").strip()
        relate_workitem_ids = [x.strip() for x in relate_ids_str.split(",") if x.strip()]
        relate_type = (args.relate_type or "").strip()
        if not work_item_ids:
            raise ValueError("--work-item-ids 不能为空")
        if not relate_workitem_ids:
            raise ValueError("--relate-workitem-ids 不能为空")
        if not relate_type:
            raise ValueError("--relate-type 不能为空")
        log_complete_params(logger, "参数完成", {"work_item_ids": work_item_ids, "relate_workitem_ids": relate_workitem_ids, "relate_type": relate_type})
        resp = run(work_item_ids, relate_workitem_ids, relate_type, headers)
        bo = resp.get("bo") or {}
        failed = bo.get("failedItems") or []
        succeeded = bo.get("succeededItems") or []
        if failed:
            err_msg = bo.get("message") or "部分关联失败"
            detail = json.dumps(failed, ensure_ascii=False, indent=2)
            logger.warning("关联存在失败 failedItems=%s", detail)
            safe_print(f"关联失败: {err_msg}\n失败项:\n{detail}", file=sys.stderr)
            sys.exit(1)
        out = {"success": True, "succeededItems": succeeded, "message": bo.get("message") or "关联成功"}
        parsed_summary = format_parsed_summary(
            {
                "work_item_ids": work_item_ids,
                "relate_workitem_ids": relate_workitem_ids,
                "relate_type": relate_type,
            },
            title="本次语义解析结果",
        )
        result_json = json.dumps(out, ensure_ascii=False, indent=2)
        out_for_display = (parsed_summary + "\n" + result_json) if parsed_summary else result_json
        safe_print(out_for_display)
    except ValueError as e:
        logger.warning("参数错误: %s", e)
        safe_print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        logger.exception("执行异常: %s", e)
        safe_print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
