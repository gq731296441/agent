## 群管理：创建群

**触发**：创建群、建群、新建群聊、开个群聊 等。命中即执行，勿再问用户。

### 核心规则（必守）

1. **脚本**：`python {baseDir}/scripts/groupmanage_creategroup.py`
2. **群名称（`--groupname`）**：用户明确提供时才传；用户未提及时**不传**，禁止自造名称。
3. **成员工号（`--members`）**：
   - 只接受**英文逗号分隔的纯数字工号**，**绝对禁止**传入姓名。
   - 用户只提供姓名时，必须先调用 `search_user.py` 查询工号。
   - 至少包含 **2 个工号**（脚本自动将调用者加入，群总人数至少 3 人）。
4. **用户信息**：脚本自动从环境变量 `coclaw_empno` 和 `coclaw_token` 获取，无需手动传入。

### 姓名 → 工号（仅提供姓名时）

```bash
python "{baseDir}/scripts/search_user.py" --keyword "姓名"
```

- `total=0`：提示「未找到名为 XXX 的人员，请确认姓名或提供工号」，停止流程。
- `total=1`：使用 `users[0].empNo`。
- `total>1`：列出候选人（姓名、工号、部门）让用户选择，确认后再继续。

对每个只有姓名的成员**逐一查询**，全部工号确认后再统一调用创建脚本。

### 参数摘要

| 参数 | 必填 | 说明 |
|------|------|------|
| `--groupname` | 可选 | 群名称；用户未提供时不传 |
| `--members` | **必填** | 英文逗号分隔的纯数字工号，至少 2 个；禁止传姓名 |

### 调用示例

```bash
# 仅指定成员工号（无群名称）
python "{baseDir}/scripts/groupmanage_creategroup.py" --members "08000000362565,0800000024758"

# 指定群名称和成员工号
python "{baseDir}/scripts/groupmanage_creategroup.py" --groupname "项目沟通群" --members "08000000362565,0800000024758,0800000001257"
```

### 典型流程

**场景一：用户提供工号**
> 「帮我建个群，把 08000000362565 和 0800000024758 加进来」

```bash
python "{baseDir}/scripts/groupmanage_creategroup.py" --members "08000000362565,0800000024758"
```

**场景二：用户只提供姓名**
> 「帮我建个群，把张三和李四加进来」

```bash
# 先查工号
python "{baseDir}/scripts/search_user.py" --keyword "张三"
python "{baseDir}/scripts/search_user.py" --keyword "李四"
# 工号确认后再创建
python "{baseDir}/scripts/groupmanage_creategroup.py" --members "0800000001257,08000000025754"
```

**场景三：用户提供群名称和姓名**
> 「帮我建个叫"项目沟通群"的群，成员是王五和赵六」

先查工号，确认后调用：
```bash
python "{baseDir}/scripts/groupmanage_creategroup.py" --groupname "项目沟通群" --members "工号1,工号2"
```

### 返回格式与展示规则

```
群名称：项目沟通群
群URI ：sip:+86xxxxxxxx@zte.com.cn
成功入群成员（2）：08000000362565, 0800000024758
未成功入群成员（0）：无
```

- 向用户展示群名称、成功入群成员数量和工号。
- 若存在未成功入群成员，告知用户哪些成员加入失败。
- **群 URI** 为内部字段，无特殊需求不必展示给用户。
