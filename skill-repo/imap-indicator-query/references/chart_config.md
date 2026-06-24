# 图表可视化配置指南

## 概述

本指南说明如何将IMAP查询结果转换为可视化图表。根据指示器配置的图表类型和数据特征，提供相应的可视化建议。

## 支持的图表类型

| 中文名称 | 英文标识 | 适用场景 | 数据特征 |
|---------|---------|---------|---------|
| 柱状图 | bar | 对比不同类别的数值 | 分类维度 + 1-3个数值指标 |
| 折线图 | line | 显示趋势变化 | 时间维度 + 1-3个数值指标 |
| 饼图 | pie | 显示占比关系 | 分类维度 + 单个数值指标 |
| 表格 | table | 精确数值展示 | 任意结构 |
| 散点图 | scatter | 关系分析 | 两个数值维度 |
| 面积图 | area | 累积趋势 | 时间维度 + 数值指标 |

## 数据解析规则

### 自动字段识别

系统会自动分析返回的content数组，识别：

1. **时间字段**：字段名为"周期"、"月份"、"年份"或包含日期格式（2026-01）
2. **数值字段**：整型或浮点型字段（平均数、中位数、MR实现周期等）
3. **分类字段**：字符串字段但非时间格式

示例解析：
```json
{
  "content": [
    {"周期": "2026-04", "平均数": 51, "中位数": 51},
    {"周期": "2026-05", "平均数": 345, "中位数": 345}
  ]
}

识别结果：
- 时间字段: ["周期"]
- 数值字段: ["平均数", "中位数"]
- 分类字段: []
```

---

## 图表配置映射

### 1. 柱状图 (bar)

**适用指示器**：
- MR交付周期（天）
- 需求交付速率
- 代码产出量

**配置示例**：
```json
{
  "chart_type": "bar",
  "title": "MR交付周期 - ZXUFP项目组",
  "x_axis": {
    "field": "周期",
    "type": "category"
  },
  "y_axes": [
    {
      "field": "平均数",
      "type": "value",
      "name": "平均交付周期（天）"
    },
    {
      "field": "中位数",
      "type": "value",
      "name": "中位数（天）"
    }
  ]
}
```

**渲染建议**：
- 使用分组柱状图（多个数值指标）
- X轴旋转45度避免重叠
- 添加数值标签

---

### 2. 折线图 (line)

**适用指示器**：
- 趋势类指标（需求交付趋势、缺陷趋势等）
- 累积值指标

**配置示例**：
```json
{
  "chart_type": "line",
  "title": "需求交付趋势 - FM开发二部",
  "x_axis": {
    "field": "周期",
    "type": "time"
  },
  "y_axes": [
    {
      "field": "完成需求数",
      "type": "value",
      "name": "需求数量",
      "styles": {
        "line": "solid",
        "smooth": true
      }
    },
    {
      "field": "延期需求数",
      "type": "value",
      "name": "延期数量",
      "styles": {
        "line": "dashed"
      }
    }
  ],
  "legend": {
    "position": "top"
  },
  "tooltip": {
    "shared": true
  }
}
```

**渲染建议**：
- 时间轴使用连续型
- 支持多条线对比
- 启用平滑曲线（smooth: true）

---

### 3. 饼图 (pie)

**适用指示器**：
- 占比类指标（缺陷分布、工时分布等）
- 分类统计

**配置示例**：
```json
{
  "chart_type": "pie",
  "title": "缺陷类型分布 - ZXUFP项目组",
  "categories": {
    "field": "缺陷类型"
  },
  "value": {
    "field": "缺陷数量"
  },
  "label": {
    "formatter": "{b}: {d}%"  // b:类别名, d:百分比
  },
  "legend": {
    "position": "right"
  }
}
```

**渲染建议**：
- 类别不超过10个，否则合并为"其他"
- 使用环形图（donut）增加可读性
- 显示百分比标签

---

### 4. 表格 (table)

**适用指示器**：
- 明细数据展示
- 需要精确数值的场景

