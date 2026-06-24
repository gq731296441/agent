"""Deep Agent 包。

Deep 模式核心模块：
  factory / manager_ext — Agent 创建与缓存
  runner / adapter      — 流式运行与事件适配
  backend_factory       — 虚拟文件系统路由
  sandbox               — Docker execute 沙箱
  artifact              — 工具结果 spill 与 legacy artifact
  memory                — User Memory（Store + DB）；非 deepagents /memories/ 路径
  store                 — LangGraph Store namespace 与清理
  skills                — SKILL.md 解析与按需匹配
  session/readiness     — 会话预热状态机
  hitl                  — Human-in-the-Loop 中断处理
  events                — 内部 EventBus（审计 / 指标）
"""
