---
name: imap-report-generator
description: IMAP端到端需求交付分析报告生成器，基于HTML/CSS/JS三文件模板自动生成分析报告
generator: co-skill-creator
version: 1.0.0
author: 00294192
tags: ["研发提效", "数据智能"]
triggers: ["iCenter", "Wiki", "空间管理", "页面编辑", "ispace"]
---

## 技能概述

本技能是专门用于生成**IMAP端到端需求交付分析报告**的智能报告生成器。技能通过调用AIP接口获取IMAP需求交付数据，以上传的HTML、CSS、JavaScript三个文件作为完整模板，自动生成包含需求交付瓶颈诊断、活动均衡性分析、周期趋势分析、待改进领域识别、异常需求详单等5个维度的交互式HTML分析报告。

## 使用时机

当用户表达以下意图时，应触发本技能：

- **IMAP报告生成**："生成IMAP端到端需求交付分析报告"、"分析IMAP需求交付情况"
- **API数据获取**："调用接口获取IMAP数据"、"从AIP接口获取需求交付分析数据"
- **模板应用**："以上传的三个文件为模板生成IMAP报告"、"用这个HTML/CSS/JS模板生成报告"

**注意区分**：
- 生成其他类型的报告（如里程碑复盘报告） → 不使用本技能
- 单纯读取iCenter页面 → 使用studio-space-manager技能
- 只上传单个文件作为模板 → 本技能需要HTML+CSS+JS三个文件作为完整模板

## 核心能力

### 1. 三文件模板解析
- **HTML模板解析**：识别报告结构、TAB导航、数据表格容器、图表canvas元素
- **CSS样式解析**：提取完整的样式定义，包括响应式设计、配色方案、布局规则
- **JavaScript逻辑解析**：理解图表绘制函数、TAB切换逻辑、数据绑定方式
- **模板完整性校验**：确保三个文件能正确组合成完整报告

### 2. API接口IMAP数据获取
- **接口调用**：通过POST请求调用AIP接口获取IMAP端到端需求交付分析数据
- **参数传递**：正确组装data_source_url、column_name_source、section_cycle_name等参数
- **数据解析**：解析API返回的JSON数据，提取需求类型、活动周期、时间序列等数据
- **错误处理**：处理网络异常、超时、JSON解析错误等情况

### 3. IMAP报告生成
- **数据映射填充**：将IMAP数据映射到模板的数据结构中
- **HTML报告生成**：基于HTML模板填充IMAP数据生成完整报告
- **CSS样式保留**：保持与模板一致的视觉风格和响应式设计
- **JavaScript适配**：根据IMAP数据调整图表绘制逻辑

### 4. 数据可视化
- **活动周期对比图**：折线图展示各需求类型的7个活动周期，依据获取到的实际需求类型和数据呈现
- **时间序列趋势图**：13个月的时间序列折线图，依据获取到的实际活动周期类型和数据呈现
- **均衡性分析表**：依据获取到的实际需求类型和数据，均值计算和30%阈值异常识别，并整合呈现完整表格
- **BOTTOM3领域表**：各需求类型的短板领域识别，依据获取到的实际活动周期类型和数据呈现
- **异常需求详单**：根据实际获取到的需求类型、分段活动周期类型、分析原因、标识、标题、周期天数进行展示，每种需求类型一张数据表

### 5. 交互式报告
- **TAB导航**：5个分析维度的TAB切换
- **图表交互**：鼠标悬停显示数据点数值
- **响应式设计**：适配桌面和移动端

## 参数说明

