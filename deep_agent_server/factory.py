"""Deep Agent 工厂。

负责组装 deepagents.create_deep_agent 所需的 model、backend、permissions、
interrupt_on 等组件，并返回 DeepAgentWrapper 供 runner 消费。
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from deepagents import create_deep_agent

from agent_server.llm import create_agent_llm
from config import get_logger
from config.config import app_config
from deep_agent_server.backend_factory import (
    build_backend_factory,
    build_interrupt_on,
    build_permissions,
)
from agent_server.middleware.RateLimitMiddleware import RateLimitMiddleware
from deep_agent_server.middleware.context_overflow_guard import ContextOverflowGuardMiddleware
from deep_agent_server.middleware.html_share_link import HtmlShareLinkMiddleware
from deep_agent_server.middleware.tool_result_ttl import ToolResultTTLMiddleware
from deep_agent_server.sandbox import get_sandbox_backend_factory_if_enabled
import deep_agent_server.tool_patches  # noqa: F401 — 模块导入即生效：替换 write_file/edit_file 描述为分块版本


# ---- 替换 _get_approximate_token_counter 为准确版本 ----
# deepagents 内置的 SummarizationMiddleware 使用 count_tokens_approximately
# 估算 token 数（chars/4），对中文文本低估 6-8 倍，导致上下文压缩从不触发。
#
# SummarizationMiddleware.__init__ 的计数逻辑：
#   if token_counter is count_tokens_approximately:  ← is 检查
#       self.token_counter = _get_approximate_token_counter(model)
#
# 不能直接替换 count_tokens_approximately（会导致 is 检查失败而跳过此路径），
# 改为替换 _get_approximate_token_counter，让它返回基于 TokenCounter 的准确版本。
def _patch_token_counter_for_summarization():
    """使 SummarizationMiddleware 获得准确的 token 计数能力。"""
    try:
        import langchain.agents.middleware.summarization as _lc_summ

        _getter = getattr(_lc_summ, "_get_approximate_token_counter", None)
        if _getter is None:
            return
        if getattr(_getter, "_patched_by_zte", False):
            return

        def _accurate_get_approximate_token_counter(model):
            """返回使用项目 TokenCounter 的准确 token 计数器。

            deepagents 内置的版本返回 partial(count_tokens_approximately,
            use_usage_metadata_scaling=True)，其 chars/4 估算对中文低估 6-8 倍
            且 scaling 校正上限仅 1.25x。

            返回的 callable 必须接受 **kwargs（兼容 _partial_token_counter
            传入的 use_usage_metadata_scaling 参数）。
            """
            from utils.token_counter import token_counter as _tk

            def _counter(messages, **kwargs):
                """准确的 token 计数 wrapper，忽略多余的 kwargs。"""
                return _tk.count_messages_tokens(messages)

            return _counter

        _accurate_get_approximate_token_counter._patched_by_zte = True
        _lc_summ._get_approximate_token_counter = _accurate_get_approximate_token_counter

        logger.info(
            "[DeepFactory] 已替换 _get_approximate_token_counter → TokenCounter "
            "(基于 tokenizer.json 精确计数)"
        )
    except Exception as e:
        logger.warning(f"[DeepFactory] 替换 token 计数器失败: {e}")


# ---- 修复摘要消息格式（防模型回显） ----
# deepagents 的 _build_new_messages_with_path 将摘要包装为 HumanMessage，
# 部分 CoT 模型会将摘要生成过程的推理内容和摘要本身输出到对话中，
# 导致用户看到压缩过程的内部思考。此处将防回显指令前置并强化。
def _patch_summary_message_format():
    """将摘要消息重构为内部上下文，防止模型回显。"""
    try:
        from deepagents.middleware.summarization import (
            _DeepAgentsSummarizationMiddleware,
        )

        _original_build = _DeepAgentsSummarizationMiddleware._build_new_messages_with_path

        if getattr(_original_build, "_patched_by_zte", False):
            return

        def _patched_build_new_messages(self, summary, file_path=None):
            messages = _original_build(self, summary, file_path)
            if not messages or not hasattr(messages[0], "content"):
                return messages
            msg = messages[0]
            # 将防回显指令放在消息最前面，比末尾追加更有效
            anti_echo_prefix = (
                "## ⚠️ INTERNAL CONTEXT — DO NOT ECHO TO USER\n\n"
                "The text below in `<summary>` tags is internal conversation "
                "history for your background awareness ONLY. You MUST:\n"
                "- NEVER repeat, recite, paraphrase, or quote any part of it\n"
                "- NEVER format your response using its section headers "
                "(SESSION INTENT / SUMMARY / ARTIFACTS / NEXT STEPS)\n"
                "- NEVER include its content in your reasoning output\n"
                "- Respond to the user naturally as if you already know this context\n"
                "- If the user's message is a simple greeting, just greet back "
                "without discussing the summary\n\n"
                "---\n\n"
            )
            messages[0] = msg.model_copy(
                update={"content": anti_echo_prefix + msg.content}
            )
            return messages

        _patched_build_new_messages._patched_by_zte = True
        _DeepAgentsSummarizationMiddleware._build_new_messages_with_path = (
            _patched_build_new_messages
        )

        logger.info("[DeepFactory] 已为摘要消息添加前置防回显指令")
    except Exception as e:
        logger.warning(f"[DeepFactory] 修补摘要消息格式失败: {e}")


# ---- 防止摘要模型调用的输出泄露到 graph stream ----
# SummarizationMiddleware._acreate_summary 内部调用 model.ainvoke() 时，
# 由于 LangChain 的 callback 继承机制（contextvars），当前 graph 的 streaming
# callbacks 会被继承，导致摘要生成的推理内容和摘要文本被捕获并路由到
# messages channel，最终作为普通对话输出到前端。
#
# 修补方案：在 _acreate_summary 调用模型时显式传入 callbacks=[]，
# 阻止 LangGraph streaming callbacks 的继承。
def _patch_acreate_summary_no_callbacks():
    """防止摘要模型输出通过 callback 继承泄露到 astream。"""
    try:
        from langchain.agents.middleware.summarization import (
            SummarizationMiddleware as LCSummarizationMiddleware,
        )

        _original_acreate = LCSummarizationMiddleware._acreate_summary

        if getattr(_original_acreate, "_patched_by_zte", False):
            return

        async def _patched_acreate_summary(self, messages_to_summarize):
            """与原始 _acreate_summary 逻辑一致，但在 model.ainvoke() 时
            显式传入 callbacks=[]，切断 LangGraph streaming callbacks 的继承链。
            """
            if not messages_to_summarize:
                return "No previous conversation history."

            trimmed_messages = self._trim_messages_for_summary(messages_to_summarize)
            if not trimmed_messages:
                return "Previous conversation was too long to summarize."

            from langchain_core.messages.utils import get_buffer_string

            formatted_messages = get_buffer_string(trimmed_messages)

            try:
                # 🔑 核心修复：config 中显式传入 callbacks=[]，
                # 阻止继承 LangGraph 的 streaming callbacks（会将模型输出
                # 捕获到 messages channel 并路由到前端）。
                response = await self.model.ainvoke(
                    self.summary_prompt.format(messages=formatted_messages).rstrip(),
                    config={
                        "callbacks": [],
                        "metadata": {"lc_source": "summarization"},
                    },
                )
                return response.text.strip()
            except Exception as e:
                return f"Error generating summary: {e!s}"

        _patched_acreate_summary._patched_by_zte = True
        LCSummarizationMiddleware._acreate_summary = _patched_acreate_summary

        logger.info("[DeepFactory] 已修补 _acreate_summary — 摘要模型调用不再继承 graph callbacks")
    except Exception as e:
        logger.warning(f"[DeepFactory] 修补 _acreate_summary callback 泄露失败: {e}")


# ---- 上下文压缩通知前端（仅在真正压缩时发送） ----
# 策略：awrap_model_call 入口处将 stream_writer 暂存到 self 上，
# _acreate_summary 被调用时才通过它发射 custom event（确保只在
# 压缩实际执行时通知前端，而非每次 agent 对话都发送）。
def _patch_awrap_store_stream_writer():
    """在 awrap_model_call 入口暂存 stream_writer，供 _acreate_summary 使用。"""
    try:
        from deepagents.middleware.summarization import (
            _DeepAgentsSummarizationMiddleware,
        )

        _original_awrap = _DeepAgentsSummarizationMiddleware.awrap_model_call

        if getattr(_original_awrap, "_patched_store_writer", False):
            return

        async def _patched_awrap_model_call(self, request, handler):
            """暂存 stream_writer，执行结束后清理。"""
            try:
                runtime = getattr(request, "runtime", None)
                self._zte_stream_writer = getattr(runtime, "stream_writer", None) if runtime else None
            except Exception:
                self._zte_stream_writer = None
            try:
                return await _original_awrap(self, request, handler)
            finally:
                self._zte_stream_writer = None

        _patched_awrap_model_call._patched_store_writer = True
        _DeepAgentsSummarizationMiddleware.awrap_model_call = _patched_awrap_model_call

        logger.info("[DeepFactory] 已修补 awrap_model_call — 暂存 stream_writer")
    except Exception as e:
        logger.warning(f"[DeepFactory] 修补 awrap_model_call stream_writer 暂存失败: {e}")


def _patch_acreate_summary_emit_compressing():
    """在 _acreate_summary 被调用时发射 context_compressing 事件。

    只有压缩真正触发时才会调用 _acreate_summary，因此不会误报。
    """
    try:
        from deepagents.middleware.summarization import (
            _DeepAgentsSummarizationMiddleware,
        )

        _original_deep_acreate = _DeepAgentsSummarizationMiddleware._acreate_summary

        if getattr(_original_deep_acreate, "_patched_emit_compressing", False):
            return

        async def _patched_deep_acreate_summary(self, messages_to_summarize):
            """在生成摘要前通知前端。"""
            writer = getattr(self, "_zte_stream_writer", None)
            if writer:
                try:
                    writer({"type": "context_compressing"})
                except Exception:
                    pass
            return await _original_deep_acreate(self, messages_to_summarize)

        _patched_deep_acreate_summary._patched_emit_compressing = True
        _DeepAgentsSummarizationMiddleware._acreate_summary = _patched_deep_acreate_summary

        logger.info("[DeepFactory] 已修补 _acreate_summary — 压缩执行时发送 CONTEXT_COMPRESSING")
    except Exception as e:
        logger.warning(f"[DeepFactory] 修补 _acreate_summary 通知失败: {e}")


from deep_agent_server.sandbox.exceptions import SandboxUserLimitExceeded
from agent_server.prompts import (
    build_base_system_prompt,
    build_subagent_system_prompt,
    build_system_prompt,
)

logger = get_logger(__name__)

def _patch_format_message_content():
    """过滤 invalid_tool_call 内容块，防止其被序列化到 API 请求中。

    v3 的 ChatModelStream._finalize_message() 会将 invalid_tool_calls
    写入 AIMessage.content（type="invalid_tool_call" 块）。这些块在 checkpoint
    持久化后会在下一轮请求中发送给 API，部分 API（如 nebulacoder）不支持此类型
    并返回 400 错误。

    修补 _format_message_content 将 invalid_tool_call 加入跳过列表。
    """
    try:
        from langchain_openai.chat_models import base as _lc_oai_base
        _orig = _lc_oai_base._format_message_content
        if getattr(_orig, "_patched_zte_invalid_tc", False):
            return

        def _patched_format_message_content(content, api="chat/completions", role=None):
            if content and isinstance(content, list):
                formatted_content = []
                for block in content:
                    if (
                        isinstance(block, dict)
                        and "type" in block
                        and block["type"]
                        in ("tool_use", "thinking", "reasoning_content", "invalid_tool_call")
                    ):
                        continue
                    # 其余逻辑与原始 _format_message_content 一致
                    from langchain_openai.chat_models.base import (
                        is_data_content_block,
                        convert_to_openai_data_block,
                    )
                    if (
                        isinstance(block, dict)
                        and is_data_content_block(block)
                        and not (
                            api == "responses" and str(role or "").lower().startswith("ai")
                        )
                    ):
                        formatted_content.append(convert_to_openai_data_block(block, api=api))
                    elif (
                        isinstance(block, dict)
                        and block.get("type") == "image"
                        and (source := block.get("source"))
                        and isinstance(source, dict)
                    ):
                        if source.get("type") == "base64" and (
                            (media_type := source.get("media_type"))
                            and (data := source.get("data"))
                        ):
                            formatted_content.append(
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{media_type};base64,{data}"
                                    },
                                }
                            )
                        elif source.get("type") == "url" and (url := source.get("url")):
                            formatted_content.append(
                                {"type": "image_url", "image_url": {"url": url}}
                            )
                        else:
                            continue
                    else:
                        formatted_content.append(block)
            else:
                formatted_content = content
            return formatted_content

        _patched_format_message_content._patched_zte_invalid_tc = True
        _lc_oai_base._format_message_content = _patched_format_message_content
        logger.info("[DeepFactory] 已修补 _format_message_content — 过滤 invalid_tool_call 块")
    except Exception as e:
        logger.warning(f"[DeepFactory] 修补 _format_message_content 失败: {e}")


def _patch_skills_reload_on_change():
    """技能目录集合变化时强制 SkillsMiddleware 重新扫描技能清单。

    deepagents 的 SkillsMiddleware 只要 state 里已有 skills_metadata 就永久跳过
    加载（before_agent 直接返回 None）。会话进行中安装/卸载技能后，系统提示里的
    技能清单仍是旧值：agent 会认为新技能"未启用"，即使 /skills/ 下已有对应目录。

    这里在执行前比对 /skills/ 当前目录集合与 state 中已加载技能的目录集合，
    不一致时重新扫描并覆盖 skills_metadata（含新增/删除/下架）。
    """
    try:
        from deepagents.middleware import skills as _skills_mod
        from deepagents.middleware.skills import SkillsMiddleware, SkillsStateUpdate

        if getattr(SkillsMiddleware, "_zte_reload_on_change", False):
            return

        def _entry_dir_names(entries) -> set:
            names = set()
            for e in entries or []:
                if not e.get("is_dir"):
                    continue
                name = Path(str(e.get("path") or "")).name
                if name:
                    names.add(name)
            return names

        def _loaded_dir_names(metadata) -> set:
            names = set()
            for item in metadata or []:
                try:
                    names.add(Path(str(item.get("path") or "")).parent.name)
                except (OSError, ValueError):
                    continue
            return names

        def _backend_ls_dirs(backend, path) -> set | None:
            try:
                result = backend.ls(path)
            except Exception:
                return None
            if result is None or getattr(result, "error", None):
                return None
            return _entry_dir_names(getattr(result, "entries", None))

        async def _backend_als_dirs(backend, path) -> set | None:
            try:
                result = await backend.als(path)
            except Exception:
                return None
            if result is None or getattr(result, "error", None):
                return None
            return _entry_dir_names(getattr(result, "entries", None))

        def _current_dirs(mw, backend) -> set | None:
            # 优先按 sources 前缀列目录；backend 直接以技能目录为根时回退 ls("/")
            current: set = set()
            found_any = False
            for source_path in mw.sources:
                dirs = _backend_ls_dirs(backend, source_path)
                if dirs is not None:
                    found_any = True
                    current |= dirs
            if not found_any:
                dirs = _backend_ls_dirs(backend, "/")
                if dirs is not None:
                    found_any = True
                    current |= dirs
            return current if found_any else None

        async def _acurrent_dirs(mw, backend) -> set | None:
            current: set = set()
            found_any = False
            for source_path in mw.sources:
                dirs = await _backend_als_dirs(backend, source_path)
                if dirs is not None:
                    found_any = True
                    current |= dirs
            if not found_any:
                dirs = await _backend_als_dirs(backend, "/")
                if dirs is not None:
                    found_any = True
                    current |= dirs
            return current if found_any else None

        def _sync_update(mw, backend):
            all_skills: dict = {}
            errors: list = []
            for source_path in mw.sources:
                found, err = _skills_mod._list_skills_with_errors(backend, source_path)
                if err is not None:
                    errors.append(err)
                for skill in found:
                    all_skills[skill["name"]] = skill
            if not all_skills and errors:
                # backend 直接以技能目录为根时 sources 前缀不存在，回退根路径
                found, err = _skills_mod._list_skills_with_errors(backend, "/")
                if err is None:
                    errors = []
                for skill in found:
                    all_skills[skill["name"]] = skill
            update = SkillsStateUpdate(skills_metadata=list(all_skills.values()))
            if errors:
                update["skills_load_errors"] = errors
            return update

        async def _async_update(mw, backend):
            all_skills: dict = {}
            errors: list = []
            for source_path in mw.sources:
                found, err = await _skills_mod._alist_skills_with_errors(backend, source_path)
                if err is not None:
                    errors.append(err)
                for skill in found:
                    all_skills[skill["name"]] = skill
            if not all_skills and errors:
                found, err = await _skills_mod._alist_skills_with_errors(backend, "/")
                if err is None:
                    errors = []
                for skill in found:
                    all_skills[skill["name"]] = skill
            update = SkillsStateUpdate(skills_metadata=list(all_skills.values()))
            if errors:
                update["skills_load_errors"] = errors
            return update

        _orig_before = SkillsMiddleware.before_agent
        _orig_abefore = SkillsMiddleware.abefore_agent

        def before_agent(self, state, runtime, config):
            if "skills_metadata" not in state:
                return _orig_before(self, state, runtime, config)
            try:
                backend = self._get_backend(state, runtime, config)
                current = _current_dirs(self, backend, _backend_ls_dirs)
                loaded = _loaded_dir_names(state.get("skills_metadata"))
                # 空集合视为挂载异常，不重载，避免把技能清单清空
                if not current or current == loaded:
                    return None
                return _sync_update(self, backend)
            except Exception as e:
                logger.warning(f"[DeepFactory] 技能清单重载检查失败: {e}")
                return None

        async def abefore_agent(self, state, runtime, config):
            if "skills_metadata" not in state:
                return await _orig_abefore(self, state, runtime, config)
            try:
                backend = self._get_backend(state, runtime, config)
                current = await _acurrent_dirs(self, backend)
                loaded = _loaded_dir_names(state.get("skills_metadata"))
                if not current or current == loaded:
                    return None
                return await _async_update(self, backend)
            except Exception as e:
                logger.warning(f"[DeepFactory] 技能清单重载检查失败: {e}")
                return None

        SkillsMiddleware.before_agent = before_agent
        SkillsMiddleware.abefore_agent = abefore_agent
        SkillsMiddleware._zte_reload_on_change = True
        logger.info("[DeepFactory] 已修补 SkillsMiddleware — 技能目录变化时自动重载")

        # ── 技能清单补充中文名 ──────────────────────────────────────────
        # deepagents 只展示 SKILL.md 的英文 name；market 包的中文名在
        # .install-meta.json（上传时填写）。这里在加载技能时把中文名拼进
        # description，使 agent 同时看到 中文名 / 英文名 / 编号（路径）。
        from deep_agent_server.skills.dirs import INSTALL_META_FILENAME

        def _meta_path_for(skill) -> str:
            p = str(skill.get("path") or "")
            if not p:
                return ""
            return str(Path(p).parent).rstrip("/") + "/" + INSTALL_META_FILENAME

        def _apply_aliases(skill, raw) -> None:
            """把中文名与技能编号拼进 description，使 agent 能关联三种标识。"""
            if not raw:
                return
            try:
                text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
                meta = json.loads(text)
            except (ValueError, UnicodeDecodeError):
                return
            if not isinstance(meta, dict):
                return
            name = str(skill.get("name") or "")
            aliases = []
            zh = str(meta.get("name_zh") or "").strip()
            if zh and zh != name:
                aliases.append(f"中文名：{zh}")
            pid = str(meta.get("package_id") or meta.get("skill_id") or "").strip()
            if pid and pid != name:
                aliases.append(f"技能编号：{pid}")
            if aliases:
                skill["description"] = (
                    "（" + "；".join(aliases) + "）" + str(skill.get("description") or "")
                )

        def _decorate_sync(backend, skills) -> None:
            if not skills:
                return
            paths = [_meta_path_for(s) for s in skills]
            if not any(paths):
                return
            try:
                responses = backend.download_files([p for p in paths if p])
            except Exception:
                return
            by_path = {}
            for r in responses or []:
                path = getattr(r, "path", None)
                content = getattr(r, "content", None)
                if path and content:
                    by_path[path] = content
            for s in skills:
                _apply_aliases(s, by_path.get(_meta_path_for(s)))

        async def _decorate_async(backend, skills) -> None:
            if not skills:
                return
            paths = [_meta_path_for(s) for s in skills]
            if not any(paths):
                return
            try:
                responses = await backend.adownload_files([p for p in paths if p])
            except Exception:
                return
            by_path = {}
            for r in responses or []:
                path = getattr(r, "path", None)
                content = getattr(r, "content", None)
                if path and content:
                    by_path[path] = content
            for s in skills:
                _apply_aliases(s, by_path.get(_meta_path_for(s)))

        _orig_list = getattr(_skills_mod, "_list_skills_with_errors", None)
        _orig_alist = getattr(_skills_mod, "_alist_skills_with_errors", None)

        if _orig_list is not None and not getattr(_orig_list, "_zte_zh_patched", False):
            def _list_with_zh(backend, source_path):
                found, err = _orig_list(backend, source_path)
                _decorate_sync(backend, found)
                return found, err

            _list_with_zh._zte_zh_patched = True
            _skills_mod._list_skills_with_errors = _list_with_zh

        if _orig_alist is not None and not getattr(_orig_alist, "_zte_zh_patched", False):
            async def _alist_with_zh(backend, source_path):
                found, err = await _orig_alist(backend, source_path)
                await _decorate_async(backend, found)
                return found, err

            _alist_with_zh._zte_zh_patched = True
            _skills_mod._alist_skills_with_errors = _alist_with_zh

        logger.info("[DeepFactory] 已修补 SkillsMiddleware — 技能清单附带中文名")
    except Exception as e:
        logger.warning(f"[DeepFactory] 修补 SkillsMiddleware 失败: {e}")


# 模块加载时执行补丁（确保 create_deep_agent 内部调用时已生效）
_patch_format_message_content()
_patch_token_counter_for_summarization()
_patch_summary_message_format()
_patch_acreate_summary_no_callbacks()
_patch_awrap_store_stream_writer()
_patch_acreate_summary_emit_compressing()
_patch_skills_reload_on_change()

BUILTIN_TOOLS = {
    "write_todos", "read_file", "write_file", "edit_file",
    "ls", "glob", "grep", "execute", "task",
}


class DeepAgentWrapper:
    """封装编译后的 Deep Agent 图及关联 store。"""

    def __init__(
        self,
        agent,
        store=None,
        mcp_tools=None,
        config_changes=None,
        html_share_links=None,
        html_share_locks=None,
        announced_html_shares=None,
    ):
        self.agent = agent
        self.store = store
        self.mcp_tools = mcp_tools or []
        # 同 session 内 skill/MCP 变更标记，runner 用于清状态 + 通知 agent
        self.config_changes = config_changes or {}
        # HTML 分享链接缓存：virtual_path → share_url，跨 run 持久（随 session 缓存生命周期）。
        # 由工厂创建并同时交给 HtmlShareLinkMiddleware，两者共享同一 dict ——
        # 中间件在工具返回前写入，adapter 的 _schedule_html_share 命中即跳过。
        self.html_share_links: Dict[str, str] = (
            html_share_links if html_share_links is not None else {}
        )
        # 已推送给 agent 的 virtual_path 集合，防止跨 run 重复注入
        self._announced_html_shares: set = (
            announced_html_shares if announced_html_shares is not None else set()
        )
        # 分享 token 创建锁：f"{user_id}:{virtual_path}" → asyncio.Lock。
        # 串行化「查重 + 插入」，防止同路径删除再生成（连续 write_file）时
        # 并发异步任务各自创建 token 导致同一文件产生多个分享链接
        self._html_share_locks: dict = (
            html_share_locks if html_share_locks is not None else {}
        )


async def create_deep_agent_instance(
    user_id: str,
    session_id: str,
    user_agent: dict,
    mcp_tools: list,
    checkpointer=None,
    store=None,
    selected_skill_ids: Optional[List[str]] = None,
    disabled_skill_labels: Optional[List[str]] = None,
    *,
    agent_kind: str = "default",
    skip_sandbox: bool = False,
    user_token: Optional[str] = None,
    llm=None,
    agent_skills_root: Optional[Path] = None,
    agent_id_for_sandbox: Optional[str] = None,
    config_changes: Optional[Dict[str, bool]] = None,
) -> Tuple[Optional[DeepAgentWrapper], Optional[str]]:
    """
    创建 Deep Agent 实例。

    使用官方 SkillsMiddleware：skills=["/skills/"]
    渐进式披露由 SkillsMiddleware 自动处理。

    - selected_skill_ids：用户勾选的 skill ID 列表，控制 /skills/ 的可见性
    - skip_sandbox=True：warmup 阶段跳过 Docker 拉起，execute 暂不可用
    - config_changes：同 session 内 skill/MCP 变更标记，传给 runner 清状态+通知
    """
    try:
        model = llm or create_agent_llm()
        # 订阅智能体：基础提示词（不含 Role 身份） + 自定义提示词
        # 默认智能体：完整提示词（含 Role 智搭身份）
        if agent_kind == "subscription":
            base_prompt = build_base_system_prompt(user_id=user_id, user_token=user_token)
            custom_prompt = user_agent.get("system_prompt", "").strip()
            system_prompt = base_prompt + "\n\n# 智能体设定\n" + custom_prompt if custom_prompt else base_prompt
        else:
            system_prompt = build_system_prompt(user_id=user_id, user_token=user_token)
        if disabled_skill_labels:
            names = "、".join(str(n) for n in disabled_skill_labels if n)
            if names:
                system_prompt += (
                    "\n\n# 已下架技能\n"
                    "以下技能已被作者下架，你可以看到它们的名称，但已被禁用、无法访问："
                    f"{names}。"
                    "禁止读取这些技能的 SKILL.md 或任何技能文件，禁止按其指令行动，禁止调用它们。"
                    "若用户要求使用它们，请明确说明该技能已禁用、无法访问。"
                )
        config = user_agent.get("config") or {}
        sandbox_mode = False
        backend = build_backend_factory(
            user_id, session_id, store=store,
            agent_skills_root=agent_skills_root,
            selected_skill_ids=selected_skill_ids,
        )
        if not skip_sandbox:
            sandbox_backend_factory = await get_sandbox_backend_factory_if_enabled(
                user_id, session_id=session_id, store=store, user_token=user_token,
                agent_skills_root=agent_skills_root,
                agent_id=agent_id_for_sandbox,
                selected_skill_ids=selected_skill_ids,
            )
            if sandbox_backend_factory is not None:
                backend = sandbox_backend_factory(None)
                sandbox_mode = True

        subagents = config.get("subagents") or None
        if subagents is None:
            # 覆盖库默认 general-purpose（deepagents 检测到同名 spec 即跳过自动添加，
            # graph.py:693）：默认子智能体只有两句英文提示词，与平台规则脱节。
            # 此处注入继承主 agent 规则类的系统提示词（不含 Role 身份设定与
            # task 工具使用手册——子智能体无 task 工具）。
            # tools 不传键 → deepagents 默认继承主 agent 全部工具（graph.py:658）；
            # model / permissions / interrupt_on 同样默认继承。
            subagents = [
                {
                    "name": "general-purpose",
                    "description": (
                        "通用智能体：用于研究复杂问题、搜索文件和内容、执行多步任务。"
                        "继承主智能体的工具与技能。"
                    ),
                    "system_prompt": build_subagent_system_prompt(
                        user_id=user_id, user_token=user_token
                    ),
                    "skills": ["/skills/"],  # 显式 spec 不继承主 agent 的 skills 参数（graph.py:650）
                }
            ]
        interrupt_on = build_interrupt_on(config) if app_config.AGENT_RUNTIME == "deep" else None

        permissions = build_permissions()

        # 确保 model.profile 包含 max_input_tokens，使 SummarizationMiddleware
        # 使用基于分数的触发阈值（默认 85% 上下文窗口），而非回退到固定 token 数。
        # 用于测试上下文压缩功能。
        if not model.profile or "max_input_tokens" not in model.profile:
            from utils.context_size import DEFAULT_CONTEXT_WINDOW

            trigger_fraction = float(os.environ.get("COMPACTION_TRIGGER_FRACTION", "0.85"))
            # max_input_tokens = real_limit * trigger_fraction / 0.85
            # → trigger = max_input_tokens * 0.85 = real_limit * trigger_fraction
            effective_max = int(DEFAULT_CONTEXT_WINDOW * trigger_fraction / 0.85)
            model.profile = {"max_input_tokens": effective_max}

        # ContextOverflowGuardMiddleware 插入在模型与 SummarizationMiddleware 之间，
        # 将 API 级别的 BadRequestError（context length）转换为 ContextOverflowError，
        # 使 SummarizationMiddleware 的兜底摘要重试机制能正常触发。
        overflow_guard = ContextOverflowGuardMiddleware()

        # RateLimitMiddleware：429 限流时长时间退避重试，避免 agent 任务崩溃。
        # OpenAI client 内置 max_retries=10 处理短时波动（~55s），
        # 此中间件处理持续限流：30s→45s→67s→100s→120s(封顶)，直至 DEEP_TIMEOUT 触发。
        rate_limit_mw = RateLimitMiddleware(
            max_retries=50,           # 远超 DEEP_TIMEOUT 内可完成的重试次数
            base_wait_seconds=30.0,
            max_wait_seconds=120.0,
        )

        # ToolResultTTLMiddleware：每次模型调用前零成本清理过期/超量的工具结果
        # （替换为占位符），释放上下文窗口。已 spill 外置的结果不清理。
        ttl_mw = None
        if app_config.TOOL_RESULT_TTL_ENABLED:
            ttl_mw = ToolResultTTLMiddleware(
                gap_threshold_minutes=app_config.TOOL_RESULT_TTL_GAP_MINUTES,
                count_trigger=app_config.TOOL_RESULT_TTL_COUNT_TRIGGER,
                keep_recent=app_config.TOOL_RESULT_TTL_KEEP_RECENT,
            )

        # HtmlShareLinkMiddleware：工具写出完整 HTML 时同步创建分享链接
        # （含资源快照），并把链接追加到工具结果文本 —— Agent 本轮即可在
        # 最终回复中告知用户，无需等待下一轮 run 的回灌。
        # 缓存/锁容器在此创建并同时交给 DeepAgentWrapper，中间件与 adapter
        # 的自动分享共享同一份状态，重复触发命中缓存即返回。
        share_links: Dict[str, str] = {}
        share_locks: dict = {}
        announced_shares: set = set()
        html_share_mw = None
        if app_config.HTML_SHARE_AUTO_INJECT_ENABLED:
            html_share_mw = HtmlShareLinkMiddleware(
                user_id=user_id,
                session_id=session_id,
                share_links=share_links,
                share_locks=share_locks,
                announced=announced_shares,
            )

        main_middleware = (
            [overflow_guard, rate_limit_mw, ttl_mw]
            if ttl_mw is not None
            else [overflow_guard, rate_limit_mw]
        )
        if html_share_mw is not None:
            # 置于链首（最外层）：工具调用返回前完成链接注入
            main_middleware.insert(0, html_share_mw)

        # 子 agent 的 middleware 不继承主 agent（deepagents 对 SubAgent spec
        # 执行 subagent_middleware.extend(spec.get("middleware", []))），
        # 需要显式注入 TTL 中间件（子 agent 工具结果同样清理）与分享中间件
        # （子 agent 写出的 HTML 同样要拿到链接）。
        # 仅对 dict 型 SubAgent spec 注入（CompiledSubAgent/AsyncSubAgent 不适用）。
        _sub_middleware = [mw for mw in (html_share_mw, ttl_mw) if mw is not None]
        if _sub_middleware and isinstance(subagents, list):
            for _spec in subagents:
                if (
                    isinstance(_spec, dict)
                    and "runnable" not in _spec
                    and "graph_id" not in _spec
                ):
                    _existing = list(_spec.get("middleware") or [])
                    _spec["middleware"] = _existing + _sub_middleware

        agent = create_deep_agent(
            model=model,
            tools=mcp_tools or [],
            system_prompt=system_prompt,
            skills=["/skills/"],
            backend=backend,
            permissions=permissions,
            interrupt_on=interrupt_on,
            subagents=subagents,
            checkpointer=checkpointer,
            store=store,
            middleware=main_middleware,
        )
        logger.info(
            f"[DeepFactory] Agent 创建成功 session={session_id} "
            f"selected_skill_ids={selected_skill_ids}"
        )
        return DeepAgentWrapper(
            agent,
            store=store,
            mcp_tools=mcp_tools,
            config_changes=config_changes,
            html_share_links=share_links,
            html_share_locks=share_locks,
            announced_html_shares=announced_shares,
        ), None
    except SandboxUserLimitExceeded:
        raise
    except Exception as e:
        logger.error(f"[DeepFactory] 创建失败: {e}")
        import traceback
        traceback.print_exc()
        return None, f"创建 Deep Agent 失败: {e}"
