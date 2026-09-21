# 工作项统计与分组聚合 · 脚本入参说明


> **工具约束（必遵）**：须通过本技能已有 `scripts/` 脚本执行研发云工作项能力；**禁止**使用 write、edit、web-search、web_fetch 等处理本业务或替代脚本；**禁止**新建 Python 脚本。

对应**同一脚本**：`scripts/atomic/stats_work_item.py`，通过**是否传入首参 `aggregate`** 区分两种能力：

| 模式 | 命令行形式 | 能力 |
|------|------------|------|
| **整单统计** | **无**子命令，直接跟 `--select-items` / `--conditions` 等 | 按条件分页拉取后在内存中做 COUNT / SUM / AVG / MIN / MAX |
| **分组聚合** | **第一个参数**为 **`aggregate`**，再跟 `--group-by` 等 | 与列表查询同源拼 `query_data`，写 `groupingKeys`，由服务端分桶返回各组条数并格式化为 Markdown |

执行规则：不做执行前确认；脚本执行完成后，先展示「本次语义解析结果」，再展示统计/分组结果。
结果展示约束：默认不得总结、省略或改写脚本返回结果；仅当用户语义明确要求“总结/汇总/精简”时，才可在完整结果后追加总结。

---

## 一、整单统计（默认，无子命令）

按查询列与条件查询后对结果做 COUNT / SUM / AVG / MIN / MAX 统计。须使用**具体业务工作区**（配置 `api.workspace`），不可用 `SYSMySpace` 作为统计目标工作区。

### 命令行调用

```bash
python scripts/atomic/stats_work_item.py --select-items "标识" --conditions "状态=进行中"
python scripts/atomic/stats_work_item.py --select-items "标识" --conditions "工作项类型=用户故事 AND 工作区=业务运营" --stats count
python scripts/atomic/stats_work_item.py --select-items "已完成工作" --conditions "(状态=进行中 OR 状态=待处理) AND 优先级=P0" --order-by "更新时间 desc" --limit 50 --stats sum --field 已完成工作
```

### 参数说明（仅字面值）

