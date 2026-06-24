## 群管理：移除成员

**触发**：删除成员、移除成员、踢出群、把某人移出群、从群里删人 等。命中即执行，勿再问用户。

### 核心规则（必守）

1. **脚本**：移除成员用 `{baseDir}/scripts/groupmanage_delmem.py`，搜索群用 `{baseDir}/scripts/search_group.py`。
2. **群 URI（`--groupid`）**：
   - 群 URI 格式：`sip:xxxxxx@gm.zte.com.cn`（以 `sip:` 开头、以 `@gm.zte.com.cn` 结尾）。
   - 用户提供的内容符合该格式 → 直接作为 `--groupid` 传入，**无需搜索**。
   - 用户提供的是群名称 → **必须先调用** `search_group.py --groupname "群名称"` 查询群 URI。
   - 搜索结果：0 个提示未找到；1 个直接使用；多个列出候选让用户选择。
3. **成员工号（`--members`）**：
   - 只接受**英文逗号分隔的纯数字工号**，禁止传姓名。
   - 用户只提供姓名时，先调用 `search_user.py` 查询工号。
   - 至少 **1 个工号**。
4. **权限**：**仅群主**有权限删除成员，脚本自动校验，非群主调用时直接报错。
5. **用户信息**：脚本自动从环境变量获取，无需手动传入。

### 姓名 → 工号（仅提供姓名时）

```bash
python "{baseDir}/scripts/search_user.py" --keyword "姓名"
```

- `total=0`：提示未找到，停止流程。
- `total=1`：使用 `users[0].empNo`。
- `total>1`：列出候选（姓名、工号、部门）让用户选择，确认后再继续。

### 参数摘要

| 参数 | 必填 | 说明 |
|------|------|------|
| `--groupid` | **必填** | 目标群 URI，格式：`sip:xxx@gm.zte.com.cn` |
| `--members` | **必填** | 英文逗号分隔的纯数字工号，至少 1 个；禁止传姓名 |

### 调用示例

```bash
# 先搜索群（用户提供群名称时）
python "{baseDir}/scripts/search_group.py" --groupname "项目沟通群"

# 从群中删除成员（已知群 URI 和成员工号）
python "{baseDir}/scripts/groupmanage_delmem.py" --groupid "sip:p1_20260121154511551@gm.zte.com.cn" --members "08000000362565,0800000024758"
```

### 典型流程

**场景一：用户提供群 URI 和成员工号**

```bash
python "{baseDir}/scripts/groupmanage_delmem.py" --groupid "sip:p1_20260121154511551@gm.zte.com.cn" --members "08000000362565"
```

**场景二：用户提供群名称和成员工号**

```bash
python "{baseDir}/scripts/search_group.py" --groupname "项目沟通群"
# 找到唯一群后
python "{baseDir}/scripts/groupmanage_delmem.py" --groupid "sip:p1_xxx@gm.zte.com.cn" --members "08000000362565"
```

**场景三：用户提供群名称和成员姓名（并行执行）**

```bash
python "{baseDir}/scripts/search_group.py" --groupname "项目沟通群"
python "{baseDir}/scripts/search_user.py" --keyword "张三"
# 两者确认后
python "{baseDir}/scripts/groupmanage_delmem.py" --groupid "sip:p1_xxx@gm.zte.com.cn" --members "0800000001257"
```

### 返回格式与展示规则

```
群URI ：sip:p1_20260121154511551@gm.zte.com.cn
成功删除成员（2）：08000000362565, 0800000024758
```

- 向用户展示成功删除的成员数量和工号。
- 若脚本报错（如无权限），告知用户原因（仅群主可删除成员）。
- **群 URI** 为内部字段，无特殊需求不必展示给用户。
