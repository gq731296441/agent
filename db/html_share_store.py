"""HTML 分享 Token 存储 —— 创建 / 校验 / 更新访问记录。"""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from db.database import db_manager
from config import get_logger

logger = get_logger(__name__)

TOKEN_BYTES = 32  # 256-bit 随机 token


async def _ensure_table():
    """自动建表（幂等），在首次调用时执行。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS html_share_tokens (
                id          SERIAL PRIMARY KEY,
                token       VARCHAR(64) UNIQUE NOT NULL,
                user_id     TEXT NOT NULL,
                session_id  TEXT NOT NULL,
                file_path   TEXT NOT NULL,
                created_at  TIMESTAMPTZ DEFAULT now(),
                expires_at  TIMESTAMPTZ,
                access_count INT DEFAULT 0,
                last_access TIMESTAMPTZ
            );
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_html_share_token
            ON html_share_tokens(token);
        """)


def _generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


async def create_share_token(
    user_id: str,
    session_id: str,
    file_path: str,
    *,
    expires_in_hours: Optional[int] = None,
) -> str:
    """创建分享 token，同一文件路径复用已有有效 token 避免重复生成。"""
    await _ensure_table()

    expires_at = None
    if expires_in_hours and expires_in_hours > 0:
        expires_at = datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)

    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        # 先查已有有效 token（未过期），命中则直接返回
        existing = await conn.fetchrow(
            """
            SELECT token FROM html_share_tokens
            WHERE user_id = $1 AND file_path = $2
              AND (expires_at IS NULL OR expires_at > now())
            LIMIT 1
            """,
            user_id, file_path,
        )
        if existing:
            logger.info(
                f"[HtmlShare] token reused user={user_id} path={file_path} "
                f"token={existing['token']}"
            )
            return existing["token"]

        # 无有效 token，创建新的
        token = _generate_token()
        await conn.execute(
            """
            INSERT INTO html_share_tokens (token, user_id, session_id, file_path, expires_at)
            VALUES ($1, $2, $3, $4, $5)
            """,
            token, user_id, session_id, file_path, expires_at,
        )

    logger.info(
        f"[HtmlShare] token created user={user_id} session={session_id} "
        f"path={file_path} expires_in_hours={expires_in_hours}"
    )
    return token


async def get_share_token(token: str) -> Optional[Dict]:
    """查询 token 记录，自动处理过期逻辑。"""
    await _ensure_table()

    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT token, user_id, session_id, file_path, created_at,
                   expires_at, access_count, last_access
            FROM html_share_tokens
            WHERE token = $1
            """,
            token,
        )
        if not row:
            return None

        record = dict(row)
        # 检查是否过期
        expires_at = record.get("expires_at")
        if expires_at is not None:
            now = datetime.now(timezone.utc)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if now > expires_at:
                logger.info(f"[HtmlShare] token expired: {token}")
                return None

        return record


async def touch_share_token(token: str) -> bool:
    """更新访问计数和最后访问时间。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE html_share_tokens
            SET access_count = access_count + 1,
                last_access = now()
            WHERE token = $1
            """,
            token,
        )
        return result == "UPDATE 1"
