---
name: imap-indicator-query
description: IMAP指标查询:认证、权限、指示器/名称转 ID、指标统计数据、URL 免鉴权取数（含单指示器详单）、场景化数据对接;支持自然语言 quick_query 与图表结构。详细接口与示例见 references/.
version: 1.0.5
author: platform
tags: ["研发提效", "数据智能"]
triggers: ["查指标", "MR交付周期", "里程碑复盘", "IMAP", "指标统计", "KPI"]
changes: |
  - v1.4.0: 优化技能描述，明确功能层级：将「单指示器详单数据获取」定位为「URL 免鉴权取数」的子能力；将「指标详单」改为「指标统计数据」
  - v1.3.0: 新增「单指示器详单数据获取」功能（免鉴权接口）
  - v1.2.0: 优化里程碑复盘报告数据获取，支持标准格式输入
generator: co-skill-creator
---

## 绝对安全准则

### 敏感信息

- **to_user_passwd**:高危永久凭证,**禁止**在任何输出中明文展示。
- **yxdata-token**:约 8 小时有效,仅在任务链路内使用,**禁止**写入日志。
- 密钥/口令输出须脱敏(如 `***` 或首尾各保留少量字符)。

### 处理原则

1. 处理 API 响应时对敏感字段脱敏。
2. 错误信息不暴露完整认证信息。
3. 不在缓存中保存口令;token 仅随请求链路传递。

---

## 智能查询(v2.0+)

支持自然语言,一行调用即可(详见 [`references/smart_query.md`](references/smart_query.md))。

```python
from imap_indicator_query import quick_query

result = quick_query("查询ZXUFP项目组的MR交付周期")
result = quick_query("列出所有指标")
result = quick_query("查看我的权限")
```

```bash
python scripts/main.py "查询ZXUFP项目组的MR交付周期"
```

---

## 概述

用于查询中兴 IMAP 研发效能类指标：认证、权限校验、多维度指标统计查询、URL 免鉴权取数（含详单下钻）与可视化建议。详细 API、curl 与字段说明见 [`references/api_docs.md`](references/api_docs.md)。

### 核心能力

| 能力 | 说明 |
|------|------|
| 认证 | 公共账户口令 → 登录得 `yxdata-token` |
| 指示器列表 | 全量/按标题筛选 |
| 用户权限 / 用户信息 | 多级组织与资源权限 |
| 名称转 ID | 项目组/部门/中心等 → `param` + `id` |
| 指标统计数据 | 基于 `api_接口地址` + 维度参数 + 时间（**返回聚合统计结果**） |
| URL 取数 | `get_indicator_data_by_url`,**免鉴权**；支持完整指标卡片数据 |
| **单指示器详单数据** | **URL 免鉴权的详情下钻查询（子能力）**，只需提供URL和字段名；**默认展示前10条**，**完整数据自动保存为 Excel** |

### 场景化数据对接

#### 里程碑复盘报告数据获取（v2.0 优化版）

当用户输入**标准化格式**时**直接解析并调用API**，无需自然语言理解。标准输入格式必须严格遵循：

```
返回类型：{table|chart|...}；
里程碑：{里程碑名称1},{里程碑名称2},...；
指标：{指标名称1},{指标名称2},...；
添加内容：{添加指标1},{添加指标2},...；
```

**示例：**
```
返回类型：table；
里程碑：ZXUFPV2.30.30,ZXUFPV1.50.30；
指标：MR吞吐量,PR吞吐量,成果鉴定前一个月发现缺陷占比；
添加内容：需求分析引入缺陷数；
```

**重要：**
- 必须使用**全角冒号（：）和分号（；）**
- 里程碑和指标使用**半角逗号（,）**分隔
- 末尾必须有分号
- 无需任何其他文字说明

**标准输入格式:**
```
返回类型：table；
里程碑：{里程碑名称1},{里程碑名称2}；
指标：{指标名称1},{指标名称2},{指标名称3},......,{指标名称n}；
添加内容：{需要添加的指标名称1},{需要添加的指标名称2},......,{需要添加的指标名称n}；
```

**示例:**
```
返回类型：table；
里程碑：ZXUFPV2.30.30,ZXUFPV1.50.30；
指标：MR吞吐量,PR吞吐量,成果鉴定前一个月发现缺陷占比；
添加内容：需求分析引入缺陷数；
```

**实现方式（优化）：**
1. 严格校验输入格式，匹配标准模式时**直接解析**
2. 自动获取工号（`coclaw_empno` 环境变量）和认证令牌（`coclaw_token` 环境变量）
3. POST 调用接口：`https://aip.zte.com.cn/cmtool/api/v1/ai_platform/milestone_retrospective`
4. **返回 `bo.result` 部分的内容**（严格按照用户需求）

**返回结构：**
```json
{
  "status": true,
  "data": {
    // 从 API 返回的 bo.result 中提取的实际数据
  },
  "doc_id": "可选，文档ID",
  "chat_uuid": "可选，会话UUID",
  "finish_reason": "可选，结束原因"
}
```

