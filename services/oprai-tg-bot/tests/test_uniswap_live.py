"""Uniswap (stock) swap path on Robinhood Chain.

Unit tests cover the transaction plan; the live test quotes real stock trades
through the gateway — including the SELL direction, which used to be refused
because stock decimals aren't in Relay's currency list.
"""

from __future__ import annotations

import random
import urllib.error
import urllib.request

import pytest

from app.config import settings
from app.db import close_pool, init_pool, pool, upsert_tg_user
from app.services import auth as auth_svc
from app.services import tokens as tok
from app.services import uniswap
from app.services import wallet as wallet_svc


def _reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


LIVE = _reachable(f"{settings.OPRAI_TG_SIGNER_URL.rstrip('/')}/health") and _reachable(
    f"{settings.GATEWAY_URL.rstrip('/')}/health"
)


# ── unit: how many transactions a trade takes ───────────────────────────────
def test_native_input_is_a_single_transaction():
    """Buying with ETH needs no approval and no permit."""
    assert uniswap.transaction_count({"needsPermit": False, "approval": None}) == 1


def test_erc20_input_adds_an_approval_transaction():
    """The permit is a signature, not a transaction — only approval adds one."""
    q = {"needsPermit": True, "approval": {"to": "0xToken", "data": "0x095ea7b3"}}
    assert uniswap.transaction_count(q) == 2


def test_summarize_surfaces_permit_and_approval_requirements():
    s = uniswap.summarize({
        "inputAmountDisplay": "25", "inputSymbol": "USDG",
        "outputAmountDisplay": "0.111", "outputSymbol": "NVDA",
        "needsPermit": True, "approval": {"to": "0x"}, "priceImpact": 0.05,
    })
    assert s["needs_permit"] is True and s["has_approval"] is True
    assert s["out_amount"] == "0.111"


# ── live: real quotes, both directions ──────────────────────────────────────
@pytest.fixture
async def db():
    await init_pool()
    yield
    await close_pool()


@pytest.mark.skipif(not LIVE, reason="signer/gateway not reachable")
@pytest.mark.asyncio
async def test_live_stock_quotes_both_directions(db):
    tg_id = random.randint(10_000_000_000, 99_999_999_999)
    await upsert_tg_user(tg_id, "pytest-uni")
    try:
        addr = await wallet_svc.wallet_address(tg_id)
        jwt = await auth_svc.get_jwt(tg_id)
        nvda = (await tok.resolve("NVDA"))[0]["address"]
        usdg = (await tok.resolve("USDG"))[0]["address"]

        # BUY with native ETH — no approval, no permit
        buy = await uniswap.quote(jwt, uniswap.build_params(
            origin_currency=uniswap.NATIVE, destination_currency=nvda,
            amount="0.01", sender=addr))
        b = uniswap.summarize(buy)
        assert float(b["out_amount"]) > 0
        assert b["needs_permit"] is False and b["has_approval"] is False
        assert uniswap.transaction_count(buy) == 1

        # SELL a stock — this direction used to fail outright
        sell = await uniswap.quote(jwt, uniswap.build_params(
            origin_currency=nvda, destination_currency=usdg,
            amount="0.1", sender=addr))
        s = uniswap.summarize(sell)
        assert float(s["out_amount"]) > 0, "selling a stock must be quotable"
        assert s["needs_permit"] is True, "an ERC-20 input needs Permit2"
    finally:
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg_id)
