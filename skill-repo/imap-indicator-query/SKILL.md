---
name: imap-indicator-query
description: IMAP指标查询:认证、权限、指示器/名称转 ID、指标统计数据、URL 免鉴权取数（含单指示器详单）、场景化数据对接;支持自然语言 quick_query 与图表结构。详细接口与示例见 references/.
version: 1.0.6
author: 00114732 (optimized by Co-Claw)
tags: ["研发提效", "数据智能"]
triggers: ["查指标", "MR交付周期", "里程碑复盘", "IMAP", "指标统计", "KPI"]
changes: |
  - v1.6.0: 新增「DN 智能体调用（dn_search_imap）」，支持用电量监控、MR交付周期基础分析、北极星指标等 iCenter 报告，软平中心多指标基线目标报告，以及数据查询汇聚结果（见「场景化数据对接」）
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
| 指标详单数据 | 基于查询指示器获取信息中详单信息的 `all_api` （**返回全量详单csv文件**） |
| URL 取数 | `get_indicator_data_by_url`,**免鉴权**；支持完整指标卡片数据 |
| **单指示器详单数据** | **URL 取详单数据**，只需提供URL和字段名；**完整数据自动保存为 CSV** |

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
- **单指示器详单数据**:URL 取详单数据，只需提供URL和字段名；完整数据自动保存为 CSV,属于「URL 免鉴权取数」的子能力.
  > **v2.0 优化说明：** 当检测到**标准格式输入**时，系统**跳过意图识别**，直接调用里程碑复盘API。这避免了被错误分类为普通指标查询.
- **DN 智能体调用**:查询/生成用电量监控报告、MR交付周期基础分析报告、北极星指标分析报告、软平中心指标统计报告、数据汇聚统计结果等。

---

#### DN 智能体调用（dn_search_imap）

通过统一接口 `https://aip.zte.com.cn/dashboard/api/mcp/dn_search_imap` 调用 DN 应用智能体，
按 `agent_name` 路由，`query_request` **完全透传**。适用于生成 iCenter 分析报告、指标统计报告、
数据汇聚统计结果。用户常表述为「使用 iMAP 技能，查询/生成 ……报告」。

**支持的应用（agent_name）：**

| agent_name | 名称 | query_request 结构 |
|---|---|---|
| `iMAP_Data_Analysis` | 【在用】有线imap分析智能体-对接iCenter空间 | `{"analysis_scene":..., "organize":..., "indicator":...}` |
| `iMAP_Report_Analysis` | 【在用】有线imap分析智能体-指标报告 | `{"organize":..., "indicators":..., "report_template":...}` |
| `iMAP_Indicator_Statistics` | 【在用】有线imap分析智能体-数据查询 | `{"question":...}` |

**凭证：** 环境变量 `coclaw_empno` / `coclaw_token`（即 auth_value），由 openclaw 注入。
缺失时快速失败并提示变量名，**勿在命令里硬编码工号/令牌**。

**CLI 用法：**

```bash
export coclaw_empno="你的工号"
export coclaw_token="你的auth_value"

# 通用形式：<agent_name> '<query_request JSON>'
python scripts/dn_agent.py <agent_name> '<query_request JSON>'

# 长 JSON 可从 stdin 读取
echo '<query_request JSON>' | python scripts/dn_agent.py <agent_name> -

# 排障：原始服务端响应打印到 stderr
python scripts/dn_agent.py <agent_name> '<query_request JSON>' --raw
```

**5 个典型场景配方（可直接照抄）：**

1. 查询用电量监控报告（研究院）
```bash
python scripts/dn_agent.py iMAP_Data_Analysis \
  '{"analysis_scene":"用电量","organize":"研究院"}'
```

2. ZXUFP 项目 MR 交付周期基础分析报告
```bash
python scripts/dn_agent.py iMAP_Data_Analysis \
  '{"analysis_scene":"MR交付周期基础分析","organize":"ZXUFP","indicator":"MR交付周期"}'
```

3. Jinos 项目北极星指标分析报告（organize 用正式名 ZXJiNOS）
```bash
python scripts/dn_agent.py iMAP_Data_Analysis \
  '{"analysis_scene":"北极星指标分析","organize":"ZXJiNOS"}'
```

