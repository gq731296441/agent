# Scripts/CreateCustom 定制创建脚本目录

本目录存放针对特定工作项类型的定制创建脚本。

## 目录结构

```
createCustom/
├── __init__.py                      # 包初始化文件
├── mr_common.py                     # MR 通用模块
├── xydt_bug.py                      # 星云缺陷定制创建
├── xydt_chgrequest.py               # 星云变更请求定制创建
├── xydt_common.py                   # 星云通用模块
├── xydt_shared.py                   # 星云共享模块
└── smart_fill_supplement_config.json # 智能填充补充配置
```

## 模块列表

| 模块 | 功能 | 大小 |
|------|------|------|
| `mr_common.py` | MR（变更请求）通用模块 | 21 KB |
| `xydt_bug.py` | 星云缺陷定制创建 | 23 KB |
| `xydt_chgrequest.py` | 星云变更请求定制创建 | 27 KB |
| `xydt_common.py` | 星云通用模块 | 20 KB |
| `xydt_shared.py` | 星云共享模块 | 4 KB |

## 配置文件

| 文件 | 说明 |
|------|------|
| `smart_fill_supplement_config.json` | 智能填充补充配置 |

## 定制创建场景

### xydt_bug.py - 星云缺陷
针对星云平台的缺陷类型提供定制创建逻辑：
- 特殊字段处理
- 必填字段校验
- 工作流适配
- 业务规则检查

### xydt_chgrequest.py - 星云变更请求
针对星云平台的变更请求类型提供定制创建逻辑：
- 变更流程适配
- 影响分析字段
- 审批链配置
- 关联关系处理

### mr_common.py - MR 通用模块
变更请求相关的通用逻辑：
- MR 字段解析
- MR 状态流转
- MR 关联处理

### xydt_common.py - 星云通用模块
星云平台的通用逻辑：
- 平台特定 API 封装
- 星云字段映射
- 星云业务规则

### xydt_shared.py - 星云共享模块
星云平台共享的工具函数：
- 共享常量
- 工具函数
- 配置项

## 定制创建映射

定制创建映射配置在 `common/create_custom_map.json` 中定义：

```json
{
  "create_custom_map": [
    {
      "workspace": "星云",
      "work_item_type_key": "Bug",
      "module": "xydt_bug"
    },
    {
      "workspace": "星云",
      "work_item_type_key": "ChangeRequest",
      "module": "xydt_chgrequest"
    }
  ]
}
```

## 智能填充配置

`smart_fill_supplement_config.json` 定义智能填充的补充规则：
- 字段填充优先级
- 历史数据匹配规则
- 特殊字段处理

## 何时使用定制创建

1. **特殊业务规则**：标准创建逻辑不满足业务需求
2. **平台差异**：不同平台有不同的字段和流程
3. **复杂校验**：需要额外的业务校验
4. **流程适配**：适配特定的工作流

## 开发定制脚本的原则

1. **继承默认**：优先使用默认创建逻辑，仅覆盖特殊部分
2. **配置驱动**：通过配置而非硬编码实现差异
3. **向后兼容**：保持接口稳定，不影响现有功能
4. **充分测试**：定制脚本需要充分测试验证

## 调用流程

```
用户请求创建
    ↓
检查是否有定制映射
    ↓
有 → 调用定制创建脚本
    ↓
无 → 调用默认创建逻辑 (create_work_item.py)
```

## 相关文档

- [原子脚本](../atomic/README.md) - 核心创建脚本
- [公共模块](../common/README.md) - 公共模块说明
- [配置说明](../../assets/README.md) - 配置文件说明