| 参数名 | 类型 | 必填 | 说明 | 示例值 |
|--------|------|------|------|--------|
| data_source_url | string | 是 | 数据源URL | "https://aip.zte.com.cn/middle/api/remote/call/xxx?projectGroup_id=14&beginTime=2026-01-01&endTime=2026-07-01" |
| column_name_source | string | 是 | 列名来源 | "PR交付周期" |
| section_cycle_name | array | 是 | 分段周期名称列表 | ["PR分析等待周期","PR分析周期","PR开发等待周期","PR开发周期","PR验证等待周期","PR验证周期","PR系统测试等待周期","PR系统测试周期"] |
| category_column | string | 是 | 分类列名 | "分类1" |
| direction | string | 是 | 方向 | "向低" |
| indicator_type | string | 是 | 指标类型 | "周期类" |
| html_template | string | 是 | HTML模板文件路径 | "/references/template.html" |
| css_template | string | 是 | CSS模板文件路径 | "/references/style.css" |
| js_template | string | 是 | JavaScript模板文件路径 | "/references/script.js" |
| output_html | string | 否 | 输出HTML文件路径 | "/workspace/imap_report.html" |
| output_css | string | 否 | 输出CSS文件路径 | "/workspace/imap_style.css" |
| output_js | string | 否 | 输出JavaScript文件路径 | "/workspace/imap_script.js" |
| report_title | string | 否 | 报告标题 | "IMAP端到端需求交付分析报告" |

## 输入输出格式

### 输入（用户自然语言）
"调用API接口获取IMAP数据，然后根据模板生成IMAP端到端需求交付分析报告，参数如下：
- data_source_url: https://aip.zte.com.cn/middle/api/remote/call/xxx?projectGroup_id=14&beginTime=2026-01-01&endTime=2026-07-01
- column_name_source: PR交付周期
- section_cycle_name: PR分析等待周期,PR分析周期,PR开发等待周期,PR开发周期,PR验证等待周期,PR验证周期,PR系统测试等待周期,PR系统测试周期
- category_column: 分类1
- direction: 向低
- indicator_type: 周期类"

### 内部参数提取
```json
{
  "action": "generate_imap_report",
  "data_source_url": "https://aip.zte.com.cn/middle/api/remote/call/xxx?projectGroup_id=14&beginTime=2026-01-01&endTime=2026-07-01",
  "column_name_source": "PR交付周期",
  "section_cycle_name": ["PR分析等待周期","PR分析周期","PR开发等待周期","PR开发周期","PR验证等待周期","PR验证周期","PR系统测试等待周期","PR系统测试周期"],
  "category_column": "分类1",
  "direction": "向低",
  "indicator_type": "周期类",
  "html_template": "/references/index.html",
  "css_template": "/references/style.css",
  "js_template": "/references/script.js",
  "output_html": "/workspace/imap_report.html",
  "output_css": "/workspace/imap_style.css",
  "output_js": "/workspace/imap_script.js",
  "report_title": "IMAP端到端需求交付分析报告"
}
```

### 输出（成功响应）
```
✅ IMAP端到端需求交付分析报告生成成功！

📊 报告信息：
- 标题：IMAP端到端需求交付分析报告
- 数据来源：AIP接口（https://aip.zte.com.cn/imap/api/data_analysis/endtoend_dashboard_analyze）
- API参数：
  * data_source_url: https://aip.zte.com.cn/middle/api/remote/call/xxx...
  * column_name_source: PR交付周期
  * section_cycle_name: ["PR分析等待周期","PR分析周期",...]
- 模板：index.html + style.css + script.js
- 生成时间：2026-08-07 09:30:45

📁 生成文件：
- HTML报告：/workspace/imap_report.html
- CSS样式：/workspace/imap_style.css
- JavaScript：/workspace/imap_script.js

📈 报告包含5个TAB页：
1. 需求交付瓶颈诊断 - 识别长周期和超20天异常周期
2. 活动均衡性分析 - 均值计算和30%阈值异常识别
3. 周期趋势分析 - 13个月时间序列趋势和上升周期识别
4. 待改进领域 - BOTTOM3短板领域识别
5. 异常需求详单 - 具体异常需求单据详情

🎯 关键发现：
- 识别X个需求类型的最长活动周期
- 发现Y个超20天的异常等待周期
- 识别Z个呈现上升趋势的周期
```

