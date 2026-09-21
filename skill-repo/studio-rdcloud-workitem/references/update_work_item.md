# 更新工作项 · 脚本入参说明


> **工具约束（必遵）**：须通过本技能已有 `scripts/` 脚本执行研发云工作项能力；**禁止**使用 write、edit、web-search、web_fetch 等处理本业务或替代脚本；**禁止**新建 Python 脚本。

对应脚本：`scripts/atomic/update_work_item.py`。调用方式与执行顺序见 [SKILL.md](../SKILL.md)。**工作项标识支持多个，以英文逗号分隔**，将按顺序逐个更新并汇总输出。

执行规则：不做执行前确认；脚本执行完成后，先展示「本次语义解析结果」（工作项标识、字段数量、关联参数等），再展示更新结果。
结果展示约束：默认不得总结、省略或改写脚本返回结果；仅当用户语义明确要求“总结/汇总/精简”时，才可在完整结果后追加总结。

## 命令行调用

- **命令形式**：`python scripts/atomic/update_work_item.py --work-item-id <ID> --fields '<JSON>' [可选参数]`
- **必填参数**：`--work-item-id`（工作项标识，多个以英文逗号分隔，如 PLM-12345,PLM-12346）、`--fields`（字段数组 JSON 字符串）。
- **工作区**：从各 work_item_id 截取（最后一个 `-` 前半部分），无需单独传入。
- **查看完整参数**：`python scripts/atomic/update_work_item.py --help`

```bash
# 最简调用（字段用名称）
python scripts/atomic/update_work_item.py --work-item-id PLM-12345 --fields '[{"key":"标题","value":"新标题"}]'

# 多个工作项同时更新（同一批字段）
python scripts/atomic/update_work_item.py --work-item-id PLM-12345,PLM-12346 --fields '[{"key":"状态","value":"进行中"}]'

# 更新后发消息
python scripts/atomic/update_work_item.py --work-item-id PLM-12345 --fields '[{"key":"状态","value":"进行中"}]' --is-send-icenter true --icenter-to @me

# 更新后与指定工作项建立关联（当前工作项作为 PLM-100 的子级 → --relate-type 父级）
python scripts/atomic/update_work_item.py --work-item-id PLM-12345 --fields '[{"key":"状态","value":"进行中"}]' --relate-workitem-ids PLM-100 --relate-type 父级
```

## 接口说明

- **方法与路径**：`PUT {base_url}/api/rest/v1/work_items/update/{work_item_id}`
- **请求体**：仅 `fields` 数组；脚本内部将传入的**字段名称**解析为接口所需格式。无需单独传 workspace。
- **响应**：`{"bo": {...}, "code": {"code":"0000","msg":"","msgId":"SUCCESS"}}`，更新后的工作项在 `bo` 中。

## 参数说明（与 --help 一致）

命令行参数支持长格式（`--work-item-id`）、驼峰（`--workItemId`）及短格式（`-f`），与脚本 `--help` 输出一致。

| 参数（命令行写法） | 必填 | 说明 |
|--------------------|------|------|
| **--work-item-id, --workItemId** | 是 | 工作项标识，多个以英文逗号分隔，如 PLM-12345,PLM-12346 |
| **--fields, -f** | 是 | 字段数组 JSON，每项为「字段名称」与「值」；支持字段"追加"，增加参数 "modifyType":"appendAfter"，如 `[{"key":"标题","value":"新标题"}]` 或 `[{"key":"下一环节处理人","modifyType":"appendAfter","value":"赵飞0668001219"}]`；脚本内部按名称解析 |
| **--work-item-type-name, --workItemTypeName** | 否 | 工作项类型名称（仅用于脚本内部解析，不随更新请求体发送） |
| **--is-send-email, --is_send_email** | 否 | 更新成功后是否发邮件：true/false，默认 false |
| **--is-send-icenter, --is_send_icenter** | 否 | 更新成功后是否发 iCenter 消息：true/false，默认 false |
| **--mail-to, --mailTo** | 否 | 邮件接收人，多人英文逗号分隔 |
| **--icenter-to, --icenter_to** | 否 | iCenter 消息接收人，多人英文逗号分隔 |
| **--relate-workitem-ids, --relateWorkitemIds** | 否 | 更新成功后要关联的工作项标识，多个以英文逗号分隔；需与 --relate-type 同时使用 |
| **--relate-type, --relateType** | 否 | 关联类型中文名。建立关联时口语「作为 xxx 的子级」须传 **`父级`**（取反规则见 [SKILL.md §0.3](../SKILL.md)） |
| **--user-input, --userInput** | 否 | **用户的原始输入**，必须是用户真实、完整的一句话，用于工作区/类型/字段解析的上下文与日志 |