**配置示例**：
```json
{
  "chart_type": "table",
  "title": "MR交付周期明细 - ZXUFP项目组",
  "columns": [
    {"field": "周期", "title": "月份", "width": 100},
    {"field": "MR实现周期", "title": "MR周期", "width": 80, "align": "right"},
    {"field": "平均数", "title": "平均", "width": 80, "align": "right"},
    {"field": "中位数", "title": "中位数", "width": 80, "align": "right"}
  ],
  "summary": {
    "show": true,
    "columns": ["MR实现周期", "平均数", "中位数"],
    "calculations": ["average", "max", "min"]
  }
}
```

**渲染建议**：
- 数值列右对齐
- 固定表头
- 支持排序
- 小计/总计行

---

### 5. 散点图 (scatter)

**适用指示器**：
- 相关性分析
- 两个数值变量的关系

**配置示例**：
```json
{
  "chart_type": "scatter",
  "title": "需求交付周期与复杂度关系",
  "x_axis": {
    "field": "需求复杂度",
    "name": "复杂度评分"
  },
  "y_axis": {
    "field": "交付周期",
    "name": "交付周期（天）"
  },
  "symbol": "circle",
  "symbol_size": 8,
  "tooltip": {
    "formatter": "需求ID: {b}<br/>复杂度: {x}<br/>周期: {y}"
  }
}
```

---

## 配置模板

### 模板1：多指标柱状图

适用于"MR交付周期"类指标，显示平均数和中位数：

```json
{
  "chart_type": "bar",
  "title": "{indicator_name} - {query_name}",
  "subtitle": "时间范围: {start_date} 至 {end_date}",
  "x_axis": {
    "field": "{x_axis_field}",
    "type": "category",
    "rotate": 45
  },
  "y_axes": [
    {
      "field": "{primary_field}",
      "name": "{y_axis_name}",
      "position": "left"
    },
    {
      "field": "{secondary_field}",
      "name": "{secondary_name}",
      "position": "right"
    }
  ],
  "series": [
    {
      "type": "bar",
      "data_index": 0,
      "color": "#5470C6"
    },
    {
      "type": "bar",
      "data_index": 1,
      "color": "#91CC75"
    }
  ],
  "legend": {
    "data": ["{primary_field}", "{secondary_field}"],
    "position": "top"
  },
  "grid": {
    "left": "3%",
    "right": "4%",
    "bottom": "15%",
    "containLabel": true
  }
}
```

### 模板2：时间趋势折线图

适用于需求交付趋势等：

```json
{
  "chart_type": "line",
  "title": "{indicator_name} - {query_name}",
  "x_axis": {
    "field": "周期",
    "type": "time"
  },
  "y_axes": [
    {
      "name": "数量",
      "type": "value"
    }
  ],
  "series": [
    {
      "type": "line",
      "data_index": 0,
      "name": "完成数",
      "smooth": true,
      "area_style": {}
    },
    {
      "type": "line",
      "data_index": 1,
      "name": "延期数",
      "smooth": true,
      "line_style": {
        "type": "dashed"
      }
    }
  ],
  "tooltip": {
    "trigger": "axis",
    "shared": true
  },
  "data_zoom": [
    {
      "type": "slider",
      "start": 0,
      "end": 100
    }
  ]
}
```

### 模板3：占比饼图

适用于分类统计：

```json
{
  "chart_type": "pie",
  "title": "{indicator_name}",
  "series": [
    {
      "type": "pie",
      "radius": ["40%", "70%"],
      "center": ["50%", "50%"],
      "data": [
        {"value": "{value1}", "name": "{category1}"},
        {"value": "{value2}", "name": "{category2}"}
      ],
      "label": {
        "formatter": "{b}: {d}%"
      }
    }
  ],
  "legend": {
    "orient": "vertical",
    "left": "left"
  }
}
```

---

## 颜色主题

### 默认配色方案

