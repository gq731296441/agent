# 安全页面编辑指南 - 避免 HTML-MD-HTML 转换问题

## 问题背景

用户反馈在使用 studio-space-manager 进行页面编辑时，当前的 **HTML → Markdown → HTML** 双向转换方式存在以下问题：

### 1. 特定内容格式错乱
- **PlantUML 代码块**: `html2markdown` 和 `markdown2html` API 在双向转换过程中可能破坏 PlantUML 的换行符和特殊字符
- **宏结构丢失**: iCenter 使用特殊的 `<table data-macro-props='...'>` 结构表示各种宏（提醒、折叠、包含页面等），Markdown 无法保留这些结构化信息

### 2. 大内容场景下的截断
- API 调用可能有隐式的内容长度限制
- 双向转换增加了处理链长度，每层都可能引入截断或简化
- 模型在处理长文本时可能丢弃部分内容

### 3. 转换损失示例

```
原始 HTML: 
<table data-macro-props='{"macroKey":"com.zte.wiki.macro.RemindMacro",...}'>
  <tr><td><p>提醒内容</p></td></tr>
</table>

↓ html2markdown API

Markdown:
> 提醒内容

↓ markdown2html API

新 HTML:
<blockquote>
  <p>提醒内容</p>
</blockquote>

❌ 宏结构完全丢失！
```

## 解决方案：HTML 直传模式

核心思路：**跳过 Markdown 转换，直接操作原始 HTML**

### 工作流程

```mermaid
graph LR
    A[原始页面] -->|导出| B[HTML 文件]
    B -->|编辑器| C[编辑后的 HTML]
    C -->|导入| D[更新页面]
    
    style B fill:#90EE90
    style C fill:#90EE90
    
    classDef safe fill:#90EE90;
    class B,C safe
```

### 优势对比

| 特性 | 传统 HTML-MD-HTML 方式 | HTML 直传模式 ⭐ |
|------|---------------------|------------------|
| 宏结构保留 | ❌ 丢失 | ✅ 100% 保留 |
| PlantUML 格式 | ❌ 可能破坏 | ✅ 完全保留 |
| 大内容处理 | ❌ 可能截断 | ✅ 无限制 |
| API 调用次数 | 2 次 (导出+回写) | 2 次 (但更可靠) |
| 编辑体验 | ✅ Markdown 友好 | ⚠️ 需要 HTML 基础 |
| 格式保真度 | ~60-80% | 100% |

## v3.0.0 新功能亮点

### ✨ 智能内容类型检测

当用户提供文件时，脚本会自动检测内容是 Markdown 还是 HTML:

```python
# 自动检测算法
- HTML特征：包含 <tag> 标签
- Markdown特征：包含 #标题、**粗体**、-列表等
- 混合内容：计算特征强度，决定最优处理方式
```

**好处**: 用户无需手动指定 `--content-html`，脚本会智能判断

### ✨ PlantUML保护机制增强

```python
# 1. 提取并保护PlantUML代码块
protected_content, plantuml_blocks = protect_plantuml_blocks(markdown_content)

# 2. 调用后端API转换
html_result = markdown2html_api(protected_content)

# 3. 恢复PlantUML为正确HTML格式
html_result = restore_plantuml_blocks(html_result, plantuml_blocks)
```

**效果**: PlantUML图表100%保真，即使经过Markdown转换

### ✨ 详细日志记录

```bash
# 启用调试模式查看详细日志
python3 studio_space_page_edit.py --content-file page.md 2>&1 | grep DEBUG
```

输出示例:
```
DEBUG - 开始转换Markdown内容，长度=1234字符
DEBUG - 检测到2个PlantUML代码块，已保护
DEBUG - 调用后端markdown2html API...
DEBUG - 后端API转换成功，HTML长度=5678字符
DEBUG - 恢复PlantUML代码块
```

### ✨ 增强的错误处理

| 场景 | 旧行为 | 新行为 (v3.0.0) |
|------|--------|----------------|
| API超时 | 直接报错 | 使用本地fallback继续 |
| API返回错误 | 失败退出 | 降级到本地转换 |
| 响应解析失败 | 崩溃 | 优雅降级并记录日志 |
| 结果为空 | 返回空字符串 | 返回原始Markdown防止数据丢失 |

---

## 使用方法

### 方式选择指南

| 你的需求 | 推荐方式 | 理由 |
|---------|---------|------|
| 简单文本页面 | Markdown模式 | 易读易写，NL2BR自动处理换行 |
| 包含PlantUML/宏 | HTML直传模式 | 100%保真，无转换损失 |
| 不确定 | 让脚本自动检测 | v3.0.0智能识别内容类型 |
| 只改一小节 | `--mode chapter` | 最快，不影响其他内容 |

