## 群管理：最近群查询（recv/atMe/enter）

**触发**：获取最近相关群聊，支持三种模式：
- 最近给我发送消息的群（`mode=recv`）
- 最近 @ 我的群（`mode=atMe`）
- 最近看过/进入过的群（`mode=enter`）

## 技能概述

**工具位置**：
- 脚本路径：`{baseDir}/scripts/groupmanage_search_top_group.py`

其中 `{baseDir}` 是技能安装目录的绝对路径。

**功能特点**：
- 支持按时间范围查询（`--start/--end` 或 `--day`）
- 支持多模式查询：`recv` / `atMe` / `enter`
- 支持过滤免扰群（`--ignoreMute`）
- 支持过滤 `@所有人` 消息（`--ignoreAtAll`）
- 支持限制返回结果数量（`--topn`）
- 返回每个群的管理员工号、群名称、群 URI

## 参数说明

### 用户参数映射

| 用户自然语言 | 脚本参数 | 说明 | 默认值 |
|-------------|---------|------|--------|
| 时间范围 | `--start` `--end` | 日期格式 YYYY-MM-DD | 必填（或使用--day） |
| 往前天数 | `--day` | 1-7天，与start/end互斥 | - |
| 免扰过滤 | `--ignoreMute` | 1=忽略免扰群，0=包含免扰群 | 0（包含） |
| @所有人过滤 | `--ignoreAtAll` | 1=忽略@消息，0=包含@消息 | 0（包含） |
| 返回数量限制 | `--topn` | 正整数，限制返回群数 | 无限制 |
| 查询模式 | `--mode` | `recv`=最近给我发消息，`atMe`=最近@我，`enter`=最近看过 | summary |

### 时间参数优先级
1. 同时指定 `--start` 和 `--end` → 使用指定范围
2. 指定 `--day` → 自动计算为最近N天（结束日期为今天）
3. 未指定以上任何参数 → 报错

### 模式说明
- `recv`：统计最近给我发送消息的群
- `atMe`：统计最近出现@我的群（包含与@相关的活跃群）
- `enter`：统计最近被我查看/进入过的群

### 输出格式

成功时返回JSON格式（有数据）：
```json
{
  "success": true,
  "message": "Success",
  "total": 2,
  "groups": [
    {
      "adminID": "管理员工号",
      "groupName": "群名称",
      "groupUri": "sip:group_xxx@zte.com.cn"
    }
  ]
}
```

成功但无数据时：
```json
{
  "success": true,
  "message": "无可推送群组",
  "total": 0,
  "groups": []
}
```

失败时（参数/请求异常，示例）：
```json
{
  "success": false,
  "error": "INVALID_DATE",
  "message": "日期格式不正确，支持 YYYY-MM-DD / YYYYMMDD / YYYYMMDDHHMMSS"
}
```

失败时（接口业务失败，示例）：
```json
{
  "success": false,
  "message": "获取TOP群组失败"
}
```

## 快速调用参考

**调用流程**：
1. Agent解析用户自然语言，提取时间范围、TOPN、免扰过滤等参数
2. 自动补全时间参数（如用户只说"最近7天"则计算--start/--end）
3. 执行：`python {baseDir}/scripts/groupmanage_search_top_group.py [参数]`
4. 解析JSON输出，格式化展示给用户

**典型调用示例**：

查询最近7天收到消息的群（`recv`，过滤免扰）：
```bash
python {baseDir}/scripts/groupmanage_search_top_group.py --mode recv --day 7 --ignoreMute 1
```

查询最近7天 @ 我的群（`atMe`）：
```bash
python {baseDir}/scripts/groupmanage_search_top_group.py --mode atMe --day 7
```

查询最近3天看过的群（`enter`）：
```bash
python {baseDir}/scripts/groupmanage_search_top_group.py --mode enter --day 3
```

查询指定日期范围，返回前20个群（可用于任意模式）：
```bash
python {baseDir}/scripts/groupmanage_search_top_group.py --mode atMe --start 2026-03-01 --end 2026-04-01 --topn 20
```

查询包含免扰群，不限制数量：
```bash
python {baseDir}/scripts/groupmanage_search_top_group.py --mode recv --day 3 --ignoreMute 0
```


## 注意事项

1. **时间范围必填**：必须指定 `--start + --end`，或指定 `--day`
2. **按需求选择模式**：`recv`（给我发消息）、`atMe`（@我）、`enter`（看过）
3. **脚本默认值**：`ignoreMute=0`、`ignoreAtAll=0`、`mode=summary`
4. **日期格式兼容**：支持 `YYYY-MM-DD` / `YYYYMMDD` / `YYYYMMDDHHMMSS`
5. **返回字段说明**：脚本返回 `adminID/groupName/groupUri`，不返回消息数
6. **空结果语义**：无匹配群时返回 `success=true` 且 `groups=[]`

---
