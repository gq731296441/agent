# 根据查询链接查询工作项 · 脚本入参说明


> **工具约束（必遵）**：须通过本技能已有 `scripts/` 脚本执行研发云工作项能力；**禁止**使用 write、edit、web-search、web_fetch 等处理本业务或替代脚本；**禁止**新建 Python 脚本。

对应脚本：`scripts/atomic/query_work_item_by_url.py`。

执行规则：不做执行前确认；脚本执行完成后，先展示「本次语义解析结果」（链接/查询名、工作区等），再展示查询结果。
结果展示约束：默认不得总结、省略或改写脚本返回结果；仅当用户语义明确要求“总结/汇总/精简”时，才可在完整结果后追加总结。

当用户**意图为查询**且满足以下任一情况时，应识别为「根据 URL / 查询名/视图名 查询」场景，**必须**使用本脚本，禁止走通用查询或与我相关查询：

- **提供了研发云工作项查询链接**：用户粘贴或提供的 URL 匹配本脚本支持的多种模式（见下方「支持的 URL 模式」），不限于某两类。
- **明确按查询名/视图名查询**：用户表述为「根据查询名 XXX」「根据视图名 XXX」「根据查询名称/视图名称/查询视图 XXX」等，由脚本按名称解析视图后按链接方式拉取结果。

上述任一匹配即走本脚本；不传查询列、条件等，由链接或名称唯一确定查询范围。

## 命令行调用

- **命令形式**：`python scripts/atomic/query_work_item_by_url.py --query-url "查询链接或查询名/视图名" [--user-input "用户原话"]`
- **查看完整参数**：`python scripts/atomic/query_work_item_by_url.py --help`

## 参数说明

| 参数（命令行写法） | 必填 | 说明 |
|--------------------|------|------|
| **--query-url, --queryUrl** | 视场景 | 查询链接（匹配支持的 URL 模式），或直接传查询名/视图名（如 `WODE`）；按名称查询时可与 --user-input 配合 |
| **--user-input, --userInput** | 建议必传 | 用户原始输入；按「根据查询名/视图名」查询时用于解析名称，建议始终传入 |

**说明**：本脚本**无** `--limit` 参数，返回条数以接口与视图配置为准。若需按条数控制的列表查询，请使用通用查询/与我相关脚本，并遵守 SKILL 中 limit 规则（默认 20、上限 10000）。

## 支持的 URL 模式与按名称查询

脚本支持多种研发云查询 URL 模式（由 `parse_query_url` 统一解析），**不限定为某两类**，例如：

| 类型 | 说明 / 示例 |
|------|-----------------------------|
| iChange 工作区查询 | 路径型 `.../workspaces/{工作区}/apps/ichange/workspacequery/{查询ID}`；参数型 `.../workspacequery?wicViewId={24位视图ID}` |
| WIM 视图查询 | 参数型 `.../apps/wim/queries?wicViewId={视图ID}`；路径型 `.../apps/wim/queries/{数字ID}` |
| subject 视图 | `.../ireq/subject/?viewId={视图ID}`、`.../iecApp/subject/?viewId={视图ID}` 等 |

**按查询名/视图名查询**：用户说「根据查询名 WODE」「根据视图名 我的查询」等时，传 --user-input（或 --query-url 为名称），脚本先调用 getQueryByKeyword 解析出视图 id，再按链接方式拉取结果。与按 URL 使用同一脚本、同一处理流程。

## 示例

```bash
# 用户提供查询链接（支持多种 URL 模式，见上文）
python scripts/atomic/query_work_item_by_url.py --query-url "https://rdcloud.zte.com.cn/uportal/rd-uportal/workspaces/PLM/apps/ichange/workspacequery/5198925" --user-input "查一下这个链接里的工作项"
python scripts/atomic/query_work_item_by_url.py --query-url "https://rdcloud.zte.com.cn/uportal/rd-uportal/workspaces/PLM/apps/wim/queries?wicViewId=69b0171c5bc5ec18e1ae6205"

# 按查询名/视图名查询（与按链接同一脚本、同一处理方式）
python scripts/atomic/query_work_item_by_url.py --user-input "根据查询名 WODE 查询"
python scripts/atomic/query_work_item_by_url.py --query-url "WODE" --user-input "根据查询名 WODE 查询"
```

## 结果与心智提示

- 查询结果必须完整展示给用户；脚本输出后可能追加心智提示（如确认链接与权限），须完整保留并展示。
- 脚本根据解析结果调用对应 API（URL 模式或按名称解析出的视图），返回该查询下的工作项列表。

## 与其它查询脚本的区别

| 场景 | 脚本 | 说明 |
|------|------|------|
| 根据查询链接查询 / 按查询名/视图名查询 | query_work_item_by_url.py | 用户提供支持的查询 URL 或明确按查询名/视图名查询，由链接或名称唯一确定查询；**不限定为某两类 URL** |
| 查询（通用/按条件） | query_work_item.py | 按查询列、条件等字面组合查询 |
| 与我相关 | query_my_work_item.py | 指派给我、我创建的等 |

意图识别时：**先判断是否带支持的查询 URL 或是否明确按查询名/视图名查询**；若匹配则用本脚本，否则再区分「与我相关」与「通用查询」。
