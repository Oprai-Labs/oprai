"""
Portfolio Optimizer Service

Analyzes user portfolio and suggests optimizations:
- Rebalancing recommendations
- Yield improvement suggestions
- Risk diversification
- Tax-loss harvesting opportunities
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default risk tolerance levels
RISK_LEVELS = {
    "conservative": 0.3,  # Max 30% in single protocol
    "moderate": 0.5,      # Max 50% in single protocol
    "aggressive": 0.7,   # Max 70% in single protocol
}


def calculate_diversification_score(positions: list[dict[str, Any]]) -> float:
    """Calculate portfolio diversification score (0-100)"""
    if not positions:
        return 0.0

    # Count unique protocols
    protocols = set(p.get("protocol", "unknown") for p in positions)
    unique_count = len(protocols)

    # More protocols = better diversification (up to 5)
    return min(100.0, unique_count * 20.0)


def identify_concentration_risks(
    positions: list[dict[str, Any]],
    risk_tolerance: str = "moderate"
) -> list[dict[str, Any]]:
    """Identify positions with concentration risk"""
    risks = []
    max_allocation = RISK_LEVELS.get(risk_tolerance, 0.5)

    # Calculate total value
    total_value = sum(p.get("value_usd", 0) for p in positions)
    if total_value == 0:
        return risks

    # Check each position
    for pos in positions:
        value = pos.get("value_usd", 0)
        allocation = (value / total_value) * 100

        if allocation > (max_allocation * 100):
            risks.append({
                "protocol": pos.get("protocol", "unknown"),
                "token": pos.get("token", "unknown"),
                "allocation_percent": round(allocation, 2),
                "risk_level": "high" if allocation > 60 else "medium",
                "recommendation": f"Consider reducing {pos.get('protocol')} exposure"
            })

    return sorted(risks, key=lambda x: x["allocation_percent"], reverse=True)


def suggest_rebalancing(
    positions: list[dict[str, Any]],
    yields: list[dict[str, Any]],
    target_allocation: dict[str, float] | None = None
) -> list[dict[str, Any]]:
    """Suggest portfolio rebalancing based on yields and current positions"""
    suggestions = []

    # Get current allocations
    total_value = sum(p.get("value_usd", 0) for p in positions)
    if total_value == 0:
        return suggestions

    # Current protocol allocations
    current_alloc: dict[str, float] = {}
    for pos in positions:
        protocol = pos.get("protocol", "unknown")
        value = pos.get("value_usd", 0)
        current_alloc[protocol] = current_alloc.get(protocol, 0) + value

    # Convert to percentages
    for protocol in current_alloc:
        current_alloc[protocol] = (current_alloc[protocol] / total_value) * 100

    # Create yield map
    yield_map = {y.get("protocol"): y.get("apy", 0) for y in yields}

    # Default target: equal distribution across protocols in yield_map
    if target_allocation is None:
        protocols_with_yield = list(yield_map.keys())
        if protocols_with_yield:
            target = 100.0 / len(protocols_with_yield)
            target_allocation = {p: target for p in protocols_with_yield}

    # Compare current vs target
    for protocol, target_pct in (target_allocation or {}).items():
        current_pct = current_alloc.get(protocol, 0)
        diff = target_pct - current_pct

        if abs(diff) > 5:  # Only suggest if >5% difference
            current_apy = yield_map.get(protocol, 0)
            suggestions.append({
                "protocol": protocol,
                "current_percent": round(current_pct, 2),
                "target_percent": round(target_pct, 2),
                "difference_percent": round(diff, 2),
                "current_apy": current_apy,
                "action": "increase" if diff > 0 else "decrease",
                "reason": f"Target allocation is {target_pct}%, current is {current_pct}%"
            })

    return sorted(suggestions, key=lambda x: abs(x["difference_percent"]), reverse=True)


def identify_tax_loss_harvesting(
    positions: list[dict[str, Any]],
    prices_24h: dict[str, float]
) -> list[dict[str, Any]]:
    """Identify potential tax-loss harvesting opportunities"""
    opportunities = []

    for pos in positions:
        token = pos.get("token", "")
        cost_basis = pos.get("cost_basis", 0)
        current_value = pos.get("value_usd", 0)

        if cost_basis > 0 and current_value < cost_basis:
            loss = current_value - cost_basis
            loss_pct = (loss / cost_basis) * 100

            # Consider harvesting if loss > 10%
            if loss_pct < -10:
                opportunities.append({
                    "token": token,
                    "protocol": pos.get("protocol", "unknown"),
                    "cost_basis": cost_basis,
                    "current_value": current_value,
                    "unrealized_loss": round(loss, 2),
                    "loss_percent": round(loss_pct, 2),
                    "recommendation": f"Consider selling {token} to harvest ${abs(round(loss, 2))} loss"
                })

    return sorted(opportunities, key=lambda x: x["unrealized_loss"], reverse=True)


def analyze_portfolio(
    positions: list[dict[str, Any]],
    yields: list[dict[str, Any]],
    risk_tolerance: str = "moderate",
    prices_24h: dict[str, float] | None = None
) -> dict[str, Any]:
    """Complete portfolio analysis with recommendations"""

    total_value = sum(p.get("value_usd", 0) for p in positions)

    # Basic analysis
    diversification = calculate_diversification_score(positions)
    concentration = identify_concentration_risks(positions, risk_tolerance)
    rebalancing = suggest_rebalancing(positions, yields)

    # Tax-loss harvesting (if price data available)
    tax_loss = []
    if prices_24h:
        tax_loss = identify_tax_loss_harvesting(positions, prices_24h)

    # Calculate weighted average yield
    weighted_yield = 0.0
    if total_value > 0:
        for pos in positions:
            protocol = pos.get("protocol", "")
            value = pos.get("value_usd", 0)
            for y in yields:
                if y.get("protocol") == protocol:
                    weighted_yield += (value / total_value) * y.get("apy", 0)
                    break

    return {
        "summary": {
            "total_value_usd": round(total_value, 2),
            "position_count": len(positions),
            "diversification_score": round(diversification, 1),
            "weighted_average_apy": round(weighted_yield, 2),
        },
        "concentration_risks": concentration,
        "rebalancing_suggestions": rebalancing,
        "tax_loss_opportunities": tax_loss,
        "overall_health": "good" if diversification >= 60 and len(concentration) == 0 else "needs_attention"
    }


async def optimize_portfolio(
    positions: list[dict[str, Any]],
    category: str = "liquid_staking",
    risk_tolerance: str = "moderate"
) -> dict[str, Any]:
    """Main portfolio optimization function"""

    # Import here to avoid circular imports
    from app.services.yield_aggregator import get_yield_comparison

    # Get current yields
    yields = await get_yield_comparison(category)

    # Analyze portfolio
    return analyze_portfolio(positions, yields, risk_tolerance, None)