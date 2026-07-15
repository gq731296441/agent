---
name: vanilla-web-builder
description: 结合专业视觉设计规范，使用原生 HTML、CSS 和 JavaScript 分步骤构建高质量、美观且功能完备的单页面网页（SPA）。适用于追求独特视觉身份、轻量级性能和原生交互的专业前端开发场景。
version: 1.1.0
author: platform
tags: ["网页设计"]
triggers: ["制作网页", "交互", "网页", "制作网站", "html", "可交互"]
---

# Vanilla Web Builder (Enhanced with Frontend Design)

本 Skill 引导 agent 作为一个专业的设计与开发工作室，使用原生技术栈通过分阶段、模块化的方式构建具有**独特视觉身份**的**单文件**网页。

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

---

## 分块构建网页的标准工作流

网页通常包含 HTML 结构、CSS 样式和 JS 逻辑三大部分，总长度远超 1000 字符。**必须按以下流程分块构建：**

### 第一步：规划

使用 `write_todos` 工具创建任务清单，列出所有需要写入的内容块。每个块预估不超过 1000 字符，超长则继续拆分。

### 第二步：创建 HTML 骨架

使用 `write_file` 创建文件，只写入最小的 HTML 固定结构。在 `<style>` 标签内放置占位符 `<!-- CSS_SLOT -->`，在 `<body>` 内放置 `<!-- BODY_SLOT -->`，在 `<script>` 标签内放置 `<!-- JS_SLOT -->`。这三个占位符将作为后续 `edit_file` 的锚点。

### 第三步：填充 CSS 样式

先 `read_file` 确认文件内容，然后用 `edit_file` 分模块写入样式。每次替换 `<!-- CSS_SLOT -->` 为实际样式代码，并在新内容末尾保留 `<!-- CSS_SLOT -->` 以便继续追加。设计系统、布局、组件样式等各自拆为独立块。

### 第四步：填充 HTML 内容

用 `edit_file` 以 `<!-- BODY_SLOT -->` 为锚点，逐段填充 body 内的 HTML 结构。每次替换后保留占位符以继续追加。

### 第五步：填充 JS 交互逻辑

用 `edit_file` 以 `<!-- JS_SLOT -->` 为锚点，逐功能填充 JavaScript。事件监听、状态管理、动画逻辑等各自拆为独立块。

### 第六步：收尾清理

所有内容写入完毕后，用 `edit_file` 将三个占位符分别替换为空字符串，清理干净。

### 第七步：验证

用 `read_file` 检查最终文件，确保结构完整、无残留占位符。

---

## 核心原则

1.  **独特视觉身份**：拒绝"AI 风格"的平庸模板。通过对调色板、字体排版和布局的深思熟虑，为每个项目打造专属的视觉语言。
2.  **单文件交付 (Single-File Delivery)**：最终输出必须是一个包含所有 HTML、CSS 和 JS 的单一 `.html` 文件。严禁使用外部文件引入。
3.  **分步构建**：严禁一次性输出完整代码。必须先进行视觉与结构规划，再分块实现样式和逻辑，最后完成集成。
4.  **零框架依赖**：仅使用原生 HTML5, CSS3 和 ECMAScript 6+，确保极致的性能和可控性。
5.  **内容驱动设计**：设计必须根植于实际内容，结构化元素应具有语义意义。

## 增强型构建流程

### 第一步：视觉方向与结构规划

在写代码前，先确定设计的"灵魂"。
- **定义主题**：明确网页的受众、核心任务和视觉调性。
- **设计提案**：描述将采用的配色方案、字体配对和核心交互点。
- **结构大纲**：输出 DOM 树大纲，标注出关键的语义化标签和模块划分。

### 第二步：编写语义化 HTML 骨架

使用 `write_file` 创建文件，写入最小的 HTML 结构 + 三个占位符。使用语义化标签（`<header>`, `<main>`, `<article>`, `<section>`, `<nav>` 等）。

### 第三步：分块编写内联样式

使用 `edit_file` 在 `<style>` 标签内分模块编写 CSS。用 CSS 变量定义设计系统，按模块逐步替换 `<!-- CSS_SLOT -->`。

### 第四步：原生 JS 交互与动效

使用 `edit_file` 在 `<script>` 标签内分功能模块编写 JavaScript。使用 `addEventListener` 和原生 API，利用 CSS Transition/Animation 提升体验。

### 第五步：集成、审核与优化

用 `edit_file` 删除所有占位符，用 `read_file` 审核最终结果，确保不依赖任何外部资源。

## 常用模式与技巧

- **SVG 内联**：使用内联 SVG 代替外部图片图标，确保单文件的完整性。
- **Base64 资源**：对于必需的小型图片资源，建议转换为 Base64 内联。
- **原生组件化**：使用函数返回 HTML 字符串或 `<template>` 标签在单文件内实现组件复用。
- **CSS 艺术**：利用 `clip-path`, `mask`, `backdrop-filter` 等现代原生 CSS 属性创造独特视觉效果。
