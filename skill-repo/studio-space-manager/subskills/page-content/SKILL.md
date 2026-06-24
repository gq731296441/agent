---
name: studio-space-page-content
description: >-
  iCenter/Wiki 页面内容子技能：关键词搜索页面；按 URL 或 spaceId/pageId 读取正文；先读后由对话层总结；
  新建并保存 Markdown 或 HTML 页面；文档空间模板列表、模板正文、基于模板创建文档；
  转发页面到 iCenter 消息、邮件（抄送密送、主题与附言）或群组（列群、群名匹配）；
  指定页面的「页面浏览」宏数据（浏览记录表格，接口见 all/接口.md #58）。
  i.zte.com.cn 页面须走本仓库脚本，禁止 web 抓取类工具。
version: 1.3.1
---
# 页面内容子技能

## 1. 适用范围

- 页面关键词搜索（标题/条件可选）
- 按 spaceId/pageId 或 URL 读取页面正文
- 页面转 Markdown 并保存到本地（文件首行保留页面链接）
- 新建/保存页面（Markdown 或 HTML）
- **空间模板**：列出模板、查看模板内容、在指定父节点下基于模板新建文档
- 转发到 iCenter 消息、邮件、群组
- 指定页面的页面浏览记录（页面浏览宏执行结果，HTML 表格）
- **总结**：无独立脚本 → 先 `fetch_space_page` 取正文，再由对话层总结

### 1.1 调用禁令（与总入口 `SKILL.md` 同步）

- **禁止**在调用本子技能所列脚本（含下文示例命令）时，自行在 shell 命令中追加 `2>&1`、`&>`、`> ... 2>&1` 等会把 **stderr 合并进 stdout**（或与 stdout 写入同一流）的重定向。约定：**业务结果走 stdout**（如 `--json-output`）、**日志与提示走 stderr**；合并后会破坏 JSON/结构化解析与排障。若需保存输出或日志，应使用脚本自身提供的参数或约定方式，而不是改 shell 重定向。

## 2. 脚本映射

- `scripts/space_page_search.py`
- `scripts/fetch_space_page.py`
- `scripts/space_page_tomd.py`（页面转 Markdown 并保存本地文件）
- `scripts/studio_space_page_save.py`
- `scripts/space_template_page.py`（模板列表 / 模板详情 / 基于模板创建）
- `scripts/forward_page_to_msg.py`
- `scripts/forward_page_to_group.py`
- `scripts/space_get_macro_data.py`（页面浏览宏：`com.zte.wiki.macro.PageViewsMacro`）

## 3. 常用触发词（分类）

- **搜索**：搜页面、查找文档、关键词检索、标题搜（配合 `--title-model` 等）
- **读取/打开**：打开页面、读取内容、查看页面、总结页面（先读后总结）
- **页面转 Markdown**：转 Markdown、导出 Markdown、本地保存页面、页面落盘
- **保存/发布**：保存到空间、新建页面、发布、子页面、Markdown/HTML
- **转发消息**：发消息、转发给我、转给某人（工号）
- **转发邮件**：发邮件、抄送、密送、邮件主题、附言
- **转发群组**：发到群、项目群、列出群组、模糊匹配群名
- **空间模板**：模板列表、模板内容、用模板新建、按模板建文档
- **页面浏览数据**：谁浏览过这篇、页面浏览记录、浏览宏统计（与「我浏览的页面」个人列表不同，针对**当前页面**）

## 4. 意图识别与命令路由（核心）

### 4.1 路由优先级（一句话多意图时）

1. **保存/创建页面**（“保存到空间、新建页面、发布到某目录下”且带正文或明确要落库）→ `studio_space_page_save.py`
2. **空间模板**（“模板列表、查看模板、按模板创建文档”）→ `space_template_page.py`
3. **页面转 Markdown（本地）**（“转 Markdown、导出 md、保存到本地文件”）→ `space_page_tomd.py`
4. **转发邮件**（出现“邮件、邮箱、抄送、密送”或 `--to-email` 语义）→ `forward_page_to_msg.py --to-email ...`
5. **转发群组**（出现“群、群组、发到群”且非邮件）→ `forward_page_to_group.py`
6. **转发消息**（“转发、发消息、分享页面”）→ `forward_page_to_msg.py`（无 `--to-email`）
7. **读取/总结**（“打开、读取、总结、讲了什么”）→ `fetch_space_page.py`（总结在对话层）
8. **页面浏览记录（单页）**（“这篇谁看过、页面浏览情况、浏览宏”）→ `space_get_macro_data.py`
9. **搜索**（“搜、查找、关键词”且无明确 URL）→ `space_page_search.py`

若用户同时说“先读后转发”，按顺序执行：先 `fetch_space_page`，再 `forward_page_to_*`。

