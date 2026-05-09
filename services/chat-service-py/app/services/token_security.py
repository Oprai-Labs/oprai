"""
Token Security Service

Security analysis for Solana tokens:
- Rug pull detection
- Holder distribution analysis
- Mint authority verification
- Liquidity analysis
- Suspicious patterns detection
"""

import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Risk thresholds
TOP_HOLDER_THRESHOLD = 10  # Top 10 holders should not exceed these percentages
SUSPICIOUS_TOP_HOLDER_PCT = 30  # %30+ in single wallet = suspicious
HIGH_CONCENTRATION_PCT = 50  # %50+ in top 10 = dangerous
MIN_LIQUIDITY_USD = 10000  # Minimum liquidity to be considered safe
MIN_HOLDERS = 10  # Minimum number of holders

# Suspicious patterns
SUSPICIOUS_MINT_PATTERNS = [
    "renounced", "mint_disabled", "no_mint",
]
SUSPICIOUS_TRANSFER_PATTERNS = [
    "no_freeze", "no_transfer_fee",
]


@dataclass
class SecurityScore:
    """Security score breakdown"""
    overall: int  # 0-100, higher = safer
    rug_pull_risk: int  # 0-100, lower = more risky
    liquidity_score: int  # 0-100
    holder_distribution_score: int  # 0-100
    mint_security_score: int  # 0-100


def analyze_holder_distribution(
    holders: List[Dict[str, Any]],
    total_supply: float
) -> Dict[str, Any]:
    """
    Analyze token holder distribution for concentration risks.

    Args:
        holders: List of holders with 'address' and 'amount'/'percentage'
        total_supply: Total token supply

    Returns:
        Holder distribution analysis
    """
    if not holders or total_supply <= 0:
        return {
            "total_holders": 0,
            "top_10_percent": 0,
            "concentration_risk": "unknown",
            "score": 0,
            "is_suspicious": True,
        }

    # Sort holders by amount descending
    sorted_holders = sorted(
        holders,
        key=lambda h: h.get("amount", h.get("uiAmount", 0)),
        reverse=True
    )

    # Calculate percentages
    total_percentage = 0
    top_10_percentage = 0

    for i, holder in enumerate(sorted_holders):
        pct = holder.get("pct", holder.get("percentage", 0))
        total_percentage += pct
        if i < 10:
            top_10_percentage += pct

    # Determine risk level
    if top_10_percentage >= HIGH_CONCENTRATION_PCT:
        concentration_risk = "critical"
        score = 20
    elif top_10_percentage >= SUSPICIOUS_TOP_HOLDER_PCT:
        concentration_risk = "high"
        score = 40
    elif top_10_percentage >= 20:
        concentration_risk = "medium"
        score = 60
    else:
        concentration_risk = "low"
        score = 80

    # Check for single wallet dominance
    top_holder_pct = sorted_holders[0].get("pct", 0) if sorted_holders else 0
    is_suspicious = top_holder_pct >= SUSPICIOUS_TOP_HOLDER_PCT

    return {
        "total_holders": len(holders),
        "top_10_percent": round(top_10_percentage, 2),
        "top_1_percent": round(top_holder_pct, 2),
        "concentration_risk": concentration_risk,
        "score": score,
        "is_suspicious": is_suspicious,
        "top_holders": [
            {
                "rank": i + 1,
                "address": h.get("address", "")[:8] + "...",
                "percentage": round(h.get("pct", 0), 2)
            }
            for i, h in enumerate(sorted_holders[:5])
        ]
    }


