# s3canner 安全扫描报告

**扫描时间**: 2026-04-29 17:32:05 | **耗时**: 1884 ms | **结果**: 未通过 | **s3can 版本**: 1.1.1

全部 | Critical: 0 | High: 1 | Medium: 0 | Low: 0 | Info: 0

## 规则检查结果

| 规则ID | 规则名称 | 规则描述 | 检查结果 |
| --- | --- | --- | --- |
| INST-001 | 提示词注入 | 存在提示词注入风险 | 通过 |
| INST-002 | 权限提升 | 包含提升权限的指令 | 通过 |
| INST-004 | 外网访问 | 从外网下载或上传（非内网） | 通过 |
| INST-005 | 隐藏/零宽字符 | 存在隐藏或零宽 Unicode 字符 | 通过 |
| META-001 | 描述过于宽泛 | 描述包含过于宽泛的表述 | 通过 |
| META-002 | 描述权限提升 | 描述包含权限提升类关键词 | 通过 |
| PATH-001 | 路径穿越 | 在脚本或 MD 代码块中检测到路径穿越模式（不扫描 MD 正文） | 通过 |
| PATH-002 | 隐藏目录 | skill 中存在以 . 开头的隐藏目录 | 通过 |
| SCRIPT-001 | Python 危险代码执行 | 危险的动态执行或反序列化：eval/exec/compile/pickle/marshal/yaml.load/__import__ | 通过 |
| SCRIPT-002 | Python 危险 Shell/命令执行 | 危险的 Shell/命令执行：os.system、os.popen、os.spawn、subprocess(shell=True) | 通过 |
| SCRIPT-006 | 硬编码凭证 | 可能存在硬编码的 API key、token、password、secret、private_key | 通过 |
| SENS-001 | 硬编码凭证 | 可能存在硬编码的密码、API key、token 或密钥 | 通过 |
| SENS-002 | 硬编码用户ID | 可能存在硬编码的用户/工号 | 通过 |
| SENS-003 | 外网下载或上传 | 从外网下载或上传（非内网） | 通过 |
| SENS-004 | HTTP Authorization 头凭证泄露 | HTTP 请求头 Authorization 中含硬编码的 Bearer Token 或 Basic 凭证 | 通过 |
| SENS-005 | 禁止访问的内网 URL 或受限端口 | 包含禁止访问的内网域名或端口 | 通过 |
| SENS-006 | 挖矿检测 | 存在矿池连接、矿机软件或挖矿算法特征 | 通过 |
| SENS-007 | 私钥或证书明文 | 文件中存在 PEM 格式私钥或证书块 | 通过 |
| SENS-008 | 硬编码 JWT | 存在明文 JWT（eyJ 开头的 base64 串） | 通过 |
| SENS-009 | 连接串内嵌密码 | 数据库或消息队列连接串中包含明文密码 | 通过 |
| SENS-010 | URL 携带敏感凭证 | HTTP 请求头或 URL 中包含账号密码、Token 或 API Key 等敏感凭证 | 通过 |
| SENS-011 | 账号:长 Token 组合 | 账号标识（如工号）后直接跟随长串 Token/口令，例如 06680523:AKC... | 通过 |
| SENS-012 | 高熵长串疑似凭证 | 出现高熵随机长串，可能为泄露的 token/secret 等敏感信息 | 通过 |
| SPEC-001 | SKILL.md 缺失 | SKILL.md 未找到 | 通过 |
| SPEC-002 | 读取失败 | 无法读取 SKILL.md | 通过 |
| SPEC-003 | SKILL.md 存在 BOM | SKILL.md 文件开头存在 UTF-8 BOM，可能导致解析或展示异常 | 通过 |
| SPEC-004 | 缺少 YAML 前置 | 缺少 YAML 前置元数据 | 通过 |
| SPEC-005 | 缺少 name 字段 | 缺少必填 name 字段 | 通过 |
| SPEC-007 | 缺少 version 字段 | 缺少version或为空 | 通过 |
| SPEC-008 | name与父文件夹名称不匹配 | name必须与父文件夹名称一致 | 未通过 |
| SPEC-009 | name 格式不符 | name 格式不符合规范 | 通过 |
| SPEC-010 | SKILL.md 过长 | SKILL.md 行数过多（agentskills.io 建议 500 行内） | 通过 |
| SPEC-014 | YAML frontmatter 语法错误 | YAML frontmatter 必须为合法YAML | 通过 |

## 问题详情

| 规则名称 | 等级 | 问题描述 | 文件 | 行号 | 片段 | 修复建议 | LLM |
| --- | --- | --- | --- | --- | --- | --- | --- |
| name与父文件夹名称不匹配 | high | name必须与父文件夹名称一致 | pdm-backfill2excel-skill/SKILL.md |  |  | Change name to '' or rename directory | — |
