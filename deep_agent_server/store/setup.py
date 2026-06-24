"""AsyncPostgresStore 初始化（含 pgvector 不可用时的降级）。"""

from typing import Any, Optional, Tuple

from langgraph.store.postgres.aio import AsyncPostgresStore

from config import get_logger
from config.config import app_config

logger = get_logger(__name__)


def _is_pgvector_unavailable(exc: BaseException) -> bool:
    name = type(exc).__name__
    if name in ("FeatureNotSupported", "UndefinedFile"):
        return True
    msg = str(exc).lower()
    return "vector" in msg and ("extension" in msg or "not available" in msg)


def _build_index_config(embed_fn: Any) -> Optional[dict]:
    if not app_config.STORE_INDEX_ENABLED or embed_fn is None:
        return None
    return {
        "dims": app_config.STORE_EMBEDDING_DIMS,
        "embed": embed_fn,
        "fields": ["text"],
    }


async def create_kv_store(
    pool,
    embed_fn: Any = None,
) -> Tuple[AsyncPostgresStore, bool]:
    """
    创建并 setup AsyncPostgresStore。

    Returns:
        (store, semantic_search_enabled)
        semantic_search_enabled 为 False 表示 pgvector/embedding 不可用，已降级。
    """
    index_config = _build_index_config(embed_fn)
    if app_config.STORE_INDEX_ENABLED and index_config is None:
        logger.warning("【Store】STORE_INDEX_ENABLED=true 但 embedding 不可用，以无索引模式启动")

    kv_store = AsyncPostgresStore(conn=pool, index=index_config)
    try:
        await kv_store.setup()
        if index_config:
            logger.info("【Store】✅ pgvector 语义索引已启用")
            return kv_store, True
        return kv_store, False
    except Exception as e:
        if not index_config or not _is_pgvector_unavailable(e):
            raise
        logger.warning(
            "【Store】PostgreSQL 未安装 pgvector 扩展，语义检索已降级为 DB 关键词/最近 N 条。"
            " 安装 pgvector 后设置 STORE_INDEX_ENABLED=true 可启用向量检索。"
            f" 原始错误: {e}"
        )
        kv_store = AsyncPostgresStore(conn=pool, index=None)
        await kv_store.setup()
        return kv_store, False
