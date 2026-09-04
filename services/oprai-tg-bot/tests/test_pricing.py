"""What a month costs, and what happens when we can't tell.

The subscription is priced in dollars and paid in ETH, so everything here is
about one property: the dollar figure is the promise and the ETH amount
follows the live rate — never the other way round. The rest is about refusing
to sell rather than guessing, because a payment converted at a made-up rate
either overcharges the buyer or sells a month for less than it should.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.services import pricing


def _pair(usd: float, native: float, *, liquidity: float = 8_120_489,
          volume: float = 36_000_953, quote: str = "ETH") -> dict:
    return {"priceUsd": str(usd), "priceNative": str(native),
            "liquidity": {"usd": liquidity}, "volume": {"h24": volume},
            "quoteToken": {"symbol": quote}}


# ── which pool sets the rate ────────────────────────────────────────────────
def test_the_deepest_live_pool_sets_the_rate():
    """Several pairs quote the same token; the one with real depth is the one
    a trade would actually clear against."""
    rate = pricing._eth_from([
        _pair(1.0021, 0.0004080, liquidity=8_120_489),
        _pair(1.5000, 0.0004080, liquidity=60_000),   # shallower, off price
    ])
    assert rate == pytest.approx(1.0021 / 0.0004080)


def test_a_pool_quoted_in_dollars_says_nothing_about_ETH():
    """Dividing a USDG/USDC pair's dollar price by its 'native' price yields a
    number that looks like a rate and is garbage."""
    assert pricing._eth_from([_pair(1.0, 1.0, quote="USDC")]) is None


def test_a_near_empty_pool_prices_nothing():
    assert pricing._eth_from([_pair(1.0021, 0.000408, liquidity=12_000)]) is None
    assert pricing._eth_from([]) is None


def test_a_pool_that_stopped_trading_prices_nothing():
    """Deep but dead: its last price is a memory of the last trade, and a
    month sold against it is sold at whatever ETH was worth back then."""
    assert pricing._eth_from([_pair(1.0021, 0.000408, volume=0)]) is None


def test_a_depegged_anchor_is_not_a_dollar():
    """The whole derivation assumes the stablecoin is worth $1. If it isn't,
    every number downstream is wrong and none of the arithmetic looks it."""
    assert pricing._eth_from([_pair(0.62, 0.000408)]) is None
    assert pricing._eth_from([_pair(1.0021, 0.000408)]) is not None


def test_a_broken_quote_is_skipped_not_crashed():
    assert pricing._eth_from([
        {"priceUsd": None, "priceNative": "1", "liquidity": {"usd": 9e9},
         "volume": {"h24": 1}, "quoteToken": {"symbol": "ETH"}},
        {"priceUsd": "x", "priceNative": "1", "liquidity": {"usd": 9e9},
         "volume": {"h24": 1}, "quoteToken": {"symbol": "ETH"}},
        _pair(1.0021, 0.0004080),
    ]) == pytest.approx(1.0021 / 0.0004080)


# ── the dollar is the promise ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_the_price_stays_in_dollars_when_ETH_moves(monkeypatch):
    """A 2x in ETH must not double what a month costs. The dollar figure
    stays; the ETH amount halves."""
    async def cheap():
        return 2_457.04

    async def double():
        return 4_914.08

    monkeypatch.setattr(pricing, "eth_usd", cheap)
    eth_before, usd_before, _ = await pricing.subscription_cost_eth()

    monkeypatch.setattr(pricing, "eth_usd", double)
    eth_after, usd_after, _ = await pricing.subscription_cost_eth()

    assert usd_before == usd_after == pytest.approx(settings.OPRAI_TG_SUB_PRICE_USD)
    assert eth_after == pytest.approx(eth_before / 2)


@pytest.mark.asyncio
async def test_no_rate_means_no_sale(monkeypatch):
    async def unavailable():
        raise pricing.PriceUnavailable("no price")

    monkeypatch.setattr(pricing, "eth_usd", unavailable)
    with pytest.raises(pricing.PriceUnavailable):
        await pricing.subscription_cost_eth()


@pytest.mark.asyncio
async def test_an_unreachable_price_source_does_not_return_a_number():
    """Rather than fall back to a stale or default rate."""
    import httpx

    async def dead(*a, **k):
        raise httpx.ConnectError("down")

    pricing._eth_cached, pricing._eth_cached_at = None, 0.0
    with pytest.MonkeyPatch.context() as m:
        m.setattr(httpx.AsyncClient, "get", dead)
        with pytest.raises(pricing.PriceUnavailable):
            await pricing.eth_usd(force=True)


def test_the_rate_is_read_from_the_deep_stable_not_our_own_token():
    """$OPRAI's pool holds $29k and USDG's holds $8.1M. Deriving the ETH price
    from our own token would make selling a subscription depend on the health
    of a pool that has nothing to do with it."""
    assert (settings.OPRAI_TG_STABLE_ADDRESS.lower()
            != settings.OPRAI_TG_TOKEN_ADDRESS.lower())
    assert pricing._MIN_LIQUIDITY_USD >= 50_000
