# Scripts/Atomic 原子脚本目录

本目录存放技能的核心功能脚本，每个脚本完成单一明确的功能。

## 脚本列表

| 脚本 | 功能 |
|------|------|
| `create_work_item.py` | 创建工作项（可选创建后关联） |
| `update_work_item.py` | 更新工作项（可选更新后关联） |
| `relate_work_item.py` | 关联工作项（将两个或以上工作项建立关联） |
| `query_work_item.py` | 查询工作项（通用） |
| `query_my_work_item.py` | 查询与我相关 |
| `query_overdue_work_item.py` | 查询我超期的工作项 |
| `get_work_item_detail.py` | 工作项详情 |
| `export_work_item.py` | 工作项导出 |
| `stats_work_item.py` | 工作项统计与分组聚合（首参 `aggregate` 为服务端分组） |
| `stats_aggregate_scenario_queries.py` | 供 `stats_work_item.py aggregate` 按场景拼 `query_data`（非独立入口） |
| `query_comment.py` | 查询工作项评论（树形） |
| `add_comment.py` | 添加工作项评论或回复 |
| `copy_work_item.py` | 基于源工作项复制新工作项（可覆盖字段/建关联/通知） |

## 配置文件

| 文件 | 说明 |
|------|------|
| `query_overdue_work_item_config.json` | 超期查询配置文件 |

## 运行方式

在技能根目录下运行：

```bash
python scripts/atomic/create_work_item.py --work-item-type-name 任务 --fields '[{"key":"标题","value":"测试"}]'
```

每个脚本都支持 `--help` 查看帮助：

```bash
python scripts/atomic/create_work_item.py --help
```

## 结果展示约束

**默认不得总结、省略或改写脚本返回结果**；仅当用户语义明确要求"总结/汇总/精简"时，才可在完整结果后追加总结。

## 工具约束

- **必须**使用本技能已有 `scripts/` 脚本执行研发云工作项能力
- **禁止**使用 write、edit、web-search、web_fetch 等处理本业务或替代脚本
- **禁止**新建 Python 脚本

## 依赖说明

所有脚本依赖 `common/` 目录下的公共模块：
- 认证：`auth_helper.py`
- 配置：`config_helper.py`
- 日志：`log_helper.py`
- API 封装：`rdcloud_api.py`
- 字段解析：`field_resolver.py`、`field_value_resolver.py`
- 通知：`notify_helper.py`

## 相关文档

- [技术参考](../../references/README.md) - 详细的参数说明
- [用户文档](../../docs/README.md) - 面向用户的使用指南
- [公共模块](../common/README.md) - 公共模块说明
