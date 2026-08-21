"""
User Preferences Service

Manages user preferences including:
- Notification settings (email, push, telegram, in-app)
- Theme settings (dark, light, system)
- Language preferences (en, tr, etc.)
- Risk tolerance (conservative, moderate, aggressive)
- Preferred protocols
- Quiet hours
- Privacy settings

Uses Redis for fast access and PostgreSQL for persistence.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum
from typing import Any, Dict, List, Optional

import redis.asyncio as redis
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.cache import get_redis_client

logger = logging.getLogger(__name__)


# ============================================================================
# Enums
# ============================================================================

class Theme(str, Enum):
    """Theme options"""
    DARK = "dark"
    LIGHT = "light"
    SYSTEM = "system"


class Language(str, Enum):
    """Supported languages"""
    EN = "en"
    TR = "tr"
    ES = "es"
    DE = "de"
    FR = "fr"
    JP = "ja"
    CN = "zh"


class RiskTolerance(str, Enum):
    """User risk tolerance levels"""
    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"


class NotificationChannel(str, Enum):
    """Notification channels"""
    EMAIL = "email"
    PUSH = "push"
    TELEGRAM = "telegram"
    IN_APP = "in_app"
    WEBHOOK = "webhook"


class NotificationType(str, Enum):
    """Types of notifications"""
    PRICE_ALERT = "price_alert"
    POSITION_ALERT = "position_alert"
    TRANSACTION = "transaction"
    YIELD = "yield"
    NEWS = "news"
    SYSTEM = "system"
    WHALE = "whale"


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class NotificationSettings:
    """Notification preferences for a user"""
    # Channel preferences
    email_enabled: bool = False
    push_enabled: bool = True
    telegram_enabled: bool = False
    webhook_enabled: bool = False
    in_app_enabled: bool = True

    # Type preferences
    price_alerts: bool = True
    position_alerts: bool = True
    transaction_notifications: bool = True
    yield_notifications: bool = True
    news_alerts: bool = False
    whale_alerts: bool = True
    system_notifications: bool = True

    # Quiet hours
    quiet_hours_enabled: bool = False
    quiet_hours_start: str = "22:00"  # HH:MM format
    quiet_hours_end: str = "08:00"   # HH:MM format
    quiet_hours_timezone: str = "UTC"

    # Rate limiting
    max_daily_notifications: int = 50
    max_per_type_per_day: int = 10

    # Telegram/Webhook specifics
    telegram_chat_id: str | None = None
    webhook_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "email_enabled": self.email_enabled,
            "push_enabled": self.push_enabled,
            "telegram_enabled": self.telegram_enabled,
            "webhook_enabled": self.webhook_enabled,
            "in_app_enabled": self.in_app_enabled,
            "price_alerts": self.price_alerts,
            "position_alerts": self.position_alerts,
            "transaction_notifications": self.transaction_notifications,
            "yield_notifications": self.yield_notifications,
            "news_alerts": self.news_alerts,
            "whale_alerts": self.whale_alerts,
            "system_notifications": self.system_notifications,
            "quiet_hours_enabled": self.quiet_hours_enabled,
            "quiet_hours_start": self.quiet_hours_start,
            "quiet_hours_end": self.quiet_hours_end,
            "quiet_hours_timezone": self.quiet_hours_timezone,
            "max_daily_notifications": self.max_daily_notifications,
            "max_per_type_per_day": self.max_per_type_per_day,
            "telegram_chat_id": self.telegram_chat_id,
            "webhook_url": self.webhook_url,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NotificationSettings":
        return cls(
            email_enabled=data.get("email_enabled", False),
            push_enabled=data.get("push_enabled", True),
            telegram_enabled=data.get("telegram_enabled", False),
            webhook_enabled=data.get("webhook_enabled", False),
            in_app_enabled=data.get("in_app_enabled", True),
            price_alerts=data.get("price_alerts", True),
            position_alerts=data.get("position_alerts", True),
            transaction_notifications=data.get("transaction_notifications", True),
            yield_notifications=data.get("yield_notifications", True),
            news_alerts=data.get("news_alerts", False),
            whale_alerts=data.get("whale_alerts", True),
            system_notifications=data.get("system_notifications", True),
            quiet_hours_enabled=data.get("quiet_hours_enabled", False),
            quiet_hours_start=data.get("quiet_hours_start", "22:00"),
            quiet_hours_end=data.get("quiet_hours_end", "08:00"),
            quiet_hours_timezone=data.get("quiet_hours_timezone", "UTC"),
            max_daily_notifications=data.get("max_daily_notifications", 50),
            max_per_type_per_day=data.get("max_per_type_per_day", 10),
            telegram_chat_id=data.get("telegram_chat_id"),
            webhook_url=data.get("webhook_url"),
        )


@dataclass
class PrivacySettings:
    """Privacy preferences"""
    # Data sharing
    share_portfolio_data: bool = False
    share_trading_history: bool = False
    anonymous_analytics: bool = True

    # Visibility
    show_wallet_address: bool = False
    show_portfolio_value: bool = True
    show_yield_earned: bool = True

    # Marketing
    receive_marketing: bool = False
    participate_beta: bool = True

    # Data retention (days)
    history_retention_days: int = 90
    analytics_retention_days: int = 365

    def to_dict(self) -> dict[str, Any]:
        return {
            "share_portfolio_data": self.share_portfolio_data,
            "share_trading_history": self.share_trading_history,
            "anonymous_analytics": self.anonymous_analytics,
            "show_wallet_address": self.show_wallet_address,
            "show_portfolio_value": self.show_portfolio_value,
            "show_yield_earned": self.show_yield_earned,
            "receive_marketing": self.receive_marketing,
            "participate_beta": self.participate_beta,
            "history_retention_days": self.history_retention_days,
            "analytics_retention_days": self.analytics_retention_days,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PrivacySettings":
        return cls(
            share_portfolio_data=data.get("share_portfolio_data", False),
            share_trading_history=data.get("share_trading_history", False),
            anonymous_analytics=data.get("anonymous_analytics", True),
            show_wallet_address=data.get("show_wallet_address", False),
            show_portfolio_value=data.get("show_portfolio_value", True),
            show_yield_earned=data.get("show_yield_earned", True),
            receive_marketing=data.get("receive_marketing", False),
            participate_beta=data.get("participate_beta", True),
            history_retention_days=data.get("history_retention_days", 90),
            analytics_retention_days=data.get("analytics_retention_days", 365),
        )


@dataclass
class TradingPreferences:
    """Trading and DeFi preferences"""
    # Risk settings
    risk_tolerance: str = "moderate"  # conservative, moderate, aggressive
    max_slippage: int = 5  # percentage
    max_transaction_value_usd: float = 10000
    require_confirmation: bool = True

    # Auto-execution
    auto_stake_rewards: bool = False
    auto_rebalance: bool = False
    auto_compound_yield: bool = False

    # Preferred protocols (by category)
    preferred_dex: list[str] = field(default_factory=lambda: ["jupiter"])
    preferred_lending: list[str] = field(default_factory=lambda: ["kamino", "jupiter"])
    preferred_staking: list[str] = field(default_factory=lambda: ["jito", "marinade"])

    # Safety
    enable_simulation: bool = True
    check_token_security: bool = True
    mev_protection: bool = True

    # Limits
    daily_limit_usd: float = 50000
    weekly_limit_usd: float = 100000
    monthly_limit_usd: float = 500000

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_tolerance": self.risk_tolerance,
            "max_slippage": self.max_slippage,
            "max_transaction_value_usd": self.max_transaction_value_usd,
            "require_confirmation": self.require_confirmation,
            "auto_stake_rewards": self.auto_stake_rewards,
            "auto_rebalance": self.auto_rebalance,
            "auto_compound_yield": self.auto_compound_yield,
            "preferred_dex": self.preferred_dex,
            "preferred_lending": self.preferred_lending,
            "preferred_staking": self.preferred_staking,
            "enable_simulation": self.enable_simulation,
            "check_token_security": self.check_token_security,
            "mev_protection": self.mev_protection,
            "daily_limit_usd": self.daily_limit_usd,
            "weekly_limit_usd": self.weekly_limit_usd,
            "monthly_limit_usd": self.monthly_limit_usd,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TradingPreferences":
        return cls(
            risk_tolerance=data.get("risk_tolerance", "moderate"),
            max_slippage=data.get("max_slippage", 5),
            max_transaction_value_usd=data.get("max_transaction_value_usd", 10000),
            require_confirmation=data.get("require_confirmation", True),
            auto_stake_rewards=data.get("auto_stake_rewards", False),
            auto_rebalance=data.get("auto_rebalance", False),
            auto_compound_yield=data.get("auto_compound_yield", False),
            preferred_dex=data.get("preferred_dex", ["jupiter"]),
            preferred_lending=data.get("preferred_lending", ["kamino", "jupiter"]),
            preferred_staking=data.get("preferred_staking", ["jito", "marinade"]),
            enable_simulation=data.get("enable_simulation", True),
            check_token_security=data.get("check_token_security", True),
            mev_protection=data.get("mev_protection", True),
            daily_limit_usd=data.get("daily_limit_usd", 50000),
            weekly_limit_usd=data.get("weekly_limit_usd", 100000),
            monthly_limit_usd=data.get("monthly_limit_usd", 500000),
        )


@dataclass
class UserPreferences:
    """Complete user preferences"""
    user_id: str
    wallet_address: str

    # Basic settings
    theme: str = "dark"
    language: str = "en"
    display_name: str | None = None
    timezone: str = "UTC"

    # Nested preferences
    notifications: NotificationSettings = field(default_factory=NotificationSettings)
    privacy: PrivacySettings = field(default_factory=PrivacySettings)
    trading: TradingPreferences = field(default_factory=TradingPreferences)

    # Metadata
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "wallet_address": self.wallet_address,
            "theme": self.theme,
            "language": self.language,
            "display_name": self.display_name,
            "timezone": self.timezone,
            "notifications": self.notifications.to_dict(),
            "privacy": self.privacy.to_dict(),
            "trading": self.trading.to_dict(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserPreferences":
        notifications = NotificationSettings.from_dict(data.get("notifications", {}))
        privacy = PrivacySettings.from_dict(data.get("privacy", {}))
        trading = TradingPreferences.from_dict(data.get("trading", {}))

        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))

        updated_at = data.get("updated_at")
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))

        return cls(
            user_id=data.get("user_id", ""),
            wallet_address=data.get("wallet_address", ""),
            theme=data.get("theme", "dark"),
            language=data.get("language", "en"),
            display_name=data.get("display_name"),
            timezone=data.get("timezone", "UTC"),
            notifications=notifications,
            privacy=privacy,
            trading=trading,
            created_at=created_at,
            updated_at=updated_at,
        )


# ============================================================================
# Database Table Model
# ============================================================================

# Note: We'll use direct SQL queries since this service uses raw SQLAlchemy
# The table is defined as:
"""
CREATE TABLE IF NOT EXISTS auth_schema.user_preferences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth_schema.users(id),
    wallet_address VARCHAR(255) NOT NULL,

    -- Basic settings
    theme VARCHAR(20) DEFAULT 'dark',
    language VARCHAR(10) DEFAULT 'en',
    display_name VARCHAR(255),
    timezone VARCHAR(50) DEFAULT 'UTC',

    -- JSONB preferences
    notifications JSONB DEFAULT '{}',
    privacy JSONB DEFAULT '{}',
    trading JSONB DEFAULT '{}',

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE(user_id)
);

