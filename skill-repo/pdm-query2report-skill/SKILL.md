---
name: pdm-query2report-skill
version: 5.14.0
description: 读取《软件组件清单》Excel的"软件结构"页签,对部件编号为空的行,使用中文名称/英文名称/英文代号查询PDM,生成汇总报告(支持CSV/XLSX多种格式)。✨ 支持返回多个匹配部件编号（相同部件编号只保留最新版本）**支持根据查询结果序号回填**：允许精确选择多匹配中的特定记录进行回填。纯Python架构，零pandas/NumPy依赖。CSV科学计数法问题已彻底解决（Excel文本公式方案）。不修改源Excel文件。v5.14.0重大改进：优化Excel读取性能，解决16,384列卡死问题，使用read_only模式和列范围检测。
tags: ["数据分析"]
author: 洪伟韬10306212
---

# pdm-query2report-skill (v5.14.0 - 优化Excel读取性能)

## 概述

本技能用于读取《软件组件清单》Excel文件的"软件结构"页签，对"部件编号"为空的行执行PDM查询，并生成汇总报告。**v5.14.0核心改进**：解决超大列数（16,384列）Excel文件的性能瓶颈，使用`read_only`模式和智能列范围检测。

### 🎯 核心特性

- ✅ **纯Python架构**：使用openpyxl替代pandas，无需NumPy/pandas依赖
- ✅ **零外部技能依赖**：完全自包含，仅依赖requests和openpyxl
- ✅ **智能多策略查询**：
  - 策略1: 多条件组合查询（中文名+英文名+英文代号）
  - 策略2: 单条件查询（优先级：中文名→英文名→英文代号）
- ✅ **多格式输出**：XLSX, CSV
- ✅ **自动去重**：相同部件编号只保留版本号最大的那条记录
- ✅ **CSV科学计数法彻底解决**：使用Excel文本公式 `="数字"` 格式
- ✅ **序号回填（v4.8.0核心）**：
  - 用户指定查询报告中的**序号**进行回填（如3,4,5）
  - 支持多匹配时精确选择某个特定匹配（源行号10有2个匹配，序号6和7，可分别回填）
  - **完全消除歧义**：不再需要"源行号"概念，直接使用报告中的序号ID
- ✅ **Dry-run模式**：支持模拟运行，不实际查询或回填
- ✅ **生产级错误处理**：完善的异常捕获、重试逻辑和详细日志
- ✅ **超大列数优化（v5.14.0）**：使用`read_only`模式 + 智能列范围检测，解决16,384列卡死问题

## 版本演变

- **v5.14.0 (2026-06-23)**: 🔥 **优化Excel读取性能**；使用`read_only=True`模式；智能检测有效列范围（扫描前100行+扩大扫描）；限制最大读取列数（默认200列）；解决超大列数（如16,384列）文件卡死问题
- **v5.13.0 (2026-05-28)**: ✨ 进一步简化标题判断逻辑，直接基于未覆盖空行数量确定完成状态，去除冗余条件判断，逻辑更简洁高效
- **v5.9.0 (2026-05-28)**: ✨ 优化回填完成状态提示；改进标题和状态描述的准确性；提供更清晰的状态汇总信息
- **v5.8.0 (2026-05-28)**: ✨ 增强回填完成状态展示；新增部分/全部完成智能判断；详细完成情况汇总；精准补充回填建议；优化用户体验与可读性
- **v5.7.0 (2026-05-27)**: 🔥 架构重构：验证逻辑模块化，职责分离；交互流程优化；支持--no-prompt跳过警告和最终确认；backfill_by_result_ids函数精简；提升数据安全性与用户可控性
- **v4.8.0 (2026-05-22)**: 🔥 序号回填革命；支持使用查询结果序号进行回填；参数变更`--source-rows` → `--result-ids`；精确控制多匹配
- **v4.7.0 (2026-05-22)**: 🔥 源行号简化；使用Excel实际行号；回填参数变更；输出字段简化
- **v4.3.0 (2025-05-22)**: ✨ 交互式选择功能；非交互模式
- **v4.2.0 (2025-05-22)**: ✨ 支持返回多个匹配结果；智能去重
- **v4.1.0 (2025-05-22)**: ✨ 新增回填功能；新增差异对比报告
- **v3.8.0 (2025-05-22)**: CSV科学计数法问题彻底解决

