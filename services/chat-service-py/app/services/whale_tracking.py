"""
Whale Tracking Service

API for whale wallet tracking and smart money monitoring:
- Track whale wallets (exchanges, funds, market makers)
- Monitor smart money wallets
- Volume anomaly detection
- Custom alert rules
- Multi-channel notifications

This wraps the opraios/core/advanced_alerts.py functionality for the backend API.
"""

import json
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

class WhaleType(str, Enum):
    """Types of whale wallets"""
    EXCHANGE = "exchange"
    FUND = "fund"
    MARKET_MAKER = "market_maker"
    DEFI_PROTOCOL = "defi_protocol"
    LARGE_HOLDER = "large_holder"
    UNKNOWN = "unknown"


class AlertPriority(str, Enum):
    """Alert priority levels"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class AlertStatus(str, Enum):
    """Alert status"""
    PENDING = "pending"
    SENT = "sent"
    READ = "read"
    DISMISSED = "dismissed"


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class WhaleInfo:
    """Information about a tracked whale"""
    address: str
    name: str
    whale_type: WhaleType
    description: str | None = None
    tags: list[str] = field(default_factory=list)
    is_active: bool = True
    first_seen: datetime = field(default_factory=datetime.utcnow)
    last_activity: datetime | None = None
    total_volume_usd: float = 0
    transaction_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "name": self.name,
            "whale_type": self.whale_type.value,
            "description": self.description,
            "tags": self.tags,
            "is_active": self.is_active,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_activity": self.last_activity.isoformat() if self.last_activity else None,
            "total_volume_usd": self.total_volume_usd,
            "transaction_count": self.transaction_count,
        }


@dataclass
class SmartMoneyInfo:
    """Information about a smart money wallet"""
    address: str
    name: str
    win_rate: float = 0
    total_pnl_usd: float = 0
    trading_style: str = "unknown"  # swing, scalp, trend, value
    preferred_tokens: list[str] = field(default_factory=list)
    preferred_protocols: list[str] = field(default_factory=list)
    avg_hold_time_hours: float = 0
    is_active: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "name": self.name,
            "win_rate": self.win_rate,
            "total_pnl_usd": self.total_pnl_usd,
            "trading_style": self.trading_style,
            "preferred_tokens": self.preferred_tokens,
            "preferred_protocols": self.preferred_protocols,
            "avg_hold_time_hours": self.avg_hold_time_hours,
            "is_active": self.is_active,
        }


@dataclass
class AlertRule:
    """Custom alert rule"""
    id: str
    user_id: str
    name: str
    condition: dict[str, Any]
    priority: AlertPriority = AlertPriority.MEDIUM
    channels: list[str] = field(default_factory=lambda: ["in_app"])
    is_active: bool = True
    cooldown_minutes: int = 60
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "condition": self.condition,
            "priority": self.priority.value,
            "channels": self.channels,
            "is_active": self.is_active,
            "cooldown_minutes": self.cooldown_minutes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class WhaleAlert:
    """A whale activity alert"""
    id: str
    alert_type: str
    whale_address: str
    whale_name: str
    action: str  # buy, sell, transfer, etc.
    token_address: str
    token_symbol: str
    amount: float
    value_usd: float
    priority: AlertPriority = AlertPriority.MEDIUM
    status: AlertStatus = AlertStatus.PENDING
    message: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "alert_type": self.alert_type,
            "whale_address": self.whale_address,
            "whale_name": self.whale_name,
            "action": self.action,
            "token_address": self.token_address,
            "token_symbol": self.token_symbol,
            "amount": self.amount,
            "value_usd": self.value_usd,
            "priority": self.priority.value,
            "status": self.status.value,
            "message": self.message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class VolumeAnomaly:
    """Volume anomaly detection result"""
    token_address: str
    token_symbol: str
    current_volume_usd: float
    avg_volume_usd: float
    anomaly_ratio: float  # e.g., 3.0 = 3x average
    direction: str  # up or down
    detected_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_address": self.token_address,
            "token_symbol": self.token_symbol,
            "current_volume_usd": self.current_volume_usd,
            "avg_volume_usd": self.avg_volume_usd,
            "anomaly_ratio": self.anomaly_ratio,
            "direction": self.direction,
            "detected_at": self.detected_at.isoformat() if self.detected_at else None,
        }


# ============================================================================
# Service Class
# ============================================================================

class WhaleTrackingService:
    """
    Service for whale tracking and smart money monitoring.

    Features:
    - Track whale wallets
    - Monitor smart money
    - Volume anomaly detection
    - Custom alert rules
    - Multi-channel notifications
    """

    CACHE_TTL_SECONDS = 300  # 5 minutes
    WHALES_CACHE_KEY = "whale:tracked:"
    ALERTS_CACHE_KEY = "whale:alerts:"

    # Known whale addresses (placeholder - would come from database)
    KNOWN_WHALES = {
        # Exchanges
        "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU": {
            "name": "Binance Hot Wallet",
            "type": WhaleType.EXCHANGE,
            "tags": ["exchange", "withdrawal"],
        },
        "GUohe4DJUA5FKPWo3PNiKoiB6xMrE3sse2BNFy4V7m8": {
            "name": "Coinbase Hot Wallet",
            "type": WhaleType.EXCHANGE,
            "tags": ["exchange", "withdrawal"],
        },
        "2b2JB7xLpL4eGG4dGVkEYP7wT7TLg3g3g3g3g3g3g3g3": {
            "name": "Kraken",
            "type": WhaleType.EXCHANGE,
            "tags": ["exchange"],
        },
        # Funds
        "51CnS8xDLiM5L9mRxEWsS5n8QJ5xKF7vJFgMF8NQFL1": {
            "name": "Paradigm",
            "type": WhaleType.FUND,
            "tags": ["vc", "fund", "early_stage"],
        },
        "Grqw8w3uBBNB5tCKCWzJ6M3M1xF3kX3kX3kX3kX3kX3": {
            "name": "a16z",
            "type": WhaleType.FUND,
            "tags": ["vc", "fund"],
        },
        # Market Makers
        "MarketMaker123456789ABCDEFGHIJKLMNOPQRSTU": {
            "name": "Jump Trading",
            "type": WhaleType.MARKET_MAKER,
            "tags": ["mm", "defi"],
        },
        "JaneStreet123456789ABCDEFGHIJKLMNOPQRSTU": {
            "name": "Jane Street",
            "type": WhaleType.MARKET_MAKER,
            "tags": ["mm"],
        },
    }

    def __init__(self):
        self._redis: redis.Redis | None = None

    async def _get_redis(self) -> redis.Redis:
        """Get Redis client"""
        if self._redis is None:
            self._redis = await get_redis_client()
        return self._redis

    async def get_whales(
        self,
        wallet: str,
        whale_type: WhaleType | None = None,
        active_only: bool = True,
    ) -> list[WhaleInfo]:
        """
        Get the caller's whale watchlist: the curated KNOWN_WHALES baseline that
        every user sees, plus THIS wallet's own tracked whales.

        Args:
            wallet: the authenticated owner — watchlists are per-user (Redis
                key ``whale:tracked:<wallet>``), so no user sees or affects
                another user's additions.
            whale_type: Filter by whale type
            active_only: Only return active whales
        """
        whales = []

        # curated baseline (read-only, shared by all users)
        for address, info in self.KNOWN_WHALES.items():
            wtype = WhaleType(info["type"])
            if whale_type and wtype != whale_type:
                continue
            whales.append(WhaleInfo(
                address=address,
                name=info["name"],
                whale_type=wtype,
                tags=info.get("tags", []),
            ))

        # this user's own tracked whales
        try:
            r = await self._get_redis()
            raw = await r.hgetall(self.WHALES_CACHE_KEY + wallet)
            for _addr, blob in (raw or {}).items():
                if isinstance(blob, bytes):
                    blob = blob.decode()
                d = json.loads(blob)
                wt = WhaleType(d.get("whale_type", WhaleType.UNKNOWN.value))
                if whale_type and wt != whale_type:
                    continue
                if active_only and not d.get("is_active", True):
                    continue
                whales.append(WhaleInfo(
                    address=d["address"],
                    name=d.get("name", ""),
                    whale_type=wt,
                    description=d.get("description"),
                    tags=d.get("tags", []),
                    is_active=d.get("is_active", True),
                ))
        except Exception:
            logger.warning("whale watchlist read failed", exc_info=True)

        return whales

    async def add_whale(
        self,
        wallet: str,
        address: str,
        name: str,
        whale_type: WhaleType = WhaleType.UNKNOWN,
        tags: list[str] | None = None,
    ) -> WhaleInfo:
        """
        Add a whale to THIS wallet's own watchlist (persisted per-user in Redis).
        """
        whale = WhaleInfo(
            address=address,
            name=name,
            whale_type=whale_type,
            tags=tags or [],
        )
        try:
            r = await self._get_redis()
            await r.hset(self.WHALES_CACHE_KEY + wallet, address, json.dumps(whale.to_dict()))
            logger.info("Added whale to watchlist", extra={"wallet": wallet, "address": address})
        except Exception:
            logger.error("whale watchlist add failed", exc_info=True)
        return whale

    async def remove_whale(self, wallet: str, address: str) -> bool:
        """
        Remove a whale from THIS wallet's own watchlist. The curated KNOWN_WHALES
        baseline is shared and cannot be removed by a user.
        """
        try:
            r = await self._get_redis()
            removed = await r.hdel(self.WHALES_CACHE_KEY + wallet, address)
            return bool(removed)
        except Exception:
            logger.error("whale watchlist remove failed", exc_info=True)
            return False

    async def get_whale_activity(
        self,
        address: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Get recent activity for a whale address.

        Args:
            address: Whale address
            limit: Number of transactions to return

        Returns:
            List of transaction data
        """
        # This would integrate with blockchain to get recent transactions
        # For now, return empty list as placeholder
        return []

    async def get_smart_money(
        self,
        style: str | None = None,
    ) -> list[SmartMoneyInfo]:
        """
        Get list of smart money wallets.

        Args:
            style: Filter by trading style

        Returns:
            List of SmartMoneyInfo
        """
        # This would come from database in production
        return []

    async def add_smart_money(
        self,
        address: str,
        name: str,
        trading_style: str = "unknown",
    ) -> SmartMoneyInfo:
        """
        Add a smart money wallet to track.

        Args:
            address: Wallet address
            name: Display name
            trading_style: Trading style

        Returns:
            Created SmartMoneyInfo
        """
        smart_money = SmartMoneyInfo(
            address=address,
            name=name,
            trading_style=trading_style,
        )

        logger.info(f"Added smart money: {name} ({address})")

        return smart_money

    async def get_volume_anomalies(
        self,
        min_ratio: float = 3.0,
        limit: int = 10,
    ) -> list[VolumeAnomaly]:
        """
        Get tokens with volume anomalies.

        Args:
            min_ratio: Minimum anomaly ratio (e.g., 3.0 = 3x average)
            limit: Number of results

        Returns:
            List of VolumeAnomaly objects
        """
        # This would analyze real-time volume data
        # Placeholder return
        return []

    async def get_alerts(
        self,
        wallet_address: str,
        limit: int = 20,
        unread_only: bool = False,
    ) -> list[WhaleAlert]:
        """
        Get whale alerts for a user.

        Args:
            wallet_address: User wallet address
            limit: Number of alerts
            unread_only: Only return unread alerts

        Returns:
            List of WhaleAlert objects
        """
        # This would come from database/Redis
        return []

    async def create_alert_rule(
        self,
        user_id: str,
        name: str,
        condition: dict[str, Any],
        priority: AlertPriority = AlertPriority.MEDIUM,
        channels: list[str] | None = None,
    ) -> AlertRule:
        """
        Create a custom alert rule.

        Args:
            user_id: User ID
            name: Rule name
            condition: Alert condition
            priority: Alert priority
            channels: Notification channels

        Returns:
            Created AlertRule
        """
        import uuid

        rule = AlertRule(
            id=str(uuid.uuid4()),
            user_id=user_id,
            name=name,
            condition=condition,
            priority=priority,
            channels=channels or ["in_app"],
        )

        logger.info(f"Created alert rule: {name} for user {user_id}")

        return rule

    async def get_alert_rules(
        self,
        user_id: str,
    ) -> list[AlertRule]:
        """
        Get alert rules for a user.

        Args:
            user_id: User ID

        Returns:
            List of AlertRule objects
        """
        return []

    async def delete_alert_rule(
        self,
        rule_id: str,
        user_id: str,
    ) -> bool:
        """
        Delete an alert rule.

        Args:
            rule_id: Rule ID
            user_id: User ID

        Returns:
            True if successful
        """
        logger.info(f"Deleted alert rule: {rule_id}")
        return True

    async def get_statistics(self) -> dict[str, Any]:
        """
        Get whale tracking statistics.

        Returns:
            Statistics dictionary
        """
        return {
            "total_whales": len(self.KNOWN_WHALES),
            "by_type": {
                "exchange": sum(1 for w in self.KNOWN_WHALES.values() if w["type"] == "exchange"),
                "fund": sum(1 for w in self.KNOWN_WHALES.values() if w["type"] == "fund"),
                "market_maker": sum(1 for w in self.KNOWN_WHALES.values() if w["type"] == "market_maker"),
            },
            "smart_money_count": 0,
            "active_alerts": 0,
        }


# ============================================================================
# Global Instance
# ============================================================================

_whale_service: WhaleTrackingService | None = None


def get_whale_service() -> WhaleTrackingService:
    """Get or create whale tracking service"""
    global _whale_service
    if _whale_service is None:
        _whale_service = WhaleTrackingService()
    return _whale_service
