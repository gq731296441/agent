# 发送消息

## 触发条件

当用户说以下**特定格式**的内容时，**必须立即使用此技能**：

**单聊触发格式**：
- `发送消息给<接收者>：<消息内容>`
- `将<消息内容>发消息给<接收者>`
- `发送消息给<接收者>，内容为：<消息内容>`
- `发送图片给<接收者>：<图片路径>`
- `发送文件给<接收者>：<文件路径>`

**群聊触发格式**：
- `发送消息给群<群组名>：<消息内容>`
- `发送消息给<群组名>群：<消息内容>`
- `将<消息内容>发消息给群<群组名>`
- `发送图片给群<群组名>：<图片路径>`
- `发送文件给群<群组名>：<文件路径>`

**示例触发语句**：

**单聊示例**：
- "发送消息给小张：你好，这是一条测试消息"
- "发送消息给小张，内容是：你好，这是一条测试消息"
- "将这是一条测试消息发给小张"
- "发送图片给小张：/path/to/image.jpg"
- "发送文件给梁其天：/home/user/document.pdf"

**群聊示例**：
- "发送消息给群项目组：明天开会"
- "发消息给群智能会议联调，内容是：请查看会议纪要"
- "发送消息给项目组群：明天下午3点开会"
- "发送消息给项目组群：明天下午3点开会。@所有人 @小张"
- "发送图片给群项目组：/path/to/image.png"
- "发送文件给群项目组：/home/user/report.docx"

**格式说明**：
- 支持中英文冒号（`:` 和 `：`）
- 提问中的`@` 格式要求： `@` 后至少有一个空格分隔用户名和消息内容
- 接收者名称可以是中文名、英文名或工号（单聊）
- 群组名称可以是中文或英文（群聊），可以存在单独的@人或者@所有人。
- 消息类型：
  - **文本消息**：使用 `--msg` 参数，消息内容为文本字符串
  - **图片消息**：通过"发送图片"等关键词触发，使用 `--img` 参数，值为图片文件的绝对路径（只支持 bmp、jpg、png）
  - **文件消息**：通过"发送文件"等关键词触发，使用 `--file` 参数，值为文件的绝对路径

**不触发的情况**：
- "我要发消息"（格式不匹配，没有内容）
- "给张三发个消息"（格式不匹配，没有内容）
- 要求发送文本、图片、文件之外的其他格式消息（如语音、视频等），应提示仅支持文本、图片和文件类型
- 其他不匹配上述格式的请求

## 技能概述

**工具位置**：
- 脚本路径：`{baseDir}/scripts/icenter-msg.py`
- 依赖工具：`{baseDir}/scripts/icenter_msg_utils.py`

其中 `{baseDir}` 是技能安装目录（oa-message_manage 目录的绝对路径）。

**调用方式**：
- 使用 exec 工具调用 Python 脚本，传入参数化命令。例如：`python {baseDir}/scripts/icenter-msg.py` 或 `python3 {baseDir}/scripts/icenter-msg.py`。
- agent 从用户的自然语言描述中提取信息转换为命令参数，通过 Python 解释器执行 icenter-msg.py 并传入相应参数。
- 需确保运行环境已安装 Python（无需第三方依赖，仅使用标准库）。

**功能特点**：
- 支持单聊和群聊消息发送
- 支持三种消息类型：文本（text）、图片（image）、文件（file）
- 自动搜索接收者（用户或群组）、被@的用户，如果找到多个同名用户/群组，返回候选列表供用户选择；如果只找到一个匹配结果，自动选择并直接发送。
- 调用方式：使用参数化命令，agent 从自然语言中提取信息并转换为参数。

**认证与用户信息获取（必须）**：
- 需要当前用户工号和 token，工具会自动从环境变量获取。
- 如果环境变量未设置或不全，工具会返回错误，agent 应提示用户检查环境变量配置，不能继续调用接口。


## 快速调用参考

**调用说明**：

