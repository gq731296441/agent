# 配置审核报告 - 接口文档

本文件记录配置审核报告技能所需的接口调用细节、输出字段定义与计算逻辑。供 SKILL.md 按需引用。

## 一、接口总览

本技能共需调用 **2 个接口** 获取基础数据。

| 接口 | URL | 用途 |
|------|-----|------|
| 接口一 | `https://aip.zte.com.cn/cmtool/api/v1/config_audit/query` | 获取配置管理领域检查单 |
| 接口二 | `https://aip.zte.com.cn/cmtool/api/v1/config_audit/queryTask` | 获取改进任务详情 |

**公共 Header（两个接口相同）：**

| Header | 值 |
|--------|-----|
| `X-Emp-No` | 用户的 8 位工号（由智能体提供） |
| `X-Auth-Value` | 用户的 UAC 认证 Token（由智能体提供） |
| `Content-Type` | `application/json` |

**公共 Body（所有参数均非必填）：**

```json
{
  "pageNum": 1,
  "pageSize": 50,
  "standardAuditMonth": ["2026-08", "2026-08"],
  "belongProject": "ZXODN V4.0"
}
```

**Body 参数说明：**

| 参数 | 说明 |
|------|------|
| `pageNum` | 页码，从 1 开始 |
| `pageSize` | 每页条数，固定为 50 |
| `standardAuditMonth` | 审核月份时间区间，第一个字符串为起始时间，第二个为结束时间，格式 `YYYY-MM` |
| `belongProject` | 所属项目 |

**参数提取规则：**
- 当用户问题中提及**时间/项目**时，提取对应参数值带入 Body；未提及则不传入对应参数。
- `pageSize` 固定为 50，当 `total > pageSize` 时，需要**遍历获取所有完整数据**。

## 二、接口一：获取配置管理领域检查单

- **URL**：`https://aip.zte.com.cn/cmtool/api/v1/config_audit/query`
- **方式**：POST

**CURL 示例：**

```bash
curl --location --request POST 'https://aip.zte.com.cn/cmtool/api/v1/config_audit/query' \
--header "X-Emp-No: $coclaw_empno" \
--header "X-Auth-Value: $coclaw_token" \
--header 'Content-Type: application/json' \
--data-raw '{
  "pageNum": 1,
  "pageSize": 50,
  "standardAuditMonth":["2026-08","2026-08"],
  "belongProject": "ZXODN V4.0"
}'
```

### 输出字段定义

| 输出字段（中文） | 接口字段 |
|------------------|----------|
| 标识 | `systemId` |
| 所属项目 | `belongProject` |
| 审核分类 | `auditClassification` |
| 研发活动 | `developmentActivities` |
| 审核项 | `auditItems` |
| 检查要点 | `checkPoints` |
| 审核时机 | `auditTiming` |
| 审核依据 | `auditBasis` |
| 审核结果 | `auditResult` |
| 典型问题 | `typicalQuestion` |
| 改进建议 | `improvementSuggest` |

## 三、接口二：获取改进任务详情

- **URL**：`https://aip.zte.com.cn/cmtool/api/v1/config_audit/queryTask`
- **方式**：POST
- **Header / Body**：与接口一相同

**CURL 示例：**

```bash
curl --location --request POST 'https://aip.zte.com.cn/cmtool/api/v1/config_audit/queryTask' \
--header "X-Emp-No: $coclaw_empno" \
--header "X-Auth-Value: $coclaw_token" \
--header 'Content-Type: application/json' \
--data-raw '{
    "pageNum": 1,
    "pageSize": 50,
    "standardAuditMonth":["2026-07", "2026-07"],
    "belongProject": "ZXODN V4.0"
}'
```

### 输出字段定义

| 输出字段（中文） | 接口字段 | 说明 |
|------------------|----------|------|
| 标识 | `systemId` | |
| 状态 | `systemState` | |
| 标题 | `systemTitle` | |
| 所属项目 | `belongProject` | |
| 所属中心 | `belongCenter` | |
| 研发活动 | `developmentActivities` | |
| 审核项 | `auditItems` | |
| 典型问题 | `typicalQuestion` | |
| 审核说明 | `auditDescHtml` | |
| 改进建议 | `improvementSuggest` | |
| 计划开始日期 | `createdTime` | |
| 计划完成日期 | `planFinishDate` | |
| 是否超期 | （计算字段） | 见下方计算逻辑 |
| 责任人 | `bizField` | |

