# CLI：`--to` / `--cc` / `--bcc`

本文档为发信相关子命令收件人参数的**单一说明**；实现见 `main.py`、[`util.split_email_cli_arg`](../scripts/util.py)、[`zmail_send_draft_base`](../scripts/zmail_send_draft_base.py)。

## 行为

- 使用三个独立参数 **`--to`**、**`--cc`**、**`--bcc`**，值为**逗号分隔的邮箱**；未传或空字符串表示该维度无收件人。
- **群组邮箱与个人邮箱合并**：可写在同一参数中（例如 `--to "user@zte.com.cn,group@zte.com.cn"`）。
- 每个邮箱在发送前会校验并解析收件人信息，失败则整单报错。
- 根据返回结果自动区分个人 / 群组并拼 MT（见 `_mt_item_from_get_user`）。

## 子命令

| 子命令 | `--to` / `--cc` / `--bcc` |
|--------|---------------------------|
| `send` | 至少一项非空 |
| `create-draft` | 至少一项非空 |
| `save-draft` | 均可空（无收件人草稿） |
| `forward` | 至少一项非空 |
| `reply` | 均可空（空则仅回复原发件人） |

## 兼容性

已移除 `--resolved-recipients` JSON；旧调用需改为三参数。
