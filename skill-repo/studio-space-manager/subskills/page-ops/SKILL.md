---
name: studio-space-page-ops
description: >-
  iCenter 页面操作子技能:页面复制/拷贝(`--level 1` 为当前页及**全部层级**子页面即整棵子树;可选目标空间/目录或「当前目录」语义);
  仅更新标题保留正文;**页面正文编辑**(整页/章节/表格单元格,`studio_space_page_edit.py`,细则见 `../page-edit/SKILL.md`);
  子页面与页面层级结构查询;空间最新更新页面列表;
  附件识别清单、批量下载到指定目录、批量上传(单文件 <=100MB,超限本地拦截并提示)、docId 单文档下载;
  **附件上传到表格单元格**(`attachment_to_table_cell.py`,一键上传并插入指定单元格);
  **图片上传并插入页面**(`upload_image_to_page.py`,上传图片到文件服务并插入页面正文（支持页面末尾、指定章节、表格单元格）);
  **HTML 包含宏**(`studio_space_html_include_macro.py`,上传本地 HTML 文件,以 HtmlIncludeMacro 宏形式渲染到页面正文/章节,避免复杂样式被 xss 过滤破坏)。
version: 1.5.0
---
# 页面操作子技能

## 1. 适用范围

- 页面复制/拷贝(可选包含子页面)
- 页面标题更新(保留原内容)
- **页面正文编辑**:整页 Markdown/HTML 回写、按章节更新、表格单元格更新(详见 `../page-edit/SKILL.md`)
- 子页面查询(URL 或参数)
- 空间最新页面列表
- 附件识别、下载与上传(URL、spaceId/pageId、docId)
- **图片上传并插入页面**:将本地图片上传到空间并插入页面正文，支持指定插入位置（页面末尾、指定章节、表格单元格）和自定义图片说明

### 1.1 调用禁令(与总入口 `SKILL.md` 同步)

- **禁止**在调用本子技能所列脚本(含下文示例命令)时,自行在 shell 命令中追加 `2>&1`、`&>`、`> ... 2>&1` 等会把 **stderr 合并进 stdout**(或与 stdout 写入同一流)的重定向。约定:**业务结果走 stdout**(如 `--json-output`)、**日志与提示走 stderr**;合并后会破坏 JSON/结构化解析与排障。若需保存输出或日志,应使用脚本自身提供的参数或约定方式,而不是改 shell 重定向。

## 2. 脚本映射

- `scripts/copy_page.py`
- `scripts/rewrite_page.py`
- `scripts/studio_space_page_edit.py`(页面正文编辑;用法见 `../page-edit/SKILL.md`)
- `scripts/get_page_children.py`
- `scripts/space_latest_pages.py`
- `scripts/studio_space_attachment.py`
- `scripts/attachment_to_table_cell.py`(附件上传并挂载到表格单元格)
- `scripts/upload_image_to_page.py`(图片上传并插入页面，支持多种插入位置)
- `scripts/studio_space_html_include_macro.py`(**HTML 包含宏**:上传本地 HTML 文件并渲染到页面)

## 3. 常用触发词

- 复制/拷贝:复制、拷贝、副本、迁移到、放到、复制到当前目录
- 标题更新:改标题、重命名页面标题、标题改为、更新标题
- **正文编辑**:编辑页面、更新正文、回写页面、更新章节、改表格单元格(与「仅改标题」区分)
- 子页面查询:子页面、页面结构、页面层级、下级页面
- 最新页面:最近更新、最新页面、空间最近内容
- 附件:附件、下载附件、附件清单、上传附件、文档附件、docId
- 附件上传到表格:附件上传到表格、把附件放到表格里、表格中插入附件
- 图片:上传图片、插入图片、将图片添加到页面、上传并插入图片
- **HTML 包含宏**:HTML包含宏、上传html、html渲染、复杂页面、动态报表、html宏

## 4. 意图识别与命令路由(核心)

### 4.1 路由优先级(避免冲突)

当一句话同时出现多类关键词时,按以下顺序判定主意图:

