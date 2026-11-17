---
name: yx-sw-component-manager
description: 有线组件清单管理技能。统一整合组件清单的综合校验、PDM部件编号查询与回填功能：1) validate 综合校验《软件组件清单》的模板格式、逐行规则、部件编号（通过PDM接口校验）、代码库/目录（通过Gerrit接口校验拆分及路径存在性）、API一致性，输出一份统一审核报告（多sheet Excel + HTML + JSON）；2) query 对部件编号为空的行使用中文/英文名称/英文代号查询PDM并生成查询结果报告；3) backfill 根据查询结果序号将部件编号精确回填到原Excel（支持多匹配选择、dry-run预览）。
tags: ["研发提效"]
version: 1.1.1
author: 洪伟韬10306212
exec: |
  python main.py "$command" "$excel_path" ...
---

# 技能：有线组件清单管理 (yx-sw-component-manager)

## 概述

本技能将三个既有技能的完整功能融合为一个统一技能：

| 源技能 | 融合的功能 | 子命令 |
|--------|-----------|--------|
| wx-sw-component-list-check | 逐行规则引擎校验（11条规则）+ PDM/iComp API 一致性检查 | `validate` |
| component-manifest-verify-addDir | 文档格式检查 + CMDB部件编号校验 + 代码库拆分校验 + Gerrit路径校验 | `validate` |
| pdm-query2report-skill | PDM 部件编号查询 + 按序号回填 | `query` / `backfill` |

`validate` 子命令将前两个校验技能的**全部检查项合并**，统一输出**一份审核报告**
（多 sheet Excel + HTML + JSON），替代原来的多份独立报告。

## 触发条件

当用户表达以下意图时，触发本技能：
- "检查/校验软件组件清单（格式、内容、路径）"
- "审核组件清单，输出报告"
- "查询组件清单中部件编号为空的行的 PDM 部件信息"
- "回填部件编号到组件清单"

## 子命令详解

### 1. validate - 综合校验（统一审核报告）

```
python main.py validate <excel_path> [--config <cfg>] [--output-dir <dir>]
                         [--product-model <X>] [--work-group-1 <T1>]
                         [--work-group-2 <T2>] [--work-group-3 <T3>]
                         [--no-path-check] [--log-level <LEVEL>]
```

> ⚠️ **检查对象必须是 Excel 的【软件结构】sheet**。validate 会自动优先检查该 sheet
> （仅当其不存在时才回退到智能选 sheet 并告警）。AI 调用时不要自行选择其他 sheet。

**校验流程（合并两个源技能）：**
1. **表头验证**：检查 13 个必需列是否齐全（来自 wx）
2. **逐行规则校验**：规则引擎执行 11 条规则（必填字段、第一行层次、部件类别枚举、
   外部组件要求、层次间隔、系统硬件、外部/普通组件详情、下层维护禁止、产品一致性、
   名称首字符等）（来自 wx）
3. **API 一致性检查**：调用 PDM 部件 API / iComp 第三方组件 API，对比名称/类别一致性，
   生成修正建议（不自动修正）（来自 wx）
4. **文档格式检查**：检查最新模板（"软件结构"sheet）、必需列、数据完整性；
   从文件名提取12位文档编号并调用 PDM 文档接口（`/v1/pdm/doc_detail`、`/v1/pdm/doc_info`）
   查询文档信息（来自 addDir）
5. **部件编号校验**：调用 PDM 校验接口（`/v1/pdm/verify_part`）批量校验部件编号是否存在
   （仅校验"组件/软件模块"）（来自 addDir）
6. **代码库/目录校验**：调用 Gerrit 拆分/校验接口（`/v1/gerrit/split_repo_path`、
   `/v1/gerrit/check_repo`），拆分代码库名称与路径并校验存在性（来自 addDir）
7. **组件路径校验**：调用 Gerrit 路径校验接口（`/v1/gerrit/check_path`），
   验证填写的组件目录是否真实存在于代码库中（来自 addDir）
8. **生成统一审核报告**

