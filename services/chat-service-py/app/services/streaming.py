"""
gRPC Streaming Service

Provides real-time streaming capabilities:
- Price streaming (token price updates)
- Position streaming (portfolio position updates)
- Notification streaming (alerts, price alerts)
- Transaction status streaming
- Market events streaming
"""

import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional, Set
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import uuid

import grpc
from grpc import aio as grpc_aio

logger = logging.getLogger(__name__)


# ============================================================================
# Stream Types
# ============================================================================

class StreamType(Enum):
    PRICE = "price"
    POSITION = "position"
    NOTIFICATION = "notification"
    TRANSACTION = "transaction"
    MARKET_EVENT = "market_event"
    CHAT = "chat"


class NotificationType(Enum):
    TYPE_UNSPECIFIED = 0
    TYPE_PRICE_ALERT = 1
    TYPE_POSITION_ALERT = 2
    TYPE_TRANSACTION = 3
    TYPE_AIRDROP = 4
    TYPE_SYSTEM = 5
    TYPE_WHALE = 6


class Priority(Enum):
    PRIORITY_UNSPECIFIED = 0
    PRIORITY_LOW = 1
    PRIORITY_NORMAL = 2
    PRIORITY_HIGH = 3
    PRIORITY_URGENT = 4


# ============================================================================
# Stream Manager
# ============================================================================

@dataclass
class StreamSubscription:
    """Represents a stream subscription"""
    subscription_id: str
    stream_type: StreamType
    wallet_address: Optional[str]
    filters: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    active: bool = True


