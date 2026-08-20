---
name: studio-space-manager
description: >-
  iCenter/ispace/Wiki 空间与页面统一技能入口（i.zte.com.cn 链接）：按意图路由子技能并用 Python 脚本执行。
  渐进式披露：主技能负责意图识别、参数提取、路由决策与默认参数规则；具体 CLI、错误处理与澄清模板在各子技能中维护，按需展开。
  空间：搜索/创建/重命名、我管理与个人空间、成员增删、管理员、选人桥查工号姓名；
  页面内容：关键词搜索、读取正文与先读后总结、页面转 Markdown 保存本地（`space_page_tomd.py`）、新建保存 Markdown 或 HTML、**文档空间模板**（列表/模板内容/基于模板新建文档，`space_template_page.py`）、转发到消息/邮件/群组、**单页页面浏览宏数据**（`space_get_macro_data.py`，接口文档 #58）；
  协作：评论与回复、@我、收藏列表与关键字翻页、我编辑/我浏览记录（支持日期/时段/区间筛选）、点赞查询与切换点赞（取消/补赞）、**页面统计数据**（阅读/评论/转发/点赞次数，时间范围内，`get_page_stat.py`，与热点页同源检索接口）、页面标签查询/贴标签/删标签/按标签搜索同标签页面；
  操作：页面复制（`--level 1` 时含当前页下**所有层级**子页面，即整棵子树）、更新标题、**页面正文编辑**（整页/章节/表格单元格，`studio_space_page_edit.py`，见 `subskills/page-edit/SKILL.md`）、子页面列表、空间最新页面、附件下载/上传（上传单文件 <=100MB）、**图片上传并插入页面**（将本地图片上传到空间并自动插入页面正文）。
  禁止对 iCenter 页面使用 web 抓取类工具。
version: 4.0.8
generator: co-skill-creator
compatibility: Requires Python 3.x（建议 3.7+，与标准流 reconfigure 一致）。UTF-8 文本；支持 Linux / Windows / macOS。
author: platform
tags: ["研发提效"]
triggers: ["iCenter", "Wiki", "空间管理", "页面编辑", "ispace"]
---
# Space 空间管理助手（总入口）

## 1. 设计目标

- 本技能作为统一入口，负责识别意图、参数提取和路由决策。
- 具体命令、错误处理、边界细节在子技能中维护，避免主文档膨胀。
- 采用渐进式披露：先确定「做什么」，再展开「怎么做」。

### 1.1 运行环境与编码（UTF-8 / Linux / Windows）

- **字符集**：源码与本地文本读写统一为 **UTF-8**；从页面导出的 Markdown 等若带 BOM，各脚本在读取时会按需剥离 `\ufeff`。
- **标准流**：入口脚本通过 `_platform_utils.configure_stdio_utf8()` 将 **stdin / stdout / stderr** 尽量设为 UTF-8，减轻 **Windows** 控制台默认代码页下中文、符号或 emoji 的乱码与 `UnicodeEncodeError`；**Linux** 下通常已为 UTF-8，调用为幂等。
- **跨平台**：**Linux、Windows** 为一等支持路径（User-Agent、`sec-ch-ua-platform`、日志目录权限等与平台对齐）；macOS 一般可沿用 Linux 路径。
- **Windows 仍异常时**：在启动解释器**之前**设置环境变量 `PYTHONUTF8=1`，或在 CMD 中先执行 `chcp 65001` 再运行脚本。

### 1.2 iCenter 接口环境切换（统一基地址）

- iCenter API 基础地址统一定义在 `scripts/system_param.py`：
  - 产线：`https://icenterapi.zte.com.cn`
  - 测试：`https://icenter-systest.test.zte.com.cn`
- 通过环境变量 `ICENTER_ENV` 切换：
  - `ICENTER_ENV=prod` 使用产线（默认）
  - `ICENTER_ENV=test` 使用测试环境
- 脚本内应通过 `system_param.py` 的 `ICENTER_API_BASE` 或 `build_icenter_api_url()` 拼接请求地址，避免硬编码基础域名。

## 2. 强制规则

