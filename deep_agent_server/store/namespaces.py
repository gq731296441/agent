"""LangGraph Store namespace 常量与 builder。"""

from typing import Tuple

# 全局 / 框架级
HITL_PENDING: Tuple[str, ...] = ("hitl_pending",)
TURN_ACTIVE: Tuple[str, ...] = ("turn_active",)
MICROCOMPACT: Tuple[str, ...] = ("microcompact_state",)
COMPACTION: Tuple[str, ...] = ("compaction_state",)

# Session 级 suffix（删除 session 时清理）
SESSION_NS_SUFFIXES = ("failure", "tool_ok", "retry", "tool_cache", "artifact", "filesystem")

NS_ARTIFACT = "artifact"
NS_USER_MEMORY = "memory"
NS_FILESYSTEM = "filesystem"


def artifact_namespace(user_id: str, session_id: str) -> Tuple[str, ...]:
    return (user_id, session_id, NS_ARTIFACT)


def user_memory_namespace(user_id: str) -> Tuple[str, ...]:
    return (user_id, NS_USER_MEMORY)


def filesystem_namespace(user_id: str, session_id: str) -> Tuple[str, ...]:
    return (user_id, session_id, NS_FILESYSTEM)


def session_store_namespaces(
    user_id: str,
    session_id: str,
    thread_id: str,
) -> list[tuple[tuple[str, ...], str]]:
    """返回 (namespace, key) 列表，供 session 清理使用。"""
    pairs: list[tuple[tuple[str, ...], str]] = [
        (MICROCOMPACT, thread_id),
        (COMPACTION, thread_id),
        (HITL_PENDING, thread_id),
    ]
    for suffix in SESSION_NS_SUFFIXES:
        pairs.append(((user_id, session_id, suffix), thread_id))
    return pairs