- 通过 Python 解释器执行脚本：`python {baseDir}/scripts/icenter-msg.py` 或 `python3 {baseDir}/scripts/icenter-msg.py`，以下以 `python {baseDir}/scripts/icenter-msg.py` 说明（可据环境选用 python 或 python3）。
- 若提问中消息接收者有姓名没有工号，或者存在 @某人、at某人、"艾特"某人，或者有群名称没有群ID，要先调用搜索用户或搜索群，得到用户工号或群ID 后再调用发送消息。
- 若提问中出现接收者有姓名工号（格式为姓名在前工号在后，工号一般为纯数字部分，例如林明东0668000290），则提取出工号后，可直接调用发送消息。
- **消息类型识别**：
  - 文本消息：使用 `--msg` 参数，值为文本内容
  - 图片消息：使用 `--img` 参数，值为图片文件的绝对路径（只支持 bmp、jpg、png）
  - 文件消息：使用 `--file` 参数，值为文件的绝对路径
  - `--msg`、`--img`、`--file` 三者只能出现一个

**搜索用户格式**：

当提问中有姓名没有工号，或存在 @某人、at某人、"艾特"某人时，先提取出姓名，调用以下命令先根据姓名搜索工号，如果搜索到一个则直接使用并继续调用脚本发送消息；如果搜索到多个用户，需要列出结果让用户选择。
```bash
python {baseDir}/scripts/icenter-msg.py -q --userName <用户名>
```

***搜索用户参数***：
- `-q`：表示搜索
- `--userName`：用户名称（必须，用于查询）

***搜索结果***：
- 找到 0 个：返回错误
- 找到 1 个：自动选择并继续发送
- 找到多个：返回候选列表，等待用户选择
- **重要限制**：若搜索结果中用户 `employeeType` 为 **4**，表示该用户为**数字员工**，**不能给数字员工发起单聊消息**。agent 应提示用户更换为真实员工联系人（`employeeType` ≠ 4）后再发送，或改为发送群聊消息。

   - `action` 为 `select_user`
   - 从 `candidates` 数组中提取候选信息
   - 单聊：显示姓名（中英文）、工号、部门
   - 按序号（1-N）列出供用户选择

```json
{
  "success": false,
  "action": "select_user",
  "message": "找到 2 个名为 '小张' 的用户，请选择具体用户",
  "candidates": [
    {
      "name": "小张",
      "nameEn": "Zhang",
      "empNo": "0668000408",
      "employeeType": 1,
      "deptName": "研发部"
    },
    {
      "name": "小张",
      "nameEn": "Xiao Zhang",
      "empNo": "0668000409",
      "employeeType": 4,
      "deptName": "市场部"
    }
  ]
}
```
***重点注意***：如果搜索到多个用户，务必提示用户选择后再继续，不得默认选用第一个用户。


**搜索群组格式**：

当提问中有群组没有群ID时，先调用以下命令根据群名称搜索群 ID，如果搜索到一个则直接使用并继续调用脚本；如果搜索到多个群组，需要列出结果让用户选择。
```bash
python {baseDir}/scripts/icenter-msg.py -q --groupName <群组名>
```
***搜索群组参数***：
  - `-q`：表示搜索
  - `--groupName`：群组名称（必须，用于查询）

***搜索结果***：
- 找到 0 个：返回错误
- 找到 1 个：自动选择并继续发送
- 找到多个：返回候选列表，等待用户选择。

   - `action` 为 `select_group`
   - 从 `candidates` 数组中提取候选信息   
   - 群聊：显示群组名称、群组URI、管理员工号
   - 按序号（1-N）列出供用户选择
   
```json
{
  "success": false,
  "action": "select_group",
  "message": "找到 2 个名为 '项目组' 的群组，请选择具体群组",
  "candidates": [
    {
      "groupName": "项目组",
      "groupUri": "sip:p1_20250922142134455452@gm.zte.com.cn",
      "adminID": "0668000234"
    },
    {
      "groupName": "项目组",
      "groupUri": "sip:p1_20250812093953562506@gm.zte.com.cn",
      "adminID": "0668000290"
    }
  ]
}
```

