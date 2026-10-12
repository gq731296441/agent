---
name: config-audit-report
description: 生成配置审核报告（Config Audit Report）。当用户提到"配置审核报告"、"项目配置审核报告"、"配置审核"、"配置管理领域检查单"、"改进计划/改进任务"等关键词，或需要查询配置管理领域检查单数据、改进任务详情并生成配置审核统计与明细报告时，使用本技能。
version: 1.2.0
author: 00294192
changes: |
  - v1.2.0: 新增 scripts/create_icenter_page_with_macro.py：创建 iCenter 页面时同步通过 HTML 宏（HtmlIncludeMacro）嵌入报告链接，一步完成建页+嵌入
  - v1.1.0: 优化报告功能：表格增加"审核月份"列；按单据状态+审核月排序；多月份时增加下拉筛选；TAB标题显示状态统计；按期关闭率按月+总计展示；每次生成报告均重新从接口获取数据
  - v1.0.0: 初始版本，按 OpenClaw 标准模板重构；接口文档迁移至 references/api_docs.md
generator: co-skill-creator
tags: ["研发提效"]
---

## 安全准则
- 接口认证使用用户工号（`X-Emp-No`）与 UAC 认证 Token（`X-Auth-Value`），由智能体从系统上下文获取，**不得**在文档或日志中明文记录或外泄。
- 生成的报告可能包含项目内部审核数据，仅限授权范围内使用与分发。

## 概述
本技能用于生成**配置审核报告**。通过调用配置管理领域接口获取检查单与改进任务数据，自动完成统计计算，并生成包含"概览、配置管理领域检查单、改进计划"三个 TAB 页的 HTML 报告。

### 核心能力
| 能力 | 说明 |
|------|------|
| 数据获取 | 调用 2 个接口获取配置管理领域检查单与改进任务详情，自动处理分页遍历 |
| 统计计算 | 计算概览指标、按研发活动分布、改进任务按期关闭率等统计结果 |
| 报告生成 | 基于模板生成 3 个 TAB 页的配置审核 HTML 报告 |
| 超期判断 | 依据状态与日期自动计算"是否超期"字段 |

### 运行环境约定（智能体平台）

- 本技能在沙箱容器内以**只读**方式挂载在 `/mnt/skills/config-audit-report-v7/`，`execute` 命令中的脚本、模板路径均使用该前缀；用文件工具（`read_file` 等）读取技能文件时用 `/skills/config-audit-report-v7/`。
- `/workspace/` 为当前会话工作区（可读写），所有产物统一写入 `/workspace/output/`。
- 工号与认证 Token 由平台注入到每次命令执行的环境变量 `coclaw_empno` / `coclaw_token` 中，命令里**直接引用变量**（如 `--emp-no "$coclaw_empno"`），不要向用户索要，也不要写死具体值。

## 使用方式

### 第一步：识别并提取参数

根据用户输入提取以下参数：

| 参数 | 说明 | 是否必填 |
|------|------|----------|
| `emp_no` | 员工编号，用于接口认证（X-Emp-No）；取平台注入的环境变量 `coclaw_empno` | 是 |
| `auth_value` | 认证令牌，用于接口认证（X-Auth-Value）；取平台注入的环境变量 `coclaw_token` | 是 |
| `belong_project` | 所属项目名称，如"智能项目 V3.00" | 否 |
| `start_month` | 查询起始月份，格式"YYYY-MM"，如"2026-01" | 否 |
| `end_month` | 查询结束月份，格式"YYYY-MM"，如"2026-12" | 否 |
| `page_size` | 每页数据条数，默认50 | 否 |

**默认规则：**
- 如果用户未指定明确的查询时间范围，默认查询时间范围为**今年**。
- 如果用户未指定明确项目，默认获取**全部项目**的数据（即有线研究院的数据）。

### 第二步：调用脚本获取数据

推荐使用技能自带的 Python 脚本 `scripts/fetch_config_audit.py` 获取数据，脚本会自动处理分页遍历逻辑。

```bash
python3 /mnt/skills/config-audit-report-v7/scripts/fetch_config_audit.py \
    --emp-no "$coclaw_empno" \
    --auth-value "$coclaw_token" \
    --start-month 2026-08 \
    --end-month 2026-08 \
    --belong-project "ZXODN V4.0" \
    --output-dir /workspace/output
```

**脚本参数：**

| 参数 | 说明 | 是否必填 |
|------|------|----------|
| `--emp-no` | 用户8位工号，取 `$coclaw_empno` | 是 |
| `--auth-value` | 用户UAC认证Token，取 `$coclaw_token` | 是 |
| `--start-month` | 起始月份 YYYY-MM | 否 |
| `--end-month` | 结束月份 YYYY-MM | 否 |
| `--belong-project` | 所属项目 | 否 |
| `--output-dir` | 输出目录，默认 `/workspace/output` | 否 |

**脚本输出：**
- `config_audit_checklist.json`：配置管理领域检查单数据
- `config_audit_tasks.json`：改进任务详情数据
- `config_audit_overview.json`：概览统计数据

> **⚠️ 输出目录约定：** 所有生成的文件（数据 JSON、HTML 报告）**必须输出到技能目录之外**，默认统一输出到 `/workspace/output/` 目录。**禁止**将生成文件写入 `/mnt/skills/config-audit-report-v7/` 技能目录内（该目录为只读挂载，仅存放技能代码、模板与文档）。

### 第三步：生成报告

根据获取的数据生成配置审核统计与明细报告（3 个 TAB 页：概览、配置管理领域检查单、改进计划）。

**注意：** 每次生成报告都必须**重新调用接口获取最新数据**（执行第二步），不得复用历史数据文件。