### 输出（错误响应）
```
❌ IMAP报告生成失败：无法从API接口获取数据。

错误详情：API调用失败（网络错误、超时、JSON解析错误等）。

💡 建议：
- 检查data_source_url是否正确且有效
- 确认API接口参数（column_name_source、section_cycle_name等）是否正确
- 确保网络连接正常
- 检查API返回的错误信息
- 确保上传了HTML、CSS、JS三个模板文件
```

## 执行流程

### IMAP报告生成完整流程
```
1. 解析用户请求 → 识别API参数（data_source_url、column_name_source、section_cycle_name等）和三个模板文件路径
2. 验证模板完整性 → 检查HTML、CSS、JS三个文件都存在（模板文件位于/references目录）
3. 读取HTML模板 → 解析报告结构、TAB导航、数据表格、图表容器
4. 读取CSS模板 → 提取完整样式定义
5. 读取JS模板 → 解析图表绘制函数、TAB切换逻辑
6. 调用API接口 → 通过POST请求调用AIP接口获取IMAP数据
   - 接口URL：https://aip.zte.com.cn/imap/api/data_analysis/endtoend_dashboard_analyze
   - 请求头：Content-Type: application/json
   - 请求体：包含data_source_url、column_name_source、section_cycle_name、category_column、direction、indicator_type
7. 数据解析 → 从API返回数据中提取IMAP需求交付统计数据，同时提取基本信息中的项目组/发布版本名称和数据时间范围
   - 提取报告标题（格式："XXXXX需求交付分析报告"）：
     * 规则1：优先提取"发布版本"后的内容（支持"发布版本XXX"、"发布版本：XXX"、"发布版本: XXX"等形式）
     * 规则2：如果没有"发布版本"字段，则提取"本次分析针对"后的项目组名称（支持"本次分析针对XXX"、"本次分析针对：XXX"等形式）
   - 提取数据时间范围：如果"基本信息"中包含"数据时间范围"字段则提取
   - 动态识别活动周期类型：根据实际数据确定周期类型列表（MR数据或PR数据）
8. 数据映射 → 将IMAP数据填充到模板的数据结构中
9. 生成HTML → 组合模板结构和IMAP数据，同时将HTML中的CSS/JS引用修改为输出文件名（如style.css→imap_style.css，script.js→imap_script.js）
10. 生成CSS → 复制样式文件到输出路径
11. 生成JS → 根据IMAP数据调整图表绘制逻辑并保存到输出路径
12. 保存文件 → 写入/workspace/目录（或用户指定的输出目录，如/workspace/reports）
    ⚠️ 重要：生成的文件不会保存到技能文件夹中，而是保存到工作区目录
13. 生成分享链接 → 系统自动创建免登录链接
14. 返回结果 → 展示报告信息和链接
```

