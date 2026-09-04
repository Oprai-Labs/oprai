"""Live test for read-only balances on Robinhood Chain.

Needs the signer (to create the wallet) + Postgres + a reachable Robinhood RPC.
A fresh custodial wallet has 0 ETH, so we assert the call returns a numeric
balance (the RPC path works), not a specific amount.

Run: cd services/oprai-tg-bot && .venv/bin/pytest tests/test_portfolio_live.py -v
"""

from __future__ import annotations

import random
import urllib.error
import urllib.request

import pytest

from app.config import settings
from app.db import close_pool, init_pool, pool, upsert_tg_user
from app.services import portfolio as pf


def _reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


if not _reachable(f"{settings.OPRAI_TG_SIGNER_URL.rstrip('/')}/health"):
    pytest.skip("signer not reachable — live portfolio test skipped", allow_module_level=True)


@pytest.fixture
async def db():
    await init_pool()
    yield
    await close_pool()


@pytest.mark.asyncio
async def test_robinhood_native_balance(db):
    tg_id = random.randint(10_000_000_000, 99_999_999_999)
    await upsert_tg_user(tg_id, "pytest-pf")
    try:
        bal = await pf.native_balance(tg_id)
        assert bal["address"].startswith("0x")
        assert isinstance(bal["wei"], int) and bal["wei"] >= 0
        assert isinstance(bal["eth"], float) and bal["eth"] >= 0.0
        assert abs(bal["eth"] * pf.WEI_PER_ETH - bal["wei"]) < 1
    finally:
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg_id)


# ── holdings ────────────────────────────────────────────────────────────────
def test_amounts_use_each_tokens_own_decimals():
    """USDG is 6 where nearly everything else is 18. One shared assumption is
    a millionfold error in whichever direction it is wrong."""
    from app.services.portfolio import _units

    assert _units(47_658_297_629_942, 6) == "47,658,297.629942"
    assert _units(3_560_521_000_000_000_000, 18) == "3.560521"
    assert _units(0, 18) == "0"


@pytest.mark.asyncio
async def test_holdings_read_only_what_the_wallet_has_touched():
    """Reading the whole registry was ~200 contract calls per /portfolio —
    slow enough to look broken and enough for a public node to rate-limit."""
    import time

    from app.services import portfolio as pf

    await init_pool()
    tg = random.randint(10**10, 10**11)
    await upsert_tg_user(tg, "pf_scope")
    try:
        started = time.monotonic()
        held = await pf.token_holdings(
            "0x0000000000000000000000000000000000000001", tg
        )
        elapsed = time.monotonic() - started

        # Not an emptiness assertion — any address on a real chain may hold
        # dust, and what it holds is not ours to predict. What must hold is
        # that we only looked at the base assets, never the whole registry.
        assert len(held) <= len(pf.BASE_ASSETS)
        # The bound is loose on purpose: it only has to fail if someone
        # reintroduces the full scan, which took twenty-odd seconds.
        assert elapsed < 8, f"a wallet with no recorded tokens took {elapsed:.1f}s"
    finally:
        await pool().execute("DELETE FROM tg_wallet_tokens WHERE telegram_id = $1", tg)
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg)
        await close_pool()


@pytest.mark.asyncio
async def test_a_real_holder_is_reported_with_the_right_scale():
    """Morpho's singleton custodies supplied USDG, so it is a holder we can
    check against without needing funds of our own."""
    from app.services import portfolio as pf

    await init_pool()
    try:
        held = await pf.token_holdings(
            "0x9D53d5E3bd5E8d4Cbfa6DB1ca238AEA02E651010", None
        )
        by_symbol = {h["symbol"]: h for h in held}
        assert "USDG" in by_symbol, "the lending pool's USDG went unseen"
        assert by_symbol["USDG"]["decimals"] == 6
        # A decimals mistake shows up as a factor of a million, so a wide band
        # catches it without pinning a balance that legitimately moves.
        amount = by_symbol["USDG"]["amount"] / 10**6
        assert 1_000 < amount < 1_000_000_000, f"implausible USDG balance {amount}"
    finally:
        await close_pool()