4. 软件平台中心多指标基线目标统计报告
```bash
python scripts/dn_agent.py iMAP_Report_Analysis \
  '{"organize":"软件平台中心","indicators":"MR交付周期,MR分析周期,MR开发周期,MR测试周期,MR研发人均交付数","report_template":"基线目标"}'
```

5. ROSNG V13.0 MR 交付周期，团队按月趋势
```bash
python scripts/dn_agent.py iMAP_Indicator_Statistics \
  '{"question":"ROSNG V13.0 MR交付周期，团队按月趋势"}'
```

**参数说明与注意事项：**

- `query_request` 完全透传，skill 不校验/改写字段；场景值以 DN 应用实际支持为准。
- 场景 1~3 返回 **iCenter 链接**；场景 4 返回指标统计表格；场景 5 返回数据汇聚表格。结果正文原样返回，不作二次改写。
- 默认超时 **175 秒**（网关约 180 秒截断），可用 `--timeout` 覆盖；**不自动重试**。
- **已知问题**：`iMAP_Report_Analysis`（场景 4）当前偶发返回服务端异常（提示词泄漏/内部错误），
  skill 会显式报错退出而不是把无效内容当报告；待服务端修复后无需改代码。
- 场景 2/3 的 `analysis_scene`（`MR交付周期基础分析` / `北极星指标分析`）、场景 4 的 `report_template`
  （`基线目标`）为按需求推断值，**尚未经服务端验证**；若报告内容不符请按实际支持值调整。
- 失败时：stdout 为空、stderr 输出一行错误、退出码 1（参数错误为 2）；原始响应需加 `--raw` 才输出到 stderr。

**Python API：**

```python
from imap_indicator_query import call_dn_agent

result = call_dn_agent(
    agent_name="iMAP_Data_Analysis",
    query_request={"analysis_scene": "用电量", "organize": "研究院"},
)
# 成功: {"status": True, "result": "**iCenter链接**：...", "agent_name": ..., "raw_response": {...}}
# 失败: {"status": False, "error": "..."}
```

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
| 8 | 指标详单数据查询 | `{all_api}/detail` + 维度与时间（返回详单数据） | **是** |
| 9 | URL 取数（聚合） | `GET .../dashboard/api/mcp/get_indicator_data_by_url` | **否** |
| 10 | 单指示器详单数据获取 | `GET /imap/api/mcp/get_indicator_detail_data_by_url?single_indicator_url=...&column_name=...` | **否** (免鉴权；URL取数的子能力) |

业务请求通常同时携带 **`X-Auth-Value`**、`X-Emp-No`(与接口文档一致)。完整示例与返回 JSON 见 [`references/api_docs.md`](references/api_docs.md)。

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
    quick_query,
    list_all_indicators,
    query_indicator,
    query_indicator_list_export,
    query_by_imap_url,
    query_indicator_detail_by_url,
    query_user_auth,
    get_user_auth_types,
    query_user_info,
    extract_user_org_info,
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

# URL
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
# 智能查询（推荐方式 - 自然语言）
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

# 或使用标准输入格式（注意全角冒号和分号）
python scripts/milestone_retrospective.py --stdin "返回类型：table；里程碑：ZXUFPV2.30.30,ZXUFPV1.50.30；指标：MR吞吐量,PR吞吐量；添加内容：需求分析引入缺陷数；"
# **单指示器详单数据获取*
python scripts/query_indicator_detail_by_url.py \
  "https://aip.zte.com.cn/dashboard/#!/kanban/window/2-10-76-75/template/menu/107/sub-menu/MTA3LS8tL-WfuuehgOaVsOaNrg/5dd1e3ef2c674aa6bd54d259aaf840d0" \
  "数量"


# 用户权限与信息查询（传统 API 脚本）
python scripts/query_user_auth.py --empno "00114732" --token "your_token"
python scripts/query_user_info.py --empno "00114732"
python scripts/query_by_url.py --imap-url "https://aip.zte.com.cn/dashboard/..." --is-encoded false

# 指示器列表查询
python scripts/query_indicator_list.py --keyword "交付周期"
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
