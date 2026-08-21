"""
Trending Tokens Service

Fetches trending tokens from DexScreener for Solana ecosystem:
- Hot tokens (recently created)
- Top gainers/losers
- Most traded pairs
- Price changes by timeframe
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# DexScreener API base URL
DEXSCREENER_API = "https://api.dexscreener.com"


# Timeframe options for trending
class Timeframe:
    HOUR_1 = "1h"
    HOUR_6 = "6h"
    HOUR_24 = "24h"


@dataclass
class TokenPair:
    """A DEX pair with token info"""
    address: str
    pair_address: str
    name: str
    symbol: str
    price: float
    price_change_h1: float
    price_change_h6: float
    price_change_h24: float
    volume_h24: float
    liquidity_usd: float
    market_cap: float
    pair_type: str  # solana, raydium, etc.
    dex: str  # raydium, orca, etc.


async def fetch_trending_pairs(
    timeframe: str = Timeframe.HOUR_24,
    limit: int = 20,
    min_liquidity: float = 10000,
) -> list[TokenPair]:
    """
    Fetch trending pairs from DexScreener.

    Args:
        timeframe: 1h, 6h, or 24h
        limit: Number of results
        min_liquidity: Minimum liquidity filter in USD

    Returns:
        List of trending token pairs
    """
    try:
        # DexScreener trending endpoint
        url = f"{DEXSCREENER_API}/latest/dex/pairs/solana"
        params = {
            "limit": limit,
            "orderBy": f"priceChange{timeframe.upper()}",
            "orderType": "desc",
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params)
            if response.status_code != 200:
                logger.warning(f"DexScreener API error: {response.status_code}")
                return []

            data = response.json()
            pairs = data.get("pairs", [])

            result = []
            for pair in pairs:
                # Filter by liquidity
                liquidity = pair.get("liquidity", {}).get("usd", 0)
                if liquidity < min_liquidity:
                    continue

                # Get token info
                base_token = pair.get("baseToken", {})
                quote_token = pair.get("quoteToken", {})

                # Price info
                price = pair.get("priceUsd")
                if price:
                    price = float(price)
                else:
                    continue

                # Price changes
                price_change = pair.get("priceChange", {})
                h1 = float(price_change.get("h1", 0) or 0)
                h6 = float(price_change.get("h6", 0) or 0)
                h24 = float(price_change.get("h24", 0) or 0)

                # Volume & market cap
                volume = pair.get("volume", {}).get("h24", 0) or 0
                market_cap = pair.get("marketCap", 0) or 0

                # Pair info
                pair_address = pair.get("pairAddress", "")
                dex_id = pair.get("dexId", "unknown")

                result.append(TokenPair(
                    address=base_token.get("address", ""),
                    pair_address=pair_address,
                    name=base_token.get("name", "Unknown"),
                    symbol=base_token.get("symbol", "???"),
                    price=price,
                    price_change_h1=h1,
                    price_change_h6=h6,
                    price_change_h24=h24,
                    volume_h24=float(volume),
                    liquidity_usd=float(liquidity),
                    market_cap=float(market_cap),
                    pair_type="solana",
                    dex=dex_id,
                ))

            return result

    except Exception as e:
        logger.error("Error fetching trending pairs", exc_info=True)
        return []


async def get_hot_tokens(
    limit: int = 10,
    min_liquidity: float = 50000,
) -> list[dict[str, Any]]:
    """
    Get hot tokens - highest volume in last hour.

    Args:
        limit: Number of tokens to return
        min_liquidity: Minimum liquidity filter

    Returns:
        List of hot tokens with metadata
    """
    pairs = await fetch_trending_pairs(
        timeframe=Timeframe.HOUR_1,
        limit=limit * 2,  # Get more to filter
        min_liquidity=min_liquidity,
    )

    # Sort by volume
    pairs.sort(key=lambda x: x.volume_h24, reverse=True)

    return [_pair_to_dict(p) for p in pairs[:limit]]


async def get_top_gainers(
    timeframe: str = Timeframe.HOUR_24,
    limit: int = 10,
    min_liquidity: float = 50000,
) -> list[dict[str, Any]]:
    """
    Get top gaining tokens by price change.

    Args:
        timeframe: 1h, 6h, or 24h
        limit: Number of tokens
        min_liquidity: Minimum liquidity

    Returns:
        List of top gainers
    """
    pairs = await fetch_trending_pairs(
        timeframe=timeframe,
        limit=limit * 2,
        min_liquidity=min_liquidity,
    )

    # Already sorted by price change descending from API
    return [_pair_to_dict(p) for p in pairs[:limit]]


async def get_top_losers(
    timeframe: str = Timeframe.HOUR_24,
    limit: int = 10,
    min_liquidity: float = 50000,
) -> list[dict[str, Any]]:
    """
    Get top losing tokens by price change.

    Args:
        timeframe: 1h, 6h, or 24h
        limit: Number of tokens
        min_liquidity: Minimum liquidity

    Returns:
        List of top losers
    """
    pairs = await fetch_trending_pairs(
        timeframe=timeframe,
        limit=limit * 3,
        min_liquidity=min_liquidity,
    )

    # Sort by price change ascending (worst first)
    pairs.sort(key=lambda x: (
        x.price_change_h24 if timeframe == Timeframe.HOUR_24
        else x.price_change_h6 if timeframe == Timeframe.HOUR_6
        else x.price_change_h1
    ))

    return [_pair_to_dict(p) for p in pairs[:limit]]


async def get_new_tokens(
    limit: int = 10,
    min_liquidity: float = 10000,
) -> list[dict[str, Any]]:
    """
    Get newest tokens (by pair creation time if available).

    Note: DexScreener doesn't expose creation time directly,
    so we use recent high-volume pairs as proxy.
    """
    pairs = await fetch_trending_pairs(
        timeframe=Timeframe.HOUR_1,
        limit=50,
        min_liquidity=min_liquidity,
    )

    # For now, return high-volume recent pairs as "new"
    # In production, would need additional data source
    return [_pair_to_dict(p) for p in pairs[:limit]]


async def search_tokens(
    query: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    Search tokens by symbol or name.

    Args:
        query: Search query
        limit: Max results

    Returns:
        Matching tokens
    """
    try:
        url = f"{DEXSCREENER_API}/tokens/solana"
        params = {"limit": 100}

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params)
            if response.status_code != 200:
                return []

            data = response.json()
            tokens = data.get("tokens", [])

            # Filter by query
            query_lower = query.lower()
            matches = [
                t for t in tokens
                if query_lower in t.get("symbol", "").lower()
                or query_lower in t.get("name", "").lower()
            ]

            return [
                {
                    "address": t.get("address"),
                    "name": t.get("name"),
                    "symbol": t.get("symbol"),
                    "price": t.get("priceUsd"),
                    "market_cap": t.get("marketCap"),
                    "liquidity": t.get("liquidity"),
                }
                for t in matches[:limit]
            ]

    except Exception as e:
        logger.error("Error searching tokens", exc_info=True)
        return []


