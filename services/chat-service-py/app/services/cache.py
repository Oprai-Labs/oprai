"""
Redis Cache Service

Centralized caching for the chat service:
- Yield data caching
- Token price caching
- Portfolio data caching
- Session data caching
- Configurable TTL per cache key
"""

import json
import logging
from typing import Any, Optional
from dataclasses import dataclass

import redis.asyncio as redis
from redis.asyncio import Redis

logger = logging.getLogger(__name__)


# Cache key prefixes
CACHE_PREFIX = "oprai:chat"


@dataclass
class CacheConfig:
    """Cache configuration for different data types"""
    ttl_seconds: int  # Time to live in seconds
    prefix: str  # Cache key prefix


# Predefined cache configurations.
#
# TTLs are tuned per data nature, not per protocol. The user-visible knobs:
#   * price-like (changes every block): 15s — enough to survive a quote→build
#     round-trip without re-fetching, short enough that a swap card never
#     shows stale dollar amounts.
#   * volume / TVL (5-30 min realtime cadence at the source): 120s — covers
#     a full picker conversation without going stale.
#   * holders / risk score (heavy aggregation, slow to change): 10–15 min.
#   * static metadata (logo, decimals, security score): 24h.
CACHE_CONFIGS = {
    # Yields — protocol APYs change with utilization; 5min is the right
    # cadence for "compare yields" UI.
    "yields:liquid_staking": CacheConfig(ttl_seconds=300, prefix="yields"),
    "yields:lending":        CacheConfig(ttl_seconds=300, prefix="yields"),
    "yields:all":            CacheConfig(ttl_seconds=300, prefix="yields"),

    # Token prices — high volatility. 15s is short enough that a stale
    # number can't drive a wrong swap decision, long enough to absorb
    # bursty re-renders on a portfolio dashboard.
    "price":           CacheConfig(ttl_seconds=15,  prefix="price"),
    "prices:batch":    CacheConfig(ttl_seconds=15,  prefix="prices"),
    "price:ohlcv:1m":  CacheConfig(ttl_seconds=30,  prefix="ohlcv"),
    "price:ohlcv:15m": CacheConfig(ttl_seconds=300, prefix="ohlcv"),
    "price:ohlcv:1h":  CacheConfig(ttl_seconds=900, prefix="ohlcv"),
    "price:ohlcv:1d":  CacheConfig(ttl_seconds=3600, prefix="ohlcv"),

    # Portfolio — wallet balances refresh on every tx, but list pages can
    # tolerate 30s of staleness in exchange for a snappier UI.
    "portfolio":           CacheConfig(ttl_seconds=30,  prefix="portfolio"),
    "portfolio:positions": CacheConfig(ttl_seconds=60,  prefix="portfolio"),
    "portfolio:history":   CacheConfig(ttl_seconds=300, prefix="portfolio"),

    # Protocol data — TVL and pool stats move on multi-minute cadences.
    "protocol:stats":   CacheConfig(ttl_seconds=120, prefix="protocol"),
    "protocol:compare": CacheConfig(ttl_seconds=120, prefix="protocol"),
    "protocol:pools":   CacheConfig(ttl_seconds=120, prefix="protocol"),

    # Token security score — expensive aggregation, changes rarely.
    "token:security": CacheConfig(ttl_seconds=86400, prefix="token"),
    # Token holders / distribution — heavy query, hourly cadence is plenty.
    "token:holders":  CacheConfig(ttl_seconds=600,   prefix="token"),
    # Token static metadata — symbol, decimals, logo. Effectively immutable.
    "token:meta":     CacheConfig(ttl_seconds=86400, prefix="token"),

    # NFT collection stats — 10min covers the typical scroll-and-decide loop.
    "nft:collection": CacheConfig(ttl_seconds=600,  prefix="nft"),
    "nft:listings":   CacheConfig(ttl_seconds=120,  prefix="nft"),

    # Risk analysis - cache for 5 minutes
    "risk:position":   CacheConfig(ttl_seconds=120, prefix="risk"),
    "risk:portfolio":  CacheConfig(ttl_seconds=300, prefix="risk"),

    # Per-session "what the user just saw in a QueryCard" — survives across
    # message-block summarization (which strips per-message metadata) so the
    # next turn can still resolve "the highest TVL one" against real rows.
    # 24h is generous; cache is overwritten on every new card render anyway.
    "session:card_state": CacheConfig(ttl_seconds=86400, prefix="card"),

    # Per-wallet daily LLM token usage — drives the OPRAI_LLM_DAILY_TOKEN_CAP
    # backstop. 25h TTL so stale rows expire without a sweep job.
    "session:llm_daily": CacheConfig(ttl_seconds=90000, prefix="llmcost"),

    # Generic - default cache time
    "default": CacheConfig(ttl_seconds=60, prefix="default"),
}