```json
{
  "color_palette": [
    "#5470C6",  // 蓝色
    "#91CC75",  // 绿色
    "#FAC858",  // 黄色
    "#EE6666",  // 红色
    "#73C0DE",  // 浅蓝
    "#3BA272",  // 深绿
    "#FC8452",  // 橙色
    "#9A60B4",  // 紫色
    "#EA7CCC"   // 粉色
  ]
}
```

### 语义化颜色

| 指标类型 | 主色 | 说明 |
|---------|------|------|
| 正常/平均 | #5470C6 | 蓝色 |
| 延期/异常 | #EE6666 | 红色 |
| 目标值 | #91CC75 | 绿色 |
| 对比值 | #FAC858 | 黄色 |

---

## 工具函数

### Python实现示例

```python
def generate_chart_config(indicator: dict, data: dict, query_info: dict) -> dict:
    """
    生成图表配置
    
    Args:
        indicator: 指示器信息
        data: 查询结果数据
        query_info: 查询条件信息
        
    Returns:
        图表配置字典
    """
    chart_type = map_chart_type(indicator.get('图表类型', '表格'))
    
    config = {
        "title": f"{indicator.get('指示器名称')} - {query_info['name']}",
        "chart_type": chart_type
    }
    
    if chart_type == 'bar':
        config.update(_build_bar_config(indicator, data))
    elif chart_type == 'line':
        config.update(_build_line_config(indicator, data))
    elif chart_type == 'table':
        config.update(_build_table_config(indicator, data))
    
    return config


def _build_bar_config(indicator: dict, data: dict) -> dict:
    """构建柱状图配置"""
    x_field = indicator.get('图表x轴', '周期')
    y_fields = [f.strip() for f in indicator.get('图表y轴', '').split('，')]
    
    return {
        "x_axis": {"field": x_field, "type": "category"},
        "y_axes": [
            {"field": field, "name": field, "position": "left"}
            for field in y_fields
        ],
        "series": [
            {"type": "bar", "data_index": i, "name": field}
            for i, field in enumerate(y_fields)
        ]
    }
```

---

## 前端集成示例

### Vue + ECharts

```vue
<template>
  <div class="chart-container">
    <div ref="chartRef" style="width: 100%; height: 400px;"></div>
  </div>
</template>

<script>
import * as echarts from 'echarts'

export default {
  props: {
    config: Object
  },
  data() {
    return {
      chart: null
    }
  },
  mounted() {
    this.initChart()
  },
  watch: {
    config: {
      handler() {
        this.renderChart()
      },
      deep: true
    }
  },
  methods: {
    initChart() {
      this.chart = echarts.init(this.$refs.chartRef)
    },
    renderChart() {
      if (!this.chart || !this.config) return
      
      const option = this.buildOption(this.config)
      this.chart.setOption(option, true)
    },
    buildOption(config) {
      const option = {
        title: {
          text: config.title,
          subtext: config.subtitle
        },
        tooltip: config.tooltip || {},
        legend: config.legend || {},
        grid: config.grid || {},
        color: config.color_palette || defaultColors,
        xAxis: {
          type: config.x_axis?.type || 'category',
          data: config.x_axis?.data || [],
          name: config.x_axis?.name
        },
        yAxis: config.y_axes?.[0] ? {
          type: config.y_axes[0].type || 'value',
          name: config.y_axes[0].name
        } : {},
        series: config.series || []
      }
      
      return option
    }
  }
}
</script>
```

---

## 最佳实践

1. **响应式设计**：图表容器应支持自适应布局
2. **加载状态**：数据加载时显示loading动画
3. **错误处理**：数据异常时显示友好提示
4. **数据导出**：提供导出为Excel/CSV功能
5. **图表切换**：同一数据支持多种图表类型展示
6. **时间范围选择**：动态更新图表数据
7. **权限提示**：无权限时说明原因并提示

---

## 参考示例

完整示例见 `examples/` 目录：
- `example_mr_delivery.json` - MR交付周期柱状图
- `example_trend_line.json` - 需求交付趋势折线图
- `example_defect_pie.json` - 缺陷分布饼图
- `example_detail_table.json` - 明细数据表格