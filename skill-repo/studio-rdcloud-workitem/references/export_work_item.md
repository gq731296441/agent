# 工作项导出 · 脚本入参说明


> **工具约束（必遵）**：须通过本技能已有 `scripts/` 脚本执行研发云工作项能力；**禁止**使用 write、edit、web-search、web_fetch 等处理本业务或替代脚本；**禁止**新建 Python 脚本。

对应脚本：`scripts/atomic/export_work_item.py`。按查询列与条件查询工作项后导出为 Excel。

执行规则：不做执行前确认；脚本执行完成后，先展示「本次语义解析结果」（查询列、条件、排序、条数上限等），再展示导出结果。
结果展示约束：默认不得总结、省略或改写脚本返回结果；仅当用户语义明确要求“总结/汇总/精简”时，才可在完整结果后追加总结。

## 命令行调用

```bash
python scripts/atomic/export_work_item.py --select-items "标题,状态" --conditions "状态=进行中"
python scripts/atomic/export_work_item.py --select-items "标题,状态" --conditions "工作项类型=用户故事 AND 工作区=业务运营"
python scripts/atomic/export_work_item.py --select-items "标识,标题,状态" --conditions "(状态=进行中 OR 状态=待处理) AND 标题 like 测试" --order-by "更新时间 desc" --limit 100
```

## 参数说明（仅字面值）

| 参数 | 必填 | 说明 |
|------|------|------|
| **--select-items, --selectItems** | 否 | 查询列，英文逗号分隔的字段名称；不传则默认列 |
| **--conditions, -c** | 否 | **条件组合**：解析出的工作区、工作项类型与状态/标题等均写入此处，支持 AND/OR、括号 `()` 组合与嵌套，如 `工作项类型=用户故事 AND 工作区=业务运营`、`工作项类型=任务 AND (状态=进行中 OR 状态=待处理) AND 优先级=P0`；不传则无过滤。详见 [query_work_item.md · 条件组合与嵌套](query_work_item.md#conditions-nested)。 |
| **--order-by, --orderBy** | 否 | 排序，如 更新时间 desc、标题 asc, 状态 desc；不传则默认按更新时间倒序 |
| **--limit, -n** | 否 | 导出条数上限，正整数，最大 10000（超过按 10000）；不传则按脚本默认分页大小（与「全部符合条件」的接口行为一致，单页不超过 10000） |
| **--flap** | 否 | false=列表导出，true=层级导出；默认 false。用户说「关联导出」「层级导出」「带关联导出」时解析为 true |
| **--user-input, --userInput** | 否 | **用户的原始输入**，必须是用户真实、完整的一句话，用于工作区/类型/字段解析的上下文与日志 |

工作区由脚本从配置或最近访问解析以确定查询目标；用户表述中的工作区、工作项类型须并入 --conditions。

## 执行流程（总分总）

参数解析 → 参数调整（解析工作区、构建查询体）→ 分页调用查询 API → 结果处理 → 格式化为 Excel → 追加心智提示。

## 心智提示

导出结果为空时，会追加：放宽条件、确认工作区与类型、确认时间或状态条件等建议。该部分须**完整保留并展示**给用户，禁止在总结或概括时删除；详见 [SKILL.md](../SKILL.md)「心智模型必须完整保留」。