***重点注意***：只支持搜索我所在的群组。返回无群组可能群不存在，也可能不是我所在的群组。如果搜索到多个群组，务必提示用户选择后再继续，不得默认选用第一个群组。

**单聊命令格式**：

发送单聊消息，调用以下命令
```bash
python {baseDir}/scripts/icenter-msg.py -s --userId <工号> --userName <用户名> [--msg <消息内容>|--img <图片路径>|--file <文件路径>]
```

***单聊参数***：
- `-s`：表示发送消息
- `--userId`：工号（必须）
- `--userName`：用户名称（可选，用于显示）
- `--msg`：文本消息内容（必须，与 `--img`、`--file` 三者只能出现一个）
- `--img`：图片文件的绝对路径（必须，只支持 bmp、jpg、png，与 `--msg`、`--file` 三者只能出现一个）
- `--file`：文件的绝对路径（必须，与 `--msg`、`--img` 三者只能出现一个）

***单聊示例***：

**文本消息示例**：
提问：发送消息给梁其天：明天开会
命令：先查询梁其天的工号，然后调用单聊发送消息
```bash
python {baseDir}/scripts/icenter-msg.py -q --userName '梁其天'
python {baseDir}/scripts/icenter-msg.py -s --userId 0668000408 --userName '梁其天' --msg '明天开会'
```

**图片消息示例**：
提问：发送图片给小张：/home/user/photo.jpg
命令：先查询小张的工号，然后调用单聊发送图片
```bash
python {baseDir}/scripts/icenter-msg.py -q --userName '小张'
python {baseDir}/scripts/icenter-msg.py -s --userId 0668000408 --userName '小张' --img '/home/user/photo.jpg'
```

**文件消息示例**：
提问：发送文件给梁其天：/home/user/report.pdf
命令：先查询梁其天的工号，然后调用单聊发送文件
```bash
python {baseDir}/scripts/icenter-msg.py -q --userName '梁其天'
python {baseDir}/scripts/icenter-msg.py -s --userId 0668000408 --userName '梁其天' --file '/home/user/report.pdf'
```

***输出格式***：

成功时
```json
{
  "success": true,
  "message": "消息发送成功",
  "type": "single",
  "receiver": "小张",
  "userId": "0668000408"
}
```

失败时：
```json
{
  "success": false,
  "message": "错误描述"
}
```

**群聊命令格式**：

发送群聊消息，调用以下命令
```bash
python {baseDir}/scripts/icenter-msg.py -s --groupId <群组ID/URI> --groupName <群组名> --at <姓名工号,姓名工号,all> [--msg <消息内容>|--img <图片路径>|--file <文件路径>]
```

***群聊参数***：
- `-s`：表示发送消息
- `--groupId`：群组ID或群组URI（必须）
- `--groupName`：群组名称（可选，用于显示）
- `--at`：群聊 @ 参数（可选）。**必须同时携带"姓名+工号"**，多个 @ 目标用**英文逗号(,)** 分隔；@所有人使用 `all`。例如 `林明东0668000290,all,梁其天0668000408`。如果群聊提问中存在 `@某人/at某人/艾特某人` 但只有姓名或只有工号，必须先通过搜索用户补全，组装成"姓名+工号"后再传入。***重要***：`--at` **不允许只传工号或只传姓名**，否则会出现"只带工号"的问题。
- `--msg`：文本消息内容（必须，与 `--img`、`--file` 三者只能出现一个）
- `--img`：图片文件的绝对路径（必须，只支持 bmp、jpg、png，与 `--msg`、`--file` 三者只能出现一个）
- `--file`：文件的绝对路径（必须，与 `--msg`、`--img` 三者只能出现一个）

***群聊示例***：

**文本消息示例**：
提问：发送消息给小分队群，内容是：明天开会

命令：先查询小分队群的群ID，然后调用群聊发送消息
```bash
python {baseDir}/scripts/icenter-msg.py -q --groupName '小分队群'
python {baseDir}/scripts/icenter-msg.py -s --groupId 'sip:p1_20250922142134455452@gm.zte.com.cn' --groupName '小分队群' --msg '明天开会'
```

