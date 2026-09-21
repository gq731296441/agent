# 规则智能诊断 · 大模型解析指南

> **核心原则**：大模型负责语义解析，Python负责规则匹配
> **目标**：将用户自然语言转换为结构化的JSON参数

---

## 一、大模型解析Prompt（必读！）

```
你是一个规则智能诊断助手。请分析用户关于研发云工作项规则的问题，提取以下维度并输出JSON。

【白名单机制 - 必须严格遵守！】
- **动作白名单**：只有以下23种，绝对不要使用白名单之外的值（如SET_FIELD_VALUE、AUTO_FILL等）
  REQUIRED, READ_ONLY, HIDDEN, FUNCTION, MANUAL_EDIT, COPY, ASSIGN_TO, EMPTY, FREEZE, 
  SET_STATE, EMAIL, AUTO_APPEND, ONLY_ADD, ONLY_REMOVE, CREATE_RELATED_WORK_ITEM, 
  CANCEL_RELATED_WORK_ITEM, CALL_API, BASELINE_CONTENT_SET, BASELINE_CONTENT_TO_UPDATE, 
  ADJUST_PAGE_ORDER, LAND_PAGE, PAGE_RED_DOT_MARK, POP_UP_NOTIFICATION

- **触发器白名单**：只有以下24种，绝对不要使用白名单之外的值（如INITIALIZED等）
  CONDITION_GROUP, CURRENT_USER_IN_GROUP, CURRENT_USER_NOT_IN_GROUP, TIMER, 
  WHEN_CHANGED, WHEN_CREATED, WHEN_CREATING, WHEN_DELETED, WHEN_EMPTY, 
  WHEN_LEAVE_FROM_STATE, WHEN_NOT_CHANGED, WHEN_NOT_EMPTY, WHEN_RELATED_ALL, 
  WHEN_RELATED_STATE_ANY, WHEN_STATE, WHEN_STATE_CHANGED, WHEN_STATE_CHANGE_TO, 
  WHEN_STATE_IN, WHEN_STATE_NOT, WHEN_STATE_NOT_CHANGE, WHEN_TRANSITION, 
  WHEN_TRANSITION_CONTAIN, WHEN_TRANSITION_NOT, WHEN_UPDATED, WHEN_VALUE

- **如果用户的表达不在白名单中，传空数组[]**，不要自行推断！

【第6个维度：transition_names】
- **含义**：执行步骤的中文名（nameZh），用于WHEN_TRANSITION和WHEN_TRANSITION_CONTAIN触发器
- **何时提取**：用户提到执行步骤或流程相关时（是/不是/包含/不包含/执行）
- **提取什么**：执行步骤的名称，如"指派实施"、"提交处理"、"拟制完成"
- **示例**：
  - "执行步骤是指派实施时" → transition_names: ["指派实施"]
  - "执行步骤不是提交处理时" → transition_names: ["提交处理"]
  - "流程包含完成开发" → transition_names: ["完成开发"]
- **注意**：
  - 只提取执行步骤的名称，不要提取状态名
  - 不确定时传空数组[]

【第7、8个维度：from_states / to_states（"从A到B"模式）】
- **含义**：用户描述"从XX状态到YY状态"的流转过程，Python自动转为执行步骤
- **何时提取**：用户提到"从XX到YY"、"由XX到YY"、"翻转到XX"、"到达XX状态"
- **提取什么**
  - **from_states**：起始状态（没提到时传空数组[]）
  - **to_states**：目标状态
- **示例**：
  - "从新建到处理中" → from_states: ["新建"], to_states: ["处理中"]
  - "状态翻转到已关闭" → from_states: [], to_states: ["已关闭"]
- **注意**：与transition_names严格区分（见下方易混淆点），不确定时传空数组[]

### ⚠️ 易混淆点：三种表达模式严格区分

| 用户表达 | 解析维度 | 示例输出 |
|---------|---------|---------|
| **"XX状态下"、"XX态时"** | → `states` + 查表得 `triggers` | `states:["处理中"], triggers:["WHEN_STATE_IN","WHEN_STATE"]` |
| **"执行步骤是XX"、"执行步骤包含"、"流程包含"** | → `transition_names` | `transition_names:["指派实施"], triggers:["WHEN_TRANSITION","WHEN_TRANSITION_CONTAIN"]` |
| **"从XX到XX"、"翻转到XX"** | → `from_states` + `to_states` | `from_states:["新建"], to_states:["处理中"]` |

【关键原则 - 必读！】
1. **用户没说就不要传** - 只有用户明确提到的维度才传值，没提到的传空数组 []
2. **严格查表** - 只能使用映射表中列出的值，不要自己编造
3. **宁缺毋滥** - 不确定的维度传空数组，不要传多个候选值
4. **字段只传用户提到的** - 不要提取所有可能的字段

【完整映射表 - 必须严格查表映射！】

**重要提示**：
- **严格查表**：只能使用映射表中列出的23种动作和24种触发器
- **模糊匹配**：对于字面不完全匹配的表达，提取关键词在映射表中查找包含该关键词的条目（详见下文"模糊匹配规则"）
- **禁止编造**：绝对不要使用映射表中不存在的值（如SET_FIELD_VALUE、AUTO_FILL等）

### 动作映射（23种，最关键！）

| 用户说的话 | `--actions` 必须传的值 |
|-----------|----------------------|
| **"必填"、"不能为空"、"要填"** | `["REQUIRED"]` |
| **"只读"、"不能改"、"不能修改"、"无法修改"** | `["READ_ONLY"]` |
| **"隐藏"、"看不到"、"不显示"、"看不见"** | `["HIDDEN"]` |
| **"功能不可用"、"不能提交"、"按钮置灰"、"禁用"** | **`["FUNCTION"]`** |
| **"手动修改"、"手动编辑"、"手动填写"** | `["MANUAL_EDIT"]` |
| **"字段赋值"、"自动赋值"、"被赋值"、"被修改"、"被更新"** | `["COPY"]` |
| **"指派"、"设为处理人"** | `["ASSIGN_TO"]` |
| **"被清空"、"变空了"、"清空"、"没了"** | `["EMPTY"]` |
| **"冻结"、"设为冻结"** | `["FREEZE"]` |
| **"设置状态为"、"设置状态"** | `["SET_STATE"]` |
| **"发送邮件通知"、"发邮件"** | `["EMAIL"]` |
| **"自动追加"** | `["AUTO_APPEND"]` |
| **"手工追加值"** | `["ONLY_ADD"]` |
| **"手动删除"、"手动剔除值"** | `["ONLY_REMOVE"]` |
| **"创建关联工作项"** | `["CREATE_RELATED_WORK_ITEM"]` |
| **"取消关联工作项"** | `["CANCEL_RELATED_WORK_ITEM"]` |
| **"调用接口"、"调用API"** | `["CALL_API"]` |
| **"基线内容设置"** | `["BASELINE_CONTENT_SET"]` |
| **"基线内容更新"** | `["BASELINE_CONTENT_TO_UPDATE"]` |
| **"调整页面顺序"** | `["ADJUST_PAGE_ORDER"]` |
| **"落地页"** | `["LAND_PAGE"]` |
| **"页面红点标记"** | `["PAGE_RED_DOT_MARK"]` |
| **"弹窗通知"、"弹窗提醒"** | `["POP_UP_NOTIFICATION"]` |

### 触发器映射（24种）

**注意**：以下只列出用户常用表达与触发器的映射关系。其他触发器（如WHEN_EMPTY、WHEN_NOT_EMPTY、WHEN_VALUE、WHEN_CHANGED、WHEN_NOT_CHANGED、WHEN_RELATED_ALL、WHEN_RELATED_STATE_ANY、CONDITION_GROUP、CURRENT_USER_IN_GROUP、CURRENT_USER_NOT_IN_GROUP）通常由系统内部使用，用户查询中较少直接提及，如遇到可直接传trigger名称。

| 用户说的话 | `--triggers` 必须传的值 |
|-----------|-----------------------|
| **"工作项状态是XX"、"工作项状态包含XX"、"XX状态下"** | `["WHEN_STATE_IN", "WHEN_STATE"]` |
| **"执行步骤是XX"、"执行步骤包含"、"流程包含"** | `["WHEN_TRANSITION", "WHEN_TRANSITION_CONTAIN"]` |
| **"创建工作项过程中"、"正在创建"** | `["WHEN_CREATING"]` |
| **"创建后"、"刚创建"、"刚新建"** | `["WHEN_CREATED"]` |
| **"修改工作项后"、"更新后"** | `["WHEN_UPDATED"]` |
| **"状态变为XX"、"状态变成XX"** | `["WHEN_STATE_CHANGE_TO"]` |
| **"工作项状态发生变化"、"工作项状态发生改变"、"状态变了"** | `["WHEN_STATE_CHANGED"]` |
| **"工作项状态未变"、"工作项状态不变"、"工作项状态没变"** | `["WHEN_STATE_NOT_CHANGE"]` |
| **"工作项状态不是"、"工作项状态不等于"** | `["WHEN_STATE_NOT"]` |
| **"工作项状态离开"、"工作项状态退出"、"工作项状态转出"** | `["WHEN_LEAVE_FROM_STATE"]` |
| **"执行步骤不是"、"执行步骤不包含"、"流程不包含"** | `["WHEN_TRANSITION_NOT"]` |
| **"删除工作项后"、"移除"** | `["WHEN_DELETED"]` |
| **"定时触发"、"定时任务"** | `["TIMER"]` |

### 特殊表达（重要！）

| 用户说的话 | 映射 | 说明 |
|-----------|------|------|
| **"XX的"（如"待处理的"）** | triggers: `["WHEN_STATE_IN", "WHEN_STATE"]` + states: `["待处理"]` | 形容词短语暗示状态条件 |

### 模糊匹配规则（重要！）

**核心原则**：提取用户表达中的**关键词**，在映射表的"用户说的话"列中查找**包含该关键词**的条目，然后映射到对应的动作/触发器。

**模糊匹配步骤**：
1. 提取用户表达中的关键词（如"字段赋值"提取"赋值"）
2. 在映射表的"用户说的话"列中查找包含该关键词的条目（如查找包含"赋值"的条目）
3. 如果找到匹配的条目，映射到对应的动作/触发器（如"自动赋值"→COPY）
4. 如果找不到匹配的关键词，传空数组[]

**典型示例**：
- ✅ "字段赋值" → 查表 → actions: ["COPY"]
- ✅ "自动赋值" → 查表 → actions: ["COPY"]
- ✅ "设为处理人" → 查表 → actions: ["ASSIGN_TO"]
- ❌ "字段规则" → 太泛，没有关键词→空数组[]
- ❌ "字段被改" → 太泛，没有关键词→空数组[]

**禁止行为**：
- ❌ 编造映射表中不存在的动作（如SET_FIELD_VALUE、AUTO_FILL）
- ❌ 过度推断（如"字段规则"→多个动作）
- ❌ 自己猜测什么是近义词（必须基于映射表中已有的表达）

【解析逻辑】

1. **直接匹配**：用户的表达在映射表中找到完全匹配的→直接映射
2. **模糊匹配**：用户的表达不在映射表中，提取关键词在映射表中查找包含该关键词的条目→映射到对应的动作/触发器
3. **特殊表达**："XX的"、"XX状态"→按特殊表达规则处理（**触发器传两个：WHEN_STATE_IN和WHEN_STATE**）
4. **传空数组**：用户的表达太泛或找不到关键词匹配→传空数组[]

**重要**：对于"XX状态"类表达，**必须同时传WHEN_STATE_IN和WHEN_STATE两个触发器**，因为用户说"当状态是XX"时，既可能是"包含XX"也可能是"精确匹配XX"。

【示例】

**示例1：状态 + 功能限制**
**用户输入：** "RAN-7922385 待处理的功能不可用有哪些规则"
**解析过程：**
- "待处理的" → 触发器映射表 → triggers: ["WHEN_STATE_IN", "WHEN_STATE"], states: ["待处理"]
- "功能不可用" → 查表 → actions: ["FUNCTION"]
- 没有提到具体字段 → fields: []
- 没有提到执行步骤 → transition_names: []
**输出JSON：** {"triggers":["WHEN_STATE_IN","WHEN_STATE"],"states":["待处理"],"actions":["FUNCTION"],"fields":[],"transition_names":[],"conditions":{},"from_states":[],"to_states":[]}

**示例2：触发器 + 动作**
**用户输入：** "RAN-7922385 新建状态的自动赋值规则"
**解析过程：**
- "新建状态" → 触发器映射表 → triggers: ["WHEN_STATE_IN", "WHEN_STATE"], states: ["新建"]
- "自动赋值" → 查表 → actions: ["COPY"]
- 没有提到具体字段 → fields: []
- 没有提到执行步骤 → transition_names: []
**输出JSON：** {"triggers":["WHEN_STATE_IN","WHEN_STATE"],"states":["新建"],"actions":["COPY"],"fields":[],"transition_names":[],"conditions":{},"from_states":[],"to_states":[]}

**示例3：触发器 + 动作 + 字段**
**用户输入：** "RAN-7922385 创建时标题字段为什么是必填"
**解析过程：**
- "创建时" → 查表 → triggers: ["WHEN_CREATING"]
- 没有提到状态 → states: []
- "必填" → 查表 → actions: ["REQUIRED"]
- "标题" → fields: ["标题"]
- 没有提到执行步骤 → transition_names: []
**输出JSON：** {"triggers":["WHEN_CREATING"],"states":[],"actions":["REQUIRED"],"fields":["标题"],"transition_names":[],"conditions":{},"from_states":[],"to_states":[]}

**示例4：过度推断（错误示范）**
❌ **错误解析：** "字段规则" → actions: ["REQUIRED","READ_ONLY","HIDDEN","COPY"]
❌ 错误原因："字段规则"太泛，没有关键词可以模糊匹配，应该传空数组[]

✅ **正确解析：** "字段规则" → actions: []
✅ 正确原因：没有关键词可以模糊匹配，传空数组

**示例5：编造动作（严重错误）**
❌ **错误解析：** "自动填写" → actions: ["AUTO_FILL"]
❌ 错误原因：AUTO_FILL不在23种动作白名单中

✅ **正确解析：** "自动填写" → actions: ["COPY"]
✅ 正确原因：查表，"自动填写"在映射表中对应COPY

**示例6：从A到B模式**
**用户输入：** "DT_WIC_SHOWCASE-22410 单据从新建到处理中，为什么没有创建关联工作项？"
**解析过程：**
- "从新建到处理中" → 识别状态流转 → from_states: ["新建"], to_states: ["处理中"]
- "创建关联工作项" → 查表 → actions: ["CREATE_RELATED_WORK_ITEM"]
- 没有提到具体字段 → fields: []
- 没有提到执行步骤 → transition_names: []
- 没有提到触发器 → triggers: []
**输出JSON：** {"triggers":[],"states":[],"actions":["CREATE_RELATED_WORK_ITEM"],"fields":[],"transition_names":[],"conditions":{},"from_states":["新建"],"to_states":["处理中"]}

**示例7：从A到B + 动作 + 字段**
**用户输入：** "RAN-7922385 从拟制中到实施中，为什么Gerrit故障引入单字段必填？"
**解析过程：**
- "从拟制中到实施中" → 识别状态流转 → from_states: ["拟制中"], to_states: ["实施中"]
- "必填" → 查表 → actions: ["REQUIRED"]
- "Gerrit故障引入单" → fields: ["Gerrit故障引入单"]
- 没有提到执行步骤 → transition_names: []
- 没有提到触发器 → triggers: []
**输出JSON：** {"triggers":[],"states":[],"actions":["REQUIRED"],"fields":["Gerrit故障引入单"],"transition_names":[],"conditions":{},"from_states":["拟制中"],"to_states":["实施中"]}

**示例8：仅目标状态（翻转到XX）**
**用户输入：** "RAN-7922385 状态翻转到已关闭时，为什么描述字段只读？"
**解析过程：**
- "状态翻转到已关闭" → 识别目标状态 → from_states: [], to_states: ["已关闭"]
- "只读" → 查表 → actions: ["READ_ONLY"]
- "描述" → fields: ["描述"]
- 没有提到执行步骤 → transition_names: []
- 没有提到触发器 → triggers: []
**输出JSON：** {"triggers":[],"states":[],"actions":["READ_ONLY"],"fields":["描述"],"transition_names":[],"conditions":{},"from_states":[],"to_states":["已关闭"]}

用户："{USER_INPUT}"

输出严格JSON格式（不要有任何额外说明）：
{"triggers":[...],"states":[...],"actions":[...],"fields":[...],"transition_names":[...],"conditions":{},"from_states":[...],"to_states":[...]}
```

