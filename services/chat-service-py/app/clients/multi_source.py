"""Robust market-data fallback chains.

Each provider (Birdeye, Jupiter, DexScreener, Helius) has its own outage
profile, rate limits, and coverage gaps. Single-source tools fail open or
return stale data when their provider hiccups; for prod-critical price /
holder lookups we want the answer even if one provider is down.

Two helpers exposed to the LLM as `price_robust` and `holders_robust`:
  * Each tries providers in order of accuracy/recency.
  * On error or empty response, falls through to the next.
  * Returns a normalised shape with the actual `source` so the LLM can
    cite it. Adding a new provider is just one entry in the chain.

Why not silently rewrite `birdeye_price`? Existing prompts and tool
selectors call out specific sources by name; renaming would make the
LLM's source citations a lie. New tools, new contract.
"""

from __future__ import annotations

import time
from typing import Any

from app.clients import market_data


# ── Price ─────────────────────────────────────────────────────────────────────

async def _try_birdeye_price(mint: str) -> dict | None:
    try:
        raw = await market_data.birdeye_price(mint)
    except Exception:
        return None
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, dict):
        return None
    usd = data.get("value") or data.get("price")
    if usd in (None, 0):
        return None
    return {
        "usd": float(usd),
        "change24h": data.get("priceChange24h"),
        "liquidity": data.get("liquidity"),
        "source": "birdeye",
    }


async def _try_jupiter_price(mint: str) -> dict | None:
    try:
        raw = await market_data.jup_price(mint)
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    entry = raw.get(mint) or (raw.get("data") or {}).get(mint) if isinstance(raw, dict) else None
    if not isinstance(entry, dict):
        return None
    usd = entry.get("usdPrice") or entry.get("price")
    if usd in (None, 0):
        return None
    return {
        "usd": float(usd),
        "change24h": entry.get("priceChange24h"),
        "liquidity": None,
        "source": "jupiter",
    }


async def _try_dex_price(mint: str) -> dict | None:
    try:
        raw = await market_data.dex_token(mint)
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    pairs = raw.get("topPairs") or []
    if not pairs:
        return None
    top = pairs[0]
    try:
        usd = float(top.get("priceUsd") or 0)
    except (TypeError, ValueError):
        return None
    if usd <= 0:
        return None
    return {
        "usd": usd,
        "change24h": top.get("priceChange24h"),
        "liquidity": top.get("liquidityUsd"),
        "source": f"dexscreener:{top.get('dex')}",
    }


async def price_robust(mint: str) -> dict:
    """Fetch a USD price with provider fallback.

    Order: Birdeye (best metadata) → Jupiter (always-on) → DexScreener (deepest
    pool coverage). Returns `{usd, change24h, liquidity, source, fetched_at}`
    or `{error, sources_tried}` if every provider fails.
    """
    tried: list[str] = []
    for fetcher, label in (
        (_try_birdeye_price, "birdeye"),
        (_try_jupiter_price, "jupiter"),
        (_try_dex_price, "dexscreener"),
    ):
        tried.append(label)
        result = await fetcher(mint)
        if result is not None:
            result["fetched_at"] = int(time.time())
            result["sources_tried"] = tried
            return result
    return {"error": "no provider returned a price", "sources_tried": tried}


# ── Holders ───────────────────────────────────────────────────────────────────

async def _try_birdeye_holders(mint: str, limit: int) -> dict | None:
    try:
        raw = await market_data.birdeye_token_holders(mint, limit=limit)
    except Exception:
        return None
    items = (raw.get("data") or {}).get("items") if isinstance(raw, dict) else None
    if not isinstance(items, list) or not items:
        return None
    return {
        "holders": [
            {
                "address": h.get("owner") or h.get("address"),
                "amount": h.get("amount") or h.get("balance"),
                "percentage": h.get("percentage"),
            }
            for h in items
        ],
        "source": "birdeye",
    }


async def _try_helius_holders(mint: str, limit: int) -> dict | None:
    try:
        raw = await market_data.helius_token_holders(mint)
    except Exception:
        return None
    if not isinstance(raw, list) or not raw:
        return None
    return {
        "holders": [
            {
                "address": h.get("address") if isinstance(h, dict) else None,
                "amount": h.get("amount") if isinstance(h, dict) else None,
                "percentage": None,  # Helius doesn't return %
            }
            for h in raw[:limit]
        ],
        "source": "helius",
    }


async def holders_robust(mint: str, limit: int = 20) -> dict:
    """Top holders with provider fallback.

    Order: Birdeye (includes percentage) → Helius (raw RPC, no percentage).
    """
    tried: list[str] = []
    for fetcher, label in (
        (_try_birdeye_holders, "birdeye"),
        (_try_helius_holders, "helius"),
    ):
        tried.append(label)
        result = await fetcher(mint, limit)
        if result is not None:
            result["fetched_at"] = int(time.time())
            result["sources_tried"] = tried
            return result
    return {"error": "no provider returned holders", "sources_tried": tried}


# ── TVL / Liquidity ───────────────────────────────────────────────────────────

async def tvl_robust(mint: str) -> dict:
    """Aggregate liquidity / TVL across DEXes.

    Birdeye token_overview gives a single pre-aggregated `liquidity` number;
    DexScreener gives a per-DEX breakdown which is generally fresher and more
    transparent. Try DexScreener first (fresher), Birdeye as fallback.
    """
    tried: list[str] = []
    tried.append("dexscreener")
    try:
        raw = await market_data.dex_token(mint)
        liq = raw.get("totalLiquidityUsd") if isinstance(raw, dict) else None
        if liq:
            return {
                "tvl_usd": float(liq),
                "volume24h_usd": raw.get("totalVolume24hUsd"),
                "by_dex": raw.get("summaryByDex") or [],
                "source": "dexscreener",
                "fetched_at": int(time.time()),
                "sources_tried": tried,
            }
    except Exception:
        pass

    tried.append("birdeye")
    try:
        raw = await market_data.birdeye_token_overview(mint)
        data = raw.get("data") if isinstance(raw, dict) else None
        if isinstance(data, dict):
            liq = data.get("liquidity")
            if liq:
                return {
                    "tvl_usd": float(liq),
                    "volume24h_usd": data.get("v24hUSD"),
                    "by_dex": [],
                    "source": "birdeye",
                    "fetched_at": int(time.time()),
                    "sources_tried": tried,
                }
    except Exception:
        pass

    return {"error": "no provider returned tvl", "sources_tried": tried}


# ── Dispatch entries (consumed by market_data._DISPATCH) ──────────────────────

DISPATCH_ENTRIES: dict[str, tuple] = {
    "price_robust":   (price_robust,   ["mint"], []),
    "holders_robust": (holders_robust, ["mint"], ["limit"]),
    "tvl_robust":     (tvl_robust,     ["mint"], []),
}