### 4.2 意图 -> 命令决策表

| 用户意图 | 必要参数 | 可选参数 | 首选命令 | 说明 |
|---------|----------|----------|----------|------|
| 关键词搜索 | 关键词 | `space-id` / `page-size` / `page-num` / `title-model` | `space_page_search.py` | 默认分页见脚本帮助 |
| 读取页面 | `url` 或 `spaceId+pageId` | — | `fetch_space_page.py` | URL 优先解析 |
| 页面转 Markdown（本地） | `url` 或 `spaceId+pageId` | `output-dir` | `space_page_tomd.py` | 文件名默认页面标题；首行写页面链接 |
| 创建页面 | `spaceId` + `parentId` + `title` + `content` | `force-html-convert` / `content-html` | `studio_space_page_save.py --action create` | 默认 Markdown→HTML |
| 模板列表 | `spaceId` | — | `space_template_page.py list` | 文档空间模板 |
| 模板内容 | `spaceId` + `templateId` | `data-type` | `space_template_page.py get` | 默认 `--data-type HTML` |
| 按模板创建 | `spaceId` + `parentId` + `templateId` | `title`、`parent-url` | `space_template_page.py create` | 可不传 `--title`（沿用模板标题） |
| 转发消息 | `url` | `to-emp-no` | `forward_page_to_msg.py` | 缺省可发给自己（见脚本） |
| 转发邮件 | `url` + 收件人 | `cc`/`bcc`/`email-title`/`postscript`/`no-postscript` | `forward_page_to_msg.py --to-email` | 邮件模式**必须**有收件人 |
| 转发群组 | `url` + 群名或匹配串 | `--list-groups` | `forward_page_to_group.py` | 多候选时返回 `need_choice` |
| 内容总结 | 同读取 | — | 先 `fetch_space_page.py` | 再由对话层生成总结 |
| 页面浏览宏数据 | `url` 或 `spaceId+pageId` | `limit`/`offset`、`level`、`scope`、`content-title` | `space_get_macro_data.py` | 对应接口文档 #58；返回 `macro_html` 表格，可用 `--json-output` |

### 4.3 参数提取规则

- **URL 优先**：凡有 `i.zte.com.cn` 页面链接，优先解析出 `spaceId/pageId` 或直接用 `--url`。
- **搜索词**：从用户话中提取引号内或“搜/查找”后的短语作为关键词；勿照抄文档示例词。
- **保存落点**：需要 `space-id` 与 `parent-id`；未指定时可使用总 skill 中的默认空间/默认父页面（仅当用户同意或未指定时）。
- **页面转 Markdown**：优先使用 `--url`；未给 URL 时要求 `space-id` + `page-id`。本地文件使用页面标题命名，且文件开头保留页面完整链接，便于后续回写。
- **空间模板**：对应 `all/接口.md` 内容管理子领域 **#8 模板列表、#9 模板内容、#10 基于模版创建文档**（路径前缀 `/studio-ispace/doc`）；脚本走 `icenterapi` 的 `zte-rdcloud-space-content` 路径。
- **页面浏览宏**：对应 `all/接口.md` 内容管理（1.0）**#58 页面浏览宏数据**；脚本请求 `zte-rd-icenter-contents/macros/execute?spaceId=&mode=execute`（可用环境变量 `SPACE_MACRO_EXECUTE_URL` 覆盖），宏 `com.zte.wiki.macro.PageViewsMacro`。默认会先调标题接口补全 `contentTitle`，遇 `0001` 会对 `query.system` 做有限次重试；`--no-fetch-title` / `--system-query` 可关闭或固定该行为。
- **邮件**：收件人支持工号列表或姓名（姓名可能重名 → 返回候选后改用工号）。
- **邮件附言**：若脚本返回 `need_postscript_prompt`，用户确认后再加 `--postscript` 或 `--no-postscript`。

### 4.4 澄清问题模板（缺参时必须询问）

- 读取/转发但无 URL 且无 ID：  
  “请提供页面链接，或提供 spaceId 与 pageId。”
- 搜索但无关键词：  
  “请提供要搜索的关键词或标题片段。”
- 保存但无父页面：  
  “要保存到哪个空间、哪一级目录下？可提供父页面 URL 或 parentId。”
- 页面转 Markdown 但无 URL 且无 ID：  
  “请提供页面链接，或提供 spaceId 与 pageId。”
- 邮件转发无收件人：  
  “请提供收件人工号（或姓名，可能需确认重名）。”
- 转发群组无群名：  
  “要发到哪个群？可说群名关键词；也可先 `--list-groups` 再选。”

## 5. 命令模板

