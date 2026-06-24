# 发件箱

当用户需要**撤回已发送邮件**时使用此技能。入口为 **`main.py rollback-sent`**。

**发件箱列表/搜索**：使用 [search.md](search.md) 中的 `search-emails --mailbox-type outbox`。
**删除发件箱邮件**：不走本技能；走 [folder.md](folder.md) 的 **`delete-mails`**。
**删除语义说明**：收件箱、草稿箱、发件箱、自定义文件夹中的“删除”或者“移动到已删除（回收站）”须用 **`delete-mails`**，**禁止**用 `move-emails` 代替删除；该删除操作**不是永久删除**，可以通过恢复操作恢复。

## 触发条件

用户查询中包含**撤回**相关表述时触发：

| 表述类型 | 匹配词汇 |
| -------- | -------- |
| **撤回** | 撤回邮件、撤回第 N 封、撤回已发送、把邮件撤回 |

## 触发场景

- "**撤回第 N 封（发件箱）**"（如「撤回第 2 封」「把发件箱里的第 3 封撤回」）
- "**撤回这封发件箱邮件**"（在已经通过发件箱列表或查看确定了具体邮件的场景）

以下**不触发**本技能（走其他子技能）：

- "查看发件箱" / "发件箱邮件" / "已发送的邮件" → 使用 [search.md](search.md)，`search-emails --mailbox-type outbox`
- "总结发件箱" → 使用 [summary.md](summary.md)，`--mailbox-type outbox`
- "删除发件箱邮件" / "把发件箱第 N 封删掉" → 使用 [folder.md](folder.md)，`delete-mails`

## 工作流程

1. **解析意图**：确认为「撤回已发送邮件」（包括类似「撤回第 3 封」「把刚才列表里的第 2 封撤回」这类表述）。
2. **确认邮件 ID**：
   - 当用户说「撤回第 N 封」时：从**最近一次发件箱搜索结果**（`search-emails --mailbox-type outbox`）缓存中取第 N 条的 `id`。
   - 当用户直接给出邮件 ID 时（如「撤回邮件 0001...」）：直接使用该 ID。
3. **执行撤回**：调用 `main.py rollback-sent --email-id "<邮件ID>"`。

## 脚本说明

### 撤回已发送邮件：`main.py rollback-sent`

```bash
python3 {baseDir}/scripts/main.py rollback-sent --email-id "<邮件ID>"
```

返回值为规范化结构：

```json
{"status": true}
```

或失败时：

```json
{"status": false, "error": "撤回失败原因"}
```

- `status: true` 表示撤回成功；
- `status: false` 时，**必须展示 `error` 字段中的错误信息**（例如权限不足、超过可撤回时间窗口等），提示用户撤回未成功。

## 错误处理

- 脚本非 0 退出或返回含 `error`：展示错误信息，建议重试或登录 Web 邮箱。

## 与其它子技能关系

- **发件箱列表/搜索**：[search.md](search.md)，使用 `search-emails --mailbox-type outbox`。
- **总结发件箱**：[summary.md](summary.md)，使用 `summary --mailbox-type outbox`。
- **撤回已发送邮件**：本技能，使用 `main.py rollback-sent`。
- **删除发件箱邮件**：使用 [folder.md](folder.md) 的 **`delete-mails`**（并遵循“已删除邮件不支持再次删除”约束）。