def check_mint_authority(
    mint_authority: Optional[str],
    freeze_authority: Optional[str],
    supply: float,
    decimals: int
) -> Dict[str, Any]:
    """
    Analyze mint and freeze authorities for security.

    Args:
        mint_authority: Address that can mint new tokens (None = renounced)
        freeze_authority: Address that can freeze accounts (None = disabled)
        supply: Current token supply
        decimals: Token decimals

    Returns:
        Mint security analysis
    """
    # Mint authority analysis
    if mint_authority is None or mint_authority == "":
        mint_status = "renounced"
        mint_score = 100
    else:
        mint_status = "active"
        mint_score = 30  # Can mint more tokens (inflation risk)

    # Freeze authority analysis
    if freeze_authority is None or freeze_authority == "":
        freeze_status = "disabled"
        freeze_score = 100
    else:
        freeze_status = "active"
        freeze_score = 50  # Can freeze user funds

    # Combined score
    overall_score = (mint_score + freeze_score) // 2

    # Warnings
    warnings = []
    if mint_status == "active":
        warnings.append("Mint authority is active - tokens can be minted")
    if freeze_status == "active":
        warnings.append("Freeze authority is active - funds can be frozen")

    return {
        "mint_authority": mint_authority[:8] + "..." if mint_authority else None,
        "mint_status": mint_status,
        "mint_score": mint_score,
        "freeze_authority": freeze_authority[:8] + "..." if freeze_authority else None,
        "freeze_status": freeze_status,
        "freeze_score": freeze_score,
        "overall_score": overall_score,
        "warnings": warnings,
        "is_secure": overall_score >= 70
    }


def analyze_liquidity(
    liquidity_usd: Optional[float],
    market_cap_usd: Optional[float],
    pair_address: Optional[str] = None
) -> Dict[str, Any]:
    """
    Analyze token liquidity for rug pull risk.

    Args:
        liquidity_usd: Liquidity in USD
        market_cap_usd: Market cap in USD
        pair_address: DEX pair address

    Returns:
        Liquidity analysis
    """
    if liquidity_usd is None or liquidity_usd <= 0:
        return {
            "liquidity_usd": 0,
            "market_cap_usd": market_cap_usd or 0,
            "liquidity_risk": "unknown",
            "score": 0,
            "is_suspicious": True,
            "warnings": ["No liquidity data available"]
        }

    warnings = []
    score = 50

    # Check liquidity level
    if liquidity_usd < MIN_LIQUIDITY_USD:
        liquidity_risk = "critical"
        score = 10
        warnings.append(f"Very low liquidity (${liquidity_usd:,.0f})")
    elif liquidity_usd < 50000:
        liquidity_risk = "high"
        score = 30
        warnings.append(f"Low liquidity (${liquidity_usd:,.0f})")
    elif liquidity_usd < 100000:
        liquidity_risk = "medium"
        score = 60
    else:
        liquidity_risk = "low"
        score = 90

    # Check liquidity to market cap ratio (if available)
    if market_cap_usd and market_cap_usd > 0:
        liq_mc_ratio = liquidity_usd / market_cap_usd
        if liq_mc_ratio < 0.01:  # Less than 1%
            warnings.append("Very low liquidity relative to market cap")
            score = max(score - 20, 10)
        elif liq_mc_ratio < 0.05:  # Less than 5%
            warnings.append("Low liquidity relative to market cap")
            score = max(score - 10, 20)

    is_suspicious = liquidity_risk in ["critical", "high"]

    return {
        "liquidity_usd": round(liquidity_usd, 2),
        "market_cap_usd": round(market_cap_usd or 0, 2),
        "liquidity_risk": liquidity_risk,
        "score": score,
        "is_suspicious": is_suspicious,
        "pair_address": pair_address[:8] + "..." if pair_address else None,
        "warnings": warnings
    }


