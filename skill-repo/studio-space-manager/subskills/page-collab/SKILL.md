---
name: studio-space-page-collab
description: >-
  iCenter 页面协作子技能：评论列表、发布根评论、回复子评论；@我按日/周/月/年或日期区间筛选；
  加入收藏、收藏列表关键字与翻页、按列表上下文读取第 N 条页面正文；我编辑与我浏览记录（支持 --date 单日/时段/区间筛选）；
  点赞总数、是否已赞、点赞人员分页查询；切换点赞（已赞则取消、未赞则点赞，脚本内先查 detail 再 POST/PUT flower）；
  页面统计数据（时间范围内阅读次数、评论次数、转发次数、点赞次数，`get_page_stat.py`，与 `studio-space-hotpage` 同源统计检索接口，脚本内自实现）；
  页面标签查询、贴标签（重复标签先查列表，已存在则提示不重复提交）、按 tagId 或标签名删除（文档空间加 --doc-space）；
  按标签汇聚搜索同标签页面（接口「标签汇聚」tagConvergence，--action search）。
version: 1.4.2
---
# 页面协作子技能

## 1. 适用范围

- 页面评论：列表、发布根评论、回复子评论
- @我的页面：按日/周/月/年/区间筛选
- 收藏：加入收藏、列表、关键字筛选、翻页
- 从收藏列表按序号读取某条页面正文
- 我编辑的页面、我浏览的页面
- 点赞：总数、是否已赞、点赞人员分页；切换点赞（取消/补赞）
- **页面统计**：在指定时间范围内查询单页的**阅读次数**（浏览聚合）、**评论次数**（operate_code:9）、**转发次数**（operate_code:12/13）、**点赞次数**（operate_code:32）；接口与 `studio-space-hotpage/scripts/space_hotpage.py` 中 `visitrecord-statistics` / `operatelog-statistics` 一致，由 `get_page_stat.py` 独立实现（不 import 热点脚本）
- 页面标签：查询列表、新增（去重）、删除（`--tag-id` 或 `--name`）；按标签搜索同标签页面（`--action search`）

### 1.1 调用禁令（与总入口 `SKILL.md` 同步）

- **禁止**在调用本子技能所列脚本（含下文示例命令）时，自行在 shell 命令中追加 `2>&1`、`&>`、`> ... 2>&1` 等会把 **stderr 合并进 stdout**（或与 stdout 写入同一流）的重定向。约定：**业务结果走 stdout**（如 `--json-output`）、**日志与提示走 stderr**；合并后会破坏 JSON/结构化解析与排障。若需保存输出或日志，应使用脚本自身提供的参数或约定方式，而不是改 shell 重定向。

## 2. 脚本映射

- `scripts/studio_space_comment.py`
- `scripts/space_at_me.py`
- `scripts/favorite_page.py`
- `scripts/fetch_space_page.py`（从收藏序号读取时用）
- `scripts/space_edited_pages.py`
- `scripts/space_visited_pages.py`
- `scripts/get_page_flower.py`
- `scripts/get_page_stat.py`（单页阅读/评论/转发/点赞次数，需 `--date-range` 或默认今天；无网单元测试：`scripts/test_get_page_stat.py`）
- `scripts/space_page_label.py`

## 3. 常用触发词（分类）

- **评论**：评论、回复、第几条评论、盖楼、留言
- **@我**：@我、AT我、今天@我、本周@我、区间日期
- **收藏**：收藏、取消（若脚本支持则查帮助）、我收藏的、搜收藏、下一页
- **从收藏读**：第 N 条、打开第几条、这条
- **编辑/浏览记录**：我编辑的、我改过的、我浏览的、最近看过
- **点赞**：点赞数、谁赞过、我有没有赞；点个赞、取消点赞、切换点赞、帮我赞一下
- **页面统计**：阅读量、浏览量、阅读次数、评论数、转发数、点赞数、页面热度、统计本周/本月（与 `--date-range` 语义一致）
- **标签**：页面标签、打标签、贴标签、加标签、删标签、去掉标签、查标签、同标签页面、按标签找页面、标签汇聚

## 4. 意图识别与命令路由（核心）

### 4.1 路由优先级（一句话多意图时）