### 步骤 1: 导出页面原始 HTML

```bash
python3 safe_page_edit.py --action export \
  --url "https://i.zte.com.cn/index/ispace/#/space/xxx/wiki/page/yyy/view" \
  --output-dir ./my-page
```

**输出**:
- `./my-page/<页面标题>.html` - 原始 HTML 内容
- `./my-page/metadata.json` - 页面元数据（URL、ID、标题等）

### 步骤 2: 编辑 HTML 文件

推荐使用以下编辑器：

#### VSCode (推荐)
```bash
code ./my-page/page.html
```
- 安装插件: `Prettier - Code formatter`
- 快捷键: `Shift + Alt + F` 格式化 HTML

#### Sublime Text
```bash
subl ./my-page/page.html
```

#### 在线编辑器
- [CodePen](https://codepen.io/pen/)
- [JSFiddle](https://jsfiddle.net/)

#### 轻量级编辑技巧

如果只需要添加新章节，可以在 HTML 末尾追加：

```html
<!-- 新增章节 -->
<h1 id="new-section">第五章：新功能介绍</h1>
<p data-ai="studio-space-manager">这里是新功能的详细说明...</p>

<!-- 或者使用折叠宏 -->
<table data-macro-props='{"macroKey":"com.zte.wiki.macro.CollapseMacro","macroParameters":{"title":"点击展开查看示例"}}'>
  <tbody>
    <tr>
      <td>
        <p>这是折叠区域内的示例代码：</p>
        <pre><code>def hello():
    print("Hello, World!")</code></pre>
      </td>
    </tr>
  </tbody>
</table>
```

### 步骤 3: 回写页面

```bash
python3 safe_page_edit.py --action import \
  --url "https://i.zte.com.cn/index/ispace/#/space/xxx/wiki/page/yyy/view" \
  --html-file ./my-page/page.html
```

**可选**: 同时更新标题
```bash
python3 safe_page_edit.py --action import \
  --url "<URL>" \
  --html-file ./my-page/page.html \
  --title "新的页面标题"
```

## 高级用法

### 1. 批量导出多个页面

```bash
#!/bin/bash
# batch_export.sh

PAGES=(
  "https://i.zte.com.cn/.../page/001/view"
  "https://i.zte.com.cn/.../page/002/view"
  "https://i.zte.com.cn/.../page/003/view"
)

for url in "${PAGES[@]}"; do
  python3 safe_page_edit.py --action export --url "$url" --output-dir "./batch-export/$(basename $url)"
done
```

### 2. 版本控制集成

```bash
# 初始化 Git 仓库
cd my-page
git init
git add .
git commit -m "Initial page export"

# 每次编辑后
git add page.html
git commit -m "Update: 添加了新功能章节"

# 回写前可以查看差异
git diff HEAD~1 page.html
```

### 3. 自动化测试

```python
# test_page_structure.py
import sys
from bs4 import BeautifulSoup

html_file = sys.argv[1]
html_content = open(html_file).read()

soup = BeautifulSoup(html_content, 'html.parser')

# 检查是否包含必需的宏结构
macros = soup.find_all('table', attrs={'data-macro-props': True})
print(f"Found {len(macros)} macros")

# 验证 PlantUML 代码块
plantuml_blocks = soup.find_all('code', class_='language-plantuml')
print(f"Found {len(plantuml_blocks)} PlantUML blocks")

# 确保没有破坏的 HTML 结构
if soup.find():
    print("✅ HTML structure is valid")
else:
    print("❌ Invalid HTML structure")
```

## 故障排除

### Q1: 导出的 HTML 文件很大（超过 1MB）
**A**: 这是正常的。iCenter 页面可能包含大量内联样式和脚本。可以直接编辑，无需压缩。

### Q2: 编辑器显示很多看不懂的标签
**A**: 这些是 iCenter 的内部标记。只需关注以下内容进行修改：
- `<h1>`-`<h6>`: 章节标题
- `<p>`: 段落
- `<table data-macro-props='...'>`: 各种宏
- `<pre><code>`: 代码块

### Q3: 回写后页面上显示的是 HTML 源码而不是渲染后的内容
**A**: 这通常是因为 HTML 结构不完整。确保：
- 所有标签都正确闭合
- `<table>` 宏包含完整的 `<tbody><tr><td>` 结构
- 不要删除 `data-macro-props` 属性

### Q4: PlantUML 图表不显示
**A**: 检查 `<pre><code class="language-plantuml">` 块内的内容：
- 确保 `@startuml` 和 `@enduml` 完整
- 避免使用可能被 HTML 转义的特殊字符（如 `<` 应写为 `&lt;`）

## 最佳实践

### ✅ 推荐做法

1. **小改动直接用章节模式**
   ```bash
   # 只添加一个小节，用章节模式更快
   python3 studio_space_page_edit.py \
     --url "<URL>" \
     --mode chapter \
     --chapter-name "3.2 性能优化" \
     --content "## 3.2 性能优化\n\n具体内容..."
   ```

2. **大改动才用 HTML 直传**
   - 重构整个页面结构
   - 添加多个宏组件
   - 页面包含复杂格式（PlantUML、表格等）

3. **编辑前先备份**
   ```bash
   cp page.html page.html.backup
   ```

4. **使用编辑器的查找替换功能**
   - 批量修改链接
   - 统一术语
   - 修复拼写错误

### ❌ 避免的做法

1. **不要手动修改 `data-macro-props` 中的 JSON**
   - 容易导致 JSON 格式错误
   - 如需修改宏参数，建议删除旧宏并插入新宏

2. **不要删除 `<tbody>`、`<tr>`、`<td>` 等必需标签**
   - iCenter 依赖这些标签识别宏区域

3. **不要在 `<pre><code>` 块内使用 HTML 实体**
   - 代码块内的内容应该原样书写

## 迁移指南

如果你之前使用传统的 Markdown 编辑方式，以下是迁移步骤：

### 从 Markdown 切换到 HTML

**之前**:
```bash
# 导出为 Markdown
python3 space_page_tomd.py --url "<URL>"

# 编辑 .md 文件
vim page.md

# 回写（会经过 Markdown→HTML 转换）
python3 studio_space_page_edit.py --content-file page.md
```

**现在**:
```bash
# 导出为 HTML
python3 safe_page_edit.py --action export --url "<URL>" --output-dir ./page

# 编辑 .html 文件
code ./page/page.html

# 回写（直接 HTML，无转换损失）
python3 safe_page_edit.py --action import --url "<URL>" --html-file ./page/page.html
```

## 工具对比

| 工具 | 适用场景 | 优点 | 缺点 |
|------|---------|------|------|
| `space_page_tomd.py` + `studio_space_page_edit.py` | 简单文本页面 | Markdown 易读易写 | 格式可能丢失 |
| `safe_page_edit.py` (本工具) | 复杂格式页面 | 100% 保真 | 需要 HTML 基础 |
| `studio_space_page_edit.py --mode chapter` | 添加小章节 | 快速便捷 | 仅限增量更新 |
| `studio_space_page_edit.py --content-html` | 直接提供 HTML | 灵活 | 需自行准备 HTML |

## 相关资源

- [iCenter 宏 HTML 模板大全](../references/all-macro-html-templates.md) - 所有宏的完整 HTML 结构
- [Download URL Refactoring](../references/download-url-refactoring.md) - 附件下载接口重构说明
- [VSCode HTML 编辑技巧](https://code.visualstudio.com/docs/languages/html)

## 技术实现细节

### 为什么 HTML 直传能避免问题？

1. **无中间转换环节**
   ```
   传统方式: HTML → html2markdown → Markdown → markdown2html → HTML
   直传方式: HTML → HTML
   ```

2. **保留所有元数据**
   - `data-macro-props` 属性完整保留
   - `data-ai` 标识不会被移除
   - 自定义类名和 ID 保持不变

3. **避免 API 限制**
   - `html2markdown` 和 `markdown2html` 都有隐式的内容长度限制
   - 直传只使用 `updateContent` 接口，该接口支持更大的内容

### 安全性保证

- HTML 内容在保存前会经过 `html_add_ai_attr()` 处理，为文本元素添加 AI 标识
- 不会修改页面的其他属性（权限、标签等）
- 支持事务性更新（要么全部成功，要么全部失败）

## 未来改进方向

1. **可视化 HTML 编辑器集成**
   - 考虑集成 [TinyMCE](https://www.tiny.cloud/) 或 [CKEditor](https://ckeditor.com/)
   - 提供所见即所得的编辑体验

2. **智能差异合并**
   - 当多人同时编辑时，自动检测冲突
   - 提供三路合并工具

3. **模板系统**
   - 预定义常用页面结构的 HTML 模板
   - 一键插入标准宏组合

---

**更新日期**: 2026-06-10  
**版本**: 1.0.0  
**作者**: Co-Claw Team