---

## 二、脚本入参说明

对应脚本：`scripts/atomic/rule_diagnose.py`

**用法：** 解析用户问题 → 提取维度 → 拼接参数调用脚本

### CLI参数

```bash
python scripts/atomic/rule_diagnose.py --help
```

| 参数 | 必需 | 说明 |
|------|------|------|
| `--work-item-id` | **是** | 单据ID，自动解析工作区和类型 |
| `--triggers` | 否 | 触发器列表，JSON数组 |
| `--states` | 否 | 状态列表，JSON数组 |
| `--actions` | 否 | 动作列表，JSON数组 |
| `--fields` | 否 | 字段中文名列表，JSON数组 |
| `--transition-names` | 否 | 执行步骤名称列表（中文名），JSON数组 |
| `--from-states` | 否 | 【从A到B模式】起始状态列表，JSON数组 |
| `--to-states` | 否 | 【从A到B模式】目标状态列表，JSON数组 |
| `--user-input` | 否 | 用户的原始输入，用于日志记录 |
| `--problem` | 否 | 【回退用】用户原始问题描述 |

**优先级：** `--triggers/--states/--actions/--fields/--transition-names/--from-states/--to-states` > `--problem`

**规则：** 尽可能传解析好的维度参数。不确定的维度传空数组 `[]`，不要编造。