| 参数 | 必填 | 说明 |
|------|------|------|
| **--select-items, --selectItems** | 否 | 查询列，英文逗号分隔的字段名称；不传则默认列 |
| **--conditions, -c** | 否 | **条件组合**：解析出的工作区、工作项类型与状态/标题等均写入此处，支持 AND/OR、括号 `()` 组合与嵌套；不传则无过滤。详见 [query_work_item.md · 条件组合与嵌套](query_work_item.md#conditions-nested)。 |
| **--order-by, --orderBy** | 否 | 排序，如 更新时间 desc；不传则默认按更新时间倒序 |
| **--limit, -n** | 否 | 参与统计的条数上限，正整数，最大 10000（超过按 10000）；不传则脚本分页最多约 3000 条参与统计 |
| **--stats, -t** | 否 | 统计类型：count / sum / avg / min / max，默认 count |
| **--field, -F** | sum/avg/min/max 时必填 | 统计字段名称（如 已完成工作） |
| **--user-input, --userInput** | 否 | **用户的原始输入**，必须是用户真实、完整的一句话，用于工作区/类型/字段解析的上下文与日志 |

工作区由脚本从配置或最近访问解析以确定查询目标；用户表述中的工作区、工作项类型须并入 --conditions。

### 执行流程（总分总）

参数解析 → 参数调整（解析工作区、校验 stats+field）→ 调用查询 API → 在内存中聚合法（count/sum/avg/min/max）→ 结果格式化 → 追加心智提示。

### 心智提示（整单统计）

统计结果为空或异常时，会追加：放宽条件、确认工作区与类型、确认统计字段存在且为数值等建议。该部分须**完整保留并展示**给用户，禁止在总结或概括时删除；详见 [SKILL.md](../SKILL.md)「心智模型必须完整保留」。

---

## 二、分组聚合（`aggregate` 子命令）

**第一个参数**须为 **`aggregate`**。在下列与**列表查询同源**的筛选条件下，按 `--group-by` 指定字段由服务端分组，返回各分组条数并格式化为 Markdown。

| 场景 | 聚合侧如何构造 query_data |
|------|--------------------------------------|
| 通用条件查询 | `build_query_data_from_select_and_conditions` + 工作区条件 |
| 与我相关 | `stats_aggregate_scenario_queries.py`（与 `query_my_work_item` 对齐） |
| 超期 | `stats_aggregate_scenario_queries.py`（与 `query_overdue_work_item` 对齐） |
| 关联 / 树 | `stats_aggregate_scenario_queries.py`（与 `query_work_item` 关联/树分支对齐） |
| 视图 URL / 视图 ID | `stats_aggregate_scenario_queries.py`（`get_view_query_config`，去掉 `allWorkItemTypeKey`） |

### 场景互斥（必遵）

- 传 **`--query-type`**（与我相关）时，**不得**同时使用 `--overdue`、关联/树参数（`--relate-type` / `--tree-type` 等）、视图参数（`--query-url` / `--view-id`）。
- **`--overdue`**、**关联/树**、**视图** 三者**互斥**，每次只选一种。

<a id="aggregate-scenarios"></a>

### 各场景 · 调用示例（Skill / AI 按表选用）

**前提**：均须 **`aggregate` 子命令**与 **`--group-by`**；工作目录为技能根目录时路径为 `python scripts/atomic/stats_work_item.py aggregate ...`。**`--user-input`** 建议传用户原话。

| 场景 | 何时用 | 关键参数（除 `--group-by` 外） | 示例命令 |
|------|--------|-------------------------------|----------|
| **① 通用** | 按配置/语义工作区做条件查询再分组 | `--conditions`、可选 `--select-items` | `python scripts/atomic/stats_work_item.py aggregate --group-by "状态" --conditions "工作项类型=缺陷" --user-input "按状态统计缺陷数量"` |
| **② 与我相关** | 指派给我/待办/我创建等 | **`--query-type`**（与 `query_my_work_item` 相同） | `python scripts/atomic/stats_work_item.py aggregate --query-type assigned_to_me --group-by "工作区" --conditions "状态=进行中" --user-input "指派给我且进行中的按工作区分组"` |
| **③ 超期** | 我超期的单据按维度分组 | **`--overdue`**，可选 `--work-item-type`、`--order-by` | `python scripts/atomic/stats_work_item.py aggregate --overdue --group-by "状态" --work-item-type "任务" --user-input "我超期任务按状态分组"` |
| **④ 关联** | 主对象 + 关联侧条件 | `--relate-type`，可选 `--relate-conditions`、`--relatedOptions` | `python scripts/atomic/stats_work_item.py aggregate --group-by "状态" --conditions "标识=PLM-123" --relate-type 子级 --relate-conditions "工作项类型=任务" --user-input "子任务按状态分组"` |
| **⑤ 树** | 树形层级查询再分组 | **`--tree-type`**，可选 `--relate-conditions`、`--treeOptions` | `python scripts/atomic/stats_work_item.py aggregate --group-by "状态" --conditions "工作项类型=市场需求" --relate-conditions "工作项类型=用户故事" --tree-type 子级 --treeOptions 显示所有 --user-input "市场需求下用户故事按状态分组"` |
| **⑥ 视图** | 某视图（24 位 viewId）下分组 | **`--query-url`** 或 **`--view-id` + `--view-workspace`** | `python scripts/atomic/stats_work_item.py aggregate --group-by "状态" --query-url "https://rdcloud.zte.com.cn/uportal/rd-uportal/workspaces/PLM/apps/wim/queries?wicViewId=xxxxxxxxxxxxxxxxxxxxxxxx" --user-input "该视图按状态分组"` |

**`--query-type` 取值**：`assigned_to_me` | `my_todo` | `created_by_me` | `edited_by_me` | `followed_by_me`（与 [query_my_work_item.md](query_my_work_item.md) 一致）。

### 命令行调用（速查）

```bash
python scripts/atomic/stats_work_item.py aggregate --group-by "状态" --conditions "工作项类型=缺陷" --user-input "按状态统计缺陷数量"
python scripts/atomic/stats_work_item.py aggregate --group-by "状态,指派给" --select-items "标识,标题,状态,指派给" -c "状态!=已取消" --user-input "按状态和指派给分组统计"
python scripts/atomic/stats_work_item.py aggregate --query-type assigned_to_me --group-by "工作区" --user-input "指派给我的单据按工作区分组"
python scripts/atomic/stats_work_item.py aggregate --overdue --group-by "状态" --work-item-type "任务" --user-input "我超期的任务按状态分组"
python scripts/atomic/stats_work_item.py aggregate --group-by "状态" --conditions "标识=PLM-123" --relate-type 子级 --relate-conditions "工作项类型=任务" --user-input "子任务按状态分组"
python scripts/atomic/stats_work_item.py aggregate --group-by "状态" --query-url "https://rdcloud.zte.com.cn/uportal/rd-uportal/workspaces/PLM/apps/wim/queries?wicViewId=xxxxxxxxxxxxxxxxxxxxxxxx" --user-input "该视图下按状态分组"
```

### 参数说明 · 分组聚合（仅字面值）

| 参数 | 必填 | 说明 |
|------|------|------|
| **--group-by, --groupBy** | **是** | 分组维度，英文逗号分隔的**字段名称**（与 `--select-items` 同源解析），如：`状态`、`工作区,指派给` |
| **--select-items, --selectItems** | 否 | 查询列；不传则默认列，脚本会保证返回列中含分组所需字段 |
| **--conditions, -c** | 否 | **条件组合**：与 [query_work_item.md](query_work_item.md) 相同（通用 / 关联 / 树的主对象侧）；工作区、工作项类型等并入此处 |
| **--order-by, --orderBy** | 否 | 排序；不传则默认按更新时间倒序（超期场景下与 `query_overdue_work_item` 默认排序规则一致） |
| **--limit, -n** | 否 | 写入请求体 `pageSize`（上限 `QUERY_LIMIT_MAX`）。**聚合未传时默认 200**，过小会导致分桶被截断并出现「其余分桶合并」；一般无需刻意调大 |
| **--user-input, --userInput** | 否 | **用户的原始输入**，用于字段名前后匹配与日志；**调用时建议传入** |
| **--query-type, --queryType, -q** | 否 | **与我相关**场景传入，取值与 `query_my_work_item` 相同。传入后接口 **URL 工作区为 SYSMySpace**；与其它场景参数互斥 |
| **--overdue** | 否 | **超期**场景；与 `query_overdue_work_item` 同源；**URL 工作区为 SYSMySpace**；可配合 **--work-item-type**、**--order-by** |
| **--work-item-type, --workItemType** | 否 | **仅** `--overdue` 时有效：工作项类型，英文逗号分隔；默认：任务 |
| **--relate-type, --relateType** | 否 | **关联查询**；与 `query_work_item` 相同；与 **--tree-type** 互斥 |
| **--tree-type, --treeType** | 否 | **树查询**；与 `query_work_item` 相同；与 **--relate-type** 互斥 |
| **--relate-conditions, --relateConditions** | 否 | 关联侧条件（targetClauses），与 `query_work_item` 相同 |
| **--relatedOptions** | 否 | 关联选项，与 `query_work_item` 相同（需配合 `--relate-type`） |
| **--treeOptions** | 否 | 树选项，与 `query_work_item` 相同（需配合 `--tree-type`） |
| **--query-url, --queryUrl** | 否 | 研发云视图链接，须能解析出 **24 位十六进制视图 ID**（与 `parse_query_url` 一致）；与 **--view-id** 二选一 |
| **--view-id, --viewId** | 否 | 24 位十六进制视图 ID；需 **--view-workspace** 或配置 `api.workspace` |
| **--view-workspace, --viewWorkspace** | 否 | 视图所在工作区（`--view-id` 或部分 URL 无工作区时使用） |

工作区由配置解析；用户表述中的工作区、工作项类型须并入 --conditions（或与「与我相关」语义一并解析）。

### 执行流程（分组聚合）

参数解析 → 参数调整（按场景选择 builder）→ 写入 `groupingKeys` 并补全 `selectItems` → **一次** `query_work_items` → 解析分组统计结果 → Markdown → 心智提示。

### 与列表脚本对齐（实现要点）

各场景 **query_data 构造入口**（与列表脚本对齐关系）见 **[stats_aggregate_query_body.md](stats_aggregate_query_body.md)**。详细设计见 **[附录 B](../docs/多维度统计与分组聚合设计方案.md#appendix-b-stats-aggregate)**，摘要如下：

| 项 | 说明 |
|----|------|
| **通用查询** | 拼体与 `query_work_item` 同源；发请求前 **`crossWorkspace: true`**（与列表一致）。 |
| **其它场景** | 与我相关 / 超期 / 关联树在 `stats_aggregate_scenario_queries.py` 中已与对应列表脚本对齐 `crossWorkspace` 等。 |
| **分组列** | `merge_select_str_with_group_by` 将 `--group-by` 并进 select 串 → `resolve_group_by_to_keys` → `merge_aggregate_query_body` 写 `groupingKeys` 并补 `selectItems`。 |
| **工作区 key** | 聚合路径内将 `System_Workspace` 规范为 `System_WorkspaceKey`；**不**改公共 `field_resolver`。 |
| **编排（大模型）** | 用户说「只按工作区分组」时 **`--group-by` 勿含工作项类型**；类型只进 `--conditions`；复杂类型条件合并为清晰 `in` / OR；**工具输出的 Markdown 表格宜原样展示**。 |

### 其它说明（分组聚合）

- **视图场景**：仅支持能走 `get_view_query_config` 的 **24 位视图 ID**；纯数字 queryId 的链接请先使用列表查询或换用含 `wicViewId` 的链接。
- 新增与列表不同构的场景时：**先对齐对应列表脚本的 query_data**，再在 `stats_work_item.py` 的 aggregate 分支或扩展 `stats_aggregate_scenario_queries.py` 即可。

### 心智提示（分组聚合）

聚合失败或空结果时，可能追加放宽条件、确认工作区等建议。该部分须**完整保留并展示**给用户；详见 [SKILL.md](../SKILL.md)「心智模型必须完整保留」。
