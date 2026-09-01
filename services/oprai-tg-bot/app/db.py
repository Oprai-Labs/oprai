"""asyncpg connection pool + tg_schema helpers.

The bot holds NO private keys; this pool only stores identity/link/audit rows
and references the signer's encrypted-key handles. All tables live in
tg_schema (see sql/schema.sql).
"""

from __future__ import annotations

import asyncpg

from app.config import settings

_pool: asyncpg.Pool | None = None


async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            settings.pg_dsn(),
            min_size=1,
            max_size=8,
            server_settings={"search_path": f"{settings.DB_SCHEMA},public"},
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("db pool not initialised — call init_pool() on startup")
    return _pool


async def upsert_tg_user(
    telegram_id: int, username: str | None
) -> asyncpg.Record:
    """Ensure a tg_users row exists; return it. Idempotent per telegram_id."""
    return await pool().fetchrow(
        """
        INSERT INTO tg_users (telegram_id, username)
        VALUES ($1, $2)
        ON CONFLICT (telegram_id)
        DO UPDATE SET username = EXCLUDED.username, last_seen_at = now()
        RETURNING telegram_id, linked_account_id, username, created_at
        """,
        telegram_id,
        username,
    )


async def audit(telegram_id: int, kind: str, meta: dict | None = None) -> None:
    import json

    await pool().execute(
        "INSERT INTO tg_audit (telegram_id, kind, meta) VALUES ($1, $2, $3)",
        telegram_id,
        kind,
        json.dumps(meta or {}),
    )
