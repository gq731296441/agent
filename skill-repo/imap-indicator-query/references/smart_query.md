# 智能查询（v2.0+）

从 v2.0 起支持**自然语言查询**：描述需求即可，由编排器识别意图并调用接口。

## 调用方式

### Python

```python
from imap_indicator_query import quick_query

result = quick_query("查询ZXUFP项目组的MR交付周期")
result = quick_query("列出所有指标")
result = quick_query("查看我的权限")
```

### 命令行

```bash
python scripts/main.py "查询ZXUFP项目组的MR交付周期"
python scripts/main.py "列出所有指标"
python scripts/main.py "查看我的权限"
```

### HTTP（若已部署本地服务）

```bash
curl -X POST http://127.0.0.1:5000/api/imap/smart-query \
  -H "Content-Type: application/json" \
  -d '{"query": "查询ZXUFP项目组的MR交付周期"}'
```

## 意图类型

| 意图 | 示例 | 行为 |
|------|------|------|
| `list_indicators` | 列出所有指标、有哪些KPI | 拉取指示器列表 |
| `query_indicator` | 查 ZXUFP 的 MR 交付周期 | 指标数据查询 |
| `query_auth` | 我的权限、能访问的项目组 | 用户权限 |
| `query_user_info` | 我的部门、所属项目组 | 用户信息 |
| `query_by_url` | 粘贴完整 IMAP URL | URL 取数（免鉴权接口） |

## 自然语言语法要点

**指标查询**（常用）：`查询[资源][类型]的[指标名][时间范围]`

- 资源：项目组名、部门名、中心/分中心等。
- 时间：最近 N 个月、`YYYY-MM` 区间、`YYYY-MM-DD` 区间。

**列出指标**：列出 / 查看 / 获取 + 指标或 KPI。

**权限**：我的权限、查看权限、能访问的项目组。

**用户信息**：我的部门、我的项目组、分中心。

**URL**：完整 dashboard 链接或「通过这个链接取数」。

## 统一返回元数据

成功时通常包含 `_meta`（如 `intent`、`query`、`timestamp`、`processor`），以及按类型变化的 `data`、`indicator`、`chart` 等字段。详见 `response_examples.md`。

## 高级：精确参数

自然语言不准时，可使用 `query_by_indicator_name` 等函数显式传入 `indicator_name`、`query_type`、`query_name`、日期与 `empno`/`token`。可先 `process_query("我的权限有哪些")` 再查指标。

## 调试意图

```python
import logging
logging.basicConfig(level=logging.DEBUG)
result = quick_query("查询ZXUFP的MR交付周期")
print(result.get("_meta", {}).get("intent"))
```

## Web 界面

支持左侧表单精确查询、智能输入框自然语言、以及「列出指标」「查看权限」等快捷操作。

## 注意事项

1. 仍需 `coclaw_empno` / `coclaw_token`（URL 直连场景除外）。
2. 需能访问 `aip.zte.com.cn` 内网。
3. 复杂或歧义查询建议用手动参数模式。
4. 名称识别错误时响应中可能含建议值。

## 新旧模式对比

| 特性 | v1.x | v2.0+ |
|------|------|--------|
| 调用 | 脚本 + 参数 | `quick_query` + 自然语言 |
| 参数 | 全手动 | 自动抽取为主 |
| 适用 | 批量自动化 | 交互与临时分析 |
