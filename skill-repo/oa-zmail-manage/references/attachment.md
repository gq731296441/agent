# 附件（参考）

本参考描述**下载邮件附件到指定目录**与**将附件转存到云盘**的完整流程。

---

## 一、附件下载

### 触发场景

以下情况**会触发附件下载**（下载到本地）：

- "**下载**附件"
- "**保存**附件到本地"
- "把附件**下载**到 xxx 目录"
- "**导出**这封邮件的附件"
- "下载第 N 封邮件的附件"
- "**不下载邮件正文中的图片**"、"**只要附件不要正文里的图**"、"下载附件但不要正文里的图" 等：从用户视角即**插在正文里的图片**（与「附件列表里的文件」区分）；用户表达不下载正文中的图片时，需传入 `--no-download-inline`。

### 确定邮件 ID 与保存路径

- **邮件 ID**：用户说「第 N 条」/「第 N 封」时，使用**搜索结果**或**查看上下文**中对应邮件的 ID；用户提供标题时，在搜索结果中查找匹配的邮件并使用其 ID。
- **保存路径**：用户指定的本地目录路径；若未指定，可询问用户或使用默认路径（如当前工作目录）。
- **附件 ID（可选）**：若用户指定下载部分附件（如「只下载第一个」），可从 view 返回的 `attachments` 中取对应 `id`，逗号分隔传入 `--attach-ids`；不传则下载所有附件。
- **是否下载正文中的图片（可选）**：从用户视角看，即**插在正文里的图片**。若用户说「不下载邮件正文中的图片」「只要附件不要正文里的图」等，则传入 `--no-download-inline`；不传则默认下载所有附件（含正文中的图片）。

### 调用脚本

在技能目录下执行：

```bash
python3 {baseDir}/scripts/main.py download-attach --email-id "<邮件ID>" --save-path "<保存目录>" [--attach-ids "id1,id2,..."] [--no-download-inline]
```

- **{baseDir}**：当前 skill 目录的绝对路径。
- **--email-id**：必填，邮件 ID。
- **--save-path**：必填，保存目录路径（不存在则自动创建）。
- **--attach-ids**：可选，附件 ID 逗号分隔；不传则下载该邮件所有附件（含正文中的图片）。
- **--no-download-inline**：可选，添加时跳过邮件正文中的图片；不传则默认下载所有附件（含正文中的图片）。**用户说「不下载正文中的图片」时使用此参数。**（内部对应 `download_inline` 参数，默认 True。）

**成功**：stdout 返回 JSON，格式为 `{"status": true, "data": [...]}`。`data` 为附件列表，每项含 `status`（true/false）、`id`、`msg`。成功项 `msg` 为「{name}成功下载」，失败项 `msg` 为「{name}下载失败，原因是{xxx}」。

**失败**：退出码非 0，stderr 为 `{"status": false, "error": "具体错误信息"}`，须将 **error** 原文展示给用户。参见 [scripts_and_errors.md](scripts_and_errors.md)。

### 限制说明

- **外部邮件**：不支持下载附件，返回 `{"status": false, "error": "外部邮件不支持下载附件"}`。
- **正文中的图片**：从用户视角即插在正文里显示的图片。默认会下载；加 `--no-download-inline` 时跳过；若用户在「只下载第 N 个」等场景中明确指定了某正文图片的 ID（通过 `--attach-ids`），则仍会下载。

### 示例（附件下载）

```
用户：把第 2 封邮件的附件下载到 /tmp/attachments

AI：已下载 2 个附件到 /tmp/attachments：

| 附件名 | 保存路径 |
| ------ | -------- |
| 会议资料.pdf | /tmp/attachments/会议资料.pdf |
| 议程.docx | /tmp/attachments/议程.docx |
```

```
用户：下载第 1 封邮件的第一个附件到桌面

AI：已下载 1 个附件：

| 附件名 | 保存路径 |
| ------ | -------- |
| 项目进度.xlsx | /home/xxx/Desktop/项目进度.xlsx |
```

```
用户：把第 3 封邮件的附件下载到 /tmp

AI：该邮件没有附件。
```

```
用户：下载第 2 封邮件的附件到 /tmp，不要正文里的图片

AI：已下载 2 个附件到 /tmp（已跳过邮件正文中的图片）：

| 附件名 | 保存路径 |
| ------ | -------- |
| 会议资料.pdf | /tmp/会议资料.pdf |
| 议程.docx | /tmp/议程.docx |
```

### 展示规范（附件下载）

- **data 为空**：提示「没有附件」或「该邮件无可下载的附件」。
- **data 非空**：可逐条展示 `msg`；成功项（status 为 true）展示成功提示，失败项（status 为 false）展示失败原因。
- **失败时**：原文展示脚本返回的 `error`。

---

## 二、附件转存到云盘

将指定邮件的附件转存到云盘。不传 `--attach-ids` 时转存该邮件全部附件；传则仅转存指定附件。

### 触发场景

- "**把附件转存**到云盘"
- "**附件存到**网盘"
- "把这封邮件的附件**转存**"

### 确定邮件 ID 与附件 ID

- **邮件 ID**：用户说「第 N 条」/「第 N 封」时，使用**搜索结果**或**查看上下文**中对应邮件的 ID。
- **附件 ID（可选）**：若用户指定转存部分附件，可从 view 返回的 `attachments` 中取对应 `id`，逗号分隔传入 `--attach-ids`；不传则转存所有附件。

### 调用脚本

```bash
python3 {baseDir}/scripts/main.py transfer-attach --email-id "<邮件ID>" [--attach-ids "id1,id2,..."] [--no-transfer-inline]
```

- **{baseDir}**：当前 skill 目录的绝对路径。
- **--email-id**：必填，邮件 ID。
- **--attach-ids**：可选，附件 ID 逗号分隔；不传则转存该邮件所有附件。
- **--no-transfer-inline**：不转存邮件正文中的图片；不传则默认转存所有附件（含正文中的图片）。

### 成功与失败

**成功**：返回 `{"status": true, "message": "附件转存操作已完成。", "transferred": [{"id": "附件ID", "name": "附件名"}, ...]}`。

**失败**：返回 `{"status": false, "message": "错误信息", "transferred": []}`。

**部分失败**：`status` 仍为 true，`transferred` 中失败项含 `error` 字段（如 `{"id": "xxx", "name": "xxx", "error": "接口返回原因"}`）。

### 示例（附件转存）

```
用户：把第 2 封邮件的附件转存到云盘

AI：附件已转存到[我的云盘](https://i.zte.com.cn/index/ztea_icenter_document)-我的应用-来自邮箱
```

```
用户：把这封邮件的第一个附件存到网盘

AI：附件已转存到[我的云盘](https://i.zte.com.cn/index/ztea_icenter_document)-我的应用-来自邮箱
```

### 展示规范（附件转存）

- **成功**：提示“附件已转存到[我的云盘](https://i.zte.com.cn/index/ztea_icenter_document)-我的应用-来自邮箱”。
- **失败**：原文展示 `message`。
- **部分失败**：可展示 `message`，并列出 `transferred` 中失败项的 `error`。
