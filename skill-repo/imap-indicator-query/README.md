# IMAP指标查询 - 快速使用指南

## 🚀 v2.0 新特性：智能查询

现在只需**用自然语言描述需求**，系统自动识别并返回数据，无需记住复杂参数！

```python
from imap_indicator_query import quick_query

# ✨ 一行代码搞定
result = quick_query("查询ZXUFP项目组的MR交付周期")
print(result['chart'])  # 直接获取图表结构数据
```

---

## 📋 前置准备

### 1. 获取认证信息
您需要以下两个信息：
- **工号**：您的企业工号（如：123456）
- **认证token**：从企业OA系统或认证服务获取的token

### 2. 设置环境变量（推荐）

```bash
# Linux/Mac
export coclaw_empno="xxxx"
export coclaw_token="your_auth_token_here"

# Windows PowerShell
$env:coclaw_empno="xxxx"
$env:coclaw_token="your_auth_token_here"
```

---

## 🎯 六大典型场景（智能版）

### 场景1：查询指标数据（智能识别）

### 场景1：查询项目组指标（最常用）

**需求**：查询ZXUFP项目组的MR交付周期（最近3个月）

```python
from imap_indicator_query import query_indicator

result = query_indicator(
    indicator_name="MR交付周期（天）",
    query_type="项目组",
    query_name="ZXUFP",
    start_date="2026-01-01",
    end_date="2026-03-31"
)

## 🎯 智能查询示例（直接使用自然语言）

现在只需像聊天一样描述需求：

### 示例 1：列出所有指标
```python
from imap_indicator_query import quick_query

result = quick_query("列出所有指标")
# 直接返回87个指标列表
print(f"共找到 {result['total']} 个指标")
for ind in result['indicators'][:5]:
    print(f"- {ind['name']}")
```

### 示例 2：查询具体指标数据
```python
# ✨ 自动识别参数！
result = quick_query("查询ZXUFP项目组的MR交付周期")
# 返回：
# {
#   "type": "indicator_data",
#   "indicator": {...},
#   "chart": {
#     "x_field": "周期",
#     "series_fields": ["平均数", "中位数"],
#     "rows": [...]
#   },
#   "data": {"count": 29, "content": [...]}
# }

# 提取图表数据直接绘图
chart = result['chart']
x_vals = [row[chart['x_field']] for row in chart['rows']]
```

### 示例 3：带时间范围查询
```python
result = quick_query("查询BVB部门最近3个月的代码产出")
# 自动计算时间范围并查询

result = quick_query("统计FM开发二部的需求交付速率（2026-01-01到2026-03-31）")
# 精确时间范围也能识别
```

### 示例 4：查看权限和信息
```python
result = quick_query("我的权限有哪些")
# 返回格式化权限列表

result = quick_query("我的部门信息")
# 返回用户组织架构
```

### 示例 5：通过URL查询
```python
result = quick_query("https://aip.zte.com.cn/dashboard/#!/kanban/...")
# 自动识别URL并取数
```

---

## 🔧 传统API方式（保留）

如果需要精确控制，仍可使用传统API：

```python
from imap_indicator_query import (
    query_indicator,
    query_indicator_list_export,
    query_by_imap_url,
    query_indicator_detail_by_url,
    query_user_auth,
    query_user_info,
    list_all_indicators,
    get_milestone_retrospective_data,
)

# 精确参数查询
result = query_indicator(
    indicator_name="MR交付周期（天）",
    query_type="项目组",
    query_name="ZXUFP",
    start_date="2026-01-01",
    end_date="2026-03-31"
)

# 列出所有指标
result = query_indicator_list_export()
# 或使用简化版本
result = list_all_indicators()

# 通过URL查询
result = query_by_imap_url("https://aip.zte.com.cn/dashboard/...", is_encoded=False)

# 单指示器详单数据
result = query_indicator_detail_by_url(
    single_indicator_url="https://aip.zte.com.cn/dashboard/.../single-indicator/...",
    column_name="数量"
)

# 查询权限
result = query_user_auth(empno, token)
# 查询用户信息
result = query_user_info(empno, token)