## 架构说明

### 文件结构

```
pdm-query2report-skill/
├── SKILL.md              # 技能文档（本文件）
├── requirements.txt      # 依赖库列表
└── scripts/
    ├── __init__.py       # Python包标记
    └── main.py           # ⭐ 单一入口（包含所有功能模块）
```

### 字段体系（v4.8.0序号回填）

| 字段名 | 含义 | 示例 | 用途 |
|--------|------|------|------|
| **序号** | 查询结果中的**唯一递增ID**（全局唯一） | 1, 2, 3... | ✅ **回填时使用此序号** |
| **源行号** | Excel实际行号（仅用于显示和定位） | 2, 3, 4... | 📍 内部定位用，用户无需关心 |
| **部件编号** | PDM返回的部件编号（唯一标识） | 130000185131 | 🎯 回填的值 |

## 触发条件

当用户需要：
- 查询Excel中部件编号为空行的PDM部件信息
- 返回所有可能的匹配结果（而非仅第一条）
- 查看同一查询的多个匹配版本
- 使用查询序号精确回填
- 回填部件编号到原Excel文件
- 生成查询报告和差异对比

### 文件要求

- **文件格式**：`.xlsx` 或 `.xls`
- **页签名称**："软件结构"（如不存在则使用第一个工作表）
- **必要列**：`部件编号`、`中文名称`、`英文名称`、`英文代号`

## v5.14.0 性能优化详解

### 问题背景
某些Excel文件列数极大（如16,384列），使用openpyxl默认模式会加载所有列，导致：
- 内存占用过高
- 读写速度极慢
- 甚至卡死无响应

### 解决方案

1. **使用`read_only=True`模式**：
   - 流式读取，不加载整个工作簿到内存
   - 大幅减少内存占用

2. **智能列范围检测**：
   ```python
   # 扫描前100行确定最大有内容的列
   max_col_with_data = 0
   for row in ws.iter_rows(max_row=100, values_only=True):
       for col_idx, cell_value in enumerate(row, 1):
           if cell_value is not None and str(cell_value).strip() != "":
               if col_idx > max_col_with_data:
                   max_col_with_data = col_idx
   ```

3. **限制最大读取列数**：
   ```python
   read_cols = min(max_col_with_data + 5, 200)  # 最多读取200列
   ```

4. **只读取有效列**：
   ```python
   rows = list(ws.iter_rows(values_only=True, max_col=read_cols))
   ```

### 优化效果

| 指标 | 优化前 | 优化后 |
|------|--------|--------|
| 内存占用 | 高（加载16,384列） | 低（仅加载有效列） |
| 读取速度 | 极慢/卡死 | 快速完成 |
| 适用场景 | 列数较少（<100） | 任意列数（推荐<200有效列） |

## 执行流程

### 查询流程

1. **智能列检测**：扫描前100行，确定实际有内容的列范围（最多200列）
2. **读取Excel**：使用`read_only=True` + `max_col`限制，仅加载有效列
3. **筛选空行**：找出"部件编号"列为空的所有数据行
4. **执行查询**：多条件组合 → 单条件降级策略
5. **去重处理**：相同部件编号只保留版本号最大的记录
6. **生成输出记录**：分配唯一序号

### 序号回填流程

1. **读取查询结果**：支持CSV/XLSX/JSON/HTML格式
2. **建立序号映射**：`{序号: 查询记录}`
3. **验证检查**：序号存在性、冲突检测、部件编号非空
4. **执行回填**：根据源行号定位单元格，填入部件编号
5. **高亮显示**：已修改单元格添加黄色背景
6. **生成文件**：
   - 回填后文件（带高亮）
   - 差异报告（Excel + CSV）

