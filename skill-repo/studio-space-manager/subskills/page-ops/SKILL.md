---
name: studio-space-page-ops
description: >-
  iCenter 页面操作子技能：页面复制/拷贝（`--level 1` 为当前页及**全部层级**子页面即整棵子树；可选目标空间/目录或「当前目录」语义）；
  仅更新标题保留正文；**页面正文编辑**（整页/章节/表格单元格，`studio_space_page_edit.py`，细则见 `../page-edit/SKILL.md`）；
  子页面与页面层级结构查询；空间最新更新页面列表；
  附件识别清单、批量下载到指定目录、批量上传（单文件 <=100MB，超限本地拦截并提示）、docId 单文档下载。
version: 1.3.0
---
# 页面操作子技能

## 1. 适用范围

- 页面复制/拷贝（可选包含子页面）
- 页面标题更新（保留原内容）
- **页面正文编辑**：整页 Markdown/HTML 回写、按章节更新、表格单元格更新（详见 `../page-edit/SKILL.md`）
- 子页面查询（URL 或参数）
- 空间最新页面列表
- 附件识别、下载与上传（URL、spaceId/pageId、docId）

### 1.1 调用禁令（与总入口 `SKILL.md` 同步）

- **禁止**在调用本子技能所列脚本（含下文示例命令）时，自行在 shell 命令中追加 `2>&1`、`&>`、`> ... 2>&1` 等会把 **stderr 合并进 stdout**（或与 stdout 写入同一流）的重定向。约定：**业务结果走 stdout**（如 `--json-output`）、**日志与提示走 stderr**；合并后会破坏 JSON/结构化解析与排障。若需保存输出或日志，应使用脚本自身提供的参数或约定方式，而不是改 shell 重定向。

## 2. 脚本映射

- `scripts/copy_page.py`
- `scripts/rewrite_page.py`
- `scripts/studio_space_page_edit.py`（页面正文编辑；用法见 `../page-edit/SKILL.md`）
- `scripts/get_page_children.py`
- `scripts/space_latest_pages.py`
- `scripts/studio_space_attachment.py`

## 3. 常用触发词

- 复制/拷贝：复制、拷贝、副本、迁移到、放到、复制到当前目录
- 标题更新：改标题、重命名页面标题、标题改为、更新标题
- **正文编辑**：编辑页面、更新正文、回写页面、更新章节、改表格单元格（与「仅改标题」区分）
- 子页面查询：子页面、页面结构、页面层级、下级页面
- 最新页面：最近更新、最新页面、空间最近内容
- 附件：附件、下载附件、附件清单、上传附件、文档附件、docId

## 4. 意图识别与命令路由（核心）

### 4.1 路由优先级（避免冲突）

当一句话同时出现多类关键词时，按以下顺序判定主意图：

1. **附件下载/上传**（出现“附件/docId/下载附件/上传附件”）  
2. **页面复制**（出现“复制/拷贝/副本/迁移”）  
3. **标题更新**（出现“改标题/标题改为/重命名标题”）  
4. **页面正文编辑**（出现“编辑正文/更新正文/回写页面/更新章节/改表格/单元格”等，且**非**仅改标题）→ `studio_space_page_edit.py`（见 `../page-edit/SKILL.md`）  
5. **子页面查询**（出现“子页面/层级/结构”）  
6. **最新页面列表**（出现“最近更新/最新页面”）

若同一句明确包含多个动作（如“先复制再改标题”），按用户语义顺序拆分为多步执行。

### 4.2 意图 -> 命令决策表

| 用户意图 | 必要参数 | 可选参数 | 首选命令 | 说明 |
|---------|----------|----------|----------|------|
| 复制页面 | `url` 或 `spaceId+pageId` | `target_url` / `target_space_id+target_node_id` / `level` / `title-source+title-target` | `copy_page.py` | 默认复制到目标空间主页；`--level 1` = 当前页 + **所有层级**子页面（整棵子树，非仅一级子页） |
| 更新标题 | `title` + (`page-url` 或 `spaceId+pageId`) | `json-output` | `rewrite_page.py` | 仅改标题，保留原内容 |
| 整页正文编辑 | `url` 或 `spaceId+pageId` + 新正文 | `content-file` / `title` / `content-html` / `json-output` | `studio_space_page_edit.py` | 默认 `--mode page`；细则见 `../page-edit/SKILL.md` |
| 章节正文编辑 | `chapter-name` + 新内容 + 定位 | `position` / `content-html` / `json-output` | `studio_space_page_edit.py --mode chapter` | 见 `../page-edit/SKILL.md` |
| 表格单元格编辑 | `block-id` + `row-index` + `col-index` + `cell-value` + 定位 | `cell-value-html` / `json-output` | `studio_space_page_edit.py --mode table-cell` | 见 `../page-edit/SKILL.md` |
| 查子页面 | `url` 或 `spaceId+contentId` | `json-output` | `get_page_children.py` | 用于页面结构梳理 |
| 查空间最新页面 | `spaceId` | `offset` / `limit` | `space_latest_pages.py` | 默认第一页，建议 limit=20 |
| 附件识别/下载 | `url` 或 `spaceId+pageId` 或 `docId` | `download` / `output-dir` | `studio_space_attachment.py` | 默认先识别，确认后下载 |
| 附件上传 | `spaceId` + `upload-file` | `pageId` / `json` | `studio_space_attachment.py` | 单文件上限 100MB；可重复 `--upload-file` 批量上传 |

### 4.3 参数提取规则

