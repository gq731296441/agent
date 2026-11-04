# s3canner（技能安全扫描器）

技能上传 / 安装流程调用的固定扫描组件，**与 `skill-repo/` 无关**，不通过 Skill 触发。

## 目录内容

| 文件 | 说明 |
|---|---|
| `s3canner` | Linux x86_64 可执行文件 |
| `s3canner.exe` | Windows 可执行文件 |
| `s3canner.zip` | 原始分发包（内含上面两个文件，路径为 `s3canner/script/`） |

二进制体积较大，默认不入库（见根目录 `.gitignore`）。

## 部署

```bash
# zip 内结构为 s3canner/script/s3canner(.exe)，-j 平铺到当前目录
unzip -j -o s3canner.zip 's3canner/script/*' -d vendor/s3canner
chmod +x vendor/s3canner/s3canner
```

## 定位顺序

`deep_agent_server/skills/marketplace/scan/adapter.py` 的 `_s3canner_bin()`：

1. 配置 `SKILL_S3CANNER_BIN`（绝对路径，优先）
2. `vendor/s3canner/s3canner`（Windows 为 `s3canner.exe`）
3. `PATH` 中的 `s3canner`

## 相关配置

| 配置项 | 默认 | 说明 |
|---|---|---|
| `SKILL_SCAN_TIMEOUT_SECONDS` | 120 | 单次扫描超时（秒） |
| `SKILL_SCAN_BLOCK_MIN_SEVERITY` | high | 达到该级别及以上判定 BLOCKED |
| `SKILL_S3CANNER_EXCLUDE` | SPEC-006 | 逗号分隔的排除规则 |
| `SKILL_S3CANNER_RULES` | 空 | 完整规则 YAML；空则从二进制 `--generate-rules` 缓存 |
| `SKILL_S3CANNER_RULES_OVERLAY` | `scan/s3can_rules_overlay.yaml` | 叠加规则 |
| `SKILL_SCAN_FIXTURE_PASS` | false | 仅 dev/test 的无扫描器兜底，生产禁止开启 |

## 故障现象

二进制缺失时，适配器返回 `ScanOutcome(unavailable=True, failure_reason="SCAN_UNAVAILABLE")`，
前端上传页显示：**「扫描服务暂不可用，请稍后重试。」**

排查：

```bash
vendor/s3canner/s3canner --version   # 期望输出 s3can x.y.z
ls -l vendor/s3canner/s3canner       # 需有可执行权限
```

规则缓存目录：`{WORKSPACE_ROOT}/.s3can-cache/s3can-rules.builtin.<mtime>.yaml`。