class StreamManager:
    """
    Central manager for all streaming subscriptions.
    Handles subscription registration, message broadcasting, and cleanup.
    """

    def __init__(self):
        # Active subscriptions by type
        self._subscriptions: Dict[StreamType, Dict[str, StreamSubscription]] = {
            StreamType.PRICE: {},
            StreamType.POSITION: {},
            StreamType.NOTIFICATION: {},
            StreamType.TRANSACTION: {},
            StreamType.MARKET_EVENT: {},
            StreamType.CHAT: {},
        }

        # Price cache for streaming
        self._price_cache: Dict[str, float] = {}
        self._price_timestamps: Dict[str, int] = {}

        # Notification queue for broadcasting
        self._notification_queue: asyncio.Queue = asyncio.Queue()

        # Lock for thread safety
        self._lock = asyncio.Lock()

    async def subscribe(
        self,
        stream_type: StreamType,
        wallet_address: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> StreamSubscription:
        """Register a new subscription"""
        async with self._lock:
            subscription_id = str(uuid.uuid4())
            subscription = StreamSubscription(
                subscription_id=subscription_id,
                stream_type=stream_type,
                wallet_address=wallet_address,
                filters=filters or {},
            )
            self._subscriptions[stream_type][subscription_id] = subscription
            logger.info(f"New subscription: {subscription_id} for {stream_type.value}")
            return subscription

    async def unsubscribe(self, subscription_id: str, stream_type: StreamType) -> bool:
        """Remove a subscription"""
        async with self._lock:
            if subscription_id in self._subscriptions[stream_type]:
                del self._subscriptions[stream_type][subscription_id]
                logger.info(f"Unsubscribed: {subscription_id}")
                return True
            return False

    async def broadcast_price(self, token_address: str, price: float,
                            change_24h: float = 0, volume_24h: float = 0) -> None:
        """Broadcast price update to all price subscribers"""
        self._price_cache[token_address] = price
        self._price_cache[f"{token_address}_change"] = change_24h
        self._price_cache[f"{token_address}_volume"] = volume_24h
        self._price_timestamps[token_address] = int(datetime.utcnow().timestamp())

    async def get_subscribers(self, stream_type: StreamType) -> List[StreamSubscription]:
        """Get all active subscribers for a stream type"""
        async with self._lock:
            return [
                sub for sub in self._subscriptions[stream_type].values()
                if sub.active
            ]

    def get_price_cache(self, token_address: str) -> Optional[Dict[str, Any]]:
        """Get cached price data"""
        if token_address not in self._price_cache:
            return None

        return {
            "price": self._price_cache.get(token_address),
            "change_24h": self._price_cache.get(f"{token_address}_change"),
            "volume_24h": self._price_cache.get(f"{token_address}_volume"),
            "timestamp": self._price_timestamps.get(token_address),
        }

    async def get_stats(self) -> Dict[str, Any]:
        """Get streaming statistics"""
        async with self._lock:
            stats = {}
            for stream_type, subs in self._subscriptions.items():
                stats[stream_type.value] = {
                    "active": sum(1 for s in subs.values() if s.active),
                    "total": len(subs),
                }
            return stats


# ============================================================================
# Streaming Services
# ============================================================================

class PriceStreamService:
    """Service for streaming token prices"""

    def __init__(self, stream_manager: StreamManager):
        self.stream_manager = stream_manager
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self, interval_seconds: float = 5.0) -> None:
        """Start the price streaming background task"""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._price_stream_loop(interval_seconds))
        logger.info("Price stream service started")

    async def stop(self) -> None:
        """Stop the price streaming"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Price stream service stopped")

    async def _price_stream_loop(self, interval: float) -> None:
        """Background loop to fetch and broadcast prices"""
        from app.services.yield_aggregator import get_yield_comparison
        from app.services.trending_tokens import get_hot_tokens

        while self._running:
            try:
                # Get trending tokens
                trending = await get_hot_tokens(limit=20, min_liquidity=10000)

                for token in trending:
                    await self.stream_manager.broadcast_price(
                        token_address=token.get("address", ""),
                        price=token.get("price", 0),
                        change_24h=token.get("price_change_h24", 0),
                        volume_24h=token.get("volume_24h", 0),
                    )

                # Get yields for staking tokens
                yields = await get_yield_comparison("liquid_staking")
                for yield_data in yields:
                    # Broadcast yields as pseudo-prices (1.0 = 100% APY scaled)
                    token = yield_data.get("protocol", "")
                    apy = yield_data.get("apy", 0) or 0

                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Price stream error", exc_info=True)
                await asyncio.sleep(interval)

    async def subscribe_prices(
        self,
        token_addresses: List[str],
        interval_ms: int = 1000,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Stream price updates for specified tokens.

        Yields price updates as dictionaries.
        """
        subscription = await self.stream_manager.subscribe(
            stream_type=StreamType.PRICE,
            filters={"tokens": token_addresses, "interval_ms": interval_ms},
        )

        try:
            while subscription.active:
                # Get latest prices for subscribed tokens
                for token in token_addresses:
                    cached = self.stream_manager.get_price_cache(token)
                    if cached:
                        yield {
                            "token_address": token,
                            "price": cached.get("price"),
                            "price_change_24h": cached.get("change_24h"),
                            "volume_24h": cached.get("volume_24h"),
                            "timestamp": cached.get("timestamp"),
                        }

                await asyncio.sleep(interval_ms / 1000.0)

        finally:
            await self.stream_manager.unsubscribe(
                subscription.subscription_id, StreamType.PRICE
            )


