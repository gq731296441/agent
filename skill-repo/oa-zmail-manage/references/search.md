# 邮件搜索（参考）

本参考描述**搜索邮件**的完整流程：查询参数解析、调用搜索脚本、展示搜索结果。

**范围约定**：搜索/查找邮件**默认范围为收件箱**（`--mailbox-type inbox`），与「我的收件箱」一致；用户说「发件箱」「已发送」时用 `outbox`；「已删除」时用 `deleted`；「垃圾邮件」时用 `spam`；指定文件夹时用 `folder` 并传 `--folder` 名称。

**默认时间**：用户**未给时间/天数**时（如只说「我的收件箱」「查邮件」），脚本使用 **最近 14 天**。

**删除语义说明**：收件箱、草稿箱、发件箱、自定义文件夹中的“删除”或者“移动到已删除（回收站）”须用 **`delete-mails`**，**禁止**用 `move-emails` 代替删除；该删除操作**不是永久删除**，可以通过恢复操作恢复。

## 触发场景

以下情况**会触发搜索**：

- "帮我**找**一下关于**项目**的邮件"
- "**查询**最近三天的邮件"
- "**搜索**最近一个月标题含有**会议**的邮件"
- "**查找****王小明**发的邮件"
- "**搜索**最新的邮件"
- "**查询**邮件"
- "**我的收件箱**"、"**收件箱邮件**"（与查找邮件等价，默认收件箱）
- "**发件箱**里关于项目的邮件"、"**已发送**的会议邮件"
- "**工作文件夹**中王小明发的邮件"、"在 **xxx 文件夹** 里搜索"
- "搜索**有附件**的邮件"、"**外部邮件**"

## 执行搜索

当 intent 为 **搜索邮件** 时，在技能目录下运行统一入口的 `search-emails` 子命令：

```bash
python3 {baseDir}/scripts/main.py search-emails [关键字] [--mailbox-type inbox|outbox|deleted|spam|folder] [--start-date "YYYY-MM-DD"|"YYYY-MM-DD HH:MM:SS"] [--end-date "YYYY-MM-DD"|"YYYY-MM-DD HH:MM:SS"] [--days N] [--sender NAME] [--recipients NAME] [--cc NAME] [--content TEXT] [--attachment-name NAME] [--mail-attribute attachment|external] [--folder FOLDER_NAME] [--categories unread,starred,urgent,to_me]
```

- **关键字**（可选）：位置参数，搜索词。仅时间/发件人筛选时可省略。
- **--mailbox-type**（可选）：inbox（收件箱，默认）/ outbox（发件箱）/ deleted（已删除）/ spam（垃圾邮件）/ folder（文件夹）。默认 inbox。
- **--start-date**、**--end-date**（可选）：YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS 格式，指定日期范围。仅传日期时，起始默认 00:00:00、结束默认 23:59:59。**当传了 start/end 时，按时间范围搜索优先，--days 将被忽略**。
- **--days N**（可选）：最近 N 天内。**未指定则默认 14 天**。**仅当未传 start/end 时才生效**。
- **--sender NAME**（可选）：按发件人姓名筛选。
- **--recipients NAME**（可选）：按收件人筛选。
- **--cc NAME**（可选）：按抄送人筛选。
- **--content TEXT**（可选）：按正文内容筛选。
- **--attachment-name NAME**（可选）：按附件名称筛选。
- **--mail-attribute**（可选）：attachment（附件邮件）/ external（外部邮件），空为全部。
- **--folder**（可选）：文件夹名称或文件夹 ID（纯数字）。**仅当 --mailbox-type folder 时有效**。传名称时按名称匹配；**用户从 folder_data 选择时传 id**。
- **--categories**（可选）：逗号分隔：`unread`（未读）、`starred`（标星）、`urgent`（紧急）、`to_me`（主送我）。支持组合（如 `unread,to_me`）。未指定则不过滤分类。
- **{baseDir}**：当前 skill 目录的绝对路径。

示例：