## 输入/输出规范

### query子命令

```bash
python main.py query <excel_file> [--output-format <格式>]
```

**输出**：查询结果报告（XLSX/CSV/JSON/HTML）

### backfill子命令

```bash
python main.py backfill <excel_file> --result-file <查询结果文件> --result-ids <序号列表> [--dry-run] [--no-prompt]
```

**输出**：
- 回填后文件（部件编号已填回）
- 差异报告（Excel + CSV）

## 技术细节

### 智能列检测算法

```python
def read_excel_with_openpyxl(file_path, sheet_name=None):
    wb = load_workbook(file_path, data_only=True, read_only=True)
    ws = wb[sheet_name] if sheet_name else wb.active

    # 检测有内容的列
    max_col_with_data = 0
    for row in ws.iter_rows(max_row=100, values_only=True):
        for col_idx, cell_value in enumerate(row, 1):
            if cell_value is not None and str(cell_value).strip() != "":
                max_col_with_data = max(max_col_with_data, col_idx)

    # 限制读取列数
    read_cols = min(max_col_with_data + 5, 200)
    rows = list(ws.iter_rows(values_only=True, max_col=read_cols))

    # 处理表头和数据行...
```

### 序号回填算法

```python
def backfill_by_result_ids(original_file, query_results, valid_ids):
    id_to_result = {r["序号"]: r for r in query_results}
    wb = load_workbook(original_file)
    ws = wb.active

    # 定位部件编号列
    part_no_col = find_column_index(ws[1], "部件编号")

    # 执行回填
    for result_id in valid_ids:
        result = id_to_result[result_id]
        source_row = int(result["源行号"])
        part_no = result["部件编号"]
        ws.cell(row=source_row, column=part_no_col, value=part_no)

    wb.save(new_file)
    wb.close()
```

## 错误处理

- **文件不存在**：`FileNotFoundError`
- **缺少必要列**：`ValueError` 列出缺失列
- **认证缺失**：启动时检查环境变量
- **PDM查询异常**：自动重试（最多2次）
- **序号不存在**：回填时标记"未找到"，不影响其他序号
- **读取超时**：超时后自动重试

## 性能与并发

- **Excel读取**：使用`read_only`模式，流式处理
- **列范围限制**：最多200列，避免性能劣化
- **查询超时**：每请求15秒
- **重试机制**：最多2次，指数退避
- **分页查询**：每页100条，最多10页

## 依赖清单

```
requests>=2.25.0
openpyxl>=3.0.0
```

## 使用示例

### 查询模式

```bash
# 基本查询
python main.py query 软件组件清单.xlsx

# 指定输出格式
python main.py query 软件组件清单.xlsx --output-format json
```

### 回填模式

```bash
# 根据查询结果回填（指定序号）
python main.py backfill 软件组件清单.xlsx \
  --result-file 查询结果_20260623_102909.xlsx \
  --result-ids 1,2,3,4,5

# 预览回填（不实际修改）
python main.py backfill 软件组件清单.xlsx \
  --result-file 查询结果.xlsx \
  --result-ids 1,2,3 \
  --dry-run

# 自动模式（跳过交互确认）
python main.py backfill 软件组件清单.xlsx \
  --result-file 查询结果.xlsx \
  --result-ids 1,2,3 \
  --no-prompt
```

## 注意事项

1. **超大列数文件**：v5.14.0已优化，建议有效数据列不超过200列
2. **序号唯一性**：序号在查询报告中全局唯一，可跨行混合使用
3. **回填覆盖**：同一源行号的多个序号回填时，后者会覆盖前者
4. **查询时效性**：每次回填会重新查询PDM，确保数据最新

---

**v5.14.0 性能优化：超大列数Excel不再是问题！** 🚀