## 指派给、处理人解析（必遵）

用户表述须解析为对应**字段名称**，**不得混用**：

| 用户表述 | 解析为字段名称 |
|----------|----------------|
| **指派给 XXX**、指派给张三、指派给我 | **指派给** |
| **处理人设置为 XXX**、处理人设为张三、设置处理人为我 | **处理人** |

## 更新字段“追加”语义（必遵）

当用户在更新语义中明确表达某字段要“追加”（如「追加」「后追加」「再加一个」「在末尾增加」），该字段在 `--fields` 中必须携带：

- `modifyType: "appendAfter"`

注意：

- 只给“追加字段”加 `modifyType`，其它字段保持普通更新（仅 `key` + `value`）。
- 同一次更新多个字段时，允许“部分字段追加、部分字段覆盖”。
- 已显式给出 `modifyType` 时按入参透传；未显式给出但语义命中“追加”时应补 `appendAfter`。

### 指派给取值规则

更新「指派给」时，`--fields` 中写入字段名称「指派给」，例如 `{"key":"指派给","value":"0668001219"}` 或 `{"key":"指派给","value":"@me"}`。

脚本对 value 做自动解析：

| value 写法 | 脚本行为 |
|------------|----------|
| `@me` / `me` / `@Me` / `ME`（不区分大小写） | 转换为**当前用户工号**（来自 X-Emp-No / 环境变量） |
| 其他（姓名、工号等） | 调用 **search_users** 按关键字搜索用户，取**第一个**匹配结果的工号；搜不到或失败时保留原值 |

### 处理人

用户说「处理人设置为 XXX」时，解析为字段名称「处理人」，value 规则同上。人员字段接口**必须传工号**。

## 示例

```bash
# 查看完整帮助
python scripts/atomic/update_work_item.py --help

# 仅更新标题与状态（字段用名称）
python scripts/atomic/update_work_item.py --work-item-id PLM-12345 --work-item-type-name 任务 --fields '[{"key":"标题","value":"新标题"},{"key":"状态","value":"进行中"}]'

# 多个工作项批量更新状态
python scripts/atomic/update_work_item.py --work-item-id PLM-12345,PLM-12346 --fields '[{"key":"状态","value":"进行中"}]'

# 更新后发 iCenter 消息给我
python scripts/atomic/update_work_item.py --work-item-id PLM-12345 --fields '[{"key":"状态","value":"已完成"}]' --is-send-icenter true --icenter-to @me

# 用户提到要对某字段做追加，增加："modifyType":"appendAfter"
python scripts/atomic/update_work_item.py --work-item-id PLM-12345 --fields '[{"key":"下一环节处理人","modifyType":"appendAfter","value":"赵飞0668001219"},{"key":"状态","value":"进行中"}]' --user-input "更新 PLM-12345，下一环节处理人追加赵飞0668001219，状态改为进行中"
```

### 指派给、处理人相关示例

```bash
# 指派给我（字段名称「指派给」，value=@me → 当前用户工号）
python scripts/atomic/update_work_item.py --work-item-id PLM-3241996 --fields '[{"key":"指派给","value":"@me"}]'

# 指派给指定工号
python scripts/atomic/update_work_item.py --work-item-id PLM-3241996 --fields '[{"key":"指派给","value":"0668001219"}]'

# 指派给某人（传姓名，脚本 search_users 取第一个工号）
python scripts/atomic/update_work_item.py --work-item-id PLM-3241996 --fields '[{"key":"指派给","value":"赵飞"}]'

# 处理人设置为我（字段名称「处理人」）
python scripts/atomic/update_work_item.py --work-item-id PLM-3241996 --fields '[{"key":"处理人","value":"@me"}]'

# 同时改标题和指派给
python scripts/atomic/update_work_item.py --work-item-id PLM-3241996 --fields '[{"key":"标题","value":"模型同步联调1"},{"key":"指派给","value":"@me"}]'
```

## 人员字段

人员字段（如指派给）**必须传工号**。入参可传 `@me`/姓名等，脚本会按上表规则转换为工号；若传的已是工号则直接使用。

## 常用字段名称（示例）

入参一律使用**字段名称**（如 标题、状态、指派给、处理人）。各工作区/类型以实际为准，由脚本内部通过 API 解析。常见名称：标题、状态、描述、指派给、处理人、优先级、创建时间、更新时间、标识等。

## 参考

- 功能与脚本映射、触发条件、AI 必遵规则：见 [SKILL.md](../SKILL.md)。
