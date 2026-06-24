# 邮件标记（参考）

本参考描述**标记邮件已读/未读/标星/取消标星**的完整流程。

---

## 触发场景

以下情况**会触发邮件标记**：

- "**标为已读**"、"把这封邮件**设为已读**"
- "**标为未读**"、"这封邮件**还没读**，标回去"
- "**标星**"、"把这封邮件**收藏**一下"、"**加星标**"
- "**取消标星**"、"把**星标去掉**"
- "把第 N 封邮件标为已读"
- "把这几封邮件都标为未读"
- "**把未读里关于 XX 的都标为已读**"（无具体 ID 时，用与搜索相同的条件先搜再标）

## 确定邮件 ID 与标记状态

- **邮件 ID**：
  - **方式一（优先）**：用户已指明 ID，或能从**搜索结果/上下文**取到 ID 时，直接使用 `--email-ids`；**一旦传入 `--email-ids`，不再使用任何搜索条件**。
  - **方式二**：用户只说条件（主题、发件人、时间、未读等）而未给 ID 时，**不传 `--email-ids`**，使用与 `search-emails` **相同的参数**先搜索，再对命中邮件逐封标记。
- **标记状态**：根据用户意图映射为以下值：
  - `read` — 已读
  - `unread` — 未读
  - `starred` — 标星（收藏）
  - `unstarred` — 取消标星（取消收藏）

## 调用脚本

### 按邮件 ID 标记（优先）

```bash
python3 {baseDir}/scripts/main.py mark-email [--email-ids "<邮件ID1,邮件ID2,...>"] [--status read|unread|starred|unstarred]
```

- **{baseDir}**：当前 skill 目录的绝对路径。
- **`--email-ids`**：可选；**传入非空时必填语义为「只标这些 ID」**，与搜索相关的位置参数 `keyword` 及 `--mailbox-type`、`--days` 等**均被忽略**。
- **`--status`**：可选，标记状态，默认 `read`。取值：`read`（已读）、`unread`（未读）、`starred`（标星）、`unstarred`（取消标星）。

### 按搜索条件先搜再标（未传 `--email-ids` 或传空）

参数与 **`search-emails` 一致**：位置参数 `keyword`（可省略）、`--mailbox-type`、`--days`、`--start-date`、`--end-date`、`--sender`、`--recipients`、`--cc`、`--content`、`--attachment-name`、`--mail-attribute`、`--folder`、`--categories` 等。

```bash
python3 {baseDir}/scripts/main.py mark-email [关键字] [--mailbox-type inbox|outbox|deleted|spam|folder] [--days N] [--start-date "YYYY-MM-DD"|"YYYY-MM-DD HH:MM:SS"] [--end-date "YYYY-MM-DD"|"YYYY-MM-DD HH:MM:SS"] [--sender NAME] [--recipients NAME] [--cc NAME] [--content TEXT] [--attachment-name NAME] [--mail-attribute attachment|external] [--folder FOLDER_NAME] [--categories unread,starred,urgent,to_me] [--status read|unread|starred|unstarred]
```

- 未传 `days` 时默认与搜索一致（通常为最近 14 天）。
- 无命中邮件时返回错误信息（如「未找到符合条件的邮件」）；文件夹模式超出半年范围时与搜索行为一致。

**成功（按 ID）**：stdout 返回 JSON：

```json
{
  "status": true,
  "results": [
    {"id": "邮件ID", "status": true}
  ]
}
```

**成功（先搜后标）**：除 `results` 外可能包含：

```json
{
  "status": true,
  "search_used": true,
  "matched_count": 3,
  "results": [
    {"id": "邮件ID", "status": true}
  ]
}
```

**部分失败**：`status` 为 false，`results` 中失败项含 `error`：

```json
{
  "status": false,
  "results": [
    {"id": "邮件ID1", "status": true},
    {"id": "邮件ID2", "status": false, "error": "具体错误信息"}
  ]
}
```

**历史邮件**：历史邮件不支持标记操作，返回：

```json
{"status": false, "error": "历史邮件不支持标记操作", "history_ids": ["ev-xxx..."]}
```

## 限制说明

- **历史邮件**：ID 以 `ev-` 或 `mas-` 开头的历史邮件不支持标记操作，会直接返回错误。
- **标记操作不可撤回**：标记为已读后如需恢复，需再次调用标记为未读。
- **批量操作**：支持多个邮件 ID 同时操作，逐封执行，部分失败不影响其他邮件。
- **按条件标记**：命中多少封则尝试标记多少封；请注意条件范围，避免误标大量邮件。

## 示例

### 标为已读（指定 ID）

用户："把第一封邮件标为已读"

```bash
python3 {baseDir}/scripts/main.py mark-email --email-ids "00010002bfab9bd1350c53c9"
```

### 标为未读

用户："这封邮件标为未读"

```bash
python3 {baseDir}/scripts/main.py mark-email --email-ids "00010002bfab9bd1350c53c9" --status unread
```

### 标星

用户："把这封邮件收藏一下"

```bash
python3 {baseDir}/scripts/main.py mark-email --email-ids "00010002bfab9bd1350c53c9" --status starred
```

### 批量取消标星

用户："把这三封邮件的星标去掉"

```bash
python3 {baseDir}/scripts/main.py mark-email --email-ids "id1,id2,id3" --status unstarred
```

### 按搜索条件：将最近 7 天标题含「周报」的未读邮件标为已读

用户："把最近一周标题里有周报的未读都标已读"

```bash
python3 {baseDir}/scripts/main.py mark-email "周报" --days 7 --categories unread --status read
```

### 按搜索条件：将某发件人今天的邮件标为已读（未给 ID）

用户："把张三今天发的邮件都标已读"

```bash
python3 {baseDir}/scripts/main.py mark-email --sender "张三" --start-date "$(date +%F)" --end-date "$(date +%F)" --status read
```

（若环境不支持 `$(date +%F)`，请写死当日 `YYYY-MM-DD`。）

### 优先级说明：同时写了 ID 与搜索条件

用户既传了 `--email-ids` 又写了关键字或 `--sender` 时，**仅以 `--email-ids` 为准**，搜索条件不会生效。

```bash
python3 {baseDir}/scripts/main.py mark-email "会被忽略的关键字" --email-ids "00010002bfab9bd1350c53c9" --status read
```