```bash
# 页面搜索（关键词须替换为用户输入）
python3 scripts/space_page_search.py "<关键词>" --page-size 15 --page-num 1

# 读取页面
python3 scripts/fetch_space_page.py --space-id "<spaceId>" --page-id "<pageId>"

# 页面转 Markdown（按 URL，保存到默认目录）
python3 scripts/space_page_tomd.py --url "<页面链接>"

# 页面转 Markdown（按 space/page，指定输出目录）
python3 scripts/space_page_tomd.py --space-id "<spaceId>" --page-id "<pageId>" --output-dir "<本地目录>"

# 创建页面（Markdown，默认转 HTML）
python3 scripts/studio_space_page_save.py --action create --space-id <spaceId> --parent-id <parentId> --title "标题" --content "Markdown内容"

# 创建页面（内容已为 HTML）
python3 scripts/studio_space_page_save.py --action create --space-id <spaceId> --parent-id <parentId> --title "标题" --content "<p>HTML</p>" --content-html

# 转发到消息
python3 scripts/forward_page_to_msg.py --url "<页面链接>"
python3 scripts/forward_page_to_msg.py --url "<页面链接>" --to-emp-no "<收件人工号>"

# 转发到邮件（须指定收件人）
python3 scripts/forward_page_to_msg.py --url "<页面链接>" --to-email --to-emp-no "<工号1>,<工号2>"

# 列出群组 / 转发到群组
python3 scripts/forward_page_to_group.py --list-groups
python3 scripts/forward_page_to_group.py --url "<页面链接>" --group-name "群组名称"

# 空间模板：列表
python3 scripts/space_template_page.py list --space-id "<spaceId>"

# 空间模板：查看模板正文（templateId 来自 list 的 id）
python3 scripts/space_template_page.py get --space-id "<spaceId>" --template-id "<templateId>" --data-type HTML

# 空间模板：基于模板创建文档（可改用 --parent-url 父页面链接）
python3 scripts/space_template_page.py create --space-id "<spaceId>" --parent-id "<parentId>" --template-id "<templateId>" --title "新文档标题"

# 机器可读 JSON（list/get/create 均支持）
python3 scripts/space_template_page.py list --space-id "<spaceId>" --json-output

# 页面浏览记录（页面浏览宏，分页与接口文档 macroParameters.pagination 一致）
python3 scripts/space_get_macro_data.py --url "<页面链接>"
python3 scripts/space_get_macro_data.py --space-id "<spaceId>" --page-id "<pageId>" --limit 20 --offset 0 --json-output
```

### 5.1 Windows PowerShell 等价写法（推荐）

```powershell
# 设置认证上下文（仅当前终端生效）
$env:coclaw_empno='0668xxxxxx'
$env:coclaw_token='***'
$env:PYTHONUTF8='1'

# 搜索页面（标题匹配，可直接使用 --title-model 开关）
py -3 scripts/space_page_search.py '某规划同事' --title-model

# 搜索页面（按日期范围 + 分页）
py -3 scripts/space_page_search.py '需求' --create-by '0668xxxxxx' --start-date '2026-04-01' --end-date '2026-04-14' --page-size 50 --page-num 1

# 读取页面
py -3 scripts/fetch_space_page.py --space-id 'spaceId' --page-id 'pageId'

# 转 Markdown 并保存本地
py -3 scripts/space_page_tomd.py --url 'https://i.zte.com.cn/#/shared/<spaceId>/wiki/page/<pageId>/view'
```

### 5.2 月度评审任务执行建议（防单命令失败）

- 对“最近一个月评审”任务，固定采用**分步执行**：`space_page_search.py` → `fetch_space_page.py`（逐页）→ 评审内容生成 → `studio_space_page_save.py` 落库。
- 页面名建议使用：`某规划同事数字分身-需求PB评审-<yyyyMMddHHmmss>`。
- 如果目标是把结果保存到指定父页面目录，优先显式提供 `--space-id` 与 `--parent-id`，不要依赖隐式上下文。
- PowerShell 中禁止拼接 `cd /c/... && ...`；应使用 `Set-Location ...; py -3 ...` 或直接用脚本绝对路径单条执行。

## 6. 约束与澄清

- 对 `i.zte.com.cn` 页面禁止使用 web 抓取类工具；一律走本仓库脚本与认证环境变量。
- 总结不是独立 CLI：必须先取回正文再总结，避免“空读摘要”。
- 模板能力与普通「空白页+正文」创建不同：走 `space_template_page.py`，勿与 `studio_space_page_save.py --action create` 混用。
- 运行环境与 UTF-8：见总 skill **第 9 节「运行环境与字符集」**（`studio-space-manager/SKILL.md`）；长文输出建议在 UTF-8 终端下执行。
