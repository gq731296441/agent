# 错误处理说明

## 错误分类体系

本技能的错误处理按三个子命令划分。整体设计原则：**API 失败不阻塞主流程**、
**回填硬错误必须解决后才能继续**、**埋点上报静默失败不影响主流程**。

### 1. validate 子命令错误

`validate` 由 `scripts/validate/runner.py` 统一编排，任务级错误通过返回 dict 的
`status` 字段区分：

| status | 场景 | 处理 |
|--------|------|------|
| `success` | 校验正常完成（含 failed>0，即校验不通过） | 退出码 0 |
| `error` | 任务未真正执行（文件不存在/加载失败/表头失败/异常） | 退出码 1 |

#### 1.1 入口级错误
- **Excel 文件不存在** → `{'status': 'error', 'message': 'Excel文件不存在: <path>'}`
- **配置文件缺失/加载失败** → 返回空配置，缺失 `required_columns` 时终止
- **表头验证失败**（缺少 13 个必需列中的任一列）→ `{'status': 'error', 'message': '表头验证失败，缺少必需列: [...]'}`

#### 1.2 逐行规则引擎错误（wx）
每条规则失败返回 `{rule, passed, message, suggested_value, field, current_value, row}`：
- `row = row_index + 2`（表头在第 1 行，数据从第 2 行开始）
- `warning: True` 的条目（如第一行层次不为 0）不影响行"失败"判定
- 修正建议统一收集到 `correction_suggestions`（**不自动写入 Excel**）

常见错误类型见 `references/validation-rules.md` 的错误分类汇总。

#### 1.3 API 一致性检查错误（wx）
- `api_no_result`：API 未返回部件信息（建议确认部件编号）
- `api_exception`：API 调用异常（建议检查网络/稍后重试），继续处理下一行

#### 1.4 addDir 校验错误
- **格式检查不通过** → 报告 `format_check` 错误；若非"未提供文档编号"，跳过数据库校验
- **PDM 编号校验不通过/部分不通过** → `pdm_number_check` 错误
- **代码库拆分校验不通过/部分不通过** → `code_repo_check` 错误
- **代码路径校验不通过** → `code_path_check` 错误
- **测试/UT/FT 代码目录校验不通过** → `additional_code_check` 错误

以上 addDir 错误统一并入 `errors` 与 `correction_suggestions`，与 wx 错误同结构
（`row`/`type`/`message`/`source`，`source` 标识 `component-manifest-verify-addDir`），
使 Excel/JSON/HTML 三份报告内容一致。

### 2. query 子命令错误

`query` 由 `scripts/pdm_query/main.py` 的 `run_query` 执行：

| 场景 | 处理 |
|------|------|
| 缺少必要列（部件编号/中文名称/英文名称/英文代号） | 抛出 `ValueError`，main.py 捕获后退出码 1 |
| Excel 读取失败 | 抛出异常，退出码 1 |
| 部件编号为空的行查询无匹配 | 记录到 `no_match_details`（不中断），生成报告时展示 |
| API 认证缺失（无 coclaw_empno/token） | 接口返回空，记录 warning，行记为未匹配 |
| 用户中断 | 退出码 130 |

### 3. backfill 子命令错误

`backfill` 回填前执行 `validate_backfill_operation` 四类检查（`errors` 为硬错误，必须解决才能继续）：

| 错误类型 | 说明 |
|----------|------|
| `missing_ids` | 用户指定的序号在查询结果文件中不存在 |
| `conflict` | 同一源行号对应多个不同部件编号（回填冲突） |
| `empty_partno` | 所选序号对应的部件编号为空 |
| `uncovered`（警告） | 原始文件中还有部件编号为空的行未被覆盖（仅警告，不阻断） |

**冲突自动筛选**：若仅存在冲突类错误，先按第一级部件基准（产品/项目）自动筛选：
- 恰好 1 个候选与基准一致 → 自动选中，剔除其余冲突序号
- 0 个或多个候选与基准一致 → 保留冲突拦截，提示各候选的产品/项目信息供用户判断

