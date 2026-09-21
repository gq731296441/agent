---
name: studio-rdcloud-workitem
description: 研发云工作项技能（用户常称 rdc、研发云、RDCloud）。支持创建工作项、更新工作项、查询工作项、关联工作项、统计工作项、**分组聚合**（按字段分类汇总）、导出工作项；支持查询与添加工作项评论；查询我超期的工作项。支持创建任务、用户故事、缺陷、变更请求、问题单、待办事项等类型的工作项；支持更新工作项字段和状态；支持将两个或以上工作项建立关联；支持按条件查询工作项，含普通查询、关联查询（直接关联）与树查询（层级/树形）；支持查询与我相关的工作项（指派给我、我创建的、我编辑的、我关注的、我的待办等）。不支持创建：原始需求、外场故障、测试用例、测试计划、测试记录。工作项、单据、实例语义等同。
version: 1.3.0
generator: co-skill-creator
tags: ["研发提效", "数据智能"]
---
# Studio 研发云工作项技能
RDC 工作项技能提供工作项的创建、更新、查询、详情、导出、统计，以及**工作项评论**（查询评论树、添加评论、回复评论）；**邮件与 iCenter 消息仅在与上述业务联动时触发**，禁止在无创建/更新/查询/导出/统计/查看详情意图时直接调用发送脚本。
## 工具与实现约束（必遵）
- **必须**使用本技能仓库内**已有**的 `scripts/atomic/`（及若有）`scripts/composite/` 脚本完成创建、查询、更新、导出、统计、**分组聚合**、评论等能力，通过命令行参数与 `--user-input` 调用，见下文「功能与脚本映射」。
- **禁止**使用 write、search_replace、edit 等工具**新建或改写**代码/脚本以替代上述能力，或现场拼业务逻辑处理工作项数据。
- **禁止**使用 web-search、web_fetch、web_search_coclaw、web_fetch_coclaw 等外部工具抓取或查询研发云 / RDC 工作项数据以**替代**本技能脚本。
- **禁止**新建 Python（或其它可执行）脚本；缺能力时只能调整现有脚本的参数与组合，或向用户说明边界，**不得**临时编写脚本执行。
## 脚本选择规则（必遵）
| 用户意图 | 使用脚本 | 禁止 |
|----------|----------|------|
| **超期查询**：我超期的任务/缺陷/用户故事等（明确含"超期"/"过期"/"到期未完成"等语义，且无其他与我相关过滤条件） | `scripts/atomic/query_overdue_work_item.py` | **禁止**使用 `query_my_work_item.py` 或 `query_work_item.py` |
| **与我相关**：我的任务、我的缺陷、我的变更请求、指派给我、我创建的、我编辑的、我关注的、我的待办等（不含超期语义） | `scripts/atomic/query_my_work_item.py` | **禁止**使用 `query_work_item.py` |
| **导出**：导出工作项、导出为 Excel、导出查询结果、导出为文件、把结果导出等；**导出不必在句首**，如「查询XXX，导出」「查XXX，然后导出」等先查询再导出的表述，须完整理解整句语义，从查询部分提取条件并调用导出 | `scripts/atomic/export_work_item.py` | **禁止**使用 `query_work_item.py` 或 `query_my_work_item.py` |
| **根据查询链接查询** / **按查询名/视图名查询**：用户意图为查询且**明确提供了**研发云工作项查询链接，或**明确指定**按「查询名/视图名」查询（如：根据查询名 WODE、根据视图名 我的查询）；二者**均按「根据查询链接」方式处理**，使用同一脚本 | `scripts/atomic/query_work_item_by_url.py` | **禁止**为「查询链接/查询名」场景使用 `query_work_item.py` 或 `query_my_work_item.py` |
| **分组聚合 / 分类汇总**：按某（些）字段维度看「每组各多少条」、分布、分桶（须能解析出分组维度，传 **--group-by**） | `scripts/atomic/stats_work_item.py aggregate`（首参 **`aggregate`**） | **禁止**用不带 `aggregate` 的 `stats_work_item.py`（整单 count/sum 等）或 `query_work_item.py`（除非用户要的是**明细列表**而非各组条数）替代 |
| **复制工作项**：复制/拷贝/克隆某张单据、基于某单据复制一份、把某单据复制出来等（语义含「复制」「拷贝」「克隆」等，且能识别源单据标识，如 `PLM-xxxx`、`RAN-xxxx`） | `scripts/atomic/copy_work_item.py` | **禁止**使用 `create_work_item.py`：`create_work_item.py` 仅按 `--fields` 写入字段，**不会**从源单据全量带字段 |
**超期查询**一经识别（语义中含「超期」「过期」「到期未完成」等），**必须**且**仅能**调用 `query_overdue_work_item.py`；**禁止**为「超期」场景调用 `query_my_work_item.py` 或 `query_work_item.py`。
**与我相关**一经识别（无超期语义），**必须**且**仅能**调用 `query_my_work_item.py`；**禁止**为「与我相关」场景调用 `query_work_item.py`。
**导出**一经识别（用户说「导出」「导出为 Excel」「把结果导出」等，或**先查询再导出**如「查询XXX，导出」「查XXX，然后导出」），**必须**且**仅能**调用 `export_work_item.py`；**禁止**为「导出」场景调用 `query_work_item.py` 或 `query_my_work_item.py`。须**完整理解用户整句语义**，从「查询/查/找」等前置部分提取工作区、类型、条件、排序、条数等，一并传入导出脚本。
**分组聚合**一经识别（用户明确表达按字段**分组/聚合/分类汇总**、**各…分别多少条**、**…的分布**等，且能解析 **--group-by**），**必须**且**仅能**调用 `stats_work_item.py aggregate`（命令行**第一个参数**为 `aggregate`）；**禁止**用不带 `aggregate` 的 `stats_work_item.py` 或 `query_work_item.py` 替代（除非用户改口要明细列表或只要一条汇总值）。若句中同时出现「统计」与「按某字段/各组」等分组语义，**优先**走分组聚合。
**复制工作项**一经识别（用户表达「复制」「拷贝」「克隆」等，且句中出现或可解析出源单据标识），**必须**且**仅能**调用 `copy_work_item.py`；详见 [copy_work_item.md](references/copy_work_item.md)。

### 根据查询链接查询 / 按查询名/视图名查询：意图识别与脚本（必遵）
当用户**意图为查询**且满足以下任一情况时，**均按「根据查询链接」方式处理**，**必须**且**仅能**调用 `query_work_item_by_url.py`，**禁止**走通用查询或与我相关查询：
1. **明确提供了查询链接**：用户输入中包含本技能支持的研发云工作项查询 URL（支持多种模式，不限定为某两类，见 [query_work_item_by_url.md](references/query_work_item_by_url.md)），须先判断该链接是否匹配支持的 URL 模式。
2. **明确指定按查询名/视图名查询**：用户表述为「根据查询名 XXX」「根据视图名 XXX」「根据查询名称/视图名称/查询视图 XXX」等，即按**查询名或视图名**执行查询。此类场景与「根据查询链接」**使用同一脚本、同一处理流程**（脚本内部会先按名称解析出对应视图/查询，再按链接方式拉取结果）。
若用户输入中**匹配到上述任一模式**，则识别为「根据 URL / 查询名/视图名 查询」场景，调用 `query_work_item_by_url.py`：有链接时传 --query-url（链接）；按名称时传 --query-url（名称）或 --user-input（含「根据查询名/视图名 XXX」），详见 [query_work_item_by_url.md](references/query_work_item_by_url.md)。意图识别顺序建议：先判断是否带支持的查询 URL 或是否明确按查询名/视图名查询 → 是则走本脚本；否则再区分「与我相关」与「查询通用」。

