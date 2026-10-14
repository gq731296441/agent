---
name: 员工信息查询
description: 支持一站式查询员工相关信息，严格遵守企业数据规范与权限管控，可自动判断员工所拥有的权限进行回答。可快速查询权限内的部门员工信息、员工基础信息、出差记录、请假信息等，数据来自内部管理系统，安全准确高效，方便日常办公与团队管理。可直接提问：1、帮我查询我的个人信息；2、查一下XX部门员工的人数；3、查询我2025年的出差行程；4、查询我今年的请假情况；5、帮我查XX去年的出差情况；
metadata: |-
  {
      "openclaw":
        {
          "emoji": "📋",
          "requires":
            {
              "anyBins": ["python3", "python", "py"],
              "env": [],
            },
          "platform": "cross-platform",
          "platforms": ["linux", "windows", "macos"]
        },
    }
version: 1.0.5
author: platform
---




# Employee & Department Query Skill (Cross-Platform Version)

查询员工信息、部门信息、员工考核数据的专用 skill。调用 iGPT DataAgent API 并提取 `bo.data_result[].table_data_markdown`（Markdown 表格）作为最终结果。

## 🌍 跨平台支持

| 平台 | Python 版本 | 运行方式 | 解释器优先级 |
|------|-------------|----------|-------------|
| **Linux** | Python 3.6+ | 运行 `employee_info_query.sh` | `python3` → `python` |
| **Windows** | Python 3.6+ | 运行 `.sh` 或直接执行 `.py` | `py` → `python3` → `python` |
| **macOS** | Python 3.6+ | 运行 `employee_info_query.sh` | `python3` → `python` |

### 🐍 解释器兼容性

脚本会自动探测可用的 Python 解释器：

#### 平台检测策略

| 平台 | 检测逻辑 |
|------|---------|
| **Linux/macOS** | 优先使用 `python3`，回退到 `python` |
| **Windows** | 优先使用 `py.exe` (Windows 原生)，回退到 `python3` / `python` |

#### 版本验证

所有脚本都会自动检查 Python 版本：
- **最低要求**: Python 3.6+
- 如果版本不满足，会提示错误信息

#### 常见场景

```bash
# Linux 系统 (只有 python3)
$ python3 employee-info-query.py --text "查询我的个人信息" --json
# 实际执行: /usr/bin/python3 employee-info-query.py ...

# Windows 系统 (py.exe 可用)
$ py employee-info-query.py --text "查询我的个人信息" --json
# 实际执行: C:\Windows\py.exe employee-info-query.py ...
```

### 🔧 手动指定 Python 解释器

如果需要强制使用特定 Python，可以通过环境变量覆盖：

```bash
# 强制使用特定 Python
PYTHON_BIN=/usr/local/bin/python3 ./employee-info-query.py --text "查询..."
```

**相关环境变量：**
- `PYTHON_BIN`: 覆盖自动探测的 Python 路径

## ⚠️ 执行步骤（必须按此执行）

**当你需要使用此 skill 时，按以下步骤执行：**

**步骤 1：调用 exec tool 执行查询**
- 使用 Python 直接执行 `.py` 脚本
- 使用：`python3(windows平台使用py) {baseDir}/employee-info-query.py --text "查询文本" --json`

**步骤 2：必须前台等待完成（禁止后台化）**

为了保证一直在前台执行，你**必须**在 `exec` tool 调用时显式设置：

- `yieldMs`: `60000`（前台等待 60 秒，不要后台化）
- `timeout`: `70`（最多 70 秒，防止卡死）

### 跨平台执行示例

```python
# Linux平台(安装了python3)
exec command:"python3 {baseDir}/employee-info-query.py --text \"查询我的年假情况\" --json"
  yieldMs: 60000
  timeout: 70

# Windows平台(安装了py.exe)
exec command:"py {baseDir}/employee-info-query.py --text \"查询我的年假情况\" --json"
  yieldMs: 60000
  timeout: 70
```


## ⚠️ 前台连续输出（禁止"后台执行/等待提示"）

当你使用 `员工信息查询` 时，**禁止**先输出任何"正在查询/请稍等/预计 xx 秒"之类的占位话术，也不要先解释要调用哪个技能。

你必须按下面顺序执行：

1. **立刻调用 `exec` tool**（不输出任何前置文本）
2. 等所有 `exec` 返回后，再用**一条最终回复**把表格结果原样输出到前台

这样用户看到的是一次连续的最终答案，而不是"前台先说一句等待，然后后台跑脚本"的分段交互。

**关于 `--json` / `--raw`**

- `--json`：建议保留（兼容旧调用方式），**脚本仍会解析并输出 Markdown 表格**。
- `--raw`：仅用于调试，输出 iGPT 原始 JSON 响应（不做表格解析）。

**示例：用户问"查询我最近一次的半年考核结果"**

**你必须执行：**

```python
exec command:"python3 \"{baseDir}/employee-info-query.py\" --text \"查询我最近一次的半年考核结果\" --json"
  yieldMs: 60000
  timeout: 70
```

**绝对不要返回：**
```
{"arguments": {"--text": "查询我最近一次的半年考核结果"}}
```

