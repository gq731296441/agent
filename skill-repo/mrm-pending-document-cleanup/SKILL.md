---
name: mrm-pending-document-cleanup
description: 通过自然语言或命令行执行 MRM 在途单据查询、确认取消、跟催与处理。内置三类用户模型（制单人/处理人/接口人）：普通用户可按 userInfo 查询并确认取消，接口人可按单位筛选、跟催、处理。跟催和处理前自动做接口人权限校验。分段参数会去空格并匹配 queryOptions 标准值。
version: 1.3.0
author: platform
tags: ["资产管理"]
triggers: ["MRM", "在途单据", "单据清理", "跟催", "取消单据"]
---

# MRM 在途单据清理 Agent 技能

## 技能概述

本技能聚焦「MRM在途单据清理」场景，提供核心业务支撑，自然语言理解用户意图后，调用 `scripts/` 下 Python 脚本执行接口。

### 业务能力一览

| 用户类型 | 主要能力 | 对应脚本子命令 |
|------|------|----------------|
| 制单人 / 处理人（非接口人） | 按自己 `userInfo` 查询在途单；对单据确认取消（单条/批量） | `query-by-user`、`confirm-cancel`、`confirm-cancel-by-segment` |
| 接口人 | 按二三层单位查询单据；按指定姓名工号查询单据；按单位跟催；按单位处理 | `query`、`query-by-user`、`track`、`process` |
| 通用能力 | 获取筛选项、校验分段标准值、判断是否接口人、按 ID 查单据详情 | `options`、`meta`、`admin-check`、`query-by-ids` |

## 调用顺序（必读）

1. **识别用户类型**：先调用 `admin-check`（`/admin/check?userId=工号`）判断是否接口人。  
2. **普通用户路径**（制单人/处理人）：
   - `query-by-user --user-info` 查询本人单据（可加 `--segment`）。
   - `confirm-cancel` 对指定单据确认取消，或 `confirm-cancel-by-segment` 对分段内所有单据批量确认。
3. **接口人路径**：
   - `query` 按二三层单位筛选查询。
   - `query-by-user --user-info` 查询指定用户单据（可加 `--segment`）。
   - `track` / `process` 前会自动执行接口人权限校验，非接口人直接拒绝。

> 关键约束：分段参数必须先去掉所有空格，再匹配 `queryOptions` 的标准值。
> 例如 `6~12 个月`、`6 - 12个月` 都应先归一到 `6~12个月`；若不在标准值集合中，必须报错而不是透传。

**`--user-info` 传参说明（`query-by-user` / `confirm-cancel-by-segment`）**：
- 推荐格式 `姓名+工号`（如 `张三10001234`），精确度高，避免误匹配。
- 也支持纯姓名或纯工号的模糊匹配（后端使用 `containsIgnoreCase` 子串包含匹配），但可能匹配到多个用户，精准度较低。
- 例如：传入 `10344649` 可匹配到 `author`/`currentHandler` 字段中包含该工号的所有单据；传入 `张三` 同理。

**`--user` 传参说明（`query`）**：
- 后端使用 `equals` 精确匹配，必须与数据库中 `author`/`currentHandler` 字段值完全一致才能命中。
- 此参数用于查询**任意用户**（不限于当前操作者），接口人可按指定用户筛选单据。
- 建议先用 `query-by-user --user-info` 确认目标用户的准确格式，再传给 `query --user`。

## 自然语言 → 脚本映射

| 用户说法示例 | 建议动作 |
|--------------|----------|
| 「我是不是接口人」 | `admin-check --user-id 10344649` |
| 「查我名下 >=12个月 的在途单据」 | `query-by-user --user-info "张三10001234" --segment ">=12个月"` |
| 「把我名下 6~12 个月 的单据都确认取消（制单人）」 | `confirm-cancel-by-segment --user-info "张三10001234" --segment "6~12 个月" --author true` |
| 「确认单号 TEST-003 已取消（处理人）」 | `confirm-cancel --demand "TEST-003" --handler true` |
| 「查这几个单号的详细信息」 | `query-by-ids --ids "TEST-001,TEST-002"` |
| 「查某二层/三层单位（接口人）」 | `query --second "..." --third "..." --segments "..."` |
| 「跟催/处理xx单据（接口人）」 | `track ...` / `process ...`（脚本会先做 admin-check） |

Agent 应优先用 **`python scripts/mrm_pending_document.py --help`** 与各子命令 `--help` 确认参数。

## 快速开始

