# 查询工作项（通用）· 脚本入参说明


> **工具约束（必遵）**：须通过本技能已有 `scripts/` 脚本执行研发云工作项能力；**禁止**使用 write、edit、web-search、web_fetch 等处理本业务或替代脚本；**禁止**新建 Python 脚本。

对应脚本：`scripts/atomic/query_work_item.py`。**与我相关**场景必须使用 [query_my_work_item.md](query_my_work_item.md) 的脚本，禁止使用本脚本。

执行规则：不做执行前确认；脚本执行完成后，先展示「本次语义解析结果」（查询列、查询条件、关联参数等），再展示查询结果。
结果展示约束：默认不得总结、省略或改写查询结果；仅当用户语义明确要求"总结/汇总/精简"时，才可在完整结果后追加总结。
结果展示约束：默认不得总结、省略或改写脚本返回结果；仅当用户语义明确要求"总结/汇总/精简"时，才可在完整结果后追加总结。

## 命令行调用

- **命令形式**：`python scripts/atomic/query_work_item.py [--select-items ITEMS] [--conditions CONDITIONS] [其他可选参数]`
- **查询列**：`--select-items`（或 `--selectItems`），英文逗号分隔的字段名称；不传则使用默认列。
- **条件组合**：`--conditions`（或 `-c`）。**解析出的工作区、工作项类型均作为 conditions 的一部分**，与其它筛选条件（如状态、标题）用 AND 等组合；**支持 AND/OR、括号 `()` 组合与嵌套**；不传则无过滤。详见下文「条件组合与嵌套」。
- **关联条件参数**：`--relate-conditions`（或 `--relateConditions`），格式与 `--conditions` 完全一致；用于关联查询与树查询的 `targetClauses`。
- **关联查询参数**：`--relate-type`（中文关联类型名，脚本内映射到 `LINK_RELATION_TYPE_MAP` 对应 id）、`--relatedOptions`（`linksOneHopMustContain` / `linksOneHopDoesNotContain` / `linksOneHopMayContain`）。
- **树查询参数**：`--tree-type`（中文关联类型名，脚本内映射到 `LINK_RELATION_TYPE_MAP` 对应 id）、`--treeOptions`（`top` / `related` / `all`）。
- **模式互斥约束**：普通查询、关联查询、树查询三种模式互斥，不能混用。
- **查看完整参数**：`python scripts/atomic/query_work_item.py --help`

## 参数说明（与 --help 一致）

| 参数（命令行写法） | 必填 | 说明 |
|--------------------|------|------|
| **--select-items, --selectItems** | 否 | 查询列，英文逗号分隔的字段名称；不传则默认列 |
| **--conditions, -c** | 否 | **条件组合**：工作区、工作项类型与状态/标题等均写入此处，支持 AND/OR、`()` 组合与嵌套；不传则无过滤 |
| **--relate-conditions, --relateConditions** | 否 | 关联条件，格式与 `--conditions` 完全一致；关联查询/树查询时解析为 `targetClauses` |
| **--relate-type, --relateType** | 否 | 关联查询类型中文名，如 子级、父级、相关、前置、后续；脚本会从 `LINK_RELATION_TYPE_MAP` 映射到关联 id |
| **--relatedOptions, --related-options** | 否 | 关联选项，建议使用中文：`有匹配的关联工作项`、`无匹配的关联工作项`、`所有工作项`；脚本会分别映射为 `linksOneHopMustContain`、`linksOneHopDoesNotContain`、`linksOneHopMayContain` 后调用接口；仅关联查询可用 |
| **--tree-type, --treeType** | 否 | 树查询类型中文名，如 子级、父级、相关、前置、后续；脚本会从 `LINK_RELATION_TYPE_MAP` 映射到关联 id |
| **--treeOptions, --tree-options** | 否 | 树选项，建议使用中文：`基于顶级显示`、`基于关联显示`、`显示所有`；脚本会分别映射为 `top`、`related`、`all` 后调用接口；仅树查询可用 |
| **--order-by, --orderBy** | 否 | 排序，如 更新时间 desc、标题 asc, 状态 desc；不传则默认按更新时间倒序 |
| **--limit, -n** | 否 | 返回条数上限，正整数，最大 10000（超过按 10000）；不传则默认 20 |
| **--is-send-email, --is_send_email** | 否 | 是否发邮件：true/false，默认 false |
| **--is-send-icenter, --is_send_icenter** | 否 | 是否发 iCenter 消息：true/false，默认 false |
| **--mail-to, --mailTo** | 否 | 邮件接收人，多值英文逗号分隔 |
| **--icenter-to, --icenter_to** | 否 | iCenter 消息接收人，多值英文逗号分隔 |
| **--user-input, --userInput** | 否 | **用户的原始输入**，必须是用户真实、完整的一句话，用于工作区/类型/字段解析的上下文与日志 |