1. **从收藏读第 N 条**（“第 N 条/打开这条”且**上一轮为收藏列表**）→ `fetch_space_page.py` + 历史 `space_id/page_id`
2. **收藏翻页**（“下一页/再看一页”且上一轮收藏列表 `has_more`）→ `favorite_page.py --list --page <next>`（继承 `--keyword`）
3. **收藏关键字列表**（“在我收藏里搜 XX”）→ `favorite_page.py --list --keyword ...`
4. **加入收藏**（“收藏这个页面”）→ `favorite_page.py --url ...`
5. **@我**（时间语义）→ `space_at_me.py --date ...`
6. **评论**（列表/发/回）→ `studio_space_comment.py`
7. **点赞** → `get_page_flower.py`（仅查询明细不加写参数；**点赞** → `--like`；**取消点赞** → `--unlike`）
8. **页面统计（阅读/评论/转发/点赞次数，时间范围）** → `get_page_stat.py`（默认统计「今天」；与热点页同源接口，按单页 `content_id` 过滤）
9. **页面标签** → `space_page_label.py`（`list` / `add --name` / `remove --tag-id` 或 `remove --name` / `search`；文档空间加 `--doc-space`）
10. **我编辑/我浏览** → `space_edited_pages.py` / `space_visited_pages.py`

### 4.2 意图 -> 命令决策表

| 用户意图 | 必要参数 | 可选参数 | 首选命令 | 说明 |
|---------|----------|----------|----------|------|
| 评论列表 | `spaceId+pageId` 或 `url` | `limit`/`offset` | `studio_space_comment.py --action list` | 可用 `--url` 解析 |
| 发评论 | 页面定位 + 评论正文 | — | `--action publish` | |
| 回复评论 | 页面定位 + `parentCommentId` + 正文 | — | `--action reply` | 需父评论 ID |
| @我 | 日期或时段 | — | `space_at_me.py --date <值>` | 支持 today/本周/区间等 |
| 加入收藏 | 页面 `url` | — | `favorite_page.py --url` | |
| 收藏列表 | 无或分页 | `page`/`rows`/`keyword` | `favorite_page.py --list` | 关键字与翻页可组合 |
| 读收藏第 N 条 | 列表上下文中的 `space_id`/`page_id` | — | `fetch_space_page.py` | 须先 list |
| 我编辑的 | 无 | `space-id`/`page-size`/`--date`/`--json` | `space_edited_pages.py` | `--date` 支持今天/昨天/前天/本周/上周/本月/今年/区间 |
| 我浏览的 | 无 | `--start`/`--count`/`--date`/`--json` | `space_visited_pages.py` | count 最大 100；`--date` 同上 |
| 页面统计（阅读/评论/转发/点赞） | `spaceId+pageId` 或 `url` | `--date-range`（默认今天；同 `space_hotpage`：本周/上月/区间等）/`--json-output`/`--debug` | `get_page_stat.py` | 检索侧聚合；`user_id` 用环境变量 `coclaw_empno`（与热点统计一致） |
| 点赞查询 | `spaceId+pageId` 或 `url` | `offset`/`limit`/`--json-output` | `get_page_flower.py` | 人员列表可分页 |
| 点赞 | `spaceId+pageId` 或 `url` | `--like`/`--json-output` | `get_page_flower.py --like` | 未赞则 POST；已赞则提示并保持已赞 |
| 取消点赞 | `spaceId+pageId` 或 `url` | `--unlike`/`--json-output` | `get_page_flower.py --unlike` | 已赞则 PUT；未赞则提示；见下文 |
| 标签列表 | `spaceId+pageId` 或 `url` | `--json-output` | `space_page_label.py --action list` | 接口：内容管理「获取标签」 |
| 贴标签 | 页面定位 | `--name` | `space_page_label.py --action add` | 先 list 再 POST；同名（忽略英文大小写）则 `skipped`、提示已存在 |
| 删标签 | 页面定位 | `--tag-id` 或 `--name` | `space_page_label.py --action remove` | 按名删会先 list 解析 id；勿同时传 id 与 name |
| 按标签搜页面 | `spaceId` + 标签名（或参考页） | `--offset`/`--limit`、`--tags-from-page` | `space_page_label.py --action search` | 走 advanced `tagConvergence`；多 `--tag` 与接口 body.tags 一致；`--tags-from-page` 用页面当前标签列表作为条件 |

### 4.3 参数提取规则

