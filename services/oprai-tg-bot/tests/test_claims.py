"""Sending to a handle: whose wallet it reaches, and what happens if there
isn't one yet.

Two separate hazards. A Telegram handle can change hands, and our stored copy
doesn't know — resolving `@name` to the person who *used* to own it sends money
to the wrong human. And a handle with no wallet behind it used to be a dead
end, so the money simply couldn't be sent; now it waits for a claim, which
introduces its own ways to go wrong: claimed twice, claimed by the wrong
person, or hanging over the sender's wallet for ever.
"""

from __future__ import annotations

import asyncio
import random

import pytest

from app.db import close_pool, init_pool, pool, upsert_tg_user
from app.handlers.send import _resolve_recipient
from app.services import claims


async def _wallet(telegram_id: int, address: str) -> None:
    await pool().execute(
        "INSERT INTO tg_wallets (telegram_id, chain, address, enc_key_ref) "
        "VALUES ($1, 'evm', $2, 'test') ON CONFLICT DO NOTHING",
        telegram_id, address,
    )


async def _cleanup(*ids: int) -> None:
    await pool().execute("DELETE FROM tg_claims WHERE from_telegram_id = ANY($1)", list(ids))
    await pool().execute("DELETE FROM tg_wallets WHERE telegram_id = ANY($1)", list(ids))
    await pool().execute("DELETE FROM tg_users WHERE telegram_id = ANY($1)", list(ids))


# ── whose wallet a handle means ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_recycled_handle_resolves_to_its_current_owner():
    """Someone renames, another account takes the freed handle. Our copy still
    had the old owner holding it, and `/send … @name` paid them — the wrong
    human, silently, with no error to notice."""
    await init_pool()
    old = random.randint(10**10, 10**11)
    new = old + 1
    handle = f"recycled_{random.randint(1000, 9999)}"
    try:
        await upsert_tg_user(old, handle)
        await _wallet(old, "0xAAaA000000000000000000000000000000000001")
        addr, _ = await _resolve_recipient(f"@{handle}")
        assert addr.lower().startswith("0xaaaa")

        # The handle changes hands.
        await upsert_tg_user(new, handle)
        await _wallet(new, "0xBBbB000000000000000000000000000000000002")

        held_by = await pool().fetchval(
            "SELECT count(*) FROM tg_users WHERE lower(username) = lower($1)", handle
        )
        assert held_by == 1, "two accounts still claim the same handle"

        addr, _ = await _resolve_recipient(f"@{handle}")
        assert addr.lower().startswith("0xbbbb"), "money would go to the old owner"
    finally:
        await _cleanup(old, new)
        await close_pool()


@pytest.mark.asyncio
async def test_the_database_refuses_to_hold_a_handle_twice():
    """Belt and braces: the application releases the handle, and the index
    makes it impossible to end up with two anyway."""
    import asyncpg

    await init_pool()
    a = random.randint(10**10, 10**11)
    b = a + 1
    handle = f"dupe_{random.randint(1000, 9999)}"
    try:
        await upsert_tg_user(a, handle)
        await pool().execute(
            "INSERT INTO tg_users (telegram_id, username) VALUES ($1, $2)", b, None
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await pool().execute(
                "UPDATE tg_users SET username = $2 WHERE telegram_id = $1", b, handle
            )
    finally:
        await _cleanup(a, b)
        await close_pool()


# ── claims ──────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_claim_only_pays_the_handle_it_was_sent_to():
    """The link is forwarded through chats we don't control. Binding to the
    handle means passing it on cannot redirect the money."""
    await init_pool()
    sender = random.randint(10**10, 10**11)
    try:
        await upsert_tg_user(sender, f"sender_{sender}")
        token, link = await claims.create(
            from_telegram_id=sender, to_username="@intended_x",
            symbol="NVDA", amount_base=5 * 10**18, decimals=18,
            token_address="0xd0601CE157",
        )
        assert token in link and link.startswith("https://t.me/")

        with pytest.raises(claims.ClaimError):
            await claims.take(token, "someone_else")
        with pytest.raises(claims.ClaimError):
            await claims.take(token, None)  # no handle at all

        row = await claims.take(token, "intended_x")
        assert row["symbol"] == "NVDA"
    finally:
        await _cleanup(sender)
        await close_pool()


@pytest.mark.asyncio
async def test_a_claim_pays_once_however_many_taps_arrive():
    """A link can be tapped twice in a second. The status change is the lock,
    so only one attempt may send."""
    await init_pool()
    sender = random.randint(10**10, 10**11)
    try:
        await upsert_tg_user(sender, f"sender_{sender}")
        token, _ = await claims.create(
            from_telegram_id=sender, to_username="@taps", symbol="ETH",
            amount_base=10**17, decimals=18, token_address=None,
        )
        results = await asyncio.gather(
            *[claims.take(token, "taps") for _ in range(5)],
            return_exceptions=True,
        )
        won = [r for r in results if not isinstance(r, Exception)]
        assert len(won) == 1, f"{len(won)} claims were paid for one transfer"
    finally:
        await _cleanup(sender)
        await close_pool()


@pytest.mark.asyncio
async def test_a_failed_transfer_releases_the_claim():
    """The usual failure is the sender having spent the funds. Consuming the
    claim would strand the money; releasing it lets them top up and resend."""
    await init_pool()
    sender = random.randint(10**10, 10**11)
    try:
        await upsert_tg_user(sender, f"sender_{sender}")
        token, _ = await claims.create(
            from_telegram_id=sender, to_username="@retry", symbol="USDG",
            amount_base=10 * 10**6, decimals=6, token_address="0x5fc5360d04",
        )
        await claims.take(token, "retry")
        await claims.mark_failed(token, "insufficient funds")
        row = await claims.get(token)
        assert row["status"] == "failed", "a failed transfer stayed 'claimed'"
    finally:
        await _cleanup(sender)
        await close_pool()


@pytest.mark.asyncio
async def test_an_unclaimed_transfer_expires_instead_of_hanging_forever():
    """The sender was told to keep the funds available. That can't be an
    open-ended obligation."""
    await init_pool()
    sender = random.randint(10**10, 10**11)
    try:
        await upsert_tg_user(sender, f"sender_{sender}")
        token, _ = await claims.create(
            from_telegram_id=sender, to_username="@ghost", symbol="ETH",
            amount_base=10**17, decimals=18, token_address=None,
        )
        await pool().execute(
            "UPDATE tg_claims SET expires_at = now() - interval '1 day' "
            "WHERE token = $1", token,
        )
        expired = await claims.expire_stale()
        assert any(e["token"] == token for e in expired)
        assert (await claims.get(token))["status"] == "expired"

        # And an expired link pays nobody.
        with pytest.raises(claims.ClaimError):
            await claims.take(token, "ghost")
    finally:
        await _cleanup(sender)
        await close_pool()


def test_amounts_are_shown_at_the_tokens_own_scale():
    assert claims.display(5 * 10**18, 18) == "5"
    assert claims.display(10 * 10**6, 6) == "10"
    assert claims.display(1, 18) == "0"  # dust rounds to nothing at 6 places
