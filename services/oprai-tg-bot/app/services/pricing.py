"""What $OPRAI is worth right now.

Credits are priced in dollars and paid in $OPRAI. That needs a live rate, and
the rate has to come from the market rather than from a number written down
once: pegging a pack to a fixed token amount meant the same ten questions cost
twice as much after the token doubled and half as much after it dumped, which
is a price nobody chose.

The deepest pool is the price. If it can't be read we do not sell — a top-up
converted at a guessed rate either overcharges the buyer or hands out credits
for nothing, and both are worse than asking them to try again in a minute.
"""

from __future__ import annotations

import time

import httpx

from app.config import settings
from app.logging_config import log

DEXSCREENER = "https://api.dexscreener.com/latest/dex/tokens/{address}"

# Long enough that a burst of top-ups makes one request, short enough that the
# quoted rate is one someone could still trade at.
_TTL_SECONDS = 120

# A pool this thin prices nothing: the mid-price of a near-empty pair moves on
# a single dust trade, so a rate derived from it is noise, not a price.
_MIN_LIQUIDITY_USD = 1_000.0

_cached: float | None = None
_cached_at: float = 0.0
_eth_cached: float | None = None
_eth_cached_at: float = 0.0


class PriceUnavailable(RuntimeError):
    """No trustworthy rate. Callers must refuse to price, never guess."""


async def oprai_usd(*, force: bool = False) -> float:
    """Dollars per $OPRAI, from the deepest pool. Raises PriceUnavailable."""
    global _cached, _cached_at

    now = time.monotonic()
    if not force and _cached and now - _cached_at < _TTL_SECONDS:
        return _cached

    price = _deepest_price(await _fetch_pairs())
    if price is None:
        raise PriceUnavailable("couldn't read the $OPRAI price")

    _cached, _cached_at = price, now
    return price


async def _fetch_pairs() -> list[dict]:
    """The market's own record for this token. Raises PriceUnavailable."""
    url = DEXSCREENER.format(address=settings.OPRAI_TG_TOKEN_ADDRESS)
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(url)
        payload = r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError) as e:
        log.info("price_source_unavailable", error=str(e)[:120])
        raise PriceUnavailable("couldn't read the market price") from e
    return payload.get("pairs") or []


def _deepest_price(pairs: list[dict]) -> float | None:
    """The price from the pool with the most liquidity behind it.

    Several pairs can quote the same token at very different prices; the one
    with real depth is the one a trade would actually clear against.

    A pool that has not traded in a day is skipped even when it is the
    deepest: its last price is a memory of the last trade rather than what
    the token is worth now, and a top-up converted at a remembered price is
    converted at a made-up one.
    """
    best: tuple[float, float] | None = None  # (liquidity, price)
    for pair in pairs:
        try:
            price = float(pair.get("priceUsd") or 0)
            liquidity = float((pair.get("liquidity") or {}).get("usd") or 0)
            traded = float((pair.get("volume") or {}).get("h24") or 0)
        except (TypeError, ValueError):
            continue
        if price <= 0 or liquidity < _MIN_LIQUIDITY_USD or traded <= 0:
            continue
        if best is None or liquidity > best[0]:
            best = (liquidity, price)
    return best[1] if best else None


async def eth_usd(*, force: bool = False) -> float:
    """Dollars per ETH on this chain. Raises PriceUnavailable.

    Derived from the same pair we already fetch: a DEX quote carries both the
    native price and the dollar price of the token, and their ratio is what
    the market says the native asset is worth. Checked against an independent
    spot price when this was written — 0.17% apart, which is lag, not error.
    """
    global _eth_cached, _eth_cached_at

    now = time.monotonic()
    if not force and _eth_cached and now - _eth_cached_at < _TTL_SECONDS:
        return _eth_cached

    pairs = await _fetch_pairs()
    best: tuple[float, float] | None = None  # (liquidity, eth price)
    for pair in pairs:
        try:
            usd = float(pair.get("priceUsd") or 0)
            native = float(pair.get("priceNative") or 0)
            liquidity = float((pair.get("liquidity") or {}).get("usd") or 0)
            traded = float((pair.get("volume") or {}).get("h24") or 0)
        except (TypeError, ValueError):
            continue
        # Only a pair quoted in the native asset says what the native asset is
        # worth; one quoted in a stablecoin says nothing about ETH.
        if usd <= 0 or native <= 0 or liquidity < _MIN_LIQUIDITY_USD or traded <= 0:
            continue
        if (pair.get("quoteToken") or {}).get("symbol", "").upper() not in ("ETH", "WETH"):
            continue
        if best is None or liquidity > best[0]:
            best = (liquidity, usd / native)

    if best is None:
        raise PriceUnavailable("couldn't read the ETH price")
    _eth_cached, _eth_cached_at = best[1], now
    return best[1]


def credits_cost_usd(credits: int) -> float:
    return credits * settings.OPRAI_TG_CREDIT_PRICE_USD


async def credits_cost(credits: int, currency: str) -> tuple[float, float, float]:
    """-> (amount of `currency` to pay, usd price of the pack, rate used).

    The rate is returned with the amount so the caller can show what it
    converted at and record it: a receipt that says only "you paid 0.00406
    ETH" cannot be checked later by the person who paid it.
    """
    usd = credits_cost_usd(credits)
    rate = await (eth_usd() if currency.upper() == "ETH" else oprai_usd())
    return usd / rate, usd, rate


def packs() -> list[int]:
    """Credit pack sizes, smallest first."""
    sizes: list[int] = []
    for chunk in str(settings.OPRAI_TG_CREDIT_PACKS).split(","):
        chunk = chunk.strip()
        if chunk.isdigit() and int(chunk) > 0:
            sizes.append(int(chunk))
    return sorted(set(sizes))
