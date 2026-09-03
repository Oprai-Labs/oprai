"""Buying credits with $OPRAI.

A top-up moves real money, so the tests are about the two ways that goes
wrong: crediting twice for one payment, and crediting a payment that never
actually moved anything.
"""

from __future__ import annotations

import asyncio
import random
import urllib.error
import urllib.request

import pytest

from app.config import settings
from app.db import close_pool, init_pool, pool, upsert_tg_user
from app.services import credits, tokens, topups
from app.services import wallet as wallet_svc


def _reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


LIVE = _reachable(f"{settings.OPRAI_TG_SIGNER_URL.rstrip('/')}/health")


def _tx() -> str:
    return "0x" + "".join(random.choice("0123456789abcdef") for _ in range(64))


async def _clean(scope: int) -> None:
    await pool().execute("DELETE FROM tg_topups WHERE scope_id = $1", scope)
    await pool().execute("DELETE FROM tg_credit_ledger WHERE scope_id = $1", scope)
    await pool().execute("DELETE FROM tg_credits WHERE scope_id = $1", scope)


@pytest.mark.asyncio
async def test_one_payment_is_credited_once_however_many_attempts_settle_it():
    """The confirmation wait, a retry and the reconciler can all reach the
    same payment. Only one of them may grant."""
    await init_pool()
    scope, tg, tx = -random.randint(10**9, 10**10), random.randint(10**10, 10**11), _tx()
    try:
        assert await credits.record_payment(scope, True, tg, tx, 10**19, 100)
        assert not await credits.record_payment(scope, True, tg, tx, 10**19, 100)

        settled = await asyncio.gather(
            *[credits.settle_payment(tx, succeeded=True) for _ in range(5)]
        )
        assert sum(1 for s in settled if s is not None) == 1
        assert (await credits.balance(scope, True)).paid == 100
    finally:
        await _clean(scope)
        await close_pool()


@pytest.mark.asyncio
async def test_a_reverted_payment_grants_nothing():
    """A signature is not a success. Crediting on the send would hand out
    credits for a transfer that moved no tokens."""
    await init_pool()
    scope, tg, tx = -random.randint(10**9, 10**10), random.randint(10**10, 10**11), _tx()
    try:
        await credits.record_payment(scope, True, tg, tx, 10**19, 50)
        assert await credits.settle_payment(tx, succeeded=False) is None
        assert (await credits.balance(scope, True)).paid == 0
        # And it is closed, not left for the reconciler to retry for ever.
        assert not [p for p in await credits.pending_payments(0) if p["tx_hash"] == tx]
    finally:
        await _clean(scope)
        await close_pool()


@pytest.mark.asyncio
async def test_an_unconfirmed_payment_stays_owed():
    """The user's money is gone the moment it is sent. If we drop the record
    on a timeout, they paid for nothing."""
    await init_pool()
    scope, tg, tx = random.randint(10**10, 10**11), random.randint(10**10, 10**11), _tx()
    try:
        await credits.record_payment(scope, False, tg, tx, 10**19, 10)
        pending = await credits.pending_payments(older_than_seconds=0)
        assert any(p["tx_hash"] == tx for p in pending)

        # ...and settling it later still pays out.
        assert await credits.settle_payment(tx, succeeded=True) is not None
        assert (await credits.balance(scope, False)).paid == 10
    finally:
        await _clean(scope)
        await close_pool()


@pytest.mark.asyncio
async def test_group_ids_are_recognised_as_groups_when_settling():
    """settle_payment infers the scope kind from the id. Getting it wrong
    would compare a group's balance against a DM's free allowance."""
    await init_pool()
    group, dm = -random.randint(10**9, 10**10), random.randint(10**10, 10**11)
    tg = random.randint(10**10, 10**11)
    try:
        for scope, is_group in ((group, True), (dm, False)):
            tx = _tx()
            await credits.record_payment(scope, is_group, tg, tx, 10**18, 5)
            bal = await credits.settle_payment(tx, succeeded=True)
            assert bal.is_group is is_group
            assert bal.free_left == credits.free_allowance(is_group)
    finally:
        await _clean(group)
        await _clean(dm)
        await close_pool()


@pytest.mark.skipif(not LIVE, reason="signer not running")
@pytest.mark.asyncio
async def test_a_wallet_with_no_oprai_is_refused_before_signing():
    """Every refusal must happen before a transaction exists, so a failed
    top-up costs nothing — not even gas."""
    await init_pool()
    tg = random.randint(10**10, 10**11)
    await upsert_tg_user(tg, "topup_empty")
    try:
        with pytest.raises(topups.TopupError) as err:
            await topups.pay(tg, 5.0, scope_id=tg, is_group=False, credits=50)
        assert "hold" in str(err.value).lower()
        assert not await credits.pending_payments(0), "a payment was recorded"
    finally:
        await pool().execute("DELETE FROM tg_wallets WHERE telegram_id = $1", tg)
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg)
        await close_pool()


@pytest.mark.skipif(not LIVE, reason="signer not running")
@pytest.mark.asyncio
async def test_the_oprai_token_is_read_from_chain_not_assumed():
    """USDG is 6 decimals where nearly everything else is 18. Assuming the
    wrong one sends a million times the intended amount."""
    await init_pool()
    try:
        symbol = await tokens.read_symbol(settings.OPRAI_TG_TOKEN_ADDRESS)
        decimals = await tokens.read_decimals(settings.OPRAI_TG_TOKEN_ADDRESS)
        assert symbol == "OPRAI"
        assert decimals == 18
    finally:
        await close_pool()


def test_a_transfer_encodes_to_the_dev_wallet():
    data = topups._encode_transfer(settings.OPRAI_TG_DEV_WALLET, 10**18)
    assert data.startswith(topups.SEL_TRANSFER)
    assert settings.OPRAI_TG_DEV_WALLET.lower()[2:] in data
    assert len(data) == 10 + 128  # selector + two 32-byte words