class NotificationStreamService:
    """Service for streaming notifications/alerts"""

    def __init__(self, stream_manager: StreamManager):
        self.stream_manager = stream_manager

    async def subscribe_notifications(
        self,
        wallet_address: str,
        notification_types: Optional[List[str]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream notifications for a wallet"""
        subscription = await self.stream_manager.subscribe(
            stream_type=StreamType.NOTIFICATION,
            wallet_address=wallet_address,
            filters={"types": notification_types or ["all"]},
        )

        try:
            while subscription.active:
                # Check for notifications in database/cache
                # This would be replaced with actual notification fetching
                await asyncio.sleep(5.0)

        finally:
            await self.stream_manager.unsubscribe(
                subscription.subscription_id, StreamType.NOTIFICATION
            )

    async def broadcast_notification(
        self,
        notification_type: NotificationType,
        title: str,
        body: str,
        data: Optional[Dict[str, Any]] = None,
        priority: Priority = Priority.PRIORITY_NORMAL,
        wallet_address: Optional[str] = None,
    ) -> None:
        """Broadcast a notification to relevant subscribers"""
        notification = {
            "id": str(uuid.uuid4()),
            "type": notification_type.name,
            "title": title,
            "body": body,
            "data": json.dumps(data) if data else None,
            "timestamp": int(datetime.utcnow().timestamp()),
            "priority": priority.name,
            "wallet_address": wallet_address,
        }

        # Get all notification subscribers
        subscribers = await self.stream_manager.get_subscribers(
            StreamType.NOTIFICATION
        )

        # Broadcast to matching subscribers
        for sub in subscribers:
            if sub.wallet_address == wallet_address or not wallet_address:
                # Would push to their stream here
                logger.debug(f"Broadcasting notification to {sub.subscription_id}")


class PositionStreamService:
    """Service for streaming portfolio position updates"""

    def __init__(self, stream_manager: StreamManager):
        self.stream_manager = stream_manager

    async def subscribe_positions(
        self,
        wallet_address: str,
        protocols: Optional[List[str]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream position updates for a wallet"""
        subscription = await self.stream_manager.subscribe(
            stream_type=StreamType.POSITION,
            wallet_address=wallet_address,
            filters={"protocols": protocols or ["all"]},
        )

        try:
            while subscription.active:
                # Fetch current positions from portfolio service
                # This would integrate with the actual portfolio data
                await asyncio.sleep(10.0)

        finally:
            await self.stream_manager.unsubscribe(
                subscription.subscription_id, StreamType.POSITION
            )


# ============================================================================
# Main Streaming Server (gRPC)
# ============================================================================

class StreamServiceServicer:
    """
    gRPC service implementation for streaming.
    This provides the actual gRPC server for external connections.
    """

    def __init__(self):
        self.stream_manager = StreamManager()
        self.price_service = PriceStreamService(self.stream_manager)
        self.notification_service = NotificationStreamService(self.stream_manager)
        self.position_service = PositionStreamService(self.stream_manager)

    async def start(self) -> None:
        """Start all streaming services"""
        await self.price_service.start(interval_seconds=10.0)
        logger.info("All streaming services started")

    async def stop(self) -> None:
        """Stop all streaming services"""
        await self.price_service.stop()
        logger.info("All streaming services stopped")

    async def get_stats(self) -> Dict[str, Any]:
        """Get streaming statistics"""
        return await self.stream_manager.get_stats()


# ============================================================================
# Client-side Streaming (for consuming streams)
# ============================================================================

class StreamClient:
    """
    Client for connecting to gRPC streams.
    Used by other services to consume streams.
    """

    def __init__(self, grpc_url: str = "localhost:50052"):
        self.grpc_url = grpc_url
        self._channel: Optional[grpc_aio.Channel] = None
        self._stub = None
        self._active_streams: List[asyncio.Task] = []

    async def connect(self) -> None:
        """Connect to the gRPC server"""
        self._channel = grpc_aio.insecure_channel(self.grpc_url)
        logger.info(f"Connected to stream server at {self.grpc_url}")

    async def disconnect(self) -> None:
        """Disconnect from the gRPC server"""
        # Cancel all active streams
        for task in self._active_streams:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        if self._channel:
            await self._channel.close()
        logger.info("Disconnected from stream server")

    async def stream_prices(
        self,
        token_addresses: List[str],
        callback: callable,
    ) -> None:
        """
        Stream prices and call callback for each update.

        Note: Actual gRPC stub would be used here.
        For now, this is a placeholder that would use the generated stub.
        """
        # This would use the actual gRPC streaming call:
        # stub = StreamServiceStub(self._channel)
        # stream = stub.SubscribePrices(PriceSubscribeRequest(...))
        # async for price in stream:
        #     callback(price)

        logger.info(f"Would stream prices for: {token_addresses}")
        pass

    async def stream_notifications(
        self,
        wallet_address: str,
        callback: callable,
    ) -> None:
        """Stream notifications for a wallet"""
        logger.info(f"Would stream notifications for: {wallet_address}")
        pass


# ============================================================================
# Global Instance
# ============================================================================

_stream_service: Optional[StreamServiceServicer] = None


async def get_stream_service() -> StreamServiceServicer:
    """Get or create the global stream service"""
    global _stream_service

    if _stream_service is None:
        _stream_service = StreamServiceServicer()
        # Background price loop is started lazily when clients connect,
        # not at startup — avoids polling external APIs with no subscribers.

    return _stream_service


async def close_stream_service() -> None:
    """Close the global stream service"""
    global _stream_service

    if _stream_service:
        await _stream_service.stop()
        _stream_service = None
