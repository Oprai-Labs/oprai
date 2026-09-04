"""Morpho lending, SushiSwap, and choosing between venues.

The unit tests pin the arithmetic that decides how much someone borrows and
which venue fills their trade — both places where a quiet mistake costs real
money rather than raising an error.
"""

from __future__ import annotations

import random
import urllib.error
import urllib.request

import pytest

from app.config import settings
from app.db import close_pool, init_pool, pool, upsert_tg_user
from app.handlers import swap as swap_handler
from app.handlers.lend import SAFE_LTV_FRACTION, _collateral_needed
from app.services import auth as auth_svc
from app.services import morpho, relay, sushi
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

MARKET = {
    "marketId": "0x" + "ab" * 32,
    "loanSymbol": "USDG", "loanDecimals": 6, "loanPriceUsd": 1.0,
    "collateralSymbol": "USDe", "collateralDecimals": 18, "collateralPriceUsd": 1.0,
    "lltvPct": 91.5, "supplyApy": 0.05, "borrowApy": 0.055,
}


# ── unit: amounts ───────────────────────────────────────────────────────────
def test_amounts_use_each_side_decimals():
    """USDG is 6 decimals and its collateral is often 18. One shared guess
    would be a millionfold error in whichever direction it was wrong."""
    assert morpho.base_units(100, 6) == "100000000"
    assert morpho.base_units(100, 18) == "100000000000000000000"
    # Fractions must not be lost to float rounding.
    assert morpho.base_units("0.000001", 6) == "1"


def test_borrowing_stays_well_inside_the_liquidation_threshold():
    """LLTV is where liquidation starts, not where borrowing should aim."""
    needed = _collateral_needed(MARKET, 100)
    implied_ltv = 100 / needed  # equal prices, so this is the LTV
    assert implied_ltv < MARKET["lltvPct"] / 100
    assert implied_ltv == pytest.approx(SAFE_LTV_FRACTION * MARKET["lltvPct"] / 100)


def test_collateral_of_a_different_price_is_scaled_by_it():
    """A collateral worth $2 needs half as many units as one worth $1."""
    pricier = {**MARKET, "collateralPriceUsd": 2.0}
    assert _collateral_needed(pricier, 100) == pytest.approx(
        _collateral_needed(MARKET, 100) / 2
    )


def test_a_market_missing_a_price_asks_for_nothing_rather_than_infinity():
    """A zero price would divide by zero or quote a nonsense collateral —
    both worse than declining to offer that market."""
    assert _collateral_needed({**MARKET, "collateralPriceUsd": 0}, 100) == 0.0
    assert _collateral_needed({**MARKET, "lltvPct": 0}, 100) == 0.0


# ── unit: comparing venues ──────────────────────────────────────────────────
def test_quote_amounts_parse_whatever_shape_a_venue_sends():
    """A comparison that can't read one venue's number silently hands every
    trade to the other."""
    assert swap_handler._to_float("1,234.5") == pytest.approx(1234.5)
    assert swap_handler._to_float(23997043) == pytest.approx(23997043)
    assert swap_handler._to_float(None) is None
    assert swap_handler._to_float("not a number") is None


@pytest.mark.asyncio
async def test_the_better_fill_wins_and_units_are_reconciled(monkeypatch):
    """Sushi answers in base units, Relay in display units. Comparing them
    raw makes Sushi's 23997043 beat Relay's 23.94 every single time, whatever
    the real prices are."""
    async def fake_relay_quote(jwt, params):
        return {"details": {"currencyOut": {
            "currency": {"symbol": "USDG"}, "amountFormatted": "25.0"}}}

    async def fake_sushi(jwt, **kwargs):
        # 23.997 USDG in 6-decimal base units — a bigger number, a worse fill.
        return {"transactions": [{"to": "0x1", "data": "0x", "value": "0"}],
                "expectedAmountOut": 23997043, "priceImpact": 0.0003}

    monkeypatch.setattr(relay, "quote", fake_relay_quote)
    monkeypatch.setattr(sushi, "swap", fake_sushi)

    venue, _, out, symbol, extra, _ = await swap_handler._best_same_chain_route(
        "jwt", "0xwallet", "0xin", "0xout", "USDG", 0.01, 6
    )
    assert venue == "relay", "base units were compared against display units"
    assert symbol == "USDG"
    assert "SushiSwap" in extra  # the losing quote is still shown


@pytest.mark.asyncio
async def test_one_venue_being_down_does_not_refuse_the_trade(monkeypatch):
    async def dead_relay(jwt, params):
        raise relay.RelayError("no route")

    async def fake_sushi(jwt, **kwargs):
        return {"transactions": [{"to": "0x1", "data": "0x", "value": "0"}],
                "expectedAmountOut": 100_000000, "priceImpact": 0.0001}

    monkeypatch.setattr(relay, "quote", dead_relay)
    monkeypatch.setattr(sushi, "swap", fake_sushi)

    venue, _, out, _, _, _ = await swap_handler._best_same_chain_route(
        "jwt", "0xwallet", "0xin", "0xout", "USDG", 100, 6
    )
    assert venue == "sushi" and out.startswith("100")


