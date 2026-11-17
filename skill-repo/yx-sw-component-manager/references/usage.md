# yx-sw-component-manager 使用说明

## 概述

本技能统一了三个源技能的功能：

| 子命令 | 功能 | 来源 |
|--------|------|------|
| `validate` | 组件清单综合校验（格式/逐行规则/编号/代码库/路径/API一致性），输出统一审核报告 | wx-sw-component-list-check + component-manifest-verify-addDir |
| `query` | 对部件编号为空的行查询 PDM 部件信息，生成查询结果报告 | pdm-query2report-skill |
| `backfill` | 按查询结果序号回填部件编号到原 Excel，生成差异报告 | pdm-query2report-skill |

## 快速开始

### 1. 环境准备

确保已安装 Python 3.8+ 和必要的依赖：

```bash
cd <技能目录>
pip install -r requirements.txt
```

运行环境要求 **Python 3.8 及以上**（推荐 3.11）。代码使用了 `dataclasses` 与内建泛型注解，
且部分依赖（如 `numpy>=1.21`）已不支持 Python 3.7 及以下版本。

### 2. 配置认证信息

认证信息通过环境变量注入（各 API 客户端按以下候选顺序读取）：

```bash
# 工号：coclaw_empno（首选）或 RDC_EMP_NO / X_Emp-No / X_EMP_NO / X-Emp-No
export coclaw_empno="your_employee_number"

# Token：coclaw_token（首选）或 RDC_AUTH_TOKEN / X_Auth_Value / X_AUTH_VALUE / X-Auth-Value
export coclaw_token="your_token_here"
```

**注意**：
- `query`/`backfill` 的查询阶段与 validate 的 Spring Boot 接口（PDM 校验/文档/查询、Gerrit 拆分/校验/路径）需要认证头 `X-Emp-No` / `X-Auth-Value`
- 部件详情 API（`PartAPIClient`，wx 逐行 API 一致性检查）使用固定 Header `X-System-Code: 100000461002`，无需工号/Token 认证
- 第三方组件 API（`ComponentAPIClient`，iComp）同样需要 `coclaw_empno` / `coclaw_token`

### 3. 运行验证

所有功能统一通过 `main.py` 入口调用：

```bash
# 综合校验（输出统一审核报告）
python main.py validate /path/to/组件清单.xlsx --output-dir ./output

# PDM 查询（生成查询结果报告）
python main.py query /path/to/组件清单.xlsx --output-format xlsx

# 按序号回填
python main.py backfill /path/to/组件清单.xlsx --result-file 查询结果.xlsx --result-ids 1,2,3
```

## 输入文件要求

### Excel 文件结构

组件清单数据位于 Excel 的 **【软件结构】sheet**，技能**必须优先检查该 sheet**（历史教训：
之前出现过 AI 智能选择误选到其他页面，导致校验对象错误）。处理规则：

1. **优先使用【软件结构】sheet**：只要工作簿中存在名为"软件结构"的工作表，一律使用它
2. **仅在【软件结构】不存在时回退**：回退到智能选择（与必需列匹配度最高的 sheet），
   并输出警告日志提示确认检查对象
3. 输出日志显示所选 sheet 与匹配分数

### validate 的必需列

wx 逐行规则引擎要求以下 **13 个必需列**（表头验证，缺少任一列则终止处理）：

| 序号 | 列名 | 必填 | 说明 |
|------|------|------|------|
| 1 | 层次 | 是 | 部件层级，顶层为 0 |
| 2 | 部件类别 | 是 | 系统/软件/软件子系统/软件模块/组件/外部第三方组件 |
| 3 | 中文名称 | 部件编号为空时必填 | |
| 4 | 英文名称 | 部件编号为空时必填 | |
| 5 | 英文代号 | 部件编号为空时必填 | |
| 6 | 组件版本 | 否 | |
| 7 | 组件资产类型 | 否 | |
| 8 | 服务制品类型 | 否 | |
| 9 | 服务等级 | 否 | |
| 10 | 部件编号 | 外部第三方组件必填 | |
| 11 | 组件功能说明 | 否 | |
| 12 | 组件接口说明 | 否 | |
| 13 | 备注 | 否 | |