**统一审核报告（Excel 多 sheet，按实际顺序）：**
- `逐行校验明细`：wx 规则引擎逐行结果（含修正建议与"错误信息详情"列）
- `文档格式检查`：addDir 格式检查结果
- `PDM编号校验`：PDM 部件编号校验结果
- `代码库拆分校验`：代码库拆分 + 路径校验结果
- `组件的测试-UT-FT代码目录校验`：附加代码目录校验结果
- `校验汇总`：各类校验的通过率统计 + 综合结论

同时生成 **HTML 报告**（可视化）与 **JSON 报告**（机器可读）。

### 2. query - PDM 部件编号查询（原样保留自 pdm-query2report）

```
python main.py query <excel_path> [--output-format csv|xlsx|json|html] [--dry-run]
```

读取"软件结构"页签，对**部件编号为空**的行，用中文名称/英文名称/英文代号多策略调用
PDM 查询接口（`/v1/pdm/query_part`），生成查询结果报告（含全局唯一"序号"，支持多匹配）。

**查询结果新增字段**（接口返回，用于多编号自动筛选）：
- `产品编号`（productNo）、`产品`（product）、`项目`（project）

**第一级部件基准提取**：自动识别第一级部件（部件类别=系统/软件），查询其产品/项目
（取"生命周期状态=审批完成 且 产品/项目非空"的有效候选第一个）作为基准，在查询
输出中展示，供多编号回填自动筛选。

### 3. backfill - 按序号回填（原样保留自 pdm-query2report）

```
python main.py backfill <excel_path> --result-file <查询结果文件> --result-ids <序号列表> [--dry-run] [--no-prompt]
```

根据查询报告中的**序号**精确回填部件编号到原 Excel（黄色高亮已修改单元格），
生成差异报告。支持 dry-run 预览、--no-prompt 自动模式。

**多编号自动筛选**：当同一源行查询到多个不同部件编号（冲突）时，自动按第一级部件
基准（产品/项目）筛选：
- 恰好 1 个候选与基准一致 → **自动选中**该序号回填（提示"已按基准自动匹配"）
- 0 个或多个候选与基准一致 → 保留冲突拦截，提示中**带各候选的产品/项目信息**供用户判断

## 输入参数

### validate 子命令
| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `excel_path` | string | 是 | - | 组件清单 Excel 文件路径 |
| `--config` | string | 否 | `assets/config/default_config.yaml` | 配置文件路径 |
| `output_dir` | string | 否 | Excel 同目录 | 统一审核报告输出目录 |
| `product_model` | string | 否 | `null` | 产品规格型号（跨产品一致性检查） |
| `work_group_1/2/3` | string | 否 | `null` | 工作组类编号（TEAM1/2/3） |
| `no_path_check` | bool | 否 | `false` | 禁用 Gerrit 路径校验（路径校验接口调用） |
| `log_level` | string | 否 | `"INFO"` | 日志级别 |

### query / backfill 子命令
见 pdm-query2report-skill 原参数（`--output-format`、`--result-file`、`--result-ids`、
`--dry-run`、`--no-prompt`）。

## 输出结果

### validate 成功输出
```json
{
  "status": "success",
  "total_rows": 156,
  "passed": 148,
  "failed": 8,
  "correction_suggestions": 12,
  "overall_status": "不通过",
  "excel_report": "路径/_审核报告_时间戳.xlsx",
  "html_report": "路径/_审核报告_时间戳.html",
  "json_report": "路径/_审核报告_时间戳.json"
}
```

### query 输出
查询结果报告（XLSX/CSV/JSON/HTML）

### backfill 输出
- 回填后文件（部件编号已填回，黄色高亮）
- 差异报告（Excel + CSV）

## 依赖库

```
pandas>=1.3.0,<2.1
openpyxl>=3.0.0,<4.0
requests>=2.25.0,<3.0
pyyaml>=5.4.0,<7.0
xlsxwriter>=1.3.0,<4.0
numpy>=1.21,<2.0
beautifulsoup4>=4.11,<5.0
tqdm>=4.60,<5.0
```

完整依赖清单见 `requirements.txt`（包含版本上限约束，云端全新环境必需）。

## 执行方式

### OpenClaw/co-mind 技能调用
```
python main.py validate "$excel_path" --output-dir "$output_dir"
```

