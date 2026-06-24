---
name: oa-message-manage
description: |
  提供 iCenter 消息发送与群管理两大功能。
  触发词（消息发送）：发送消息给<接收者>、将<内容>发消息给<接收者>、发送图片给<接收者>、发送文件给<接收者>、发送消息给群<群组名>等。
  触发词（群管理）：创建群、建群、新建群聊、加成员、拉人进群、踢人、移除成员、删除群成员、删除群、解散群、批量删除群、批量解散群、退出群、退群、把我从这些群里移除，以及按群名/群主/最近多少天没消息/近多少年没动静/是否有正式群名筛选批量操作目标群、最近给我发送消息的群、最近@我的群、最近看过的群等。
metadata:
  version: "1.0.8"
author: platform
tags: ["办公协作"]
triggers: ["发消息", "群管理", "创建群", "解散群", "iCenter消息"]
---

# OA 消息管理技能

## 功能概述

| 技能 | 说明 |
|------|------|
| 发送消息 | 支持向指定用户或群组发送文本、图片、文件消息 |
| 群管理 | 支持创建群、添加成员、移除成员、删除群、最近群查询（recv/atMe/enter）等操作 |

## 能力边界

- **发送消息**：支持单聊和群聊；支持文本、图片（bmp/jpg/png）、文件三种类型；群聊仅支持发送到本人所在的群组。
- **群管理**：创建群至少需要 2 名成员（不含调用者）；仅群主可删除成员或群；删除群须二次确认；批量退出群聊也须二次确认，但**不需要群主权限**；批量删除/退出支持按群名、群主、最近多少天没消息、近多少年没动静、是否有正式群名先筛选候选群；最近群查询支持 `recv`/`atMe`/`enter` 三种模式。

## 执行环境

使用 **`{baseDir}`** 表示本技能所在目录，推荐以 `python {baseDir}/scripts/xxx.py` 形式调用脚本。

> ⚠️ **严禁使用 `&&` 连接命令**（Windows PowerShell 5.x 不支持）

| 目录 | 作用 | 加载机制 |
|------|------|---------|
| `reference/` | 知识库与参考资料 | 按需加载到上下文 |
| `scripts/` | 可执行脚本 | 执行，但不加载到上下文 |

## 核心工作流（渐进式披露）

子任务文档均在 `{baseDir}/reference/`，**按需读取**对应文件，执行时以「(x/N) 当前步骤：xxx」格式报告进展。

### 发送消息

读取对应文档，按文档中说明执行：

1. **功能详情**：`{baseDir}/reference/send-message.md`

### 群管理

先读意图识别，再**仅读取**与当次操作对应的子任务文档：

1. **意图识别**：`{baseDir}/reference/intent-recognition.md`
2. **创建群**：`{baseDir}/reference/group-management-create-group.md`
3. **添加成员**：`{baseDir}/reference/group-management-add-members.md`
4. **移除成员**：`{baseDir}/reference/group-management-remove-members.md`
5. **删除群**：`{baseDir}/reference/group-management-delete-group.md`
6. **最近群查询**：`{baseDir}/reference/group-management-top-group.md`
7. **批量删除群**：`{baseDir}/reference/group-management-delete-batch-group.md`
8. **批量退出群聊**：`{baseDir}/reference/group-management-leave-batch-group.md`