### "是否超期"字段计算逻辑

"是否超期"为**计算字段**，超期定义如下（符合情况1/情况2中任意一种即算超期，其他均算未超期）：

- **情况1**：当单据状态（`systemState`）为 **"执行中"** 或 **"待验收"**，且 **计划完成日期（`planFinishDate`）< 当前日期**
- **情况2**：单据状态（`systemState`）为 **"新建"**，且 **当前时间 - 计划开始日期（`createdTime`）> 60天**

**判断标准：**
- 符合情况1 或 情况2 中任意一种 → **超期**
- 其他情况 → **未超期**

## 四、报告生成规则

根据获取的数据生成配置审核统计与明细报告，报告分为 **3 个 TAB 页**：概览、配置管理领域检查单、改进计划。

### TAB 1：概览

概览页包含以下 4 个数据区域：

#### 区域1：概览指标（基于接口1获取的详单计算）

| 指标 | 计算方式 |
|------|----------|
| 审核记录总数 | 单据总数 |
| 不通过项数 | 审核结果为"不通过"的单据数 |
| 不涉及项数 | 审核结果为"不涉及"的单据数 |
| 通过项数 | 审核结果为"通过"或"部分通过"的单据数 |
| 不符合项占比 | 审核结果为"不通过"的单据数 / 单据总数，百分制 |

#### 区域2：审核条目按研发活动分布（基于接口1获取的详单计算）

按不同研发活动类型统计的配置审核单据数，**按单据数从大到小排列**。

#### 区域3：改进任务按研发活动分布（基于接口2获取的详单计算）

按不同研发活动类型统计的改进任务单据数，**按单据数从大到小排列**。

#### 区域4：改进任务按期关闭率详情，按月统计（基于接口2获取的详单计算）

| 字段 | 计算方式 |
|------|----------|
| 月份 | 每个月份一行记录（从全部任务中提取，含"已确认拒绝"） |
| 不符合项数 | 状态非"已确认拒绝"的改进任务总数 |
| 按期关闭数 | 状态="已完成" 且 "验收通过日期（`passedTimeOfAcceptance`）<= 计划完成日期" 的单据数 |
| 按期关闭率 | 按期关闭数 / 不符合项数，百分制；当不符合项数为 0（无符合规则的单据）时，置为 **"NA"** |

**展示说明（v1.3.0）：**
- 该区域上方显示**按期关闭数定义说明**："**按期关闭数**：状态为'已完成'且验收通过日期 ≤ 计划完成日期的改进任务数；**按期关闭率** = 按期关闭数 ÷ 不符合项数 × 100%。"
- **按期关闭率**无论单月还是多月数据，均显示为**百分比格式**（如"50%"）；当值为"NA"时不加百分号。

### TAB 2：配置管理领域检查单

展示接口一获取的配置管理领域检查单**表格详情**，按照接口一输出字段**完整呈现表格**。

### TAB 3：改进计划

展示接口二获取的改进任务详情**表格**，按照接口二输出字段**完整呈现表格**。

**注意：** 状态为"已确认拒绝"的单据放在表格的**最后**。

**展示说明（v1.3.0）：** 表格上方显示**按期关闭数定义说明**："**按期关闭数**：状态为'已完成'且验收通过日期 ≤ 计划完成日期的改进任务数；**按期关闭率** = 按期关闭数 ÷ 不符合项数 × 100%。"

**展示说明（v1.4.0）：** 表格新增"验收通过日期"字段（取接口返回的 `passedTimeOfAcceptance`），放在"计划完成日期"与"责任人"之间。

**展示说明（v1.5.0）：**
- 表格新增"计划开始日期"字段（取接口返回的 `createdTime`），放在"改进建议"与"计划完成日期"之间。
- 表格上方新增"是否超期"规则描述："①状态为'执行中'或'待验收'，且计划完成日期 < 当前日期 → 超期；②状态为'新建'，且当前时间 - 计划开始日期 > 60天 → 超期；其余情况为未超期。"