---

## 三、典型场景与真实案例

以下案例基于真实工作项类型的规则数据，每个场景给出**用户问题 → 大模型解析 → 脚本调用**的完整过程。

### 场景1：状态 + 功能限制（组合场景）

**真实规则（chgRequest）：** Rule_1037992，触发器 WHEN_STATE，状态=已延期，动作 FUNCTION

> **用户：** "RAN-7922385 已延期状态下自动上报高层CCB功能不可用"
> **解析：** triggers=`["WHEN_STATE"]`, states=`["已延期"]`, actions=`["FUNCTION"]`
> ```bash
> python scripts/atomic/rule_diagnose.py \
>   --work-item-id "RAN-7922385" \
>   --triggers '["WHEN_STATE"]' \
>   --states '["已延期"]' \
>   --actions '["FUNCTION"]'
> ```

### 场景2：模糊匹配（重要场景）

**真实规则（chgRequest）：** Rule_963785，触发器 WHEN_UPDATED，动作 COPY

> **用户：** "RAN-7922385 更新后DSS组网字段被自动赋值了"
> **解析：** triggers=`["WHEN_UPDATED"]`, actions=`["COPY"]`, fields=`["DSS组网"]`
> **模糊匹配过程：** "自动赋值" → 提取关键词"赋值" → 查表找到"自动赋值"→COPY
> ```bash
> python scripts/atomic/rule_diagnose.py \
>   --work-item-id "RAN-7922385" \
>   --triggers '["WHEN_UPDATED"]' \
>   --actions '["COPY"]' \
>   --fields '["DSS组网"]'
> ```

