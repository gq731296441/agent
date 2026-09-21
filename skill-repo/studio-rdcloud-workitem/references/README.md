# References 技术参考目录

本目录存放面向开发者和维护者的技术参考文档，详细描述各脚本的入参、出参、使用方式等。

## 文档列表

| 文档 | 说明 |
|------|------|
| [create_work_item.md](create_work_item.md) | 创建工作项脚本参考 |
| [update_work_item.md](update_work_item.md) | 更新工作项脚本参考 |
| [query_work_item.md](query_work_item.md) | 查询工作项脚本参考 |
| [query_my_work_item.md](query_my_work_item.md) | 与我相关查询脚本参考 |
| [query_overdue_work_item.md](query_overdue_work_item.md) | 超期查询脚本参考 |
| [query_work_item_by_url.md](query_work_item_by_url.md) | 根据链接/查询名查询脚本参考 |
| [get_work_item_detail.md](get_work_item_detail.md) | 查询工作项详情脚本参考 |
| [relate_work_item.md](relate_work_item.md) | 关联工作项脚本参考 |
| [copy_work_item.md](copy_work_item.md) | 复制工作项脚本参考 |
| [export_work_item.md](export_work_item.md) | 导出工作项脚本参考 |
| [stats_work_item.md](stats_work_item.md) | 工作项统计与分组聚合（`stats_work_item.py` / `aggregate` 子命令） |
| [stats_aggregate_query_body.md](stats_aggregate_query_body.md) | 分组聚合请求体各场景构造说明（`stats_aggregate_scenario_queries.py`） |
| [work_item_comment.md](work_item_comment.md) | 工作项评论脚本参考 |
| [query_sql_workflow.md](query_sql_workflow.md) | SQL 查询工作流参考 |

## 文档内容

每个参考文档包含以下内容：

1. **脚本说明** - 脚本的功能描述
2. **入参说明** - 命令行参数详解
3. **出参说明** - 返回结果格式
4. **调用示例** - 典型使用场景的调用示例
5. **注意事项** - 使用时的注意事项

## 典型调用示例

```bash
# 创建工作项
python scripts/atomic/create_work_item.py \
  --work-item-type-name 任务 \
  --fields '[{"key":"标题","value":"示例标题"}]'

# 更新工作项
python scripts/atomic/update_work_item.py \
  --work-item-id PLM-12345 \
  --fields '[{"key":"状态","value":"进行中"}]'

# 查询工作项
python scripts/atomic/query_work_item.py \
  --select-items "标题，状态" \
  --conditions "状态=进行中"

# 与我相关查询
python scripts/atomic/query_my_work_item.py \
  --select-items "标题，状态" \
  --query-type assigned_to_me
```

## 与用户文档的区别

| 方面 | 用户文档 (docs/) | 技术参考 (references/) |
|------|-----------------|----------------------|
| **目标读者** | 最终用户 | 开发者、维护者 |
| **内容重点** | 如何用自然语言表达 | 如何调用脚本和参数 |
| **语言风格** | 通俗易懂 | 技术术语 |
| **示例形式** | 对话话术 | 命令行调用 |

## 何时查阅

- **开发新功能**：参考现有文档的格式和内容
- **调试问题**：查看脚本的入参和出参说明
- **了解实现**：深入理解脚本的工作方式
- **维护技能**：修改配置或优化逻辑时参考
