"""What ETH is worth right now.

The subscription is priced in dollars and paid in ETH, so the rate has to come
from the market rather than from a number written down once — a fixed ETH
price would quietly change what a month costs every time ETH moved.

The rate is read from the deepest stablecoin pool on this chain, not from our
own token's pool: $OPRAI has $29k of liquidity and USDG has $8.1M, and if the
$OPRAI pool ever thinned out or stopped trading we would lose the ability to
sell a subscription that has nothing to do with it.

If no trustworthy rate can be read we do not sell. A payment converted at a
guessed rate either overcharges the buyer or takes their money for less than a
month, and both are worse than asking them to try again in a minute.
"""

from __future__ import annotations

import time

import httpx

from app.config import settings
from app.logging_config import log

DEXSCREENER = "https://api.dexscreener.com/latest/dex/tokens/{address}"

# Long enough that a burst of sign-ups makes one request, short enough that the
# quoted rate is one someone could still trade at.
_TTL_SECONDS = 120

# A pool this thin prices nothing: the mid-price of a near-empty pair moves on
# a single dust trade, so a rate derived from it is noise, not a price.
_MIN_LIQUIDITY_USD = 50_000.0

# How far the stablecoin may drift from a dollar before we stop treating it as
# one. A depegged anchor would silently rewrite the ETH price we derive from it.
_PEG_TOLERANCE = 0.05

_eth_cached: float | None = None
_eth_cached_at: float = 0.0


class PriceUnavailable(RuntimeError):
    """No trustworthy rate. Callers must refuse to price, never guess."""


async def eth_usd(*, force: bool = False) -> float:
    """Dollars per ETH on this chain. Raises PriceUnavailable.

    Derived from a stablecoin pair quoted in ETH: the pair says both what the
    stablecoin costs in dollars and what it costs in ETH, and the ratio of the
    two is what the market says ETH is worth. Verified against an independent
    spot price when this was written — 0.2% apart, which is lag, not error.
    """
    global _eth_cached, _eth_cached_at

    now = time.monotonic()
    if not force and _eth_cached and now - _eth_cached_at < _TTL_SECONDS:
        return _eth_cached

    price = _eth_from(await _fetch_pairs(settings.OPRAI_TG_STABLE_ADDRESS))
    if price is None:
        raise PriceUnavailable("couldn't read the ETH price")

    _eth_cached, _eth_cached_at = price, now
    return price


async def _fetch_pairs(address: str) -> list[dict]:
    """The market's own record for a token. Raises PriceUnavailable."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(DEXSCREENER.format(address=address))
        payload = r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError) as e:
        log.info("price_source_unavailable", error=str(e)[:120])
        raise PriceUnavailable("couldn't read the market price") from e
    return payload.get("pairs") or []


def _eth_from(pairs: list[dict]) -> float | None:
    """ETH in dollars, from the deepest live stablecoin pair quoted in ETH.

    Three things disqualify a pair, and each one has bitten somebody:
    a pool too thin to price anything, a pool that has not traded in a day
    (its last price is a memory of the last trade), and a "stablecoin" that
    is no longer worth a dollar — the last would rewrite the ETH price
    without any of the arithmetic looking wrong.
    """
    best: tuple[float, float] | None = None  # (liquidity, eth price)
    for pair in pairs:
        try:
            usd = float(pair.get("priceUsd") or 0)
            native = float(pair.get("priceNative") or 0)
            liquidity = float((pair.get("liquidity") or {}).get("usd") or 0)
            traded = float((pair.get("volume") or {}).get("h24") or 0)
        except (TypeError, ValueError):
            continue
        quote = ((pair.get("quoteToken") or {}).get("symbol") or "").upper()
        if quote not in ("ETH", "WETH"):
            continue
        if usd <= 0 or native <= 0:
            continue
        if liquidity < _MIN_LIQUIDITY_USD or traded <= 0:
            continue
        if abs(usd - 1.0) > _PEG_TOLERANCE:
            log.warning("stable_anchor_depegged", price_usd=usd)
            continue
        if best is None or liquidity > best[0]:
            best = (liquidity, usd / native)
    return best[1] if best else None


def subscription_usd() -> float:
    return float(settings.OPRAI_TG_SUB_PRICE_USD)


async def subscription_cost_eth() -> tuple[float, float, float]:
    """-> (ETH to pay, dollar price, rate used).

    The rate travels with the amount so it can be shown and recorded: a
    receipt that says only "you paid 0.004067 ETH" cannot be checked later by
    the person who paid it.
    """
    usd = subscription_usd()
    rate = await eth_usd()
    return usd / rate, usd, rate
