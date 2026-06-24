---
name: oa-zmail-manage
description: 用于处理所有邮件（邮箱）相关的任务。统一处理收件箱（含未读/紧急/标星/主送）、发件箱、草稿箱、文件夹、附件、邮件搜索/查看/回复/转发、邮件标记（已读/未读/标星/取消标星）、邮件总结、删除邮件、恢复邮件。用户在 zMail 中想查找或筛选邮件、标记邮件状态、查看第 N 封、下载或转存附件、查看或移动文件夹、给某人发邮件/存草稿/撤回邮件、查询人员邮箱，或对一段邮件做总结时，优先使用本技能，将自然语言转为具体邮件操作并返回列表、摘要或发送/操作结果。
metadata:
  version: 1.0.9
author: platform
tags: ["办公协作"]
triggers: ["邮件", "zMail", "收件箱", "发邮件", "附件"]
---

# zMail 邮件管理

集成 **收件箱**（未读/紧急/标星/主送）、**发件箱**、**总结**、**发送**（草稿与新建）、**搜索/查看/回复/转发**、**邮件标记**（已读/未读/标星/取消标星）。判意图 → **[workflow.md](references/workflow.md) + 下表当前意图一行**（双文档必读）；**「我」** [references/me.md](references/me.md)；**时间** [references/time.md](references/time.md)。

## 30 秒快速上手

1. **需求类型** → 子技能：只要出现“总结/汇总/归纳/提炼/概括/梳理/要点”等概括意图 → **总结**；看某封/第 N 条/预览附件 → **查看**；查邮件/筛选/发件箱列表 → **搜索**；发信/预约/草稿 → **新建邮件/草稿箱**；附件下载/转存云盘 → **附件下载/转存**；文件夹/移动邮件 → **文件夹**；回复/转发 → **回复/转发**；**标已读/未读、加星/去星**（改邮件状态，非仅筛列表）→ **邮件标记**。
2. 以 [workflow.md](references/workflow.md) + 主表「当前意图」一行为准；**若当前上下文中没有**二者有效条文 → **先读取**再执行。**不要**预加载与当前意图无关的 reference。
3. 命令：`python3 {baseDir}/scripts/main.py <子命令> [参数]`。缺参数时先执行 `python3 {baseDir}/scripts/main.py <子命令> --help`；子命令分组见 [references/cli_subcommands.md](references/cli_subcommands.md)，发信收件人 `--to`/`--cc`/`--bcc` 见 [references/cli-to-cc-bcc.md](references/cli-to-cc-bcc.md)。

## 任务目标与触发

- **能力**：收件箱筛选、总结、发送（草稿与新建）、删除草稿、搜索/查看/回复/转发、**邮件标记**、附件与文件夹。
- **「我」**：收件人为「我/自己」时须 `my-info` 代表「我」、不得把「我」当搜索关键字等，见 [references/me.md](references/me.md)。
- **时间**：相对时间须以 `get-time` 为准、定时发送与主题时间区分等，见 [references/time.md](references/time.md)。
- **触发**：用户表述匹配子技能之一（发件箱 / 总结 / 发送 / 搜索 / 查看 / 回复 / 转发 / 附件 / 文件夹 / **邮件标记**）。出现“总结/汇总/归纳/提炼要点”等意图词 → **总结**（`summary`；若用户指“第 N 封/这封”也按总结处理：从已有搜索结果定位到该封邮件 ID 后用 `summary --ids <id>`，不要用 `view`）。**只**要「列出/筛选」未读、紧急、标星、主送邮件 → **搜索**（`search-emails --categories`）。要**改变**已读/未读/标星状态（含「把第 N 封标已读」「把未读里关于 XX 的都标已读」）→ **邮件标记**（`mark-email`，见 [references/mark.md](references/mark.md)）。

## 强约束