### 查询模式说明（互斥）

- **普通查询**：仅使用 `--conditions` 等通用参数，不传 `--relate-type`、`--tree-type`。
- **关联查询**：传 `--relate-type`（具体类型或`全部`表示所有类型），可选传 `--relatedOptions`（未传时脚本默认 `linksOneHopMustContain`）。
- **树查询**：必须传 `--tree-type`，可选传 `--treeOptions`（未传时脚本默认 `top`）。
- **语义约束**：用户有关联查询意图时，若明确关系类型（如子级/父级/相关/前置/后续），传对应值；若只说"关联工作项""有关联的"等未指定类型，传 `--relate-type 全部`（查询所有类型）。
- **禁止臆测"相关"**：用户说"关联工作项"不等于关系类型"相关"；仅当明确表达"相关"时，才传 `--relate-type 相关`。
- **“关联关系是 X”**：X 为关联类型（如计划、被计划），必须传入 `--relate-type`；**禁止**将 X 解析为 `状态=X` 等字段条件。

### 关联类型解析（与 SKILL「§0 / §0.1 / §0.2」一致，AI 必遵）

**原则**：仅当用户原话出现可映射词时才写入具体 `--relate-type` / `--tree-type`；否则传 **`全部`**。**禁止**根据「主对象类型 + 关联对象类型」猜测关系（例如「需求 + 任务」≠ 自动子级）。

| 用户表述中出现 | `--relate-type` 或 `--tree-type` |
|----------------|----------------------------------|
| 下面、下级、子任务、挂在…下面、子项 | 子级 |
| 上面、上级、父、父级 | 父级 |
| 相关、互为相关、相关关系 | 相关 |
| 前置、后续、源、副本、测试/被测试、计划/被计划、依赖/被依赖、影响/影响者、上游/下游 | 与口语一致的脚本支持中文名 |
| 仅有「关联」「连到」「挂到」而无上表及「相关」等 | **全部**（禁止写成 相关） |

完整规则与易混边界见 [SKILL.md](../SKILL.md) 中 **「0. 关联类型传值前置约束」** 及 **「0.1」「0.2」**。  
**说明**：**建立关联**（`create_work_item` / `update_work_item` / `relate_work_item`）时口语「作为…的子级」对应 `--relate-type` 须取反（传 **`父级`**），见 **§0.3**；**本查询脚本**的关联查询/树查询仍按上表映射（如「下面/下级」→ **子级**），**不适用** §0.3 取反。

- 关联查询/树查询中，`--relate-conditions` 作为关联侧过滤条件，解析后进入请求体 `targetClauses`，解析逻辑与 `--conditions` 完全一致。
- `--relate-type` 与 `--tree-type` 不能同时传；`--relatedOptions`/`--treeOptions` 不能脱离各自模式单独传；`--relate-conditions` 不能脱离关联/树模式单独传。
- **主从条件约束**：主对象条件必须放 `--conditions`（sourceClauses），关联侧条件才放 `--relate-conditions`（targetClauses）。  
  示例：`查询PLM-3298581有哪些关联工作项`  
  - 正确：`--conditions "标识=PLM-3298581"`，`--relate-conditions ""`  
  - 错误：`--conditions ""`，`--relate-conditions "标识=PLM-3298581"`

### 关联场景语义识别（主对象 / 关联对象 / 关联条件）

- **主对象、关联对象均可带条件**：主对象条件放入 `--conditions`（sourceClauses），关联对象/关联侧条件放入 `--relate-conditions`（targetClauses）。
- **主对象**：语义中的"从谁出发查关联"，放到 `--conditions`。可带条件（如「状态为进行中的任务下面的用户故事」→ `工作项类型=任务 AND 状态=进行中`）。
- **关联对象**：语义中的"关联侧是什么类型/对象"，放到 `--relate-conditions`。可带条件（如「市场需求下面的优先级为P0的任务」→ `工作项类型=任务 AND 优先级=P0`）。
- **关联条件**：修饰关联对象的限定语（如版本、状态、优先级），也放到 `--relate-conditions` 并与关联对象条件 AND 组合。
- **默认语序判定**：若用户语义无明确反向词，按"前主后从"识别：前面的对象进 `--conditions`，后面的对象进 `--relate-conditions`。
- **反向词优先**：出现「Y 对应的 X」「关联到 X 的 Y」等反向表达时，按语义词方向识别主从关系。