### 模板解析详细流程
```
1. 读取HTML模板
   - 识别DOCTYPE、html、head、body结构
   - 提取<title>中的报告标题占位符
   - 识别<link rel="stylesheet">引用的CSS文件，记录原始CSS引用路径（如href="style.css"）
   - 识别<script>引用的JS文件，记录原始JS引用路径（如src="script.js"）
   - 提取TAB导航结构（.tab-navigation）
   - 识别5个TAB面板（#tab1到#tab5）
   - 提取数据表格结构（.data-table）
   - 识别图表canvas元素（#cycleComparisonChart等）

2. 读取CSS模板
   - 提取CSS变量定义（:root）
   - 解析所有样式规则
   - 识别响应式媒体查询
   - 记录关键样式类：
     * .report-header - 报告头部
     * .tab-navigation - TAB导航
     * .tab-panel - TAB内容面板
     * .key-findings-card - 关键发现卡片
     * .data-table - 数据表格
     * .chart-container - 图表容器
     * .equity-table - 均衡性分析表
     * .trend-summary-table - 趋势汇总表
     * .bottom3-table - BOTTOM3领域表
     * .single-requirements-table - 异常需求详单表

3. 读取JS模板
   - 识别DOMContentLoaded初始化函数
   - 提取TAB导航切换函数（initTabNavigation）
   - 解析图表绘制函数：
     * initCycleComparisonChart() - 活动周期对比图
     * initTrendCharts() - 时间序列趋势图
   - 理解数据结构：
     * 各需求类型的周期数据数组
     * 时间序列数据（13个月）
     * 图表配置（颜色、标签等）
   - 识别窗口resize事件处理

4. 修改HTML中的CSS/JS引用
   - 将模板中的CSS引用（如`<link href="style.css">`）替换为输出文件名（如`<link href="imap_style.css">`）
   - 将模板中的JS引用（如`<script src="script.js">`）替换为输出文件名（如`<script src="imap_script.js">`）
   - 确保生成的HTML能正确加载样式和脚本文件

5. 提取报告标题和时间范围
   - 从"基本信息"字段中提取"发布版本"后的发布版本名称（优先）
   - 如果没有"发布版本"，则提取"本次分析针对"后的项目组名称
   - 从"基本信息"字段中提取"数据时间范围"（如果存在）
   - 将提取的信息用于修改HTML报告标题和头部元数据

6. 动态识别活动周期类型
   - 根据iCenter数据中的实际周期类型确定是MR数据还是PR数据
   - MR数据周期顺序：MR分析等待周期、MR分析周期、MR计划周期（如有）、MR开发等待周期、MR开发周期、MR验收等待周期、MR验收周期
   - PR数据周期顺序：PR分析等待周期、PR分析周期、PR开发等待周期、PR开发周期、PR验证等待周期、PR验证周期、PR系统测试等待周期、PR系统测试周期
   - 按正确的周期顺序生成图表和表格

7. 生成JavaScript图表数据
   - 从iCenter数据中提取各需求类型的周期数据
   - 生成活动周期对比图的JavaScript数据数组（按正确的周期顺序）
   - 生成时间序列趋势图的JavaScript数据对象
   - 动态生成需求类型颜色（使用预定义颜色数组循环分配，支持任意数量的需求类型）
   - 替换JS模板中的硬编码数据为实际数据
   - 确保图表使用从iCenter页面获取的真实数据

8. 提取各分析结论的关键信息
   - 提取"分析结论2-关键"：活动均衡性分析的关键结论（包含超出均值30%的异常活动）
   - 将提取的结论用于替换HTML模板中的分析结论部分
```

### iCenter数据解析流程
```
1. 读取iCenter页面Markdown内容
2. 识别报告类型 → 确认是IMAP端到端需求交付分析
3. 提取关键数据：
   - 需求类型列表（如OTN-业务控制、OTN-网络管理等）
   - 各需求类型的7个活动周期数据
   - 最长非等待周期识别
   - 超20天异常周期识别
   - 活动均衡性分析结果
   - 上升趋势周期识别
   - BOTTOM3领域数据
   - 异常需求详单
4. 转换为JSON数据结构
5. 验证数据完整性
```

## IMAP报告数据结构

### 需求类型周期数据
```json
{
  "demand_types": [
    {
      "name": "OTN-业务控制",
      "cycles": {
        "analysis_cycle": 20.86,
        "analysis_wait": 39.12,
        "development_cycle": 6.21,
        "development_wait": 2.11,
        "plan_cycle": 2.05,
        "acceptance_cycle": 3.19,
        "acceptance_wait": 18.62
      }
    }
    // ... 其他需求类型
  ]
}
```

### 时间序列数据
```json
{
  "trend_data": {
    "months": ["2025-08", "2025-09", ..., "2026-08"],
    "series": {
      "analysis_cycle": [20.06, 10.81, ..., 21.58],
      "analysis_wait": [32.01, 25.12, ..., 45.97],
      // ... 其他周期类型
    }
  }
}
```