### 与我相关：queryType 解析（必做）
调用 `query_my_work_item.py` 时，使用**命令行参数**入参；**必须**从用户表述中解析出查询类型，传入 **--query-type**（或对应 queryType）。未明确表述时使用默认值 `assigned_to_me`。
| 用户表述 | queryType（--query-type 取值） | 说明 |
|----------|------------------------|------|
| 指派给我、指派给我的、我的任务 | `assigned_to_me` | 默认 |
| 我的待办 | `my_todo` | |
| 我创建的 | `created_by_me` | |
| 我编辑的 | `edited_by_me` | |
| 我关注的 | `followed_by_me` | |
解析结果展示给用户确认时，须包含**查询类型**及其中文说明（如：指派给我、我创建的等）；**禁止在展示中出现 queryType、脚本名或参数名**。
## 功能与脚本映射
| 功能 | 脚本 | 入参要点 | 参考 |
|------|------|----------|------|
| 创建工作项 | `scripts/atomic/create_work_item.py` | 工作项类型名称、字段（名称+值）；可选通知与接收人、创建后关联（--relate-workitem-ids、--relate-type） | [create_work_item.md](references/create_work_item.md) |
| **复制工作项** | `scripts/atomic/copy_work_item.py` | **--source-work-item-id**（源单据标识；可与 user-input 中的单据号一致）；可选 **--target-work-item-type-name** / **--target-work-item-type-key**、**--override-fields**、**--copy-all**、**--smart-complete**、**--child-of**、通知参数；须 **--user-input** | [copy_work_item.md](references/copy_work_item.md) |
| 更新工作项 | `scripts/atomic/update_work_item.py` | 工作项标识（支持多个，英文逗号分隔）、字段（名称+值）；可选通知与接收人、更新后关联（--relate-workitem-ids、--relate-type） | [update_work_item.md](references/update_work_item.md) |
| 关联工作项 | `scripts/atomic/relate_work_item.py` | 主工作项标识、被关联工作项标识（可多个）、关联类型（子级/父级/相关等） | [relate_work_item.md](references/relate_work_item.md) |
| 查询（通用/按条件/关联/树） | `scripts/atomic/query_work_item.py` | --select-items、--conditions；关联/树查询共用 --relate-conditions（解析为 targetClauses）；关联查询用 --relate-type/--relatedOptions（有匹配的关联工作项/无匹配的关联工作项/所有工作项）；树查询用 --tree-type/--treeOptions（基于顶级显示/基于关联显示/显示所有）（普通/关联/树互斥） | [query_work_item.md](references/query_work_item.md) |
| 查询与我相关 | `scripts/atomic/query_my_work_item.py` | --select-items、--conditions、--query-type 等 | [query_my_work_item.md](references/query_my_work_item.md) |
| 查询我超期的工作项 | `scripts/atomic/query_overdue_work_item.py` | --work-item-type（默认：任务）、--limit、--order-by、--raw-json | [query_overdue_work_item.md](references/query_overdue_work_item.md) |
| 工作项详情 | `scripts/atomic/get_work_item_detail.py` | 工作项标识（支持多个，英文逗号分隔）；可选工作区名称 | [get_work_item_detail.md](references/get_work_item_detail.md) |
| 工作项导出 | `scripts/atomic/export_work_item.py` | --select-items、--conditions、--flap | [export_work_item.md](references/export_work_item.md) |
| **工作项分组聚合** | `scripts/atomic/stats_work_item.py aggregate` | **首参 `aggregate`**，**--group-by**（必填）；通用查询与 `query_work_item` 相同；另支持 **--query-type**（与我相关）、**--overdue**（超期）、关联/树（**--relate-type** / **--tree-type** 等）、视图（**--query-url** / **--view-id**）；场景互斥见 [stats_work_item.md · 二、分组聚合](references/stats_work_item.md#aggregate-scenarios) | [stats_work_item.md](references/stats_work_item.md) |
| 根据查询链接查询 / 按查询名/视图名查询 | `scripts/atomic/query_work_item_by_url.py` | --query-url（查询链接或查询名/视图名）；--user-input（用户完整输入，按名称查询时用于解析「根据查询名/视图名 XXX」）；二者**均按同一方式处理** | [query_work_item_by_url.md](references/query_work_item_by_url.md) |
| 查询工作项评论 | `scripts/atomic/query_comment.py` | --work-item-id；可选 --page、--size、--sort、--raw-json；须 --user-input | [work_item_comment.md](references/work_item_comment.md) |
| 添加 / 回复工作项评论 | `scripts/atomic/add_comment.py` | --work-item-id、--comment-detail；回复时 --reply-to（序号，推荐）或 --parent-id；可选 --title、--email-disabled、--raw-json；须 --user-input | [work_item_comment.md](references/work_item_comment.md) |
| **规则智能诊断** | `scripts/atomic/rule_diagnose.py` | --work-item-id（单据ID，必填）；--triggers/--states/--actions/--fields（大模型解析好的参数，推荐）；--problem（回退描述） | [references/rule_diagnose.md](references/rule_diagnose.md) |

### 脚本调用参数速查
调用脚本时使用**命令行参数**，入参均为**字面值**（工作区名称、类型名称、字段名称、字段值）；完整说明见各 reference，`-h` / `--help` 可查看脚本帮助。
**必遵**：在调用**任何**本技能脚本时，**必须**将用户原始输入作为脚本参数传递，即传入 **--user-input**（或 **--userInput**），值为用户真实、完整的一句话，不得省略。
| 脚本 | 必填参数 | 主要可选参数 |
|------|----------|--------------|
| **create_work_item.py** | `--work-item-type-name`、`--fields`（字段名称+值） | `--user-input`、`--is-send-email`、`--is-send-icenter`、`--mail-to`、`--icenter-to`、`--relate-workitem-ids`、`--relate-type` |
| **copy_work_item.py** | `--source-work-item-id`（或 `--user-input` 中含可解析的单据号） | `--target-work-item-type-name` / `--target-work-item-type-key`、`--override-fields`、`--copy-all`、`--smart-complete`、`--child-of`、通知相关、`--user-input`（必传） |
| **update_work_item.py** | `--work-item-id`（可多个，英文逗号分隔）、`--fields`（字段名称+值；可带 `modifyType`） | `--user-input`、`--is-send-email`、`--is-send-icenter`、`--mail-to`、`--icenter-to`、`--relate-workitem-ids`、`--relate-type`（追加/多值细则见 `references/update_work_item.md`） |
| **relate_work_item.py** | `--work-item-ids`（可多个，英文逗号分隔）、`--relate-workitem-ids`、`--relate-type` | `--user-input` |
| **query_work_item.py** | 无必填（可选 --select-items、--conditions） | `--select-items`、`--conditions`、`--relate-conditions`、`--relate-type`、`--relatedOptions`、`--tree-type`、`--treeOptions`、`--order-by`、`--limit`、`--user-input`、通知与接收人（普通/关联/树互斥） |
| **query_my_work_item.py** | 无必填（可选 --select-items、--conditions） | `--query-type`（默认 assigned_to_me）、`--select-items`、`--conditions`、`--order-by`、`--limit`、`--user-input`、通知与接收人 |
| **query_overdue_work_item.py** | 无必填 | `--work-item-type`（默认 任务）、`--limit`、`--order-by`、`--raw-json`、`--user-input` |
| **get_work_item_detail.py** | `--work-item-id`（可多个，英文逗号分隔） | `--user-input` |
| **export_work_item.py** | 无必填（可选 --select-items、--conditions） | `--select-items`、`--conditions`、`--order-by`、`--limit`、`--flap`、`--user-input`；工作区由配置解析 |
| **stats_work_item.py**（无子命令，整单统计） | 无必填（可选 --select-items、--conditions） | `--select-items`、`--conditions`、`--order-by`、`--limit`、`--stats`、`--field`、`--user-input` |
| **stats_work_item.py aggregate**（分组聚合） | **`--group-by`**（分组维度，字段名逗号分隔） | 首参固定为 **`aggregate`**，其后：`--select-items`、`--conditions`、`--order-by`、`--limit`、`--user-input`；**与我相关** `--query-type`；**超期** `--overdue`、`--work-item-type`；**关联/树** `--relate-type`、`--tree-type`、`--relate-conditions`、`--relatedOptions`、`--treeOptions`；**视图** `--query-url`、`--view-id`、`--view-workspace`（场景互斥） |
| **query_work_item_by_url.py** | `--query-url`（查询链接，或直接传查询名/视图名如 WODE） | `--user-input`（用户完整输入；按「根据查询名/视图名」查询时必传以解析名称）；**按名称与按链接均用本脚本、同一处理方式** |
| **query_comment.py** | `--work-item-id` | `--business-type`、`--page`、`--size`、`--sort`、`--raw-json`、`--user-input` |
| **add_comment.py** | `--work-item-id`、`--comment-detail` | `--reply-to`（序号，推荐）或 `--parent-id`；`--title`、`--email-disabled`、`--raw-json`、`--user-input` |
| **rule_diagnose.py** | `--work-item-id`（单据ID，必填） | `--triggers`（JSON数组）、`--states`（JSON数组）、`--actions`（JSON数组）、`--fields`（JSON数组，如 `["标题"]`）、`--problem`（回退用，不推荐）、`--user-input`（用户原始输入，用于日志） |
**调用示例**（字段均使用**名称**，脚本内部解析；详见各 reference）：
```bash
python scripts/atomic/create_work_item.py --work-item-type-name 任务 --fields '[{"key":"标题","value":"示例标题"}]'
python scripts/atomic/create_work_item.py --work-item-type-name 任务 --fields '[{"key":"标题","value":"示例"}]' --relate-workitem-ids PLM-100 --relate-type 父级
# 复制工作项（全量按源单复制；勿用 create_work_item 充当复制）
python scripts/atomic/copy_work_item.py --source-work-item-id PLM-3328329 --user-input "复制 PLM-3328329" --formatAsMarkdown true
python scripts/atomic/copy_work_item.py --source-work-item-id PLM-3328329 --target-work-item-type-name 任务 --override-fields '[{"key":"标题","value":"新标题"}]' --user-input "复制为任务并改标题" --formatAsMarkdown true
python scripts/atomic/copy_work_item.py --source-work-item-id PLM-3328329 --child-of PLM-3328325 --override-fields '[{"key":"System_Title","value":"测试"}]' --user-input "复制单据 PLM-3328329，添加父级 PLM-3328325，然后更新标题为测试" --formatAsMarkdown true
python scripts/atomic/update_work_item.py --work-item-id PLM-12345 --fields '[{"key":"状态","value":"进行中"}]'
python scripts/atomic/relate_work_item.py --work-item-ids PLM-3292542 --relate-workitem-ids PLM-3292544 --relate-type 父级
python scripts/atomic/update_work_item.py --work-item-id PLM-12345,PLM-12346 --fields '[{"key":"状态","value":"进行中"}]'
python scripts/atomic/update_work_item.py --work-item-id PLM-12345 --fields '[{"key":"标签","modifyType":"appendAfter","value":"测试skill，skill更新"},{"key":"状态","value":"进行中"}]' --user-input "更新 PLM-12345：标签追加测试skill，skill更新；状态改为进行中"
python scripts/atomic/query_work_item.py --select-items "标题,状态" --conditions "状态=进行中"
python scripts/atomic/query_work_item.py --select-items "标识,标题,状态" --conditions "状态=进行中" --relate-conditions "状态!=已关闭 AND 工作项类型=任务" --relate-type 子级 --relatedOptions 有匹配的关联工作项
python scripts/atomic/query_work_item.py --select-items "标识,标题,状态" --conditions "状态!=已关闭" --relate-conditions "优先级=P0 OR 标题 like 紧急" --tree-type 子级 --treeOptions 显示所有
python scripts/atomic/query_my_work_item.py --select-items "标题,状态" --conditions "状态=进行中" --query-type assigned_to_me
python scripts/atomic/get_work_item_detail.py --work-item-id PLM-12345
python scripts/atomic/get_work_item_detail.py --work-item-id PLM-12345,PLM-12346
# 导出
python scripts/atomic/export_work_item.py --select-items "标题,状态" --conditions "状态=进行中"
python scripts/atomic/export_work_item.py --select-items "标识,标题,状态" --conditions "工作项类型=用户故事 AND 工作区=业务运营" --order-by "更新时间 desc" --limit 100
python scripts/atomic/export_work_item.py -c "工作项类型=任务 AND (状态=进行中 OR 状态=待处理)"
python scripts/atomic/stats_work_item.py --select-items "标识" --conditions "状态=进行中" --stats count
# 分组聚合（须 aggregate 子命令与 --group-by；场景互斥；完整表见 references/stats_work_item.md#aggregate-scenarios）
python scripts/atomic/stats_work_item.py aggregate --group-by "状态" --conditions "工作项类型=缺陷" --user-input "按状态统计缺陷分布"
python scripts/atomic/stats_work_item.py aggregate --group-by "状态,指派给" -c "状态!=已取消" --user-input "按状态和指派给分组"
python scripts/atomic/stats_work_item.py aggregate --query-type assigned_to_me --group-by "工作区" --user-input "指派给我的按工作区分组"
python scripts/atomic/stats_work_item.py aggregate --overdue --group-by "状态" --work-item-type "任务" --user-input "我超期任务按状态分组"
python scripts/atomic/stats_work_item.py aggregate --group-by "状态" --conditions "标识=PLM-123" --relate-type 子级 --relate-conditions "工作项类型=任务" --user-input "子任务按状态分组"
python scripts/atomic/stats_work_item.py aggregate --group-by "状态" --conditions "工作项类型=市场需求" --relate-conditions "工作项类型=用户故事" --tree-type 子级 --treeOptions 显示所有 --user-input "树查询按状态分组"
python scripts/atomic/stats_work_item.py aggregate --group-by "状态" --query-url "https://rdcloud.zte.com.cn/uportal/rd-uportal/workspaces/PLM/apps/wim/queries?wicViewId=xxxxxxxxxxxxxxxxxxxxxxxx" --user-input "视图下按状态分组"
python scripts/atomic/query_work_item_by_url.py --query-url "https://rdcloud.zte.com.cn/uportal/rd-uportal/workspaces/PLM/apps/wim/queries?wicViewId=xxx"
# 按查询名/视图名查询（与按链接同一脚本、同一处理方式）
python scripts/atomic/query_work_item_by_url.py --user-input "根据查询名 WODE 查询"
python scripts/atomic/query_work_item_by_url.py --query-url "WODE" --user-input "根据查询名 WODE 查询"
# 工作项评论
python scripts/atomic/query_comment.py --work-item-id PLM-4346370 --user-input "查看 PLM-4346370 的评论"
python scripts/atomic/add_comment.py --work-item-id PLM-3875805 --comment-detail "<p>评论内容</p>" --user-input "给 PLM-3875805 加评论"
python scripts/atomic/add_comment.py --work-item-id PLM-3875805 --comment-detail "<p>回复</p>" --reply-to 2 --user-input "回复第 2 条"
# 超期查询
python scripts/atomic/query_overdue_work_item.py --user-input "查询我超期的任务"
python scripts/atomic/query_overdue_work_item.py --work-item-type 缺陷 --user-input "查询我超期的缺陷"
python scripts/atomic/query_overdue_work_item.py --work-item-type 任务,用户故事 --limit 50 --user-input "查询超期的任务和用户故事"
# 规则智能诊断（详见 references/rule_diagnose.md）
python scripts/atomic/rule_diagnose.py --work-item-id DT_TP-1066896 --triggers '["WHEN_UPDATED"]' --actions '["COPY","ASSIGN_TO"]' --fields '["处理人"]'
# 规则日志查询
python scripts/atomic/rule_log_query.py --workspace WANT --log-count 10
```
邮件/消息：仅作为创建、更新、查询的**附属能力**；**仅当用户明确说出「发邮件」「发消息」等时才**设置 is_send_email/is_send_icenter，未明确指定则禁止开启。发送逻辑已内联在 create/update/query 脚本中，**禁止**在无创建/更新/查询意图时单独执行发送。

### 原始用户输入（--user-input）
各脚本均支持参数 **--user-input**（或 **--userInput**），表示**用户的原始输入**。**调用任何脚本时都必须传递此参数**，值为用户真实、完整的一句话，不得截断、改写或省略；用于工作区/类型/字段解析的上下文与日志。未传或传空将影响工作区语义解析等能力。
## 触发条件
- **创建**：创建工作项、新建任务/需求/缺陷/变更、创建 XXX（XXX 为工作项类型名）；可选创建后与指定工作项建立关联（--relate-workitem-ids、--relate-type）
- **更新**：更新/修改工作项、更新状态、更新 XXX（XXX 为工作项标识）；可选更新后与指定工作项建立关联（--relate-workitem-ids、--relate-type）
- **关联工作项**：将工作项 A 与 B（及以上）建立关联，如将 A 作为 B 的子级、将 A 与 B 设为相关；使用 `relate_work_item.py` 或创建/更新脚本的关联参数
- **查询通用**：查询工作项、搜索工作项、按条件查（标签、状态、标题、优先级等）；解析为查询列（--select-items）与查询条件（--conditions）；条件中的字段名取用户句中指称该字段的完整表述，不得截断或子串替换
- **分组聚合 / 分类汇总**：用户明确表达**按某（些）字段维度拆开看数量**（如「按…分组」「按…聚合」「按…分类汇总」「各…分别多少条」「…的分布」）且需要**多行分组结果**（每组一条数量），**不是**只要一条总数/求和/平均、**不是**只要明细列表 → `stats_work_item.py aggregate`（首参 **`aggregate`**），须解析 **--group-by**；未说出分组维度时**不得**默认走本路径。筛选条件仍用 --conditions，与通用查询相同
- **查询超期工作项**：我超期的任务、查询超期的缺陷、哪些单据超期了、到期未完成的工作项等（含「超期」「过期」「到期未完成」语义）→ ，见 [query_overdue_work_item.md](references/query_overdue_work_item.md)
- **查询与我相关**：我的工作项、我的任务、我的缺陷、我的变更请求、指派给我的、我的待办、我创建的、我编辑的、我关注的（不含超期语义）
- **根据查询链接查询 / 按查询名/视图名查询**：用户意图为查询且（1）明确提供了查询链接（研发云工作项查询 URL，支持多种模式、不限定为某两类），或（2）明确指定按「查询名/视图名」查询（如：根据查询名 WODE、根据视图名 我的查询）。**二者均按「根据查询链接」方式处理**，使用 `query_work_item_by_url.py`，见 [query_work_item_by_url.md](references/query_work_item_by_url.md)。
- **规则智能诊断**：用户遇到规则相关问题时进行诊断排查（必填/只读/隐藏、功能不可用、手动编辑限制、自动赋值/清空、状态流转、关联/通知/接口等），详见 [references/rule_diagnose.md](references/rule_diagnose.md) → 使用 `rule_diagnose.py`。

### 规则智能诊断使用规则（必遵）
- **必须读取** [references/rule_diagnose.md](references/rule_diagnose.md)，**严格按照其中的映射表解析参数**
- **优先使用大模型解析参数**：从用户问题中解析 `--triggers`、`--states`、`--actions`、`--fields`，但**必须严格遵循rule_diagnose.md中的映射表**，**禁止**根据自己的训练数据或语义理解自行推断动作类型或触发器（如SET_FIELD_VALUE、AUTO_FILL等不在映射表中的值）
- **错误日志查选用 `rule_log_query.py`**
- 完整说明见 [references/rule_diagnose.md](references/rule_diagnose.md)
- **错误日志查询用 rule_log_query.py**：用户要看错误日志时，使用 `rule_log_query.py`，**禁止**用 `rule_diagnose.py`
- **禁止用查询脚本替代诊断**：规则诊断有专用脚本 `rule_diagnose.py`，**禁止**用 `query_work_item.py` 查询规则或日志
- **规则诊断不加 --user-input**：`--user-input` 在本脚本中为可选，有则传用户完整输入，不做解析依赖

- **工作项评论**：查询评论、查看评论、评论列表、给某工作项加评论、回复评论等 → `query_comment.py` / `add_comment.py`，见 [work_item_comment.md](references/work_item_comment.md)。

### 创建类型边界（必遵）
- 本技能当前不支持创建以下工作项类型：**原始需求、外场故障、测试用例、测试计划、测试记录、IT flow单（事件单、问题单、变更单）、兴管家报障单、兴管家问题单**。
- 当用户明确要求创建上述类型时，必须直接告知“当前技能不支持该类型创建”，禁止擅自改成其它类型继续创建。
- 告知不支持后，必须明确引导用户改用 **Studio技能包** 中支持该类型创建的技能继续处理；禁止给出未确认存在的处理路径、脚本名或临时方案。
## AI 必遵规则
- **工具与实现**须与上文「工具与实现约束（必遵）」一致：**必须**调用已有 `scripts/` 脚本；**禁止**用 write、edit、web-search、web_fetch、web_search_coclaw、web_fetch_coclaw 等处理本业务或替代脚本；**禁止**新建 Python 脚本。

### 0. 关联类型传值前置约束（高优先级）
- 在关联查询/树查询中，若用户语义**明确提及关联类型词**（如子级、父级、相关、前置、后续、计划、被计划），则传入对应的 `--relate-type` 或 `--tree-type`。
- **“关联关系是 X” / “关联类型是 X”**：X 为**关联类型**，必须写入 `--relate-type`，**禁止**将 X 解析为字段条件（如 状态、优先级等）。示例：`关联关系是计划` → `--relate-type 计划`；**禁止**将“计划”解析为 `状态=计划`。
- 若用户说“关联工作项”“有关联的”等但**未指定具体类型**，传 `--relate-type 全部`，表示查询所有类型的关联；**禁止**臆测为“相关”等单一类型。
- 用户仅说“关联工作项/有关联”不等于关系类型“相关”；只有明确出现“相关”这一关系类型时，才传 `--relate-type 相关`。
#### 0.1 口语 → 关联类型：仅当句中出现对应词才映射（禁止凭类型组合猜关系）
**只有**用户原话里出现下表左列**或**脚本已支持的同义表述时，才将 `--relate-type` / `--tree-type` 设为右列；**不得**根据「主对象类型 + 关联对象类型」（如「需求 + 任务」）自行推断为子级或相关。
| 用户常出现的说法（含同义） | 传入 `--relate-type` 或 `--tree-type` |
|---------------------------|----------------------------------------|
| 下面、下面的、下级、子任务、挂在…下面、分解出来的、子项 | **子级** |
| 上面、上级、父、父级、父需求、父任务 | **父级** |
| 相关、互为相关、设为相关、相关关系 | **相关** |
| 前置、必须先做、阻塞 | **前置** |
| 后续、之后才能、跟着做 | **后续** |
| 计划、被计划、排进迭代/版本（语义为计划关系时） | **计划** / **被计划**（按语义词方向，勿与「状态=计划」混淆） |
| 源、副本、从…复制 | **源** / **副本** |
| 测试、被测试、用例测 | **测试** / **被测试** |
| 依赖、被依赖、依赖关系 | **依赖** / **被依赖** |
| 影响、影响者 | **影响** / **影响者** |
| 上游、下游 | **上游** / **下游** |
**未命中上表任何词**且用户**未**说出其它已在接口中登记的中文关联类型名（与建立关联时所用类型名一致）时：**必须**传 `--relate-type 全部`。**禁止**：① 默认 `--relate-type 相关`；② 仅凭「主从工作项类型常见是子级」猜测子级——句中**没有**「下面/子级/下级」等层级词时，**不得**传子级，应 `全部`。
#### 0.2 易混边界（必遵）
- **「关联」「连到」「挂到」单独出现**：不等于「相关」类型；无「相关」二字则**不得**传 `--relate-type 相关`。
- **「X 关联的 Y」「X 和 Y 关联」**：若**无**子级/父级/相关等上表词汇 → `--relate-type 全部`。
- **树查询**需要多级层级时，用户须出现「所有下级」「整棵树」「多级」等或明确 `--tree-type`；树查询的 `--tree-type` 规则与同表（如子级树），**禁止**在无用户依据时随意指定树类型。
- **展示**：本次语义解析结果中须用业务语说明**采用的关联类型**或**全部类型**；禁止向用户暴露内部枚举名。
#### 0.3 建立关联时：口语「子级/父级」与 `--relate-type` 取反向（必遵）
适用于 **`create_work_item.py` / `update_work_item.py` / `relate_work_item.py`** 中通过 `--relate-workitem-ids` + `--relate-type` **新建一条关联**的场景。  
**不适用**于 `query_work_item.py` 的关联查询/树查询：查询侧仍按 §0.1 将「下面/下级/子任务」等映射为 **`子级`** 等，**不做**本节取反。
- 参数角色：`--work-item-ids`（或创建/更新脚本里**当前操作的工作项**）与 `--relate-workitem-ids`（**对方**）确定两端；口语「**A 作为 B 的子级**」「**A 挂在 B 下面**」「**A 是 B 的子任务**」表示 A 为子、B 为父 → **`--relate-type` 必须传 `父级`**（与口语中的「子级」二字取**反向**），**禁止**照抄「子级」传 `--relate-type 子级`。
- 口语「**A 作为 B 的父级**」「**A 是 B 的上级**」→ **`--relate-type` 必须传 `子级`**（与口语「父级」取反向）。
- **对称关系**（如 **相关**）无正反：口语「设为相关」仍传 `--relate-type 相关`。
- **前置/后续、计划/被计划、依赖/被依赖、测试/被测试** 等成对类型：按「当前工作项相对对方」在接口中的关系方向，在成对名称中**择一**；口语若只强调「前置/后续」之一，勿与字面混用，须与主从工作项方向一致（原则同子级/父级：**口语描述的关系词与脚本类型名可能为成对中的另一侧**）。

### 1. 先解析，直接执行（不做执行前确认）
先完成语义解析后直接调用脚本，不做执行前确认、不增加确认回合。用户输入明确时，按当前解析直接执行；信息缺失时，仅补充最小必要问题。执行完成后，**必须先展示本次语义解析结果**（如查询类型、查询列、查询条件、工作区、工作项类型、排序、条数、是否发邮件/消息、接收人等），再展示脚本业务结果。
解析信息展示中，禁止暴露脚本路径与内部实现细节；命令行参数名仅用于脚本内部，不面向用户解释。

### 2. 查询结果必须展示给用户，禁止吞掉、禁止随意总结或省略
**无论是否发送邮件/消息**，查询脚本返回的**查询结果必须完整展示给用户**。禁止以「已发送通知」为由不展示结果、仅提示「已发送」或截断结果内容。**执行完查询脚本后，禁止随意总结结果、省略结果，必须将脚本输出的结果完整展示给用户**；对工作项列表结果，**必须以表格形式展示**（如标题、标识、工作项类型、更新时间等列），不得改为纯文字摘要；若用户语义中未明确提出“总结/汇总/精简”，则**不得**总结或省略结果；仅当用户**明确要求**总结、汇总或精简时，才允许对结果做总结。发送邮件/消息仅为附加动作，**不得替代**结果展示；用户必须能看到本次查询的完整结果（列表、条数、内容等）。

### 2.1 心智模型必须完整保留，禁止删除或总结（必遵）
脚本返回内容中，**在查询结果（或其它业务输出）后面可能追加心智模型**（如查询示例、使用提醒、多匹配字段说明等）。该部分由技能/脚本侧添加，用于引导用户。
- **必须**：将脚本的**完整输出**（含结果 + 心智模型）原样展示给用户，不得截断、不得省略心智模型段落。
- **禁止**：对脚本输出做「总结」「概括」「精简」时**删除、合并或改写**心智模型内容；**禁止**只展示查询结果而丢掉后面的示例或提醒。
- 若用户**明确要求**「只要结果不要说明」「精简掉后面的提示」等，才可仅展示业务结果并说明已省略提示；否则一律**完整保留**心智模型并呈现给用户。

### 3. 同一意图只执行一次，禁止重试
同一意图内仅调用一次对应脚本。脚本报错或查询结果为空 → 返回结果/错误并结束；禁止在同一意图里换参数重试。**除非用户明确要求再次执行**（如「再执行一次」「重跑」「再查一次」等），才可按用户指示再调一次脚本。仅当用户明确提出新意图时，进入新一轮「解析 → 执行」。

### 4. 工作项类型：忠实于用户表述，禁止擅自替换或揣测
工作项类型以用户原文为准，**不得**替换或揣测（如用户说「问题单」即按「问题单」，禁止改为「缺陷」）。仅当用户**明确**要求映射时才可按用户指示处理。

### 5. 条件字段名：按用户指称的完整表述，禁止截断或子串
- **定义**：条件中的**字段名** = 用户在该条件里、用于指称「按哪个字段筛选」的**连续表述**，到操作符或取值之前结束；可含英文、数字、中文等，**不得**因包含英文/数字/产品名等而只取其中一部分（如只取后半段、只取中文、只取“通用词”）。
- **约束**：解析出的字段名必须与用户该处表述**逐字一致**；**禁止**用用户未说出的更短名称或真子串作为字段名。与接口 name/nameZh/nameEn 匹配时，**仅能**用该完整表述做整段匹配，**禁止**先用其真子串（更短片段）匹配；整段匹配不到时才终止或请用户确认。

### 6. 查询入参：查询列与条件（工作区、工作项类型并入 conditions）
- **查询脚本**（query_work_item、query_my_work_item、export_work_item、stats_work_item；**分组聚合**为 **stats_work_item.py aggregate**）使用 **--select-items**（查询列）与 **--conditions**（查询条件，与 where 后同格式）。**解析出来的工作区、工作项类型均作为 --conditions 的一部分**：从用户表述中解析出的「在哪个工作区查」「查哪种工作项类型」须写入 --conditions，与其它筛选条件（如状态、标题）用 AND 等组合成完整条件串，不单独传工作区/类型参数；工作区仍由脚本从配置或最近访问解析以确定查询目标。**分组聚合**另须首参 **`aggregate`** 并传入 **--group-by**（分组维度字段名，逗号分隔）。
- **查询列**：用户提到的要查的字段解析为 --select-items，如「标题,状态,标识」；未提则可不传，脚本用默认列。
- **查询条件**：用户提到的**工作区、工作项类型**及**其它筛选条件**（如「状态=进行中」「标题 like 测试」）均解析为 --conditions 的一部分，共同组成条件组合；未提则可不传，脚本不做条件过滤。

### 7. 创建与更新工作项：指派给、处理人解析（必遵）
在**创建工作项**、**更新工作项**时，须将用户表述中的人员相关意图解析为对应**字段名称**，并写入 `--fields`（脚本内部会按名称解析并转为工号）：
| 用户表述 | 解析为字段名称 | 说明 |
|----------|----------------|------|
| **指派给 XXX**、指派给张三、指派给我 | **指派给** | value 为人员（@me/姓名/工号），脚本会转为工号 |
| **处理人设置为 XXX**、处理人设为张三、设置处理人为我 | **处理人** | value 为人员（@me/姓名/工号），脚本会转为工号 |
| **更新里某字段“追加”值**（如「标签追加A，B」） | 该字段保持原字段名 | 仅该字段在 `--fields` 中增加 `modifyType: "appendAfter"`；同字段多值须归并为**一个对象**（禁止同 key 多对象）；细则见 `references/update_work_item.md` |
解析时**必须**按上表映射：用户说「指派给」即用字段名称「指派给」，用户说「处理人」即用字段名称「处理人」，不得混用。
## 各场景执行顺序
| 场景 | 步骤 |
|------|------|
| 创建 | 解析（工作区、类型、字段、通知）→ 调用创建脚本 → 展示「本次语义解析结果」→ 展示创建结果 |
| 更新 | 解析（工作项标识、待改字段、通知）→ 调用更新脚本 → 展示「本次语义解析结果」→ 展示更新结果 |
| 查询通用 | 解析（工作区、工作项类型、查询列、查询条件、通知）→ 调用查询脚本 → 展示「本次语义解析结果」→ 展示查询结果（再按「查询结果后的通知逻辑」处理） |
| 查询与我相关 | 解析（查询类型、工作区、工作项类型、查询列、查询条件、通知）→ 调用与我相关查询脚本 → 展示「本次语义解析结果」→ 展示查询结果 |
| **导出** | 解析（工作区、工作项类型、导出列、导出条件、条数、层级/关联导出）→ 调用 `export_work_item.py` → 展示「本次语义解析结果」→ 展示导出结果 |
| 根据 URL 查询 / 按查询名/视图名查询 | 解析查询链接或查询名/视图名 → 调用 `query_work_item_by_url.py`（按链接与按名称同脚本同流程）→ 展示「本次语义解析结果」→ 展示查询结果 |
| **分组聚合** | 解析查询列、条件、**分组字段（--group-by）** → 调用 `stats_work_item.py aggregate` → 展示「本次语义解析结果」→ 展示各分组统计结果 |
| 工作项评论 | 解析工作项标识；回复时解析「回复第 N 条」→ --reply-to N，或父评论 id→--parent-id；调用 `query_comment.py` 或 `add_comment.py` → 展示结果 |
查询列、条件格式与字段名解析见 [query_work_item.md](references/query_work_item.md)、[query_my_work_item.md](references/query_my_work_item.md)。评论参数与示例见 [work_item_comment.md](references/work_item_comment.md)。
## 发送邮件/消息：仅允许与工作项业务联动（必遵）
- **必须明确指定才允许**：**仅当用户明确说出「发邮件」「邮件通知」「发消息」「iCenter 通知」等发送意图时**，才可设置 is_send_email / is_send_icenter 为 true 或调用发送相关逻辑。**禁止**在用户未明确提及发送邮件/消息时默认开启、推断为需要发送或主动建议并代为勾选发送。未明确指定时，is_send_email、is_send_icenter 必须为 false 或不传。
- **禁止直接调用**：不得在用户仅表达「发邮件」「发消息」且**无**创建、更新、查询工作项意图时执行发送。邮件与 iCenter 发送逻辑已内联在 create_work_item、update_work_item、query_work_item、query_my_work_item 脚本中，仅由创建/更新/查询流程触发（或针对本次查询结果的「事后发送」）。
- **允许场景**：① 创建/更新/查询时用户**明确提出**发送通知 → 由 create/update/query 脚本内联发送逻辑完成；② 查询已执行完毕，用户在同一会话中针对**本次查询结果**要求「把结果发给我/发给 XXX」→ 由查询脚本内联发送逻辑完成，**禁止**再次执行查询。
- 用户仅说「发一封邮件给张三」且无任何工作项操作时 → **禁止执行**，须提示：「邮件/消息发送需与工作项业务联动，请先进行创建、更新或查询工作项，并在该流程中提出发送需求，或先完成一次查询后再要求将结果发送给他人。」
## 发送邮件/消息：接收人解析（必遵）
从用户表述中**直接解析接收人**，解析出则传入 --mail-to / --icenter-to，**无需再次确认接收人**；仅当表述中**未出现**接收人时才在结果后确认或默认当前用户。
| 用户表述 | 解析结果 | 是否再确认接收人 |
|----------|----------|------------------|
| 发给我、发送给我、给我、发邮件/消息给我 | 当前用户（@me / 当前工号） | **否** |
| 给 XXX、发给 XXX、发给茆看、通知 XXX | 接收人 = XXX（姓名或工号，脚本内可解析） | **否** |
| 发邮件、发消息、并发送通知（未说给谁） | 接收人未明确 | **是**（结果后确认；不确认则默认当前用户） |
解析结果展示时若已解析出接收人，须在「接收人」项下列出（如「接收人：我」或「接收人：茆看」），并直接执行，禁止再追问接收人确认。
## 查询结果后的通知逻辑
**前置约束**：查询脚本执行后，**必须先将查询结果完整展示给用户**（列表、条数、内容等），再处理是否发送通知。禁止只执行发送而不展示结果，或仅提示「已发送」却吞掉查询内容。
在**查询**返回结果后，根据用户**一开始的输入**是否**明确提及**「发送通知」及是否**已解析出接收人**处理（**均不再重复调用查询脚本**）：
| 用户输入 | 返回结果后的动作 |
|----------|------------------|
| 未提发送通知 | 结果后补充：「是否需要将本次查询结果发送通知？如需发送，请告知发送方式（消息/邮件）和接收人。」若用户后续针对**本次查询结果**要求发送，由查询脚本内联的发送逻辑将**已返回的结果文本**作为正文发送；**不再次执行查询**。 |
| 有提发送通知但**未解析出接收人** | 结果后确认接收人；不确认则默认当前用户。确认后由脚本内联发送逻辑完成。 |
| 有提发送通知且**已解析出接收人**（如「发给我」「给茆看」） | 首次查询时命令行已传 is_send_email/is_send_icenter 及 mailTo/icenter_to，脚本内部直接发送，**无需结果后再确认接收人**。 |
**禁止**为发送而再次调用查询脚本。邮件与 iCenter 发送由各业务脚本内联逻辑完成，无需独立发送脚本。
## 查询脚本入参说明（命令行，必遵）
调用 **query_work_item.py** / **query_my_work_item.py** / **stats_work_item.py aggregate** 时，AI 须按语义解析结果组装**命令行参数**调用脚本。**仅按下表对应参数赋值，禁止自行发明字段或含义。** 工作区、工作项类型、邮件接收人、消息接收人均支持**多值**，用**英文逗号**分隔。
**分组聚合（`stats_work_item.py aggregate`）**：命令行**第一个参数**须为 **`aggregate`**；其后 `--select-items`、`--conditions`、`--order-by`、`--limit` 的含义与下表及 [query_work_item.md](references/query_work_item.md) 一致；**另须必填** `--group-by`（分组维度字段名，逗号分隔）。**支持**与我相关（`--query-type`）、超期（`--overdue`）、关联/树（`--relate-type` / `--tree-type` 等与 `query_work_item` 相同）、视图（`--query-url` / `--view-id`，须 24 位视图 ID，见 [stats_work_item.md · 二、分组聚合](references/stats_work_item.md)）；**场景互斥**规则见该 reference。
**列表类查询 `--limit`（条数上限，必遵）**：用户**未**明确说要返回多少条时，**不传** `--limit`（列表类查询脚本按默认 **20** 条返回）。用户明确说了「前 N 条」「最多 N 条」「只要 N 条」等时，将 **N** 传给 `--limit`；**无论语义多大，传给脚本的值不得超过 10000**（若用户说「两万条」，仍传 `--limit 10000`）。超期查询、导出、统计、**分组聚合**等脚本的 `--limit` 含义与上限见各 reference，**上限同样不超过 10000**（导出未指定 limit 时脚本侧分页默认见 `export_work_item.md`；聚合脚本中 limit 主要作用于请求体 `pageSize` 上限）。**按链接/查询名查询**（`query_work_item_by_url.py`）无 `--limit` 参数，条数以接口与视图为准。
**入参形式强制规则**：只要从用户表述中解析出**发邮件/发消息或接收人**，**必须**通过对应命令行参数传入（--is-send-email/--is-send-icenter、--mail-to/--icenter-to），禁止省略。查询列、条件解析为 --select-items、--conditions。
| 命令行参数 | 含义 | 从用户表述中如何取值 | 必填 | 示例 |
|------------|------|----------------------|------|------|
| **--select-items, --selectItems** | 查询列 | 用户说的「查哪些字段」→ 英文逗号分隔的字段名称；未提则可不传，脚本用默认列 | 否 | `--select-items "标题,状态,标识"` |
| **--conditions, -c** | 条件组合 | 用户说的「在哪个工作区」「查哪种类型」及「按什么条件」→ **均作为条件组合**：工作区、工作项类型与状态/标题等用 AND/OR、括号 `()` 组合与嵌套；如 工作项类型=用户故事 AND 工作区=业务运营、工作项类型=任务 AND 状态=进行中；未提则可不传 | 否 | 见下文「条件组合与嵌套示例」 |
| **--order-by, --orderBy** | 排序 | 用户说的「按什么排序」→ 如 更新时间 desc、标题 asc, 状态 desc；不传则默认按更新时间倒序 | 否 | `--order-by "更新时间 desc"` |
| **--limit, -n** | 条数上限 | 见上文「列表类查询 limit」：未明确条数则不传（默认 20）；明确则传正整数且 **min(语义, 10000)**；导出/统计/超期见各 reference | 否 | `--limit 10` |
| **--is-send-email** | 是否发邮件 | **仅当用户明确说出**「发邮件」「邮件通知」等时 → true；**未明确提及则必须 false 或不传** | 否，默认 false | `--is-send-email true` |
| **--is-send-icenter** | 是否发消息 | **仅当用户明确说出**「发消息」「iCenter 通知」等时 → true；**未明确提及则必须 false 或不传** | 否，默认 false | `--is-send-icenter true` |
| **--mail-to** | 邮件接收人 | 「发给我」「给张三」等 → 我/姓名/工号；多人英文逗号分隔 | 否 | `--mail-to @me` |
| **--icenter-to** | 消息接收人 | 「发给我」「给茆看」「通知启航团队」等；多人或群组英文逗号分隔 | 否 | `--icenter-to @me` |
| **--query-type**（仅 query_my_work_item） | 与我相关查询类型 | 从「与我相关」表述解析（指派给我、我创建的等），见「与我相关：queryType 解析」表；未明确时默认 assigned_to_me | 与我相关时必填 | `--query-type assigned_to_me` |
| **--group-by, --groupBy**（仅 `stats_work_item.py aggregate`） | 分组维度 | 用户说的「按什么分组/聚合/分类汇总」→ 字段名称，多维度英文逗号分隔，与 `--select-items` 同源解析 | **是** | `--group-by "状态"`、`--group-by "状态,指派给"` |
`query_work_item.py` 查询模式互斥规则：普通查询（不传 `--relate-type`/`--tree-type`）、关联查询（传 `--relate-type`，可带 `--relate-conditions`、`--relatedOptions`）、树查询（传 `--tree-type`，可带 `--relate-conditions`、`--treeOptions`）三者不可混用。`--relate-type` 与 `--tree-type` 禁止同时传入；`--relate-conditions` 仅在关联/树查询时使用，解析后写入 `targetClauses`。`--relatedOptions` 推荐使用中文值（有匹配的关联工作项/无匹配的关联工作项/所有工作项），脚本会映射为接口值 linksOneHopMustContain/linksOneHopDoesNotContain/linksOneHopMayContain；`--treeOptions` 推荐使用中文值（基于顶级显示/基于关联显示/显示所有），脚本会映射为接口值 top/related/all。
关联/树查询语义约束：用户语义中有关联查询意图时，**关联类型解析一律遵守上文「§0.1 口语 → 关联类型」「§0.2 易混边界」**；若明确说明关系类型（如子级、父级、相关、前置、后续），传对应的 `--relate-type`；若只说“关联工作项”“有关联的”等未指定类型，传 `--relate-type 全部`（查询所有类型）；**禁止**将“关联工作项”臆测为“相关”，**禁止**凭主从类型组合猜子级/相关。
关联/树查询主从条件约束（必遵）：
- **主对象、关联对象均可带条件**：主对象条件进入 `--conditions`（sourceClauses），关联对象/关联侧条件进入 `--relate-conditions`（targetClauses）。
- 用户语义中的“主对象条件”（如「**状态为进行中的任务**下面的用户故事」里的 `状态=进行中`）必须进入 `--conditions`。
- 用户语义中的“关联侧条件”（如「市场需求下面的**优先级为P0的任务**」里的 `优先级=P0`）必须进入 `--relate-conditions`。用户未明确关联侧约束时，`--relate-conditions` 留空。
- 禁止将主对象条件误放到 `--relate-conditions`。  
  典型示例：`查询PLM-3298581有哪些关联工作项`  
  - 正确：`--conditions "标识=PLM-3298581"`，`--relate-conditions ""`  
  - 错误：`--conditions ""`，`--relate-conditions "标识=PLM-3298581"`
关联查询语义识别方法（主对象 / 关联对象 / 关联条件）：
- **主对象**：用户想“从谁出发”去查关联，优先取「X 的关联工作项」「X 下面的 Y」「X 关联的 Y」里的 `X`。主对象约束写入 `--conditions`。
- **关联对象**：用户想“关联侧是什么”，常见在「X 下面的 Y」「X 关联的 Y」「Y 对应的 X」里的 `Y`。关联对象约束写入 `--relate-conditions`。
- **关联条件**：修饰关联对象的限定语（如「合入版本是 xx 版本的变更活动」里的 `合入版本=xx版本`）写入 `--relate-conditions`，与关联对象条件一起 AND 组合。
- **主对象/关联对象类型映射**：对象是工作项类型名（如市场需求、用户故事、变更活动）时，映射为对应类型条件；对象是具体标识（如 `PLM-3298581`）时，映射为 `标识=...`。
- **默认语序判定（必遵）**：在用户语义未出现明确反向词时，按“前主后从”识别，即用户输入中先出现的对象默认作为主对象（`--conditions`），后出现的对象默认作为关联对象（`--relate-conditions`）。
- **反向词优先**：若语义中出现明确反向表达（如「Y 对应的 X」「关联到 X 的 Y」），则按语义词方向识别主从，不按位置机械判定。
常见口语表达到参数的映射示例（必遵）：
- `查询市场需求下面的用户故事`  
  - `--conditions`：主对象=市场需求  
  - `--relate-conditions`：关联对象=用户故事
- `查询变更请求下的变更活动`  
  - `--conditions`：主对象=变更请求  
  - `--relate-conditions`：关联对象=变更活动
- `查合入版本是xx版本的变更活动对应的变更请求`  
  - `--conditions`：主对象=变更请求  
  - `--relate-conditions`：关联对象=变更活动 AND 关联条件=合入版本=xx版本
- `查询市场需求关联的任务`  
  - `--conditions`：主对象=市场需求  
  - `--relate-conditions`：关联对象=任务
- `查询PLM-3298581有哪些关联工作项`  
  - `--conditions`：`标识=PLM-3298581`  
  - `--relate-conditions`：空（用户未明确关联侧对象/条件）
- `查询ZXDI-109774工作项下关联关系是计划的市场需求`  
  - `--conditions`：`标识=ZXDI-109774`  
  - `--relate-type`：`计划`（“关联关系是计划”中的“计划”为关联类型）  
  - `--relate-conditions`：`工作项类型=市场需求`  
  - **禁止**：将“计划”解析为 `状态=计划` 放入 relate-conditions；禁止传 `--relate-type 全部`
**与我相关**脚本必须传入 **--query-type**（见「与我相关：queryType 解析」表）。展示给用户确认时：仅以业务用语列出已解析出的**查询列、查询条件、排序、条数、查询类型、是否发邮件/消息、接收人**等，**禁止出现任何脚本名、参数名（如 --select-items、--conditions）或 SQL**；首次确认后再用命令行调脚本，同一解析结果后续说「执行」等直接调脚本。
## 查询语义识别与赋值流程（必遵）
按以下顺序从用户表述中抽取并赋值，**仅按规则设 true/false 与接收人，不猜**。
1. **识别意图类型**：**导出**（导出、导出为 Excel、导出查询结果等；包括**先查询再导出**如「查询XXX，导出」「查XXX，然后导出」）→ 用 export_work_item.py；**与我相关**（指派给我、我创建的等）→ 用 query_my_work_item.py，并解析 --query-type；**根据查询链接/查询名** → 用 query_work_item_by_url.py；**工作项评论**（查评论、加评论、回复评论）→ 用 query_comment.py / add_comment.py，见 [work_item_comment.md](references/work_item_comment.md)；**分组聚合 / 分类汇总**（按某字段看「每组各有多少条」，见上文「触发条件」）→ 用 **stats_work_item.py aggregate**（首参 **`aggregate`**），解析 **--group-by**；**只要一条总数/求和/平均**、不要按字段分组 → 用不带子命令的 stats_work_item.py；否则用 query_work_item.py。**导出场景须完整理解整句**：查询条件、工作区、类型等可从句中的「查询/查/找」等前置部分解析，导出意图可从句末的「导出/导出为」等识别。
2. **抽取查询列与条件**：用户说的「查哪些列」/「导出哪些列」→ --select-items（英文逗号分隔的字段名称）；用户说的「在哪个工作区」「查哪种类型」及「按什么条件」→ 均并入 --conditions（**条件组合**：工作区、工作项类型与状态/标题等条件用 AND/OR、括号 `()` 组合与嵌套）；用户说的「按什么排序」→ --order-by（如 更新时间 desc）；用户说的「前 N 条」「最多 N 条」→ --limit（正整数，且 **不超过 10000**，超过语义也封顶 10000）；用户**未**说条数时 **不传** --limit（列表查询默认 20）。**分组聚合**时用户说的「按什么分组/分类汇总」→ **--group-by**（字段名逗号分隔，与 select 同源）。**导出**时用户说的「关联导出」「层级导出」「带关联导出」→ --flap true。未提查询列可不传，脚本用默认列；未提条件、排序可不传；未提条数则不传 limit。
3. **通知与接收人**：**仅当用户明确说出**「发邮件」「邮件通知」等 → --is-send-email true，并解析 --mail-to；**仅当用户明确说出**「发消息」「iCenter」「通知 XXX」等 → --is-send-icenter true，并解析 --icenter-to。**未明确提及发邮件/发消息时，禁止默认开启或推断。**
**规则**：**导出**一经识别必须用 export_work_item.py，禁止用查询脚本。**邮件/消息发送必须用户明确指定才允许**。接收人从同一句解析，解析到即填入 --mail-to 或 --icenter-to，**消息接收人统一使用 --icenter-to**。
## 查询入参：查询列与条件
- **--select-items**：查询列，英文逗号分隔的字段名称；不传则脚本用默认列（标识、标题、状态、工作项类型、更新时间）。
- **--conditions**：**条件组合**（不含 "where " 前缀）。**解析出的工作区、工作项类型均作为 conditions 的一部分**，与其它条件一起组成完整条件串。支持：
  - **工作区、工作项类型**：从用户表述解析出的「在 XX 工作区」「查 XX 类型」须写入条件（与其它条件用 AND 等组合）。
  - **AND / OR**：多个条件用 `AND`、`OR` 连接，如 `工作项类型=任务 AND 状态=进行中 AND 标题 like 测试`。
  - **括号 `()`**：用于分组与嵌套，改变优先级，如 `(状态=进行中 OR 状态=待处理) AND 优先级=P0`。
  - 单条件示例：`状态=进行中`、`工作项类型=任务`、`工作区=业务运营`、`标题 like 测试`、`优先级 in P0,P1`。组合示例：`工作项类型=用户故事 AND 工作区=业务运营`。
  - 不传则无过滤。
  - **采集规则**：组装 `--conditions` 时，应尽可能完整保留用户原始输入中所有与筛选相关的约束（工作区、工作项类型、状态、标题、优先级、时间范围等），不得随意丢弃、弱化或合并用户已明确说出的任意条件。
- **与我相关**：须解析 **--query-type**；未明确时默认 `assigned_to_me`。
- **字段/条件**：脚本内部通过 API 按名称解析。详见 [query_work_item.md](references/query_work_item.md)、[query_my_work_item.md](references/query_my_work_item.md)。
- **--order-by**：排序，格式为「字段名 asc/desc」或「字段1 asc, 字段2 desc」；不传则默认按更新时间倒序。
- **--limit**：返回/导出/参与统计的条数上限；列表类查询默认 20、最大 **10000**（语义超过也按 10000）；导出与统计见各 reference。
- **@me**：条件或字段值中的 `@me` 在脚本内解析为当前用户工号。

### 条件组合与嵌套示例（--conditions）
以下均为 **--conditions** 参数取值示例（传入时整体用双引号包裹）。
| 场景 | --conditions 示例 |
|------|--------------------|
| 工作项类型 + 工作区 | `工作项类型=用户故事 AND 工作区=业务运营` |
| 工作项类型 + 条件 | `工作项类型=任务 AND 状态=进行中` |
| 单条件 | `状态=进行中` |
| 多条件 AND | `状态=进行中 AND 标题 like 测试` |
| 多条件 OR | `状态=进行中 OR 状态=待处理` |
| 括号分组（OR 再 AND） | `(状态=进行中 OR 状态=待处理) AND 优先级=P0` |
| 括号分组（AND 再 OR） | `(状态=进行中 AND 指派给=@me) OR (状态=待处理 AND 优先级=P0)` |
| 多层嵌套 | `(状态=进行中 OR (状态=待处理 AND 优先级=P0)) AND 标题 like 需求` |
| in / like | `状态 in 进行中,待处理`、`标题 like 紧急` |
| 含 @me | `指派给=@me AND 状态!=已完成` |
```bash
# 单条件
python scripts/atomic/query_work_item.py --select-items "标题,状态" --conditions "状态=进行中"
# 工作项类型 + 工作区
python scripts/atomic/query_work_item.py --select-items "标题,状态" --conditions "工作项类型=用户故事 AND 工作区=业务运营"
# 多条件 AND
python scripts/atomic/query_work_item.py --select-items "标识,标题,状态" --conditions "状态=进行中 AND 标题 like 测试"
# 括号组合：(A OR B) AND C
python scripts/atomic/query_work_item.py --select-items "标题,状态,优先级" --conditions "(状态=进行中 OR 状态=待处理) AND 优先级=P0"
# 嵌套：(A AND B) OR (C AND D)
python scripts/atomic/query_work_item.py --select-items "标题,状态" --conditions "(状态=进行中 AND 指派给=@me) OR (状态=待处理 AND 优先级=P0)"
# 排序与条数
python scripts/atomic/query_work_item.py --select-items "标题,状态,更新时间" --conditions "状态=进行中" --order-by "更新时间 desc" --limit 10
```

### 条件字段名边界与禁止行为（必遵）
- **边界**：每个条件形如「[字段名] 操作符 值」。字段名 = 用户句中指称「哪个字段」的**连续短语**（到操作符/值之前），可含英文、数字、中文等；**禁止**因包含英文/数字而只取后半段或真子串作为字段名。
- **禁止**：用用户未说出的更短名称或真子串作为字段名；与脚本内部解析时的候选匹配仅做整段匹配。
## 配置与认证
- **配置文件**：`assets/config.json`。工作区须在 `api.workspace` 中配置，未配置则拒绝执行并终止。通用查询使用该 workspace（禁止 SYSMySpace）；与我相关由脚本内部使用 SYSMySpace。
- **认证**：body 的 `auth` 或环境变量 coclaw_empno/coclaw_token（或 RDCLOUD_*）；缺失则终止。
## 执行命令示例
所有脚本均使用**命令行参数**入参；可用 `-h` / `--help` 查看完整参数。不需要通知时可省略 --is-send-email、--is-send-icenter、--mail-to、--icenter-to。

### 基础示例
```bash
# 创建（任务，带发邮件、发消息）- 字段用名称
python scripts/atomic/create_work_item.py --work-item-type-name 任务 --fields '[{"key":"标题","value":"示例标题"}]' --is-send-email true --is-send-icenter true
# 创建（指定工作区 PLM）
python scripts/atomic/create_work_item.py --work-item-type-name 任务 --fields '[{"key":"标题","value":"示例标题"}]'
# 更新（带发邮件、发消息）- 字段用名称
python scripts/atomic/update_work_item.py --work-item-id PLM-12345 --fields '[{"key":"状态","value":"进行中"}]' --is-send-email true --is-send-icenter true
# 查询通用（查询列+条件，发给我）
python scripts/atomic/query_work_item.py --select-items "标题,状态" --conditions "标题 like 测试" --is-send-email true --is-send-icenter true --mail-to @me --icenter-to @me
# 查询与我相关（指派给我）
python scripts/atomic/query_my_work_item.py --select-items "标题,状态" --query-type assigned_to_me --is-send-icenter true --icenter-to @me
```

### 查询后发送给具体人
用户说「查完发给茆看」「把结果通知张三」时，解析出**具体接收人**（姓名或工号），传入 --mail-to / --icenter-to，无需再确认接收人。
```bash
# 查询进行中的任务，结果发邮件给 茆看、发消息给 茆看
python scripts/atomic/query_work_item.py --select-items "标识,标题,状态" --conditions "状态=进行中" --is-send-email true --is-send-icenter true --mail-to 茆看 --icenter-to 茆看
# 查询与我相关的缺陷，结果发消息给具体人（工号）
python scripts/atomic/query_my_work_item.py --select-items "标题,状态" --query-type assigned_to_me --is-send-icenter true --icenter-to 0668001219
# 仅发邮件给张三（不发消息）
python scripts/atomic/query_work_item.py --select-items "标识,标题" --conditions "优先级=P0" --is-send-email true --mail-to 张三
```

### 多值示例（工作区、工作项类型、接收人）
多值用**英文逗号**分隔，同一参数内不要加空格。
```bash
# 查询通用：邮件/消息发给多人（工号或姓名逗号分隔）
python scripts/atomic/query_work_item.py --select-items "标识,标题,状态" --conditions "状态=进行中" --is-send-email true --is-send-icenter true --mail-to "0668001219,张三" --icenter-to "茆看,李四"
```

### 用户组与消息接收人
消息接收人 **--icenter-to** 支持：个人（工号/姓名/拼音）或**用户组/群组名**；多人或多组用英文逗号分隔。
```bash
# 查询结果发消息给用户组（如 启航团队）
python scripts/atomic/query_work_item.py --select-items "标识,标题" --conditions "状态!=已取消" --is-send-icenter true --icenter-to 启航团队
# 同时发给个人和群组
python scripts/atomic/query_my_work_item.py --select-items "标题,状态" --query-type assigned_to_me --is-send-icenter true --icenter-to "0668001219,启航团队"
```

### 查询列与条件示例
```bash
# 我的缺陷（与我相关）
python scripts/atomic/query_my_work_item.py --select-items "标识,标题,状态" --query-type assigned_to_me
# 我创建的变更请求
python scripts/atomic/query_my_work_item.py --select-items "标题,状态" --query-type created_by_me
# 查产品需求，条件为状态=进行中
python scripts/atomic/query_work_item.py --select-items "标识,标题,优先级" --conditions "状态=进行中"
# 标题带有紧急
python scripts/atomic/query_work_item.py --select-items "标识,标题,状态,创建时间,更新时间" --conditions "标题 like 紧急"
```
AI 按用户表述解析后，用对应**命令行参数**调用脚本；各脚本支持 `--help` 查看完整说明。
## 错误与终止（速查）
- **邮件/消息发送未明确指定**：用户未明确说出「发邮件」「发消息」等时，禁止设置 is_send_email/is_send_icenter 为 true；禁止推断或默认开启发送。
- **查询结果未展示**：查询执行后必须将结果完整展示给用户，禁止以发送为由吞掉或截断结果。
- **心智模型被删除或总结掉**：脚本输出后追加的心智模型（查询示例、提醒等）必须完整保留并展示；禁止在总结/概括时删掉或合并该部分。
- **无创建/更新/查询工作项意图**而单独要求发邮件或发消息 → **禁止执行**；须引导用户在工作项流程中提出发送需求，或先完成查询再要求「将本次结果发给 XXX」。
- **超期查询**意图却使用 query_my_work_item.py 或 query_work_item.py → **禁止执行**；必须使用 query_overdue_work_item.py。
- **与我相关**意图（无超期语义）却使用 query_work_item.py → **禁止执行**；必须使用 query_my_work_item.py。
- **导出**意图（导出、导出为 Excel、导出查询结果等）却使用 query_work_item.py 或 query_my_work_item.py → **禁止执行**；必须使用 export_work_item.py。
- **用户提供了支持的查询链接**或**明确按查询名/视图名查询**（如：根据查询名 WODE）却使用 query_work_item.py 或 query_my_work_item.py → **禁止执行**；必须使用 query_work_item_by_url.py，且按查询名/视图名与按链接**同一脚本、同一处理方式**。
- **分组聚合**（按字段看各组条数、分布，须 **--group-by**）却使用不带 **`aggregate`** 的 stats_work_item.py 或 query_work_item.py → **禁止执行**；必须使用 **`stats_work_item.py aggregate`**。
- **只要总数/求和/平均**（无按字段分组语义）却使用 **`stats_work_item.py aggregate`** → **禁止执行**；应使用不带子命令的 stats_work_item.py，或向用户确认是否需要按某维度分组后再用 **`stats_work_item.py aggregate`**。
- 查询列、条件在用户**未提到**时可不传，脚本用默认列或无条件；不追问、不补全，直接按已有解析执行。
- 工作项类型擅自替换用户表述 → 禁止；须忠实于用户用词。
- 条件中的字段名被截断或用用户未说出的更短名称/真子串替代 → 禁止；须按用户句中指称该字段的完整表述解析。
- 解析多义或需用户选择 → 须用户选择或修正，未明确则终止。
- 配置或认证缺失 → 终止，提示配置路径与示例。
- 不做执行前确认；按当前语义解析直接执行。仅在关键信息缺失时补充最小必要问题。
- 脚本报错或查询为空 → 终止并返回；严禁同一轮中再次执行或换条件重试。
- 查询报错或查询为 0 后，**禁止**为凑结果随意丢弃原查询参数，或无依据放大查询范围后反复查询；**本次查询已结束**，需等待用户明确给出新意图后再继续。
