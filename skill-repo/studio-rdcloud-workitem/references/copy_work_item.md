# 复制工作项脚本参考

## 概述

`copy_work_item.py` 基于现有工作项复制新工作项，支持字段覆盖、目标类型变更、父子关联、通知发送等功能。默认复制源单据的可复制字段；可通过 `--copy-all false` 仅保留必填字段，或通过 `--override-fields` 覆盖指定字段。

## 入参怎么生效（复制专用）

- 默认行为：从**源单**带出可复制字段，**不改标题、不挂父级、不发邮件**。
- 若用户口头说了「改标题 / 挂父级 / 发邮件」等，必须在本次命令里用 **`--override-fields`、`--child-of`（或 `--link-relation-id`）、`--is-send-email` / `--mail-to`** 显式传入；脚本**只认这些开关**，不会从 `--user-input` 整句里自动拆出标题或收件人。
- `--user-input`：本技能要求带上，用于审计与上下文；**不能代替**上面的结构化参数。

## 脚本位置

```
scripts/atomic/copy_work_item.py
```

## 命令行调用

- **命令形式**：`python scripts/atomic/copy_work_item.py [参数] --user-input "用户原始输入"`
- **真正影响复制结果的业务参数**（改标题、父级、发邮件等）必须出现在**对应命令行开关**上；见下节「用户表述 → 参数映射」。
- **必填**：`--source-work-item-id`；本技能还要求 `--user-input`（与 [SKILL.md](../SKILL.md) 一致）。
- **查看完整参数**：`python scripts/atomic/copy_work_item.py --help`

```bash
python scripts/atomic/copy_work_item.py [参数] --user-input "用户原始输入"
```

> **路由建议（重点）**：当用户语义为「复制/拷贝/克隆某张单据」时，必须调用本脚本 `copy_work_item.py`。  
> **入参契约**：本脚本只消费已结构化的 JSON/命令行入参（如 `overrideFields`、`childOf`、`is_send_email`、`mail_to` 等）。**用户自然语言中的「改标题/发邮件/父级」等意图，必须在调用本脚本之前由技能/编排层解析并填入对应参数**；脚本内不根据 `user-input` 再猜字段。

## 用户表述 → 参数映射（必遵）

口语要先落成**下面表格里的命令行参数**，不能指望脚本只读 `--user-input` 就执行。

| 用户表述（示例） | 须落到命令行 |
|------------------|--------------|
| 标题为 XXX / 改标题为 XXX | `--override-fields` 中带 `{"key":"标题","value":"XXX"}`（或 `System_Title`；可用中文字段名，脚本内会解析为 key） |
| 作为 … 的子级 / 父级为 PLM-xxx / 挂到 PLM-xxx 下 | `--child-of PLM-xxx`（后置建关联）或 `--link-relation-id linkRelatedType_3`（复制接口内建关联） |
| 发邮件给工号 / 完成后发邮件 | `--is-send-email true`，必要时 `--mail-to 工号列表` |
| 复制为「任务/缺陷」等类型 | `--target-work-item-type-name` 或 `--target-work-item-type-key` |

**反例（无效）**：仅 `--user-input "…标题为测试…发邮件给0668001115"` 但**不传** `--override-fields` / `--is-send-email` / `--mail-to` → 脚本**不会**据此改标题或发邮件。



## 执行策略

- 命中「复制 + 单据号」时，默认**直接执行复制**，不逐项追问附件/子任务/评论等可选项。
- 默认行为：同源类型、全量复制（`--copy-all true`）、不覆盖字段、不建立父子关系、不发送通知。
- 仅在无法执行时追问：未识别源单据号、指令冲突、关键参数缺失且无法默认。
- 若用户明确说了覆盖字段、子级关联、通知等，再按用户指定追加参数。
- **强制要求**：复制场景下若用户指定“改标题/改指派/改状态/改任意字段”，必须在本次 `copy_work_item.py` 调用内通过 `--override-fields` 直接替换当前复制字段；**禁止**在复制成功后再调用 `update_work_item.py` 对新单据二次更新字段。

## 参数说明

### 必填参数

| 参数 | 说明 |
|------|------|
| `--source-work-item-id` | 源工作项标识（如 RAN-7402567，当前版本必传） |
| `--user-input` | 用户原始输入（必须传递，用于日志和语义辅助） |

### 目标类型（二选一，不指定则同源类型）