### 异常需求详单
```json
{
  "abnormal_requirements": [
    {
      "demand_type": "OTN-业务控制",
      "cycle_type": "MR开发周期",
      "reason": "上升趋势",
      "requirements": [
        {
          "id": "UMEONE-1203422",
          "title": "【应用功能：业务管理-OTN业务管理】内部MR-故障定界定位改进专题-业务领域",
          "cycle_days": 55.0
        }
        // ... 其他异常需求
      ]
    }
  ]
}
```

## 错误处理

| 错误类型 | 错误码 | 处理方式 |
|----------|--------|----------|
| 模板文件缺失 | TEMPLATE_MISSING | 提示需要HTML+CSS+JS三个文件作为模板 |
| 模板文件不完整 | TEMPLATE_INCOMPLETE | 检查三个文件是否能正确组合 |
| API调用超时 | API_TIMEOUT | 提示网络超时，建议检查网络连接或稍后重试 |
| API返回错误 | API_ERROR | 显示API返回的错误信息，检查参数是否正确 |
| JSON解析失败 | JSON_PARSE_001 | 提示API返回数据格式不正确 |
| 网络连接失败 | NETWORK_ERROR | 提示网络异常，检查连接状态 |
| IMAP数据解析失败 | DATA_PARSE_001 | 提示API返回数据格式不正确 |
| 数据字段不匹配 | DATA_MISMATCH | 提示API返回数据结构与模板不兼容 |
| 图表绘制失败 | CHART_001 | 检查数据有效性，提供降级方案 |
| 文件写入失败 | IO_001 | 检查工作区权限和磁盘空间 |

## 示例场景

### 场景1：生成IMAP端到端需求交付分析报告
**用户**："调用API接口获取IMAP数据，然后以上传的三个文件为模板生成IMAP报告，参数如下：
- data_source_url: https://aip.zte.com.cn/middle/api/remote/call/xxx?projectGroup_id=14&beginTime=2026-01-01&endTime=2026-07-01
- column_name_source: PR交付周期
- section_cycle_name: PR分析等待周期,PR分析周期,PR开发等待周期,PR开发周期,PR验证等待周期,PR验证周期,PR系统测试等待周期,PR系统测试周期
- category_column: 分类1
- direction: 向低
- indicator_type: 周期类"
**技能**：
```
✅ IMAP端到端需求交付分析报告生成成功！

📊 报告信息：
- 标题：IMAP端到端需求交付分析报告
- 数据来源：AIP接口（https://aip.zte.com.cn/imap/api/data_analysis/endtoend_dashboard_analyze）
- API参数：
  * data_source_url: https://aip.zte.com.cn/middle/api/remote/call/xxx...
  * column_name_source: PR交付周期
  * section_cycle_name: ["PR分析等待周期","PR分析周期",...]
  * category_column: 分类1
  * direction: 向低
  * indicator_type: 周期类
- 模板：index.html + style.css + script.js
- 生成时间：2026-08-07 09:30:45
- 输出目录：/workspace（工作区根目录）

📁 生成文件：
- HTML报告：/workspace/imap_report.html
- CSS样式：/workspace/imap_style.css
- JavaScript：/workspace/imap_script.js

📈 报告包含5个TAB页：
1. 需求交付瓶颈诊断 - 识别9种需求类型的瓶颈周期
2. 活动均衡性分析 - 发现3个不均衡活动
3. 周期趋势分析 - 18个分段周期呈现上升趋势
4. 待改进领域 - 各需求类型的BOTTOM3领域
5. 异常需求详单 - 47个异常需求单据定位

🎯 关键发现：
- 系统管理的MR分析等待周期最长（43.53天）
- 未配置的MR开发等待周期超出均值133.7%
- 所有9种类型的MR开发周期均呈现上升趋势

💡 文件存储说明：
- 生成的报告文件保存在/workspace/目录（工作区根目录）
- 不会保存到技能文件夹（/skills/imap-report-generator/）中
- 可通过--output-dir参数自定义输出目录，如--output-dir /workspace/reports
```