### 场景3：状态变更触发（动态）

> **用户：** "RAN-7922385 状态变成待处理时验证版本号为什么必填？"
> **解析：** triggers=`["WHEN_STATE_CHANGE_TO"]`, states=`["待处理"]`, actions=`["REQUIRED"]`, fields=`["验证版本号"]`
> **模糊匹配过程：** "状态变成" → 提取关键词"变成" → 查表 → WHEN_STATE_CHANGE_TO
> ```bash
> python scripts/atomic/rule_diagnose.py \
>   --work-item-id "RAN-7922385" \
>   --triggers '["WHEN_STATE_CHANGE_TO"]' \
>   --states '["待处理"]' \
>   --actions '["REQUIRED"]' \
>   --fields '["验证版本号"]'
> ```

### 场景4：字段隐藏（常见场景）

**真实规则（chgRequest）：** Rule_1133209，触发器 WHEN_CREATING，动作 HIDDEN

> **用户：** "RAN-7922385 创建时分支名称字段看不到"
> **解析：** triggers=`["WHEN_CREATING"]`, actions=`["HIDDEN"]`, fields=`["分支名称"]`
> ```bash
> python scripts/atomic/rule_diagnose.py \
>   --work-item-id "RAN-7922385" \
>   --triggers '["WHEN_CREATING"]' \
>   --actions '["HIDDEN"]' \
>   --fields '["分支名称"]'
> ```