- **评论定位**：优先页面 URL；否则 `space-id` + `content-id`（即 pageId）。
- **回复**：从“回复第 N 条”需先 `list` 拿到评论 ID，再 `reply`。
- **@我 日期**：映射到 `--date`（today、本周、`YYYY-MM-DD`、`YYYY-MM-DD~YYYY-MM-DD` 等，以脚本为准）。
- **我编辑/我浏览 日期**：同样映射到 `--date`，支持单日（今天/昨天/前天/具体日期）、时段（本周/上周/本月/今年）和区间（`start~end`、`start 到 end`）。
- **收藏翻页**：必须带上一轮的 `next_page`；若上一轮有 `--keyword`，本轮**必须保留**同一关键字。
- **收藏第 N 条**：从列表返回中取第 N 条记录的 `space_id` 与 `page_id`；若为空提示可能已删除。
- **页面统计**：必须能定位页面（URL 或 `spaceId+pageId`）；**默认日期为今天**（北京时间）；`--date-range` 与 `studio-space-hotpage` 的语义一致（如 `本周`、`20260101-20260131`）。输出 JSON 时 `statistics` 含 `view_count`（阅读）、`comment_count`、`forward_count`、`like_count`。
- **点赞分页**：先看总数与是否已赞，再按需 `--offset/--limit` 拉取人员明细。
- **点赞/取消点赞（`--like` / `--unlike`）**：脚本先 `GET .../flower/detail`（`limit=1` 即可），根据 `bo.isFlower` 与 `rows` 中是否含当前用户 `employeeShortId` 判断是否已赞；再按需调用 `.../content/{pageId}/flower/`（query 含 `t`、`spaceId`、`environmentType`、`system`）：**`--like` 且未赞则 `POST`（空 body）；已赞则不调用接口，提示已点过赞**；**`--unlike` 且已赞则 `PUT`（空 body）；未赞则不调用接口，提示尚未点赞**。**不向用户复述工号/token**；缺页面链接或 `spaceId+pageId` 时按 4.4 澄清。
- **页面标签**：文档空间加 `--doc-space` 走 `zte-rdcloud-space-social/content/{pageId}/tags`，新增 body 含 `type: "0000"`。**贴标签前脚本会先 GET 当前标签**；若与已有标签名等价（去首尾空格、英文 `casefold`）则不调 POST，提示「标签已存在」。删除时优先 `--tag-id`；仅 `--name` 时需先 list 且条目含 `id`。**按标签搜页面**：`POST zte-rd-icenter-advanced/tagConvergence`，query 含 `offset`、`limit`、`spaceId`（及与列表接口一致的 `t`/`environmentType`/`system`），body 为 `{"spaceIds":[...],"tags":[...]}`（与对外文档「标签汇聚」一致）；`--tags-from-page` 时先 GET 页面标签再汇聚。

### 4.4 澄清问题模板（缺参时必须询问）

- 评论/点赞但无页面：  
  “请提供页面链接，或 spaceId + pageId。”
- 页面统计但时间范围不明：  
  未说明时可使用默认「今天」，或向用户确认是否统计「本周」「上月」等再传 `--date-range`。
- 回复评论但不知 parentId：  
  “我先拉取评论列表，请确认要回复的是哪一条（或提供评论 ID）。”
- @我但时间不清：  
  “要查哪一天/哪一周/哪个区间？”
- 翻页但无上下文：  
  “请先查询收藏列表，或说明要延续上一轮哪次查询（是否带关键字）。”
- 读收藏第 N 条但无列表：  
  “请先执行收藏列表查询，或提供目标页面链接。”
- 贴标签但无名称：  
  “要为页面打哪个标签（标签文本）？”
- 删标签但无 id/名称：  
  “请先执行标签列表查询以取得 tag id，或说明要删除的标签名称。”
- 按标签搜页面但无标签名且无参考页：  
  “请提供至少一个 `--tag`，或使用 `--tags-from-page` 并给出页面 URL（或 spaceId+pageId）。”

## 5. 命令模板