### 场景2：模板文件缺失
**用户**："用这个HTML模板生成IMAP报告"（只上传了HTML文件）
**技能**：
```
❌ IMAP报告生成失败：模板文件不完整。

错误详情：本技能需要HTML、CSS、JavaScript三个文件作为完整模板。

💡 建议：
- 请同时上传HTML、CSS、JS三个文件
- 三个文件应来自同一个报告模板
- 确保HTML中正确引用了CSS和JS文件
```

### 场景3：API接口调用成功
**用户**："调用API接口获取IMAP数据生成报告"
**技能**：
```
✅ 正在调用API接口...
✅ API调用成功，获取到IMAP端到端需求交付分析数据
✅ 解析IMAP需求交付数据...
✅ 识别到9种需求类型：OTN-业务控制、OTN-网络管理、分组-业务控制...
✅ 提取活动周期数据...
✅ 提取时间序列数据...
✅ 提取BOTTOM3领域数据...
✅ 提取异常需求详单...
✅ 应用模板生成报告...
✅ IMAP报告生成成功！

📁 生成文件（保存到工作区目录，而非技能文件夹）：
- HTML报告：/workspace/imap_report.html
- CSS样式：/workspace/imap_style.css
- JavaScript：/workspace/imap_script.js

💡 说明：所有生成的报告文件都保存在/workspace/目录，与技能文件夹完全分离
```

## 依赖服务

- **requests库**：调用AIP接口获取IMAP数据
- **文件系统**：读写HTML、CSS、JS模板文件和输出文件
- **HTML解析器**：解析和生成HTML结构
- **Canvas图表库**：原生Canvas 2D API绘制折线图
- **分享链接生成**：自动创建免登录访问链接

## 📁 文件存储说明

### 模板文件位置
- HTML模板：`/references/index.html`（或用户指定的路径）
- CSS模板：`/references/style.css`（或用户指定的路径）
- JavaScript模板：`/references/script.js`（或用户指定的路径）

### 输出文件位置
**⚠️ 重要：生成的报告文件不会保存到技能文件夹中！**

- **默认输出目录**：`/workspace/`
  - HTML报告：`/workspace/imap_report.html`
  - CSS样式：`/workspace/imap_style.css`
  - JavaScript：`/workspace/imap_script.js`

- **自定义输出目录**：通过`--output-dir`参数指定
  - 例如：`--output-dir /workspace/reports`
  - 输出：`/workspace/reports/imap_report.html`等

### 技能文件夹 vs 工作区目录

| 目录 | 用途 | 是否写入生成文件 |
|------|------|-----------------|
| `/skills/imap-report-generator/` | 技能代码和模板存储 | ❌ **否**（只读） |
| `/workspace/` | 用户工作区 | ✅ **是**（默认输出目录） |
| `/workspace/reports/` | 用户自定义输出目录 | ✅ **是**（可选） |

**设计原则**：
- 技能文件夹（`/skills/`）仅用于存储技能代码和模板，是**只读**的
- 所有生成的报告文件都保存到**工作区目录**（`/workspace/`）
- 确保技能执行不会修改技能本身的文件
- 用户可以自由指定输出目录，灵活管理生成的报告

## 辅助脚本

本技能提供了一个Python脚本作为参考实现，位于`scripts/generate_imap_report_refactored_v2.py`（重构优化版 - API接口调用）。

**使用方法**：
```bash
python3 scripts/generate_imap_report_refactored_v2.py \
    --data-source-url "https://aip.zte.com.cn/middle/api/remote/call/xxx?projectGroup_id=14&beginTime=2026-01-01&endTime=2026-07-01" \
    --column-name-source "PR交付周期" \
    --section-cycle-name "PR分析等待周期" "PR分析周期" "PR开发等待周期" "PR开发周期" "PR验证等待周期" "PR验证周期" "PR系统测试等待周期" "PR系统测试周期" \
    --category-column "分类1" \
    --direction "向低" \
    --indicator-type "周期类" \
    --html-template /path/to/template.html \
    --css-template /path/to/style.css \
    --js-template /path/to/script.js \
    --output-dir /workspace
```