### 场景5：执行步骤名称匹配

**真实规则（chgRequest）：** Rule_1063130，触发器 WHEN_TRANSITION，Transition_125893（nameZh="指派实施"），动作 REQUIRED

> **用户：** "RAN-7922375 执行步骤是指派实施时，为什么Gerrit故障引入单字段必填？"
> **解析：** 
> - triggers=`["WHEN_TRANSITION", "WHEN_TRANSITION_CONTAIN"]`
> - transition_names=`["指派实施"]`
> - actions=`["REQUIRED"]`
> - fields=`["Gerrit故障引入单"]`
> 
> **关键点**：用户说的"执行步骤是指派实施"中的"指派实施"是Transition的中文名（nameZh），AI提取它放入transition_names，Python查询Transition表转换为Transition_125893，然后匹配规则condition。
> 
> ```bash
> python scripts/atomic/rule_diagnose.py \
>   --work-item-id "RAN-7922375" \
>   --triggers '["WHEN_TRANSITION", "WHEN_TRANSITION_CONTAIN"]' \
>   --transition-names '["指派实施"]' \
>   --actions '["REQUIRED"]' \
>   --fields '["Gerrit故障引入单"]' \
>   --user-input "执行步骤是指派实施时，为什么Gerrit故障引入单字段必填？"
> ```
> 
> **匹配结果**：成功匹配到Rule_1063130 "5G-指派实施时Gerrit故障引入单字段必填"