# 里程碑复盘报告数据获取
result = get_milestone_retrospective_data(
    milestones=['ZXUFPV2.30.30', 'ZXUFPV1.50.30'],
    indicators=['MR吞吐量', 'PR吞吐量', '成果鉴定前一个月发现缺陷占比'],
    additional_metrics=['需求分析引入缺陷数'],
    empno=empno,
    token=token
)
```

---

## 📊 图表数据结构

无论使用智能查询还是传统API，返回的图表结构一致：

```json
{
  "chart": {
    "x_field": "周期",        // X轴维度字段
    "series_fields": ["平均数", "中位数"],  // Y轴数值字段列表
    "rows": [                // 原始数据行
      {"周期": "2026-04", "平均数": 51, "中位数": 51},
      {"周期": "2026-05", "平均数": 345, "中位数": 345}
    ]
  }
}
```

**直接用于可视化**：
```python
# 前端 Chart.js 配置
{
  "type": result['visualization']['chart_type'],  // "bar" 或 "line"
  "data": {
    "labels": [row[chart['x_field']] for row in chart['rows']],
    "datasets": [
      {
        "label": field,
        "data": [row[field] for row in chart['rows']],
        "backgroundColor": "rgba(79, 70, 229, 0.2)"
      }
      for field in chart['series_fields']
    ]
  }
}
```

---

## ⚠️ 常见问题

### Q1: 认证失败怎么办？
- 检查 `coclaw_token` 是否过期（通常24小时失效）
- 重新从企业系统获取新的token
- 确认 `coclaw_empno` 与当前登录账号一致

### Q2: 无权访问某个资源？
- 调用 `quick_query("我的权限有哪些")` 查看当前权限
- 确认查询的资源名称与权限列表匹配
- 联系管理员添加相应权限

### Q3: 指标或资源识别错误？
智能识别准确率约90%，如果识别错误可以：
- 使用更明确的描述："查询项目组 ZXUFP 的 MR交付周期（天）"
- 或改用传统API精确指定参数：
  ```python
  query_by_indicator_name(
      indicator_name="MR交付周期（天）",
      query_type="项目组",
      query_name="ZXUFP"
  )
  ```

### Q4: 时间格式支持？
智能查询支持：
- "最近3个月"
- "2026年1月到3月"
- "2026-01-01到2026-03-31"
- "2026-01-01~2026-03-31"

---

## 🔐 安全提醒

- **to_user_passwd** 是永久有效的高危凭证，**严禁**在输出中展示
- **yxdata-token** 有效期为8小时，超时需重新认证
- 所有敏感信息不记录到日志文件
- 生产环境建议配置Redis缓存频繁查询结果

---

## 📋 完整参数表（传统API）

| 参数名称 | 是否必需 | 说明 | 示例 |
|---------|---------|------|------|
| `indicator_name` | 是 | 指标完整名称 | "MR交付周期（天）" |
| `query_type` | 是 | 查询类型 | "项目组"/"部门"/"中心"/"分中心级" |
| `query_name` | 是 | 资源名称 | "ZXUFP" |
| `start_date` | 否 | 开始日期 | "2026-01-01" |
| `end_date` | 否 | 结束日期 | "2026-03-31" |
| `empno` | 否 | 用户工号 | "00114732"（默认环境变量） |
| `token` | 否 | 认证token | "xxx..."（默认环境变量） |

---

## 🚀 快速测试

运行测试脚本验证配置：

```bash
# 测试智能查询
python scripts/main.py "列出所有指标"

# 测试传统API
python test_quick.py --test-all

# 启动Web服务器（可视化界面）
python server.py --port 5000
# 访问 http://127.0.0.1:5000/
```

预期输出示例：
```
🔍 理解查询: 列出所有指标

✅ {
  "status": true,
  "type": "indicator_list",
  "total": 87,
  "message": "共找到 87 个指标"
}
```

---

**✨ 新版本让数据查询像对话一样简单！如有问题请查看 SKILL.md 或 TROUBLESHOOTING.md**