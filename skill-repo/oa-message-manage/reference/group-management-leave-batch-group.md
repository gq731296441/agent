## 群管理：批量退出群聊

**触发**：退出这些群、把我从这些群里移除、批量退群、一次退出多个群聊 等。命中即执行，勿再问用户是否是群主。

> ⚠️ **高风险操作，执行后当前用户将从目标群中移除。执行前必须完成二次确认流程。**

### 核心规则（必守）

1. **脚本**：批量退出群用 `{baseDir}/scripts/groupmanage_leavebatchgroup.py`；按条件筛选候选群用 `{baseDir}/scripts/search_group_list.py`；按单个群名称搜索仍可使用 `{baseDir}/scripts/search_group.py`。
2. **群 URI（`--groupids`）**：
   - 批量脚本只接受多个群 URI，格式为多个 `sip:xxxxxx@gm.zte.com.cn` 以英文逗号分隔。
   - 用户提供符合格式的内容 → 直接使用，**无需搜索**。
   - 用户提供的是多个明确群名称 → **可逐个调用** `search_group.py --groupname "群名称"` 查询每个群的 URI。
   - 用户提供的是筛选条件（如群名关键字、群主工号、最后消息时间、是否有正式群名）→ **必须先调用** `search_group_list.py` 查询候选群。
   - 查询结果为 0 个：提示未找到符合条件的群；查询结果 >= 1 个：先向用户展示候选群并确认目标，再将确认后的群 URI 传给批量退群脚本。
3. **二次确认（必做）**：
   - 先以 `--check-only` 模式调用脚本获取全部目标群信息（群名称、URI、成员数量）。
   - 向用户展示待退出群数量，以及每个群的群名称、群成员数量，明确提示这是**高风险操作，将把你从这些群里移除**，要求用户明确确认。
   - 只有用户明确回复「确认退出」「确定退出」等肯定语义后，才可执行正式退群。
   - 若用户未确认、表述含糊、改变目标群，或长时间未回复 → **停止流程，不得调用退群脚本**。
   - 正式退群时，必须额外传入 `--confirm-groupids`，其值必须与 `--groupids` 完全一致。
4. **权限**：退出群聊**不需要群主权限**，只要接口返回成功即可；若接口返回当前用户不在群中或无权限等错误，按失败结果展示给用户。
5. **用户信息**：脚本自动从环境变量获取（或按脚本内部逻辑），无需手动传入。

### 参数摘要

| 参数 | 必填 | 说明 |
|------|------|------|
| `--groupids` | **必填** | 目标群 URI 列表，英文逗号分隔 |
| `--confirm-groupids` | 正式退群必填 | 二次确认群 URI 列表，必须与 `--groupids` 完全一致；`--check-only` 模式不需要 |
| `--check-only` | 可选 | 仅查询群信息，不执行退群；用于向用户展示确认信息 |

### 调用示例

```bash
# 方式一：已知多个群 URI 时，先查询群信息（用于向用户展示二次确认内容）
python "{baseDir}/scripts/groupmanage_leavebatchgroup.py" --groupids "sip:p1_demo_a@gm.zte.com.cn,sip:p1_demo_b@gm.zte.com.cn" --check-only

# 方式二：用户明确确认后，正式批量退出这些群
python "{baseDir}/scripts/groupmanage_leavebatchgroup.py" --groupids "sip:p1_demo_a@gm.zte.com.cn,sip:p1_demo_b@gm.zte.com.cn" --confirm-groupids "sip:p1_demo_a@gm.zte.com.cn,sip:p1_demo_b@gm.zte.com.cn"

# 方式三：用户先按条件筛选候选群（例如：群名包含“开发推动”，且没有正式群名）
python "{baseDir}/scripts/search_group_list.py" --groupname "开发推动" --has-group-name 0

# 方式四：从查询结果中确认目标群 URI 后，先做二次确认展示
python "{baseDir}/scripts/groupmanage_leavebatchgroup.py" --groupids "sip:rcs_demo_a@gm.zte.com.cn,sip:rcs_demo_b@gm.zte.com.cn" --check-only

# 方式五：用户确认后正式批量退出这些群
python "{baseDir}/scripts/groupmanage_leavebatchgroup.py" --groupids "sip:rcs_demo_a@gm.zte.com.cn,sip:rcs_demo_b@gm.zte.com.cn" --confirm-groupids "sip:rcs_demo_a@gm.zte.com.cn,sip:rcs_demo_b@gm.zte.com.cn"
```

### 返回格式与展示规则

```text
待退出群数量：2
成功退出群数量：2
失败群数量：0
成功[1] 群名称：项目沟通群A
成功[1] 群URI ：sip:p1_demo_a@gm.zte.com.cn
成功[1] 群成员数量：5
成功[1] 退群结果：成功
```

- 退群前必须以 `--check-only` 查询，展示**待退出群数量、每个群的群名称、群成员数量**，要求用户二次确认。
- 若 `--check-only` 报错（如某个群不存在或查询失败），告知用户具体失败群和原因，停止流程。
- 批量退群完成后，优先展示成功数量、失败数量，以及失败群原因。

