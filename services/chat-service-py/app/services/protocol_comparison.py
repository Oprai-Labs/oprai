"""
Protocol Comparison Service

Compares DeFi protocols across multiple metrics:
- APY/Yield comparison
- TVL (Total Value Locked)
- Risk assessment
- Performance history
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Risk scores by protocol (1-10, higher = riskier)
PROTOCOL_RISK_SCORES = {
    # Liquid Staking
    "jito": 3,
    "marinade": 3,
    "jup.sol": 3,
    # Lending
    "kamino": 4,
    "solend": 5,
    "francium": 6,
    "apricot": 6,
    "larix": 6,
    "port finance": 7,
    # DEX (for liquidity provision)
    "raydium": 4,
    "orca": 4,
    "meteora": 5,
    "goosefx": 6,
    "aldrin": 7,
    "dexlab": 7,
}

# Protocol categories
PROTOCOL_CATEGORIES = {
    "jito": "liquid_staking",
    "marinade": "liquid_staking",
    "jup.sol": "liquid_staking",
    "kamino": "lending",
    "solend": "lending",
    "francium": "lending",
    "apricot": "lending",
    "larix": "lending",
    "port finance": "lending",
    "raydium": "dex",
    "orca": "dex",
    "meteora": "dex",
    "goosefx": "dex",
    "aldrin": "dex",
    "dexlab": "dex",
}


def get_protocol_risk_score(protocol: str) -> int:
    """Get risk score for a protocol (1-10)"""
    return PROTOCOL_RISK_SCORES.get(protocol.lower(), 5)  # Default 5 (medium)


def get_protocol_category(protocol: str) -> str:
    """Get category for a protocol"""
    return PROTOCOL_CATEGORIES.get(protocol.lower(), "unknown")


def compare_by_apy(protocols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compare protocols by APY, returns sorted by highest APY"""
    comparisons = []
    for p in protocols:
        apy = p.get("apy", 0) or 0
        if apy > 0:
            comparisons.append({
                "protocol": p.get("protocol", "unknown"),
                "apy": apy,
                "category": get_protocol_category(p.get("protocol", "")),
                "risk_score": get_protocol_risk_score(p.get("protocol", "")),
            })

    return sorted(comparisons, key=lambda x: x["apy"], reverse=True)


def compare_by_tvl(protocols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compare protocols by TVL, returns sorted by highest TVL"""
    comparisons = []
    for p in protocols:
        tvl = p.get("tvl", p.get("tvl_usd", 0)) or 0
        if tvl > 0:
            comparisons.append({
                "protocol": p.get("protocol", "unknown"),
                "tvl": tvl,
                "category": get_protocol_category(p.get("protocol", "")),
                "risk_score": get_protocol_risk_score(p.get("protocol", "")),
            })

    return sorted(comparisons, key=lambda x: x["tvl"], reverse=True)


def compare_by_risk(protocols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compare protocols by risk score, returns sorted by lowest risk"""
    comparisons = []
    for p in protocols:
        risk = get_protocol_risk_score(p.get("protocol", ""))
        comparisons.append({
            "protocol": p.get("protocol", "unknown"),
            "risk_score": risk,
            "risk_level": "low" if risk <= 3 else "medium" if risk <= 6 else "high",
            "category": get_protocol_category(p.get("protocol", "")),
            "apy": p.get("apy", 0) or 0,
        })

    return sorted(comparisons, key=lambda x: x["risk_score"])


def calculate_risk_adjusted_apy(protocol: dict[str, Any]) -> float:
    """Calculate risk-adjusted APY (APY / risk_score)"""
    apy = protocol.get("apy", 0) or 0
    risk = get_protocol_risk_score(protocol.get("protocol", ""))
    if risk == 0:
        return apy
    return apy / (risk / 2)  # Normalize: risk 1 = 2x, risk 10 = 0.2x


def compare_by_risk_adjusted(
    protocols: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Compare protocols by risk-adjusted APY"""
    comparisons = []
    for p in protocols:
        apy = p.get("apy", 0) or 0
        if apy > 0:
            risk_adjusted = calculate_risk_adjusted_apy(p)
            comparisons.append({
                "protocol": p.get("protocol", "unknown"),
                "apy": apy,
                "risk_score": get_protocol_risk_score(p.get("protocol", "")),
                "risk_adjusted_apy": round(risk_adjusted, 2),
                "category": get_protocol_category(p.get("protocol", "")),
            })

    return sorted(comparisons, key=lambda x: x["risk_adjusted_apy"], reverse=True)


def compare_protocols(
    protocols: list[dict[str, Any]],
    sort_by: str = "apy",
    category: str | None = None,
) -> dict[str, Any]:
    """
    Compare protocols with various sorting options.

    Args:
        protocols: List of protocol data with apy, tvl fields
        sort_by: Sort method - "apy", "tvl", "risk", "risk_adjusted"
        category: Optional filter by category (liquid_staking, lending, dex)

    Returns:
        Dictionary with comparison results
    """
    # Filter by category if specified
    filtered = protocols
    if category:
        filtered = [
            p for p in protocols
            if get_protocol_category(p.get("protocol", "")) == category
        ]

    if not filtered:
        return {
            "comparison": [],
            "count": 0,
            "sort_by": sort_by,
            "category": category,
        }

    # Sort based on requested method
    if sort_by == "apy":
        sorted_protocols = compare_by_apy(filtered)
    elif sort_by == "tvl":
        sorted_protocols = compare_by_tvl(filtered)
    elif sort_by == "risk":
        sorted_protocols = compare_by_risk(filtered)
    elif sort_by == "risk_adjusted":
        sorted_protocols = compare_by_risk_adjusted(filtered)
    else:
        sorted_protocols = compare_by_apy(filtered)

    # Get best options
    best_apy = sorted_protocols[0] if sorted_protocols else None
    lowest_risk = min(sorted_protocols, key=lambda x: x["risk_score"]) if sorted_protocols else None
    best_risk_adjusted = max(sorted_protocols, key=lambda x: x.get("risk_adjusted_apy", 0)) if sorted_protocols else None

    return {
        "comparison": sorted_protocols,
        "count": len(sorted_protocols),
        "sort_by": sort_by,
        "category": category,
        "best_apy": best_apy,
        "lowest_risk": lowest_risk,
        "best_risk_adjusted": best_risk_adjusted,
    }


async def get_protocol_comparison(
    category: str | None = None,
    sort_by: str = "apy",
    limit: int = 10,
) -> dict[str, Any]:
    """Get protocol comparison with live yield data"""
    from app.services.yield_aggregator import get_yield_comparison

    # Fetch yields from all categories
    all_yields = []
    for cat in ["liquid_staking", "lending"]:
        try:
            yields = await get_yield_comparison(cat)
            all_yields.extend(yields)
        except Exception as e:
            logger.warning("Failed to fetch yields for {cat}", exc_info=True)

    # Get comparison
    result = compare_protocols(all_yields, sort_by=sort_by, category=category)

    # Apply limit
    result["comparison"] = result["comparison"][:limit]
    result["limited"] = len(all_yields) > limit

    return result
