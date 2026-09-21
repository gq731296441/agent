---
name: workload-data-monitoring
description: 提供指定工作区的工作项数据查询、统计分析、异常监控和报告生成，支持交互式和CLI两种模式。
author: 庄静文00351023
tags: ["数据分析"]
version: 1.0.0
triggers: ["工作量", "统计", "监控", "Wiki", "邮件"]
---

# 工作量数据监控技能

## 字段说明
- workspace: 明确工作区名称（如：UMEONE、PLM、业务运营等）
- project_name: 明确项目名称（如：ZENIC ONE-UME R22 V16.26.20）
- sprint_filter: 明确查询迭代，支持以下写法：
   - 单迭代："sprint09"
   - 多迭代（逗号分隔）："sprint08,sprint09" 或 "2026-sprint08,2026-sprint09"
   - 支持带年份前缀："2026-sprint08" 等，会模糊匹配迭代字段

## 功能概述

该技能基于指定工作区的数据，提供四项查询与分析功能：

1. **任务数目** - 生成表1（各团队任务统计）和表2（各团队任务完成比例）
2. **任务工作量** - 生成表3（各团队工作量统计）和表4（各团队工作量占比）
3. **异常数据** - 生成表5（预计工作量异常）和表6（实际工作量异常），支持自定义阈值与状态
4. **项目级报告** - 生成全部表格，并可发布到Wiki页面

## 数据来源

- 数据源：`{workspace}` 工作区的 `studio-rdcloud-workitem` 技能导出文件
- 数据文件：会话工作区 `/workspace/exports/studio-rdcloud-workitem/export_work_item_*.xlsx`（24 小时内字段完备的导出会被直接复用，否则由本技能自动调用 `studio-rdcloud-workitem` 重新导出）
- 报告产物：会话工作区 `/workspace/reports/workload_report_<迭代>.md`
- 字段：包括团队、负责人、预计工作量、实际工作量、任务状态等

## 交互模式

### 流程控制

1. 用户提供 **项目工作区**、**项目名称** 与 **查询迭代**（一次提供三行）
2. 选择查询类型：
   1. 任务数目
   2. 任务工作量
   3. 异常数据
   4. 生成项目级报告
3. 若选择3，则额外配置阈值与状态（默认30/closed），并询问邮件操作：
   - 收件人（必填）：支持单个邮箱或逗号分隔的多个邮箱
   - 抄送人（可选）：单个邮箱或逗号分隔
4. 根据查询类型：
   - 类型1/2：直接生成结果
   - 类型3：生成异常表格后，发送邮件（正文仅包含预计工作量或实际工作量为0的任务）
   - 类型4：生成全部表格后，询问Wiki页面URL并发布

### 消息格式

- 提示：使用 `status: "input_required"` + `prompt` 字符串（仅显示1-4选项）
- 结果：`status: "success"` + `markdown` 字段（含表格） + `output_file` + 可选 `wiki_link`

## CLI模式

支持命令行一次性执行，无需交互：

```bash
# 查询异常数据并发送邮件（仅工作量为0的任务）
python3 scripts/main_v2.py \
  --workspace "UMEONE" \
  --project "ZENIC ONE-UME R22 V16.26.20" \
  --sprint "2026-sprint08,2026-sprint09" \
  --type 3 \
  --threshold 30 \
  --status closed \
  --email "收件人邮箱" \
  [--cc "抄送人邮箱"] \
  --email-yes

# 参数说明
- --workspace: 项目工作区（必填，如 UMEONE、PLM 等）
- --project: 项目名称（必填）
- --sprint: 查询迭代（必填，如 sprint09 或 2026-sprint08,2026-sprint09）
- --type: 查询类型（必填，1-4）
- --threshold: 异常阈值（可选，默认30）
- --status: 实际工作量异常状态（可选，默认closed）
- --email: 邮件收件人（可选，支持 all=全部责任人, none=不发送, 或指定邮箱/工号）
- --cc: 邮件抄送人（可选）
- 邮件标题自动生成："各位好，内文为{项目名称}工作量数据异常任务，请各位及时治理，谢谢！"
- 邮件正文：仅包含预计工作量和实际工作量为0的任务（不包括超过阈值的任务）
- 表格表头：工作项类型、标识、标题、状态、预计工作量/实际工作量、团队、任务负责人
```

## 依赖

- Python 3.8+
- `openpyxl`、`pandas`

## 文件结构

```
skills/工作量数据监控/
├── SKILL.md
├── scripts/
│   ├── workload_analyzer.py
│   ├── wiki_publisher.py
│   ├── email_notifier.py
│   └── main_v2.py
└── logs/ (由依赖的技能生成)
```

## 注意事项

- 邮件发送功能需 `oa-zmail-manage` 技能支持
- Wiki 发布依赖 `studio-space-manager` 的页面创建能力
- 阈值与状态参数仅对异常分析有效