"""Tests for the /send transfer path (Robinhood Chain).

Pure-unit parts (calldata encoding) always run; the live parts need the signer
+ Postgres + a reachable Robinhood RPC and verify against real chain state.
"""

from __future__ import annotations

import random
import urllib.error
import urllib.request

import pytest

from app.config import settings
from app.db import close_pool, init_pool, pool, upsert_tg_user
from app.handlers.send import _resolve_recipient
from app.services import evm
from app.services import portfolio as pf
from app.services import wallet as wallet_svc


def _reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


LIVE = _reachable(f"{settings.OPRAI_TG_SIGNER_URL.rstrip('/')}/health")


def test_erc20_transfer_calldata():
    """transfer(address,uint256) — selector + 32-byte padded args."""
    data = evm.encode_erc20_transfer(
        "0x000000000000000000000000000000000000dEaD", 1_000_000
    )
    assert data.startswith("0xa9059cbb")
    assert len(data) == 2 + 8 + 64 + 64
    assert data[10:74].endswith("000000000000000000000000000000000000dead")
    assert int(data[74:], 16) == 1_000_000


@pytest.fixture
async def db():
    await init_pool()
    yield
    await close_pool()


@pytest.mark.skipif(not LIVE, reason="signer/chain not reachable")
@pytest.mark.asyncio
async def test_resolve_recipient(db):
    tg_id = random.randint(10_000_000_000, 99_999_999_999)
    uname = f"pytest{tg_id % 100000}"
    await upsert_tg_user(tg_id, uname)
    try:
        addr = await wallet_svc.wallet_address(tg_id)

        # raw address passes through
        got, _ = await _resolve_recipient("0x000000000000000000000000000000000000dEaD")
        assert got == "0x000000000000000000000000000000000000dEaD"

        # @username resolves to that user's custodial wallet
        got, label = await _resolve_recipient(f"@{uname}")
        assert got == addr and uname in label

        # unknown username resolves to nothing (handler then refuses)
        got, _ = await _resolve_recipient("@definitely_not_a_user_zzz")
        assert got is None

        # garbage is not treated as an address
        got, _ = await _resolve_recipient("hello")
        assert got is None
    finally:
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg_id)


@pytest.mark.skipif(not LIVE, reason="signer/chain not reachable")
@pytest.mark.asyncio
async def test_build_transfer_and_balance_guard(db):
    """A fresh wallet has 0 ETH, so the cost guard must refuse the send."""
    tg_id = random.randint(10_000_000_000, 99_999_999_999)
    await upsert_tg_user(tg_id, "pytest-send")
    try:
        addr = await wallet_svc.wallet_address(tg_id)
        tx = await evm.build_transfer(
            addr, "0x000000000000000000000000000000000000dEaD", 10**15
        )
        assert tx["chain_id"] == str(evm.CHAIN_ID)
        assert int(tx["gas"]) >= evm.NATIVE_TRANSFER_GAS
        assert int(tx["max_fee_per_gas"]) > 0

        cost = evm.tx_cost_wei(tx)
        assert cost > 10**15  # value + fees
        balance = (await pf.native_balance(tg_id))["wei"]
        assert balance < cost, "fresh wallet must fail the affordability check"
    finally:
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg_id)
