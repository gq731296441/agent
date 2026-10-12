#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置审核报告 - 数据获取脚本

调用两个接口获取配置审核基础数据：
1. 接口一：获取配置管理领域检查单
   POST https://aip.zte.com.cn/cmtool/api/v1/config_audit/query
2. 接口二：获取改进任务详情
   POST https://aip.zte.com.cn/cmtool/api/v1/config_audit/queryTask

用法示例（工号与 Token 由平台注入的环境变量提供）：
    python3 /mnt/skills/config-audit-report-v7/scripts/fetch_config_audit.py \
        --emp-no "$coclaw_empno" \
        --auth-value "$coclaw_token" \
        --start-month 2026-08 --end-month 2026-08 \
        --belong-project "ZXODN V4.0" \
        --output-dir /workspace/output

说明：
- 所有 Body 参数均非必填，未指定则不传入
- pageSize 固定 50，当 total > pageSize 时遍历获取所有完整数据
"""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
import urllib.request
import urllib.error

BASE_URL_QUERY = "https://aip.zte.com.cn/cmtool/api/v1/config_audit/query"
BASE_URL_TASK = "https://aip.zte.com.cn/cmtool/api/v1/config_audit/queryTask"

PAGE_SIZE = 50

# 接口一：配置管理领域检查单 - 输出字段（中文名, 接口字段名）
CHECKLIST_FIELDS = [
    ("审核月份", "standardAuditMonth"),
    ("标识", "systemId"),
    ("所属项目", "belongProject"),
    ("审核分类", "auditClassification"),
    ("研发活动", "developmentActivities"),
    ("审核项", "auditItems"),
    ("检查要点", "checkPoints"),
    ("审核时机", "auditTiming"),
    ("审核依据", "auditBasis"),
    ("审核结果", "auditResult"),
    ("典型问题", "typicalQuestion"),
    ("改进建议", "improvementSuggest"),
]

# 接口二：改进任务详情 - 输出字段（中文名, 接口字段名）
TASK_FIELDS = [
    ("审核月份", "standardAuditMonth"),
    ("标识", "systemId"),
    ("状态", "systemState"),
    ("标题", "systemTitle"),
    ("所属项目", "belongProject"),
    ("所属中心", "belongCenter"),
    ("研发活动", "developmentActivities"),
    ("审核项", "auditItems"),
    ("典型问题", "typicalQuestion"),
    ("审核说明", "auditDescHtml"),
    ("改进建议", "improvementSuggest"),
    ("计划开始日期", "createdTime"),
    ("计划完成日期", "planFinishDate"),
    ("验收通过日期", "passedTimeOfAcceptance"),
    ("是否超期", "isOverdue"),  # 计算字段
    ("责任人", "bizField"),
]

# 超期判断相关的单据状态
STATE_EXECUTING = "执行中"
STATE_PENDING_ACCEPT = "待人工验收"
STATE_NEW = "新建"


def select_fields(record, fields):
    """按输出字段定义筛选记录，返回 {中文名: 值} 字典。"""
    result = {}
    for cn_name, field_name in fields:
        result[cn_name] = record.get(field_name)
    return result


def parse_date(value):
    """解析日期字符串为 datetime，支持多种格式，解析失败返回 None。"""
    if not value:
        return None
    if isinstance(value, (int, float)):
        # 可能是时间戳（毫秒或秒）
        try:
            ts = float(value)
            if ts > 1e12:
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts)
        except Exception:
            return None
    value = str(value).strip()
    # 常见日期格式
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def is_overdue(record, now=None):
    """
    计算"是否超期"字段。

    超期定义：
    - 情况1：单据状态为"执行中"或"待验收"，且 计划完成日期 < 当前日期
    - 情况2：单据状态为"新建"，当前时间 - 计划开始日期 > 60天
    符合情况1/情况2中任意一种都算超期，其他都算未超期。

    返回："超期" 或 "未超期"
    """
    if now is None:
        now = datetime.now()

    state = record.get("systemState")
    plan_finish = parse_date(record.get("planFinishDate"))
    created_time = parse_date(record.get("createdTime"))

    # 情况1：状态为"执行中"或"待验收"，且 计划完成日期 < 当前日期
    if state in (STATE_EXECUTING, STATE_PENDING_ACCEPT):
        if plan_finish is not None and plan_finish < now:
            return "超期"

    # 情况2：状态为"新建"，当前时间 - 计划开始日期 > 60天
    if state == STATE_NEW:
        if created_time is not None and (now - created_time) > timedelta(days=60):
            return "超期"

    return "未超期"


def process_checklist_records(records):
    """处理接口一数据：筛选输出字段。"""
    return [select_fields(r, CHECKLIST_FIELDS) for r in records]


def process_task_records(records):
    """处理接口二数据：筛选输出字段并计算"是否超期"。"""
    result = []
    for r in records:
        item = select_fields(r, TASK_FIELDS)
        item["是否超期"] = is_overdue(r)
        result.append(item)

    # 状态为"待确认拒绝"或"已确认拒绝"的单据放在表格最后
    result.sort(key=lambda x: 1 if x.get("状态") in (STATE_CONFIRMED_REJECT, STATE_PENDING_CONFIRM_REJECT) else 0)
    return result


# 审核结果相关常量
RESULT_NOT_PASS = "不通过"
RESULT_NOT_INVOLVED = "不涉及"
RESULT_PASS = "通过"
RESULT_PARTIAL_PASS = "部分通过"

# 改进任务状态相关常量
STATE_CONFIRMED_REJECT = "已确认拒绝"
STATE_PENDING_CONFIRM_REJECT = "待确认拒绝"
STATE_COMPLETED = "已完成"

# 验收通过日期字段名（接口2原始字段）
FIELD_ACCEPT_DATE = "passedTimeOfAcceptance"  # 验收通过日期


def compute_overview(check_records, task_records):
    """
    计算概览页的4个数据区域。

    参数：
    - check_records: 接口1原始详单数据
    - task_records: 接口2原始详单数据

    返回：
    {
        "region1": {...},   # 概览指标
        "region2": [...],   # 审核条目按研发活动分布
        "region3": [...],   # 改进任务按研发活动分布
        "region4": [...],   # 改进任务按期关闭率详情（按月）
    }
    """
    overview = {}

    # ===== 区域1：概览指标（基于接口1） =====
    total = len(check_records)
    not_pass = sum(1 for r in check_records if r.get("auditResult") == RESULT_NOT_PASS)
    not_involved = sum(1 for r in check_records if r.get("auditResult") == RESULT_NOT_INVOLVED)
    passed = sum(1 for r in check_records
                 if r.get("auditResult") in (RESULT_PASS, RESULT_PARTIAL_PASS))
    not_pass_ratio = round(not_pass / total * 100, 2) if total > 0 else 0

    overview["region1"] = {
        "审核记录总数": total,
        "不通过项数": not_pass,
        "不涉及项数": not_involved,
        "通过项数": passed,
        "不符合项占比": not_pass_ratio,
    }

    # ===== 区域2：审核条目按研发活动分布（基于接口1） =====
    dev_activity_count = {}
    for r in check_records:
        act = r.get("developmentActivities")
        if act:
            dev_activity_count[act] = dev_activity_count.get(act, 0) + 1
    # 按单据数从大到小排列
    region2 = [{"研发活动": k, "单据数": v}
               for k, v in sorted(dev_activity_count.items(), key=lambda x: x[1], reverse=True)]
    overview["region2"] = region2

    # ===== 区域3：改进任务按研发活动分布（基于接口2） =====
    task_activity_count = {}
    for r in task_records:
        act = r.get("developmentActivities")
        if act:
            task_activity_count[act] = task_activity_count.get(act, 0) + 1
    # 按单据数从大到小排列
    region3 = [{"研发活动": k, "单据数": v}
               for k, v in sorted(task_activity_count.items(), key=lambda x: x[1], reverse=True)]
    overview["region3"] = region3

    # ===== 区域4：改进任务按期关闭率详情，按月统计（基于接口2） =====
    # 按审核月份(standardAuditMonth)分组
    # 月份从全部任务中提取（含"已确认拒绝"），确保即使某月所有任务都被排除，
    # 该月也能正常显示；当某月没有符合规则的单据时，按期关闭率置为 "NA"。
    month_data = {}
    for r in task_records:
        # 取月份：优先用审核月份(standardAuditMonth)，否则用计划开始日期(createdTime)
        month = r.get("standardAuditMonth")
        if not month:
            created = parse_date(r.get("createdTime"))
            if created:
                month = created.strftime("%Y-%m")
        if not month:
            continue

        if month not in month_data:
            month_data[month] = {"不符合项数": 0, "按期关闭数": 0}

        # 状态非"已确认拒绝"的改进任务才计入不符合项数
        if r.get("systemState") in (STATE_CONFIRMED_REJECT, STATE_PENDING_CONFIRM_REJECT):
            continue

        month_data[month]["不符合项数"] += 1

        # 按期关闭数：状态="已完成" 且 验收通过日期 <= 计划完成日期
        if r.get("systemState") == STATE_COMPLETED:
            accept_date = parse_date(r.get(FIELD_ACCEPT_DATE))
            plan_finish = parse_date(r.get("planFinishDate"))
            if accept_date is not None and plan_finish is not None and accept_date <= plan_finish:
                month_data[month]["按期关闭数"] += 1

    # 按月排序并计算按期关闭率
    region4 = []
    total_not_pass = 0
    total_on_time = 0
    for month in sorted(month_data.keys()):
        d = month_data[month]
        not_pass_count = d["不符合项数"]
        on_time_count = d["按期关闭数"]
        total_not_pass += not_pass_count
        total_on_time += on_time_count
        # 没有符合规则的单据时，按期关闭率置为 "NA"
        rate = round(on_time_count / not_pass_count * 100, 2) if not_pass_count > 0 else "NA"
        region4.append({
            "月份": month,
            "不符合项数": not_pass_count,
            "按期关闭数": on_time_count,
            "按期关闭率": rate,
        })

    # 当数据涉及多个月份时，增加总计行
    if len(region4) > 1:
        total_rate = round(total_on_time / total_not_pass * 100, 2) if total_not_pass > 0 else "NA"
        region4.append({
            "月份": "总计",
            "不符合项数": total_not_pass,
            "按期关闭数": total_on_time,
            "按期关闭率": total_rate,
        })
    overview["region4"] = region4

    return overview


def build_body(page_num, page_size, start_month, end_month, belong_project):
    """构建请求 Body，未指定的参数不传入。"""
    body = {
        "pageNum": page_num,
        "pageSize": page_size,
    }
    if start_month and end_month:
        body["standardAuditMonth"] = [start_month, end_month]
    elif start_month:
        body["standardAuditMonth"] = [start_month, start_month]
    elif end_month:
        body["standardAuditMonth"] = [end_month, end_month]
    if belong_project:
        body["belongProject"] = belong_project
    return body


def call_api(url, emp_no, auth_value, body, retries=3):
    """调用接口，带重试机制。"""
    headers = {
        "X-Emp-No": emp_no,
        "X-Auth-Value": auth_value,
        "Content-Type": "application/json",
    }
    data = json.dumps(body).encode("utf-8")

    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            print(f"[错误] HTTP {e.code}: {err_body}", file=sys.stderr)
            if attempt < retries:
                time.sleep(2)
            else:
                raise
        except Exception as e:
            print(f"[错误] 请求失败(第{attempt}次): {e}", file=sys.stderr)
            if attempt < retries:
                time.sleep(2)
            else:
                raise
    return None


def fetch_all(url, emp_no, auth_value, start_month, end_month, belong_project):
    """获取全部数据，处理分页遍历。"""
    all_records = []
    page_num = 1
    total = None

    while True:
        body = build_body(page_num, PAGE_SIZE, start_month, end_month, belong_project)
        result = call_api(url, emp_no, auth_value, body)

        if result is None:
            break

        # 兼容不同返回结构：可能是 data.content / data.records / data.list / data 等
        data = result.get("data", result)
        if isinstance(data, dict):
            records = (data.get("content") or data.get("records")
                       or data.get("list") or data.get("rows") or data.get("items") or [])
            if total is None:
                total = data.get("total") or data.get("totalCount") or 0
        elif isinstance(data, list):
            records = data
            if total is None:
                total = len(records)
        else:
            records = []

        all_records.extend(records)

        # 判断是否还有更多页：当 total > pageSize 时，需要遍历获取所有完整数据
        if total is None:
            # 无法获取 total，按返回条数判断
            if len(records) < PAGE_SIZE:
                break
        else:
            if page_num * PAGE_SIZE >= total:
                break
            # 当 total > pageSize 时，继续翻页遍历获取所有完整数据
            if total > PAGE_SIZE:
                page_num += 1
            else:
                break

    return all_records, total


def main():
    parser = argparse.ArgumentParser(description="配置审核报告数据获取脚本")
    parser.add_argument("--emp-no", required=True, help="用户8位工号")
    parser.add_argument("--auth-value", required=True, help="用户UAC认证Token")
    parser.add_argument("--start-month", help="起始月份 YYYY-MM")
    parser.add_argument("--end-month", help="结束月份 YYYY-MM")
    parser.add_argument("--belong-project", help="所属项目")
    parser.add_argument("--output-dir", default="/workspace/output",
                        help="输出目录")
    args = parser.parse_args()

    import os
    os.makedirs(args.output_dir, exist_ok=True)

    print("=" * 60)
    print("开始获取配置审核数据...")
    print(f"工号: {args.emp_no}")
    print(f"时间范围: {args.start_month or '未指定'} ~ {args.end_month or '未指定'}")
    print(f"所属项目: {args.belong_project or '未指定(全部项目)'}")
    print("=" * 60)

    # 接口一：配置管理领域检查单
    print("\n[接口一] 获取配置管理领域检查单...")
    check_records, check_total = fetch_all(
        BASE_URL_QUERY, args.emp_no, args.auth_value,
        args.start_month, args.end_month, args.belong_project
    )
    print(f"  检查单数据: 共 {len(check_records)} 条 (total={check_total})")

    # 接口二：改进任务详情
    print("\n[接口二] 获取改进任务详情...")
    task_records, task_total = fetch_all(
        BASE_URL_TASK, args.emp_no, args.auth_value,
        args.start_month, args.end_month, args.belong_project
    )
    print(f"  改进任务数据: 共 {len(task_records)} 条 (total={task_total})")

    # 处理数据：筛选输出字段 + 计算"是否超期"
    print("\n[处理] 筛选输出字段并计算'是否超期'...")
    check_processed = process_checklist_records(check_records)
    task_processed = process_task_records(task_records)
    overdue_count = sum(1 for t in task_processed if t.get("是否超期") == "超期")
    print(f"  检查单: 输出 {len(check_processed)} 条")
    print(f"  改进任务: 输出 {len(task_processed)} 条，其中超期 {overdue_count} 条")

    # 计算概览数据
    print("\n[处理] 计算概览数据...")
    overview = compute_overview(check_records, task_records)
    print(f"  概览指标: 审核记录总数={overview['region1']['审核记录总数']}, "
          f"不通过={overview['region1']['不通过项数']}, "
          f"不符合项占比={overview['region1']['不符合项占比']}%")
    print(f"  审核条目按研发活动分布: {len(overview['region2'])} 类")
    print(f"  改进任务按研发活动分布: {len(overview['region3'])} 类")
    print(f"  改进任务按期关闭率(按月): {len(overview['region4'])} 个月")

    # 保存结果
    check_file = os.path.join(args.output_dir, "config_audit_checklist.json")
    task_file = os.path.join(args.output_dir, "config_audit_tasks.json")
    overview_file = os.path.join(args.output_dir, "config_audit_overview.json")

    with open(check_file, "w", encoding="utf-8") as f:
        json.dump(check_processed, f, ensure_ascii=False, indent=2)
    with open(task_file, "w", encoding="utf-8") as f:
        json.dump(task_processed, f, ensure_ascii=False, indent=2)
    with open(overview_file, "w", encoding="utf-8") as f:
        json.dump(overview, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print("数据获取完成！")
    print(f"  检查单数据已保存: {check_file}")
    print(f"  改进任务数据已保存: {task_file}")
    print(f"  概览数据已保存: {overview_file}")
    print("=" * 60)


if __name__ == "__main__":
    main()
