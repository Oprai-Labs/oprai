"""Deep-link account linking (tg_link_tokens).

The web app (logged in) mints a single-use token bound to its account_id; the
user opens t.me/OpraiBot?start=<token>, and the bot consumes it to bind
tg_users.linked_account_id. Consumption is atomic and single-use: a token can
bind exactly one Telegram user, once, before it expires.
"""

from __future__ import annotations

import secrets

from app.db import pool

DEFAULT_TTL_MINUTES = 15


async def create_link_token(account_id: str, ttl_minutes: int = DEFAULT_TTL_MINUTES) -> str:
    """Mint a single-use link token for account_id; return the token string."""
    token = secrets.token_urlsafe(24)
    await pool().execute(
        """
        INSERT INTO tg_link_tokens (token, account_id, expires_at)
        VALUES ($1, $2::uuid, now() + ($3 || ' minutes')::interval)
        """,
        token,
        account_id,
        str(ttl_minutes),
    )
    return token


async def consume_link_token(token: str, telegram_id: int) -> str | None:
    """Atomically consume a valid, unused, unexpired token and bind the user.

    Returns the bound account_id, or None if the token is invalid/used/expired.
    The UPDATE ... WHERE used_at IS NULL RETURNING makes consumption single-use
    even under concurrent /start taps: only the first caller gets a row back.
    """
    async with pool().acquire() as con:
        async with con.transaction():
            row = await con.fetchrow(
                """
                UPDATE tg_link_tokens
                   SET used_at = now()
                 WHERE token = $1
                   AND used_at IS NULL
                   AND expires_at > now()
             RETURNING account_id
                """,
                token,
            )
            if row is None:
                return None
            account_id = str(row["account_id"])
            await con.execute(
                "UPDATE tg_users SET linked_account_id = $1::uuid WHERE telegram_id = $2",
                account_id,
                telegram_id,
            )
            return account_id


async def linked_account(telegram_id: int) -> str | None:
    row = await pool().fetchrow(
        "SELECT linked_account_id FROM tg_users WHERE telegram_id = $1", telegram_id
    )
    return str(row["linked_account_id"]) if row and row["linked_account_id"] else None
