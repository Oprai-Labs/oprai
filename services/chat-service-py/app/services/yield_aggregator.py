"""Aggregate yield data from multiple Solana DeFi protocols for comparison.

Usage:
    from app.services.yield_aggregator import get_yield_comparison
    results = await get_yield_comparison("liquid_staking")
"""

import logging
from typing import Optional

import httpx

from .tokens_generated import get_verified_token_by_symbol

logger = logging.getLogger(__name__)


def _verified_mint(symbol: str) -> str:
    """Resolve a symbol to its verified mint address.

    Centralized so every yield-aggregator entry pulls from the single
    source-of-truth registry (shared/tokens.json, CI-verified against Jupiter).
    Raises at module import if a symbol is unknown — fail-closed.
    """
    t = get_verified_token_by_symbol(symbol)
    if t is None:
        raise RuntimeError(f"yield_aggregator references unknown token symbol: {symbol!r}")
    return t["address"]


# Protocol registry — add new protocols here.
# method: "GET" (default) or "POST" (sends empty JSON body)
# mint_lookup: if True, response is a list keyed by tokenMint; match via "mint" field
PROTOCOLS: dict[str, dict] = {
    "jito": {
        "name": "Jito (jitoSOL)",
        # Returns time series [{epoch, apy, tvl, supply, validator_count}, ...]; take latest
        "url": "https://kobe.mainnet.jito.network/api/v1/stake_pool_stats",
        "method": "POST",
        "category": "liquid_staking",
        "mint": _verified_mint("JitoSOL"),
    },
    "marinade": {
        "name": "Marinade (mSOL)",
        # Returns {"value": 0.0678, "end_time": "...", ...} — APY is in "value"
        "url": "https://api.marinade.finance/msol/apy/1y",
        "method": "GET",
        "category": "liquid_staking",
        "mint": _verified_mint("mSOL"),
    },
    "jupsol": {
        "name": "Jupiter (jupSOL)",
        # Kamino staking-yields/mean returns [{tokenMint, apy}, ...] for ~41 LSTs
        # Used as the authoritative source for jupSOL since Jupiter has no public APY endpoint
        "url": "https://api.kamino.finance/v2/staking-yields/mean",
        "method": "GET",
        "mint_lookup": True,
        "category": "liquid_staking",
        "mint": _verified_mint("jupSOL"),
    },
    # Lending protocols
    "kamino_sol": {
        "name": "Kamino SOL Lending",
        "url": "https://api.kamino.finance/v2/kamino-market/7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF/reserves/metrics",
        "method": "GET",
        "category": "lending",
    },
}


async def get_yield_comparison(category: str = "liquid_staking") -> list[dict]:
    """Fetch and compare yields from multiple protocols.

    Returns a list sorted by APY descending (None APY last).
    Uses Redis caching with 5-minute TTL.
    """
    from app.services.cache import get_cache_service

    cache = await get_cache_service()

    cached = await cache.get(f"yields:{category}")
    if cached is not None:
        logger.debug("Using cached yields for %s", category)
        return cached

    candidates = {k: v for k, v in PROTOCOLS.items() if v["category"] == category}
    results: list[dict] = []

    async with httpx.AsyncClient(timeout=8.0) as client:
        for key, meta in candidates.items():
            entry: dict = {
                "protocol": key,
                "name": meta["name"],
                "category": category,
                "apy": None,
                "mint": meta.get("mint"),
            }
            try:
                if meta.get("method") == "POST":
                    resp = await client.post(meta["url"], json={})
                else:
                    resp = await client.get(meta["url"])

                if resp.status_code == 200:
                    entry["apy"] = _extract_apy(key, meta, resp.json())
                else:
                    logger.debug("yield fetch %s → HTTP %d", key, resp.status_code)
            except Exception as exc:
                logger.debug("yield fetch failed for %s: %s", key, exc)
            results.append(entry)

    results.sort(key=lambda x: x["apy"] or 0.0, reverse=True)

    await cache.set(f"yields:{category}", results)

    return results


def _extract_apy(protocol: str, meta: dict, data: object) -> float | None:
    """Extract APY float from protocol-specific response shape."""
    try:
        if not isinstance(data, (dict, list)):
            return None

        if protocol == "marinade":
            # {"value": 0.0678, "end_time": "...", ...}
            assert isinstance(data, dict)
            return float(data.get("value", 0))

        if protocol == "jito":
            # POST stake_pool_stats → {"apy": [{"data": 0.0571, "date": "..."}, ...], ...}
            assert isinstance(data, dict)
            apy_series = data.get("apy", [])
            if isinstance(apy_series, list) and apy_series:
                latest = apy_series[-1]
                if isinstance(latest, dict) and "data" in latest:
                    return float(latest["data"])
            return None

        if meta.get("mint_lookup"):
            # Response is a list of {tokenMint, apy}; find entry matching our mint
            assert isinstance(data, list)
            mint = meta.get("mint", "")
            entry = next(
                (x for x in data if isinstance(x, dict) and x.get("tokenMint") == mint),
                None,
            )
            if entry:
                apy = entry.get("apy") or entry.get("annualizedApy")
                if apy is not None:
                    return float(apy)
            return None

        if protocol == "kamino_sol":
            assert isinstance(data, (dict, list))
            reserves = data if isinstance(data, list) else data.get("reserves", [])
            sol_reserve = next(
                (r for r in reserves if r.get("symbol", "").upper() in ("SOL", "WSOL")), None
            )
            if sol_reserve:
                return float(sol_reserve.get("supplyApy") or sol_reserve.get("apy") or 0)


    except Exception as exc:
        logger.debug("APY extraction failed for %s: %s", protocol, exc)

    return None
