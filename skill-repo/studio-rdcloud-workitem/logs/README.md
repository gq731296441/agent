# Logs 日志目录

本目录用于存放技能运行时生成的日志文件。

## 日志文件

日志文件按日期和脚本类型自动生成，命名格式：

```
{脚本名}_{日期}.log
```

例如：
- `create_work_item_20240331.log` - 创建工作项的日志
- `update_work_item_20240331.log` - 更新工作项的日志
- `query_work_item_20240331.log` - 查询工作项的日志

## 日志配置

日志行为由 `assets/config.json` 中的 `log` 配置控制：

```json
{
  "log": {
    "enabled": true,                    // 是否启用日志
    "response_body_max_length": 500,    // 响应体日志最大长度
    "max_file_size_mb": 50,             // 单个日志文件最大大小（MB）
    "max_files_per_script": 10          // 每个脚本保留的最大日志文件数
  }
}
```

## 日志内容

日志包含以下信息：
- 请求参数
- API 调用详情
- 响应结果
- 错误信息
- 执行时间

## 注意事项

1. 日志目录为空时，首次执行操作会自动创建
2. 日志文件会定期清理，保留最近的文件
3. 日志可能包含敏感信息，请妥善保管
4. 排查问题时可提供相关日志给开发人员

## 查看日志

```bash
# 查看最新日志
ls -lt logs/ | head

# 查看特定日志文件
cat logs/create_work_item_20240331.log
```