| 参数 | 说明 |
|------|------|
| `--target-work-item-type-name` | 目标工作项类型中文名（如「需求」「任务」「缺陷」） |
| `--target-work-item-type-key` | 目标工作项类型 key（如 Requirement、Task、Bug） |

### 字段覆盖（可选）

| 参数 | 说明 |
|------|------|
| `--override-fields` | JSON 数组，指定需要覆盖的字段值，例如：`[{"key":"System_Title","value":"新标题"},{"key":"System_AssignedTo","value":"李海洋"}]` |
| `--copy-all` | 是否复制源工作项全部非系统字段（默认 true）；设为 false 时仅保留复制初始化返回中的必填字段，其余建议通过 `--override-fields` 补充 |

**注意**：`--override-fields` 与 `--copy-all` 在命令行参数层面互斥（argparse 互斥组），请二选一。

### 关联与通知

| 参数 | 说明 |
|------|------|
| `--child-of` | 新工作项作为指定工作项的子级（自动建立子级关联） |
| `--link-relation-id` | 复制接口内直接建立关联关系（如 `linkRelatedType_3`=子级）。支持动态传入，空值表示不在复制阶段建关联 |
| `--is-send-email` | 创建成功后发送邮件（true/false，默认 false） |
| `--is-send-icenter` | 创建成功后发送 iCenter 消息（true/false，默认 false） |
| `--mail-to` | 邮件接收人（多人英文逗号分隔），不指定则使用新工作项指派给用户的邮箱 |
| `--icenter-to` | iCenter 接收人（多人英文逗号分隔），不指定则使用新工作项指派给用户 |

### 其他参数

| 参数 | 说明 |
|------|------|
| `--smart-complete` | 是否启用字段值智能解析与 event 派生必填补齐（默认 true）；设为 false 时覆盖字段按原值写入，不做统一解析 |
| `--format-as-markdown` | 输出格式：true=Markdown，false=JSON；默认 true（返回带链接的 Markdown） |
| `--user-input` | 用户原始输入（必须传递） |

## 核心流程

1. **读取源单字段**：查询源工作项并抽取可复制字段
2. **复制范围控制**：`--copy-all=false` 时仅保留目标类型必填字段
3. **字段覆盖与解析**：根据 `--override-fields` 做 key/value 解析后覆盖
4. **复制前校验**：调用 `AiFunction/skill/check-batchCopy` 校验请求体
5. **执行复制**：调用 `AiFunction/skill/batchCopy` 完成复制
6. **关联处理（可选）**：
   - 若传 `--link-relation-id`，在复制接口体内通过 `linkRelationId` 直接建关联；
   - 若传 `--child-of`，复制成功后调用 `batch_create_relations()` 建立父子关联
7. **发送通知**（可选）：调用 `send_mail()` 或 `send_icenter_message()` 发送通知
8. **输出结果**：格式化为 Markdown 或 JSON

## 复制范围

### 自动复制的字段

- 所有用户自定义字段（文本框、数字、日期、下拉、高级数据等）
- 描述（富文本）
- 附件（附件列表结构）
- 标签
- 多值字段（用户、标签等）

### 不复制的字段（系统自动处理）

| 字段 key | 说明 |
|----------|------|
| `System_Id` | 标识（新生成） |
| `System_CreatedDate` | 创建时间（新时间） |
| `System_CreatedBy` | 创建人（当前用户） |
| `System_ChangedDate` | 修改时间 |
| `System_ChangedBy` | 修改人 |
| `System_Version` | 版本 |
| `System_State` | 状态（通常为「新建」） |
| `System_WorkItemType` | 工作项类型 |
| `System_WorkspaceKey` | 工作区 |
| `System_SourceWorkItem` | 复制源（系统自动设置） |

## 示例

阅读方式：**先对照「意图 → 脚本参数」表**，再跑下面命令；表中未列出的口头说法（若未写成对应参数）**不会**被脚本执行。

约定：下列命令均含 `--formatAsMarkdown true`（与 [SKILL.md](../SKILL.md) 调用习惯一致，可删改用默认）。

