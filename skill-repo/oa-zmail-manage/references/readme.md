# reference 索引

按 SKILL.md 里判定的意图**只打开一个入口文档**；该文档内再跟链接跳转，不要一次加载全部文件。

## 入口文档（与 SKILL.md 子技能对应）

| 子技能 | 入口文档 | 说明 |
|--------|----------|------|
| **发件箱** | [outbox.md](outbox.md) | 发件箱列表用 search-emails --mailbox-type outbox；撤回用 rollback-sent |
| **总结** | [summary.md](summary.md) | 总结/汇总收件箱或发件箱，main.py summary |
| **草稿箱（可独立触发）** | [draft_box.md](draft_box.md) | 查看草稿箱、草稿设时间/取消/立即发/删除（含示例） |
| **新建邮件（可独立触发）** | [send.md](send.md) | 给 xxx 发邮件（提到具体发送时间则为预约发送）；收件人解析见 [search-users.md](search-users.md)，展示确认（含示例） |
| **收件人解析（search-users）** | [search-users.md](search-users.md) | 收件人/抄送人/密送人/群组搜索，供 send/create-draft/forward/reply 使用 |
| **邮件标记** | [mark.md](mark.md) | 已读/未读/标星/取消标星；`mark-email`。仅「列出/筛选」未读或标星走 [search.md](search.md)（`--categories`），见 [workflow.md](workflow.md) |
| **搜索/查看/回复/附件下载/文件夹/转发（可分别触发）** | （无单独入口） | 收件箱筛选（未读/紧急/标星/主送）用 search-emails --categories；按意图分别使用 [search.md](search.md)、[view.md](view.md)、[reply.md](reply.md)、[attachment.md](attachment.md)、[folder.md](folder.md)、[forward.md](forward.md)；错误处理统一见 [scripts_and_errors.md](scripts_and_errors.md) |

### 搜索/查看/回复/转发子文档（按意图分别使用）

| 文档 | 说明 |
|------|------|
| [search.md](search.md) | 搜索参数解析、search-emails 调用与展示 |
| [view.md](view.md) | 按邮件 ID 查看正文，main.py view |
| [attachment.md](attachment.md) | 下载附件（download-attach）、转存附件到云盘（transfer-attach） |
| [folder.md](folder.md) | 查看文件夹列表，main.py folder-list |
| [reply.md](reply.md) | 回复流程、main.py reply |
| [forward.md](forward.md) | 转发流程、main.py check-forward + forward |
| [mark.md](mark.md) | 同入口表「邮件标记」；mark-email 参数与示例 |
| （示例已拆分） | 搜索/查看/回复/附件下载/文件夹/转发/标记示例对话已拆分到各自文档 |

### 共用文档（多子技能或主技能引用）

| 文档 | 说明 |
|------|------|
| [cli_subcommands.md](cli_subcommands.md) | CLI 子命令速查、`--help` 查询方式、发信 `--to`/`--cc`/`--bcc` |
| [cli-to-cc-bcc.md](cli-to-cc-bcc.md) | 发信三参数 `--to`/`--cc`/`--bcc`：单一事实来源 |
| [me.md](me.md) | 基础解析 ·「我」（my-info） |
| [time.md](time.md) | 时间：get-time、参数格式、定时发送、示例、自然语言表达分类与解析、展示规范 |
| [workflow.md](workflow.md) | 工作流：步骤、意图交叉、错误与展示 |
| [scripts_and_errors.md](scripts_and_errors.md) | 错误处理汇总（通用错误、收件箱/总结/发送侧、搜索/查看/回复侧）；CLI 见 cli_subcommands.md 与 `main.py --help` |
| [verification.md](verification.md) | 验证清单与自检要点，不参与日常执行路径 |

## 子命令 → 入口文档速查

| main.py 子命令 | 所属子技能 | 入口文档 |
|----------------|------------|----------|
| search-emails（--mailbox-type outbox）, rollback-sent | 发件箱 | outbox.md、search.md |
| summary | 总结 | summary.md |
| draft-list, operate-draft, save-draft | 草稿箱 | draft_box.md |
| create-draft, send | 新建邮件（预约发送见 draft_box） | send.md、draft_box.md |
| search-users | 收件人解析 | search-users.md |
| search-emails, view, download-attach, transfer-attach, mark-email, reply, reply-brief, check-forward, forward | 搜索/查看/附件下载/附件转存/邮件标记/回复/转发（收件箱筛选用 --categories） | search.md / view.md / attachment.md / mark.md / reply.md / forward.md |
| folder-list, delete-mails, move-emails, recover-mails | 文件夹 | folder.md |
| get-time | 相对时间 | [time.md](time.md) |
| my-info | 「我」 | [me.md](me.md) |

## 使用方式

1. 意图 → SKILL.md「技能分类与 reference」表或 workflow.md「子技能交叉说明」→ 选定**一个**入口文档并打开。
2. 按该文档内链接继续打开子步骤文档。
3. 相对时间 / 自然语言时间 → **time.md**；收件人「我」→ **me.md**；发送确认、错误与展示 → **workflow.md**。
4. 子命令分组、拼命令前 `--help` → **cli_subcommands.md**。