```bash
cd mrm-pending-document-cleanup-skill

# 安装依赖
pip install requests

# 一次打印全部筛选项（推荐）
python scripts/mrm_pending_document.py options

# 摘要模式（计数 + 列表）
python scripts/mrm_pending_document.py meta

# 判断是否接口人
python scripts/mrm_pending_document.py admin-check --user-id 10344649

# 普通用户：按 userInfo 查询
python scripts/mrm_pending_document.py query-by-user \
  --user-info "张三10001234" \
  --segment "6~12 个月"

# 按单号查询详细信息（用于查看完整字段）
python scripts/mrm_pending_document.py query-by-ids \
  --ids "TEST-001,TEST-002"

# 普通用户：单条确认取消（处理人）
python scripts/mrm_pending_document.py confirm-cancel \
  --demand "TEST-003" \
  --handler true

# 普通用户：按分段批量确认取消（制单人）
python scripts/mrm_pending_document.py confirm-cancel-by-segment \
  --user-info "张三10001234" \
  --segment ">=12个月" \
  --author true

# 按条件查询（会自动校验单位和分段是否有效）
python scripts/mrm_pending_document.py query \
  --second "二层A,二层B" \
  --third "三层1,三层2" \
  --segments "分段1,分段2" \
  --author-cancel true \
  --handler-cancel false \
  --user "指定制单人、处理姓名工号"

# 跟催（type: 1 制单人 2 当前处理人 3 两者）
python scripts/mrm_pending_document.py track --type 3 --ids "单号1,单号2" --operator "接口人姓名工号"

```

## 环境变量

| 变量 | 必需 | 说明 |
|------|------|------|
| `coclaw_empno` | 是 | 员工工号，用于所有接口的认证请求头（`X-Emp-No` / `userNumber`）。脚本第 20 行已硬编码默认值，一般无需手动设置 |
| `coclaw_token` | 写操作必需 | SSO 认证值，用于 `X-Auth-Value` / `userToken` 请求头 |
| `MRM_API_BASE` | 否 | 后端 API 根地址，默认 `https://aip.zte.com.cn/cmtool/api` |
| `MRM_OPERATOR` | 否 | 操作者展示名（如 `张三10001234`），`track` / `process` 时可用 `--operator` 覆盖 |

## 接口汇总

详见 `references/api_reference.md`。

## 表格字段中文说明

以下字段对应在途单据主表展示列，Agent 在生成结果说明、筛选解释、确认取消提示时应使用这些中文语义：

| 字段名 | 中文说明 |
|------|------|
| `demandNumber` | 需求明细单号（单据唯一标识） |
| `isHandler` | 处理状态标识（如：已处理、处理中、失败） |
| `author` | 制单人（MRM 申购提单人） |
| `currentHandler` | 当前处理人 |
| `applyTime` | 申请时间 |
| `materialCode` | 物料代码 |
| `materialName` | 物料名称 |
| `demandQuantity` | 需求数量 |
| `applyReason` | 申请原因 |
| `secondLevelOrgName` | 二层单位名称 |
| `thirdLevelOrgName` | 三层单位名称 |
| `fourthLevelOrgName` | 四层单位名称 |
| `inTransitDurationSegment` | 在途时长分段 |
| `documentStatus` | 单据状态 |
| `authorIsCancel` | 制单人确认是否取消 |
| `currentHandlerIsCancel` | 当前处理人确认是否取消 |

补充说明：
- `authorIsCancel` / `currentHandlerIsCancel`：`true` 表示“是”，`false` 表示“否”，`null` 表示“未确认/空”。
- `isHandler` 与 `documentStatus` 语义不同：`isHandler` 偏向清理处理结果，`documentStatus` 偏向单据流程状态。
- `query-by-user` 默认输出摘要信息（单号、制单人、当前处理人、取消状态等），如需某些单据的完整字段详情，请使用 `query-by-ids`。

## 目录结构

```
mrm-pending-document-cleanup-skill/
├── SKILL.md
├── references/
│   └── api_reference.md
├── scripts/
│   └── mrm_pending_document.py
└── log/          # 可选：自行写入审计日志
```

## 注意事项

- **用户类型必须先识别**：执行跟催、处理前必须先确认接口人身份；脚本会自动校验。  
- **普通用户确认取消**：由后端根据 `X-Emp-No` 与单据 `author/currentHandler` 匹配后决定可更新字段，非本人单据会报错。  
- **分段参数标准化**：先去掉全部空格再做标准化匹配，最终仅使用接口返回的标准值（如 `6~12个月`）发起查询。  
- **处理**接口仅当制单人、当前处理人均为「是」（确认取消）时才会被业务接受。  
- **跟催**会过滤不符合规则的单号（与现有 `emailFollowUp` 规则一致）；若 `ids` 全部无效，接口或业务层可能返回失败，需结合返回信息排查。  
- 测试环境与正式环境的 base 路径不同（含 `trial` 时常见）；若需连 trial，设置 `MRM_API_BASE` 为对应 trial 的 API 根地址。

## 更新日志

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.3.1 | 2026-05-14 | 补充 `--user-info` 和 `--user` 传参说明，明确前者支持姓名+工号/纯姓名/纯工号，后者使用精确匹配且可查询任意用户 |
| 1.3.0 | 2026-04-29 | 新增 `admin-check`、`query-by-user`、`confirm-cancel`、`confirm-cancel-by-segment`；按三类用户建模并对 `track/process` 增加接口人前置校验 |
| 1.2.0 | 2026-04-28 | 移除 `save`、`sync`、公共账号登录配置，统一由 `queryOptions` 提供选项能力 |
| 1.1.0 | 2026-04-28 | 新增 `queryOptions` 一次获取筛选项；`query` 增加输入校验 |
| 1.0.0 | 2026-04-28 | 初版：对齐 `MrmPendingDocumentCleanup.vue` 与 `api/index.js` |