推荐使用 `scripts/build_report.py`（基于 `template/` 下的独立 HTML 模板生成）：

```bash
python3 /mnt/skills/config-audit-report-v7/scripts/build_report.py \
    --template "/mnt/skills/config-audit-report-v7/template/家庭网络项目V3.0_2026年8月配置审核报告.html" \
    --project-name "家庭网络项目V3.0" \
    --audit-month "2026-08" \
    --checklist /workspace/output/config_audit_checklist.json \
    --tasks /workspace/output/config_audit_tasks.json \
    --output /workspace/output/家庭网络项目V3.0_2026年8月配置审核报告.html
```

报告结构说明：
- **TAB 1 概览**：概览指标（审核记录总数、不通过/不涉及/通过项数、不符合项占比）、审核条目按研发活动分布、改进任务按研发活动分布、改进任务按期关闭率（按月，多个月份时含总计行）。
- **TAB 2 配置管理领域检查单**：接口一明细表格（12 列，含"审核月份"列），按审核结果权重（不通过→通过→部分通过→其他）+ 审核月份升序排序；标题显示"总计：xx条，XX状态：XX条"；多个月份时提供月份下拉筛选。
- **TAB 3 改进计划**：接口二明细表格（15 列，含"审核月份"列），按状态权重（新建→执行中→待人工验收→已完成→待确认拒绝→已确认拒绝）+ 审核月份升序排序；标题显示"总计：xx条，XX状态：XX条"；多个月份时提供月份下拉筛选。

### 第四步：创建 iCenter 页面并同步嵌入报告（可选）

当用户要求"把报告发布到 iCenter 页面"、"在 iCenter 页面嵌入报告"、"新建页面并嵌入报告"时，可使用 `scripts/create_icenter_page_with_macro.py`，在**创建页面时同步**通过 HTML 宏（HtmlIncludeMacro）嵌入报告链接，一步完成"建页 + 嵌入"。

> **原理说明：** iCenter 的"HTML 宏"本质上是页面正文 HTML 中的一段宏标记（`<div macro-key="HtmlIncludeMacro" m-url="<链接>" ...>`）。因此创建页面时直接把宏标记作为页面正文（contentBody）传入即可，无需先建页再二次回写。

```bash
python3 /mnt/skills/config-audit-report-v7/scripts/create_icenter_page_with_macro.py \
    --space-id 15c203c3742d49698037d6ced74a09ed \
    --parent-id 6b545da5b17f11f18528c70fe21883a3 \
    --title "ZXUPN CSR V6.2_2026年7月配置审核报告" \
    --url "https://aip.zte.com.cn/cmtool/api/agenta/workspace/html-share/<报告分享ID>"
```

**脚本参数：**

| 参数 | 说明 | 是否必填 |
|------|------|----------|
| `--space-id` | 空间 ID | 是 |
| `--parent-id` | 父页面 ID（新页面创建在该父页面下） | 是 |
| `--title` | 新页面标题（建议与报告名称一致） | 是 |
| `--url` | 要嵌入的报告链接（**可变参数**，传入本轮实际生成的 HTML 报告免登录分享链接，形如 `https://aip.zte.com.cn/cmtool/api/agenta/workspace/html-share/xxx`） | 是 |
| `--page-height` | 嵌入页面高度（px），默认 1600 | 否 |
| `--page-width` | 嵌入页面宽度（px），默认 1600 | 否 |

**认证：** 工号/Token 从环境变量读取（`coclaw_empno` / `coclaw_token`），需在执行前设置。

**输出：** 成功时返回 JSON，含 `page_link`（新页面链接）、`page_id`、`title`、`embed_url` 等字段。

> **⚠️ URL 取值规则：** `--url` 必须传入**本轮任务实际生成的 HTML 报告分享链接**（或用户明确指定的目标 URL），**禁止**使用示例占位链接或写死的地址。

### 报告功能说明（v1.1.0）

1. **审核月份列**：检查单与改进计划表格最左侧新增"审核月份"列，取接口返回的 `standardAuditMonth` 字段。
2. **排序规则**：
   - 检查单：按审核结果排序（不通过 → 通过 → 部分通过 → "-"），同结果内按审核月份升序。
   - 改进计划：按状态排序（新建 → 执行中 → 待人工验收 → 已完成 → 待确认拒绝 → 已确认拒绝），同状态内按审核月份升序。
3. **月份下拉筛选**：当数据涉及多个月份时，检查单与改进计划 TAB 页顶部显示月份下拉框，可选"全部月份"或具体月份，月份由小到大排列。
4. **TAB 标题状态统计**：标题下方另起一行显示状态统计摘要，用不同颜色小圆点 + 状态名 + 冒号 + 单据数表示（如"● 总计：76　● 不通过：15　● 通过：32"），字号与"审核月份筛选"一致。
5. **按期关闭率**：当数据范围涉及多个月份时，按月统计每个月份的不符合项数、按期关闭数、按期关闭率（按月份从小到大排列），并增加"总计"行；"按期关闭率"列数值带"%"（"NA"除外）。
6. **标识超链接**：配置管理领域检查单与改进计划 TAB 页的"标识"列记录添加超链接，链接地址为 `https://rdcloud.zte.com.cn/uportal/rd-uportal/workspaces/YXCA/apps/wim/allWorkItems/{标识}`（如 `YXCA-68566` → `.../allWorkItems/YXCA-68566`），点击在新标签页打开。

## 参考文档
- [接口文档与字段定义](references/api_docs.md)：两个接口的调用方式、CURL 示例、输出字段定义、"是否超期"计算逻辑、报告生成规则。