1. **附件下载/上传**(出现"附件/docId/下载附件/上传附件")
2. **页面复制**(出现"复制/拷贝/副本/迁移")
3. **标题更新**(出现"改标题/标题改为/重命名标题")
4. **页面正文编辑**(出现"编辑正文/更新正文/回写页面/更新章节/改表格/单元格"等,且**非**仅改标题)→ `studio_space_page_edit.py`(见 `../page-edit/SKILL.md`)
5. **子页面查询**(出现"子页面/层级/结构")
6. **最新页面列表**(出现"最近更新/最新页面")

若同一句明确包含多个动作(如"先复制再改标题"),按用户语义顺序拆分为多步执行。

### 4.2 意图 -> 命令决策表

| 用户意图 | 必要参数 | 可选参数 | 首选命令 | 说明 |
|---------|----------|----------|----------|------|
| 复制页面 | `url` 或 `spaceId+pageId` | `target_url` / `target_space_id+target_node_id` / `level` / `title-source+title-target` | `copy_page.py` | 默认复制到目标空间主页;`--level 1` = 当前页 + **所有层级**子页面(整棵子树,非仅一级子页) |
| 更新标题 | `title` + (`page-url` 或 `spaceId+pageId`) | `json-output` | `rewrite_page.py` | 仅改标题,保留原内容 |
| 整页正文编辑 | `url` 或 `spaceId+pageId` + 新正文 | `content-file` / `title` / `content-html` / `json-output` | `studio_space_page_edit.py` | 默认 `--mode page`;细则见 `../page-edit/SKILL.md` |
| 章节正文编辑 | `chapter-name` + 新内容 + 定位 | `position` / `content-html` / `json-output` | `studio_space_page_edit.py --mode chapter` | 见 `../page-edit/SKILL.md` |
| 表格单元格编辑 | `block-id` + `row-index` + `col-index` + `cell-value` + 定位 | `cell-value-html` / `json-output` | `studio_space_page_edit.py --mode table-cell` | 见 `../page-edit/SKILL.md` |
| 查子页面 | `url` 或 `spaceId+contentId` | `json-output` | `get_page_children.py` | 用于页面结构梳理 |
| 查空间最新页面 | `spaceId` | `offset` / `limit` | `space_latest_pages.py` | 默认第一页,建议 limit=20 |
| 附件识别/下载 | `url` 或 `spaceId+pageId` 或 `docId` | `download` / `output-dir` | `studio_space_attachment.py` | 默认先识别,确认后下载 |
| 附件上传 | `spaceId` + `upload-file` | `pageId` / `json` / `--no-attach-to-page` | `studio_space_attachment.py` | 单文件上限 100MB;可重复 `--upload-file` 批量上传;默认自动挂载到页面正文末尾
| 附件上传到表格单元格 | `page-url` 或 `spaceId+pageId` + `block-id` + `row-index` + `col-index` + `upload-file` | `json-output` | `attachment_to_table_cell.py` | 将附件插入到表格指定单元格;行列索引从 0 开始;多个文件依次填充列;自动清理页面底部重复附件段落 |
| 图片上传并插入页面 | `space-id` + (`page-id` 或 `page-url`) + `image-path` (可重复) + (插入模式参数: `--insert-mode` + `--chapter-name` 或 `--block-id` + `--row-index` + `--col-index`) | `alt`、`--alt-prefix`、`--no-attach`、`--json`、`--insert-mode`、`--chapter-name`、`--block-id`、`--row-index`、`--col-index` | `upload_image_to_page.py` | 批量上传图片，支持插入位置：页面末尾（默认）、指定章节（--insert-mode chapter --chapter-name）、表格单元格（--insert-mode table-cell --block-id --row-index --col-index）。使用 --no-attach 仅上传不插入；alt 通过 --alt 或 --alt-prefix 控制。 |
| **HTML 包含宏** | `url` 或 `spaceId+pageId` + `html-file` | `--attach-chapter-name` / `--attach-position` / `--attach-mode` / `--json` | `studio_space_html_include_macro.py` | 上传本地 HTML 文件，以 HtmlIncludeMacro 宏形式渲染到页面（整页末尾/章节），上传后自动回写。macroId/blockId/fileKey 自动生成。单文件 ≤100MB；单页宏上限 10 个。复杂样式 HTML 请用此方式，避免直写被 xss 过滤破坏 |