### addDir 校验的扩展列（最新模板）

`validate` 的 addDir 部分（文档格式检查/PDM编号/代码库/路径校验）还需要最新模板中的以下列：

- `代码库/目录`
- `是否核心组件/模块`
- `组件对应的测试代码目录`
- `组件对应的UT代码目录`
- `组件对应的FT代码目录`
- `是否航天级组件`
- `组件/模块代码量统计专用分支`

若缺少这些列，格式检查会提示"未使用最新模板"并给出模板链接。

## 子命令详解

### validate - 综合校验（统一审核报告）

```
python main.py validate <excel_path> [--config <cfg>] [--output-dir <dir>]
                         [--product-model <X>] [--work-group-1 <T1>]
                         [--work-group-2 <T2>] [--work-group-3 <T3>]
                         [--no-path-check] [--log-level <LEVEL>]
```

**校验流程**：
1. **加载 Excel 并选择【软件结构】sheet**（不存在时回退智能选择并告警）
2. **表头验证**：检查 13 个必需列是否齐全（wx）
3. **逐行规则校验**：规则引擎执行 11 条规则（wx）
4. **API 一致性检查**：调用 PDM 部件 API / iComp 第三方组件 API，对比名称/类别一致性，
   生成修正建议（**不自动修正**）（wx）
5. **addDir 校验**：文档格式检查 + PDM 文档详情查询 + 部件编号校验 + 代码库拆分校验 +
   Gerrit 路径校验 + 测试/UT/FT 代码目录校验
6. **生成统一审核报告**（多 sheet Excel + HTML + JSON）

**统一审核报告（Excel 多 sheet）**：
- `逐行校验明细`：wx 规则引擎逐行结果（含"错误信息详情"列）
- `文档格式检查`：addDir 格式检查结果
- `PDM编号校验`：addDir 部件编号校验结果
- `代码库拆分校验`：addDir 代码库拆分 + 路径校验结果
- `组件的测试-UT-FT代码目录校验`：addDir 附加代码目录校验结果
- `校验汇总`：各类校验通过率统计 + 综合结论

同时生成 **HTML 报告**（可视化）与 **JSON 报告**（机器可读）。

### query - PDM 部件编号查询

```
python main.py query <excel_path> [--output-format csv|xlsx|json|html] [--dry-run]
```

- `--output-format` / `-f`：输出格式，默认 `xlsx`
- `--dry-run` / `-d`：模拟运行，不生成文件

读取"软件结构"sheet，对**部件编号为空**的行，用中文名称/英文名称/英文代号多策略调用
PDM 查询接口（`/v1/pdm/query_part`），生成查询结果报告。

**查询结果字段**（全局唯一"序号"，支持多匹配）：
`序号、源行号、中文名称、英文名称、英文代号、部件编号、版本、生命周期状态、部件类型、
所属产品、产品编号、产品、项目、工作组类1、工作组类2、工作组类3、查询策略`

**第一级部件基准提取**：自动识别第一级部件（部件类别=系统/软件），查询其产品/项目
（取"生命周期状态=审批完成 且 产品/项目非空"的有效候选第一个）作为基准，在查询
输出中展示，供多编号回填自动筛选。

### backfill - 按序号回填

```
python main.py backfill <excel_path> --result-file <查询结果文件> --result-ids <序号列表> [--dry-run] [--no-prompt]
```

- `--result-file` / `-r`：查询结果文件路径（支持 CSV、XLSX、JSON、HTML 格式）
- `--result-ids` / `-i`：要回填的查询结果序号，逗号分隔，如 `3,4,5`
- `--dry-run` / `-d`：模拟运行，仅预览不实际回填
- `--no-prompt`：跳过交互确认（含警告确认与最终确认），自动执行