- 凡涉及 iCenter 空间 / Wiki / 页面 / 文档操作，优先使用本技能。
- 凡包含 `i.zte.com.cn`、`ispace`、`space` 且指向空间/页面的链接，必须走本技能体系。
- 对上述 iCenter 链接，禁止使用 `web-search`、`web_fetch` 等外部抓取方式。
- 仅在**纯概念问答**且不依赖具体页面内容时，可不进入本技能执行链。
- **禁止**在调用本技能体系内任意脚本（含子技能文档中的示例命令）时，自行在 shell 命令中追加 `2>&1`、`&>`、`> ... 2>&1` 等会把 **stderr 合并进 stdout**（或与 stdout 写入同一流）的重定向。本技能脚本约定：**业务结果走 stdout**（如 `--json-output`）、**日志与提示走 stderr**；合并后会破坏 JSON/结构化解析与排障。若需保存输出或日志，应使用脚本自身提供的参数或约定方式，而不是改 shell 重定向。

## 3. 子技能目录

| 子技能 | 定位 |
|--------|------|
| `subskills/space-admin/SKILL.md` | 空间管理：空间、成员、管理员、选人桥 |
| `subskills/page-content/SKILL.md` | 页面内容：搜索、读取、页面转 Markdown、本地保存、**空间模板**（列表/详情/按模板创建）、转发 |
| `subskills/page-collab/SKILL.md` | 页面协作：评论、@我、收藏、浏览/编辑记录、点赞查询与切换、**页面统计数据**（阅读/评论/转发/点赞）、页面标签 |
| `subskills/page-ops/SKILL.md` | 页面操作：复制、标题、子页面、最新页面、附件下载 |
| `subskills/page-edit/SKILL.md` | **页面正文编辑**：整页/章节/表格单元格（`studio_space_page_edit.py`）；路由常与 `page-ops` 同条命中 |

## 4. 路由规则（总 skill 负责）