### 4.3 参数提取规则

- **URL优先**:用户给了页面 URL 时,优先走 `--url/--page-url`。
- **ID兜底**:无 URL 时使用显式 `spaceId/pageId/contentId`。
- **当前目录语义**:用户说"当前目录/原目录",表示目标父节点=源页面父节点。
- **复制层级(`copy_page.py --level`)**:`0` = 仅复制当前页面;`1` = 复制当前页面及其下**所有层级**的子页面(整棵子树,与「只复制直属一级子页」不同)。用户说「包含子页面/连同子页/整棵树/全部下级」等 -> `--level 1`,否则默认 `--level 0`。
- **附件执行模式**:
  - "看看有哪些附件/列出附件" -> 仅识别(不加 `--download`)
  - "全部下载/下载到xx目录" -> 加 `--download --output-dir <dir>`
  - "上传附件/把本地文件传到页面" -> 加 `--upload-file <path>`(可重复);必须提供 `--space-id`(或可解析出 spaceId 的 URL)

### 4.4 澄清问题模板(缺参时必须询问)

- 复制页面缺目标位置:
  "要复制到哪个空间/目录?可提供目标页面 URL,或说'复制到当前目录'。"
- 标题更新缺新标题:
  "请提供新标题内容。"
- 子页面查询缺定位信息:
  "请提供页面 URL,或提供 spaceId + contentId。"
- 最新页面缺 spaceId:
  "请提供空间 ID,或给我该空间任一页面链接用于解析。"
- 附件下载缺定位信息:
  "请提供页面 URL、spaceId+pageId,或 docId 中任意一种。"
- 附件上传缺定位信息:
  "请提供 spaceId(或可解析出 spaceId 的页面 URL)以及本地文件路径。"
- 附件上传到表格单元格缺参：
  “请提供页面 URL（或 spaceId+pageId）、表格 block-id、目标行索引（从0开始）、起始列索引（从0开始）以及要上传的本地文件路径。”
- 图片上传缺空间/页面定位：
  “请提供空间 ID（或页面链接）以及要上传的本地图片路径。”
- 图片上传缺图片路径：
  “请提供本地图片文件路径，或使用 --image-path 重复指定以批量上传。”
- 图片上传缺章节定位：
  “请使用 --chapter-name 指定目标章节标题。”
- 图片上传缺表格定位：
  “请提供 --block-id、--row-index、--col-index 以定位表格单元格。”
- 图片上传超限：
  “单个图片不能超过 10MB，请压缩后重试。”
- 图片上传 batch 说明：
  “支持一次性上传多张图片，使用 --image-path 重复指定。图片会自动按顺序追加到页面末尾，alt 说明可通过 --alt 或 --alt-prefix 定制。”
- 附件上传超限：
  “单个附件不能超过 100MB，请压缩/拆分后重试。”
- 附件上传鉴权失败:
  "当前账号未开通上传接口权限,请联系管理员申请授权。"
- 正文/章节编辑缺内容:
  "请提供新正文(或本地 md 文件路径);章节编辑还需章节标题。"
- 表格单元格缺 blockId:
  "请提供表格所在 block 的 blockId,或先通过其它方式列出页面 block 后再更新。"
- HTML 包含宏缺 html 文件:
  "请提供本地 HTML 文件路径（--html-file），以及目标页面 URL 或 spaceId+pageId。"
- HTML 包含宏缺页面定位:
  "请提供页面链接（用于解析 spaceId+pageId），或直接给 --space-id 与 --page-id。"
- HTML 包含宏缺章节定位:
  "请使用 --attach-chapter-name 指定目标章节标题（可加 --attach-position 指定同名章节第几个）。"
- HTML 包含宏超限:
  "单页 HTML 包含宏上限 10 个，当前页面已达到上限，请先删除部分宏或换页。"

## 5. 页面编辑子技能(专表)

整页 / 章节 / 表格单元格的完整意图表、澄清模板与命令示例见:

**`subskills/page-edit/SKILL.md`**

## 6. 命令模板

```bash
# 复制页面(默认复制到目标空间主页)
python3 scripts/copy_page.py --url "https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view"

# 复制页面并包含其下所有层级的子页面(整棵子树,--level 1)
python3 scripts/copy_page.py --space-id "源空间ID" --page-id "源页面ID" --target-space-id "目标空间ID" --target-node-id "目标父页面ID" --level 1

# 更新页面标题(保留原内容)
python3 scripts/rewrite_page.py --page-url "https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view" --title "新标题"

# 查询子页面
python3 scripts/get_page_children.py --url "https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view"

# 查询空间最新页面
python3 scripts/space_latest_pages.py <spaceId> --offset 0 --limit 20

# 附件识别(仅列表)
python3 scripts/studio_space_attachment.py --url "https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view"

# 附件下载(到指定目录)
python3 scripts/studio_space_attachment.py --url "https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view" --download --output-dir ./downloads

# 上传单个附件(默认自动挂载到页面正文)
python3 scripts/studio_space_attachment.py --space-id "{spaceId}" --page-id "{pageId}" --upload-file "/path/to/local.docx"

# 批量上传多个附件
python3 scripts/studio_space_attachment.py --space-id "{spaceId}" --upload-file "/path/a.docx" --upload-file "/path/b.zip" --json

# 仅上传到文件服务,不自动挂载到页面正文
python3 scripts/studio_space_attachment.py --space-id "{spaceId}" --page-id "{pageId}" --upload-file "/path/to/local.docx" --no-attach-to-page

# 上传并挂载到指定章节(同名章节可用 --attach-position)
python3 scripts/studio_space_attachment.py --page-url "https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view" --upload-file "/path/to/local.docx" --attach-chapter-name "1. 背景" --attach-position 1 --attach-mode append

# 附件上传并插入到表格单元格(新功能)
python3 scripts/attachment_to_table_cell.py \
  --page-url "https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view" \
  --block-id "BQU4T2MY" \
  --upload-file "/path/a.pdf" \
  --upload-file "/path/b.pdf" \
  --row-index 1 --col-index 0

# 上传单张图片并自动插入到页面正文
python3 scripts/upload_image_to_page.py --space-id "{spaceId}" --page-id "{pageId}" --image-path "/path/to/image.png"

# 批量上传多张图片（使用 --image-path 重复指定）
python3 scripts/upload_image_to_page.py \
  --space-id "{spaceId}" \
  --page-id "{pageId}" \
  --image-path "/path/to/image1.png" \
  --image-path "/path/to/image2.jpg" \
  --image-path "/path/to/image3.png" \
  --alt-prefix "图" \
  --json

# 使用页面 URL 定位上传图片
python3 scripts/upload_image_to_page.py --page-url "https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view" --image-path "/path/to/image.jpg" --alt "示意图"

# 仅上传图片,不自动插入页面
python3 scripts/upload_image_to_page.py --space-id "{spaceId}" --image-path "/path/to/image.png" --no-attach --json


# 🆕 将图片插入到指定章节
python3 scripts/upload_image_to_page.py \
  --page-url "https://i.zte.com.cn/#/shared/<spaceId>/wiki/page/<pageId>/view" \
  --insert-mode chapter \
  --chapter-name "1. 背景" \
  --image-path "/path/to/diagram.png" \
  --alt "架构图"

# 🆕 将图片插入到表格指定单元格
python3 scripts/upload_image_to_page.py \
  --page-url "https://i.zte.com.cn/#/shared/<spaceId>/wiki/page/<pageId>/view" \
  --insert-mode table-cell \
  --block-id "BQU4T2MY" \
  --row-index 1 --col-index 0 \
  --image-path "/path/to/chart.png" \
  --alt "数据图表"

# docId 单文档下载
python3 scripts/studio_space_attachment.py --doc-id "{docId}"

# 🆕 HTML 包含宏：上传本地 html 并以宏形式渲染到页面末尾（推荐，macroId/blockId/fileKey 自动生成）
python3 scripts/studio_space_html_include_macro.py \
  --url "https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view" \
  --html-file "/path/to/report.html"

# 🆕 HTML 包含宏：挂载到指定章节（自动生成；同名章节多个可用 --attach-position 指定第几个）
python3 scripts/studio_space_html_include_macro.py \
  --space-id "{spaceId}" --page-id "{pageId}" \
  --html-file "/path/to/report.html" \
  --attach-chapter-name "1. 背景" --attach-mode append
```

