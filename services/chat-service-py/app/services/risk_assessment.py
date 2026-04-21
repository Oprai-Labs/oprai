"""
Risk Assessment Service

Detailed risk analysis for DeFi positions:
- Impermanent Loss calculation for LP positions
- Liquidation Risk analysis for lending/borrowing
- Protocol risk scoring
- Position-level risk assessment
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Constants
LIQUIDATION_THRESHOLD_COLLATERAL = 0.25  # 25% health factor trigger
CRITICAL_HEALTH_FACTOR = 1.1
WARNING_HEALTH_FACTOR = 1.5
SAFE_HEALTH_FACTOR = 2.0


def calculate_impermanent_loss(
    initial_price: float,
    current_price: float,
    is_stable_pair: bool = False,
) -> Dict[str, Any]:
    """
    Calculate impermanent loss for a liquidity position.

    Args:
        initial_price: Price when position was opened (tokenB/tokenA)
        current_price: Current price (tokenB/tokenA)
        is_stable_pair: Whether it's a stablecoin pair (lower IL)

    Returns:
        Dictionary with IL percentage and analysis
    """
    if initial_price <= 0 or current_price <= 0:
        return {
            "impermanent_loss_percent": 0,
            "direction": "neutral",
            "severity": "unknown",
            "description": "Invalid price data",
        }

    price_ratio = current_price / initial_price
    sqrt_ratio = price_ratio ** 0.5

    # IL formula: 2 * sqrt(price_ratio) / (1 + price_ratio) - 1
    il_factor = (2 * sqrt_ratio) / (1 + price_ratio) - 1
    il_percent = il_factor * 100

    # Determine severity
    abs_il = abs(il_percent)
    if is_stable_pair:
        severity = "low" if abs_il < 5 else "medium" if abs_il < 15 else "high"
    else:
        severity = "low" if abs_il < 10 else "medium" if abs_il < 25 else "high"

    # Direction
    if current_price > initial_price:
        direction = "loss"  # Price went up, LP lost vs holding
    elif current_price < initial_price:
        direction = "loss"  # Price went down, LP lost vs holding
    else:
        direction = "none"

    return {
        "impermanent_loss_percent": round(il_percent, 2),
        "price_change_percent": round((price_ratio - 1) * 100, 2),
        "direction": direction,
        "severity": severity,
        "is_stable_pair": is_stable_pair,
        "description": f"IL of {abs(il_percent):.2f}% due to {((price_ratio - 1) * 100):.1f}% price change",
    }


def calculate_liquidation_risk(
    collateral_value: float,
    debt_value: float,
    collateral_price: float,
    debt_price: float,
    liquidation_threshold: float = 0.5,
) -> Dict[str, Any]:
    """
    Calculate liquidation risk for a lending position.

    Args:
        collateral_value: Value of collateral in USD
        debt_value: Value of borrowed debt in USD
        collateral_price: Current price of collateral token
        debt_price: Current price of debt token
        liquidation_threshold: Protocol-specific threshold (default 0.5 = 50%)

    Returns:
        Dictionary with liquidation risk analysis
    """
    if collateral_value <= 0 or debt_value <= 0:
        return {
            "health_factor": float("inf"),
            "risk_level": "none",
            "liquidation_distance_percent": None,
            "description": "No debt or collateral",
        }

    # Health factor = (collateral * threshold) / debt
    health_factor = (collateral_value * liquidation_threshold) / debt_value

    # Calculate distance to liquidation
    if health_factor > 1:
        distance_to_liq = ((health_factor - 1) / health_factor) * 100
    else:
        distance_to_liq = 0

    # Determine risk level
    if health_factor >= SAFE_HEALTH_FACTOR:
        risk_level = "safe"
    elif health_factor >= WARNING_HEALTH_FACTOR:
        risk_level = "warning"
    elif health_factor >= CRITICAL_HEALTH_FACTOR:
        risk_level = "critical"
    else:
        risk_level = "liquidation_imminent"

    # Calculate max borrow before liquidation
    max_borrow = (collateral_value * liquidation_threshold) / CRITICAL_HEALTH_FACTOR
    borrow_headroom = max_borrow - debt_value

    return {
        "health_factor": round(health_factor, 2),
        "collateral_value": round(collateral_value, 2),
        "debt_value": round(debt_value, 2),
        "liquidation_threshold": liquidation_threshold,
        "risk_level": risk_level,
        "liquidation_distance_percent": round(distance_to_liq, 2),
        "borrow_headroom_usd": round(borrow_headroom, 2),
        "description": f"Health factor {health_factor:.2f} - {risk_level}",
    }


def assess_position_risk(
    position: Dict[str, Any],
    current_prices: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    Assess risk for a single position.

    Args:
        position: Position data with type, values, etc.
        current_prices: Optional dict of current token prices

    Returns:
        Risk assessment with score and recommendations
    """
    position_type = position.get("type", position.get("protocol", "unknown"))
    protocol = position.get("protocol", "unknown")
    value_usd = position.get("value_usd", 0)

    risks = []
    overall_score = 0  # 0-100, higher = riskier
    max_score = 100

    # Protocol-based risk
    protocol_risk = {
        "jito": 3,
        "marinade": 3,
        "kamino": 4,
        "marginfi": 5,
        "solend": 5,
        "raydium": 4,
        "orca": 4,
        "meteora": 5,
    }.get(protocol.lower(), 5)

    if protocol_risk >= 5:
        risks.append({
            "type": "protocol",
            "severity": "medium",
            "description": f"Protocol {protocol} has elevated risk profile",
        })
    overall_score += protocol_risk * 2

    # Value-based risk (large positions = higher risk)
    if value_usd > 10000:
        risks.append({
            "type": "concentration",
            "severity": "medium",
            "description": f"Large position value (${value_usd:,.0f}) increases exposure",
        })
        overall_score += 10
    elif value_usd > 50000:
        risks.append({
            "type": "concentration",
            "severity": "high",
            "description": f"Very large position (${value_usd:,.0f}) - consider diversification",
        })
        overall_score += 20

    # Type-specific risk
    if "lend" in position_type.lower() or "borrow" in position_type.lower():
        # Check for liquidation risk if we have the data
        collateral = position.get("collateral_usd", 0)
        debt = position.get("debt_usd", 0)
        if collateral > 0 and debt > 0:
            liq_risk = calculate_liquidation_risk(
                collateral, debt,
                position.get("collateral_price", 1),
                position.get("debt_price", 1),
            )
            if liq_risk["risk_level"] in ["critical", "liquidation_imminent"]:
                risks.append({
                    "type": "liquidation",
                    "severity": "high",
                    "description": liq_risk["description"],
                })
                overall_score += 30
            elif liq_risk["risk_level"] == "warning":
                risks.append({
                    "type": "liquidation",
                    "severity": "medium",
                    "description": liq_risk["description"],
                })
                overall_score += 15

    elif "lp" in position_type.lower() or "liquidity" in position_type.lower():
        # Check for impermanent loss
        initial_price = position.get("initial_price", 0)
        current_price = position.get("current_price", 0)
        is_stable = position.get("is_stable_pair", False)

        if initial_price > 0 and current_price > 0:
            il = calculate_impermanent_loss(initial_price, current_price, is_stable)
            if il["severity"] in ["medium", "high"]:
                risks.append({
                    "type": "impermanent_loss",
                    "severity": il["severity"],
                    "description": il["description"],
                })
                overall_score += 15 if il["severity"] == "medium" else 25

    # Cap score at 100
    overall_score = min(overall_score, max_score)

    # Overall risk level
    if overall_score < 25:
        overall_level = "low"
    elif overall_score < 50:
        overall_level = "medium"
    elif overall_score < 75:
        overall_level = "high"
    else:
        overall_level = "very_high"

    return {
        "position_id": position.get("id", "unknown"),
        "protocol": protocol,
        "position_type": position_type,
        "value_usd": value_usd,
        "risk_score": overall_score,
        "risk_level": overall_level,
        "risks": risks,
        "recommendations": _generate_risk_recommendations(risks, overall_level),
    }


