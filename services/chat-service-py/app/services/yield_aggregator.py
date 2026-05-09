"""Aggregate yield data from multiple Solana DeFi protocols for comparison.

Usage:
    from app.services.yield_aggregator import get_yield_comparison
    results = await get_yield_comparison("liquid_staking")
"""

import httpx
import logging
from typing import Optional

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


# Protocol registry — add new protocols here
PROTOCOLS: dict[str, dict] = {
    "jito": {
        "name": "Jito (jitoSOL)",
        "url": "https://kobe.mainnet.jito.network/api/v1/validators",
        "category": "liquid_staking",
        "mint": _verified_mint("JitoSOL"),
    },
    "marinade": {
        "name": "Marinade (mSOL)",
        "url": "https://api.marinade.finance/v1/stats",
        "category": "liquid_staking",
        "mint": _verified_mint("mSOL"),
    },
    "jupsol": {
        "name": "Jupiter (jupSOL)",
        "url": "https://worker.jup.ag/sol-stake-pool-stats",
        "category": "liquid_staking",
        "mint": _verified_mint("jupSOL"),
    },
    # Lending protocols
    "kamino_sol": {
        "name": "Kamino SOL Lending",
        "url": "https://api.kamino.finance/v2/kamino-market/7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF/reserves/metrics",
        "category": "lending",
    },
    "marginfi_sol": {
        "name": "MarginFi SOL Lending",
        "url": "https://marginfi-v2-ui-data.s3.eu-central-1.amazonaws.com/lending-data.json",
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

    # Try to get from cache first
    cached = await cache.get(f"yields:{category}")
    if cached is not None:
        logger.debug(f"Using cached yields for {category}")
        return cached

    # Cache miss - fetch from APIs
    candidates = {k: v for k, v in PROTOCOLS.items() if v["category"] == category}
    results: list[dict] = []

    async with httpx.AsyncClient(timeout=5.0) as client:
        for key, meta in candidates.items():
            entry: dict = {
                "protocol": key,
                "name": meta["name"],
                "category": category,
                "apy": None,
                "mint": meta.get("mint"),
            }
            try:
                resp = await client.get(meta["url"])
                if resp.status_code == 200:
                    entry["apy"] = _extract_apy(key, resp.json())
            except Exception as exc:
                logger.debug("yield fetch failed for %s: %s", key, exc)
            results.append(entry)

    results.sort(key=lambda x: x["apy"] or 0.0, reverse=True)

    # Cache the results
    await cache.set(f"yields:{category}", results)

    return results


def _extract_apy(protocol: str, data: object) -> Optional[float]:
    """Extract APY float from protocol-specific response shape."""
    try:
        if not isinstance(data, (dict, list)):
            return None

        if protocol == "marinade":
            assert isinstance(data, dict)
            return float(data.get("apy", 0))

        if protocol in ("jupsol",):
            assert isinstance(data, dict)
            return float(
                data.get("apy") or data.get("annualizedApy") or 0
            )

        if protocol == "jito":
            validators = data if isinstance(data, list) else (data.get("validators", []) if isinstance(data, dict) else [])  # type: ignore[union-attr]
            apys = [float(v["apy"]) for v in validators[:20] if v.get("apy")]
            return sum(apys) / len(apys) if apys else None

        if protocol == "kamino_sol":
            assert isinstance(data, (dict, list))
            reserves = data if isinstance(data, list) else data.get("reserves", [])
            sol_reserve = next(
                (r for r in reserves if r.get("symbol", "").upper() in ("SOL", "WSOL")), None
            )
            if sol_reserve:
                return float(sol_reserve.get("supplyApy") or sol_reserve.get("apy") or 0)

        if protocol == "marginfi_sol":
            assert isinstance(data, dict)
            banks = data.get("banks", [])
            sol_bank = next(
                (b for b in banks if b.get("tokenSymbol", "").upper() == "SOL"), None
            )
            if sol_bank:
                return float(sol_bank.get("lendingRate") or sol_bank.get("apy") or 0) * 100

    except Exception as exc:
        logger.debug("APY extraction failed for %s: %s", protocol, exc)

    return None
