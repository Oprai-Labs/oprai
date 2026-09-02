"""Deposit detection — money arriving without the user asking.

We run the chain's node, so this should be near-instant. The tests pin the
behaviour that matters: a first sighting is a baseline (not news), an increase
is a deposit, a decrease is the user's own spending, token transfers are read
from logs exactly once, and one busy address can't stall the watcher.
"""

from __future__ import annotations

import random
import urllib.error
import urllib.request

import pytest

from app.config import settings
from app.db import close_pool, init_pool, pool, upsert_tg_user
from app.services import deposits, evm
from app.services import wallet as wallet_svc


def _reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


LIVE = _reachable(f"{settings.OPRAI_TG_SIGNER_URL.rstrip('/')}/health")

# A real, very active Robinhood Chain address — used READ-ONLY, to prove the
# watcher stays bounded when a watched address is busy.
BUSY_ADDRESS = "0x8366a39cc670b4001a1121b8f6a443a643e40951"


def test_amount_is_formatted_by_the_token_s_own_decimals():
    """USDG is 6 decimals; printing it as 18 would understate it a millionfold."""
    assert deposits.Deposit(1, 25_000_000, 6, "USDG").display == "25 USDG"
    assert deposits.Deposit(1, 10**16, 18, "ETH").display == "0.01 ETH"


def test_work_per_cycle_is_capped():
    assert deposits.MAX_LOGS_PER_CYCLE > 0
    assert deposits.MAX_BLOCKS_PER_CYCLE > 0


@pytest.fixture
async def db():
    await init_pool()
    yield
    await close_pool()


@pytest.mark.skipif(not LIVE, reason="signer/chain not reachable")
@pytest.mark.asyncio
async def test_baseline_then_increase_then_decrease(db):
    tg_id = random.randint(10_000_000_000, 99_999_999_999)
    await upsert_tg_user(tg_id, "pytest-deposit")
    try:
        await wallet_svc.wallet_address(tg_id)  # a fresh, stable, empty wallet

        # first sighting records a baseline and says nothing — the money on an
        # imported wallet may have been there for weeks
        first = [d for d in await deposits.check_native() if d.telegram_id == tg_id]
        assert first == []

        # pretend we last saw less: the difference is a deposit
        await pool().execute(
            "UPDATE tg_balance_watch SET wei = 0 WHERE telegram_id = $1", tg_id
        )
        actual = evm.to_int(
            await evm.rpc("eth_getBalance", [await wallet_svc.wallet_address(tg_id), "latest"])
        )
        if actual == 0:
            # an empty wallet can't show an increase; assert the quiet path
            assert [d for d in await deposits.check_native() if d.telegram_id == tg_id] == []
        else:
            got = [d for d in await deposits.check_native() if d.telegram_id == tg_id]
            assert got and got[0].amount == actual

        # a decrease is the user spending, not news
        await pool().execute(
            "UPDATE tg_balance_watch SET wei = $2::numeric WHERE telegram_id = $1",
            tg_id, str(10**18),
        )
        assert [d for d in await deposits.check_native() if d.telegram_id == tg_id] == []
    finally:
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg_id)


@pytest.mark.skipif(not LIVE, reason="signer/chain not reachable")
@pytest.mark.asyncio
async def test_token_transfers_are_announced_once_and_stay_bounded(db):
    """A busy watched address must neither stall the cycle nor repeat itself."""
    tg_id = random.randint(10_000_000_000, 99_999_999_999)
    await upsert_tg_user(tg_id, "pytest-deposit-tok")
    await pool().execute(
        "INSERT INTO tg_wallets (telegram_id, chain, address, enc_key_ref) "
        "VALUES ($1, 'evm', $2, 'test-only-no-key')",
        tg_id, BUSY_ADDRESS,
    )
    try:
        head = evm.to_int(await evm.rpc("eth_blockNumber"))
        await deposits._set_cursor(head - 30)
        found = [d for d in await deposits.check_tokens() if d.telegram_id == tg_id]
        assert len(found) <= deposits.MAX_LOGS_PER_CYCLE
        for d in found:
            assert d.amount > 0 and d.symbol and d.tx_hash

        # Rewinding the cursor must not re-announce anything already seen. The
        # chain keeps moving, so the second pass legitimately picks up NEW
        # transfers — what must never reappear is a transfer from the first.
        await deposits._set_cursor(head - 30)
        again = [d for d in await deposits.check_tokens() if d.telegram_id == tg_id]
        assert not ({d.tx_hash for d in found} & {d.tx_hash for d in again})
    finally:
        await pool().execute("DELETE FROM tg_deposit_seen WHERE telegram_id = $1", tg_id)
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg_id)
