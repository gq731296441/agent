# CLI：子命令与参数

## 拼命令前

- 子命令全集与顺序：`python3 {baseDir}/scripts/main.py --help`
- 当前子命令的参数名、必填项、choices、默认值：`python3 {baseDir}/scripts/main.py <子命令> --help`
- Windows 解释器见 [runtime_env.md](runtime_env.md)

## 子命令 → 能力（速查）

| 类别 | 子命令 |
|------|--------|
| 搜索 / 查看 / 附件 / 标记 / 回复 / 转发 | `search-emails`, `view`, `download-attach`, `transfer-attach`, `mark-email`, `reply`, `reply-brief`, `forward`, `check-forward` |
| 总结 | `summary` |
| 草稿 / 撤回 | `draft-list`, `create-draft`, `save-draft`, `operate-draft`, `rollback-sent` |
| 立即发送 | `send` |
| 文件夹 | `folder-list`, `delete-mails`, `move-emails`, `recover-mails` |
| 收件人解析 | `search-users` |
| 时间 / 当前用户 | `get-time`, `my-info` |

意图路由、展示格式、出参字段：按 SKILL.md 打开的对应 reference；各文档「出参」小节。**改已读/未读/标星** → [mark.md](mark.md)（`mark-email`）；**仅筛未读/标星列表** → [search.md](search.md)（`search-emails --categories`）。

## `--to` / `--cc` / `--bcc`（发送/转发/部分回复）

`send`、`create-draft`、`save-draft`、`forward`；`reply` 在需要改收件人时可选。三参数均为**逗号分隔邮箱**；个人与群组邮箱可写在同一参数中（如 `--to` 中混排）。发送前会校验邮箱并解析收件人信息。约定见 [cli-to-cc-bcc.md](cli-to-cc-bcc.md)、[send.md](send.md)。

## `--is-inline`（是否把图片插入正文，供模型路由）

| 子命令 | CLI 是否含 `--is-inline` | 拼命令 |
|--------|--------------------------|--------|
| `send` | 是 | `attachments` 非空且需把图片插入正文时追加 `--is-inline "1"` |
| `create-draft` | 是 | 同上 |
| `save-draft` | 是 | **仅**未传 `--draft` 时可带；与 `--draft` 联用时**不得**使用非默认的 `--is-inline`，见 [draft_box.md](draft_box.md) 场景六 |

语义（仅图片）：[send.md](send.md) 参数表「图片插入正文」行；拼命令条件表：[send.md](send.md) 步骤 4。