def detect_suspicious_patterns(
    metadata: Dict[str, Any],
    transfers: List[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Detect suspicious patterns in token creation and transfers.

    Args:
        metadata: Token metadata (name, symbol, creation info)
        transfers: Optional list of recent transfers

    Returns:
        Suspicious pattern analysis
    """
    patterns_found = []
    risk_score = 0

    # Check metadata for suspicious patterns
    name = metadata.get("name", "").lower()
    symbol = metadata.get("symbol", "").lower()

    # Generic/suspicious names
    suspicious_names = ["test", "fake", "scam", "ponzi", "airdrop"]
    if any(s in name for s in suspicious_names):
        patterns_found.append(f"Suspicious token name: {metadata.get('name')}")
        risk_score += 30

    # Very short or no symbol
    if not symbol or len(symbol) < 2:
        patterns_found.append("Missing or very short token symbol")
        risk_score += 10

    # Check transfers if provided
    if transfers:
        # Check for sudden large transfers
        amounts = [t.get("amount", 0) for t in transfers]
        if amounts:
            avg_amount = sum(amounts) / len(amounts)
            for t in transfers:
                if t.get("amount", 0) > avg_amount * 10:
                    patterns_found.append("Large outlier transfer detected")
                    risk_score += 20
                    break

    # Creation date check (if available)
    if metadata.get("created_at"):
        # Could add logic to flag newly created tokens
        pass

    return {
        "patterns_found": patterns_found,
        "risk_score": min(risk_score, 100),
        "is_suspicious": risk_score >= 30,
        "warnings": patterns_found
    }


def calculate_rug_pull_score(
    holder_analysis: Dict[str, Any],
    liquidity_analysis: Dict[str, Any],
    mint_analysis: Dict[str, Any],
    pattern_analysis: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Calculate overall rug pull risk score.

    Args:
        holder_analysis: Holder distribution analysis
        liquidity_analysis: Liquidity analysis
        mint_analysis: Mint authority analysis
        pattern_analysis: Suspicious patterns analysis

    Returns:
        Rug pull risk assessment
    """
    # Weighted scoring
    holder_weight = 0.30
    liquidity_weight = 0.35
    mint_weight = 0.20
    pattern_weight = 0.15

    # Invert scores (lower = more risky for holders/liquidity)
    holder_score = 100 - holder_analysis.get("score", 50)
    liquidity_score = 100 - liquidity_analysis.get("score", 50)
    mint_score = 100 - mint_analysis.get("overall_score", 50)
    pattern_score = pattern_analysis.get("risk_score", 0)

    # Weighted total
    rug_score = (
        holder_score * holder_weight +
        liquidity_score * liquidity_weight +
        mint_score * mint_weight +
        pattern_score * pattern_weight
    )

    # Determine risk level
    if rug_score >= 70:
        risk_level = "critical"
        description = "HIGH RUG PULL RISK - Do not buy"
    elif rug_score >= 50:
        risk_level = "high"
        description = "Elevated risk - Exercise caution"
    elif rug_score >= 30:
        risk_level = "medium"
        description = "Moderate risk - Research further"
    else:
        risk_level = "low"
        description = "Appears relatively safe"

    return {
        "rug_pull_score": round(rug_score, 1),
        "risk_level": risk_level,
        "description": description,
        "components": {
            "holder_score": holder_score,
            "liquidity_score": liquidity_score,
            "mint_score": mint_score,
            "pattern_score": pattern_score
        }
    }


async def analyze_token_security(
    token_address: str,
    holders: Optional[List[Dict[str, Any]]] = None,
    liquidity_usd: Optional[float] = None,
    market_cap_usd: Optional[float] = None,
    mint_authority: Optional[str] = None,
    freeze_authority: Optional[str] = None,
    supply: float = 0,
    decimals: int = 9,
    metadata: Optional[Dict[str, Any]] = None,
    pair_address: Optional[str] = None,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """
    Complete token security analysis.

    Args:
        token_address: Token mint address
        holders: Optional list of top holders
        liquidity_usd: Optional liquidity in USD
        market_cap_usd: Optional market cap in USD
        mint_authority: Optional mint authority address
        freeze_authority: Optional freeze authority address
        supply: Token supply
        decimals: Token decimals
        metadata: Optional token metadata
        pair_address: Optional DEX pair address
        use_cache: Whether to use caching (default True)

    Returns:
        Complete security analysis
    """
    # Try to get from cache
    if use_cache:
        from app.services.cache import get_cache_service

        cache = await get_cache_service()
        cached = await cache.get("token:security", token_address)
        if cached is not None:
            logger.debug(f"Using cached security analysis for {token_address[:8]}...")
            return cached

    # Run all analyses
    holder_analysis = analyze_holder_distribution(
        holders or [],
        supply
    )

    liquidity_analysis = analyze_liquidity(
        liquidity_usd,
        market_cap_usd,
        pair_address
    )

    mint_analysis = check_mint_authority(
        mint_authority,
        freeze_authority,
        supply,
        decimals
    )

    pattern_analysis = detect_suspicious_patterns(
        metadata or {}
    )

    # Calculate rug pull risk
    rug_analysis = calculate_rug_pull_score(
        holder_analysis,
        liquidity_analysis,
        mint_analysis,
        pattern_analysis
    )

    # Overall security score (higher = safer)
    overall_score = (
        holder_analysis.get("score", 50) * 0.25 +
        liquidity_analysis.get("score", 50) * 0.30 +
        mint_analysis.get("overall_score", 50) * 0.25 +
        (100 - pattern_analysis.get("risk_score", 0)) * 0.20
    )

    # Combine all warnings
    all_warnings = []
    all_warnings.extend(holder_analysis.get("warnings", []))
    all_warnings.extend(liquidity_analysis.get("warnings", []))
    all_warnings.extend(mint_analysis.get("warnings", []))
    all_warnings.extend(pattern_analysis.get("warnings", []))

    result = {
        "token_address": token_address,
        "overall_score": round(overall_score, 1),
        "risk_level": rug_analysis["risk_level"],
        "rug_pull_analysis": rug_analysis,
        "holder_analysis": holder_analysis,
        "liquidity_analysis": liquidity_analysis,
        "mint_analysis": mint_analysis,
        "pattern_analysis": pattern_analysis,
        "warnings": all_warnings,
        "recommendation": _generate_recommendation(rug_analysis, overall_score)
    }

    # Cache the result
    if use_cache:
        from app.services.cache import get_cache_service

        cache = await get_cache_service()
        await cache.set("token:security", result, token_address)

    return result


def _generate_recommendation(rug_analysis: Dict, overall_score: float) -> str:
    """Generate recommendation based on analysis"""
    risk_level = rug_analysis.get("risk_level", "unknown")

    if risk_level == "critical":
        return "AVOID - High probability of rug pull"
    elif risk_level == "high":
        return "Exercise extreme caution if buying"
    elif risk_level == "medium":
        return "Only buy if you understand the risks"
    elif overall_score >= 70:
        return "Relatively safe - always do your own research"
    else:
        return "Proceed with caution"


# ----------------------------------------------------------------------
# Integration with external APIs (Birdeye, Solscan)
# ----------------------------------------------------------------------
async def fetch_token_data_from_rpc(
    rpc_client: Any,
    token_address: str
) -> Dict[str, Any]:
    """Fetch token data from Solana RPC"""
    # This would use solana RPC to get token supply, mint info
    # Placeholder for actual implementation
    return {}


async def get_token_security_from_api(
    token_address: str,
    api_key: Optional[str] = None
) -> Dict[str, Any]:
    """
    Fetch token security data from external APIs (Birdeye, DexScreener, etc.)

    This is a placeholder that would integrate with:
    - Birdeye API for holder data
    - DexScreener for liquidity/pair data
    - Solscan API for transaction history
    """
    # In production, this would call external APIs
    # For now, return structure that can be filled
    return {
        "token_address": token_address,
        "holders": None,
        "liquidity_usd": None,
        "market_cap_usd": None,
        "mint_authority": None,
        "freeze_authority": None,
        "supply": None,
        "metadata": None,
        "pair_address": None,
    }