| 用户表达 | --conditions（主对象） | --relate-conditions（关联对象/关联条件） |
|------|--------------------|--------------------|
| 查询市场需求下面的用户故事 | `工作项类型=市场需求` | `工作项类型=用户故事` |
| 查询变更请求下的变更活动 | `工作项类型=变更请求` | `工作项类型=变更活动` |
| 查合入版本是xx版本的变更活动对应的变更请求 | `工作项类型=变更请求` | `工作项类型=变更活动 AND 合入版本=xx版本` |
| 查询市场需求关联的任务 | `工作项类型=市场需求` | `工作项类型=任务` |
| 查询PLM-3298581有哪些关联工作项 | `标识=PLM-3298581` | 空 |
| 查询ZXDI-109774工作项下关联关系是计划的市场需求 | `标识=ZXDI-109774` | `工作项类型=市场需求`；关联类型=计划（「关联关系是计划」中的计划为类型，禁止解析为 状态=计划） |

<a id="conditions-nested"></a>

## 条件组合与嵌套（--conditions）

**解析出的工作区、工作项类型均作为 --conditions 的一部分**，与其它条件一起组成条件组合。**--conditions** 支持：

- **工作区、工作项类型**：从用户表述解析出的「在 XX 工作区」「查 XX 类型」须写入条件（与其它条件用 AND 等组合）。
- **AND / OR**：多条件用 `AND`、`OR` 连接。
- **括号 `()`**：分组与嵌套，改变优先级。

| 场景 | --conditions 示例 |
|------|--------------------|
| 工作项类型 + 工作区 | `工作项类型=用户故事 AND 工作区=业务运营` |
| 工作项类型 + 条件 | `工作项类型=任务 AND 状态=进行中` |
| 单条件 | `状态=进行中` |
| 多条件 AND | `状态=进行中 AND 标题 like 测试` |
| 多条件 OR | `状态=进行中 OR 状态=待处理` |
| 括号分组 (OR 再 AND) | `(状态=进行中 OR 状态=待处理) AND 优先级=P0` |
| 括号分组 (AND 再 OR) | `(状态=进行中 AND 指派给=@me) OR (状态=待处理 AND 优先级=P0)` |
| 多层嵌套 | `(状态=进行中 OR (状态=待处理 AND 优先级=P0)) AND 标题 like 需求` |
| in / like | `状态 in 进行中,待处理`、`标题 like 紧急` |

## 示例

```bash
# 单条件
python scripts/atomic/query_work_item.py --select-items "标题,状态" --conditions "标题 like 测试"

# 工作项类型 + 工作区
python scripts/atomic/query_work_item.py --select-items "标题,状态" --conditions "工作项类型=用户故事 AND 工作区=业务运营"

# 多条件 AND
python scripts/atomic/query_work_item.py --select-items "标识,标题,状态" --conditions "状态=进行中 AND 标题 like 测试"

# 条件组合：(A OR B) AND C
python scripts/atomic/query_work_item.py --select-items "标题,状态,优先级" --conditions "(状态=进行中 OR 状态=待处理) AND 优先级=P0"

# 嵌套：(A AND B) OR (C AND D)
python scripts/atomic/query_work_item.py --select-items "标题,状态" --conditions "(状态=进行中 AND 指派给=@me) OR (状态=待处理 AND 优先级=P0)" --is-send-icenter true --icenter-to @me

# 排序与条数上限
python scripts/atomic/query_work_item.py --select-items "标题,状态,更新时间" --conditions "状态=进行中" --order-by "更新时间 desc" --limit 10

# 关联查询（one-hop）
python scripts/atomic/query_work_item.py --select-items "标识,标题,状态" --conditions "状态=进行中" --relate-conditions "状态!=已关闭 AND 工作项类型=任务" --relate-type 子级 --relatedOptions 有匹配的关联工作项

# 树查询
python scripts/atomic/query_work_item.py --select-items "标识,标题,状态" --conditions "状态!=已关闭" --relate-conditions "优先级=P0 OR 标题 like 紧急" --tree-type 子级 --treeOptions 显示所有
```

## 通知与结果后逻辑

- 是否发邮件/发消息、收件人/接收人解析：见 SKILL「发送邮件/消息：接收人解析」与「查询结果后的通知逻辑」。
- **结果展示与心智模型**：查询结果必须完整展示给用户；脚本输出后可能追加心智模型（查询示例、提醒等），须**完整保留并展示**，禁止在总结或概括时删除、合并或省略该部分。详见 SKILL「查询结果必须展示」与「心智模型必须完整保留」。

## 常用字段名称（示例）

--select-items / --conditions 使用**字段名称**（如 标题、状态、标识）。脚本内部通过 API 按名称解析。常见名称：标识、标题、状态、工作项类型、更新时间、创建时间、指派给、处理人、优先级等。
