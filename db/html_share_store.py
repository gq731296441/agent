"""HTML 分享 Token 存储 —— 创建 / 校验 / 更新访问记录 / 文件身份绑定。

同一文件唯一 token 约束：同一文件全生命周期（含改名、编辑）只对应一个分享 token，
由 create_or_reuse_share_token 的四级复用链保证（路径 → 内容 sha → 唯一 basename → 新建）。
"""

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from db.database import db_manager
from config import get_logger

logger = get_logger(__name__)

TOKEN_BYTES = 32  # 256-bit 随机 token


async def _ensure_table():
    """自动建表（幂等）与列迁移，在首次调用时执行。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS html_share_tokens (
                id          SERIAL PRIMARY KEY,
                token       VARCHAR(64) UNIQUE NOT NULL,
                user_id     TEXT NOT NULL,
                session_id  TEXT NOT NULL,
                file_path   TEXT NOT NULL,
                file_key    TEXT,
                last_source_sha256 TEXT,
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
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_html_share_user
            ON html_share_tokens(user_id, session_id);
        """)
        # 存量库列迁移（幂等）
        await conn.execute(
            "ALTER TABLE html_share_tokens ADD COLUMN IF NOT EXISTS file_key TEXT"
        )
        await conn.execute(
            "ALTER TABLE html_share_tokens "
            "ADD COLUMN IF NOT EXISTS last_source_sha256 TEXT"
        )
        # 快照缺失资源状态：JSON [[虚拟路径, size:mtime_ns], ...]
        # NULL = 旧数据未记录（访问时重建一次以自愈）
        await conn.execute(
            "ALTER TABLE html_share_tokens "
            "ADD COLUMN IF NOT EXISTS snapshot_missing_assets TEXT"
        )


def _generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


async def create_share_token(
    user_id: str,
    session_id: str,
    file_path: str,
    *,
    expires_in_hours: Optional[int] = None,
) -> str:
    """创建分享 token（按 (user_id, file_path) 复用），兼容旧调用（adapter 自动分享）。"""
    token, _reused, _reason = await create_or_reuse_share_token(
        user_id, session_id, file_path, None,
        expires_in_hours=expires_in_hours,
    )
    return token


