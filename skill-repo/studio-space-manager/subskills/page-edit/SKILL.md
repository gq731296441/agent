---
name: studio-space-page-edit
description: >-
  iCenter 页面正文编辑子技能：整页 Markdown/HTML 回写（MCP 同款 updateContent）、按章节标题更新章节（updateChapter）、
  按 blockId 与行列索引更新表格单元格（block 查询 + block 回写）。**统一编辑入口**：`safe_page_edit.py --action edit`（Markdown自动转HTML）/**专业编辑脚本**：`studio_space_page_edit.py`（支持章节/表格单元格精细控制）；
  认证使用环境变量 coclaw_empno、coclaw_token；页面链接优先解析 spaceId/pageId。
version: 2.0.0
---
# 页面编辑子技能（page-edit）

## 1. 适用范围

### 1.1 统一编辑入口（推荐）

- **脚本**：`safe_page_edit.py --action edit`（v3.0+）
- **功能**：用户主动提供Markdown/HTML内容时，自动检测内容类型并转换为HTML后回写
- **适用场景**：
  - 直接提供Markdown文本：`--content "# 标题\n正文"`
  - 从本地Markdown文件读取：`--md-file ./page.md`（自动转HTML）
  - 从本地HTML文件读取：`--html-file ./page.html`（直传模式）
- **智能特性**：
  - 自动检测内容类型（基于特征模式匹配）
  - PlantUML保护机制（确保图表格式100%保真）
  - 详细的日志记录（INFO/DEBUG级别）
  - 完善的错误处理和降级策略

### 1.2 专业编辑脚本（精细控制）

- **整页更新**：用 Markdown 或 HTML 替换页面正文（默认 `--mode page`）
- **章节更新**：按章节标题更新该章节 HTML 内容（`--mode chapter`）
- **表格单元格**：在已知表格所在 `blockId` 的前提下，按行列更新单元格（`--mode table-cell`）

**选择指南**：
- 用户主动提供内容 -> 优先使用 `safe_page_edit.py --action edit`
- 需要章节/表格单元格等精细控制 -> 使用 `studio_space_page_edit.py`

与「仅改标题、不动正文」区分：改标题请走 `scripts/rewrite_page.py`（见 `../page-ops/SKILL.md`）。

### 1.1 调用禁令（与总入口 `SKILL.md` 同步）

- **禁止**在调用本子技能所列脚本（含下文示例命令）时，自行在 shell 命令中追加 `2>&1`、`&>`、`> ... 2>&1` 等会把 **stderr 合并进 stdout**（或与 stdout 写入同一流）的重定向。约定：**业务结果走 stdout**（如 `--json-output`）、**日志与提示走 stderr**；合并后会破坏 JSON/结构化解析与排障。若需保存输出或日志，应使用脚本自身提供的参数或约定方式，而不是改 shell 重定向。

## 2. 脚本与依赖

### 2.1 统一编辑入口（safe_page_edit.py --action edit）

- **脚本路径**：`scripts/safe_page_edit.py`（在技能根目录 `studio-space-manager/` 下执行）
- **认证**：`coclaw_empno`、`coclaw_token`（环境变量）
- **内容类型检测**：自动识别Markdown vs HTML（基于特征模式匹配和分数计算）
- **转换机制**：Markdown通过 `markdown_to_html()` 转换为HTML（带PlantUML保护）
- **支持的操作**：
  - `--content "Markdown内容"`：直接传递Markdown字符串
  - `--md-file ./page.md`：从本地Markdown文件读取（首行可选包含页面URL）
  - `--html-file ./page.html`：从本地HTML文件读取（直传模式）
- **日志级别**：INFO（操作摘要）、DEBUG（详细过程）
- **错误处理**：API超时/错误时降级到本地转换，防止数据丢失

### 2.2 专业编辑脚本（studio_space_page_edit.py）

- **脚本路径**：`scripts/studio_space_page_edit.py`（在技能根目录 `studio-space-manager/` 下执行）
- **认证**：`coclaw_empno`、`coclaw_token`
- **整页/章节**：内容可为 `--content` 或 `--content-file`；Markdown会经 `markdown2html` 转 HTML 后回写
- **表格单元格**：需用户或上游步骤提供 **`blockId`**（本脚本不内置 block 树枚举）；`row-index`/`col-index` 均为 **0 起算**

## 3. 常用触发词

### 3.1 统一编辑入口（safe_page_edit.py --action edit）

- **通用**：编辑页面、更新正文、回写页面、替换页面内容、把 md/html 写回空间
- **Markdown场景**：把本地md保存到这个页面、用这段Markdown更新页面
- **HTML场景**：用这个HTML更新页面、从本地html文件读取并更新

### 3.2 专业编辑脚本（studio_space_page_edit.py）

- 整页：编辑页面、更新正文、回写页面、替换页面内容、把 md 写回空间
- 章节：更新章节、改某一节、章节「xxx」改成
- 表格：改表格第几行第几列、更新单元格、表格里某个格子（需 blockId）

## 4. 意图 -> 命令决策表

### 4.1 统一编辑入口（safe_page_edit.py --action edit）

| 用户意图 | 必要参数 | 可选参数 | 命令要点 |
|---------|----------|----------|----------|
| 直接提供Markdown | `url` 或 `spaceId+pageId` + Markdown文本 | `--title`、`--json-output` | `--action edit --content "# 标题\n正文"` |
| 从本地md文件读取 | `url` 或 `spaceId+pageId` + md文件路径 | `--title`、`--json-output` | `--action edit --md-file ./page.md` |
| 从本地html文件读取 | `url` 或 `spaceId+pageId` + html文件路径 | `--title`、`--json-output` | `--action edit --html-file ./page.html` |

**智能检测**：自动识别内容类型（Markdown vs HTML），无需手动指定 `--content-type`

### 4.2 专业编辑脚本（studio_space_page_edit.py）

| 用户意图 | 必要参数 | 可选参数 | 命令要点 |
|---------|----------|----------|----------|
| 整页回写 | `url` 或 `spaceId+pageId` + 正文 | `--title`、`--content-html`、`--json-output` | `--mode page`（默认） |
| 本地 md 回写 | `content-file`（首行页面 URL） | `--title`、`--json-output` | 可省略 `--page-url`，从文件首行解析 |
| 更新章节 | `chapter-name` + 新内容 + 定位 | `--position`（同名章节，默认 1）、`--content-html` | `--mode chapter` |
| 更新表格单元格 | `block-id` + `row-index` + `col-index` + `cell-value` + 定位 | `--cell-value-html`、`--json-output` | `--mode table-cell` |

**定位**：`--page-url` 与 `--space-id`+`--page-id` 二选一（与 `rewrite_page.py` 等脚本一致）。

## 5. 参数提取规则

### 5.1 统一编辑入口（safe_page_edit.py --action edit）

- **URL 优先**：有 `i.zte.com.cn` 页面链接时优先 `--url`
- **内容类型检测**：自动识别Markdown vs HTML（基于特征模式匹配）
  - Markdown特征：`#`, `##`, `- `, `**text**`, `[text](url)`, PlantUML语法等
  - HTML特征：`<div>`, `<p>`, `<h1>`, `<table>`, `<tr>`, `<td>`等标签
  - 混合内容：计算特征分数，高分者胜出；不确定时默认当作Markdown
- **从本地文件读取**：
  - `--md-file ./page.md`：自动转HTML（PlantUML保护）
  - `--html-file ./page.html`：直传模式
  - 文件首行可选包含页面URL（与 `--url` 冲突时以后者为准）

### 5.2 专业编辑脚本（studio_space_page_edit.py）

- **URL 优先**：有 `i.zte.com.cn` 页面链接时优先 `--page-url`
- **从本地 md 推断页面**：`space_page_tomd.py` 导出的文件首行为页面完整链接时，仅用 `--content-file` 即可
- **章节名**：`--chapter-name` 为**纯文本章节标题**（不含 Markdown 标记）；正文仍用 Markdown/HTML 传入 `--content` 或文件
- **表格**：`row-index`/`col-index` 为 0 基；多表格时以 block 内**第一个** `<table>` 为准，复杂版式需人工确认 block

## 6. 澄清问题模板（缺参时）

### 6.1 统一编辑入口（safe_page_edit.py --action edit）

- 无正文且无文件：「请提供Markdown/HTML内容，或指定本地文件路径（--md-file / --html-file）。」
- 无定位信息：「请提供页面 URL，或 spaceId + pageId。」
- 内容类型模糊（混合特征）：「检测到内容同时包含Markdown和HTML特征，您希望以哪种格式处理？（默认使用Markdown）」

### 6.2 专业编辑脚本（studio_space_page_edit.py）

- 整页/章节但无正文：「请提供新正文（或本地 md 文件路径）。」
- 章节但无章节名：「要更新的是哪一节？请给出章节标题原文。」
- 表格但无 blockId：「需要表格所在内容块的 blockId；是否先用其它工具/接口列出页面 block 后再更新？」
- 无定位信息：「请提供页面 URL，或 spaceId + pageId。」

## 7. 命令模板

以下均在技能根目录执行：`cd <studio-space-manager根目录>`。

### 7.1 统一编辑入口（safe_page_edit.py --action edit，推荐）

```bash
# 直接提供Markdown内容（自动转HTML）
python3 scripts/safe_page_edit.py --action edit \
  --url "https://i.zte.com.cn/#/shared/<spaceId>/wiki/page/<pageId>/view" \
  --content "# 标题\n这是**Markdown**内容"

# 从本地Markdown文件读取（自动转HTML，PlantUML保护）
python3 scripts/safe_page_edit.py --action edit \
  --url "https://i.zte.com.cn/#/shared/<spaceId>/wiki/page/<pageId>/view" \
  --md-file ./page.md

# 从本地HTML文件读取（直传模式）
python3 scripts/safe_page_edit.py --action edit \
  --url "https://i.zte.com.cn/#/shared/<spaceId>/wiki/page/<pageId>/view" \
  --html-file ./page.html

# 机器可读JSON输出
python3 scripts/safe_page_edit.py --action edit \
  --url "<url>" --content "# 标题" --json-output
```

### 7.2 专业编辑脚本（studio_space_page_edit.py，精细控制）

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

### 7.3 使用建议

- **优先选择**：用户主动提供Markdown/HTML内容时，使用 `safe_page_edit.py --action edit`（自动检测、智能转换）
- **精细控制**：需要章节更新、表格单元格编辑等特殊场景，使用 `studio_space_page_edit.py`
- **混合工作流**：先用 `safe_page_edit.py` 整页回写，再用 `studio_space_page_edit.py` 微调特定章节/表格

## 8. 约束与说明

### 8.1 统一编辑入口（safe_page_edit.py）

- **智能检测**：自动识别Markdown vs HTML，无需手动指定内容类型
- **PlantUML保护**：确保图表格式在转换过程中100%保真
- **错误降级**：API超时/错误时自动降级到本地转换，防止数据丢失
- **日志记录**：INFO级别显示关键步骤，DEBUG级别显示详细过程

### 8.2 专业编辑脚本（studio_space_page_edit.py）

- **i.zte.com.cn**：须走本仓库脚本，禁止对页面使用 web 抓取类工具（与总技能一致）。
- **与改标题区分**：只改标题用 `rewrite_page.py`；改正文/章节/表格用本脚本。
- **与新建区分**：新建页面用 `studio_space_page_save.py` 或模板流 `space_template_page.py`（见 `page-content` 子技能）。
- **运行目录与 UTF-8**：见总技能第 9 节「运行环境与字符集」。

### 8.3 选择指南

| 场景 | 推荐脚本 | 理由 |
|------|---------|------|
| 用户直接提供Markdown文本 | `safe_page_edit.py --action edit` | 自动检测、一键转换 |
| 从本地md文件读取 | `safe_page_edit.py --action edit --md-file ./page.md` | PlantUML保护、详细日志 |
| 从本地html文件读取 | `safe_page_edit.py --action edit --html-file ./page.html` | 直传模式、无转换开销 |
| 更新特定章节 | `studio_space_page_edit.py --mode chapter` | 精细控制章节定位 |
| 更新表格单元格 | `studio_space_page_edit.py --mode table-cell` | block级精确编辑 |
| 混合工作流 | 先用 `safe_page_edit.py` 整页回写，再用 `studio_space_page_edit.py` 微调 | 结合两者优势 |

## 9. 典型组合场景

### 9.1 先复制后编辑

```bash
# 步骤1：复制到测试空间
python3 scripts/copy_page.py --url "<源页面URL>" --target-space-id "<测试spaceId>"

# 步骤2：编辑新页面
python3 scripts/safe_page_edit.py --action edit \
  --url "<新页面URL>" \
  --content "# 更新后的内容"
```

### 9.2 先改标题再编辑正文

```bash
# 步骤1：仅改标题
python3 scripts/rewrite_page.py --url "<url>" --title "新标题"

# 步骤2：编辑正文
python3 scripts/safe_page_edit.py --action edit \
  --url "<url>" \
  --md-file ./updated_content.md
```

### 9.3 批量更新多个页面

```bash
# 先用 safe_page_edit.py 统一处理所有页面的Markdown转HTML和回写
for file in *.md; do
  python3 scripts/safe_page_edit.py --action edit \
    --url "$(head -1 "$file")" \
    --md-file "$file"
done
```

## 10. 与 page-ops 的关系

本技能与 **`page-ops`** 同级，路由上常与 `page-ops`（复制、仅改标题、子页面、附件等）组合使用；其它页面操作总表见 **`../page-ops/SKILL.md`**。

### 10.1 编辑入口选择指南

| 场景 | 推荐脚本 | 理由 |
|------|---------|------|
| 用户直接提供Markdown/HTML内容 | `safe_page_edit.py --action edit` | 自动检测类型、智能转换、PlantUML保护 |
| 从本地Markdown文件回写 | `safe_page_edit.py --action edit --md-file ./page.md` | 一键处理、无需手动指定格式 |
| 需要章节级别精细控制 | `studio_space_page_edit.py --mode chapter` | 支持按章节标题定位和更新 |
| 需要修改表格单元格 | `studio_space_page_edit.py --mode table-cell` | 支持blockId和行列索引定位 |
| 批量处理多个Markdown文件 | `safe_page_edit.py --action edit` | 统一的错误处理和日志记录 |
| 混合工作流（先整页后微调） | 先用 `safe_page_edit.py`，再用 `studio_space_page_edit.py` | 发挥各自优势 |