**Python调用示例:**
```python
from imap_indicator_query import (
    query_indicator,
    query_by_imap_url,
    query_indicator_detail_by_url,
    query_indicator_list_export,
    get_milestone_retrospective_data,
)

# 指标数据查询(最常用)
result = query_indicator(
    indicator_name="MR交付周期(天)",
    query_type="项目组",
    query_name="ZXUFP",
    empno="00114732",
    token="your_token",
    start_date="2026-01-01",
    end_date="2026-03-31",
)

# URL 免鉴权
result = query_by_imap_url("https://aip.zte.com.cn/dashboard/...", is_encoded=False)

# **单指示器详单数据**（新增）
result = query_indicator_detail_by_url(
    single_indicator_url="https://aip.zte.com.cn/dashboard/#!/kanban/window/2-10-76-75/template/menu/107/sub-menu/MTA3LS8tL-WfuuehgOaVsOaNrg/5dd1e3ef2c674aa6bd54d259aaf840d0",
    column_name="数量"
)

# 指标列表
result = query_indicator_list_export(keyword="MR交付周期", empno="...", token="...")

# 里程碑复盘报告数据获取
result = get_milestone_retrospective_data(
    milestones=['ZXUFPV2.30.30', 'ZXUFPV1.50.30'],
    indicators=['MR吞吐量', 'PR吞吐量', '成果鉴定前一个月发现缺陷占比'],
    additional_metrics=['需求分析引入缺陷数']
)
```


### 使用时机(触发词示例)

- 查某项目组/部门/中心的 MR 周期、代码产出、需求速率等。
- 「有哪些指标」「列出 KPI」。
- 「我的权限」「我的部门/项目组」。
- 粘贴 IMAP dashboard URL 直接取数（聚合数据）。
- 名称转 ID:某项目组/部门对应的系统参数。
- **里程碑复盘报告**:获取里程碑复盘报告数据、复盘报告数据、报告数据获取、获取报告数据、报告数据、复盘报告、里程碑报告等。
- **场景化数据对接**:里程碑复盘、项目复盘、阶段总结等场景的数据获取。
- **单指示器详单数据**:查询单指示器的明细数据（下钻数据），属于「URL 免鉴权取数」的子能力.
  > **v2.0 优化说明：** 当检测到**标准格式输入**时，系统**跳过意图识别**，直接调用里程碑复盘API。这避免了被错误分类为普通指标查询.

---

## 必备前提

- 环境变量 **`coclaw_empno`**、**`coclaw_token`**(或调用时显式传入)。
- 网络可达 **`aip.zte.com.cn`**(内网)。
- 对目标资源需具备相应权限(否则返回明确错误)。

---

## 接口与请求头速查

逻辑顺序:**接口1 公共口令 → 接口2 登录 → 接口3~5 业务查询 → 接口7 指标统计数据查询**;**接口8/9 URL取数**（免鉴权，可独立调用）。

| 序号 | 用途 | 路径/说明 | `yxdata-token` |
|------|------|-----------|----------------|
| 1 | 公共账户口令 | `GET /middle/api/user/middle/api` | 否 |
| 2 | 登录 | `POST /middle/api/login` | 否 |
| 3 | 指示器列表 | `.../f3a0787995c0a06f` | **是** |
| 4 | 用户权限 | `.../e98f8b3f7d733935/detail` | **是** |
| 5 | 用户信息 | `.../d6e24b8e49d19eda/detail` | **是** |
| 6 | 名称转 ID | `.../8ca5daa07b878eaf/detail` | **是** |
| 7 | 指标统计数据查询 | `{api_接口地址}/detail` + 维度与时间（返回聚合统计） | **是** |
| 8 | URL 取数（聚合） | `GET .../dashboard/api/mcp/get_indicator_data_by_url` | **否** |
| 9 | 单指示器详单数据获取 | `GET /imap/api/mcp/get_indicator_detail_data_by_url?single_indicator_url=...&column_name=...` | **否** (免鉴权；URL取数的子能力) |

除接口 8、9 外,业务请求通常同时携带 **`X-Auth-Value`**、`X-Emp-No`(与接口文档一致)。完整示例与返回 JSON 见 [`references/api_docs.md`](references/api_docs.md)。

---

## 数据与缓存

当前 skill 实现:**不**做跨请求的 24 小时持久缓存;接口 1~4 结果仅在**单次查询链路**内传递。`yxdata-token` 可在**同一编排器/进程**内按有效期复用。扩展缓存设计见 [`references/caching.md`](references/caching.md)。

---

## 图表与可视化

当返回含 `data.content` 数组时,可按指示器的 `图表类型`、`图表x轴`/`图表y轴` 生成 `chart` / `visualization` 结构。字段规则与类型映射见 [`references/chart_config.md`](references/chart_config.md)。

