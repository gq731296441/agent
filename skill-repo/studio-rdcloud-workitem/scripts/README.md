# Scripts 脚本代码目录

本目录存放技能的所有 Python 脚本代码，是技能的核心实现部分。

## 目录结构

```
scripts/
├── atomic/              # 原子脚本（核心功能）
├── common/              # 公共模块和工具类
├── composite/           # 复合脚本（组合功能）
├── createCustom/        # 定制创建脚本
└── tests/               # 测试脚本
```

## 各子目录说明

| 目录 | 说明 | 详细内容 |
|------|------|----------|
| **atomic/** | 原子脚本 | 核心功能脚本，每个脚本完成单一明确的功能 |
| **common/** | 公共模块 | 工具类、辅助函数、配置解析、认证、日志等 |
| **composite/** | 复合脚本 | 组合多个原子能力的脚本 |
| **createCustom/** | 定制创建 | 针对特定工作项类型的定制创建逻辑 |
| **tests/** | 测试脚本 | 单元测试、集成测试脚本 |

## 原子脚本列表 (atomic/)

| 脚本 | 功能 |
|------|------|
| `create_work_item.py` | 创建工作项 |
| `update_work_item.py` | 更新工作项 |
| `query_work_item.py` | 查询工作项（通用） |
| `query_my_work_item.py` | 查询与我相关的工作项 |
| `query_overdue_work_item.py` | 查询超期的工作项 |
| `query_work_item_by_url.py` | 根据链接/查询名查询 |
| `get_work_item_detail.py` | 查询工作项详情 |
| `relate_work_item.py` | 关联工作项 |
| `export_work_item.py` | 导出工作项 |
| `stats_work_item.py` | 统计工作项 |
| `query_comment.py` | 查询工作项评论 |
| `add_comment.py` | 添加/回复工作项评论 |

## 公共模块列表 (common/)

| 模块 | 功能 |
|------|------|
| `auth_helper.py` | 认证辅助（获取 token、工号等） |
| `config_helper.py` | 配置加载和解析 |
| `log_helper.py` | 日志工具 |
| `field_resolver.py` | 字段名解析（中文→系统 key） |
| `field_value_resolver.py` | 字段值解析和转换 |
| `work_item_type_resolver.py` | 工作项类型解析 |
| `rdcloud_api.py` | 研发云 API 封装 |
| `notify_helper.py` | 通知辅助（邮件、iCenter 消息） |
| `user_search.py` | 用户搜索 |
| `sql_query_parser.py` | SQL 查询解析器 |
| `mindset_messages.py` | 心智模型消息 |
| `work_item_hierarchy.py` | 工作项层级关系 |
| `workspace/` | 工作区相关模块 |

## 调用脚本

所有原子脚本都支持命令行调用，通用格式：

```bash
python scripts/atomic/{脚本名}.py --参数 值
```

每个脚本都支持 `--help` 查看帮助：

```bash
python scripts/atomic/create_work_item.py --help
```

## 开发规范

1. **单一职责**：每个原子脚本只完成一个明确的功能
2. **参数统一**：使用 `--user-input` 传递用户原始输入
3. **日志规范**：使用 `log_helper` 统一日志格式
4. **错误处理**：捕获异常并返回友好的错误信息
5. **编码规范**：所有文件使用 UTF-8 编码

## 测试

测试脚本位于 `tests/` 目录：

```bash
# 运行原子脚本测试
python scripts/tests/atomic/test_create_work_item.py

# 运行公共模块测试
python scripts/tests/common/test_field_resolver.py
```

## 相关文档

- [用户文档](../docs/README.md) - 面向用户的使用指南
- [技术参考](../references/README.md) - 脚本参数和调用示例
- [设计文档](../docs/DESIGN.md) - 整体设计说明