**参数说明**：
- `--data-source-url`: 数据源URL（必填），格式如：`https://aip.zte.com.cn/middle/api/remote/call/{id}?projectGroup_id=14&beginTime=2026-01-01&endTime=2026-07-01`
- `--column-name-source`: 列名来源（必填），如："PR交付周期"、"MR交付周期"
- `--section-cycle-name`: 分段周期名称列表（必填），多个值用空格分隔，如："PR分析等待周期" "PR分析周期" "PR开发等待周期" ...
- `--category-column`: 分类列名（必填），如："分类1"
- `--direction`: 方向（必填），如："向低"
- `--indicator-type`: 指标类型（必填），如："周期类"
- `--html-template`: HTML模板文件路径（必填）
- `--css-template`: CSS模板文件路径（必填）
- `--js-template`: JavaScript模板文件路径（必填）
- `--output-dir`: 输出目录（可选，默认：/workspace）
  - **重要**：输出目录默认为`/workspace`，确保生成的报告文件保存在工作区根目录，而不是技能文件夹中
  - **自定义输出**：可以通过此参数指定任意输出目录，如`--output-dir /workspace/reports`或`--output-dir /workspace/imap-reports`
- `--output-html`: 输出HTML文件名（可选，默认：imap_report.html）
- `--output-css`: 输出CSS文件名（可选，默认：imap_style.css）
- `--output-js`: 输出JavaScript文件名（可选，默认：imap_script.js）

**⚠️ 重要说明**：
- 技能执行时，所有生成的报告文件（HTML、CSS、JS）都会保存到`/workspace`目录或用户指定的输出目录
- **不会**将生成的文件保存到技能文件夹（`/skills/imap-report-generator/`）中
- 模板文件（HTML/CSS/JS）应从`/references`目录或用户指定的路径读取
- 调用API时需要提供正确的参数，确保数据源URL有效且有访问权限

**功能特性**：
- **通过API接口获取IMAP端到端需求交付分析数据**
- 调用接口：`POST https://aip.zte.com.cn/imap/api/data_analysis/endtoend_dashboard_analyze`
- 自动从API返回数据中提取IMAP需求交付统计数据
- 动态识别活动周期类型（MR数据或PR数据）并按正确顺序呈现
- 自动提取报告标题（格式："XXXXX需求交付分析报告"）：
  * 规则1：优先提取"发布版本"后的内容（支持"发布版本XXX"、"发布版本：XXX"、"发布版本: XXX"等形式）
  * 规则2：如果没有"发布版本"字段，则提取"本次分析针对"后的项目组名称（支持"本次分析针对XXX"、"本次分析针对：XXX"等形式）
- 自动提取数据时间范围（如果存在）
- 生成包含5个TAB页的交互式HTML报告
- **数据清洗**：自动处理"nan"字符串和无效数据，将其转换为0，确保图表正常绘制
- **活动名称保留**：保留完整的"MR"或"PR"前缀，确保图表和表格中显示完整的活动名称（如"PR分析等待周期"）
- 自动修改HTML中的CSS/JS引用路径

## 注意事项

