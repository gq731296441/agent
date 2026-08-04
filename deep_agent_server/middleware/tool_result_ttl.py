"""工具结果 TTL 清理中间件 (ToolResultTTLMiddleware)

零成本的轻量工具结果清理（不调用 LLM），在每次模型调用前（abefore_model）
检测历史工具结果，将过期/超量的结果替换为占位符，释放上下文窗口。

两种触发机制（参考 Light Agent 的 MicroCompactMiddleware）：
1. 时间触发：距上次模型调用超过阈值分钟数 → 清理旧工具结果
   原理：长时间间隔意味着用户回到旧会话继续提问，旧工具结果已无参考价值
2. 数量触发：可压缩工具结果累积超过阈值 → 清理最旧的
   原理：工具调用过多会消耗大量上下文窗口

清理规则：
- 保留最近 keep_recent 个可压缩结果
- 已清理过的（占位符）消息跳过
- 无 id 的消息跳过（无法通过 add_messages 原地替换）
- 已 spill 外置的结果（内容含 "[工具结果已外置]" marker，即超长结果已
  落盘到 workspace 并保留 摘要+路径+ref_id）不清理 —— 内容本来就小，
  且保留路径让模型可 read_file 找回全文，替换为占位符反而丢失信息

与 SummarizationMiddleware（85% LLM 摘要）协作：
  ToolResultTTL（先，零成本）→ 清理工具结果 → 释放 token
  SummarizationMiddleware（后）→ 若仍超阈值 → LLM 摘要压缩
"""

import time
from typing import Any, Optional

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore

from config import get_logger
from deep_agent_server.artifact.spill import parse_spill_content
from utils.token_counter import token_counter

logger = get_logger(__name__)

# 工具结果被清理后的占位符
CLEARED_CONTENT = "[旧工具结果已清理]"

# Store 持久化 namespace（存储上次模型调用时间戳）
_STORE_NAMESPACE = ("tool_result_ttl",)