```bash
python3 {baseDir}/scripts/main.py search-emails "健康管理"
python3 {baseDir}/scripts/main.py search-emails "会议" --start-date "2024-01-01" --end-date "2024-01-31"
python3 {baseDir}/scripts/main.py search-emails "会议" --start-date "2024-01-01 09:00:00" --end-date "2024-01-01 18:30:59"
python3 {baseDir}/scripts/main.py search-emails --days 3
python3 {baseDir}/scripts/main.py search-emails "会议" --days 30 --sender "王小明"
python3 {baseDir}/scripts/main.py search-emails --sender "王小明"
python3 {baseDir}/scripts/main.py search-emails --mailbox-type outbox "项目"
python3 {baseDir}/scripts/main.py search-emails --mailbox-type folder --folder "工作" --sender "王小明"
python3 {baseDir}/scripts/main.py search-emails --mail-attribute attachment
```

## 查询参数解析

### 关键字解析（可选）

从用户输入中提取搜索关键词，作为脚本的第一个位置参数（`keyword`）。**若用户未明确说「按内容搜索」「正文包含」等，默认使用关键字**（即 `keyword`，搜索标题等）；明确要求按正文搜索时再传 `--content`。未提则省略或传空。

### 发件人/收件人/抄送人/内容/附件名称解析（可选）

- **发件人**：从「发件人 xxx」「来自 xxx」「xxx 发的」等提取，作为 `--sender`。
- **收件人**：从「收件人 xxx」「发给 xxx」等提取，作为 `--recipients`。
- **抄送人**：从「抄送 xxx」「cc xxx」等提取，作为 `--cc`。
- **内容**：从「正文包含 xxx」「内容里有 xxx」等提取，作为 `--content`。
- **附件名称**：从「附件名 xxx」「附件叫 xxx」等提取，作为 `--attachment-name`。

未指定时不传。**角色不明确时的默认规则**：若能分析出人名，但**不明确**是收件人、发件人还是抄送人，**默认按发件人**处理，传 `--sender`。

### 箱体类型与文件夹解析（可选）

- **收件箱**（默认）：`--mailbox-type inbox`，不传则默认。
- **发件箱**：从「发件箱」「已发送」「我发出的」等识别，传 `--mailbox-type outbox`。
- **已删除**：从「已删除」「垃圾箱」等识别，传 `--mailbox-type deleted`。
- **垃圾邮件**：从「垃圾邮件」「垃圾箱」等识别，传 `--mailbox-type spam`。
- **文件夹**：从「某文件夹」「在 xxx 文件夹」等识别，传 `--mailbox-type folder` 和 `--folder`。`--folder` 可为**文件夹名称**（按名称匹配）或**文件夹 ID**（纯数字）。**当用户从 folder_data 列表中选择某一项时**（如「选择第 1 个」「选收件箱」），应传该文件夹的 **id**，而非名称。

### 邮件属性解析（可选）

- **附件邮件**：从「有附件的」「带附件的」等识别，传 `--mail-attribute attachment`。
- **外部邮件**：从「外部邮件」「外部发来的」等识别，传 `--mail-attribute external`。
- **全部**（默认）：不传 `--mail-attribute`。

### 时间范围解析（可选）

- 若用户**明确提到“天数/最近 N 天/按天数”**应按天数解析，优先生成 `--days N`。
- 若用户给到**小时/分钟**粒度（如「最近 2 小时」「过去 30 分钟」「今天 10:00 到 12:30」），应解析出具体的起止时间并使用 `--start-date` / `--end-date` 传参（而不是再换算为 `--days`），以避免时间精度丢失。

### 天数解析（可选）

将自然语言转为 `--days N`：

| 用户输入                      | 天数 |
| ----------------------------- | ---- |
| 最新 / 今天 / 当天            | 1    |
| 最近（未说具体天数）           | 3    |
| 最近三天 / 近三天              | 3    |
| 最近一周 / 本周                | 7    |
| 最近两周 / 近两周              | 14   |
| 最近一个月 / 本月              | 30   |
| 最近三个月                     | 90   |
| 最近一年                       | 365  |

未指定时**不传** `--days`，由脚本使用默认（14 天）。

**复杂日期表述**：当用户使用「本周」「本月」「下周」「上周」等相对时间时，按 **[time.md](time.md)** 解析后，换算为 `--days N`。