@pytest.mark.asyncio
async def test_both_venues_down_is_reported_not_swallowed(monkeypatch):
    async def dead_relay(jwt, params):
        raise relay.RelayError("no route")

    async def dead_sushi(jwt, **kwargs):
        raise sushi.SushiError("no route")

    monkeypatch.setattr(relay, "quote", dead_relay)
    monkeypatch.setattr(sushi, "swap", dead_sushi)
    with pytest.raises(sushi.SushiError):
        await swap_handler._best_same_chain_route(
            "jwt", "0xw", "0xin", "0xout", "USDG", 1, 6
        )


# ── live ────────────────────────────────────────────────────────────────────
@pytest.mark.skipif(not LIVE, reason="gateway/signer not running")
@pytest.mark.asyncio
async def test_real_markets_lend_usdg_and_build_real_transactions():
    await init_pool()
    tg = random.randint(10**10, 10**11)
    await upsert_tg_user(tg, "lend_live")
    try:
        addr = await wallet_svc.wallet_address(tg)
        jwt = await auth_svc.get_jwt(tg)

        markets = await morpho.markets(jwt)
        assert markets, "no Morpho markets on Robinhood Chain"
        assert all(int(m["chainId"]) == morpho.CHAIN_ID for m in markets)

        best = morpho.best_supply_market(markets)
        assert best and float(best["supplyApy"]) > 0
        # Nothing pays less than the one we default to.
        assert all(float(m["supplyApy"]) <= float(best["supplyApy"]) for m in markets)

        built = await morpho.build_supply(jwt, addr, best, 100)
        txs = built["transactions"]
        assert txs and int(built["chainId"]) == morpho.CHAIN_ID
        # The approval comes first and the Morpho call last; sending them out
        # of order races the allowance.
        assert txs[-1]["to"].lower() != best["loanAddress"].lower()

        # Borrowing posts collateral first, so it takes more steps than supply.
        borrow = await morpho.build_borrow(jwt, addr, markets[0],
                                           borrow=50, collateral=100)
        assert len(borrow["transactions"]) > len(txs)
    finally:
        await pool().execute("DELETE FROM tg_wallets WHERE telegram_id = $1", tg)
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg)
        await close_pool()


@pytest.mark.skipif(not LIVE, reason="gateway/signer not running")
@pytest.mark.asyncio
async def test_a_wallet_with_no_position_has_nothing_to_repay():
    await init_pool()
    tg = random.randint(10**10, 10**11)
    await upsert_tg_user(tg, "lend_empty")
    try:
        addr = await wallet_svc.wallet_address(tg)
        jwt = await auth_svc.get_jwt(tg)
        assert await morpho.positions(jwt, addr) == []
    finally:
        await pool().execute("DELETE FROM tg_wallets WHERE telegram_id = $1", tg)
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg)
        await close_pool()


@pytest.mark.skipif(not LIVE, reason="gateway/signer not running")
@pytest.mark.asyncio
async def test_sushi_prices_the_chains_own_pairs():
    await init_pool()
    tg = random.randint(10**10, 10**11)
    await upsert_tg_user(tg, "sushi_live")
    try:
        addr = await wallet_svc.wallet_address(tg)
        jwt = await auth_svc.get_jwt(tg)

        native = await sushi.swap(jwt, wallet=addr, token_in="eth",
                                  token_out="usdg", amount=0.01)
        # A native input needs no allowance, so it is a single transaction.
        assert sushi.transaction_count(native) == 1
        assert float(sushi.summarize(native)["out_amount"]) > 0

        # An ERC-20 input needs an allowance, so a route through it is two
        # transactions. Whether Sushi HAS a route for a given pair right now is
        # theirs, not ours — a missing one is skipped rather than failed, or
        # the suite reports our code broken when their liquidity moved.
        try:
            erc20 = await sushi.swap(jwt, wallet=addr, token_in="usdg",
                                     token_out="usde", amount=100)
        except sushi.SushiError as e:
            if "no route" not in str(e):
                raise
            pytest.skip("Sushi has no USDG/USDe route right now")
        assert sushi.transaction_count(erc20) == 2, "the approval is missing"
    finally:
        await pool().execute("DELETE FROM tg_wallets WHERE telegram_id = $1", tg)
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg)
        await close_pool()


# ── scale ───────────────────────────────────────────────────────────────────
def test_position_amounts_are_scaled_out_of_base_units():
    """Morpho reports base units and gives us the decimals to divide by.
    Shown raw, 1,480,746 stood in for 1.48 USDG — a million times the truth,
    on a wallet holding under a dollar."""
    raw = {
        "chainId": 4663, "loanDecimals": 6, "collateralDecimals": 18,
        "supplyAssets": 1480746, "borrowAssets": 50025,
        "collateral": 2 * 10**18,
    }
    scaled = morpho._to_human(raw)
    assert scaled["supplyAssets"] == pytest.approx(1.480746)
    assert scaled["borrowAssets"] == pytest.approx(0.050025)
    assert scaled["collateral"] == pytest.approx(2.0)


