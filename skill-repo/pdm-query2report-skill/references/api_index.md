# API 索引

本技能依赖以下外部API和技能：

## 1. studio-pdm-package 技能

提供PDM（产品数据管理）系统的部件查询能力。

### 接口：basic_query

**用途**：根据条件查询PDM部件基本信息

**调用方式**：
- 通过 `sessions_spawn` 调用 `studio-pdm-package` 技能
- 传递参数：`queryType`, `partType`, `nameEn`, `nameEnAbbr` 等

**参数说明**：
| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| queryType | string | 是 | 查询类型，固定为 "G0001"（部件查询） |
| partType | string | 否 | 部件类别 |
| nameEn | string | 否 | 部件英文名称 |
| nameEnAbbr | string | 否 | 部件英文代号 |

**返回结构**：
```json
{
  "items": [
    {
      "partNo": "130000206059",
      "nameEn": "Server PSU",
      "lifeCycleStatus": "审批完成",
      "partType": "电源"
    }
  ],
  "total": 1
}
```

**生命周期状态值**：
- `审批完成`（或 "Approved"）: 部件已审批，可直接使用
- `未审批完成`（或 "Pending"、"Draft"）: 部件未完成审批
- 其他状态按未完成处理

### 示例调用代码

```python
import os
import subprocess
import json
import tempfile

def call_studio_pdm_package(query_type: str, **kwargs):
    """
    调用 studio-pdm-package 技能
    注意：实际调用方式需根据OpenClaw技能调用机制调整
    """
    # 这里需要实现真正的技能调用，例如使用 sessions_spawn
    # 由于技能运行在OpenClaw环境中，可能需要通过系统事件触发
    # 伪代码：
    # result = sessions_spawn(
    #     runtime="subagent",
    #     agentId="studio-pdm-package",
    #     task=f"basic_query queryType={query_type} ..."
    # )
    # return json.loads(result)
    pass
```

## 2. excel-tool 技能

提供Excel文件的读取、编辑和保存能力。

### 主要功能

- `read_excel(path, sheet_name)`: 读取Excel文件为DataFrame
- `to_excel(df, path, sheet_name)`: 将DataFrame写入Excel文件

### 示例调用代码

```python
import pandas as pd

# 读取Excel
df = pd.read_excel("input.xlsx", sheet_name="软件结构", dtype=str)

# 修改数据
df.at[row_index, "部件编号"] = "130000206059"

# 保存Excel
df.to_excel("output.xlsx", index=False)
```

## 3. 文件上传机制

OpenClaw接收用户上传的文件，通常会：
1. 将文件保存到临时目录
2. 通过环境变量或参数传递文件路径给技能脚本
3. 技能脚本处理完成后，输出结果文件路径

**文件路径获取**：
- 命令行参数：`sys.argv[1]`
- 环境变量：`INPUT_EXCEL_PATH`
- OpenClaw会话消息中的附件路径

## 认证信息

调用PDM系统需要认证，从OpenClaw环境变量获取：

- `coclaw_token` 或 `RDC_AUTH_TOKEN`: 用户认证Token
- `coclaw_empno` 或 `RDC_EMP_NO`: 用户工号

```python
token = os.getenv("coclaw_token") or os.getenv("RDC_AUTH_TOKEN")
emp_no = os.getenv("coclaw_empno") or os.getenv("RDC_EMP_NO")
```

## 性能考虑

- PDM查询可能较慢，建议每批次处理不超过20行，或使用并发
- 使用 `time.sleep` 避免触发PDM限流
- 查询失败的重试机制（最多2次）

## 错误处理

- API返回错误：记录日志，继续处理下一行
- 网络超时：设置合理timeout（建议10-30秒）
- 认证失败：立即终止并提示用户