class ToolResultTTLMiddleware(AgentMiddleware):
    """工具结果 TTL 中间件 - 轻量级工具结果清理（Deep Agent）。

    在每次模型调用前检查工具结果的时间和数量，
    将过期或溢出的工具结果内容替换为占位符，释放上下文窗口。

    配置参数：
        gap_threshold_minutes: 时间触发阈值（分钟），默认 5.0
            距上次模型调用超过此值时，清理旧工具结果
        count_trigger: 数量触发阈值，默认 6
            可压缩工具结果数量超过此值时触发清理
        keep_recent: 保留最近的工具结果数量，默认 3
            无论哪种触发，始终保留最近 N 个工具结果
    """

    def __init__(
        self,
        gap_threshold_minutes: float = 5.0,
        count_trigger: int = 6,
        keep_recent: int = 15,
    ):
        super().__init__()
        self._gap_threshold_minutes = max(0.0, gap_threshold_minutes)
        self._count_trigger = max(0, count_trigger)
        # 至少保留 1 个，避免 slice(-0) 返回全部的边界问题
        self._keep_recent = max(1, keep_recent)

        logger.info(
            f"【ToolResultTTL】已初始化: "
            f"时间阈值={gap_threshold_minutes}min, "
            f"数量阈值={count_trigger}, "
            f"保留最近={keep_recent}"
        )

    # ==================== Store 读写辅助 ====================

    def _get_thread_id(self) -> Optional[str]:
        """从当前运行上下文获取 thread_id。"""
        try:
            config = get_config()
            return config.get("configurable", {}).get("thread_id")
        except Exception:
            pass
        return None

    async def _load_last_active(self, runtime: Runtime) -> Optional[float]:
        """从 store 加载上次模型调用时间戳。"""
        store: Optional[BaseStore] = getattr(runtime, "store", None)
        thread_id = self._get_thread_id()
        if not store or not thread_id:
            return None
        try:
            item = await store.aget(_STORE_NAMESPACE, thread_id)
            if item is not None:
                return item.value.get("last_active_at")
        except Exception as e:
            logger.debug(f"【ToolResultTTL】加载时间戳失败: {e}")
        return None

    async def _save_last_active(self, runtime: Runtime, timestamp: float) -> None:
        """将当前时间戳写入 store。"""
        store: Optional[BaseStore] = getattr(runtime, "store", None)
        thread_id = self._get_thread_id()
        if not store or not thread_id:
            return
        try:
            await store.aput(
                _STORE_NAMESPACE,
                thread_id,
                {"last_active_at": timestamp},
            )
        except Exception as e:
            logger.debug(f"【ToolResultTTL】保存时间戳失败: {e}")

    # ==================== 核心逻辑 ====================

    def _collect_compactable_tool_messages(
        self, messages: list
    ) -> list[ToolMessage]:
        """收集可压缩的 ToolMessage（按出现顺序）。

        跳过：已清理的占位符、无 id 的消息、已 spill 外置的结果。
        """
        results = []
        for msg in messages:
            if not isinstance(msg, ToolMessage):
                continue
            # 已清理的跳过
            if msg.content == CLEARED_CONTENT:
                continue
            # 无 id 的消息无法通过 add_messages 替换
            if not getattr(msg, "id", None):
                continue
            # 已 spill 外置的结果：内容已是 摘要+路径+ref_id 的小消息，
            # 保留路径让模型可 read_file 找回全文，不清理也不参与计数
            try:
                content = msg.content
                if isinstance(content, str) and parse_spill_content(content):
                    continue
            except Exception:
                pass
            results.append(msg)
        return results

    def _calculate_tokens(self, content: Any) -> int:
        """估算工具结果的 token 数。"""
        text = token_counter.stringify_content(content)
        return token_counter.count_text_tokens(text)

    async def abefore_model(
        self, state: AgentState, runtime: Runtime
    ) -> dict[str, Any] | None:
        """模型调用前检查并执行工具结果 TTL 清理。

        执行顺序：
        1. 收集可压缩的 ToolMessage
        2. 检查时间触发（距上次活跃超过阈值？）
        3. 检查数量触发（工具结果数量超过阈值？）
        4. 任一触发 → 保留最近 N 个，清理其余
        5. 更新 last_active 时间戳
        """
        messages = state.get("messages", [])
        now = time.time()

        compactable = self._collect_compactable_tool_messages(messages)
        if not compactable:
            await self._save_last_active(runtime, now)
            return None

        trigger_type = None

        # ===== 1. 时间触发检查 =====
        last_active = await self._load_last_active(runtime)
        if last_active is not None:
            gap_minutes = (now - last_active) / 60.0
            if gap_minutes >= self._gap_threshold_minutes:
                trigger_type = "time"
                logger.info(
                    f"【ToolResultTTL】⏰ 时间触发: "
                    f"距上次活跃 {gap_minutes:.1f} 分钟 "
                    f"(阈值 {self._gap_threshold_minutes} 分钟)"
                )

        # ===== 2. 数量触发检查 =====
        # 触发下限取 max(数量阈值, 保留数量)：keep_recent > count_trigger 时
        # 结果数在 (count_trigger, keep_recent] 区间内会"触发但零清理"，
        # 语义退化为"超过保留量才清理"，任何配置组合都自洽。
        count_limit = max(self._count_trigger, self._keep_recent)
        if trigger_type is None and len(compactable) > count_limit:
            trigger_type = "count"
            logger.info(
                f"【ToolResultTTL】📊 数量触发: "
                f"{len(compactable)} 个工具结果 "
                f"(阈值 {count_limit}, 保留最近 {self._keep_recent})"
            )

        if trigger_type is None:
            await self._save_last_active(runtime, now)
            return None

        # ===== 3. 确定保留集合（最近 N 个） =====
        keep_set = set(
            msg.id for msg in compactable[-self._keep_recent:]
        )

        # ===== 4. 构建替换消息 =====
        updates = []
        tokens_saved = 0
        cleared_tools = []

        for msg in compactable:
            if msg.id in keep_set:
                continue

            tokens_saved += self._calculate_tokens(msg.content)
            cleared_tools.append(getattr(msg, "name", "unknown"))

            updates.append(
                ToolMessage(
                    content=CLEARED_CONTENT,
                    tool_call_id=msg.tool_call_id,
                    name=getattr(msg, "name", None),
                    id=msg.id,
                )
            )

        if not updates:
            await self._save_last_active(runtime, now)
            return None

        # ===== 5. 日志 & 通知前端 =====
        kept_count = min(self._keep_recent, len(compactable))
        logger.info(
            f"【ToolResultTTL】✅ {trigger_type} 触发清理完成: "
            f"清理 {len(updates)} 个工具结果 (~{tokens_saved} tokens)，"
            f"保留最近 {kept_count} 个（已 spill 外置的结果不清理）"
        )

        writer = getattr(runtime, "stream_writer", None)
        if writer:
            try:
                writer(
                    {
                        "type": "status",
                        "message": (
                            f"清理过期工具结果 {len(updates)} 个"
                            f"(~{tokens_saved} tokens)..."
                        ),
                    }
                )
            except Exception:
                pass

        # ===== 6. 更新时间戳并返回状态更新 =====
        await self._save_last_active(runtime, now)

        # 返回相同 id 的 ToolMessage，add_messages reducer 会原地替换
        return {"messages": updates}
