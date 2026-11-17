# API 接口文档

## 概述

yx-sw-component-manager 作为技能运行在 OpenClaw/co-mind 平台中，**不提供对外 HTTP API 接口**。
它是一个内部工具，通过技能插件机制（`main.py`）被调用，主要完成 Excel 文件处理与数据校验。

本技能**消费**外部系统提供的 HTTP API 接口，分两类：

1. **Spring Boot 接口（10.57.153.195:8080，需认证）**：validate 的 PDM 文档/编号校验、Gerrit 拆分/路径校验，
   以及 query 的 PDM 部件查询，均通过本地 Spring Boot 网关转发，替代原直连 pdmweb / CMDB 数据库的方式。
2. **遗留直连接口（需认证）**：wx 逐行规则引擎的 API 一致性检查直接调用 PDM GBOM 部件详情接口与 iComp 第三方组件接口。

所有 API 客户端统一继承 `BaseAPIClient`（见 `scripts/api_clients/base_client.py`），
提供**指数退避重试（默认 3 次，延迟 1s/2s/4s）**与统一错误处理。

## 1. 技能输入/输出接口

### 1.1 命令行参数

技能通过 `main.py` 的三个子命令调用，参数如下：

| 子命令 | 参数 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|------|--------|------|
| validate | `excel_path` | str | 是 | - | 组件清单 Excel 文件路径 |
| | `--config` | str | 否 | `assets/config/default_config.yaml` | 配置文件路径 |
| | `--output-dir` | str | 否 | Excel 同目录 | 统一审核报告输出目录 |
| | `--product-model` | str | 否 | `null` | 产品规格型号（跨产品一致性检查） |
| | `--work-group-1/2/3` | str | 否 | `null` | 工作组类编号（TEAM1/2/3） |
| | `--no-path-check` | bool | 否 | `false` | 禁用 Gerrit 路径校验 |
| | `--log-level` | str | 否 | `INFO` | 日志级别 |
| query | `excel_path` | str | 是 | - | Excel 文件路径 |
| | `--output-format`/`-f` | str | 否 | `xlsx` | 输出格式：csv/xlsx/json/html |
| | `--dry-run`/`-d` | bool | 否 | `false` | 模拟运行，不生成文件 |
| backfill | `excel_path` | str | 是 | - | 原始 Excel 文件路径 |
| | `--result-file`/`-r` | str | 是 | - | 查询结果文件（CSV/XLSX/JSON/HTML） |
| | `--result-ids`/`-i` | str | 是 | - | 要回填的序号，逗号分隔，如 `3,4,5` |
| | `--dry-run`/`-d` | bool | 否 | `false` | 模拟运行，不实际回填 |
| | `--no-prompt` | bool | 否 | `false` | 跳过交互确认 |

### 1.2 validate 返回值格式

```json
{
  "status": "success",
  "mode": "unified",
  "total_rows": 156,
  "passed": 148,
  "failed": 8,
  "correction_suggestions": 12,
  "overall_status": "不通过",
  "excel_report": "路径/_审核报告_时间戳.xlsx",
  "html_report": "路径/_审核报告_时间戳.html",
  "json_report": "路径/_审核报告_时间戳.json",
  "errors": [],
  "correction_suggestions_list": []
}
```

**退出码**：
- `0` - 成功（validate 全部通过 / query / backfill 成功）
- `1` - 校验失败、有错误，或执行错误（文件不存在、配置错误等）
- `130` - 用户中断（KeyboardInterrupt）

## 2. Spring Boot 接口（10.57.153.195:8080，validate/query 主接口）

以下接口均需认证头：

```
X-Emp-No: <工号>
X-Auth-Value: <Token>
Content-Type: application/json
```

认证环境变量候选顺序：`coclaw_empno` / `RDC_EMP_NO` / `X_Emp-No` / `X_EMP_NO` / `X-Emp-No`；
`coclaw_token` / `RDC_AUTH_TOKEN` / `X_Auth_Value` / `X_AUTH_VALUE` / `X-Auth-Value`。