def _generate_risk_recommendations(risks: List[Dict], level: str) -> List[str]:
    """Generate recommendations based on identified risks"""
    recommendations = []

    for risk in risks:
        if risk["type"] == "concentration":
            recommendations.append("Consider diversifying position across multiple protocols")
        elif risk["type"] == "liquidation":
            recommendations.append("Add more collateral or reduce debt to improve health factor")
        elif risk["type"] == "impermanent_loss":
            recommendations.append("Consider stable pair LP or single-sided staking")
        elif risk["type"] == "protocol":
            recommendations.append("Research protocol security and audits before continuing")

    if level in ["high", "very_high"]:
        recommendations.append("Review all positions and consider risk reduction")

    return recommendations


def analyze_portfolio_risk(
    positions: List[Dict[str, Any]],
    current_prices: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    Analyze risk for entire portfolio.

    Args:
        positions: List of all positions
        current_prices: Optional token prices

    Returns:
        Portfolio-wide risk analysis
    """
    if not positions:
        return {
            "overall_risk_score": 0,
            "risk_level": "none",
            "positions_analyzed": 0,
            "high_risk_positions": [],
            "recommendations": [],
        }

    # Analyze each position
    position_risks = []
    high_risk_count = 0
    total_value = 0
    high_risk_value = 0

    for pos in positions:
        risk = assess_position_risk(pos, current_prices)
        position_risks.append(risk)

        value = pos.get("value_usd", 0)
        total_value += value

        if risk["risk_level"] in ["high", "very_high"]:
            high_risk_count += 1
            high_risk_value += value

    # Calculate portfolio-level metrics
    avg_risk_score = sum(p["risk_score"] for p in position_risks) / len(position_risks)

    # Weight risk by value
    if total_value > 0:
        weighted_risk = sum(
            p["risk_score"] * p.get("value_usd", 0)
            for p in position_risks
        ) / total_value
    else:
        weighted_risk = avg_risk_score

    # Determine overall level
    if weighted_risk < 25:
        overall_level = "low"
    elif weighted_risk < 50:
        overall_level = "medium"
    elif weighted_risk < 75:
        overall_level = "high"
    else:
        overall_level = "very_high"

    # Get high risk positions
    high_risk_positions = [
        {
            "protocol": p["protocol"],
            "value_usd": p["value_usd"],
            "risk_score": p["risk_score"],
            "risk_level": p["risk_level"],
            "primary_risks": [r["type"] for r in p["risks"]],
        }
        for p in position_risks
        if p["risk_level"] in ["high", "very_high"]
    ]

    # Generate recommendations
    all_recommendations = []
    if high_risk_count > 0:
        all_recommendations.append(f"Address {high_risk_count} high-risk positions")
    if high_risk_value / total_value > 0.5 if total_value > 0 else False:
        all_recommendations.append("Over 50% of portfolio in high-risk positions - diversify")
    if overall_level in ["high", "very_high"]:
        all_recommendations.append("Consider reducing overall portfolio risk")

    return {
        "overall_risk_score": round(weighted_risk, 1),
        "risk_level": overall_level,
        "positions_analyzed": len(positions),
        "total_value_usd": round(total_value, 2),
        "high_risk_count": high_risk_count,
        "high_risk_value_usd": round(high_risk_value, 2),
        "high_risk_positions": high_risk_positions,
        "position_risks": position_risks,
        "recommendations": all_recommendations,
    }