**这是 tool call，不是参数返回。你必须实际调用 exec tool。**

## 何时使用（触发场景）

**必须使用此 skill 当用户询问以下任何内容：**

- 员工信息：员工姓名、员工编号、员工状态、员工类型、员工画像
- 员工出差：出差时间、出差目的地、出差事由
- 管理干部：管理干部名单、层级（一层/二层/三层/四层管理干部）
- 员工考核：考核结果、考核数据、绩效信息、月考核、半年考核
- 员工统计：部门人数、员工数量、员工分布
- 员工请假：已请年休假、已调休天数
- 人员查询：查找员工、搜索员工、员工清单
- 会议信息：会议次数、时长
- 考勤数据：考勤、异常考勤
- 项目经历和工时
- 违规和荣誉
- 购买的商业保险
- AP任务
- 签证信息

**示例触发问题：**
- "查询数据管理团队有多少人"
- "数据管理团队有哪些员工？"
- "查询北京兴云数科技术有限公司的员工"
- "员工考核数据"
- "查询某个部门的员工信息"
- "兴云数科有多少管理干部"

**关键点：**
1. **这是 tool call，不是参数构造**
2. **你必须实际调用 exec tool，使用系统的 tool call 机制**
3. **脚本路径（相对 skill 目录）：** `employee-info-query.py`
4. 查询文本需要用双引号包裹，并在 JSON 中转义
5. **绝对不要返回 `{"arguments": {...}}` 这样的格式**

## 示例

### 示例 1：基本查询（Windows）

用户问："查询数据管理团队有多少人"

**正确做法**：立即调用 exec tool（使用 tool call）

```python
exec command:"py \"{baseDir}/employee-info-query.py\" --text \"查询数据管理团队有多少人\" --json"
  yieldMs: 60000
  timeout: 70
```

**错误做法**：只返回参数（❌ 不要这样做）
```json
{
  "arguments": {"--text": "查询数据管理团队有多少人"}
}
```

### 示例 2：员工考核查询（Linux）

用户问："查询我最近一次的半年考核怎么样"

**正确做法**：立即调用 exec tool（使用 tool call）

```python
exec command:"python3 \"{baseDir}/employee-info-query.py\" --text \"查询我最近一次的半年考核结果\" --json"
  yieldMs: 60000
  timeout: 70
```

**错误做法**：只返回参数（❌ 不要这样做）
```json
{
  "arguments": {"--text": "查询我最近一次的半年考核结果"}
}
```


> 💡 **说明**：当默认脚本执行失败时，你需要自己确定当前系统使用的python解释器情况：
> - Windows: `py` → `python3` → `python`
> - Linux/macOS: `python3` → `python`

## ⚠️ 重要提醒

**当需要使用此 skill 时：**
1. **立即调用 `exec` tool**（使用 tool call，不是返回 JSON）
2. **不要返回 `{"arguments": {...}}` 格式**
3. **不要只构造参数对象**
4. **必须实际执行 exec tool call**

**正确格式：**
- ✅ `exec command:"python3 \"{baseDir}/employee-info-query.py\" --text \"查询文本\" --json"`

**错误格式：**
- ❌ `{"arguments": {"--text": "查询文本"}}`
- ❌ `{"text": "查询文本"}`
- ❌ 任何只返回参数而不调用 tool 的格式

## 输出

- 如果接口返回了表格：直接输出 `table_data_markdown`（可被 OpenClaw 原样渲染）。
- 如果没有表格：会输出简要诊断信息（code/msg/可用字段）。


## 🔧 技术架构

### 文件结构

```
{baseDir}/
├── SKILL.md                    # 技能说明文档
└── employee-info-query.py      # 核心查询逻辑 (Python)
```

### 🛠️ 跨平台实现细节

#### 1. Python 核心脚本 (`employee-info-query.py`)

```python
# 版本检查 (脚本启动时执行)
check_python_version()  # 确保 >= 3.6

# 解释器探测 (供外部调用)
find_python_executable()  # 返回最佳解释器路径
```

**特性：**
- 纯 Python 实现，无 Bash 依赖
- 支持 Python 3.6+
- Linux/macOS: 使用 `curl` 发送 HTTP 请求
- Windows: 自动回退到 `requests` 库或 `urllib`
- 动态生成 UUID（兼容所有 Python 版本）

#### 2. 解释器探测优先级

| 平台 | 优先级顺序 |
|------|-----------|
| Linux | `python3` → `python` |
| macOS | `python3` → `python` |
| Windows | `py` → `python3` → `python` |

### 调试模式

添加 `--debug` 或 `-d` 参数可以查看详细信息：

```python
exec command:"python3 \"{baseDir}/employee-info-query.py\" --text \"查询数据管理团队有多少人\" --debug"
  yieldMs: 60000
  timeout: 70
```

输出示例：
```
DEBUG: Platform=linux
DEBUG: Python=python3
DEBUG: emp_no=set
DEBUG: token=set
DEBUG: URL=https://api.sdp.zte.com.cn/...
DEBUG: chatUuid=abc123...
```

