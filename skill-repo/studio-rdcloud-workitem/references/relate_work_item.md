# 关联工作项 · 脚本入参说明


> **工具约束（必遵）**：须通过本技能已有 `scripts/` 脚本执行研发云工作项能力；**禁止**使用 write、edit、web-search、web_fetch 等处理本业务或替代脚本；**禁止**新建 Python 脚本。

对应脚本：`scripts/atomic/relate_work_item.py`。用于将两个或以上工作项建立关联关系。口语「将 A 作为 B 的子级」时 **`--relate-type` 传 `父级`**（与「子级」取反），见 [SKILL.md §0.3](../SKILL.md)。调用方式与执行顺序见 [SKILL.md](../SKILL.md)。

执行规则：不做执行前确认；脚本执行完成后，先展示「本次语义解析结果」（主工作项、关联工作项、关联类型），再展示关联结果。
结果展示约束：默认不得总结、省略或改写脚本返回结果；仅当用户语义明确要求“总结/汇总/精简”时，才可在完整结果后追加总结。

## 命令行调用

- **命令形式**：`python scripts/atomic/relate_work_item.py [必填参数] [可选参数]`
- **必填**：`--work-item-ids`、`--relate-workitem-ids`、`--relate-type`
- **查看完整参数**：`python scripts/atomic/relate_work_item.py --help`

## 参数说明

| 参数（命令行写法） | 必填 | 说明 |
|--------------------|------|------|
| **--work-item-ids, --workItemIds** | 是 | 主工作项标识，多个以英文逗号分隔（作为关联主体的一方，如「将 A 作为 B 的子级」中的 A；支持 A,B 多个主工作项） |
| **--relate-workitem-ids, --relateWorkitemIds** | 是 | 被关联的工作项标识，多个以英文逗号分隔（如 B 或 B,C） |
| **--relate-type, --relateType** | 是 | 关联类型中文名，如 子级、父级、相关、前置、后续 等。建立关联时口语「A 作为 B 的子级」须传 **`父级`**（与「子级」取反），详见 [SKILL.md §0.3](../SKILL.md) |
| **--user-input, --userInput** | 否 | 用户原始输入，用于日志与上下文 |

## 支持的关联类型（linkInZh）

| 中文 | 说明 |
|------|------|
| 相关 | linkRelatedType_1 |
| 父级 | linkRelatedType_2 |
| 子级 | linkRelatedType_3 |
| 前置 / 后续 | linkRelatedType_13 / linkRelatedType_12 |
| 源 / 副本 | linkRelatedType_11 / linkRelatedType_10 |
| 被测试 / 测试 | linkRelatedType_4 / linkRelatedType_5 |
| 影响 / 影响者 | linkRelatedType_6 / linkRelatedType_7 |
| 下游 / 上游 | linkRelatedType_8 / linkRelatedType_9 |
| 计划 / 被计划 | plan_in / plan_out |
| 依赖 / 被依赖 | depend_in / depend_out |

## 示例

```bash
# 将 PLM-3292542 作为 PLM-3292544 的子级进行关联（口语「子级」→ --relate-type 父级）
python scripts/atomic/relate_work_item.py --work-item-ids PLM-3292542 --relate-workitem-ids PLM-3292544 --relate-type 父级

# 将工作项 A 与 B、C 建立「相关」关系（对称关系，仍传 相关）
python scripts/atomic/relate_work_item.py --work-item-ids PLM-100 --relate-workitem-ids PLM-101,PLM-102 --relate-type 相关

# 将多个主工作项 A、B 分别与 C 建立「作为 C 的子级」关系（共 2 条关联，口语子级 → 父级）
python scripts/atomic/relate_work_item.py --work-item-ids PLM-1,PLM-2 --relate-workitem-ids PLM-100 --relate-type 父级
```

## 与创建/更新联动

创建、更新工作项脚本支持在成功后再建立关联，通过可选参数传入：

- **create_work_item.py**：`--relate-workitem-ids`、`--relate-type`（创建成功后，将新工作项与指定工作项建立关联）
- **update_work_item.py**：`--relate-workitem-ids`、`--relate-type`（更新成功后，将本次更新的工作项与指定工作项建立关联）

详见 [create_work_item.md](create_work_item.md)、[update_work_item.md](update_work_item.md)。

## 参考

- 功能与脚本映射、触发条件、AI 必遵规则：见 [SKILL.md](../SKILL.md)。