1. 命中「成员/管理员/选人桥/空间创建改名」 -> 路由 `space-admin`
2. 命中「搜索页面/读取页面/页面转 Markdown（本地）/保存页面/**空间模板（列表、查看模板、按模板新建文档）**/转发页面/**指定页面的页面浏览记录（页面浏览宏）**」 -> 路由 `page-content`
3. 命中「评论/@我/收藏/编辑记录/浏览记录/点赞（含取消点赞、切换点赞）/页面标签/页面统计（阅读次数、评论次数、转发次数、点赞次数及时间范围）」 -> 路由 `page-collab`
4. 命中「复制/拷贝/标题更新/**编辑正文/回写页面/更新章节/改表格单元格**/子页面/最新页面/附件下载/**图片上传并插入页面**」 -> 路由 `page-ops`（正文编辑细则展开 `subskills/page-edit/SKILL.md`）
5. 同时命中多类意图时按用户主目标优先；不明确时先澄清再执行。

## 5. 默认参数与上下文规则

- 页面链接优先：优先从 URL 解析 `spaceId/pageId`，其次使用显式参数。
- 内容总结无独立脚本：先读取页面正文，再由对话层总结。
- **空间模板**：与「空白页 + 自填正文」不同；须走 `scripts/space_template_page.py`（`list` / `get` / `create`），**不要**用 `studio_space_page_save.py --action create` 代替。需 `spaceId`；按模板创建时还需 `parentId`（或父页面 URL）与 `templateId`（来自列表接口的 `id`）；`create` 时 `title` 可选（不传则沿用模板标题）。细则与命令模板见 `subskills/page-content/SKILL.md`。

## 6. 执行框架（统一流程）

1. 意图识别（路由到子技能）
2. 参数提取（URL、spaceId、pageId、日期、分页等）
3. 参数补全（默认空间/默认父页面）
4. 调用子技能对应脚本
5. 结构化回显（结果 + 可继续操作建议）

## 7. 子技能调用示例（单意图）

- 「把张三加到测试空间」 -> `space-admin`
- 「读取这个页面并总结」 -> `page-content`（读取）+ 对话层总结
- 「列出当前文档空间的模板 / 看这个模板内容 / 用这个模板在某目录下新建一篇」 -> `page-content`（`space_template_page.py`）
- 「看下我收藏的下一页」 -> `page-collab`
- 「把这个页面复制到当前目录并下载附件」 -> `page-ops`
- 「把本地 md 回写到这篇页面 / 更新某一节 / 改表格里某格」 -> `page-ops`（`studio_space_page_edit.py`，见 `subskills/page-edit/SKILL.md`）

## 8. 组合意图示例（多子 skill / 多步）

组合场景按**时间顺序**拆步：每一步对应一个子技能里的脚本调用；前一步的输出（工号、URL、`space_id`/`page_id` 等）作为后一步输入。

**说明**：单句「把页面转发给某人」若已提供**收件人工号**，或转发脚本已能仅凭姓名完成解析且无歧义，通常**只走 `page-content`** 即可。下面重点写**需要两个子 skill**或**明显两段能力**的常见组合。

### 8.1 先查人、再转发页面（`space-admin` → `page-content`）

- **用户说法**：「把这个页面用消息转发给张三」（未给工号，或需确认重名）
- **步骤 1 — `space-admin`**：`space_member_setting.py --employee "张三"`（或等价关键词）得到**确定工号**
- **步骤 2 — `page-content`**：`forward_page_to_msg.py --url "<页面链接>" --to-emp-no "<工号>"`

若用户已说「发给 0668xxxxxx」，则**不要**再查人，直接 `page-content` 一步。

### 8.2 先搜页面、再转发（`page-content` → `page-content`）

- **用户说法**：「搜一下『项目周报』，把找到的第一条转发给李四」
- **步骤 1**：`space_page_search.py` 得到目标页面 URL（或 `spaceId`/`pageId`）
- **步骤 2**：若缺李四工号，接 **8.1** 的选人桥；否则 `forward_page_to_msg.py` 转发

### 8.3 收藏列表定位 + 转发（`page-collab` → `page-content`）

- **用户说法**：「把我收藏列表里第 3 条转发给王五」
- **步骤 1 — `page-collab`**：基于上一轮收藏列表取第 3 条的 `space_id`/`page_id`（必要时拼页面 URL 或先 `fetch_space_page` 确认）
- **步骤 2 — `page-content`**：转发到消息/邮件；收件人解析同 **8.1**

### 8.4 空间成员操作 + 页面转发（`space-admin` → `page-content`）

- **用户说法**：「把赵六加进 XX 空间，并把当前这篇页面发给他」
- **步骤 1 — `space-admin`**：按空间关键词搜索 → `--add-member <spaceId> <工号>`
- **步骤 2 — `page-content`**：`forward_page_to_msg.py` 将当前页发给赵六（工号与上步一致或再次确认）

### 8.5 复制/标题/附件在同一任务（`page-ops` → `page-ops`）

- **用户说法**：「先复制到测试空间主页，再下载这个页面的附件」
- **步骤 1**：`copy_page.py`（得到新页面 URL 或明确仍对**源页**下载）
- **步骤 2**：`studio_space_attachment.py`（**与用户确认**下载源页还是新复制页对应的 URL）

### 8.6 @我 / 浏览记录里捞链接 + 下载或转发（`page-collab` → `page-ops` 或 `page-content`）

- **用户说法**：「上周 @ 我里提到的那篇，帮我把附件下下来」
- **步骤 1 — `page-collab`**：`space_at_me.py` 等在结果中定位页面链接或 `space_id`/`page_id`
- **步骤 2 — `page-ops`**：`studio_space_attachment.py --url ...`（或等价参数）

若用户改为「把这篇转发给组长」，则步骤 2 改为 **`page-content`** 转发，逻辑同上。

### 8.7 空间模板：先列模板再按模板建文档（`page-content` → `page-content`）

- **用户说法**：「看下我们空间有哪些文档模板，用第一个在某父页面下新建一篇」
- **步骤 1 — `page-content`**：`space_template_page.py list --space-id "<spaceId>" --json-output`（或人机可读输出）得到 `templateId`
- **步骤 2 — `page-content`**：`space_template_page.py create --space-id "<spaceId>" --parent-id "<parentId>" --template-id "<templateId>"`（可选 `--title`；也可用 `--parent-url` 解析父节点）

接口与 CLI 细节见 `subskills/page-content/SKILL.md`。

## 9. 运行环境与字符集（Python / UTF-8 / Windows）

- **工作目录**：子技能中的示例形如 `python3 scripts/xxx.py`，默认在技能根目录 `studio-space-manager/` 下执行（即当前目录下可见 `scripts/` 文件夹）。
- **解释器名称**：文档统一写 `python3`。在 Windows 上若未安装 `python3` 命令，可使用 `py -3`、`python` 等本机已配置的 Python 3 启动方式，效果相同。
- **UTF-8 输出**：业务脚本在 CLI 入口会尽量将标准输出/标准错误设为 UTF-8（见 `scripts/_platform_utils.py` 中 `configure_stdio_utf8`），避免在 Windows 默认非 UTF-8 控制台下打印中文、emoji 等时触发编码异常。若仍出现乱码，可在运行前将终端设为 UTF-8（例如 Windows Terminal 中选用 UTF-8 代码页，或设置环境变量 `PYTHONUTF8=1`）。
- **源文件编码**：脚本与 SKILL 均按 UTF-8；HTTP 请求头中 JSON 相关字段使用 `charset=UTF-8` 等与接口约定一致。

## 10. Windows PowerShell 执行规范（防失败）

以下规则用于避免在 Windows PowerShell 下出现“命令被截断/参数丢失/编码异常”：

1. **禁止把自然语言任务文本当命令执行**  
   像 `#任务说明# ... #执行步骤# ...` 这类内容应作为对话输入，不应直接粘贴到 PowerShell 命令行执行。`#` 在 PowerShell 中是注释起始符，未加引号时后续内容会被忽略。

2. **解释器优先级（按环境可用性）**  
   - 首选：`py -3 scripts/<xxx>.py ...`  
   - 其次：`python scripts/<xxx>.py ...`  
   - 兼容：`python3 scripts/<xxx>.py ...`

2.1 **PowerShell 命令连接符约束（必须）**  
   - 禁止使用 `&&`（在部分 PowerShell 环境会触发 `InvalidEndOfLine`）。  
   - 单命令执行脚本时，不要写 `cd /c/... && ...` 这类 Linux/Git-Bash 风格命令。  
   - 需要切目录时使用 `;`，例如：  
     `Set-Location 'C:\\Users\\xx\\.openclaw\\workspace'; py -3 '...\\space_page_search.py' '需求'`

3. **参数传递规则**  
   - 带空格、`#`、中文、URL 的参数必须加引号。  
   - 推荐在 PowerShell 使用单引号包裹 URL 与长文本，例如：  
     `py -3 scripts/fetch_space_page.py --space-id 'xxx' --page-id 'yyy'`
   - 布尔参数 `--title-model` 既可写成开关，也可显式写值：  
     `--title-model` / `--title-model false`

4. **环境变量设置（PowerShell）**  
   - `$env:coclaw_empno='0668xxxxxx'`  
   - `$env:coclaw_token='your_token'`  
   - 可选：`$env:PYTHONUTF8='1'`

5. **复杂任务执行方式**  
   大任务（如“一个月评审”）必须拆成多次脚本调用：先搜索、再逐页读取、再生成评审内容并保存页面。不要尝试把整段任务说明塞进单条 shell 命令。

5.1 **评审场景推荐搜索命令（Windows）**  
   `space_page_search.py` 支持日期与分页参数，推荐：
   - `py -3 scripts/space_page_search.py '需求' --create-by 0668xxxxxx --start-date 2026-04-01 --end-date 2026-04-14 --page-size 50 --page-num 1`
   - 关键词为必填参数，请明确传入检索词。

6. **iCenter 页面写入工具约束**  
   对 iCenter 页面的新建/更新，优先使用本技能脚本 `scripts/studio_space_page_save.py`（或模板脚本），不要切到浏览器自动化类技能去“模拟点击保存”，以避免在 PowerShell/跨环境下引入额外不确定性。

