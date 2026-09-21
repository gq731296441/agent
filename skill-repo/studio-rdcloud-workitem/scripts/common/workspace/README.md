# Scripts/Common/Workspace 工作区模块目录

本目录存放与工作区相关的公共模块。

## 目录结构

```
workspace/
└── workspace_resolver.py    # 工作区解析模块
```

## 模块功能

### workspace_resolver.py
工作区解析模块，提供：
- 工作区名称解析
- 工作区匹配
- 多工作区支持
- 默认工作区选择

## 主要函数

| 函数 | 功能 |
|------|------|
| `resolve_workspace()` | 解析工作区名称 |
| `match_workspace()` | 匹配工作区 |
| `get_default_workspace()` | 获取默认工作区 |

## 使用场景

1. **创建工作项**：确定创建到哪个工作区
2. **查询工作项**：确定在哪个工作区查询
3. **更新工作项**：从工作项 ID 解析工作区
4. **字段解析**：不同工作区的字段定义可能不同

## 工作区配置

工作区配置在 `assets/config.json` 中定义：

```json
{
  "api": {
    "workspace": "PLM,CT,iChangesj,ZXMPH001"
  }
}
```

支持多个工作区，用英文逗号分隔。

## 使用示例

```python
from common.workspace.workspace_resolver import resolve_workspace

# 解析工作区
workspace = resolve_workspace("PLM", headers)

# 匹配工作区
workspace = match_workspace(user_input, available_workspaces)
```

## 工作区匹配规则

1. **精确匹配**：用户输入与工作区名称完全匹配
2. **模糊匹配**：用户输入包含工作区名称关键词
3. **默认选择**：未指定时使用配置中的第一个工作区
4. **上下文推断**：根据工作项 ID 等上下文推断

## 支持的工作区

| 工作区 | 说明 |
|--------|------|
| PLM | 产品生命周期管理 |
| CT | 通用技术 |
| iChangesj | 变更管理 |
| ZXMPH001 | 特定项目 |

## 相关文档

- [公共模块](../README.md) - 公共模块总览
- [原子脚本](../atomic/README.md) - 核心功能脚本
- [配置说明](../../assets/README.md) - 配置文件说明
