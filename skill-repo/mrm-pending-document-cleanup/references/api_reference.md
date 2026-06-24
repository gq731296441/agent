# MRM 在途单据清理 — API 参考

本文档对应新版 skill（按用户类型分流：普通用户/管理员）。

## 1. 管理员校验（管理员路径前置）

| 项 | 值 |
|----|-----|
| URL | `{MRM_API_BASE}/v1/mrm_pending_doc/admin/check` |
| 方法 | GET |
| Query | `userId`（工号，必填） |
| 响应关键字段 | `data.isAdmin` |

说明：
- `isAdmin=true` 视为管理员，可执行跟催与处理。
- 跟催、处理前必须先做管理员校验（接口侧也会二次校验）。

## 2. 查询筛选项（单位/分段标准字典）

| 项 | 值 |
|----|-----|
| URL | `{MRM_API_BASE}/v1/mrm_pending_doc/queryOptions` |
| 方法 | GET |
| Header | `X-Emp-No`（必填） |
| 响应 | `data.secondLevelOrgNames`、`data.thirdLevelOrgNames`、`data.inTransitDurationSegments`、`data.orgTree` |

说明：
- 分段查询参数应先去空格，再匹配到该接口返回的标准值后再传入后续查询接口。

## 3. 按用户查询在途单据（普通用户主路径）

| 项 | 值 |
|----|-----|
| URL | `{MRM_API_BASE}/v1/mrm_pending_doc/queryByUser` |
| 方法 | GET |
| 必填 Query | `userInfo`（如 `张三10001234`） |
| 说明 | `userInfo` 使用 `containsIgnoreCase` 子串包含匹配，支持姓名+工号精确查询，也支持纯姓名或纯工号的模糊匹配 |
| 可选 Query | `inTransitDurationSegment`（标准值） |

调用示例：
- `.../queryByUser?userInfo=饶争荣10107659`
- `.../queryByUser?userInfo=饶争荣10107659&inTransitDurationSegment=>=12个月`

## 4. 制单人/处理人确认取消

| 项 | 值 |
|----|-----|
| URL | `{MRM_API_BASE}/v1/mrm_pending_doc/createCancelStatus` |
| 方法 | POST |
| Header | `X-Emp-No`（必填）、`X-Auth-Value`（可传） |
| Body | 数组（每项至少包含 `demandNumber`；可包含 `authorIsCancel`/`currentHandlerIsCancel`） |

后端规则（逐条单据）：
- 若 `X-Emp-No` 命中单据 `author`，允许更新 `authorIsCancel`。
- 若 `X-Emp-No` 命中单据 `currentHandler`，允许更新 `currentHandlerIsCancel`。
- 两者都不命中：报错 `不可确认取消非制单人或处理人的单据，demandNumber=xxx`。
- 其他校验：`X-Emp-No` 为空、请求体为空、`demandNumber` 为空、单据不存在等会报错。

## 5. 管理员查询（按单位/分段筛选）

| 项 | 值 |
|----|-----|
| URL | `{MRM_API_BASE}/v1/mrm_pending_doc/query` |
| 方法 | GET |
| Header | 建议携带 `userNumber`、`userToken` |
| Query（可选） | `secondLevelOrgName`、`thirdLevelOrgName`、`inTransitDurationSegment`、`authorIsCancel`、`currentHandlerIsCancel`、`user` |
| 说明 | `user` 使用 `equals` 精确匹配 `author`/`currentHandler`，需传入与数据库一致的值（建议 `姓名+工号`）；此参数用于查询任意用户，非仅限当前操作者 |

## 6. 按 ID 列表查询

| 项 | 值 |
|----|-----|
| URL | `{MRM_API_BASE}/v1/mrm_pending_doc/queryByIds` |
| 方法 | POST |
| Body | JSON 数组，元素为需求明细单号字符串 |

## 7. 邮件跟催（管理员）

| 项 | 值 |
|----|-----|
| URL | `{MRM_API_BASE}/v1/mrm_pending_doc/track` |
| 方法 | POST |
| Body | `{"ids":["单号",...],"user":"操作者信息","type":"1"|"2"|"3"}` |

## 8. 处理单据（管理员）

| 项 | 值 |
|----|-----|
| URL | `{MRM_API_BASE}/v1/mrm_pending_doc/process_doc_pending` |
| 方法 | POST |
| Header | `X-Emp-No`、`X-Auth-Value` |
| Body | `{"demandNumbers":[...],"user":"操作者信息"}` |

## 响应约定

业务接口通常返回 `status`、`msg`、`data`。调用方应先判定 `status`，再解析 `data`。