**交互确认**：
- 有警告无错误时：询问是否继续（`--no-prompt` 跳过）
- 有硬错误时：`--no-prompt` 自动终止；否则询问是否重新输入序号
- 回填前最终确认：`--no-prompt` 跳过

## API 客户端错误处理（BaseAPIClient）

所有 API 客户端继承 `BaseAPIClient`（`scripts/api_clients/base_client.py`）：

- **重试机制**: 默认 3 次，指数退避（1s / 2s / 4s），重试序列为 1.0s、2.0s、4.0s
- **非 200 状态码**: 记录 warning，不重试（直接返回 None）
- **网络异常（超时/连接失败）**: 记录 warning，按退避重试，最终返回 None
- **返回 None 的处理**: 各客户端记录 warning；调用方据此判定失败（如部件不存在/未匹配）

### 认证错误
- 各 Spring Boot 客户端与 iComp 客户端需要 `X-Emp-No` / `X-Auth-Value`
- 缺少工号 → 记录 `未找到 X-Emp-No（环境变量 coclaw_empno）`
- 缺少 Token → 记录 `未找到 X-Auth-Value（环境变量 coclaw_token）`
- 认证环境变量候选：`coclaw_empno` / `RDC_EMP_NO` / `X_Emp-No` / `X_EMP_NO` / `X-Emp-No`；
  `coclaw_token` / `RDC_AUTH_TOKEN` / `X_Auth_Value` / `X_AUTH_VALUE` / `X-Auth-Value`

## 埋点上报错误（静默失败）

`main.py` 在任务结束时调用 `scripts/monitor/report_usage.py` 的 `send_report` 上报使用记录：

- **设计原则**：静默失败——上报异常绝不影响技能主流程（超时 3 秒、快速失败）
- **离线兜底**：上报失败时写入 `.skill_usage_cache/` 缓存文件，可后续补传
- **幂等**：`event_id` 全局唯一，重复上报服务端不重复入库
- **状态枚举**：`success` / `failed` / `cancelled` / `partial`
- 上报接口：`POST /v1/cmai/skill_usage/report`（默认 `http://10.57.153.195:8080`）

## 常见错误解决方案

### 1. 表头不匹配
```
错误: 表头验证失败，缺少必需列
解决:
  - 使用官方模板文件（13 个必需列）
  - 检查是否漏掉必需的列
  - 确认列名拼写完全一致
```

### 2. 部件编号在 PDM 中不存在
```
错误: normal_component_detailed / pdm_number_check
解决:
  - 检查部件编号是否正确输入
  - 确认部件在 PDM 系统中存在且生命周期状态为"审批完成"
  - 如需新部件，提交创建申请
```

### 3. 外部第三方组件校验失败
```
错误: external_component_requirement / external_component_detailed
解决:
  - 确认外部组件必须填写部件编号
  - 部件在 iComp 中必须存在且状态不为"废弃"
  - 外部组件不允许维护下层部件、层次不能为 0
```

### 4. API 连接失败 / 认证失败
```
错误: api_exception / 接口返回空
解决:
  - 设置环境变量 coclaw_token 或 X_Auth_Value
  - 设置环境变量 coclaw_empno 或 X_Emp-No
  - 确保 Token 未过期、网络可达 10.57.153.195:8080
```

### 5. 回填序号不存在 / 冲突
```
错误: missing_ids / conflict
解决:
  - 确认 `--result-ids` 中的序号在查询结果文件中存在
  - 同一源行号多个不同部件编号时，只选择一个序号回填
  - 多编号冲突可依赖第一级基准自动筛选
```

## 调试模式

### 启用详细日志
```bash
python main.py validate 组件清单.xlsx --log-level DEBUG
```
或设置环境变量/修改配置 `logging.level: "DEBUG"`。

### 查看日志
日志默认输出到控制台，同时记录到 `./logs/` 目录：
```bash
tail -f logs/yx_sw_component_manager_*.log
```

### 检查 API 响应
各 API 客户端在 DEBUG 级别输出请求/响应详情（`logger.debug`）。

### 独立运行 addDir 校验
```bash
python scripts/validate/db_and_path_check.py <Excel路径>
```
（支持 `ENABLE_PATH_CHECK` 环境变量控制路径校验）
