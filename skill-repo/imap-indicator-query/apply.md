# 一、技能说明

`imap-indicator-query` 是一个用于查询 iMAP（AIP）研发效能指标的技能，支持自然语言与参数化两种方式调用，覆盖以下核心能力：

- 完整认证链路：使用 `coclaw_empno` 和 `coclaw_token` 获取 `to_user`，再登录获取 `yxdata-token`
- 指标列表查询（接口1）：获取系统中可用指标及其元信息（图表类型、x/y轴、数据接口地址等）
- 用户权限查询（接口2）：查询用户在项目组/部门/中心等维度的访问权限
- 用户信息查询（接口3）：查询用户组织信息（部门、项目组、分中心等）
- 名称转ID（接口4）：将“项目组/部门/中心名称”转换为接口取数参数（`param=id`）
- 指标详单查询（接口5）：按“指标 + 组织维度 + 时间范围”获取具体数据并生成图表结构
- URL直连查询（接口6）：通过 iMAP 看板 URL 直接取数（接口6不要求 `yxdata-token`）
- 结果结构化输出：统一返回 `status/type/data/chart/visualization`，便于前端直接渲染图表


# 二、使用前期准备工作

## 运行环境

- Python：需要 Python 3.9+
- 依赖包：安装 `requests`

## 安装依赖

```bash
pip install requests
```

或在技能目录执行：

```bash
pip install -r requirements.txt
```

## 必要环境变量

在运行前配置：

- `coclaw_empno`：用户工号
- `coclaw_token`：用户认证 token

示例（PowerShell）：

```powershell
$env:coclaw_empno="xxxx"
$env:coclaw_token="xxxx"
```

## 连通性要求

- 需能访问企业内网地址：`https://aip.zte.com.cn`

## 安全注意事项

- `to_user_passwd` 为高敏感凭证，不可输出
- `yxdata-token` 有时效性，不应写入日志明文
