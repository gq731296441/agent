---
name: studio-space-page-edit
description: >-
  iCenter 页面正文编辑子技能：整页 Markdown/HTML 回写（MCP 同款 updateContent）、按章节标题更新章节（updateChapter）、
  按 blockId 与行列索引更新表格单元格（block 查询 + block 回写）。依赖 `scripts/studio_space_page_edit.py`；
  认证使用环境变量 coclaw_empno、coclaw_token；页面链接优先解析 spaceId/pageId。
version: 1.0.1
---
# 页面编辑子技能（page-edit）

## 1. 适用范围

- **整页更新**：用 Markdown 或 HTML 替换页面正文（默认 `--mode page`）
- **章节更新**：按章节标题更新该章节 HTML 内容（`--mode chapter`）
- **表格单元格**：在已知表格所在 `blockId` 的前提下，按行列更新单元格（`--mode table-cell`）

与「仅改标题、不动正文」区分：改标题请走 `scripts/rewrite_page.py`（见 `../page-ops/SKILL.md`）。

### 1.1 调用禁令（与总入口 `SKILL.md` 同步）

- **禁止**在调用本子技能所列脚本（含下文示例命令）时，自行在 shell 命令中追加 `2>&1`、`&>`、`> ... 2>&1` 等会把 **stderr 合并进 stdout**（或与 stdout 写入同一流）的重定向。约定：**业务结果走 stdout**（如 `--json-output`）、**日志与提示走 stderr**；合并后会破坏 JSON/结构化解析与排障。若需保存输出或日志，应使用脚本自身提供的参数或约定方式，而不是改 shell 重定向。

## 2. 脚本与依赖

- **脚本**：`scripts/studio_space_page_edit.py`（在技能根目录 `studio-space-manager/` 下执行，路径相对 `scripts/`）
- **认证**：`coclaw_empno`、`coclaw_token`
- **整页/章节**：内容可为 `--content` 或 `--content-file`；Markdown 会经 `markdown2html` 转 HTML 后回写
- **表格单元格**：需用户或上游步骤提供 **`blockId`**（本脚本不内置 block 树枚举）；`row-index`/`col-index` 均为 **0 起算**

## 3. 常用触发词

- 整页：编辑页面、更新正文、回写页面、替换页面内容、把 md 写回空间
- 章节：更新章节、改某一节、章节「xxx」改成
- 表格：改表格第几行第几列、更新单元格、表格里某个格子（需 blockId）

## 4. 意图 -> 命令决策表

| 用户意图 | 必要参数 | 可选参数 | 命令要点 |
|---------|----------|----------|----------|
| 整页回写 | `url` 或 `spaceId+pageId` + 正文 | `--title`、`--content-html`、`--json-output` | `--mode page`（默认） |
| 本地 md 回写 | `content-file`（首行页面 URL） | `--title`、`--json-output` | 可省略 `--page-url`，从文件首行解析 |
| 更新章节 | `chapter-name` + 新内容 + 定位 | `--position`（同名章节，默认 1）、`--content-html` | `--mode chapter` |
| 更新表格单元格 | `block-id` + `row-index` + `col-index` + `cell-value` + 定位 | `--cell-value-html`、`--json-output` | `--mode table-cell` |

**定位**：`--page-url` 与 `--space-id`+`--page-id` 二选一（与 `rewrite_page.py` 等脚本一致）。

## 5. 参数提取规则

- **URL 优先**：有 `i.zte.com.cn` 页面链接时优先 `--page-url`
- **从本地 md 推断页面**：`space_page_tomd.py` 导出的文件首行为页面完整链接时，仅用 `--content-file` 即可
- **章节名**：`--chapter-name` 为**纯文本章节标题**（不含 Markdown 标记）；正文仍用 Markdown/HTML 传入 `--content` 或文件
- **表格**：`row-index`/`col-index` 为 0 基；多表格时以 block 内**第一个** `<table>` 为准，复杂版式需人工确认 block

## 6. 澄清问题模板（缺参时）

- 整页/章节但无正文：「请提供新正文（或本地 md 文件路径）。」
- 章节但无章节名：「要更新的是哪一节？请给出章节标题原文。」
- 表格但无 blockId：「需要表格所在内容块的 blockId；是否先用其它工具/接口列出页面 block 后再更新？」
- 无定位信息：「请提供页面 URL，或 spaceId + pageId。」

## 7. 命令模板
你们自己
以下均在技能根目录执行：`cd <studio-space-manager根目录>`。

```bash
# 整页：从本地 md（首行页面 URL）回写
python3 scripts/studio_space_page_edit.py --content-file ./page.md

# 整页：直接传 Markdown
python3 scripts/studio_space_page_edit.py \
  --page-url "https://i.zte.com.cn/#/shared/<spaceId>/wiki/page/<pageId>/view" \
  --content "# 标题\n正文"

# 整页：HTML 直传
python3 scripts/studio_space_page_edit.py \
  --page-url "https://i.zte.com.cn/#/shared/<spaceId>/wiki/page/<pageId>/view" \
  --content "<p>HTML</p>" \
  --content-html

# 章节：按章节标题更新（position 处理同名章节，默认 1）
python3 scripts/studio_space_page_edit.py \
  --page-url "https://i.zte.com.cn/#/shared/<spaceId>/wiki/page/<pageId>/view" \
  --mode chapter \
  --chapter-name "1. 背景" \
  --position 1 \
  --content "## 1. 背景\n更新后的内容"

# 表格单元格：需 blockId；行列从 0 开始
python3 scripts/studio_space_page_edit.py \
  --page-url "https://i.zte.com.cn/#/shared/<spaceId>/wiki/page/<pageId>/view" \
  --mode table-cell \
  --block-id "<blockId>" \
  --row-index 1 \
  --col-index 2 \
  --cell-value "新值"

# 表格单元格：单元格内容为 HTML
python3 scripts/studio_space_page_edit.py \
  --page-url "https://i.zte.com.cn/#/shared/<spaceId>/wiki/page/<pageId>/view" \
  --mode table-cell \
  --block-id "<blockId>" \
  --row-index 0 \
  --col-index 0 \
  --cell-value "<b>高亮</b>" \
  --cell-value-html

# 机器可读 JSON
python3 scripts/studio_space_page_edit.py --page-url "<url>" --content "x" --json-output
```

## 8. 约束与说明

- **i.zte.com.cn**：须走本仓库脚本，禁止对页面使用 web 抓取类工具（与总技能一致）。
- **与改标题区分**：只改标题用 `rewrite_page.py`；改正文/章节/表格用本脚本。
- **与新建区分**：新建页面用 `studio_space_page_save.py` 或模板流 `space_template_page.py`（见 `page-content` 子技能）。
- **运行目录与 UTF-8**：见总技能第 9 节「运行环境与字符集」。

## 9. 与 page-ops 的关系

本技能与 **`page-ops`** 同级，路由上常与 `page-ops`（复制、仅改标题、子页面、附件等）组合使用；其它页面操作总表见 **`../page-ops/SKILL.md`**。
