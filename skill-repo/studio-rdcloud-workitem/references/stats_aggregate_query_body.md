# 分组聚合 · 查询体构造说明

分组聚合的请求体与列表查询一样，都要带上结构正确的 `query_data`；`stats_work_item.py` 的 **`aggregate` 子命令**在此基础上再写入 `groupingKeys`、`aggregations` 等聚合字段。

**实现方式**：在 `stats_aggregate_scenario_queries.py` 中按场景拼 `query_data`，**复用** `query_my_work_item.py`、`query_overdue_work_item.py`、`query_work_item.py` 中已有的公共函数（工作区条件、类型解析、关联/树分支等），拼体顺序与对应列表脚本的 `main` 流程一致，这样聚合与列表行为对齐。

| 场景 | 构造入口 | 说明 |
|------|----------|------|
| 与我相关 | `stats_aggregate_scenario_queries.build_my_related_query_body_for_aggregate` | 与 `query_my_work_item` 主流程拼体对齐，import 其辅助函数 |
| 超期 | `stats_aggregate_scenario_queries.build_overdue_query_body_for_aggregate` | 与 `query_overdue_work_item` 主流程拼体对齐 |
| 关联/树 | `stats_aggregate_scenario_queries.build_link_query_body_for_aggregate` | 与 `query_work_item` 关联/树分支对齐 |
| 视图 | `stats_aggregate_scenario_queries.build_view_query_body_for_aggregate` | `get_view_query_config` 取 bo，去掉 `allWorkItemTypeKey` 后与聚合入口合并 |

**统一入口**：`stats_work_item.py aggregate`（首参 `aggregate`，`--group-by` 必填；场景参数见 [stats_work_item.md · 二、分组聚合](stats_work_item.md)）。

**各场景一条命令怎么写**：见 [stats_work_item.md · 各场景调用示例](stats_work_item.md#aggregate-scenarios)（通用 / 与我相关 / 超期 / 关联 / 树 / 视图）。

若列表侧拼体规则变更，需同步检查并调整上表对应 `stats_aggregate_scenario_queries` 模块，使聚合与列表仍一致。
