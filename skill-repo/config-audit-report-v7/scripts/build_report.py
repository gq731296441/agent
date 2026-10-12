#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基于独立模板生成配置审核报告 HTML。

模板为完整独立 HTML（含内联 CSS/JS），通过全局变量 REPORT_DATA 驱动渲染。
本脚本读取模板，替换标题、审核月份与 REPORT_DATA 数据，生成新报告。

用法：
    python3 /mnt/skills/config-audit-report-v7/scripts/build_report.py \
        --template /mnt/skills/config-audit-report-v7/template/家庭网络项目V3.0_2026年8月配置审核报告.html \
        --project-name "ZXODN V4.0" \
        --audit-month "2026-07" \
        --checklist /workspace/output/config_audit_checklist.json \
        --tasks /workspace/output/config_audit_tasks.json \
        --output /workspace/output/ZXODN_V4.0_2026年7月配置审核报告.html
"""

import argparse
import json
import os
import re
from datetime import datetime

# 饼图配色（按顺序分配）
PIE_COLORS = [
    "#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#06b6d4",
    "#ec4899", "#14b8a6", "#f97316", "#84cc16", "#6366f1",
    "#ef4444", "#0ea5e9", "#a855f7", "#22c55e", "#eab308",
]

STATE_CONFIRMED_REJECT = "已确认拒绝"
STATE_PENDING_CONFIRM_REJECT = "待确认拒绝"
STATE_COMPLETED = "已完成"
STATE_EXECUTING = "执行中"
STATE_PENDING_ACCEPT = "待人工验收"
STATE_NEW = "新建"
RESULT_NOT_PASS = "不通过"
RESULT_NOT_INVOLVED = "不涉及"
RESULT_PASS = "通过"
RESULT_PARTIAL_PASS = "部分通过"

# 检查单审核结果排序权重（不通过 > 通过 > 部分通过 > 其他(-)）
CHECKLIST_RESULT_ORDER = {
    RESULT_NOT_PASS: 0,
    RESULT_PASS: 1,
    RESULT_PARTIAL_PASS: 2,
    # 其他（含 None / "-"）权重最大，排最后
}

# 改进任务状态排序权重（新建 > 执行中 > 待人工验收 > 已完成 > 待确认拒绝 > 已确认拒绝）
TASK_STATE_ORDER = {
    STATE_NEW: 0,
    STATE_EXECUTING: 1,
    STATE_PENDING_ACCEPT: 2,
    STATE_COMPLETED: 3,
    STATE_PENDING_CONFIRM_REJECT: 4,
    STATE_CONFIRMED_REJECT: 5,
}


def result_sort_key(item):
    """检查单排序键：先按审核结果权重，再按审核月份升序。"""
    result = item.get("审核结果")
    weight = CHECKLIST_RESULT_ORDER.get(result, 3)
    month = str(item.get("审核月份") or "")
    return (weight, month)


def task_sort_key(item):
    """改进任务排序键：先按状态权重，再按审核月份升序。"""
    state = item.get("状态")
    weight = TASK_STATE_ORDER.get(state, 6)
    month = str(item.get("审核月份") or "")
    return (weight, month)


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def compute_stats(checklist, tasks):
    """计算统计卡片数据（基于处理后的中文键名数据）。"""
    total = len(checklist)
    not_pass = sum(1 for r in checklist if r.get("审核结果") == RESULT_NOT_PASS)
    not_involved = sum(1 for r in checklist if r.get("审核结果") == RESULT_NOT_INVOLVED)
    passed = sum(1 for r in checklist if r.get("审核结果") in (RESULT_PASS, RESULT_PARTIAL_PASS))
    not_pass_ratio = round(not_pass / total * 100, 1) if total > 0 else 0
    return {
        "total": total,
        "notPass": not_pass,
        "notInvolved": not_involved,
        "passed": passed,
        "notPassRatio": not_pass_ratio,
        "taskTotal": len(tasks),
    }


def compute_distribution(records, activity_field):
    """按研发活动统计分布，从大到小排列，计算百分比。"""
    count = {}
    for r in records:
        act = r.get(activity_field)
        if act:
            count[act] = count.get(act, 0) + 1
    total = sum(count.values())
    items = []
    for i, (label, value) in enumerate(sorted(count.items(), key=lambda x: x[1], reverse=True)):
        percentage = round(value / total * 100, 1) if total > 0 else 0
        items.append({
            "label": label,
            "value": value,
            "percentage": percentage,
            "color": PIE_COLORS[i % len(PIE_COLORS)],
        })
    return items


def compute_closure(tasks):
    """计算按期关闭率（按月统计，多个月份时增加总计行）。

    月份从全部任务中提取（含"已确认拒绝"），确保即使所有任务都被排除，
    月份也能正常显示。当没有符合规则的单据（not_pass_count==0）时，
    按期关闭率置为 "NA"。

    返回结构：
    {
        "months": [{"月份","不符合项数","按期关闭数","按期关闭率"}, ...],  # 按月，升序
        "total": {"月份":"总计","不符合项数","按期关闭数","按期关闭率"},     # 多个月份时存在
        "month": 首个月份（兼容旧字段）,
        "notPassCount": 总不符合项数,
        "onTimeCount": 总按期关闭数,
        "onTimeRate": 总按期关闭率,
    }
    """
    # 排除"待确认拒绝"和"已确认拒绝"
    valid = [t for t in tasks if t.get("状态") not in (STATE_CONFIRMED_REJECT, STATE_PENDING_CONFIRM_REJECT)]

    # 按月分组
    month_data = {}
    for t in valid:
        month = t.get("审核月份") or ""
        if not month:
            created = t.get("计划开始日期") or ""
            if created:
                month = str(created)[:7]
        if not month:
            continue
        if month not in month_data:
            month_data[month] = {"不符合项数": 0, "按期关闭数": 0}
        month_data[month]["不符合项数"] += 1
        if t.get("状态") == STATE_COMPLETED:
            accept = t.get("验收通过日期")
            plan = t.get("计划完成日期")
            if accept and plan and str(accept) <= str(plan):
                month_data[month]["按期关闭数"] += 1

    months = []
    total_not_pass = 0
    total_on_time = 0
    for month in sorted(month_data.keys()):
        d = month_data[month]
        not_pass_count = d["不符合项数"]
        on_time_count = d["按期关闭数"]
        total_not_pass += not_pass_count
        total_on_time += on_time_count
        rate = round(on_time_count / not_pass_count * 100, 1) if not_pass_count > 0 else "NA"
        months.append({
            "月份": month,
            "不符合项数": not_pass_count,
            "按期关闭数": on_time_count,
            "按期关闭率": rate,
        })

    total_rate = round(total_on_time / total_not_pass * 100, 1) if total_not_pass > 0 else "NA"
    total = {
        "月份": "总计",
        "不符合项数": total_not_pass,
        "按期关闭数": total_on_time,
        "按期关闭率": total_rate,
    }

    # 兼容旧字段：取首个月份
    month = months[0]["月份"] if months else ""

    return {
        "months": months,
        "total": total,
        "month": month,
        "notPassCount": total_not_pass,
        "onTimeCount": total_on_time,
        "onTimeRate": total_rate,
    }


def format_date(value):
    if not value:
        return "-"
    s = str(value)
    return s[:10]


def strip_html(value):
    """去除HTML标签，保留纯文本内容。"""
    if not value:
        return value
    import re as _re
    text = _re.sub(r'<[^>]*>', '', str(value))
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&')
    text = text.replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"').replace('&#39;', "'")
    text = _re.sub(r'\s+', ' ', text).strip()
    return text


def build_report_data(checklist, tasks):
    """构建 REPORT_DATA 数据结构。"""
    stats = compute_stats(checklist, tasks)
    audit_dist = compute_distribution(checklist, "研发活动")
    task_dist = compute_distribution(tasks, "研发活动")
    closure = compute_closure(tasks)

    # 检查单明细（12列，含审核月份），按审核结果权重 + 审核月份排序
    checklist_rows = []
    for r in checklist:
        checklist_rows.append({
            "审核月份": r.get("审核月份"),
            "标识": r.get("标识"),
            "所属项目": r.get("所属项目"),
            "审核分类": r.get("审核分类"),
            "研发活动": r.get("研发活动"),
            "审核项": r.get("审核项"),
            "检查要点": r.get("检查要点"),
            "审核时机": r.get("审核时机"),
            "审核依据": r.get("审核依据"),
            "审核结果": r.get("审核结果"),
            "典型问题": r.get("典型问题"),
            "改进建议": r.get("改进建议"),
        })
    checklist_rows.sort(key=result_sort_key)

    # 改进任务明细（15列，含审核月份），按状态权重 + 审核月份排序
    task_rows = []
    for t in tasks:
        task_rows.append({
            "审核月份": t.get("审核月份"),
            "标识": t.get("标识"),
            "状态": t.get("状态"),
            "标题": t.get("标题"),
            "所属项目": t.get("所属项目"),
            "审核项": t.get("审核项"),
            "典型问题": t.get("典型问题"),
            "审核说明": strip_html(t.get("审核说明")),
            "改进建议": t.get("改进建议"),
            "计划完成日期": format_date(t.get("计划完成日期")),
            "责任人": t.get("责任人"),
            "是否超期": t.get("是否超期", "未超期"),
        })
    task_rows.sort(key=task_sort_key)

    # 月份筛选数据（去重、升序）
    checklist_months = sorted({str(r.get("审核月份")) for r in checklist_rows if r.get("审核月份")})
    task_months = sorted({str(t.get("审核月份")) for t in task_rows if t.get("审核月份")})

    # TAB标题状态统计
    checklist_status = {}
    for r in checklist_rows:
        result = r.get("审核结果") or "-"
        checklist_status[result] = checklist_status.get(result, 0) + 1
    task_status = {}
    for t in task_rows:
        state = t.get("状态") or "-"
        task_status[state] = task_status.get(state, 0) + 1

    return {
        "stats": stats,
        "auditDist": audit_dist,
        "taskDist": task_dist,
        "closure": closure,
        "checklist": checklist_rows,
        "tasks": task_rows,
        "checklistMonths": checklist_months,
        "taskMonths": task_months,
        "checklistStatus": checklist_status,
        "taskStatus": task_status,
    }


def generate_html(template_path, project_name, audit_month, report_data, generate_date):
    """读取模板，替换标题、审核月份与 REPORT_DATA。"""
    with open(template_path, encoding="utf-8") as f:
        html = f.read()

    # 替换标题
    html = re.sub(
        r'<h1 class="report-title">.*?</h1>',
        f'<h1 class="report-title">{project_name} 项目配置审核报告</h1>',
        html,
        flags=re.DOTALL,
    )
    # 替换审核范围
    html = re.sub(
        r'<p class="report-subtitle">.*?</p>',
        f'<p class="report-subtitle">审核范围：{audit_month}</p>',
        html,
        flags=re.DOTALL,
    )
    # 替换页脚生成日期
    html = re.sub(
        r'<span>由 <strong style="color:#475569;">智搭 AI助手</strong> 生成 · 中兴通讯有线技术部 · .*?</span>',
        f'<span>由 <strong style="color:#475569;">智搭 AI助手</strong> 生成 · 中兴通讯有线技术部 · {generate_date}</span>',
        html,
        flags=re.DOTALL,
    )

    # 替换 REPORT_DATA
    # 注意：re.sub 的替换字符串会解析反斜杠转义（如 \n 会变成真实换行），
    # 因此必须使用替换函数，避免破坏 json.dumps 生成的合法转义序列。
    data_json = json.dumps(report_data, ensure_ascii=False)
    html = re.sub(
        r'const REPORT_DATA = \{.*?\};',
        lambda m: 'const REPORT_DATA = ' + data_json + ';',
        html,
        flags=re.DOTALL,
    )

    return html


def main():
    parser = argparse.ArgumentParser(description="基于独立模板生成配置审核报告HTML")
    parser.add_argument("--template", required=True, help="模板HTML路径")
    parser.add_argument("--project-name", required=True, help="项目名称")
    parser.add_argument("--audit-month", required=True, help="审核月份，如 2026-07")
    parser.add_argument("--checklist", required=True, help="检查单数据JSON路径")
    parser.add_argument("--tasks", required=True, help="改进任务数据JSON路径")
    parser.add_argument("--output", required=True, help="输出HTML文件路径")
    args = parser.parse_args()

    checklist = load_json(args.checklist)
    tasks = load_json(args.tasks)

    report_data = build_report_data(checklist, tasks)
    generate_date = datetime.now().strftime("%Y年%m月%d日")

    html = generate_html(args.template, args.project_name, args.audit_month,
                         report_data, generate_date)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)

    print("=" * 60)
    print("配置审核报告HTML生成完成！")
    print(f"  项目: {args.project_name}")
    print(f"  审核月份: {args.audit_month}")
    print(f"  审核项总数: {report_data['stats']['total']}")
    print(f"  不符合项数: {report_data['stats']['notPass']}")
    print(f"  不涉及项数: {report_data['stats']['notInvolved']}")
    print(f"  通过项数: {report_data['stats']['passed']}")
    print(f"  不符合项占比: {report_data['stats']['notPassRatio']}%")
    print(f"  检查单明细: {len(report_data['checklist'])} 条")
    print(f"  改进任务明细: {len(report_data['tasks'])} 条")
    print(f"  输出文件: {args.output}")
    print("=" * 60)


if __name__ == "__main__":
    main()
