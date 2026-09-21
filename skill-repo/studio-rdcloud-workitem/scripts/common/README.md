# Scripts/Common 公共模块目录

本目录存放供原子脚本和组合能力复用的公共模块。

## 模块列表

| 模块 | 职责 |
|------|------|
| `auth_helper.py` | 认证头、人员→工号解析 |
| `config_helper.py` | 配置加载 |
| `log_helper.py` | 日志初始化与 logger |
| `notify_helper.py` | 邮件、iCenter 消息发送 |
| `rdcloud_api.py` | 研发云接口封装 |
| `user_search.py` | 人员搜索 |
| `sql_query_parser.py` | SQL 模式解析、查询体构建 |
| `field_resolver.py` | 字段名解析（中文→系统 key） |
| `field_value_resolver.py` | 字段值解析和转换 |
| `work_item_type_resolver.py` | 工作项类型解析 |
| `mindset_messages.py` | 心智模型消息 |
| `work_item_hierarchy.py` | 工作项层级关系 |

## 子目录

| 目录 | 说明 |
|------|------|
| `workspace/` | 工作区解析等 |

## 配置文件

| 文件 | 说明 |
|------|------|
| `field_resolver_config.json` | 字段解析配置 |
| `field_work_item_type_resolver_config.json` | 工作项类型解析配置 |
| `create_custom_map.json` | 定制创建映射 |

## 使用方式

原子脚本通过 `from common.xxx import ...` 使用公共模块：

```python
from common.auth_helper import get_auth_headers
from common.config_helper import load_base_config
from common.field_resolver import resolve_field_key
```

## 结果展示约束

**默认不得总结、省略或改写脚本返回结果**；仅当用户语义明确要求"总结/汇总/精简"时，才可在完整结果后追加总结。

## 工具约束

- **必须**使用本技能已有 `scripts/` 脚本执行研发云工作项能力
- **禁止**使用 write、edit、web-search、web_fetch 等处理本业务或替代脚本
- **禁止**新建 Python 脚本

## 相关文档

- [原子脚本](../atomic/README.md) - 核心功能脚本
- [技术参考](../../references/README.md) - 脚本参数说明
- [用户文档](../../docs/README.md) - 面向用户的使用指南
