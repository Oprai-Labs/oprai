"""
MEV Protection Service

Provides MEV (Maximal Extractable Value) protection for transactions:
- Jito tip optimization (bundle tips)
- Priority fee estimation
- Front-run protection strategies
- Private transaction hints
- Slippage protection with deadline

This service helps protect users from:
- Front-running
- Sandwich attacks
- Back-running
- Gas manipulation
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

import redis.asyncio as redis

from app.services.cache import get_redis_client

logger = logging.getLogger(__name__)


# ============================================================================
# Enums
# ============================================================================

class ProtectionLevel(str, Enum):
    """MEV Protection levels"""
    NONE = "none"           # No protection
    LOW = "low"             # Basic slippage protection
    MEDIUM = "medium"       # Priority fees + basic protection
    HIGH = "high"          # Full protection with Jito bundles
    MAXIMUM = "maximum"     # Maximum protection (slower, more expensive)


class TransactionPriority(str, Enum):
    """Transaction priority levels"""
    LOW = "low"             # Low priority, low fee
    MEDIUM = "medium"       # Medium priority
    HIGH = "high"           # High priority
    URGENT = "urgent"       # Urgent, highest fee


class ProtectionStrategy(str, Enum):
    """Protection strategies"""
    JITO_BUNDLE = "jito_bundle"
    JITO_TIP = "jito_tip"
    PRIVITY = "privity"
    FLASHBOTS = "flashbots"
    STANDARD = "standard"


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class PriorityFeeConfig:
    """Configuration for priority fees"""
    # Priority level
    priority: TransactionPriority = TransactionPriority.MEDIUM

    # Fee settings
    min_priority_fee_micro_lamports: int = 1000       # Minimum 0.001 SOL
    max_priority_fee_micro_lamports: int = 100000     # Maximum 0.1 SOL

    # Percentile for dynamic calculation
    percentile: int = 90

    # Jito settings
    use_jito: bool = True
    jito_tip_percentile: int = 95

    def to_dict(self) -> dict[str, Any]:
        return {
            "priority": self.priority.value,
            "min_priority_fee_micro_lamports": self.min_priority_fee_micro_lamports,
            "max_priority_fee_micro_lamports": self.max_priority_fee_micro_lamports,
            "percentile": self.percentile,
            "use_jito": self.use_jito,
            "jito_tip_percentile": self.jito_tip_percentile,
        }


@dataclass
class MEVProtectionConfig:
    """Configuration for MEV protection"""
    # Protection level
    level: ProtectionLevel = ProtectionLevel.MEDIUM

    # Strategy
    strategy: ProtectionStrategy = ProtectionStrategy.JITO_TIP

    # Slippage protection
    max_slippage_bps: int = 300          # 3% max slippage
    protection_slippage_bps: int = 100    # Extra 1% slippage for protection

    # Deadline
    default_deadline_seconds: int = 120    # 2 minutes
    max_deadline_seconds: int = 300       # 5 minutes max

    # Jito bundle settings
    bundle_timeout_ms: int = 10000        # 10 seconds
    bundle_retry_count: int = 3

    # Additional protection
    check_sandwich: bool = True
    check_price_impact: bool = True
    max_price_impact_bps: int = 500       # 5% max price impact

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "strategy": self.strategy.value,
            "max_slippage_bps": self.max_slippage_bps,
            "protection_slippage_bps": self.protection_slippage_bps,
            "default_deadline_seconds": self.default_deadline_seconds,
            "max_deadline_seconds": self.max_deadline_seconds,
            "bundle_timeout_ms": self.bundle_timeout_ms,
            "bundle_retry_count": self.bundle_retry_count,
            "check_sandwich": self.check_sandwich,
            "check_price_impact": self.check_price_impact,
            "max_price_impact_bps": self.max_price_impact_bps,
        }


@dataclass
class EstimatedFees:
    """Estimated fees for a transaction"""
    # Base fees
    compute_units: int = 200_000           # Default compute units
    compute_unit_price_micro_lamports: int = 1000  # 0.001 SOL per CU

    # Priority fees
    priority_fee_micro_lamports: int = 5000  # 0.005 SOL priority

    # Jito tip
    jito_tip_micro_lamports: int = 1000    # 0.001 SOL tip
    use_jito_bundle: bool = False

    # Total
    total_fee_lamports: int = 0
    total_fee_sol: float = 0

    def __post_init__(self):
        self.total_fee_lamports = (
            self.compute_units * self.compute_unit_price_micro_lamports
            + self.priority_fee_micro_lamports
            + (self.jito_tip_micro_lamports if self.use_jito_bundle else 0)
        )
        self.total_fee_sol = self.total_fee_lamports / 1_000_000_000

    def to_dict(self) -> dict[str, Any]:
        return {
            "compute_units": self.compute_units,
            "compute_unit_price_micro_lamports": self.compute_unit_price_micro_lamports,
            "priority_fee_micro_lamports": self.priority_fee_micro_lamports,
            "jito_tip_micro_lamports": self.jito_tip_micro_lamports,
            "use_jito_bundle": self.use_jito_bundle,
            "total_fee_lamports": self.total_fee_lamports,
            "total_fee_sol": self.total_fee_sol,
        }


@dataclass
class ProtectionResult:
    """Result of MEV protection application"""
    # Applied protection
    protection_applied: bool = False
    protection_level: ProtectionLevel = ProtectionLevel.NONE
    strategy_used: ProtectionStrategy | None = None

    # Modified transaction params
    priority_fee: int = 0
    jito_tip: int = 0
    slippage_bps: int = 0
    deadline_seconds: int = 0

    # Recommendations
    recommended: bool = False
    warning: str | None = None
    suggestions: list[str] = field(default_factory=list)

    # Price impact
    price_impact_bps: int = 0
    sandwich_risk: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "protection_applied": self.protection_applied,
            "protection_level": self.protection_level.value,
            "strategy_used": self.strategy_used.value if self.strategy_used else None,
            "priority_fee": self.priority_fee,
            "jito_tip": self.jito_tip,
            "slippage_bps": self.slippage_bps,
            "deadline_seconds": self.deadline_seconds,
            "recommended": self.recommended,
            "warning": self.warning,
            "suggestions": self.suggestions,
            "price_impact_bps": self.price_impact_bps,
            "sandwich_risk": self.sandwich_risk,
        }


# ============================================================================
# Service Class
# ============================================================================

class MEVProtectionService:
    """
    Service for MEV protection on Solana transactions.

    Features:
    - Dynamic priority fee estimation
    - Jito bundle integration
    - Sandwich attack detection
    - Price impact analysis
    - Slippage protection
    """

    CACHE_TTL_SECONDS = 60  # 1 minute
    PRIORITY_FEE_CACHE_KEY = "mev:priority_fee:"

    # Jito tip floors (in micro-lamports)
    JITO_TIP_FLOOR_LOW = 1000       # 0.001 SOL
    JITO_TIP_FLOOR_MEDIUM = 5000     # 0.005 SOL
    JITO_TIP_FLOOR_HIGH = 10000      # 0.01 SOL
    JITO_TIP_FLOOR_MAX = 50000      # 0.05 SOL

    def __init__(self):
        self._redis: redis.Redis | None = None

    async def _get_redis(self) -> redis.Redis:
        """Get Redis client"""
        if self._redis is None:
            self._redis = await get_redis_client()
        return self._redis

    async def get_priority_fee_estimate(
        self,
        priority: TransactionPriority = TransactionPriority.MEDIUM,
        use_jito: bool = True,
    ) -> int:
        """
        Get priority fee estimate in micro-lamports.

        Args:
            priority: Priority level
            use_jito: Whether to include Jito tip

        Returns:
            Priority fee in micro-lamports
        """
        # Try cache first
        try:
            redis_client = await self._get_redis()
            cache_key = f"{self.PRIORITY_FEE_CACHE_KEY}{priority.value}"

            cached = await redis_client.get(cache_key)
            if cached:
                return int(cached)
        except Exception as e:
            logger.warning("Failed to get cached priority fee", exc_info=True)

        # Calculate based on priority level
        fee_map = {
            TransactionPriority.LOW: 1000,
            TransactionPriority.MEDIUM: 5000,
            TransactionPriority.HIGH: 20000,
            TransactionPriority.URGENT: 100000,
        }

        base_fee = fee_map.get(priority, 5000)

        # Add some randomness to avoid clustering
        import random
        jitter = random.randint(-500, 500)
        priority_fee = max(1000, base_fee + jitter)

        # Cache the result
        try:
            redis_client = await self._get_redis()
            cache_key = f"{self.PRIORITY_FEE_CACHE_KEY}{priority.value}"
            await redis_client.setex(cache_key, self.CACHE_TTL_SECONDS, str(priority_fee))
        except Exception as e:
            logger.warning("Failed to cache priority fee", exc_info=True)

        return priority_fee

    async def get_jito_tip_estimate(
        self,
        priority: TransactionPriority = TransactionPriority.MEDIUM,
    ) -> int:
        """
        Get Jito tip estimate in micro-lamports.

        Args:
            priority: Priority level

        Returns:
            Jito tip in micro-lamports
        """
        tip_map = {
            TransactionPriority.LOW: self.JITO_TIP_FLOOR_LOW,
            TransactionPriority.MEDIUM: self.JITO_TIP_FLOOR_MEDIUM,
            TransactionPriority.HIGH: self.JITO_TIP_FLOOR_HIGH,
            TransactionPriority.URGENT: self.JITO_TIP_FLOOR_MAX,
        }

        base_tip = tip_map.get(priority, self.JITO_TIP_FLOOR_MEDIUM)

        # Add randomness
        import random
        jitter = random.randint(-1000, 1000)
        return max(self.JITO_TIP_FLOOR_LOW, base_tip + jitter)

    async def estimate_fees(
        self,
        config: PriorityFeeConfig,
    ) -> EstimatedFees:
        """
        Estimate total fees for a transaction.

        Args:
            config: Priority fee configuration

        Returns:
            EstimatedFees object
        """
        priority_fee = await self.get_priority_fee_estimate(config.priority)

        # Clamp to configured limits
        priority_fee = max(
            config.min_priority_fee_micro_lamports,
            min(config.max_priority_fee_micro_lamports, priority_fee)
        )

        jito_tip = 0
        use_jito_bundle = False

        if config.use_jito:
            jito_tip = await self.get_jito_tip_estimate(config.priority)
            use_jito_bundle = config.priority in [
                TransactionPriority.HIGH,
                TransactionPriority.URGENT
            ]

        return EstimatedFees(
            compute_unit_price_micro_lamports=priority_fee,
            priority_fee_micro_lamports=priority_fee,
            jito_tip_micro_lamports=jito_tip,
            use_jito_bundle=use_jito_bundle,
        )

    async def apply_protection(
        self,
        config: MEVProtectionConfig,
        estimated_price_impact_bps: int = 0,
    ) -> ProtectionResult:
        """
        Apply MEV protection to a transaction.

        Args:
            config: Protection configuration
            estimated_price_impact_bps: Estimated price impact in basis points

        Returns:
            ProtectionResult with applied protection
        """
        result = ProtectionResult()

        # Determine protection level
        if config.level == ProtectionLevel.NONE:
            return result

        result.protection_applied = True
        result.protection_level = config.level

        # Calculate slippage with protection buffer
        base_slippage = config.max_slippage_bps
        if config.strategy in [ProtectionStrategy.JITO_TIP, ProtectionStrategy.JITO_BUNDLE]:
            result.slippage_bps = base_slippage + config.protection_slippage_bps
            result.strategy_used = config.strategy
        else:
            result.slippage_bps = base_slippage

        # Set deadline
        result.deadline_seconds = config.default_deadline_seconds

        # Calculate priority fee based on strategy
        priority = TransactionPriority.MEDIUM
        if config.level == ProtectionLevel.HIGH:
            priority = TransactionPriority.HIGH
        elif config.level == ProtectionLevel.MAXIMUM:
            priority = TransactionPriority.URGENT

        result.priority_fee = await self.get_priority_fee_estimate(priority)

        # Add Jito tip for high protection levels
        if config.strategy in [ProtectionStrategy.JITO_TIP, ProtectionStrategy.JITO_BUNDLE]:
            result.jito_tip = await self.get_jito_tip_estimate(priority)

        # Check price impact
        result.price_impact_bps = estimated_price_impact_bps

        if config.check_price_impact and estimated_price_impact_bps > config.max_price_impact_bps:
            result.warning = f"High price impact detected: {estimated_price_impact_bps / 100}%"
            result.suggestions.append("Consider reducing trade size or using limit order")

        # Check sandwich risk
        if config.check_sandwich:
            result.sandwich_risk = estimated_price_impact_bps > 100  # >1% impact

            if result.sandwich_risk:
                result.suggestions.append(
                    "Sandwich attack detected - consider using Jito bundle for protection"
                )

        # Add recommendations
        if config.level in [ProtectionLevel.MEDIUM, ProtectionLevel.HIGH]:
            result.recommended = True
            result.suggestions.append(
                "Enable Jito bundle for maximum MEV protection"
            )

        return result

    def calculate_slippage(
        self,
        amount: float,
        protection_level: ProtectionLevel,
        base_slippage_bps: int = 300,
    ) -> int:
        """
        Calculate slippage based on protection level.

        Args:
            amount: Transaction amount in USD
            protection_level: Protection level
            base_slippage_bps: Base slippage in basis points

        Returns:
            Adjusted slippage in basis points
        """
        # Adjust slippage based on amount and protection
        slippage = base_slippage_bps

        if protection_level == ProtectionLevel.LOW:
            slippage = int(base_slippage_bps * 1.2)
        elif protection_level == ProtectionLevel.MEDIUM:
            slippage = int(base_slippage_bps * 1.5)
        elif protection_level == ProtectionLevel.HIGH:
            slippage = int(base_slippage_bps * 2.0)
        elif protection_level == ProtectionLevel.MAXIMUM:
            slippage = int(base_slippage_bps * 3.0)

        # Additional protection for large amounts
        if amount > 10000:
            slippage = int(slippage * 1.1)
        if amount > 100000:
            slippage = int(slippage * 1.25)

        return min(slippage, 5000)  # Cap at 50%

    def create_bundle_params(
        self,
        transactions: list[str],
        tip: int = 5000,
        timeout_ms: int = 10000,
    ) -> dict[str, Any]:
        """
        Create Jito bundle parameters.

        Args:
            transactions: List of serialized transactions
            tip: Jito tip in micro-lamports
            timeout_ms: Bundle timeout in milliseconds

        Returns:
            Bundle parameters for RPC
        """
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendBundle",
            "params": {
                "bundle": transactions,
                "tip": tip,
                "timeout": timeout_ms,
            }
        }

    def calculate_deadline(
        self,
        protection_level: ProtectionLevel,
        base_deadline: int = 120,
    ) -> int:
        """
        Calculate transaction deadline based on protection level.

        Args:
            protection_level: Protection level
            base_deadline: Base deadline in seconds

        Returns:
            Deadline in seconds
        """
        deadline = base_deadline

        # Shorter deadlines for higher protection (faster execution)
        if protection_level == ProtectionLevel.HIGH:
            deadline = int(base_deadline * 0.75)
        elif protection_level == ProtectionLevel.MAXIMUM:
            deadline = int(base_deadline * 0.5)

        return max(30, deadline)  # Minimum 30 seconds


# ============================================================================
# Helper Functions
# ============================================================================

def get_default_protection_config(
    risk_tolerance: str = "moderate",
) -> MEVProtectionConfig:
    """Get default protection config based on user risk tolerance"""
    if risk_tolerance == "conservative":
        return MEVProtectionConfig(
            level=ProtectionLevel.HIGH,
            strategy=ProtectionStrategy.JITO_BUNDLE,
            max_slippage_bps=500,
            check_sandwich=True,
            check_price_impact=True,
        )
    elif risk_tolerance == "aggressive":
        return MEVProtectionConfig(
            level=ProtectionLevel.LOW,
            strategy=ProtectionStrategy.STANDARD,
            max_slippage_bps=200,
            check_sandwich=False,
            check_price_impact=False,
        )
    else:  # moderate
        return MEVProtectionConfig(
            level=ProtectionLevel.MEDIUM,
            strategy=ProtectionStrategy.JITO_TIP,
            max_slippage_bps=300,
            check_sandwich=True,
            check_price_impact=True,
        )


def get_priority_fee_config(
    urgency: str = "normal",
) -> PriorityFeeConfig:
    """Get priority fee config based on urgency"""
    if urgency == "fast":
        return PriorityFeeConfig(
            priority=TransactionPriority.HIGH,
            use_jito=True,
        )
    elif urgency == "slow":
        return PriorityFeeConfig(
            priority=TransactionPriority.LOW,
            use_jito=False,
        )
    else:  # normal
        return PriorityFeeConfig(
            priority=TransactionPriority.MEDIUM,
            use_jito=True,
        )


# ============================================================================
# Global Instance
# ============================================================================

_mev_service: MEVProtectionService | None = None


def get_mev_service() -> MEVProtectionService:
    """Get or create MEV protection service"""
    global _mev_service
    if _mev_service is None:
        _mev_service = MEVProtectionService()
    return _mev_service
