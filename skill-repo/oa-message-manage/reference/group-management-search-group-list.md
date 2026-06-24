## 群管理：按条件查询批量操作候选群

**适用场景**：当用户要**解散群**或**退出群**或**批量解散群**或**批量退出群聊**，但没有直接提供群 URI，而是提供筛选条件时，先用本步骤查询候选群。

### 触发特征

用户表述中包含以下任一条件时，优先使用本查询：

- **群名称关键字**：如「群名里带开发推动的群」
- **群主工号**：如「群主是 10145777 的群」
- **最后消息时间**：如「最近 30 天没消息的群」「近 1 年没动静的群」
- **是否有正式群名**：如「没有正式名称的群」「有正式群名的群」

### 脚本

使用 `{baseDir}/scripts/search_group_list.py`。

### 参数映射

| 用户条件 | 脚本参数 | 说明 |
|------|------|------|
| 群名称包含 XXX | `--groupname "XXX"` | 按群名称关键字过滤 |
| 群主是 10145777 | `--adminid "10145777"` | 按群主工号过滤 |
| 最近 30 天没有消息 | `--inactive-days 30` | 只查最近 30 天没有新消息的群 |
| 没有正式群名 | `--has-group-name 0` | 只查没有正式群名的群 |
| 有正式群名 | `--has-group-name 1` | 只查有正式群名的群 |
| 不按正式群名过滤 | 不传或 `--has-group-name 2` | 默认行为 |
| 解散/批量解散| `--adminid "${当前用户工号}"` | 自动传入当前用户工号，查询该用户是群主的全部群聊列表 |

> 注意：脚本至少需要 1 个查询条件，避免直接拉取全部群列表。

### 调用示例

```bash
# 按群名称关键字查询
python "{baseDir}/scripts/search_group_list.py" --groupname "开发推动"

# 按群主 + 正式群名过滤
python "{baseDir}/scripts/search_group_list.py" --adminid "10145777" --has-group-name 1

# 查询最近 30 天没有消息、且没有正式群名的群
python "{baseDir}/scripts/search_group_list.py" --inactive-days 30 --has-group-name 0

# 按最近 1 年未活跃 + 群主 + 是否有正式群名组合筛选
python "{baseDir}/scripts/search_group_list.py" --inactive-years 1 --adminid "10145777" --has-group-name 1

# 解散/批量解散 当前用户工号为群主，查询该用户是群主的全部群聊列表
python "{baseDir}/scripts/search_group_list.py" --adminid "${当前用户工号}"
```

### 输出说明

```text
匹配群数量：2
1. 群名称：开发推动呵呵
   正式群名：开发推动呵呵
   默认群名：王鹤朝,温怡,林明东...
   群主工号：10145777
   最后一条消息时间戳：173561454554
   群URI ：sip:rcs_xxx@gm.zte.com.cn
```

### 后续处理规则

1. 若结果为 0 个：直接告知用户未找到符合条件的群，停止后续批量操作。
2. 若结果 >= 1 个：必须向用户展示全部所有候选群列表，不能预览、展示部分群列表，然后确认这些群是否就是要批量解散/退出的目标。
3. 用户确认目标群后，再把候选群的 `群URI` 组装成英文逗号分隔字符串，传给对应批量脚本：
   - 批量删除群：`groupmanage_delbatchgroup.py`
   - 批量退出群聊：`groupmanage_leavebatchgroup.py`
4. 本步骤只负责查询，不执行删除或退群。
