"""
Real-time Analytics Service

Provides real-time portfolio analytics:
- Portfolio value tracking
- P&L calculations
- Protocol-level attribution
- Performance metrics
- Historical analysis
"""

import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

logger = logging.getLogger(__name__)


class MetricType(Enum):
    """Types of analytics metrics"""
    TOTAL_VALUE = "total_value"
    PNL = "pnl"
    APY = "apy"
    VOLUME = "volume"
    FEES = "fees"
    YIELD = "yield"


@dataclass
class PortfolioSnapshot:
    """A point-in-time snapshot of portfolio value"""
    timestamp: datetime
    total_value_usd: float
    sol_value_usd: float
    token_value_usd: float
    positions_count: int


@dataclass
class PnLEntry:
    """Profit/Loss entry"""
    timestamp: datetime
    realized_pnl: float
    unrealized_pnl: float
    yield_earned: float
    fees_paid: float
    transaction_count: int


@dataclass
class ProtocolAttribution:
    """Protocol-level performance attribution"""
    protocol: str
    category: str
    value_usd: float
    percentage: float
    pnl_usd: float
    apy: float
    risk_level: str


class RealTimeAnalytics:
    """
    Real-time portfolio analytics engine.
    Provides live metrics based on current positions and prices.
    """

    def __init__(self):
        # Cache for price data
        self._price_cache: Dict[str, float] = {}
        self._price_cache_time: Dict[str, datetime] = {}

    async def calculate_portfolio_metrics(
        self,
        positions: List[Dict[str, Any]],
        prices: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """
        Calculate comprehensive portfolio metrics.

        Args:
            positions: List of portfolio positions
            prices: Optional price map (token_address -> USD price)

        Returns:
            Portfolio metrics
        """
        if not positions:
            return self._empty_portfolio_metrics()

        # Calculate totals
        total_value = 0
        total_yield = 0
        sol_value = 0
        token_value = 0
        protocols = set()

        for pos in positions:
            value = pos.get("value_usd", 0)
            apy = pos.get("apy", 0) or 0

            total_value += value

            # Calculate daily yield estimate
            daily_yield = (value * apy / 100) / 365
            total_yield += daily_yield

            # Track protocol
            protocol = pos.get("protocol", "unknown")
            protocols.add(protocol)

            # SOL vs token breakdown
            token = pos.get("token", "").upper()
            if token in ["SOL", "WSOL"]:
                sol_value += value
            else:
                token_value += value

        # Calculate allocation by category
        allocations = self._calculate_allocations(positions)

        # Calculate concentration risk
        concentration = self._calculate_concentration(positions, total_value)

        return {
            "summary": {
                "total_value_usd": round(total_value, 2),
                "sol_value_usd": round(sol_value, 2),
                "token_value_usd": round(token_value, 2),
                "daily_yield_usd": round(total_yield, 2),
                "annual_yield_usd": round(total_yield * 365, 2),
                "positions_count": len(positions),
                "protocols_count": len(protocols),
            },
            "allocations": allocations,
            "concentration": concentration,
            "updated_at": datetime.utcnow().isoformat(),
        }

    async def calculate_pnl(
        self,
        positions: List[Dict[str, Any]],
        historical_snapshots: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Calculate P&L metrics.

        Args:
            positions: Current positions
            historical_snapshots: Historical portfolio snapshots

        Returns:
            P&L analysis
        """
        if not positions:
            return self._empty_pnl_metrics()

        # Calculate current unrealized P&L
        unrealized = 0
        for pos in positions:
            cost_basis = pos.get("cost_basis", 0)
            current_value = pos.get("value_usd", 0)
            if cost_basis > 0:
                unrealized += current_value - cost_basis

        # Calculate from historical if available
        realized = 0
        if historical_snapshots and len(historical_snapshots) >= 2:
            first = historical_snapshots[0]
            last = historical_snapshots[-1]
            initial_value = first.get("total_value_usd", 0)
            current_value = last.get("total_value_usd", 0)

            # Approximate realized as change minus unrealized
            total_change = current_value - initial_value
            realized = total_change - unrealized

        # P&L by token
        pnl_by_token = self._calculate_pnl_by_token(positions)

        return {
            "unrealized_pnl": round(unrealized, 2),
            "realized_pnl": round(realized, 2),
            "total_pnl": round(unrealized + realized, 2),
            "pnl_by_token": pnl_by_token,
            "updated_at": datetime.utcnow().isoformat(),
        }

    async def get_protocol_attribution(
        self,
        positions: List[Dict[str, Any]],
    ) -> List[ProtocolAttribution]:
        """
        Get protocol-level performance attribution.

        Args:
            positions: Portfolio positions

        Returns:
            List of protocol attributions sorted by value
        """
        if not positions:
            return []

        # Group by protocol
        protocol_data: Dict[str, Dict] = {}

        for pos in positions:
            protocol = pos.get("protocol", "unknown")
            category = pos.get("category", "unknown")
            value = pos.get("value_usd", 0)
            apy = pos.get("apy", 0) or 0
            cost_basis = pos.get("cost_basis", 0)

            if protocol not in protocol_data:
                protocol_data[protocol] = {
                    "protocol": protocol,
                    "category": category,
                    "value_usd": 0,
                    "pnl_usd": 0,
                    "apy": 0,
                    "count": 0,
                }

            protocol_data[protocol]["value_usd"] += value
            protocol_data[protocol]["count"] += 1

            # Calculate P&L
            if cost_basis > 0:
                protocol_data[protocol]["pnl_usd"] += value - cost_basis

            # Weighted APY
            if value > 0:
                current_apy = protocol_data[protocol]["apy"]
                weight = value / (protocol_data[protocol]["value_usd"])
                protocol_data[protocol]["apy"] = current_apy + (apy - current_apy) * weight

        # Calculate total for percentages
        total_value = sum(p["value_usd"] for p in protocol_data.values())

        # Convert to attribution objects
        attributions = []
        for protocol, data in protocol_data.items():
            pct = (data["value_usd"] / total_value * 100) if total_value > 0 else 0

            attributions.append(ProtocolAttribution(
                protocol=data["protocol"],
                category=data["category"],
                value_usd=round(data["value_usd"], 2),
                percentage=round(pct, 2),
                pnl_usd=round(data["pnl_usd"], 2),
                apy=round(data["apy"], 2),
                risk_level=self._get_protocol_risk_level(data["protocol"]),
            ))

        # Sort by value descending
        return sorted(attributions, key=lambda x: x.value_usd, reverse=True)

    async def get_performance_metrics(
        self,
        positions: List[Dict[str, Any]],
        timeframe_days: int = 30,
    ) -> Dict[str, Any]:
        """
        Calculate performance metrics.

        Args:
            positions: Current positions
            timeframe_days: Historical timeframe

        Returns:
            Performance metrics
        """
        # Calculate total value
        total_value = sum(p.get("value_usd", 0) for p in positions)

        if total_value == 0:
            return self._empty_performance_metrics()

        # Calculate weighted average APY
        weighted_apy = 0
        for pos in positions:
            value = pos.get("value_usd", 0)
            apy = pos.get("apy", 0) or 0
            if value > 0:
                weighted_apy += (value / total_value) * apy

        # Calculate yield earned (estimated)
        daily_yield = total_value * weighted_apy / 100 / 365
        period_yield = daily_yield * timeframe_days

        # Sharpe ratio approximation (simplified)
        # In production, would use historical volatility
        sharpe_approx = weighted_apy / 15  # Rough estimate

        # Max drawdown approximation
        max_drawdown_approx = 0.1  # Placeholder - would calculate from history

        return {
            "total_value_usd": round(total_value, 2),
            "weighted_apy": round(weighted_apy, 2),
            "period_yield_usd": round(period_yield, 2),
            "sharpe_ratio_approx": round(sharpe_approx, 2),
            "max_drawdown_approx": round(max_drawdown_approx * 100, 1),
            "timeframe_days": timeframe_days,
            "updated_at": datetime.utcnow().isoformat(),
        }

    async def get_risk_metrics(
        self,
        positions: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Calculate portfolio risk metrics.

        Args:
            positions: Portfolio positions

        Returns:
            Risk metrics
        """
        if not positions:
            return self._empty_risk_metrics()

        # Diversification score
        protocols = set(p.get("protocol") for p in positions)
        diversification_score = min(100, len(protocols) * 20)

        # Concentration risk
        total_value = sum(p.get("value_usd", 0) for p in positions)
        max_allocation = 0

        for pos in positions:
            value = pos.get("value_usd", 0)
            if total_value > 0:
                allocation = (value / total_value) * 100
                max_allocation = max(max_allocation, allocation)

        concentration_risk = "low" if max_allocation < 30 else "medium" if max_allocation < 50 else "high"

        # Category risk
        categories = set(p.get("category", "unknown") for p in positions)
        category_risk = "low" if len(categories) >= 3 else "medium" if len(categories) >= 2 else "high"

        # Overall risk score (0-100, higher = riskier)
        risk_score = 0
        risk_score += 50 if concentration_risk == "high" else 25 if concentration_risk == "medium" else 0
        risk_score += 30 if category_risk == "high" else 15 if category_risk == "medium" else 0
        risk_score += 20 if diversification_score < 40 else 0

        return {
            "diversification_score": diversification_score,
            "concentration_risk": concentration_risk,
            "category_risk": category_risk,
            "max_allocation_percent": round(max_allocation, 1),
            "protocol_count": len(protocols),
            "category_count": len(categories),
            "overall_risk_score": min(100, risk_score),
            "risk_level": "low" if risk_score < 30 else "medium" if risk_score < 60 else "high",
            "updated_at": datetime.utcnow().isoformat(),
        }

    # Helper methods
    def _calculate_allocations(self, positions: List[Dict]) -> Dict[str, float]:
        """Calculate allocation by category"""
        total = sum(p.get("value_usd", 0) for p in positions)
        if total == 0:
            return {}

        allocations = {}
        for pos in positions:
            category = pos.get("category", pos.get("protocol", "unknown"))
            value = pos.get("value_usd", 0)
            pct = (value / total) * 100
            allocations[category] = round(pct, 1)

        return allocations

    def _calculate_concentration(self, positions: List[Dict], total: float) -> Dict:
        """Calculate concentration metrics"""
        if total == 0:
            return {"risk": "unknown", "top_holder_pct": 0}

        # Find largest position
        max_pos = max(positions, key=lambda p: p.get("value_usd", 0))
        max_value = max_pos.get("value_usd", 0)
        top_pct = (max_value / total) * 100 if total > 0 else 0

        risk = "low" if top_pct < 30 else "medium" if top_pct < 50 else "high"

        return {
            "risk": risk,
            "top_holder_pct": round(top_pct, 1),
            "top_holder_protocol": max_pos.get("protocol", "unknown"),
        }

    def _calculate_pnl_by_token(self, positions: List[Dict]) -> List[Dict]:
        """Calculate P&L by token"""
        pnl_list = []

        for pos in positions:
            cost_basis = pos.get("cost_basis", 0)
            current_value = pos.get("value_usd", 0)

            if cost_basis > 0:
                pnl = current_value - cost_basis
                pnl_list.append({
                    "token": pos.get("token", "unknown"),
                    "protocol": pos.get("protocol", "unknown"),
                    "cost_basis": cost_basis,
                    "current_value": current_value,
                    "pnl": round(pnl, 2),
                    "pnl_percent": round((pnl / cost_basis) * 100, 2),
                })

        return sorted(pnl_list, key=lambda x: abs(x["pnl"]), reverse=True)

    def _get_protocol_risk_level(self, protocol: str) -> str:
        """Get risk level for protocol"""
        risk_map = {
            "jito": "low",
            "marinade": "low",
            "jup.sol": "low",
            "kamino": "medium",
            "solend": "medium",
            "raydium": "medium",
            "orca": "medium",
            "meteora": "medium",
        }
        return risk_map.get(protocol.lower(), "medium")

    def _empty_portfolio_metrics(self) -> Dict:
        return {
            "summary": {
                "total_value_usd": 0,
                "sol_value_usd": 0,
                "token_value_usd": 0,
                "daily_yield_usd": 0,
                "annual_yield_usd": 0,
                "positions_count": 0,
                "protocols_count": 0,
            },
            "allocations": {},
            "concentration": {"risk": "low", "top_holder_pct": 0},
            "updated_at": datetime.utcnow().isoformat(),
        }

    def _empty_pnl_metrics(self) -> Dict:
        return {
            "unrealized_pnl": 0,
            "realized_pnl": 0,
            "total_pnl": 0,
            "pnl_by_token": [],
            "updated_at": datetime.utcnow().isoformat(),
        }

    def _empty_performance_metrics(self) -> Dict:
        return {
            "total_value_usd": 0,
            "weighted_apy": 0,
            "period_yield_usd": 0,
            "sharpe_ratio_approx": 0,
            "max_drawdown_approx": 0,
            "timeframe_days": 30,
            "updated_at": datetime.utcnow().isoformat(),
        }

    def _empty_risk_metrics(self) -> Dict:
        return {
            "diversification_score": 0,
            "concentration_risk": "low",
            "category_risk": "low",
            "max_allocation_percent": 0,
            "protocol_count": 0,
            "category_count": 0,
            "overall_risk_score": 0,
            "risk_level": "low",
            "updated_at": datetime.utcnow().isoformat(),
        }


# Global instance
_analytics: Optional[RealTimeAnalytics] = None


def get_analytics() -> RealTimeAnalytics:
    """Get or create analytics instance"""
    global _analytics
    if _analytics is None:
        _analytics = RealTimeAnalytics()
    return _analytics