| 脚本参数 | 在行为上的含义 |
|----------|----------------|
| `--source-work-item-id` | 从哪张单复制（必填） |
| `--target-work-item-type-name` / `--target-work-item-type-key` | 副本的工作项类型；不传则与源单同类型 |
| `--override-fields` | 覆盖哪些字段（JSON）；与 `--copy-all` **互斥**，同一条命令只能二选一 |
| `--copy-all false` | 只带目标类型必填字段复制；**不能**与同命令中的 `--override-fields` 同时出现 |
| `--child-of` | 副本作为**该标识**对应工作项的子级（父单标识，常与源单不同） |
| `--is-send-email` / `--mail-to` | 邮件通知 |
| `--is-send-icenter` / `--icenter-to` | iCenter 通知（与邮件是两套开关） |
| `--user-input` | 必填审计串；**不替代**上表业务参数 |

---

### 示例 1：只复制，不换类型、不改字段、不挂父级、不通知

| 用户意图 | 对应脚本参数 |
|----------|----------------|
| 从 RAN-7402567 复制一张 | `--source-work-item-id RAN-7402567` |
| 保留技能要求的原始句 | `--user-input "…"` |

```bash
python scripts/atomic/copy_work_item.py \
  --source-work-item-id "RAN-7402567" \
  --user-input "复制 RAN-7402567" \
  --formatAsMarkdown true
```

---

### 示例 2：复制成「需求」，并改标题、指派、团队

| 用户意图 | 对应脚本参数 |
|----------|----------------|
| 目标类型为需求 | `--target-work-item-type-name "需求"` |
| 标题 / 指派 / 团队要改成指定值 | `--override-fields '[...]'`（一条 JSON，多个 key） |
| 审计 | `--user-input "…"` |

```bash
python scripts/atomic/copy_work_item.py \
  --source-work-item-id "RAN-7402567" \
  --target-work-item-type-name "需求" \
  --override-fields '[{"key":"System_Title","value":"LTE、NR协议字段全量梳理—用户面二队"},{"key":"System_AssignedTo","value":"李海洋"},{"key":"团队","value":"用户面二队"}]' \
  --user-input "基于RAN-7402567复制为新需求，修改标题和指派给" \
  --formatAsMarkdown true
```

---

### 示例 3：复制为任务，副本挂到**指定父单**下，并发 iCenter

| 用户意图 | 对应脚本参数 |
|----------|----------------|
| 源单 | `--source-work-item-id RAN-7402567` |
| 副本类型为任务 | `--target-work-item-type-key Task` |
| 新单作为 **PLM-3328325** 的子级（父单号一般 ≠ 源单号） | `--child-of PLM-3328325` |
| 创建后发 iCenter 给李海洋 | `--is-send-icenter true` + `--icenter-to "李海洋"` |

```bash
python scripts/atomic/copy_work_item.py \
  --source-work-item-id "RAN-7402567" \
  --target-work-item-type-key Task \
  --child-of "PLM-3328325" \
  --is-send-icenter true \
  --icenter-to "李海洋" \
  --user-input "复制 RAN-7402567 为任务，挂到 PLM-3328325 下，并 iCenter 通知李海洋" \
  --formatAsMarkdown true
```

---

### 示例 4：换成「任务」类型，且只复制必填字段（本命令不传 `--override-fields`）

| 用户意图 | 对应脚本参数 |
|----------|----------------|
| 目标类型为任务 | `--target-work-item-type-name "任务"` |
| 不要全量字段，只要必填集 | `--copy-all false`（**不要**在同一条命令里再写 `--override-fields`，互斥） |
| 审计 | `--user-input "…"` |

```bash
python scripts/atomic/copy_work_item.py \
  --source-work-item-id "RAN-7402567" \
  --target-work-item-type-name "任务" \
  --copy-all false \
  --user-input "仅复制必填字段到任务类型" \
  --formatAsMarkdown true
```

---

### 示例 5：目标类型为任务，全量复制但只改标题和描述

| 用户意图 | 对应脚本参数 |
|----------|----------------|
| 目标类型任务 | `--target-work-item-type-name "任务"` |
| 默认全量复制；只覆盖标题、描述 | `--override-fields`（未传 `--copy-all` 时默认为全量，见脚本默认值） |
| 审计 | `--user-input "…"` |

```bash
python scripts/atomic/copy_work_item.py \
  --source-work-item-id "RAN-7402567" \
  --target-work-item-type-name "任务" \
  --override-fields '[{"key":"System_Title","value":"新任务标题"},{"key":"System_Description_html","value":"<p>任务描述</p>"}]' \
  --user-input "复制为任务，只改标题和描述" \
  --formatAsMarkdown true
```