### 场景6：状态条件（静态）

> **用户：** "RAN-7922385 待处理状态下配置项处理方式为什么必填？"
> **解析：** triggers=`["WHEN_STATE_IN", "WHEN_STATE"]`, states=`["待处理"]`, actions=`["REQUIRED"]`, fields=`["配置项处理方式"]`
> **模糊匹配过程：** "待处理状态下" → 提取关键词"状态下" → 查表 → WHEN_STATE_IN和WHEN_STATE
> ```bash
> python scripts/atomic/rule_diagnose.py \
>   --work-item-id "RAN-7922385" \
>   --triggers '["WHEN_STATE_IN", "WHEN_STATE"]' \
>   --states '["待处理"]' \
>   --actions '["REQUIRED"]' \
>   --fields '["配置项处理方式"]'
> ```

**场景3 vs 场景6 对比**：
| 用户表达 | 触发器 | 含义 | 触发时机 |
|---------|--------|------|---------|
| "**状态变成**待处理时" | `WHEN_STATE_CHANGE_TO` | **动态**：状态变更瞬间 | 从A→B的**瞬间** |
| "**待处理状态下**" | `WHEN_STATE_IN`, `WHEN_STATE` | **静态**：在某个状态期间 | 在B状态的**整个期间** |

**场景5 vs 场景6 对比**：
| 用户表达 | 触发器 | transition_names | states | 含义 |
|---------|--------|-----------------|--------|------|
| "**执行步骤是指派实施时**" | `WHEN_TRANSITION`, `WHEN_TRANSITION_CONTAIN` | `["指派实施"]` | 不传 | **精确匹配**某个执行步骤名称 |
| "**待处理状态下**" | `WHEN_STATE_IN`, `WHEN_STATE` | 不适用 | `["待处理"]` | **状态条件**触发（在XX状态期间） |