```bash
# 评论：列表 / 发布 / 回复
python3 scripts/studio_space_comment.py --action list --space-id <spaceId> --content-id <pageId>
python3 scripts/studio_space_comment.py --action publish --space-id <spaceId> --content-id <pageId> --comment "评论内容"
python3 scripts/studio_space_comment.py --action reply --space-id <spaceId> --content-id <pageId> --parent-id <parentCommentId> --comment "回复内容"

# @我
python3 scripts/space_at_me.py --date today
python3 scripts/space_at_me.py --date 本周

# 收藏：加入 / 列表 / 关键字 / 翻页（next_page、keyword 按上下文继承）
python3 scripts/favorite_page.py --url "<空间页面链接>"
python3 scripts/favorite_page.py --list --page 1 --rows 30
python3 scripts/favorite_page.py --list --keyword "项目" --page 2

# 从收藏列表读取第 N 条（space_id/page_id 来自列表返回）
python3 scripts/fetch_space_page.py --space-id "<spaceId>" --page-id "<pageId>"

# 我编辑 / 我浏览（支持 --date：单日/时段/区间）
python3 scripts/space_edited_pages.py --date 本周 --json
python3 scripts/space_visited_pages.py --start 0 --count 50 --date 2026-03-01~2026-03-10 --json

# 页面统计：阅读/评论/转发/点赞（默认今天；时间范围同热点页）
python3 scripts/get_page_stat.py --url "https://i.zte.com.cn/#/shared/<spaceId>/wiki/page/<pageId>/view"
python3 scripts/get_page_stat.py --space-id "<spaceId>" --page-id "<pageId>" --date-range 本周 --json-output
python3 scripts/get_page_stat.py --space-id "<spaceId>" --page-id "<pageId>" --date-range 20260101-20260131

# 点赞：查询明细（总数、是否已赞、点赞人分页）
python3 scripts/get_page_flower.py --url "https://i.zte.com.cn/#/shared/{spaceId}/wiki/page/{pageId}/view"
python3 scripts/get_page_flower.py --space-id "<spaceId>" --page-id "<pageId>" --offset 0 --limit 20 --json-output

# 点赞 / 取消点赞（程序调用可加 --json-output；已赞再 --like、未赞再 --unlike 时仅提示不调接口）
python3 scripts/get_page_flower.py --space-id "<spaceId>" --page-id "<pageId>" --like
python3 scripts/get_page_flower.py --space-id "<spaceId>" --page-id "<pageId>" --unlike
python3 scripts/get_page_flower.py --url "https://i.zte.com.cn/#/shared/<spaceId>/wiki/page/<pageId>/view" --like --json-output

# 页面标签：查询 / 新增（重复则 skipped）/ 删除（文档空间加 --doc-space）
python3 scripts/space_page_label.py --action list --space-id "<spaceId>" --page-id "<pageId>" --json-output
python3 scripts/space_page_label.py --action add --space-id "<spaceId>" --page-id "<pageId>" --name "标签名"
python3 scripts/space_page_label.py --action remove --space-id "<spaceId>" --page-id "<pageId>" --tag-id "<tagId>"
python3 scripts/space_page_label.py --action remove --space-id "<spaceId>" --page-id "<pageId>" --name "标签名"
python3 scripts/space_page_label.py --action add --url "https://i.zte.com.cn/#/shared/<spaceId>/wiki/page/<pageId>/view" --name "标签名" --doc-space

# 按标签汇聚：同空间内包含指定标签的页面（可多个 --tag；分页 --offset/--limit）
python3 scripts/space_page_label.py --action search --space-id "<spaceId>" --tag "标签A" --tag "标签B" --offset 0 --limit 20 --json-output
python3 scripts/space_page_label.py --action search --url "https://i.zte.com.cn/#/shared/<spaceId>/wiki/page/<pageId>/view" --tag "标签A" --json-output
# 以某页当前全部标签名为条件汇聚（文档空间加 --doc-space）
python3 scripts/space_page_label.py --action search --tags-from-page --url "https://i.zte.com.cn/#/shared/<spaceId>/wiki/page/<pageId>/view" --json-output
```

## 6. 约束与澄清

- **页面统计**为检索侧**按日聚合**结果，依赖统计索引时效；与「当前点赞总人数」等实时接口数值可能不完全一致。
- “查看下一页”必须继承上一轮收藏查询状态（`keyword`、`page`）。
- “读取第 N 条”必须依赖最近一次收藏列表中的 `space_id/page_id`；缺失时提示页面可能已删除。
- 点赞人员较多时分页拉取，避免一次性假设全量 `rows`。
- **点赞/取消点赞**为写操作：执行前应让用户明确目标页面；`--json-output` 成功时含 `intent`（`like`/`unlike`）、`skipped`、`had_flower_before`、`is_flower_after`（跳过时有 `message`）。
- **贴标签**为写操作：重复标签时 `--json-output` 含 `skipped: true` 与 `message`；文档空间必须加 `--doc-space`，否则接口可能失败。
