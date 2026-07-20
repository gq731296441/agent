---
name: vanilla-web-builder
description: 结合专业视觉设计规范，使用原生 HTML、CSS 和 JavaScript 分步骤构建高质量、美观且功能完备的网页。采用多文件项目结构（HTML/CSS/JS 分离），适用于追求独特视觉身份、轻量级性能和原生交互的专业前端开发场景。
version: 1.2.0
author: platform
tags: ["网页设计"]
triggers: ["可交互网页", "网页", "网站", "html", "HTML报告"]
---

# Vanilla Web Builder (Enhanced with Frontend Design)

本 Skill 引导 agent 作为一个专业的设计与开发工作室，使用原生技术栈通过分阶段、模块化的方式构建具有**独特视觉身份**的网页项目。

---

## ⚠️ 工具使用规范（必须在每一步严格遵守）

以下文件工具均已默认启用。**每条工具调用都必须包含所有必填参数，不得遗漏。**

### 工具速查表

| 工具 | 必填参数 | 用途 |
|------|---------|------|
| `read_file` | `file_path` | 查看文件内容 |
| `write_file` | `file_path`, `content` | 创建新文件 |
| `edit_file` | `file_path`, `old_string`, `new_string` | 修改已有文件 |
| `ls` | `path` | 查看目录结构 |
| `glob` | `pattern` | 查找文件 |
| `grep` | `pattern` | 搜索代码 |
| `execute` | `command` | 在沙箱执行命令 |

### 🚫 严禁行为

- 调用 `write_file` 时遗漏 `content` 参数
- 调用 `edit_file` 时遗漏 `old_string` 或 `new_string` 参数
- 一次性在 `write_file` 或 `edit_file` 中传入超过 1000 字符的内容
- 在没有 `read_file` 查看文件内容的情况下直接 `edit_file`
- 连续用 `write_file` 覆盖同一个文件（应从第二次开始改用 `edit_file`）
- **禁止用 `execute("mkdir ...")` 创建项目目录**：`write_file` 会自动创建父目录（`mkdir -p`），且权限正确。使用 `execute` 创建目录会导致后续 `write_file` 权限被拒绝（Permission denied）。直接 `write_file /workspace/项目名/index.html` 即可，目录自动生成。

---

## 多文件项目构建标准工作流

网页项目包含 HTML 结构、CSS 样式和 JS 逻辑三大部分。**必须在 workspace 下创建项目子目录，将各部分拆分为独立文件。** 每个文件按分块策略写入（单次 ≤1000 字符）。

### 第一步：规划

使用 `write_todos` 工具创建任务清单。确定项目目录名（按主题命名，如 `web-report/`、`landing-page/`、`dashboard/`），规划文件结构。标准结构为：

```
/workspace/{项目目录}/
  ├── index.html    ← 入口文件，HTML 结构（引用 CSS/JS）
  ├── style.css     ← 所有 CSS 样式
  └── script.js     ← 所有 JS 交互逻辑
```

如有需要可增加额外的 CSS 或 JS 文件，但总数不超过 5 个（前端预览最多支持 20 个文件，留足余量）。

### 第二步：创建项目目录和 HTML 入口文件

使用 `write_file` 创建 `index.html`。写入最小 HTML 骨架，使用**相对路径**引用同目录下的 CSS 和 JS 文件，在 `<body>` 内保留 `<!-- BODY_SLOT -->` 占位符。

正确示例：
```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>项目标题</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<!-- BODY_SLOT -->
<script src="script.js"></script>
</body>
</html>
```

**关键规则：**
- ✅ 使用相对路径：`href="style.css"`、`src="script.js"`
- ❌ 禁止外部 CDN：`href="https://cdn.example.com/..."` 
- ❌ 禁止绝对路径：`href="/workspace/project/style.css"`
- 所有资源必须是同目录下的本地文件

### 第三步：分块填充 HTML 内容

先 `read_file` 确认文件内容，然后用 `edit_file` 以 `<!-- BODY_SLOT -->` 为锚点，逐段填充 body 内的语义化 HTML 结构。每次替换后保留占位符以继续追加。

### 第四步：分块编写 CSS 样式

1. 用 `write_file` 创建 `style.css`，写入 CSS 变量和全局重置样式，末尾保留 `<!-- CSS_SLOT -->` 占位符
2. 用 `edit_file` 逐模块填充：设计系统（变量）→ 布局 → 组件样式 → 动画
3. 每次替换后保留占位符以继续追加

### 第五步：分块编写 JS 交互逻辑