- **URL优先**：用户给了页面 URL 时，优先走 `--url/--page-url`。
- **ID兜底**：无 URL 时使用显式 `spaceId/pageId/contentId`。
- **当前目录语义**：用户说“当前目录/原目录”，表示目标父节点=源页面父节点。
- **复制层级（`copy_page.py --level`）**：`0` = 仅复制当前页面；`1` = 复制当前页面及其下**所有层级**的子页面（整棵子树，与「只复制直属一级子页」不同）。用户说「包含子页面/连同子页/整棵树/全部下级」等 -> `--level 1`，否则默认 `--level 0`。
- **附件执行模式**：  
  - “看看有哪些附件/列出附件” -> 仅识别（不加 `--download`）  
  - “全部下载/下载到xx目录” -> 加 `--download --output-dir <dir>`
  - “上传附件/把本地文件传到页面” -> 加 `--upload-file <path>`（可重复）；必须提供 `--space-id`（或可解析出 spaceId 的 URL）

### 4.4 澄清问题模板（缺参时必须询问）

- 复制页面缺目标位置：  
  “要复制到哪个空间/目录？可提供目标页面 URL，或说‘复制到当前目录’。”
- 标题更新缺新标题：  
  “请提供新标题内容。”
- 子页面查询缺定位信息：  
  “请提供页面 URL，或提供 spaceId + contentId。”
- 最新页面缺 spaceId：  
  “请提供空间 ID，或给我该空间任一页面链接用于解析。”
- 附件下载缺定位信息：  
  “请提供页面 URL、spaceId+pageId，或 docId 中任意一种。”
- 附件上传缺定位信息：  
  “请提供 spaceId（或可解析出 spaceId 的页面 URL）以及本地文件路径。”
- 附件上传超限：  
  “单个附件不能超过 100MB，请压缩/拆分后重试。”
- 附件上传鉴权失败：  
  “当前账号未开通上传接口权限，请联系管理员申请授权。”
- 正文/章节编辑缺内容：  
  “请提供新正文（或本地 md 文件路径）；章节编辑还需章节标题。”
- 表格单元格缺 blockId：  
  “请提供表格所在 block 的 blockId，或先通过其它方式列出页面 block 后再更新。”

## 5. 页面编辑子技能（专表）

整页 / 章节 / 表格单元格的完整意图表、澄清模板与命令示例见：

**`subskills/page-edit/SKILL.md`**

## 6. 命令模板

```bash
# 复制页面（默认复制到目标空间主页）
python3 scripts/copy_page.py --url "https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view"

# 复制页面并包含其下所有层级的子页面（整棵子树，--level 1）
python3 scripts/copy_page.py --space-id "源空间ID" --page-id "源页面ID" --target-space-id "目标空间ID" --target-node-id "目标父页面ID" --level 1

# 更新页面标题（保留原内容）
python3 scripts/rewrite_page.py --page-url "https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view" --title "新标题"

# 查询子页面
python3 scripts/get_page_children.py --url "https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view"

# 查询空间最新页面
python3 scripts/space_latest_pages.py <spaceId> --offset 0 --limit 20

# 附件识别（仅列表）
python3 scripts/studio_space_attachment.py --url "https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view"

# 附件下载（到指定目录）
python3 scripts/studio_space_attachment.py --url "https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view" --download --output-dir ./downloads

# 上传单个附件（默认自动挂载到页面正文）
python3 scripts/studio_space_attachment.py --space-id "{spaceId}" --page-id "{pageId}" --upload-file "/path/to/local.docx"

# 批量上传多个附件
python3 scripts/studio_space_attachment.py --space-id "{spaceId}" --upload-file "/path/a.docx" --upload-file "/path/b.zip" --json

# 仅上传到文件服务，不自动挂载到页面正文
python3 scripts/studio_space_attachment.py --space-id "{spaceId}" --page-id "{pageId}" --upload-file "/path/to/local.docx" --no-attach-to-page

# 上传并挂载到指定章节（同名章节可用 --attach-position）
python3 scripts/studio_space_attachment.py --page-url "https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view" --upload-file "/path/to/local.docx" --attach-chapter-name "1. 背景" --attach-position 1 --attach-mode append

# docId 单文档下载
python3 scripts/studio_space_attachment.py --doc-id "{docId}"
```

## 7. 约束与澄清

- **`--level 1`**：`copyWithSpace` 语义为复制当前页及**全部层级**子页面；脚本仅在执行前用**直接子页数量**做 ≤100 的防堵校验，与「是否多级」无关。
- 复制到“当前目录/原目录”时先解析源页面父节点 ID 再执行复制。
- 标题更新需明确新标题；优先使用页面 URL 解析定位。
- **正文编辑**与标题更新区分：改标题用 `rewrite_page.py`；改正文/章节/表格用 `studio_space_page_edit.py`（见 `../page-edit/SKILL.md`）。
- 附件任务默认先识别清单，再按用户确认执行下载。
- 附件上传有接口限制：单文件必须 <=100MB；脚本会在请求前做本地校验并给出明确提示。
- 附件上传默认自动将附件链接回写到页面正文；若只上传不挂载，显式加 `--no-attach-to-page`。
- 支持指定章节挂载：`--attach-chapter-name` + `--attach-position`；`--attach-mode` 可选 `append`（章节末尾）或 `prepend`（章节开头）。
- 附件上传新增能力遵循跨平台约束：支持 Windows/Linux 的本地路径解析（含 `~` 与环境变量展开），文件名与日志按 UTF-8 处理。