async def get_token_by_address(
    token_address: str,
) -> dict[str, Any] | None:
    """
    Get detailed token info by address.

    Args:
        token_address: Token mint address

    Returns:
        Token info or None
    """
    try:
        url = f"{DEXSCREENER_API}/tokens/solana/{token_address}"

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            if response.status_code != 200:
                return None

            data = response.json()
            token = data.get("token")

            if not token:
                return None

            # Get pair info
            pairs = data.get("pairs", [])
            best_pair = pairs[0] if pairs else None

            return {
                "address": token.get("address"),
                "name": token.get("name"),
                "symbol": token.get("symbol"),
                "price": token.get("priceUsd"),
                "market_cap": token.get("marketCap"),
                "liquidity": best_pair.get("liquidity", {}).get("usd") if best_pair else None,
                "pairs": [
                    {
                        "pair_address": p.get("pairAddress"),
                        "dex": p.get("dexId"),
                        "price": p.get("priceUsd"),
                        "liquidity": p.get("liquidity", {}).get("usd"),
                        "volume_24h": p.get("volume", {}).get("h24"),
                    }
                    for p in pairs[:5]
                ],
            }

    except Exception as e:
        logger.error("Error fetching token", exc_info=True)
        return None


def _pair_to_dict(pair: TokenPair) -> dict[str, Any]:
    """Convert TokenPair to dict for JSON serialization"""
    return {
        "address": pair.address,
        "pair_address": pair.pair_address,
        "name": pair.name,
        "symbol": pair.symbol,
        "price": round(pair.price, 6) if pair.price < 1 else round(pair.price, 2),
        "price_change_h1": round(pair.price_change_h1, 2),
        "price_change_h6": round(pair.price_change_h6, 2),
        "price_change_h24": round(pair.price_change_h24, 2),
        "volume_24h": round(pair.volume_h24, 2),
        "liquidity_usd": round(pair.liquidity_usd, 2),
        "market_cap": round(pair.market_cap, 2) if pair.market_cap else None,
        "dex": pair.dex,
    }


async def get_trending_summary(use_cache: bool = True) -> dict[str, Any]:
    """
    Get a complete trending summary with all categories.
    Uses cache with 2-minute TTL.
    """
    if use_cache:
        from app.services.cache import get_cache_service

        cache = await get_cache_service()
        cached = await cache.get("trending", "summary")
        if cached is not None:
            logger.debug("Using cached trending summary")
            return cached

    hot = await get_hot_tokens(limit=5)
    gainers_24h = await get_top_gainers(Timeframe.HOUR_24, limit=5)
    losers_24h = await get_top_losers(Timeframe.HOUR_24, limit=5)

    result = {
        "hot": hot,
        "gainers_24h": gainers_24h,
        "losers_24h": losers_24h,
        "updated_at": datetime.utcnow().isoformat(),
    }

    if use_cache:
        from app.services.cache import get_cache_service

        cache = await get_cache_service()
        await cache.set("trending", result, "summary", ttl=120)  # 2 min cache

    return result