async def create_or_reuse_share_token(
    user_id: str,
    session_id: str,
    file_path: str,
    sha256: Optional[str],
    *,
    expires_in_hours: Optional[int] = None,
    basename_unique: bool = False,
) -> Tuple[str, bool, str]:
    """创建分享 token，保证同一文件唯一 token（四级复用链）。

    Returns:
        (token, reused, reason)  reason: 'path' | 'sha' | 'basename' | ''（新建）

    复用链：
      ① (user_id, file_path) 有效 token 命中 → 复用（路径身份，内容变化也复用，快照重建更新）
      ② 未命中 → 同 session、同 basename、last_source_sha256 相等的 token → 复用 + 迁移路径
         （改名/移动且内容未变）
      ③ 未命中 → 同 session、同 basename 的 token（调用方已确认工作区内该 basename 唯一，
         且旧 file_path 不存在）→ 复用 + 迁移路径（改名+编辑，唯一文件判定）
      ④ 新建（file_key = 当前 file_path）
    """
    await _ensure_table()

    expires_at = None
    if expires_in_hours and expires_in_hours > 0:
        expires_at = datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)

    pool = await db_manager.get_pool()
    basename = (file_path or "").rsplit("/", 1)[-1]

    async with pool.acquire() as conn:
        # ① 路径复用
        existing = await conn.fetchrow(
            """
            SELECT token, file_key FROM html_share_tokens
            WHERE user_id = $1 AND file_path = $2
              AND (expires_at IS NULL OR expires_at > now())
            LIMIT 1
            """,
            user_id, file_path,
        )
        if existing:
            # 旧行兼容：backfill file_key
            if not existing["file_key"]:
                await conn.execute(
                    "UPDATE html_share_tokens SET file_key = $1 WHERE token = $2",
                    file_path, existing["token"],
                )
            logger.info(
                f"[HtmlShare] token reused(user+path) user={user_id} path={file_path} "
                f"token={existing['token']}"
            )
            return existing["token"], True, "path"

        # ② 内容身份复用（改名/移动且内容未变——内容 sha 即文件身份，与名字无关）
        if sha256:
            hit = await conn.fetchrow(
                """
                SELECT token FROM html_share_tokens
                WHERE user_id = $1 AND session_id = $2
                  AND last_source_sha256 = $3
                  AND (expires_at IS NULL OR expires_at > now())
                ORDER BY created_at DESC
                LIMIT 1
                """,
                user_id, session_id, sha256,
            )
            if hit:
                await conn.execute(
                    "UPDATE html_share_tokens SET file_path = $1, "
                    "file_key = COALESCE(file_key, $1) WHERE token = $2",
                    file_path, hit["token"],
                )
                logger.info(
                    f"[HtmlShare] token reused(content) user={user_id} "
                    f"new_path={file_path} token={hit['token']}"
                )
                return hit["token"], True, "sha"

        # ③ 唯一 basename 复用（改名+编辑；调用方已确认唯一性）
        if basename_unique:
            rows = await conn.fetch(
                """
                SELECT token, file_path, file_key, last_source_sha256 FROM html_share_tokens
                WHERE user_id = $1 AND session_id = $2
                  AND (expires_at IS NULL OR expires_at > now())
                """,
                user_id, session_id,
            )
            hit = next((r for r in rows if (r["file_path"] or "").rsplit("/", 1)[-1] == basename), None)
            if hit:
                await conn.execute(
                    "UPDATE html_share_tokens SET file_path = $1, "
                    "file_key = COALESCE(file_key, $1) WHERE token = $2",
                    file_path, hit["token"],
                )
                logger.info(
                    f"[HtmlShare] token reused(basename) user={user_id} "
                    f"new_path={file_path} token={hit['token']}"
                )
                return hit["token"], True, "basename"

        # ④ 新建（同事务/同连接写入内容指纹：改名后按 sha 找回源文件依赖此列，
        #    自动创建路径不经过 update_share_source，必须在插入时就落库）
        token = _generate_token()
        await conn.execute(
            """
            INSERT INTO html_share_tokens
                (token, user_id, session_id, file_path, file_key,
                 last_source_sha256, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            token, user_id, session_id, file_path, file_path, sha256, expires_at,
        )

    logger.info(
        f"[HtmlShare] token created user={user_id} session={session_id} "
        f"path={file_path} expires_in_hours={expires_in_hours}"
    )
    return token, False, ""


async def update_share_source(
    token: str,
    file_path: str,
    sha256: Optional[str],
    *,
    missing_assets: Optional[List[Tuple[str, str]]] = None,
) -> bool:
    """更新分享源文件路径与内容指纹（路径迁移 / 快照重建后记录）。

    missing_assets 非 None 时同步记录快照构建时缺失的资源（含文件签名）：
    NULL 表示状态未知，访问分享页时会被动重建一次。
    """
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        if missing_assets is None:
            result = await conn.execute(
                """
                UPDATE html_share_tokens
                SET file_path = $1,
                    file_key = COALESCE(file_key, $1),
                    last_source_sha256 = $2
                WHERE token = $3
                """,
                file_path, sha256, token,
            )
        else:
            result = await conn.execute(
                """
                UPDATE html_share_tokens
                SET file_path = $1,
                    file_key = COALESCE(file_key, $1),
                    last_source_sha256 = $2,
                    snapshot_missing_assets = $4
                WHERE token = $3
                """,
                file_path, sha256, token,
                json.dumps(
                    [[vp, sig] for vp, sig in missing_assets],
                    ensure_ascii=False,
                ),
            )
        return result == "UPDATE 1"


async def get_share_token(token: str) -> Optional[Dict]:
    """查询 token 记录，自动处理过期逻辑。"""
    await _ensure_table()

    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT token, user_id, session_id, file_path, file_key,
                   last_source_sha256, snapshot_missing_assets, created_at,
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


async def delete_share_token(token: str, user_id: str) -> bool:
    """删除分享 token（校验归属）。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM html_share_tokens WHERE token = $1 AND user_id = $2",
            token, user_id,
        )
        return result == "DELETE 1"


async def delete_share_tokens_by_session(user_id: str, session_id: str) -> int:
    """删除某会话的全部分享 token（会话永久删除时联动）。"""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM html_share_tokens WHERE user_id = $1 AND session_id = $2",
            user_id, session_id,
        )
    try:
        return int(result.split()[-1])
    except (ValueError, AttributeError):
        return 0


async def list_share_tokens(
    user_id: str,
    session_id: Optional[str] = None,
) -> List[Dict]:
    """列出用户的分享 token（管理界面用）。"""
    await _ensure_table()

    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        if session_id:
            rows = await conn.fetch(
                """
                SELECT token, user_id, session_id, file_path, file_key,
                       last_source_sha256, created_at,
                       expires_at, access_count, last_access
                FROM html_share_tokens
                WHERE user_id = $1 AND session_id = $2
                ORDER BY created_at DESC
                """,
                user_id, session_id,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT token, user_id, session_id, file_path, file_key,
                       last_source_sha256, created_at,
                       expires_at, access_count, last_access
                FROM html_share_tokens
                WHERE user_id = $1
                ORDER BY created_at DESC
                """,
                user_id,
            )
    result = []
    now = datetime.now(timezone.utc)
    for row in rows:
        record = dict(row)
        expires_at = record.get("expires_at")
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        record["valid"] = not (expires_at is not None and now > expires_at)
        result.append(record)
    return result