所有接口响应统一为 `{ "code": 0, "msg": "成功", "data": ..., "status": true }`，`code=0` 表示业务成功。

### 2.1 PDM 部件查询接口（query 使用）

- **接口**: `POST /v1/pdm/query_part`
- **默认地址**: `http://10.57.153.195:8080/v1/pdm/query_part`
- **环境变量**: `PDM_QUERY_API_URL`（超时 60 秒）
- **请求体**:
```json
{
  "nameCn": "XSP5产品intfirb组件",
  "nameEn": "xsp5-intfirb",
  "enCode": "5960X-HN-intfirb-l1"
}
```
- **响应体**（`data` 为数组）:
```json
{
  "code": 0, "msg": "成功",
  "data": [
    {
      "partNo": "130000249711",
      "nameCn": "XSP5产品intfirb组件",
      "nameEn": "xsp5-intfirb",
      "enCode": "5960X-HN-intfirb-l1",
      "version": "AB",
      "lifecycleStatus": "审批完成",
      "productNo": "100000455962",
      "product": "NEP",
      "project": "装备软件NEP平台V3.0(310001257272)"
    }
  ],
  "status": true
}
```
- **客户端**: `scripts/api_clients/pdm_query_api_client.py`（`PdmQueryAPIClient`）
- **说明**: query 对"部件编号为空"的行，先多条件组合查询，无结果时按 name_cn→name_en→en_code
  单条件依次降级；同一部件编号去重保留最新版本。

### 2.2 PDM 部件编号校验接口（validate 使用）

- **接口**: `POST /v1/pdm/verify_part`
- **默认地址**: `http://10.57.153.195:8080/v1/pdm/verify_part`
- **环境变量**: `PDM_VERIFY_API_URL`（单批最大 100 个编号）
- **请求体**:
```json
{
  "partNos": ["130000233321", "130000233374"]
}
```
- **响应体**:
```json
{
  "code": 0, "msg": "成功",
  "data": [
    { "partNo": "130000233321", "exists": false },
    { "partNo": "130000233374", "exists": true }
  ],
  "status": true
}
```
- **客户端**: `scripts/api_clients/pdm_verify_api_client.py`（`PdmVerifyAPIClient`）
- **说明**: validate 中仅对"组件"和"软件模块"类别的部件编号批量校验（`should_validate_component`）。

### 2.3 PDM 文档详情查询接口（validate 使用）

- **接口**: `GET /v1/pdm/doc_detail?docNo=xxx`
- **默认地址**: `http://10.57.153.195:8080/v1/pdm/doc_detail`
- **环境变量**: `PDM_DOC_DETAIL_API_URL`
- **响应体**:
```json
{
  "code": 0, "msg": "成功",
  "data": { "found": true, "docNo": "...", "version": "D",
            "docName": "...", "lifecycleStatus": "审批完成" },
  "status": true
}
```
- **说明**: `data.found` 为 true 时文档存在。文档编号从文件名中提取前 12 位数字。

### 2.4 PDM 文档信息查询接口（validate 使用）

- **接口**: `GET /v1/pdm/doc_info?docNo=xxx`
- **默认地址**: `http://10.57.153.195:8080/v1/pdm/doc_info`
- **环境变量**: `PDM_DOC_INFO_API_URL`
- **响应体**:
```json
{
  "code": 0, "msg": "成功",
  "data": { "product": "...", "team1": "...", "team2": "...", "team3": "...",
            "projectName": "...", "lastUpdatedDate": "...", "nameCn": "...",
            "version": "D", "type1": "...", "type2": "...", "type3": "...",
            "lastUpdatedBy": "..." },
  "status": true
}
```
- **客户端**: `scripts/api_clients/pdm_doc_api_client.py`（`PdmDocAPIClient`，同时提供 2.3/2.4 两个接口）

### 2.5 Gerrit 代码库拆分接口（validate 使用）