1. **三文件模板**：必须同时提供HTML、CSS、JS三个文件作为完整模板
2. **专用性**：本技能专门用于IMAP端到端需求交付分析报告，不用于其他报告
3. **模板兼容性**：确保HTML中正确引用了CSS和JS文件（相对路径）
4. **iCenter权限**：确保用户有iCenter页面的访问权限
5. **数据格式**：iCenter页面应包含IMAP需求交付的统计数据表格
6. **图表数据**：确保时间序列数据为13个月（或可适配）
7. **分享链接**：HTML文件必须有完整结构（包含</body></html>）才能生成分享链接
8. **文件命名**：输出文件使用有意义的名称（如imap_report.html）
9. **CSS/JS引用修改**：生成HTML报告时，必须将HTML模板中的CSS和JS文件引用（如`<link href="style.css">`和`<script src="script.js">`）修改为实际输出的文件名（如`imap_style.css`和`imap_script.js`），否则报告无法正常加载样式和交互功能
10. **报告标题提取**：报告标题格式为"XXX需求交付分析报告"，其中"XXX"从iCenter页面的"基本信息"字段提取。优先提取"发布版本"后的内容，如果没有则提取"本次分析针对"后的项目组名称。
11. **数据时间范围显示**：如果iCenter页面的"基本信息"字段包含"数据时间范围"，则在报告头部显示该信息，样式与"需求类型"元数据一致。
12. **活动周期类型动态识别**：根据iCenter数据中的实际周期类型确定是MR数据还是PR数据还是MR数据，并按正确的顺序呈现周期：
    - MR数据周期顺序：MR分析等待周期、MR分析周期、MR计划周期（如有）、MR开发等待周期、MR开发周期、MR验收等待周期、MR验收周期
    - PR数据周期顺序：PR分析等待周期、PR分析周期、PR开发等待周期、PR开发周期、PR验证等待周期、PR验证周期、PR系统测试等待周期、PR系统测试周期
13. **JavaScript图表数据更新**：生成报告时，必须修改JavaScript文件中的图表数据，将模板中的硬编码数据替换为从iCenter页面获取的实际数据。包括：
    - 活动周期对比图：替换activities数组和各需求类型的数据数组
    - 时间序列趋势图：替换drawTrendChart函数调用中的series数据对象
    - 图例HTML：替换为动态生成的需求类型图例
14. **动态需求类型处理**：需求类型和数量在每次报告生成时都可能不同。图表颜色使用预定义颜色数组循环分配（10种颜色：蓝、紫、绿、红、橙、青、粉、靛蓝、天蓝、黄绿），确保相同位置的需求类型获得相同颜色。支持任意数量的需求类型（超出10种时通过HSL动态生成颜色）。
15. **数据清洗**：脚本会自动处理iCenter数据中的"nan"字符串、空字符串和None值，将其转换为0.0，确保JavaScript图表能够正常绘制。这包括：
    - 周期数据中的"nan"值 → 转换为0.0
    - 趋势数据中的"nan"值 → 转换为0.0
    - 时间序列数据中的"nan"值 → 转换为0.0
16. **活动名称保留**：图表和表格中使用完整的活动周期名称（保留"MR"或"PR"前缀），如"PR分析等待周期"、"MR开发周期"等，确保用户能够清晰识别活动类型。
17. **分析结论提取**：活动均衡性分析TAB页的分析结论部分，必须从iCenter页面的"分析结论2-关键"字段提取实际结论，而不是使用模板中的硬编码结论。提取的结论格式如："MR分析等待周期：组件级/跨组件级-单板类(109.1天, 超出平均109.9%)； MR开发等待周期：组件级/跨组件级-产品功能类(63.2天, 超出平均102.4%)；..."
18. **分析结论分点展示**：活动均衡性分析的分析结论部分使用列表格式分点展示，将"； "替换为"</li><li>"，每个异常活动作为一个列表项，使结论更清晰易读。

## 最佳实践

### 模板设计
- HTML使用语义化标签和清晰的类名
- CSS使用变量定义配色方案
- JavaScript模块化组织图表绘制函数
- 支持响应式设计适配移动端

### 数据准备
- 确保iCenter页面包含完整的IMAP统计数据
- 数据字段与模板数据结构匹配
- 时间序列数据格式统一（YYYY-MM）
- 异常需求详单包含需求标识、标题、周期天数

### 报告优化
- TAB切换添加平滑过渡动画
- 图表添加鼠标悬停提示
- 表格添加行悬停高亮
- 打印优化样式（@media print）