class CacheService:
    """Redis-backed caching service"""

    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis_url = redis_url
        self._redis: Optional[Redis] = None
        self._connected = False

    async def connect(self) -> None:
        """Connect to Redis"""
        if self._connected and self._redis:
            return

        try:
            self._redis = redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            # Test connection
            await self._redis.ping()
            self._connected = True
            logger.info("Redis cache connected")
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}. Caching disabled.")
            self._connected = False
            self._redis = None

    async def disconnect(self) -> None:
        """Disconnect from Redis"""
        if self._redis:
            await self._redis.close()
            self._connected = False
            logger.info("Redis cache disconnected")

    def _make_key(self, prefix: str, *parts: str) -> str:
        """Generate cache key"""
        key_parts = [CACHE_PREFIX, prefix] + list(parts)
        return ":".join(key_parts)

    def _get_config(self, cache_type: str) -> CacheConfig:
        """Get cache configuration"""
        return CACHE_CONFIGS.get(cache_type, CACHE_CONFIGS["default"])

    async def get(
        self,
        cache_type: str,
        *key_parts: str,
    ) -> Optional[Any]:
        """
        Get value from cache.

        Args:
            cache_type: Type of cache (determines TTL)
            *key_parts: Parts of the cache key

        Returns:
            Cached value or None if not found
        """
        if not self._connected or not self._redis:
            return None

        try:
            key = self._make_key(self._get_config(cache_type).prefix, *key_parts)
            value = await self._redis.get(key)

            if value:
                logger.debug(f"Cache HIT: {key}")
                envelope = json.loads(value)
                # Backward compat: callers that haven't migrated still see
                # the raw payload; new callers use `get_with_age`.
                if isinstance(envelope, dict) and "_v" in envelope and "_data" in envelope:
                    return envelope["_data"]
                return envelope

            logger.debug(f"Cache MISS: {key}")
            return None
        except Exception as e:
            logger.error("Cache get error", exc_info=True)
            return None

    async def get_with_age(
        self,
        cache_type: str,
        *key_parts: str,
    ) -> tuple[Optional[Any], Optional[int]]:
        """Like `get`, but also returns the cached entry's age in seconds.

        Age is None on cache miss or for legacy entries written before the
        envelope wrapper was introduced. Frontend uses this to render a
        "stale" badge when a price/TVL number is older than its expected
        refresh window.
        """
        import time as _time

        if not self._connected or not self._redis:
            return None, None

        try:
            key = self._make_key(self._get_config(cache_type).prefix, *key_parts)
            value = await self._redis.get(key)
            if not value:
                return None, None
            envelope = json.loads(value)
            if isinstance(envelope, dict) and "_v" in envelope and "_data" in envelope:
                fetched_at = float(envelope.get("_fetched_at", 0) or 0)
                age = int(_time.time() - fetched_at) if fetched_at else None
                return envelope["_data"], age
            return envelope, None
        except Exception:
            logger.error("Cache get_with_age error", exc_info=True)
            return None, None

    async def set(
        self,
        cache_type: str,
        value: Any,
        *key_parts: str,
        ttl: Optional[int] = None,
    ) -> bool:
        """
        Set value in cache.

        Args:
            cache_type: Type of cache (determines TTL if not overridden)
            value: Value to cache
            *key_parts: Parts of the cache key
            ttl: Optional TTL override in seconds

        Returns:
            True if successful
        """
        if not self._connected or not self._redis:
            return False

        try:
            import time as _time

            config = self._get_config(cache_type)
            key = self._make_key(config.prefix, *key_parts)
            ttl = ttl or config.ttl_seconds

            # Wrap in an envelope so consumers can read the fetch timestamp
            # later. _v allows future schema upgrades without breaking old
            # readers (the get() unwrap also handles legacy entries).
            envelope = {
                "_v": 1,
                "_fetched_at": _time.time(),
                "_ttl": ttl,
                "_data": value,
            }
            serialized = json.dumps(envelope)
            await self._redis.setex(key, ttl, serialized)

            logger.debug(f"Cache SET: {key} (TTL: {ttl}s)")
            return True
        except Exception as e:
            logger.error("Cache set error", exc_info=True)
            return False

    async def delete(self, cache_type: str, *key_parts: str) -> bool:
        """Delete value from cache"""
        if not self._connected or not self._redis:
            return False

        try:
            key = self._make_key(self._get_config(cache_type).prefix, *key_parts)
            await self._redis.delete(key)
            logger.debug(f"Cache DELETE: {key}")
            return True
        except Exception as e:
            logger.error("Cache delete error", exc_info=True)
            return False

    async def delete_pattern(self, pattern: str) -> int:
        """
        Delete all keys matching pattern.

        Args:
            pattern: Pattern to match (e.g., "yields:*")

        Returns:
            Number of keys deleted
        """
        if not self._connected or not self._redis:
            return 0

        try:
            full_pattern = self._make_key(pattern)
            keys = []
            async for key in self._redis.scan_iter(match=full_pattern):
                keys.append(key)

            if keys:
                deleted = await self._redis.delete(*keys)
                logger.info(f"Cache invalidation: {deleted} keys for pattern {pattern}")
                return deleted
            return 0
        except Exception as e:
            logger.error("Cache pattern delete error", exc_info=True)
            return 0

    async def invalidate_yields(self) -> int:
        """Invalidate all yield caches"""
        return await self.delete_pattern("yields:*")

    async def invalidate_prices(self) -> int:
        """Invalidate all price caches"""
        return await self.delete_pattern("price:*")

    async def invalidate_portfolio(self, wallet: str) -> int:
        """Invalidate portfolio caches for a wallet"""
        return await self.delete_pattern(f"portfolio:{wallet[:8]}*")

    async def invalidate_token(self, token_address: str) -> int:
        """Invalidate token-specific caches"""
        return await self.delete_pattern(f"token:{token_address[:8]}*")

    async def get_or_set(
        self,
        cache_type: str,
        *key_parts: str,
        factory: Optional[callable] = None,
        ttl: Optional[int] = None,
    ) -> tuple[Any, bool]:
        """
        Get from cache or compute and cache.

        Args:
            cache_type: Type of cache
            *key_parts: Cache key parts
            factory: Optional async function to compute value if not cached
            ttl: Optional TTL override

        Returns:
            Tuple of (value, was_cached)
        """
        # Try to get from cache
        cached = await self.get(cache_type, *key_parts)
        if cached is not None:
            return cached, True

        # If factory provided, compute value
        if factory:
            try:
                value = await factory()
                if value is not None:
                    await self.set(cache_type, value, *key_parts, ttl=ttl)
                    return value, False
            except Exception as e:
                logger.error("Factory error", exc_info=True)

        return None, False

    async def health_check(self) -> dict:
        """Check Redis health"""
        if not self._connected or not self._redis:
            return {"status": "disconnected", "connected": False}

        try:
            await self._redis.ping()
            info = await self._redis.info("stats")
            memory = await self._redis.info("memory")

            return {
                "status": "healthy",
                "connected": True,
                "version": info.get("redis_version"),
                "used_memory_human": memory.get("used_memory_human"),
                "total_connections": info.get("total_connections_received", 0),
            }
        except Exception as e:
            return {
                "status": "error",
                "connected": False,
                "error": str(e),
            }

    async def get_stats(self) -> dict:
        """Get cache statistics"""
        if not self._connected or not self._redis:
            return {"error": "Not connected"}

        try:
            # Count keys by prefix
            stats = {}
            for prefix in ["yields", "price", "portfolio", "protocol", "token", "risk"]:
                pattern = self._make_key(f"{prefix}:*")
                count = 0
                async for _ in self._redis.scan_iter(match=pattern):
                    count += 1
                stats[prefix] = count

            return stats
        except Exception as e:
            return {"error": str(e)}


# Global instance
_cache_service: Optional[CacheService] = None


async def get_redis_client():
    """Return the underlying Redis client from the global cache service."""
    svc = await get_cache_service()
    return svc._redis


async def get_cache_service() -> CacheService:
    """Get or create the global cache service"""
    global _cache_service

    if _cache_service is None:
        from app.config import settings

        redis_url = getattr(settings, 'REDIS_URL', None) or "redis://localhost:6379"
        _cache_service = CacheService(redis_url)
        await _cache_service.connect()

    return _cache_service


async def close_cache_service() -> None:
    """Close the global cache service"""
    global _cache_service

    if _cache_service:
        await _cache_service.disconnect()
        _cache_service = None
