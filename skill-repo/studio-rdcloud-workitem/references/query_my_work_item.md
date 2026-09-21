# 查询与我相关的工作项 · 脚本入参说明


> **工具约束（必遵）**：须通过本技能已有 `scripts/` 脚本执行研发云工作项能力；**禁止**使用 write、edit、web-search、web_fetch 等处理本业务或替代脚本；**禁止**新建 Python 脚本。

对应脚本：`scripts/atomic/query_my_work_item.py`。**仅用于**「与我相关」意图（我的任务、指派给我、我创建的、我编辑的、我关注的等）；禁止对「与我相关」使用 `query_work_item.py`。

执行规则：不做执行前确认；脚本执行完成后，先展示「本次语义解析结果」（查询类型、查询列、查询条件等），再展示查询结果。
结果展示约束：默认不得总结、省略或改写查询结果；仅当用户语义明确要求“总结/汇总/精简”时，才可在完整结果后追加总结。
结果展示约束：默认不得总结、省略或改写脚本返回结果；仅当用户语义明确要求“总结/汇总/精简”时，才可在完整结果后追加总结。

## 命令行调用

- **命令形式**：`python scripts/atomic/query_my_work_item.py [--select-items ITEMS] [--conditions CONDITIONS] [--query-type TYPE] [其他可选参数]`
- **查询列**：`--select-items`，英文逗号分隔的字段名称；不传则使用默认列。
- **条件组合**：`--conditions`。**解析出的工作区、工作项类型均作为 conditions 的一部分**，与其它筛选条件用 AND 等组合；支持 AND/OR、括号 `()` 组合与嵌套；不传则无过滤。详见 [query_work_item.md · 条件组合与嵌套](query_work_item.md#conditions-nested)。
- **可选**：`--query-type`（默认 `assigned_to_me`）、`--is-send-email`、`--is-send-icenter`、`--mail-to`、`--icenter-to`。
- **查看完整参数**：`python scripts/atomic/query_my_work_item.py --help`

```bash
# 最简调用（指派给我，默认 query-type=assigned_to_me）
python scripts/atomic/query_my_work_item.py --select-items "标题,状态"

# 指定查询类型 + 发消息
python scripts/atomic/query_my_work_item.py --select-items "标题,状态" --query-type created_by_me --is-send-icenter true --icenter-to @me
```

## 参数说明（与 --help 一致）

| 参数（命令行写法） | 必填 | 说明 |
|--------------------|------|------|
| **--select-items, --selectItems** | 否 | 查询列，英文逗号分隔的字段名称 |
| **--conditions, -c** | 否 | 条件组合：工作区、工作项类型与状态/标题等均写入此处，支持 AND/OR、`()` 组合与嵌套，如 `工作项类型=用户故事 AND 工作区=业务运营`、`工作项类型=任务 AND (状态=进行中 OR 状态=待处理) AND 优先级=P0` |
| **--order-by, --orderBy** | 否 | 排序，如 更新时间 desc、标题 asc, 状态 desc；不传则默认按更新时间倒序 |
| **--limit, -n** | 否 | 返回条数上限，正整数，最大 10000（超过按 10000）；不传则默认 20 |
| **--query-type, --queryType, -q** | 否 | 与我相关类型，默认 `assigned_to_me`；见下表 |
| **--is-send-email, --is_send_email** | 否 | 是否发送邮件：true/false，默认 false |
| **--is-send-icenter, --is_send_icenter** | 否 | 是否发送 iCenter 消息：true/false，默认 false |
| **--mail-to, --mailTo** | 否 | 邮件接收人，多人英文逗号分隔 |
| **--icenter-to, --icenter_to** | 否 | iCenter 消息接收人，多人英文逗号分隔 |
| **--user-input, --userInput** | 否 | **用户的原始输入**，必须是用户真实、完整的一句话，用于工作区/类型/字段解析的上下文与日志 |

### queryType 取值（与 SKILL 一致）

| 用户表述 | --query-type 取值 |
|----------|-------------------|
| 指派给我、指派给我的 | `assigned_to_me`（默认） |
| 我的待办 | `my_todo` |
| 我创建的 | `created_by_me` |
| 我编辑的 | `edited_by_me` |
| 我关注的 | `followed_by_me` |

## 示例

```bash
python scripts/atomic/query_my_work_item.py --help

python scripts/atomic/query_my_work_item.py --select-items "标题,状态" --query-type assigned_to_me

python scripts/atomic/query_my_work_item.py --select-items "标题,状态" --queryType assigned_to_me --is_send_icenter true --icenter_to @me

python scripts/atomic/query_my_work_item.py --select-items "标识,标题,状态" --query-type created_by_me

# 条件组合示例
python scripts/atomic/query_my_work_item.py --select-items "标题,状态" --conditions "工作项类型=用户故事 AND 工作区=业务运营" --query-type assigned_to_me
python scripts/atomic/query_my_work_item.py --select-items "标题,状态" --conditions "(状态=进行中 OR 状态=待处理) AND 优先级=P0" --query-type assigned_to_me

# 排序与条数
python scripts/atomic/query_my_work_item.py --select-items "标题,状态,更新时间" --query-type assigned_to_me --order-by "更新时间 desc" --limit 10
```

## 结果展示与心智模型

- 查询结果必须完整展示给用户；脚本输出后可能追加心智模型（查询示例、提醒等），须**完整保留并展示**，禁止在总结或概括时删除、合并或省略该部分。详见 [SKILL.md](../SKILL.md)「查询结果必须展示」与「心智模型必须完整保留」。