### CLI 调用
```bash
# 综合校验
python main.py validate 软件组件清单.xlsx --output-dir ./output
# PDM查询
python main.py query 软件组件清单.xlsx --output-format xlsx
# 回填
python main.py backfill 软件组件清单.xlsx --result-file 查询结果.xlsx --result-ids 1,2,3
```

## 环境与认证

- iComp 组件 API：`icomp.zte.com.cn`（validate 第三方组件）
- PDM 校验 API：`POST /v1/pdm/verify_part`（validate 部件编号校验，默认 `http://10.57.153.195:8080`，可用 `PDM_VERIFY_API_URL` 覆盖）
- PDM 文档 API：`GET /v1/pdm/doc_detail`、`GET /v1/pdm/doc_info`（validate 文档信息查询，默认 `http://10.57.153.195:8080`，可用 `PDM_DOC_DETAIL_API_URL` / `PDM_DOC_INFO_API_URL` 覆盖）
- PDM 查询 API：`POST /v1/pdm/query_part`（query 部件编号查询，默认 `http://10.57.153.195:8080`，可用 `PDM_QUERY_API_URL` 覆盖；超时 60 秒）
- Gerrit 拆分 API：`GET /v1/gerrit/split_repo_path`（validate 代码库/目录拆分，默认 `http://10.57.153.195:8080`，可用 `GERRIT_SPLIT_API_URL` 覆盖）
- Gerrit 校验 API：`POST /v1/gerrit/check_repo`（validate 代码库存在性校验，默认 `http://10.57.153.195:8080`，可用 `REPO_CHECK_API_URL` 覆盖）
- Gerrit 路径校验 API：`POST /v1/gerrit/check_path`（validate 路径存在性校验，默认 `http://10.57.153.195:8080`，可用 `PATH_CHECK_API_URL` 覆盖；分支默认 `master`，可用 `GERRIT_CHECK_BRANCH` 覆盖）
- 认证环境变量：`coclaw_empno` / `coclaw_token`（或 RDC_EMP_NO / RDC_AUTH_TOKEN / X_Emp-No / X_Auth-Value）

## 🤝 执行后交互

`validate` 执行完成后输出统一审核报告。技能**不自动修改 Excel**——所有校验仅生成修正建议
（`correction_suggestions`），不写回源文件，由用户依据 `逐行校验明细` sheet 中的
"错误信息详情"与修正建议手动修订。如需自动修正，请使用 `query`/`backfill` 子命令
（仅回填部件编号）。

## 注意事项

1. `validate` 的 addDir 部分仅对"组件"和"软件模块"类别部件执行编号/代码库/路径校验
2. 若格式检查不通过（非文档编号问题），会跳过接口校验
3. Gerrit 路径校验通过 `/v1/gerrit/check_path` 接口完成，无需本地 clone；
   可用 `--no-path-check` 跳过
4. `query`/`backfill` 不修改源 Excel（backfill 生成新文件）
5. 运行环境要求 **Python 3.8 及以上**（最低 3.8，推荐 3.11，依赖已验证）。
   注意：Python 3.7 及以下无法运行——代码使用了 `dataclasses`（3.7+ 标准库）与
   内建泛型注解（如 `List[str]` 作为运行时注解，3.9 起原生支持，3.8 需 `typing` 导入），
   且 `requirements.txt` 中部分依赖（如 `numpy>=1.21`）不再支持旧版本 Python。

## 退出码

- `0` - 成功（validate 全部通过 / query / backfill 成功）
- `1` - 校验失败、有错误，或执行错误（文件不存在、配置错误等）
- `130` - 用户中断（KeyboardInterrupt）

## 参考文档
- 字段规格：`references/field-specification.md`
- 验证规则：`references/validation-rules.md`
- API接口：`references/api-documentation.md`
- 错误处理：`references/error-handling.md`
- 故障排查：`references/troubleshooting.md`
- 使用说明：`references/usage.md`
- 软件组件清单模板：
  https://i.zte.com.cn/index/ispace/#/space/6ceb1c0d972d48bb9568b0842f27bd0e/wiki/page/a442ea70abfc48fbbfd5f971ceeced50/view