### 完整查询示例

| 用户输入                                     | 解析结果 |
| -------------------------------------------- | -------- |
| 查找今天 10:00 到 12:30 的邮件               | --start-date "YYYY-MM-DD 10:00:00", --end-date "YYYY-MM-DD 12:30:59" |
| 查找最近 24 小时的邮件                       | --start-date "YYYY-MM-DD HH:MM:SS", --end-date "YYYY-MM-DD HH:MM:SS" |
| 查询最近三天的邮件                           | --days 3 |
| 搜索标题含有健康管理的邮件                   | keyword=健康管理 |
| 查找王小明发的邮件                           | --sender "王小明" |
| 搜索最近一个月标题含有会议的王小明发的邮件   | keyword=会议, --days 30, --sender "王小明" |
| 搜索最新的邮件                               | --days 1 |
| 搜索发件箱里关于项目的邮件                   | --mailbox-type outbox, keyword=项目 |
| 搜索工作文件夹中王小明发的邮件               | --mailbox-type folder, --folder "工作", --sender "王小明" |
| 搜索有附件的邮件                             | --mail-attribute attachment |

### 出参（stdout JSON）

#### 成功

| 字段 | 类型 | 说明 |
|------|------|------|
| emails | array | 邮件列表 |
| count | number | 邮件数量 |

`emails` 每项：

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 邮件 ID |
| title | string | 主题 |
| date | string | 日期 |
| is_external | boolean | 可选，仅外部邮件时存在（SID=0 且 IOM≠0） |
| has_attachments | boolean | 可选，仅有附件时存在（IA=1） |
| is_rollback | boolean | 可选，仅发件箱且已撤回时存在（IRC=1） |

stdout 示例：

```json
{
  "emails": [
    {"id": "123", "title": "项目进度更新", "date": "2024-01-15", "is_external": false, "has_attachments": true},
    {"id": "456", "title": "会议邀请", "date": "2024-01-14", "is_external": true, "has_attachments": false},
    {"id": "789", "title": "项目方案", "date": "2024-01-13", "is_rollback": true}
  ],
  "count": 3
}
```

#### 失败

退出码非 0，stderr：`{"error": "具体错误信息"}`，须将 **error** 原文展示给用户。参见 [scripts_and_errors.md](scripts_and_errors.md)。

#### 文件夹不存在

当 `--mailbox-type folder` 且指定文件夹不存在时，stdout 返回：

```json
{"error": "文件夹「xxx」不存在", "emails": [], "count": 0, "folder_data": {"folders": [...], "count": N}}
```

须展示 error 原文，并附带当前可用文件夹列表供用户参考。**用户选择文件夹时，应传 folder_data 中对应项的 id**。

- **folder_data**：与 [folder.md](folder.md) 中 folder-list 成功返回格式一致，为 `{"folders": [{"id": "文件夹ID", "folder": "完整路径"}, ...], "count": N}`。**展示文件夹时须按 [folder.md](folder.md) 的展示规范**：count 为 0 时提示「暂无文件夹」；count > 0 时以表格展示 `序号` 与 `文件夹路径`，便于用户选择正确名称或修正输入。

#### 超过半年

当使用 `--start-date` / `--end-date` 且结束日期早于「当前时间往前推 6 个月」时，脚本**不发起搜索**，stdout 示例：

```json
{"beyond_half_year": true, "emails": [], "count": 0}
```

**脚本只返回该标志，不返回提示文案**。须据此向用户说明：「**半年前的邮件请使用历史邮件查询**」。

**重要**：在上下文中存储搜索结果，记住列表序号与邮件 ID 的映射关系（序号从 1 开始）。

### 未传时间时的默认行为

- 用户**未给时间/天数**（如只说「我的收件箱」「查邮件」）时：脚本默认查 **最近 14 天** 的邮件。
- 脚本会返回该时间范围内的**全部命中邮件**（内部自动分页），按返回的 `emails` 与 `count` 展示即可，无需关心分页参数。

## 展示搜索结果

以 Markdown 表格形式展示，**仅向用户展示最终结果，不输出中间过程**（不输出所执行的命令、工具调用状态）。