---

### 示例 6：换类型 + 多字段覆盖 + 子级 + iCenter（与脚本 `--help` 里长示例一致）

| 用户意图 | 对应脚本参数 |
|----------|----------------|
| 源单 RAN-7402567 → 新单为需求 | `--target-work-item-type-name "需求"` |
| 指派、标题、团队 | `--override-fields` |
| 新单作为 RAN-7402567 的子级（父为源单本人） | `--child-of RAN-7402567` |
| 句中「发送给李海洋」本例实现为 **iCenter**（不是邮件） | `--is-send-icenter true` + `--icenter-to "李海洋"`；若要走邮件需改 `--is-send-email true` 和 `--mail-to` |

```bash
python scripts/atomic/copy_work_item.py \
  --source-work-item-id "RAN-7402567" \
  --target-work-item-type-name "需求" \
  --override-fields '[{"key":"System_AssignedTo","value":"李海洋"},{"key":"System_Title","value":"LTE、NR协议字段全量梳理—用户面二队"},{"key":"团队","value":"用户面二队"}]' \
  --child-of "RAN-7402567" \
  --is-send-icenter true \
  --icenter-to "李海洋" \
  --user-input '基于市场需求RAN-7402567复制一个新需求：指派给李海洋、标题为"LTE、NR协议字段全量梳理—用户面二队"、团队为"用户面二队"、作为RAN-7402567的子级，其余字段相同，完成后发送给李海洋。' \
  --formatAsMarkdown true
```

## 错误码

| 错误码 | 说明 | 处理建议 |
|--------|------|----------|
| `0000` | 成功 | - |
| 其他 | 复制初始化失败、提交失败等 | 检查参数、网络、权限 |

## 注意事项

1. **字段存在性检查**：目标类型必须包含复制的字段，否则会被系统自动过滤
2. **关联限制**：支持 `--child-of`（后置建关联）以及 `--link-relation-id`（复制接口内建关联）；未传时不建立关联
3. **跨工作区复制**：当前版本仅支持同工作区复制（接口支持但脚本未开放相关参数）
4. **必填字段**：若目标类型有必填字段源工作项为空且未覆盖，创建可能失败；建议配合 `--override-fields` 或开启 `--smart-complete`。
5. **自然语言识别范围**：当前仅自动提取单据标识（如 `PLM-3328329`）；“当前单据”这类上下文词本身无法在脚本内定位具体单据，建议显式写出单据号。
6. **组合操作说明（文档级）**：对于“复制 + 改字段/挂父级/通知”等组合诉求，推荐在一次 `copy_work_item.py` 调用中通过 `--override-fields`、`--child-of`、通知参数完成，避免“先复制再 update”。
7. **源单据不存在提示**：当源单据查不到时，脚本会给出可读错误提示（单据号校验/权限校验/可显式指定目标类型），不再抛出数组越界异常。

## 依赖的公共模块

- `common.rdcloud_api`：`query_work_item_details`、`batch_create_relations`、`studio_workitem_transmit_url`
- `common.auth_helper`：`get_auth_headers`
- `common.log_helper`：日志相关
- `common.notify_helper`：`send_mail`、`send_icenter_message`
- `common.mindset_messages`：`get_mindset_message`
- `atomic.create_work_item`：`_step_create_init`、`_step_put_event`、`_create_work_item_post_with_fallback`、`_step_destroy_event`

## 与 create_work_item.py 的差异

| 对比项 | create_work_item.py | copy_work_item.py |
|--------|-------------------|-------------------|
| 数据来源 | 手动提供字段 | 源工作项自动复制 |
| 初始化接口 | `work_items/init/getInitDetail` | `work_items/copy-init/getDetail` |
| 字段处理 | 用户完整构造 fields | 复制 + 覆盖 |
| 智能补全 | 支持（从历史创建记录） | 通常不需要（复制已完整） |
| 复制源跟踪 | 无 | `System_SourceWorkItem` 自动记录 |

## 响应结构

成功响应（与 create 一致）：

```json
{
  "bo": {
    "id": "RAN-7402568",
    "System_Title": "LTE、NR协议字段全量梳理—用户面二队",
    "System_WorkItemType": {
      "name": "需求"
    },
    "fields": [...]
  },
  "code": {
    "code": "0000",
    "msg": "",
    "msgId": "SUCCESS"
  }
}
```