def test_a_missing_decimal_count_does_not_invent_a_fortune():
    """Defaulting to 18 is the safe direction: it under-reports rather than
    turning dust into millions."""
    scaled = morpho._to_human({"supplyAssets": 1480746})
    assert scaled["supplyAssets"] < 1


@pytest.mark.skipif(not LIVE, reason="gateway/signer not running")
@pytest.mark.asyncio
async def test_a_real_position_agrees_with_its_own_usd_value():
    """The strongest available check: Morpho reports the USD value separately,
    so for a dollar-pegged loan token the scaled amount and the USD figure have
    to land in the same place. They differed by a million."""
    await init_pool()
    tg = random.randint(10**10, 10**11)
    await upsert_tg_user(tg, "scale_check")
    try:
        jwt = await auth_svc.get_jwt(tg)
        markets = await morpho.markets(jwt)
        assert markets, "no markets to check against"
        # Every market here lends USDG, a dollar — so a scaled amount and its
        # USD value must agree.
        for position in await morpho.positions(jwt, "0x9D53d5E3bd5E8d4Cbfa6DB1ca238AEA02E651010"):
            supply = float(position.get("supplyAssets") or 0)
            supply_usd = float(position.get("supplyUsd") or 0)
            if supply_usd > 1:
                assert supply == pytest.approx(supply_usd, rel=0.2), (
                    f"amount {supply} and value ${supply_usd} disagree — "
                    "the amount is probably still in base units"
                )
    finally:
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg)
        await close_pool()


def test_each_venue_is_read_in_the_units_it_actually_speaks():
    """The scale of an amount is a property of the venue, not a guess.

    Morpho reports base units; Sushi reports base units; Relay and Uniswap
    report display units (their field names say so); Lighter and OpenSea report
    human numbers. Reading any of them in the wrong one is a millionfold error,
    and it shipped once — so the expectation is written down rather than
    rediscovered.
    """
    import inspect

    from app.handlers import swap as swap_handler

    # Sushi's expectedAmountOut is base units; the venue comparison must divide
    # before it can be compared with Relay's display amount, or Sushi wins
    # every trade on scale alone.
    source = inspect.getsource(swap_handler._best_same_chain_route)
    assert "10**dst_decimals" in source.replace(" ", ""), (
        "Sushi's base-unit output is compared against Relay's display amount"
    )

    # Morpho scales where it reads, so nothing downstream has to remember.
    assert "_to_human" in inspect.getsource(morpho.positions)


# ── whose choice the route is ───────────────────────────────────────────────
def test_naming_a_venue_is_honoured():
    """Saying "swap on Sushi" and getting Relay is the bot overriding the one
    instruction it was given."""
    from app.handlers.swap import _named_venue

    assert _named_venue(["on", "sushi"]) == "sushi"
    assert _named_venue(["via", "Relay"]) == "relay"
    assert _named_venue(["uniswap"]) == "uniswap"
    assert _named_venue([]) is None
    assert _named_venue(["please"]) is None


def test_the_venue_the_model_chose_survives_the_hand_off():
    """The model emits sushi_swap when someone asks for Sushi. Dropping that
    on the way to the command means the request was understood and then
    quietly re-decided."""
    from app.handlers.chat import _args_for

    _, args = _args_for({
        "type": "sushi_swap",
        "params": {"tokenIn": "ETH", "tokenOut": "USDG", "amount": "0.01"},
    })
    assert args.endswith("on sushi"), args

    # A generic swap carries no venue, so the better fill still wins.
    _, plain = _args_for({
        "type": "swap",
        "params": {"amount": "1", "fromToken": "ETH", "toToken": "USDG"},
    })
    assert "on " not in plain


@pytest.mark.asyncio
async def test_a_forced_venue_wins_even_when_it_fills_worse(monkeypatch):
    """Best price is the default, not a rule. Someone who asks for a venue is
    asking for that venue."""
    from app.handlers import swap as swap_handler

    async def fake_relay_quote(jwt, params):
        return {"details": {"currencyOut": {
            "currency": {"symbol": "USDG"}, "amountFormatted": "100.0"}}}

    async def fake_sushi(jwt, **kwargs):
        return {"transactions": [{"to": "0x1", "data": "0x", "value": "0"}],
                "expectedAmountOut": 50_000000, "priceImpact": 0.001}

    monkeypatch.setattr(relay, "quote", fake_relay_quote)
    monkeypatch.setattr(sushi, "swap", fake_sushi)

    # Relay fills better (100 vs 50), so it wins by default...
    venue, *_ = await swap_handler._best_same_chain_route(
        "jwt", "0xw", "0xin", "0xout", "USDG", 1, 6)
    assert venue == "relay"

    # ...but not when Sushi was asked for.
    venue, *_ = await swap_handler._best_same_chain_route(
        "jwt", "0xw", "0xin", "0xout", "USDG", 1, 6, forced="sushi")
    assert venue == "sushi"