**使用默认参数时的提示（必须遵守）**：当本次请求中**未指定**时间/天数/起止日期，且脚本使用了默认值时，**必须**在结果表格**之前**明确告诉用户本次使用的默认值。仅在实际使用了默认时间时才提示；若用户已明确指定（如「最近三天」「本周」），则不再提示。表述示例：「本次未指定时间范围，使用默认：最近 14 天。」（须写出具体默认值「最近 14 天」，不得省略）。

### 输出格式模板

收件箱示例：

```
找到 3 封邮件：

| 序号 | 标题 | 邮件类型 | 附件 | 时间 |
| ---- | ---- | -------- | ---- | ---- |
| 1    | **项目进度更新** | | 有 | 2024-01-15 |
| 2    | **项目会议记录** | 外部邮件 | | 2024-01-14 |
| 3    | **项目需求文档** | | | 2024-01-13 |

---

关联操作：
输入 "查看第N条" 或邮件标题来查看详情（邮件查看）
总结第 N 条/这几封邮件，要求：xxxxx（邮件总结）
将第 N 条或筛选结果批量标记为已读/标星（邮件标记）
删除第 N 条或按条件批量删除（邮件删除）
```

发件箱示例（含已撤回列）：

```
找到 2 封邮件：

| 序号 | 标题 | 状态 | 时间 |
| ---- | ---- | ---- | ---- |
| 1    | **项目方案** | 已撤回 | 2024-01-15 |
| 2    | **会议通知** | | 2024-01-14 |

---

关联操作：
输入 "查看第N条" 或邮件标题来查看详情（邮件查看）
总结第 N 条/这几封邮件，要求：xxxxx（邮件总结）
将第 N 条或筛选结果批量标记为已读/标星（邮件标记）
删除第 N 条或按条件批量删除（邮件删除）
```

**展示规则**：
- **邮件类型**：仅当返回数据中包含 `is_external` 字段时展示该列；列内仅当 `is_external` 为 true 时展示「外部邮件」，否则为空。
- **附件**：仅当返回数据中包含 `has_attachments` 字段时展示该列；列内仅当 `has_attachments` 为 true 时展示「是」，否则为空。
- **已撤回**：仅当搜索发件箱（`--mailbox-type outbox`）时，返回数据中可能包含 `is_rollback` 字段；列内仅当 `is_rollback` 为 true 时展示「已撤回」，否则为空。
- 若返回数据中**无** `is_external`、`has_attachments` 或 `is_rollback` 字段，则**不展示**对应列。

搜索成功但 **count=0** 时：提示「没有找到与'{keyword}'相关的邮件，请尝试其他关键词。」

## 示例（搜索）

```
用户：查找王小明最近一周发的邮件

AI：找到 1 封邮件：

| 序号 | 标题 | 邮件类型 | 附件 | 时间 |
| ---- | ---- | -------- | ---- | ---- |
| 1    | **周报提交提醒** | | | 2024-01-18 |

---

关联操作：
输入「查看第 N 条」或邮件标题来查看详情（邮件查看）
总结第 N 条/这几封邮件，要求：xxxxx（邮件总结）
将第 N 条或筛选结果批量标记为已读/标星（邮件标记）
删除第 N 条或按条件批量删除（邮件删除）
```

```
用户：搜索不存在的关键词

AI：没有找到与「不存在的关键词」相关的邮件，请尝试其他关键词。
```

```
用户：帮我找一下关于项目的邮件

AI：本次未指定时间范围，使用默认：最近 14 天。

找到 3 封邮件：

| 序号 | 标题 | 邮件类型 | 附件 | 时间 |
| ---- | ---- | -------- | ---- | ---- |
| 1    | **项目进度更新** | | 有 | 2024-01-15 |
| 2    | **项目会议记录** | 外部邮件 | | 2024-01-14 |
| 3    | **项目需求文档** | | | 2024-01-13 |

---

关联操作：
输入「查看第 N 条」或邮件标题来查看详情（邮件查看）
总结第 N 条/这几封邮件，要求：xxxxx（邮件总结）
将第 N 条或筛选结果批量标记为已读/标星（邮件标记）
删除第 N 条或按条件批量删除（邮件删除）
```