### 场景7：从A到B模式

**背景：** 用户描述"从XX状态到YY状态"的流转过程，LLM提取 from_states 和 to_states，Python解析为执行步骤进行规则匹配。

> **用户：** "DT_WIC_SHOWCASE-22410 单据从新建到处理中，为什么没有创建关联工作项？"
> **LLM解析：**
> - 识别"从新建到处理中" → `from_states=["新建"]`, `to_states=["处理中"]`
> - "创建关联工作项" → `actions=["CREATE_RELATED_WORK_ITEM"]`
> **调用脚本：**
> ```bash
> python scripts/atomic/rule_diagnose.py \
>   --work-item-id "DT_WIC_SHOWCASE-22410" \
>   --from-states '["新建"]' \
>   --to-states '["处理中"]' \
>   --actions '["CREATE_RELATED_WORK_ITEM"]' \
>   --user-input "单据从新建到处理中，为什么没有创建关联工作项？"
> ```
> 

**Python处理流程：**
1. 接收 from_states=["新建"], to_states=["处理中"]
2. 查状态定义API，将"新建"映射为 State_XXX，"处理中"映射为 State_YYY（nameZh → key）
3. 查 transitions API，找 from=State_XXX, to=State_YYY 的 transition
4. 获取匹配到的 transition 的 nameZh（如"提交处理"），存入 `transition_names`
5. 自动补充 `triggers=["WHEN_TRANSITION"]` 和对应状态到 `states` 列表
6. 继续按现有匹配逻辑进行规则匹配

**场景5 vs 场景7 对比**：
| 用户表达 | 维度 | 含义 | Python处理方式 |
|---------|------|------|--------------|
| "**执行步骤是指派实施时**" | `transition_names: ["指派实施"]` | 直接指定执行步骤 | 直匹配transition名称 |
| "**从新建到处理中**" | `from_states: ["新建"]`, `to_states: ["处理中"]` | 描述状态流转过程 | 查states→查transitions→转成transition_names |

---

## 四、注意事项

- **必须查表映射**：严格根据映射表转换用户表达，不要自主解释
- **必须传 `--work-item-id`**：用户提到单据号时**必须**传入
- **字段传中文名**：不要自行映射为 System Key
- **不确定传空数组**：无法确定的维度传 `[]`，不要编造
- **错误日志用 `rule_log_query.py`**，不要用 `rule_diagnose.py`