## 7. 约束与澄清

- **`--level 1`**:`copyWithSpace` 语义为复制当前页及**全部层级**子页面;脚本仅在执行前用**直接子页数量**做 ≤100 的防堵校验,与「是否多级」无关。
- 复制到"当前目录/原目录"时先解析源页面父节点 ID 再执行复制。
- 标题更新需明确新标题;优先使用页面 URL 解析定位。
- **正文编辑**与标题更新区分:改标题用 `rewrite_page.py`;改正文/章节/表格用 `studio_space_page_edit.py`(见 `../page-edit/SKILL.md`)。
- 附件任务默认先识别清单,再按用户确认执行下载。
- 附件上传有接口限制:单文件必须 <=100MB;脚本会在请求前做本地校验并给出明确提示。
- 附件上传默认自动将附件链接回写到页面正文;若只上传不挂载,显式加 `--no-attach-to-page`。
- 支持指定章节挂载:`--attach-chapter-name` + `--attach-position`;`--attach-mode` 可选 `append`(章节末尾)或 `prepend`(章节开头)。
- 附件上传到表格单元格(`attachment_to_table_cell.py`)自动清理页面底部重复附件段落;行列索引从0开始;多个文件依次填充列。
- 附件上传新增能力遵循跨平台约束:支持 Windows/Linux 的本地路径解析(含 `~` 与环境变量展开),文件名与日志按 UTF-8 处理。
- 图片上传限制:单文件必须 <=10MB;仅支持常见图片格式(PNG、JPG、JPEG、GIF、WEBP 等)。
- 图片上传默认自动将图片链接追加到页面正文末尾;若仅需上传,显式加 `--no-attach`。
- 图片 URL 通过 `uploadImage` 接口返回的 `path` 拼接生成:`https://icenterapi.zte.com.cn/{path}`。
- **HTML 包含宏**(`studio_space_html_include_macro.py`):将复杂样式/交互 HTML 以 HtmlIncludeMacro 宏渲染到页面,避免直写被 xss 过滤破坏(官方推荐方案)。
  - **流程**:上传本地 HTML(`--html-file`)→ 自动生成 macroId/blockId → 构造宏片段 → 自动回写页面(整页末尾或指定章节),无需额外参数;macroId/blockId/fileKey 均由脚本自动生成,不支持手动指定。
  - **上传接口**:`POST {SPACE_PLUGIN}/file/upload/html?docId={pageId}&spaceId={spaceId}&macroId={macroId}`,表单字段 `htmlFile`(multipart/form-data);单文件 ≤100MB,脚本请求前本地校验。
  - **渲染地址** = `https://icenterapi.zte.com.cn/zte-rdcloud-space-plugin/` + 接口返回的 `bo.urlPath`(NAS 直链);宏属性取自 `bo.nasFileName`/`bo.udmFileKey`/`bo.blockId`;未返回 urlPath 时兜底取返回的完整 http 地址。
  - **宏结构**(2.0 空间真实格式):`<div class="ze-macro" macro-key="HtmlIncludeMacro" macro-id=... block-id=... m-mode="upload" m-file-name="{nasFileName}" m-url="{base+urlPath}" m-file-key=...>...<div class="ze-macro-body">...</div></div>`;回写走 `updateContent`(MCP 同款)。
  - **挂载位置**:整页末尾(默认)或指定章节(`--attach-chapter-name` + `--attach-position`,同名章节从 1 起;`--attach-mode` 可选 `append`/`prepend`)。
  - **平台限制**:单页 HTML 包含宏上限 10 个,回写前统计已存在宏,达到上限即阻断提示;宏计数兼容 2.0(`macro-key`)与 1.0(`data-macro-key`)。