- **接口**: `GET /v1/gerrit/split_repo_path?repoPath=<url-encoded>`
- **默认地址**: `http://10.57.153.195:8080/v1/gerrit/split_repo_path`
- **环境变量**: `GERRIT_SPLIT_API_URL`
- **响应体**:
```json
{
  "code": 0, "msg": "成功",
  "data": [ { "repo": "<最长匹配repo名>", "path": "<剩余路径>" } ],
  "status": true
}
```
- **语义**: 传入整个"代码库/目录"字符串，接口自动拆分为最长匹配 repo + 剩余 path；
  匹配不到时 `data[0].repo` 为空字符串，整个字符串放入 `path`。
- **客户端**: `scripts/api_clients/gerrit_api_client.py`（`GerritAPIClient`）

### 2.6 代码库存在性校验接口（validate 使用）

- **接口**: `POST /v1/gerrit/check_repo`
- **默认地址**: `http://10.57.153.195:8080/v1/gerrit/check_repo`
- **环境变量**: `REPO_CHECK_API_URL`（单批最大 100 个）
- **请求体**:
```json
{
  "repoNames": ["pol_pon_d/PON_D_elephants/packages/common/oss",
                "pol_pon_d/PON_D_elephants/packages/common/oss/lob"]
}
```
- **响应体**:
```json
{
  "code": 0, "msg": "成功",
  "data": [
    { "repoName": "pol_pon_d/.../oss", "exists": true },
    { "repoName": "pol_pon_d/.../oss/lob", "exists": false }
  ],
  "status": true
}
```
- **客户端**: `scripts/api_clients/repo_check_api_client.py`（`RepoCheckAPIClient`）

### 2.7 Gerrit 路径存在性校验接口（validate 使用）

- **接口**: `POST /v1/gerrit/check_path`
- **默认地址**: `http://10.57.153.195:8080/v1/gerrit/check_path`
- **环境变量**: `PATH_CHECK_API_URL`；分支默认 `master`，可用 `GERRIT_CHECK_BRANCH` 覆盖
- **请求体**:
```json
{
  "repoName": "WirelineTech/Gerrit-access",
  "branch": "authority_sync",
  "path": "source/Gerrit/.gitignore"
}
```
- **响应体**（文件/目录存在）:
```json
{
  "code": 0, "msg": "成功",
  "data": { "exists": true, "type": "file", "message": "路径存在(文件)" },
  "status": true
}
```
- **说明**: 替代原"clone 代码库到本地再检查路径"的方式。路径为空时视为通过（无需校验）。
- **客户端**: `scripts/api_clients/path_check_api_client.py`（`PathCheckAPIClient`）

## 3. 遗留直连接口（wx 逐行 API 一致性检查）

### 3.1 部件详情 API（PDM GBOM）

- **接口**: `POST http://pdmweb.zte.com.cn/zte-plm-iproduct-gbom/gbom/v2/query`
- **认证头**: `X-System-Code: 100000461002`、`Content-Type: application/json`
- **请求体**:
```json
{
  "NOs": [ { "no": "PDM-2024-001", "version": "latest", "allLifecycle": "1" } ],
  "infoBlocks": [ { "block": "G0000", "ver": "v2" } ]
}
```
- **成功响应**: `{ "code": {"code": "0000"}, "bo": [...] }`，`bo[0]` 或嵌套 `basic[0]` 含
  `NAME_CN`, `NAME_EN`, `EN_CODE`, `PART_TYPE`, `STATUS`, `LIFECYCLE_STATUS` 等字段
- **客户端**: `scripts/api_clients/part_api_client.py`（`PartAPIClient`）
- **用途**: validate 对常规部件（非外部第三方组件）且部件编号非空时查询并做一致性检查

### 3.2 第三方组件 API（iComp）

- **接口1（两段式组件，如 0013-0004）**: `POST https://icomp.zte.com.cn/zte-plm-icomp-gateway/zte-plm-icomp-comp/outer/component/list`
  - 请求体: `{"pageNo":1,"pageSize":10,"queryType":0,"strName":"0356-0044",...}`