执行每个子命令前，**必须先加载该子命令对应的参考文档**（下表“参考文档”列；必要时再补充 `workflow.md` 的相关段落），再拼命令与执行，避免参数/输出骨架/强约束走偏。
当用户明确表达「总结 / 汇总 / 归纳 / 提炼要点」等意图时，**必须直接使用 `summary` 子命令**（见 [references/summary.md](references/summary.md)），**禁止**先用 `view` 查看再二次做总结。
当用户表达“先看一下再总结/把第 N 封总结一下/看完帮我归纳”等**混合意图**时，仍以“总结”为主：直接走 `summary`（需要定位具体邮件时先从上下文的搜索结果取对应邮件 ID，再用 `summary --ids`）。

## 路径与入口

### 执行命令前必读

> **解释器**：Windows 用 `python` 或 `py -3`；Linux/macOS 用 `python3`。**禁止在 Windows 照抄示例里的 `python3`**。详见 [references/runtime_env.md](references/runtime_env.md)。

- **{baseDir}**：本技能目录绝对路径（oa-zmail-manage）。
- **统一入口**：`python3 {baseDir}/scripts/main.py <子命令> [参数]`。

## CLI

- 全量子命令：`python3 {baseDir}/scripts/main.py --help`
- 当前子命令参数：`python3 {baseDir}/scripts/main.py <子命令> --help`
- 子命令分组、发信 `--to`/`--cc`/`--bcc`：[references/cli_subcommands.md](references/cli_subcommands.md)

## 技能分类与 reference

解析意图 → **只打开下表一行**里的文档，按文内步骤执行。

| 子技能 | 触发场景（简要） | 参考文档 |
|--------|------------------|----------|
| **发件箱** | 撤回；列表/搜索用 search-emails --mailbox-type outbox | [references/outbox.md](references/outbox.md) |
| **总结** | 总结/汇总/归纳/提炼要点等概括意图；需要对“第 N 封/这几封”做概括时也走总结（用 summary --ids），不要走查看 | [references/summary.md](references/summary.md) |
| **草稿箱** | 草稿列表、新建草稿、设时间/取消/立即发/删除 | [references/draft_box.md](references/draft_box.md) |
| **新建邮件** | 给 xxx 发邮件（有具体发送时间→预约，否则立即） | [references/send.md](references/send.md)、[references/draft_box.md](references/draft_box.md) |
| **搜索邮件** | 查找邮件；未读/紧急/标星/主送 + 日期/关键字 | [references/search.md](references/search.md) |
| **搜索人员** | 查找/搜索联系人；查询人员邮箱 | [references/search-users.md](references/search-users.md) |
| **查看** | 查看/阅读/打开/第 N 条；**仅单封邮件**（不得传多个 ID，也不得用逗号拼多个 ID）；概括/总结意图一律走总结 | [references/view.md](references/view.md) |
| **附件下载** | 下载附件到本地 | [references/attachment.md](references/attachment.md) |
| **附件转存** | 附件转存云盘 | [references/attachment.md](references/attachment.md) |
| **文件夹** | 文件夹列表；**删除邮件**（`delete-mails`）；移动邮件（`move-emails`，**无 outbox / 不含 deleted**） | [references/folder.md](references/folder.md) |
| **邮件标记** | 已读/未读/标星/取消标星；按邮件 ID 或按条件批量标记（`mark-email`） | [references/mark.md](references/mark.md) |
| **回复** | 回复邮件 | [references/reply.md](references/reply.md) |
| **转发** | 转发给 xxx | [references/forward.md](references/forward.md) |

## 附加参考

| 文档 | 说明 |
|------|------|
| [references/me.md](references/me.md) | **「我」**（my-info、禁止把「我」当关键字） |
| [references/time.md](references/time.md) | **时间**（格式、get-time、定时发送、示例、自然语言表达分类与解析） |
| [references/workflow.md](references/workflow.md) | **工作流**：步骤、意图交叉、错误与展示（必读） |
| [references/cli_subcommands.md](references/cli_subcommands.md) | 子命令分组、发信 `--to`/`--cc`/`--bcc`、拼命令前 `--help` |
| [references/readme.md](references/readme.md) | reference 文档索引 |
| [references/scripts_and_errors.md](references/scripts_and_errors.md) | 错误处理汇总 |
| [references/verification.md](references/verification.md) | 执行前核对项 |

## 执行前核对

[references/verification.md](references/verification.md)