根据查询报告中的**序号**精确回填部件编号到原 Excel（黄色高亮已修改单元格），
生成差异报告（Excel + CSV）。

**多编号自动筛选**：当同一源行查询到多个不同部件编号（冲突）时，自动按第一级部件
基准（产品/项目）筛选：
- 恰好 1 个候选与基准一致 → **自动选中**该序号回填（提示"已按基准自动匹配"）
- 0 个或多个候选与基准一致 → 保留冲突拦截，提示中**带各候选的产品/项目信息**供用户判断

## 输出文件

### validate
在 `--output-dir`（默认 Excel 同目录）生成：
- `<原名>_审核报告_时间戳.xlsx`（多 sheet）
- `<原名>_审核报告_时间戳.html`
- `<原名>_审核报告_时间戳.json`

### query
- `<原名>_查询结果_时间戳.<csv|xlsx|json|html>`

### backfill
- `<原名>_回填后_时间戳.xlsx`（部件编号已填回，黄色高亮）
- `<原名>_差异对比_时间戳.xlsx`
- `<原名>_差异对比_时间戳.csv`

## 环境变量

| 环境变量 | 用途 | 默认值 |
|----------|------|--------|
| `PDM_QUERY_API_URL` | PDM 查询接口（query） | `http://10.57.153.195:8080/v1/pdm/query_part` |
| `PDM_VERIFY_API_URL` | PDM 部件编号校验接口（validate） | `http://10.57.153.195:8080/v1/pdm/verify_part` |
| `PDM_DOC_DETAIL_API_URL` | PDM 文档详情接口（validate） | `http://10.57.153.195:8080/v1/pdm/doc_detail` |
| `PDM_DOC_INFO_API_URL` | PDM 文档信息接口（validate） | `http://10.57.153.195:8080/v1/pdm/doc_info` |
| `GERRIT_SPLIT_API_URL` | Gerrit 代码库拆分接口（validate） | `http://10.57.153.195:8080/v1/gerrit/split_repo_path` |
| `REPO_CHECK_API_URL` | 代码库存在性校验接口（validate） | `http://10.57.153.195:8080/v1/gerrit/check_repo` |
| `PATH_CHECK_API_URL` | Gerrit 路径校验接口（validate） | `http://10.57.153.195:8080/v1/gerrit/check_path` |
| `GERRIT_CHECK_BRANCH` | 路径校验分支（validate） | `master` |
| `ENABLE_PATH_CHECK` | 是否启用代码路径校验（独立运行脚本时） | `true` |
| `coclaw_empno` / `RDC_EMP_NO` 等 | 认证工号 | - |
| `coclaw_token` / `RDC_AUTH_TOKEN` 等 | 认证 Token | - |

## 示例工作流

```bash
# 1. 设置认证
export coclaw_token="eyJhbGciOiJIUzI1NiIs..."
export coclaw_empno="10012345"

# 2. 综合校验
python main.py validate 组件清单.xlsx --output-dir ./output

# 3. 查询部件编号为空的行
python main.py query 组件清单.xlsx --output-format xlsx

# 4. 按序号回填（先 dry-run 预览）
python main.py backfill 组件清单.xlsx --result-file 组件清单_查询结果_xxx.xlsx --result-ids 1,2,3 --dry-run
python main.py backfill 组件清单.xlsx --result-file 组件清单_查询结果_xxx.xlsx --result-ids 1,2,3 --no-prompt
```

## 注意事项

1. `validate` 的 addDir 部分仅对"组件"和"软件模块"类别部件执行编号/代码库/路径校验
2. 若格式检查不通过（非"未提供文档编号"），会跳过数据库（PDM编号/代码库/路径）校验
3. 技能**不自动修正 Excel**——validate 仅生成修正建议，由用户手动修订
4. `query`/`backfill` 不修改源 Excel（backfill 生成新文件）
5. 大型文件处理较慢（涉及大量 API 调用），建议分批处理
