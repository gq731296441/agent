## 群管理：删除群

**触发**：删除群、解散群、解散群聊、把这个群删掉 等。命中即执行，勿再问用户。

> ⚠️ **高危操作，不可恢复。执行前必须完成二次确认流程。**

### 核心规则（必守）

1. **脚本**：删除群用 `{baseDir}/scripts/groupmanage_delgroup.py`，搜索群用 `{baseDir}/scripts/search_group.py`。
2. **群 URI（`--groupid`）**：
   - 群 URI 格式：`sip:xxxxxx@gm.zte.com.cn`（以 `sip:` 开头、以 `@gm.zte.com.cn` 结尾）。
   - 用户提供符合格式的内容 → 直接使用，**无需搜索**。
   - 用户提供群名称 → **必须先调用** `search_group.py --groupname "群名称"` 查询群 URI。
   - 搜索结果：0 个提示未找到；1 个直接使用；多个列出候选让用户选择。
3. **二次确认（必做）**：
   - 先以 `--check-only` 模式调用脚本获取群信息（群名称、URI、成员数量）。
   - 向用户展示群名称、群成员数量，明确提示这是**高风险不可恢复操作**，要求用户明确确认。
   - 只有用户明确回复「确认删除」「确定删除」等肯定语义后，才可执行正式删除。
   - 若用户未确认、表述含糊、改变目标群，或长时间未回复 → **停止流程，不得调用删除脚本**。
   - 正式删除时，必须额外传入 `--confirm-groupid`，其值必须与 `--groupid` 完全一致。
4. **权限**：**仅群主**有权限删除群，脚本自动校验，非群主调用时直接报错。
5. **用户信息**：脚本自动从环境变量获取，无需手动传入。

### 参数摘要

| 参数 | 必填 | 说明 |
|------|------|------|
| `--groupid` | **必填** | 目标群 URI，格式：`sip:xxx@gm.zte.com.cn` |
| `--confirm-groupid` | 正式删除必填 | 二次确认群 URI，必须与 `--groupid` 完全一致；`--check-only` 模式不需要 |
| `--check-only` | 可选 | 仅查询群信息和权限，不执行删除；用于向用户展示确认信息 |

### 调用示例

```bash
# 第一步：查询群信息，用于向用户展示二次确认内容
python "{baseDir}/scripts/groupmanage_delgroup.py" --groupid "sip:p1_20250827143555081257@gm.zte.com.cn" --check-only

# 第二步：用户明确确认后，正式删除群
python "{baseDir}/scripts/groupmanage_delgroup.py" --groupid "sip:p1_20250827143555081257@gm.zte.com.cn" --confirm-groupid "sip:p1_20250827143555081257@gm.zte.com.cn"
```

### 典型流程

**场景一：用户提供群 URI**
> 「帮我删除群 sip:p1_20250827143555081257@gm.zte.com.cn」

```bash
# Step 1：查询并向用户确认
python "{baseDir}/scripts/groupmanage_delgroup.py" --groupid "sip:p1_20250827143555081257@gm.zte.com.cn" --check-only
```
→ 脚本输出示例：
```
群名称：项目沟通群
群URI ：sip:p1_20250827143555081257@gm.zte.com.cn
群成员数量：5
```
→ 向用户展示：「即将删除群：项目沟通群，群成员数量：5。这是高风险且不可恢复操作，请确认是否删除？」

→ 用户明确确认后：
```bash
# Step 2：正式删除
python "{baseDir}/scripts/groupmanage_delgroup.py" --groupid "sip:p1_20250827143555081257@gm.zte.com.cn" --confirm-groupid "sip:p1_20250827143555081257@gm.zte.com.cn"
```

**场景二：用户提供群名称**
> 「把项目沟通群解散掉」

```bash
# 先搜索群
python "{baseDir}/scripts/search_group.py" --groupname "项目沟通群"
# 找到群 URI 后，Step 1：查询确认信息
python "{baseDir}/scripts/groupmanage_delgroup.py" --groupid "sip:p1_xxx@gm.zte.com.cn" --check-only
# 用户确认后，Step 2：正式删除
python "{baseDir}/scripts/groupmanage_delgroup.py" --groupid "sip:p1_xxx@gm.zte.com.cn" --confirm-groupid "sip:p1_xxx@gm.zte.com.cn"
```

### 返回格式与展示规则

```
群名称：项目沟通群
群URI ：sip:p1_20250827143555081257@gm.zte.com.cn
群成员数量：5
删除结果：成功
```

- 删除前必须以 `--check-only` 查询，展示**群名称、群成员数量**，要求用户二次确认。
- 若 `--check-only` 报错（如无权限），告知用户仅群主可删除群，停止流程。
- **群 URI** 为内部字段，无特殊需求不必展示给用户。
