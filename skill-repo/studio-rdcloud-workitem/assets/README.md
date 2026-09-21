# Assets 配置文件目录

本目录存放技能运行时所需的配置文件。

## 文件说明

| 文件 | 说明 |
|------|------|
| `config.json` | 技能主配置文件 |

## config.json 配置项

### api 配置
- `workspace` - 默认工作区列表（多个工作区用英文逗号分隔）
- `tenant_id` - 租户 ID（默认：ZTE）
- `byPassRules` - 创建时是否绕过规则检查（1=直接提交，0=生成草稿）

### format 配置
- `as_markdown` - 是否以 Markdown 格式输出结果
- `filter_virtual_root_for_related` - 关联查询时是否过滤虚拟根节点
- `omit_large_children_for_display` - 是否省略过多的子节点
- `children_display_limit` - 子节点显示数量限制
- `one_hop_children_display_limit` - 一跳子节点显示数量限制

### create 配置
- `smart_complete` - 是否启用智能填充
- `fallback_bypass_on_error` - 创建失败时是否启用兜底（强制 byPassRules=0）
- `mr_plm_question07_no_force_false` - 特定业务配置

### log 配置
- `enabled` - 是否启用日志
- `response_body_max_length` - 响应体日志最大长度
- `max_file_size_mb` - 单个日志文件最大大小（MB）
- `max_files_per_script` - 每个脚本保留的最大日志文件数

### query 配置
- `send_notify_when_empty_result` - 查询结果为空时是否发送通知

## 修改配置

**不建议手动修改配置文件**。如需修改配置，请通过 Coclaw 对话完成，例如：

```
将 studio-rdcloud-workitem 技能中的 config.json 中的 byPassRules 设置为 0
```

## 注意事项

1. 修改配置后可能需要重启技能或重新加载配置才能生效
2. `byPassRules` 设置为 0 时，创建工作项会生成草稿单据，需要手动提交
3. 工作区配置影响查询和创建操作的目标工作区