CREATE INDEX IF NOT EXISTS idx_user_preferences_wallet
    ON auth_schema.user_preferences (wallet_address);
"""


# ============================================================================
# Service Class
# ============================================================================

class UserPreferencesService:
    """
    Service for managing user preferences.
    Uses Redis for caching and PostgreSQL for persistence.
    """

    CACHE_TTL_SECONDS = 3600  # 1 hour
    CACHE_KEY_PREFIX = "user:preferences:"

    def __init__(self, db_session: AsyncSession | None = None):
        self.db_session = db_session
        self._redis: redis.Redis | None = None

    async def _get_redis(self) -> redis.Redis:
        """Get Redis client"""
        if self._redis is None:
            self._redis = await get_redis_client()
        return self._redis

    def _cache_key(self, wallet_address: str) -> str:
        """Generate cache key for wallet"""
        return f"{self.CACHE_KEY_PREFIX}{wallet_address}"

    async def get_preferences(
        self,
        wallet_address: str,
        use_cache: bool = True,
    ) -> UserPreferences | None:
        """
        Get user preferences.

        Args:
            wallet_address: User's wallet address
            use_cache: Whether to use Redis cache

        Returns:
            UserPreferences if found, None otherwise
        """
        # Try cache first
        if use_cache:
            redis_client = await self._get_redis()
            cached = await redis_client.get(self._cache_key(wallet_address))
            if cached:
                try:
                    import json
                    data = json.loads(cached)
                    return UserPreferences.from_dict(data)
                except Exception as e:
                    logger.warning("Failed to parse cached preferences", exc_info=True)

        # Load from database
        if self.db_session:
            try:
                result = await self.db_session.execute(
                    select(
                        "user_id",
                        "wallet_address",
                        "theme",
                        "language",
                        "display_name",
                        "timezone",
                        "notifications",
                        "privacy",
                        "trading",
                        "created_at",
                        "updated_at",
                    ).select_from("auth_schema.user_preferences").where(
                        "wallet_address = :wallet"
                    ),
                    {"wallet": wallet_address}
                )
                row = result.fetchone()

                if row:
                    data = dict(row._mapping)
                    preferences = UserPreferences.from_dict(data)

                    # Cache for next time
                    if use_cache:
                        await self._cache_preferences(wallet_address, preferences)

                    return preferences
            except Exception as e:
                logger.error("Failed to get preferences from DB", exc_info=True)

        return None

    async def _cache_preferences(
        self,
        wallet_address: str,
        preferences: UserPreferences,
    ) -> None:
        """Cache preferences in Redis"""
        try:
            redis_client = await self._get_redis()
            import json
            await redis_client.setex(
                self._cache_key(wallet_address),
                self.CACHE_TTL_SECONDS,
                json.dumps(preferences.to_dict()),
            )
        except Exception as e:
            logger.warning("Failed to cache preferences", exc_info=True)

    async def save_preferences(
        self,
        preferences: UserPreferences,
        invalidate_cache: bool = True,
    ) -> bool:
        """
        Save user preferences.

        Args:
            preferences: UserPreferences to save
            invalidate_cache: Whether to invalidate cache

        Returns:
            True if successful
        """
        if invalidate_cache:
            await self._invalidate_cache(preferences.wallet_address)

        if self.db_session:
            try:
                import json

                # Use PostgreSQL upsert
                stmt = insert("auth_schema.user_preferences").values(
                    user_id=preferences.user_id,
                    wallet_address=preferences.wallet_address,
                    theme=preferences.theme,
                    language=preferences.language,
                    display_name=preferences.display_name,
                    timezone=preferences.timezone,
                    notifications=json.dumps(preferences.notifications.to_dict()),
                    privacy=json.dumps(preferences.privacy.to_dict()),
                    trading=json.dumps(preferences.trading.to_dict()),
                    updated_at=datetime.utcnow(),
                ).on_conflict_do_update(
                    index_elements=["user_id"],
                    set_={
                        "theme": preferences.theme,
                        "language": preferences.language,
                        "display_name": preferences.display_name,
                        "timezone": preferences.timezone,
                        "notifications": json.dumps(preferences.notifications.to_dict()),
                        "privacy": json.dumps(preferences.privacy.to_dict()),
                        "trading": json.dumps(preferences.trading.to_dict()),
                        "updated_at": datetime.utcnow(),
                    }
                )

                await self.db_session.execute(stmt)
                await self.db_session.commit()

                # Re-cache
                await self._cache_preferences(preferences.wallet_address, preferences)

                return True
            except Exception as e:
                logger.error("Failed to save preferences", exc_info=True)
                await self.db_session.rollback()

        return False

    async def update_preferences(
        self,
        wallet_address: str,
        updates: dict[str, Any],
    ) -> UserPreferences | None:
        """
        Update specific preference fields.

        Args:
            wallet_address: User's wallet address
            updates: Dictionary of fields to update

        Returns:
            Updated UserPreferences
        """
        # Get current preferences
        current = await self.get_preferences(wallet_address)
        if not current:
            # Create new preferences with defaults
            current = UserPreferences(
                user_id="",
                wallet_address=wallet_address,
            )

        # Apply updates
        if "theme" in updates:
            current.theme = updates["theme"]
        if "language" in updates:
            current.language = updates["language"]
        if "display_name" in updates:
            current.display_name = updates["display_name"]
        if "timezone" in updates:
            current.timezone = updates["timezone"]

        # Nested updates
        if "notifications" in updates:
            current.notifications = NotificationSettings.from_dict(updates["notifications"])
        if "privacy" in updates:
            current.privacy = PrivacySettings.from_dict(updates["privacy"])
        if "trading" in updates:
            current.trading = TradingPreferences.from_dict(updates["trading"])

        current.updated_at = datetime.utcnow()

        # Save
        await self.save_preferences(current)

        return current

    async def _invalidate_cache(self, wallet_address: str) -> None:
        """Invalidate cached preferences"""
        try:
            redis_client = await self._get_redis()
            await redis_client.delete(self._cache_key(wallet_address))
        except Exception as e:
            logger.warning("Failed to invalidate cache", exc_info=True)

    async def delete_preferences(self, wallet_address: str) -> bool:
        """Delete user preferences"""
        await self._invalidate_cache(wallet_address)

        if self.db_session:
            try:
                await self.db_session.execute(
                    delete("auth_schema.user_preferences").where(
                        "wallet_address = :wallet"
                    ),
                    {"wallet": wallet_address}
                )
                await self.db_session.commit()
                return True
            except Exception as e:
                logger.error("Failed to delete preferences", exc_info=True)
                await self.db_session.rollback()

        return False

    async def get_notification_channels(
        self,
        wallet_address: str,
    ) -> list[str]:
        """Get enabled notification channels for user"""
        preferences = await self.get_preferences(wallet_address)
        if not preferences:
            return ["in_app"]  # Default

        channels = []
        if preferences.notifications.in_app_enabled:
            channels.append("in_app")
        if preferences.notifications.push_enabled:
            channels.append("push")
        if preferences.notifications.email_enabled:
            channels.append("email")
        if preferences.notifications.telegram_enabled:
            channels.append("telegram")
        if preferences.notifications.webhook_enabled:
            channels.append("webhook")

        return channels

    async def should_send_notification(
        self,
        wallet_address: str,
        notification_type: str,
    ) -> bool:
        """
        Check if notification should be sent based on user preferences.

        Args:
            wallet_address: User's wallet address
            notification_type: Type of notification

        Returns:
            True if notification should be sent
        """
        preferences = await self.get_preferences(wallet_address)
        if not preferences:
            return True  # Default: send

        notifications = preferences.notifications

        # Check quiet hours
        if notifications.quiet_hours_enabled:
            if self._in_quiet_hours(
                notifications.quiet_hours_start,
                notifications.quiet_hours_end,
                notifications.quiet_hours_timezone,
            ):
                return False

        # Check type preference
        type_map = {
            "price_alert": notifications.price_alerts,
            "position_alert": notifications.position_alerts,
            "transaction": notifications.transaction_notifications,
            "yield": notifications.yield_notifications,
            "news": notifications.news_alerts,
            "whale": notifications.whale_alerts,
            "system": notifications.system_notifications,
        }

        return type_map.get(notification_type, True)

    def _in_quiet_hours(
        self,
        start: str,
        end: str,
        timezone: str,
    ) -> bool:
        """Check if current time is in quiet hours"""
        try:
            from datetime import datetime

            import pytz

            tz = pytz.timezone(timezone)
            now = datetime.now(tz)

            start_time = datetime.strptime(start, "%H:%M").time()
            end_time = datetime.strptime(end, "%H:%M").time()
            current_time = now.time()

            # Handle overnight quiet hours (e.g., 22:00 - 08:00)
            if start_time <= end_time:
                return start_time <= current_time <= end_time
            else:
                return current_time >= start_time or current_time <= end_time

        except Exception:
            return False

    async def export_preferences(
        self,
        wallet_address: str,
    ) -> dict[str, Any] | None:
        """Export all preferences as JSON"""
        preferences = await self.get_preferences(wallet_address)
        return preferences.to_dict() if preferences else None


# ============================================================================
# Default Preferences Factory
# ============================================================================

def get_default_preferences(
    user_id: str,
    wallet_address: str,
) -> UserPreferences:
    """Get default preferences for a new user"""
    return UserPreferences(
        user_id=user_id,
        wallet_address=wallet_address,
        theme="dark",
        language="en",
        timezone="UTC",
        notifications=NotificationSettings(),
        privacy=PrivacySettings(),
        trading=TradingPreferences(),
    )


# ============================================================================
# Global Instance
# ============================================================================

_preferences_service: UserPreferencesService | None = None


def get_preferences_service(db_session: AsyncSession | None = None) -> UserPreferencesService:
    """Get or create preferences service"""
    global _preferences_service
    if _preferences_service is None:
        _preferences_service = UserPreferencesService(db_session)
    return _preferences_service
