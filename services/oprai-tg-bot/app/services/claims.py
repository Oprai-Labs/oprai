"""Sending to someone who hasn't used the bot yet.

Telegram will not let a bot open a conversation, so we cannot tell a stranger
that money is waiting for them. The only thing that reaches them is a link the
sender forwards — so that is what this builds.

Nothing is escrowed. The sender's funds stay in the sender's wallet until the
claim runs, which means a claim can fail because they were spent in the
meantime. That is honest and recoverable; a pooled custody wallet holding other
people's money until they show up is neither, and nobody asked for one.

The claim is bound to the HANDLE the sender named, not to whoever holds the
link. Forwarding it to the wrong chat cannot redirect the money.
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from decimal import Decimal

from app.config import settings
from app.db import pool

# Long enough that someone can be nudged twice, short enough that a sender's
# committed intent doesn't hang over their wallet indefinitely.
CLAIM_TTL_DAYS = 7


class ClaimError(RuntimeError):
    pass


def _link(token: str) -> str:
    bot = settings.OPRAI_TG_BOT_USERNAME or "Oprai_Labs_Bot"
    return f"https://t.me/{bot}?start=claim_{token}"


def display(amount_base: int, decimals: int) -> str:
    value = Decimal(amount_base) / (10 ** int(decimals))
    return f"{value:,.6f}".rstrip("0").rstrip(".") or "0"


async def create(
    *,
    from_telegram_id: int,
    to_username: str,
    symbol: str,
    amount_base: int,
    decimals: int,
    token_address: str | None,
) -> tuple[str, str]:
    """Record the sender's intent. -> (token, forwardable link)."""
    token = secrets.token_urlsafe(16)
    await pool().execute(
        """
        INSERT INTO tg_claims (token, from_telegram_id, to_username, token_address,
                               symbol, amount_base, decimals, expires_at)
        VALUES ($1, $2, $3, $4, $5, $6::numeric, $7, now() + $8::interval)
        """,
        token, from_telegram_id, to_username.lstrip("@").lower(), token_address,
        symbol, str(amount_base), decimals, timedelta(days=CLAIM_TTL_DAYS),
    )
    return token, _link(token)


async def pending_for(username: str) -> list[dict]:
    rows = await pool().fetch(
        """
        SELECT * FROM tg_claims
         WHERE lower(to_username) = lower($1)
           AND status = 'pending' AND expires_at > now()
         ORDER BY created_at
        """,
        username.lstrip("@"),
    )
    return [dict(r) for r in rows]


async def get(token: str) -> dict | None:
    row = await pool().fetchrow("SELECT * FROM tg_claims WHERE token = $1", token)
    return dict(row) if row else None


async def take(token: str, claimer_username: str | None) -> dict:
    """Claim it, once.

    The status change is the lock: only the attempt that moves the row out of
    'pending' gets to send anything, so two taps on the same link cannot pay
    twice. Raises ClaimError with something worth showing a person.
    """
    row = await get(token)
    if row is None:
        raise ClaimError("that link isn't valid.")
    if row["status"] == "claimed":
        raise ClaimError("that has already been claimed.")
    if row["status"] == "cancelled":
        raise ClaimError("the sender cancelled that transfer.")
    if row["status"] != "pending":
        raise ClaimError("that link has expired.")
    if row["expires_at"].timestamp() <= __import__("time").time():
        await _finish(token, "expired")
        raise ClaimError("that link has expired.")

    # Bound to the handle the sender named — a forwarded link cannot redirect
    # the money to whoever happens to open it.
    if (claimer_username or "").lower() != row["to_username"].lower():
        raise ClaimError(
            f"this was sent to @{row['to_username']}, so only that account can "
            "claim it."
        )

    claimed = await pool().fetchrow(
        "UPDATE tg_claims SET status = 'claimed', updated_at = now() "
        "WHERE token = $1 AND status = 'pending' RETURNING *",
        token,
    )
    if claimed is None:
        raise ClaimError("that has already been claimed.")
    return dict(claimed)


async def cancel(token: str, from_telegram_id: int) -> dict:
    """Take it back, before anyone claims it.

    A claim committed the sender to keeping the funds available for a week with
    no way to change their mind — the only "cancel" was to spend the money so
    the claim would fail, which is a workaround, not a decision. Only the
    sender can do this, and only while it is still unclaimed: the same status
    change that pays a claim is the one that blocks a cancel, so a link being
    tapped at this exact moment wins or loses cleanly, never both.
    """
    row = await pool().fetchrow(
        "UPDATE tg_claims SET status = 'cancelled', updated_at = now() "
        " WHERE token = $1 AND from_telegram_id = $2 AND status = 'pending' "
        "RETURNING token, to_username, symbol, amount_base, decimals",
        token, from_telegram_id,
    )
    if row is None:
        existing = await get(token)
        if existing is None or existing["from_telegram_id"] != from_telegram_id:
            raise ClaimError("that isn't one of your transfers.")
        if existing["status"] == "claimed":
            raise ClaimError("too late — that one has already been claimed.")
        raise ClaimError("that transfer is no longer pending.")
    return dict(row)


async def pending_from(from_telegram_id: int) -> list[dict]:
    """What a sender still has committed, so they can see it and take it back."""
    rows = await pool().fetch(
        "SELECT token, to_username, symbol, amount_base, decimals, expires_at "
        "  FROM tg_claims WHERE from_telegram_id = $1 AND status = 'pending' "
        "   AND expires_at > now() ORDER BY created_at",
        from_telegram_id,
    )
    return [dict(r) for r in rows]


async def mark_failed(token: str, reason: str = "") -> None:
    """Put it back to nobody: the transfer didn't happen, so the claim didn't
    either, and the sender is free of it."""
    await pool().execute(
        "UPDATE tg_claims SET status = 'failed', updated_at = now() WHERE token = $1",
        token,
    )


async def record_sent(token: str, tx_hash: str) -> None:
    await pool().execute(
        "UPDATE tg_claims SET tx_hash = $2, updated_at = now() WHERE token = $1",
        token, tx_hash,
    )


async def _finish(token: str, status: str) -> None:
    await pool().execute(
        "UPDATE tg_claims SET status = $2, updated_at = now() WHERE token = $1",
        token, status,
    )


async def expire_stale() -> list[dict]:
    """Close claims nobody came for, so a sender's committed intent doesn't
    hang over their wallet for ever."""
    rows = await pool().fetch(
        "UPDATE tg_claims SET status = 'expired', updated_at = now() "
        "WHERE status = 'pending' AND expires_at <= now() "
        "RETURNING token, from_telegram_id, to_username, symbol, amount_base, decimals"
    )
    return [dict(r) for r in rows]