1. 用 `write_file` 创建 `script.js`，写入基础结构和事件监听骨架，末尾保留 `<!-- JS_SLOT -->` 占位符
2. 用 `edit_file` 逐功能填充：状态管理 → 事件处理 → 动画逻辑 → 工具函数
3. 每次替换后保留占位符以继续追加

### 第六步：收尾清理与验证

1. 用 `edit_file` 将各文件中的占位符替换为空字符串
2. 用 `read_file` 检查所有文件，确保结构完整、无残留占位符
3. 确认 `index.html` 中对 CSS/JS 文件的引用路径正确
4. 用 `ls` 确认项目目录结构完整

---

## 核心原则

1.  **独特视觉身份**：拒绝"AI 风格"的平庸模板。通过对调色板、字体排版和布局的深思熟虑，为每个项目打造专属的视觉语言。
2.  **多文件项目交付 (Multi-File Project Delivery)**：在 workspace 下创建项目子目录，将 HTML 结构、CSS 样式和 JS 逻辑拆分为独立文件。HTML 通过 `<link>` 和 `<script>` 的相对路径引用同目录下的 CSS/JS 文件。严禁使用外部 CDN 资源。
3.  **分步构建**：严禁一次性输出完整代码。必须先进行视觉与结构规划，再按文件分块实现，最后完成集成验证。
4.  **零框架依赖**：仅使用原生 HTML5, CSS3 和 ECMAScript 6+，确保极致的性能和可控性。
5.  **内容驱动设计**：设计必须根植于实际内容，结构化元素应具有语义意义。

## 增强型构建流程

### 第一步：视觉方向与结构规划

在写代码前，先确定设计的"灵魂"。
- **定义主题**：明确网页的受众、核心任务和视觉调性。
- **设计提案**：描述将采用的配色方案、字体配对和核心交互点。
- **结构大纲**：输出 DOM 树大纲，标注出关键的语义化标签和模块划分。
- **文件规划**：确定项目目录名和文件清单。

### 第二步：创建项目目录和 HTML 入口

使用 `write_file` 在 `/workspace/{项目目录}/` 下创建 `index.html`。写入最小 HTML 结构，使用 `<link rel="stylesheet" href="style.css">` 引用样式、`<script src="script.js">` 引用脚本。在 `<body>` 内保留 `<!-- BODY_SLOT -->` 占位符。使用语义化标签（`<header>`, `<main>`, `<article>`, `<section>`, `<nav>` 等）。

### 第三步：分块填充 HTML 内容

使用 `edit_file` 以 `<!-- BODY_SLOT -->` 为锚点，逐段填充 body 内的语义化内容。每次替换后保留占位符以继续追加。

### 第四步：分块编写 CSS 样式

1. 用 `write_file` 创建 `style.css`，写入 CSS 变量定义和全局重置，末尾保留 `<!-- CSS_SLOT -->`
2. 用 `edit_file` 以 `<!-- CSS_SLOT -->` 为锚点，按模块逐步填充：设计系统 → 布局 → 组件 → 动画与装饰
3. 利用 CSS 变量统一管理颜色、间距、字体等设计 Token

### 第五步：分块编写 JS 交互逻辑

1. 用 `write_file` 创建 `script.js`，写入基础结构（DOMContentLoaded 事件包裹），末尾保留 `<!-- JS_SLOT -->`
2. 用 `edit_file` 以 `<!-- JS_SLOT -->` 为锚点，按功能模块逐步填充
3. 使用 `addEventListener` 和原生 API，利用 CSS Transition/Animation 提升体验

### 第六步：集成、审核与优化

1. 用 `edit_file` 删除所有残留占位符
2. 用 `read_file` 审核各文件内容
3. 用 `ls` 确认项目目录结构
4. 确保不依赖任何外部 CDN 资源

## 常用模式与技巧

- **多文件项目结构示例**：标准三文件（index.html + style.css + script.js）可覆盖 90% 的网页项目。仅在内容特别复杂时考虑拆分（如 `layout.css` + `components.css` 或 `utils.js` + `main.js`），总数不超过 5 个文件。
- **SVG 内联**：使用内联 SVG 代替外部图片图标，保持项目自包含。
- **Base64 资源**：对于必需的小型图片资源，建议转换为 Base64 内联到 HTML 或 CSS 中。
- **原生组件化**：使用函数返回 HTML 字符串或 `<template>` 标签实现组件复用。
- **CSS 艺术**：利用 `clip-path`, `mask`, `backdrop-filter` 等现代原生 CSS 属性创造独特视觉效果。
- **路径验证清单**：最终验证时逐一检查 HTML 中的 `<link href>` 和 `<script src>` — 确保是相对路径、指向同目录下存在的文件、不包含 `https://` 或 `http://`。
