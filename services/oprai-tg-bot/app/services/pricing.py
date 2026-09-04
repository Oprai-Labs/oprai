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


class PriceUnavailable(RuntimeError):
    """No trustworthy rate. Callers must refuse to price, never guess."""


async def oprai_usd(*, force: bool = False) -> float:
    """Dollars per $OPRAI, from the deepest pool. Raises PriceUnavailable."""
    global _cached, _cached_at

    now = time.monotonic()
    if not force and _cached and now - _cached_at < _TTL_SECONDS:
        return _cached

    url = DEXSCREENER.format(address=settings.OPRAI_TG_TOKEN_ADDRESS)
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(url)
        payload = r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError) as e:
        log.info("oprai_price_unavailable", error=str(e)[:120])
        raise PriceUnavailable("couldn't read the $OPRAI price") from e

    price = _deepest_price(payload.get("pairs") or [])
    if price is None:
        raise PriceUnavailable("couldn't read the $OPRAI price")

    _cached, _cached_at = price, now
    return price


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


def credits_cost_usd(credits: int) -> float:
    return credits * settings.OPRAI_TG_CREDIT_PRICE_USD


async def credits_cost_oprai(credits: int) -> tuple[float, float, float]:
    """-> (oprai to pay, usd price of the pack, rate used).

    The rate is returned with the amount so the caller can show what it
    converted at and record it: a receipt that says only "you paid 101,061
    $OPRAI" cannot be checked by the person who paid it.
    """
    usd = credits_cost_usd(credits)
    rate = await oprai_usd()
    return usd / rate, usd, rate


def packs() -> list[int]:
    """Credit pack sizes, smallest first."""
    sizes: list[int] = []
    for chunk in str(settings.OPRAI_TG_CREDIT_PACKS).split(","):
        chunk = chunk.strip()
        if chunk.isdigit() and int(chunk) > 0:
            sizes.append(int(chunk))
    return sorted(set(sizes))