提问：将明天下午3点开会作为消息内容发送给小分队群，@所有人，@梁其天。

命令：先查询小分队群的群ID，查询梁其天的工号，然后调用群聊发送消息，发送时携带at参数。
```bash
python {baseDir}/scripts/icenter-msg.py -q --groupName '小分队群'
python {baseDir}/scripts/icenter-msg.py -q --userName '梁其天'
python {baseDir}/scripts/icenter-msg.py -s --groupId 'sip:p1_20250922142134455452@gm.zte.com.cn' --groupName '小分队' --at 'all,梁其天0668000408' --msg '明天下午3点开会'
```

**图片消息示例**：
提问：发送图片给群项目组：/home/user/chart.png

命令：先查询项目组的群ID，然后调用群聊发送图片
```bash
python {baseDir}/scripts/icenter-msg.py -q --groupName '项目组'
python {baseDir}/scripts/icenter-msg.py -s --groupId 'sip:p1_20250922142134455452@gm.zte.com.cn' --groupName '项目组' --img '/home/user/chart.png'
```

**文件消息示例**：
提问：发送文件给群项目组：/home/user/report.docx

命令：先查询项目组的群ID，然后调用群聊发送文件
```bash
python {baseDir}/scripts/icenter-msg.py -q --groupName '项目组'
python {baseDir}/scripts/icenter-msg.py -s --groupId 'sip:p1_20250922142134455452@gm.zte.com.cn' --groupName '项目组' --file '/home/user/report.docx'
```

***输出格式***：

成功时
```json
{
  "success": true,
  "message": "消息发送成功",
  "type": "group",
  "groupName": "项目组",
  "groupUri": "sip:p1_20250922142134455452@gm.zte.com.cn"
}
```

失败时：
```json
{
  "success": false,
  "message": "错误描述"
}
```


## 显示说明
- ***重要***：消息发送成功后，不用给用户展示群ID、msg_id(消息ID)、错误码等用户不理解的内容。


## 错误处理

工具会在 JSON 输出中返回错误信息，agent 需要解析并提示用户：

**参数错误**：
- 缺少必需参数：`{"success": false, "message": "单聊必须提供 --userName 参数"}` 或 `{"success": false, "message": "群聊必须提供 --groupName 参数"}`
- 缺少消息内容：`{"success": false, "message": "必须提供 --msg、--img 或 --file 参数之一"}`
- 同时提供多个消息类型参数：`{"success": false, "message": "--msg、--img、--file 三者只能出现一个"}`
- 文件路径不存在（图片/文件消息）：当使用 `--img` 或 `--file` 时，指定的文件路径不存在或无法访问，会返回相应的错误信息
- 图片格式不支持：当使用 `--img` 时，图片格式不是 bmp、jpg、png，会返回相应的错误信息

**认证错误**：
- 无法获取用户认证：`{"success": false, "message": "无法获取用户账号和Token，请检查登录状态或环境配置"}`

**鉴权错误**：
- 没有权限调用接口：`{"success": false, "message": "没有权限调用此接口"}`

**流量控制**：
- 此次调用受到流量控制：`{"success": false, "message": "此次调用受到流量控制"}`

**查询错误**（单聊）：
- 搜索用户失败：`{"success": false, "message": "搜索用户失败: <错误详情>"}`
- 未找到用户：`{"success": false, "message": "未找到用户: <用户名>"}`

**查询错误**（群聊）：
- 搜索群组失败：`{"success": false, "message": "搜索群组失败: <错误详情>"}`
- 未找到群组：`{"success": false, "message": "未找到群组: <群组名>"}`

**发送错误**：
- 发送消息失败：`{"success": false, "message": "发送消息失败: <错误详情>"}`
- API返回空响应：`{"success": false, "message": "发送消息失败: API返回空响应，可能未成功处理消息"}`
- 网络超时（30秒）：`{"success": false, "message": "发送消息失败: 请求失败: context deadline exceeded"}`
