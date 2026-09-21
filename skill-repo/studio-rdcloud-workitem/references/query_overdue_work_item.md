# 工作项超期查询场景（query_overdue_work_item）


> **工具约束（必遵）**：须通过本技能已有 `scripts/` 脚本执行研发云工作项能力；**禁止**使用 write、edit、web-search、web_fetch 等处理本业务或替代脚本；**禁止**新建 Python 脚本。

查询指派给我、计划完成日期已过（超期）的工作项。默认类型：**任务**；可指定其他类型如用户故事、缺陷等。

## 脚本

`scripts/atomic/query_overdue_work_item.py`

## 触发语义

用户说以下类似内容时触发：

- 查询我超期的任务
- 查询超期的工作项
- 查询我超期的缺陷
- 查询超期的用户故事和产品需求
- 哪些任务超期了
- 我有哪些到期未完成的单据

## 参数说明

| 参数 | 别名 | 默认值 | 说明 |
|------|------|--------|------|
| `--work-item-type` | `--workItemType` | `任务` | 工作项类型，英文逗号分隔，如 `任务,缺陷` |
| `--limit` / `-n` | — | `20` | 返回条数上限，最大 10000（超过按 10000） |
| `--order-by` | `--orderBy` | 按超期日期升序 | 排序字段，如 `PlanFinishDate asc` |
| `--raw-json` | `--rawJson` | — | 输出原始 JSON |
| `--user-input` | `--userInput` | — | 用户原始输入（完整一句话） |

## 超期日期字段映射

| 工作项类型 | 超期日期字段 |
|-----------|-------------|
| 任务、用户故事、产品需求 | `PlanFinishDate` |
| 变更请求 | `ExpectedResolvedDate` |
| 缺陷 | `PlanResolvedDate` |

## 查询逻辑

1. 使用 `SYSMySpace` 工作区 + `crossWorkspace=true`，配合 `myWorkspaceConditions` 筛选「指派给我」的工作项。
2. 按工作项类型确定超期日期字段，构建 `日期字段 < @Today` 条件。
3. 多类型时：若日期字段不同，自动以 OR 括号分组，如 `(PlanFinishDate < @Today OR ExpectedResolvedDate < @Today)`。
4. 自动追加时间范围条件：`System_ChangedDate > @Today-365d`（限制查询范围）。
5. 自动排除已取消/处理完成状态（未完结的超期项才有意义）。
6. 类型通过顶层 `workItemTypeKeys` 过滤（不放在 `sourceClauses`），与研发云跨工作区查询规范一致。

## 结果格式

Markdown 表格，7 列：

| 标题 | 标识 | 工作项类型 | 状态 | 计划完成日期 | 更新时间 | 超期天数 |
|------|------|-----------|------|-------------|---------|---------|
| [title](url) | [id](url) | 任务 | 进行中 | 2026-01-01 | 2026-03-01 10:00:00 | 75 |

- **标题**与**标识**均带跳转链接，点击直达工作项详情页。
- **超期天数** = 今日日期 − 计划完成日期（单位：天）。
- 默认按超期日期升序排列（最早超期的在最前）。

## 调用示例

```bash
# 查询我超期的任务（默认）
python3 scripts/atomic/query_overdue_work_item.py --user-input "查询我超期的任务"

# 查询我超期的缺陷
python3 scripts/atomic/query_overdue_work_item.py --work-item-type 缺陷 --user-input "查询我超期的缺陷"

# 查询超期的任务和用户故事，限 50 条
python3 scripts/atomic/query_overdue_work_item.py --work-item-type 任务,用户故事 --limit 50

# 输出原始 JSON
python3 scripts/atomic/query_overdue_work_item.py --work-item-type 任务 --raw-json
```

## 配置（可选）

工作项类型映射、超期日期字段映射等可从 `scripts/atomic/query_overdue_work_item_config.json` 读取；缺失时使用脚本内置默认。

示例结构：

```json
{
  "work_item_type_mapping": { "任务": "Task", "缺陷": "Bug", ... },
  "overdue_date_field_map": { "任务": "PlanFinishDate", "缺陷": "PlanResolvedDate", ... },
  "default_overdue_date_field": "PlanFinishDate",
  "all_overdue_date_fields": ["PlanFinishDate", "ExpectedResolvedDate", "PlanResolvedDate"]
}
```

配置文件与内置默认会合并：仅覆盖你提供的键，其余沿用默认。

## 禁止事项

- 不要使用 `System_DueDate`（无此字段）。
- 不要使用 `System_PlanFinishDate`（字段名为 `PlanFinishDate`）。
- 超期条件只写一条（按类型）；多类型时自动用 OR 组合，不要重复拼入。
- 不要在 `sourceClauses` 中写工作项类型条件，类型过滤用顶层 `workItemTypeKeys`。

## 迁移说明

本场景功能来自独立技能 `query-overdue-work-item`（UUID: `e3e894cb-9864-4719-952b-915232c88264`），
该技能已废弃，请改用本脚本。主要差异：

| | 旧技能 | 新场景 |
|---|--------|--------|
| 调用方式 | `python3 scripts/query_overdue_work_item.py '<json>'` | `python3 scripts/atomic/query_overdue_work_item.py --work-item-type 任务` |
| 参数格式 | JSON 字符串 | `--` 风格命令行参数 |
| 认证 | 同 | 同（共用 `common/auth_helper`） |
| 配置 | 独立 `config.json` | 共用 `studio-rdcloud-workitem/assets/config.json` |
| 日志 | 独立日志目录 | 共用 `studio-rdcloud-workitem/logs/` |
