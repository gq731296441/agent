# 创建工作项 · 脚本入参说明


> **工具约束（必遵）**：须通过本技能已有 `scripts/` 脚本执行研发云工作项能力；**禁止**使用 write、edit、web-search、web_fetch 等处理本业务或替代脚本；**禁止**新建 Python 脚本。

对应脚本：`scripts/atomic/create_work_item.py`。调用方式与执行顺序见 [SKILL.md](../SKILL.md)。

执行规则：不做执行前确认；脚本执行完成后，先展示「本次语义解析结果」（工作区、类型、字段数量、通知参数等），再展示创建结果。
结果展示约束：默认不得总结、省略或改写脚本返回结果；仅当用户语义明确要求“总结/汇总/精简”时，才可在完整结果后追加总结。

## 创建类型边界（必遵）

- 当前不支持创建以下工作项类型：**原始需求、外场故障、测试用例、测试计划、测试记录**。
- 若用户明确要求创建上述类型，应直接提示不支持，禁止改用其它类型代替创建。

## 命令行调用

- **命令形式**：`python scripts/atomic/create_work_item.py [必填参数] [可选参数]`
- **必填**：工作项类型（二选一）+ `--fields`；可选：通知与接收人。
- **查看完整参数**：`python scripts/atomic/create_work_item.py --help`


## 参数说明（与 --help 一致）

命令行参数支持长格式（`--work-item-type-name`）、短格式（`-f`）及驼峰（`--workItemTypeName`），与脚本 `--help` 输出一致。

| 参数（命令行写法） | 必填 | 说明 |
|--------------------|------|------|
| **--work-item-type-name, --workItemTypeName** | 是（推荐） | 工作项类型名称，如 任务、缺陷 |
| **--fields, -f** | 是 | 字段数组 JSON，每项为「字段名称」与「值」，如 `[{"key":"标题","value":"标题xxx"}]`；脚本内部按名称解析 |
| **--is-send-email, --is_send_email** | 否 | 创建成功后是否发邮件：true/false，默认 false |
| **--is-send-icenter, --is_send_icenter** | 否 | 创建成功后是否发 iCenter 消息：true/false，默认 false |
| **--mail-to, --mailTo** | 否 | 邮件接收人，多人英文逗号分隔 |
| **--icenter-to, --icenter_to** | 否 | iCenter 消息接收人，多人英文逗号分隔 |
| **--relate-workitem-ids, --relateWorkitemIds** | 否 | 创建成功后要关联的工作项标识，多个以英文逗号分隔；需与 --relate-type 同时使用 |
| **--relate-type, --relateType** | 否 | 关联类型中文名。建立关联时口语「新工作项作为 xxx 的子级」须传 **`父级`**（与「子级」取反），见 [SKILL.md §0.3](../SKILL.md) |
| **--user-input, --userInput** | 否 | **用户的原始输入**，必须是用户真实、完整的一句话，用于工作区/类型/字段解析的上下文与日志 |

## 示例

```bash
# 查看完整帮助
python scripts/atomic/create_work_item.py --help

# 使用类型名 + 字段名称创建任务
python scripts/atomic/create_work_item.py --work-item-type-name 任务 --fields '[{"key":"标题","value":"测试任务"},{"key":"描述","value":"描述内容"}]'

# 创建后发邮件/消息
python scripts/atomic/create_work_item.py --work-item-type-name 任务 --fields '[{"key":"标题","value":"示例标题"}]' --is-send-email true --is-send-icenter true --icenter-to @me

# 创建后与指定工作项建立关联（新工作项作为 PLM-100 的子级 → --relate-type 父级）
python scripts/atomic/create_work_item.py --work-item-type-name 任务 --fields '[{"key":"标题","value":"示例"}]' --relate-workitem-ids PLM-100 --relate-type 父级

```

## 指派给、处理人解析（必遵）

用户表述须解析为对应**字段名称**，**不得混用**：

| 用户表述 | 解析为字段名称 |
|----------|----------------|
| **指派给 XXX**、指派给张三、指派给我 | **指派给** |
| **处理人设置为 XXX**、处理人设为张三、设置处理人为我 | **处理人** |

创建时若用户说「指派给张三」，则 `--fields` 中写入 `{"key":"指派给","value":"张三"}`（脚本会通过 search_users 转为工号）；若说「处理人设为我」，则写入 `{"key":"处理人","value":"@me"}`。

## 人员字段

人员类字段（如指派给、处理人）**必须传工号**。入参可传 `@me`/姓名等，脚本会转为工号；若传的已是工号则直接使用。

## 常用字段名称（示例）

入参一律使用**字段名称**（如 标题、状态、描述、指派给、处理人、优先级）。各工作区/类型可用字段以实际为准，由脚本内部通过 API 解析名称。常见名称示例：标题、状态、描述、指派给、处理人、优先级、创建时间、更新时间、标识、迭代、团队等。

## 参考

- 功能与脚本映射、触发条件、AI 必遵规则：见 [SKILL.md](../SKILL.md)。