- **接口2（三段式组件，如 0202-0000-0829）**: `POST https://icomp.zte.com.cn/zte-plm-icomp-gateway/zte-plm-icomp-comp/third/comp/list`
  - 请求体: `{"specialFilter":1,"pageNo":1,"pageSize":10,"compIdOrName":"0202-0000-0829"}`
- **认证头**: `X-Emp-No` / `X-Auth-Value`（从 `coclaw_empno` / `coclaw_token` 读取）
- **响应**: `bo.list[]`，取第一条；字段含 `name`/`statusName`/`externalId`（接口1）、
  `compName`/`availableStatus`/`compId`（接口2）
- **客户端**: `scripts/api_clients/component_api_client.py`（`ComponentAPIClient`）
- **用途**: validate 对外部第三方组件校验部件编号、状态（废弃不可导入）与名称一致性

## 4. Python API（开发接口）

如需扩展或集成，可直接使用本技能的 Python 模块。核心模块结构：

```
scripts/
  api_clients/         # 各类 API 客户端
  validators/          # header_validator / field_validator / rule_engine / consistency_checker
  processors/          # excel_processor / consistency_checker / data_corrector / report_generator
  validate/            # runner（统一编排）/ db_and_path_check（addDir 校验）/ report（统一报告）
  pdm_query/           # query/backfill 核心逻辑
  monitor/             # 使用埋点上报
  utils/logger.py      # 日志配置
```

### 4.1 关键类参考

| 类 | 模块 | 主要方法 |
|----|------|----------|
| `BaseAPIClient` | `scripts/api_clients/base_client.py` | `request` / `post` / `get`（指数退避重试） |
| `ExcelProcessor` | `scripts/processors/excel_processor.py` | `load_workbook` / `select_best_sheet` / `load_sheet` / `get_row_data` |
| `HeaderValidator` | `scripts/validators/header_validator.py` | `validate` / `get_matching_score` |
| `RuleEngine` | `scripts/validators/rule_engine.py` | `validate_row` / `execute_rules` / `register_rule` |
| `ConsistencyChecker` | `scripts/processors/consistency_checker.py` | `check_fields` / `has_discrepancy` / `get_corrections` |
| `UnifiedReportGenerator` | `scripts/validate/report.py` | `build_report` / `save_excel_report` / `save_html_report` / `save_json_report` |
| `run_validate` | `scripts/validate/runner.py` | 统一校验入口 |
| `run_query` / `backfill_by_result_ids` | `scripts/pdm_query/main.py` | query/backfill 核心 |

### 4.2 便捷函数

- `load_excel_file(excel_path, required_columns, preferred_sheet)`：加载 Excel 并优先选择【软件结构】sheet
- `run_checks(excel_file, enable_path_check)`：addDir 完整校验入口
- `send_report(...)`：技能使用埋点上报（`scripts/monitor/report_usage.py`）

## 5. 配置文件

默认配置 `assets/config/default_config.yaml`，关键项：

```yaml
validation:
  required_columns: [...]        # 13 个必需列定义
  component_categories: [...]    # 6 个部件类别枚举
  api:
    timeout: 30                  # API 超时（秒）
    max_retries: 3               # 最大重试次数
    retry_delay: 1.0
    backoff_factor: 2.0

output:
  create_corrected: false        # 已强制禁用自动修正（用户手动修订）
  report_suffix: "_report.json"
  html_suffix: "_report.html"

logging:
  level: "INFO"
  log_to_file: true
  log_dir: "./logs"

processing:
  first_row_level_must_be_zero: true
  auto_correct_first_row: false  # 已强制禁用自动修正第一行层次
  check_api_consistency: true
  auto_correct_inconsistencies: false  # 已强制禁用所有自动修正
  continue_on_api_error: true
```

**注意**：新版 validate 以 `--output-dir` 指定输出目录并生成统一审核报告；
配置文件中的 `output.create_corrected` 与 `processing.auto_correct_*` 已强制禁用自动修正。

## 6. 测试

本项目单元测试原位于 `scripts/tests/`（wx 源技能遗留），当前版本以 `main.py` 集成测试为主。
运行方式：

```bash
python main.py validate <excel_path> --output-dir ./output
```