---

## 调用方式总览

### Python

```python
from imap_indicator_query import (
    query_indicator,
    query_by_imap_url,
    query_indicator_list_export,
    get_milestone_retrospective_data,
)

# 指标数据查询(最常用)
result = query_indicator(
    indicator_name="MR交付周期(天)",
    query_type="项目组",
    query_name="ZXUFP",
    empno="00114732",
    token="your_token",
    start_date="2026-01-01",
    end_date="2026-03-31",
)

# URL 免鉴权
result = query_by_imap_url("https://aip.zte.com.cn/dashboard/...", is_encoded=False)

# 指标列表
result = query_indicator_list_export(keyword="MR交付周期", empno="...", token="...")

# 里程碑复盘报告数据获取(场景化数据对接)
result = get_milestone_retrospective_data(
    milestones=['ZXUFPV2.30.30', 'ZXUFPV1.50.30'],
    indicators=['MR吞吐量', 'PR吞吐量', '成果鉴定前一个月发现缺陷占比'],
    additional_metrics=['需求分析引入缺陷数'],
    empno="00114732",
    token="your_token"
)

# 或直接使用标准输入格式(需包含触发词)
result = quick_query(
    "获取里程碑复盘报告数据:返回类型:table;里程碑:ZXUFPV2.30.30,ZXUFPV1.50.30;指标:MR吞吐量,PR吞吐量,成果鉴定前一个月发现缺陷占比;添加内容:需求分析引入缺陷数;"
)
```

### CLI

```bash
export coclaw_empno="00114732"
export coclaw_token="your_auth_token"

python scripts/main.py query-indicator \
  --indicator-name "MR交付周期(天)" \
  --query-type "项目组" \
  --query-name "ZXUFP" \
  --start-date "2026-01-01" \
  --end-date "2026-03-31"

python scripts/main.py query-by-url --imap-url "https://aip.zte.com.cn/dashboard/..."
python scripts/main.py list-indicators --keyword "交付周期"

# 里程碑复盘报告数据获取
python scripts/milestone_retrospective.py \
  --milestones "ZXUFPV2.30.30,ZXUFPV1.50.30" \
  --indicators "MR吞吐量,PR吞吐量,成果鉴定前一个月发现缺陷占比" \
  --additional "需求分析引入缺陷数"

# **单指示器详单数据获取**（新增，免鉴权接口）
python scripts/query_indicator_detail_by_url.py \
  "https://aip.zte.com.cn/dashboard/#!/kanban/window/2-10-76-75/template/menu/107/sub-menu/MTA3LS8tL-WfuuehgOaVsOaNrg/5dd1e3ef2c674aa6bd54d259aaf840d0" \
  "数量"

# 使用标准输入格式（注意：冒号和分号必须用全角字符）
python scripts/milestone_retrospective.py --stdin "返回类型：table；里程碑：ZXUFPV2.30.30,ZXUFPV1.50.30；指标：MR吞吐量,PR吞吐量；添加内容：需求分析引入缺陷数；"
```

---

## 环境变量

| 变量 | 说明 | 必需 |
|------|------|------|
| `coclaw_empno` | 工号 | 是* |
| `coclaw_token` | 认证 token | 是* |
| `coclaw_lang` | 语言,如 zh_CN | 否 |

\* 可通过参数显式传入则不必设环境变量。

---

## 返回结构说明

统一元数据、成功/错误 JSON、分步场景与流程图见 [`references/response_examples.md`](references/response_examples.md)。

---

## 错误处理(摘要)

| 情况 | 处理 |
|------|------|
| 401/403 | 刷新 token 或检查权限 |
| 404 | 核对指示器名、资源名、类型 |
| 500 | 稍后重试 |
| 权限不足 | 联系管理员开通 |

详单量过大时引导用户通过 URL 免鉴权方式获取单指示器详单数据(见接口9与单指标技能)。

---

## 测试

见仓库 `tests/`;本地可执行 `python -m pytest tests/`(需环境与网络)。

---

## 参考资源(详细内容所在)

| 文档 | 内容 |
|------|------|
| [`references/api_docs.md`](references/api_docs.md) | 全部接口、curl、字段、错误码、认证时序 |
| [`references/smart_query.md`](references/smart_query.md) | 自然语言意图、语法、调试、v1/v2 对比 |
| [`references/response_examples.md`](references/response_examples.md) | 返回示例、场景步骤、工作流图 |
| [`references/chart_config.md`](references/chart_config.md) | 图表类型与配置映射 |
| [`references/caching.md`](references/caching.md) | 若需自建缓存时的模式与安全 |
| [`apply.md`](apply.md) | 环境与依赖准备 |

实现源码:`scripts/*.py`、包入口 `imap_indicator_query`。

---

## 版本历史

| 版本 | 日期 | 说明 |
|------|------|------|
| 1.0.0 | 2025-03-27 | 初始版本,支持基础查询与可视化 |
