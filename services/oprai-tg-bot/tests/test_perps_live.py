"""Lighter perps — stocks and memecoins with leverage.

Unit tests pin the sizing maths, because that is where a bad number becomes a
rejected order. The live tests read real markets through the gateway and check
that a wallet with no perps account reports its state honestly rather than
erroring.
"""

from __future__ import annotations

import random
import urllib.error
import urllib.request

import pytest

from app.config import settings
from app.db import close_pool, init_pool, pool, upsert_tg_user
from app.services import auth as auth_svc
from app.services import lighter
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


# ── unit: the minimum that will actually fill ───────────────────────────────
def test_minimum_takes_the_larger_of_both_floors():
    """Lighter enforces a quote minimum AND a base minimum. Quoting only one
    sends the user into a rejection: NVDA's 0.04 share floor at $225 is $9,
    under the $10 quote floor, so $10 is the real answer."""
    market = {"mark_price": 225.0, "min_quote_amount": 10.0, "min_base_amount": 0.04}
    assert lighter.min_collateral_usd(market, 1) == pytest.approx(10.0)

    # And when the base floor is the binding one, it wins.
    market = {"mark_price": 400.0, "min_quote_amount": 10.0, "min_base_amount": 0.05}
    assert lighter.min_collateral_usd(market, 1) == pytest.approx(20.0)


def test_leverage_lowers_the_collateral_floor():
    """The minimum is on position size, not on the collateral, so leverage
    divides it — otherwise we would refuse a trade the exchange accepts."""
    market = {"mark_price": 225.0, "min_quote_amount": 10.0, "min_base_amount": 0.04}
    assert lighter.min_collateral_usd(market, 20) == pytest.approx(0.5)


def test_missing_price_does_not_crash_the_floor():
    """A market with no price yet must still produce a usable number rather
    than raising in the middle of a quote."""
    assert lighter.min_collateral_usd({"min_quote_amount": 10.0}, 1) == pytest.approx(10.0)


# ── unit: reading a position out of account state ───────────────────────────
def test_position_lookup_ignores_closed_and_is_ticker_insensitive():
    state = {
        "positions": [
            {"symbol": "TSLA", "size": 0},        # closed — not a position
            {"symbol": "NVDA", "size": 0.5, "side": "long"},
        ]
    }
    assert lighter.position_for(state, "TSLA") is None
    assert lighter.position_for(state, "$nvda")["side"] == "long"


def test_onboard_detection_matches_what_the_server_says():
    """Opening auto-onboards on this signal; if the phrasing stops matching, a
    user's first trade fails instead of quietly authorising."""
    for msg in ("account needs onboard", "Please connect Lighter", "not authorised"):
        assert lighter.NEEDS_ONBOARD.search(msg)
    assert not lighter.NEEDS_ONBOARD.search("insufficient collateral")


# ── live ────────────────────────────────────────────────────────────────────
@pytest.mark.skipif(not LIVE, reason="gateway/signer not running")
@pytest.mark.asyncio
async def test_stock_and_crypto_markets_are_listed():
    await init_pool()
    tg = random.randint(10_000_000_000, 99_999_999_999)
    await upsert_tg_user(tg, "perps_markets")
    try:
        jwt = await auth_svc.get_jwt(tg)
        markets = await lighter.markets(jwt)
        assert len(markets) > 10

        symbols = {(m.get("symbol") or "").upper() for m in markets}
        # Both halves of what the user asked for: tokenized stocks and memes.
        assert "NVDA" in symbols and "BTC" in symbols

        nvda = await lighter.market_for(jwt, "NVDA")
        assert float(nvda["mark_price"]) > 0
        assert int(nvda["max_leverage"]) >= 2
        # The floor must be a real, quotable number, not zero.
        assert lighter.min_collateral_usd(nvda, 1) > 0
    finally:
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg)
        await close_pool()


@pytest.mark.skipif(not LIVE, reason="gateway/signer not running")
@pytest.mark.asyncio
async def test_wallet_without_a_perps_account_reads_cleanly():
    """The no-account case is the one every new user hits, and it must answer
    with state rather than an error the handler would show as a failure."""
    await init_pool()
    tg = random.randint(10_000_000_000, 99_999_999_999)
    await upsert_tg_user(tg, "perps_fresh")
    try:
        addr = await wallet_svc.wallet_address(tg)
        jwt = await auth_svc.get_jwt(tg)
        state = await lighter.account(jwt, addr)

        assert state.get("has_account") is False
        assert not state.get("onboarded")
        assert state.get("positions") == []
        assert lighter.position_for(state, "NVDA") is None
    finally:
        await pool().execute("DELETE FROM tg_wallets WHERE telegram_id = $1", tg)
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg)
        await close_pool()


@pytest.mark.skipif(not LIVE, reason="gateway/signer not running")
@pytest.mark.asyncio
async def test_unfunded_open_is_refused_before_any_signature():
    """No collateral must produce a plain refusal, never a signed order or a
    raw exchange error.

    The assertion is on the *text*, not just the exception: an environment gap
    (SDK missing, service down) also raises LighterError, and a test that
    accepts any failure would report this path as working when it isn't.
    """
    await init_pool()
    tg = random.randint(10_000_000_000, 99_999_999_999)
    await upsert_tg_user(tg, "perps_unfunded")
    try:
        w = await wallet_svc.get_or_create_wallet(tg)
        jwt = await auth_svc.get_jwt(tg)
        with pytest.raises(lighter.LighterError) as err:
            await lighter.open_position(
                jwt, w["enc_key_ref"], w["address"],
                symbol="NVDA", side="long", collateral_usd=10.0, leverage=1,
            )
        # The full chain ran: open -> needs onboarding -> onboarding needs a
        # deposit -> the user is told the one thing that unblocks them.
        assert "deposit" in str(err.value).lower()
    finally:
        await pool().execute("DELETE FROM tg_wallets WHERE telegram_id = $1", tg)
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg)
        await close_pool()
