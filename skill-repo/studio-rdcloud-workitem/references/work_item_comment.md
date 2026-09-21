# 工作项评论 · 脚本入参说明


> **工具约束（必遵）**：须通过本技能已有 `scripts/` 脚本执行研发云工作项能力；**禁止**使用 write、edit、web-search、web_fetch 等处理本业务或替代脚本；**禁止**新建 Python 脚本。

本场景对应 **研发云 / RDC 工作项评论**：查询某工作项下的全部评论（树形，含回复），或添加顶级评论 / 回复某条评论。评论正文接口侧支持 HTML。

| 能力 | 脚本 |
|------|------|
| 查询评论 | `scripts/atomic/query_comment.py` |
| 添加 / 回复评论 | `scripts/atomic/add_comment.py` |

## 触发语义（意图识别）

- **查询**：查询评论、查看评论、列出评论、评论列表、某工作项有哪些评论等。
- **添加**：添加评论、发表评论、给某单号写评论、回复某条评论。回复时**推荐用序号**（如「回复第 2 条」「回复 1.1」），无需复制评论 ID。

## 命令行调用（必遵）

调用时须传 **`--user-input`**，值为用户完整原始话，与其它原子脚本一致。

认证：`coclaw_empno` / `coclaw_token` 或 `RDCLOUD_EMP_NO` / `RDCLOUD_AUTH_TOKEN`；租户等见 [SKILL.md 配置与认证](../SKILL.md)。

### 查询评论

```bash
python scripts/atomic/query_comment.py --work-item-id PLM-4346370 \
  --user-input "查看 PLM-4346370 的评论"
```

| 参数 | 必填 | 说明 |
|------|------|------|
| **--work-item-id** / **--business-id** | 是 | 工作项标识，如 PLM-4346370 |
| **--business-type** | 否 | 默认 `wic` |
| **--page** | 否 | 页码，默认 1 |
| **--size** | 否 | 每页条数，默认 1000 |
| **--sort** | 否 | `asc` / `desc`，默认 asc |
| **--raw-json** | 否 | 指定则输出 JSON；否则遵循 `assets/config.json` 的 `format.as_markdown` |
| **--user-input** | 是 | 用户完整原始输入 |

### 添加评论

**顶级评论**（不传 `--parent-id`）：

```bash
python scripts/atomic/add_comment.py --work-item-id PLM-3875805 \
  --comment-detail "<p>评论内容</p>" \
  --title "可选标题" \
  --user-input "给 PLM-3875805 加一条评论"
```

**回复评论**（推荐：`--reply-to` 序号）：

```bash
python scripts/atomic/add_comment.py --work-item-id PLM-3875805 \
  --comment-detail "<p>回复内容</p>" \
  --reply-to 2 \
  --user-input "回复第 2 条"
```

**回复评论**（传父评论 `id`）：

```bash
python scripts/atomic/add_comment.py --work-item-id PLM-3875805 \
  --comment-detail "<p>回复内容</p>" \
  --parent-id 87e8e2b151564a9aa0894ce95e37e714 \
  --user-input "回复该评论"
```

| 参数 | 必填 | 说明 |
|------|------|------|
| **--work-item-id** / **--business-id** | 是 | 工作项标识 |
| **--comment-detail** | 是 | 评论 HTML 正文（Shell 中注意引号转义） |
| **--title** | 否 | 标题，最长约 100（仅顶级等场景可用） |
| **--reply-to** | 否 | 按序号指定要回复的评论（如 1、2、1.1），与查询结果中的 [1]、[2]、[1.1] 对应；与 --parent-id 二选一 |
| **--parent-id** | 否 | 父评论 id；与 --reply-to 二选一 |
| **--email-disabled** | 否 | 加上则不发送评论相关邮件 |
| **--raw-json** | 否 | 指定则输出 JSON |
| **--user-input** | 是 | 用户完整原始输入 |

## 接口与响应要点

- 评论 API URL 与基址统一在 `scripts/common/rdcloud_api.py`：`URL_WORK_ITEM_COMMENTS`（基于 `BASE_RDC_SERVER`），路径为 `/comments/api/{businessType}/{businessId}`；查询为 GET，添加为 POST。
- 成功时业务码多为 `code.code == "0000"`。
- 查询结果中评论为树形：`children` 为子评论；`level` 1 为顶级，2 为回复。每条评论前带序号 `[1]`、`[2]`、`[1.1]` 等，回复时使用 `--reply-to` 指定序号，无需复制评论 ID。

## 结果展示

- 默认 Markdown 输出时，将完整脚本输出展示给用户；不要随意删改树形结构含义。
- 若用户只要 JSON 调试，使用 `--raw-json`。

## 错误与缺失信息

- 未提供认证 → 脚本报错退出。
- 未提供工作项标识或 `--comment-detail`（添加）→ 参数错误。
- 回复时未提供 `--parent-id` 且未提供 `--reply-to` → 须提示用户使用「回复第 N 条」或在查询结果中确认序号后传 `--reply-to N`。

## 迁移说明

原独立技能 **rdc-comment**（目录 `44bc57c2-1201-4de2-8626-b7cae7f0a9b0`）已并入本技能；请一律使用上表脚本路径，勿再引用旧 `rdc-comment/scripts/*.py`。
