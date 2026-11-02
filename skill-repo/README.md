# Skill 仓库（平台只读源）

本目录为 **Skill 市场** 的文件真相源。用户订阅时，Agent 将目录内容 **拷贝** 到 `{WORKSPACE_ROOT}/{user_id}/skills/{skill_id}/`，运行时不会直接读取本目录。

## 目录结构

```
skill-repo/
└── {skill_id}/
    ├── SKILL.md      # 必需
    └── ...           # 可选脚本、模板、数据文件
```

- **skill_id** = 子目录名（小写、下划线，如 `code_review`）
- 禁止 `..` 或路径分隔符

## SKILL.md frontmatter

```yaml
---
name: 显示名称
description: 一句话描述
version: 1.0.0
tags: [research, 文档]
author: platform-team
triggers: ["关键词1", "keyword2"]
always_on: false
---
```

更新 skill 时请 **递增 version**，部署后执行 sync，用户在市场页点击「更新」即可覆盖本地安装。

## 同步到市场索引

```bash
# 启动时自动 sync（SKILL_CATALOG_AUTO_SYNC=true）
python run_main.py

# 或手动
curl -X POST http://localhost:8002/internal/skills/sync \
  -H "X-Internal-Token: $SKILL_INTERNAL_TOKEN"
```

## 部署

- Docker/K8s：将 `skill-repo/` **COPY 进镜像** 或只读挂载，设置 `SKILL_REPO_ROOT=/app/skill-repo`
- 勿与用户 NFS 工作区混放

## 未来独立仓库

可将本目录迁为独立 Git 仓库，构建时 clone/submodule 到 `./skill-repo`，API 与用户路径不变。
