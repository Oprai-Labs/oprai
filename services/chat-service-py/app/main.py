"""FastAPI application entrypoint for the OPRAI chat service.

Starts both the HTTP server (FastAPI/Uvicorn) and the gRPC server
within the same process using the async lifespan protocol.
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from typing import Any, Dict, List, Optional

# Configure structured logging before any logger is created.
from app.logging_config import configure_logging
configure_logging(level=os.environ.get("LOG_LEVEL", "INFO"), fmt=os.environ.get("LOG_FORMAT", "json"))

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel, Field
from prometheus_client import (
    Counter,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)
from starlette.responses import Response, StreamingResponse
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.connection import get_session, init_db, close_db, async_session_factory
from app.middleware.request_audit import RequestAuditMiddleware
from app.services.cache import get_cache_service, close_cache_service
from app.grpc_server import start_grpc_server
from app.middleware.auth import require_auth, require_internal
from app.services import session as session_svc
from app.services import message as message_svc
from app.services.llm import LLMService
from app.services.summary import build_llm_context, maybe_create_summary
from app.services.title_generator import generate_title
from app.services.yield_aggregator import get_yield_comparison
from app.services.portfolio_optimizer import analyze_portfolio, optimize_portfolio
from app.services.protocol_comparison import get_protocol_comparison
from app.services.risk_assessment import analyze_portfolio_risk, assess_position_risk
from app.services.token_security import analyze_token_security
from app.services.trending_tokens import (
    get_trending_summary,
    get_hot_tokens,
    get_top_gainers,
    get_top_losers,
    search_tokens,
    get_token_by_address,
)
from app.services.streaming import (
    get_stream_service,
    StreamType,
)
from app.services.audit_trail import (
    AuditTrailService,
    AuditEvent,
    AuditEventType,
    AuditEventSeverity,
    AuditEntityType,
)

import structlog
logger = structlog.get_logger(__name__)


async def _fire_audit(event: AuditEvent) -> None:
    try:
        async with async_session_factory() as db:
            await AuditTrailService(db).log_event(event)
            await db.commit()
    except Exception:
        pass


def _audit(db: AsyncSession, event: AuditEvent) -> None:
    """Non-blocking audit write — uses its own session to avoid race conditions."""
    asyncio.ensure_future(_fire_audit(event))

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------
REQUEST_COUNT = Counter(
    "chat_service_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)
REQUEST_LATENCY = Histogram(
    "chat_service_request_duration_seconds",
    "Request latency in seconds",
    ["method", "endpoint"],
)

# ---------------------------------------------------------------------------
# Pydantic request/response schemas
# ---------------------------------------------------------------------------


class CreateSessionRequest(BaseModel):
    title: str = Field(default="New chat", max_length=200)


class UpdateSessionRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


class PinSessionRequest(BaseModel):
    pinned: bool


class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)


class Attachment(BaseModel):
    """File attachment (e.g., uploaded image with IPFS URL)."""
    url: str = Field(..., description="IPFS URL or other URL for the attachment")
    type: str = Field(default="image", description="Attachment type (image, file, etc.)")
    filename: str | None = Field(default=None, description="Original filename")


class StreamMessageRequest(BaseModel):
    session_id: str = Field(..., alias="sessionId")
    content: str = Field(..., min_length=1, max_length=2000)
    attachments: list[Attachment] = Field(default_factory=list)
    # thinking: reserved — accepted to avoid frontend errors, currently unused
    thinking: bool = Field(default=False)

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
_grpc_server = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: init DB, start gRPC, yield, then teardown."""
    global _grpc_server

    # Startup validation
    if settings.OPRAI_JWT_SECRET == "dev-insecure-secret-change":
        raise RuntimeError(
            "FATAL: OPRAI_JWT_SECRET is set to the insecure default value. "
            "Set OPRAI_JWT_SECRET to a secure random value in all environments."
        )

    if settings.OPRAI_INTERNAL_API_KEY == "dev-internal-key-change":
        if settings.NODE_ENV == "production":
            raise RuntimeError(
                "FATAL: OPRAI_INTERNAL_API_KEY is set to the insecure default. "
                "Refusing to start in production."
            )
        logger.warning(
            "Using insecure default internal API key, set OPRAI_INTERNAL_API_KEY in production"
        )

    await init_db()
    logger.info("Database initialised")

    # Connect to Redis cache
    cache = await get_cache_service()
    cache_health = await cache.health_check()
    logger.info("Cache status: %s", cache_health.get("status", "unknown"))

    # Start streaming service
    from app.services.streaming import get_stream_service
    stream_service = await get_stream_service()
    logger.info("Streaming service started")

    _grpc_server = await start_grpc_server()
    logger.info("gRPC server started on port %d", settings.GRPC_PORT)

    yield

    # Shutdown
    if _grpc_server is not None:
        await _grpc_server.stop(grace=5)
        logger.info("gRPC server stopped")
    await close_cache_service()
    logger.info("Cache connections closed")
    from app.services.streaming import close_stream_service
    await close_stream_service()
    logger.info("Streaming service stopped")
    await close_db()
    logger.info("Database connections closed")


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="OPRAI Chat Service",
    version="0.1.0",
    lifespan=lifespan,
)

_cors_raw = settings.CORS_ALLOWED_ORIGINS
if _cors_raw:
    _cors_origins = [o.strip() for o in _cors_raw.split(",") if o.strip()]
else:
    _cors_origins = ["http://localhost:3000", "http://localhost:4200"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Accept", "Authorization", "Content-Type", "X-Requested-With", "X-User-Wallet", "X-Internal-Api-Key"],
)

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"error": detail})

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"error": "Validation error", "details": exc.errors()})

# Request audit middleware — adds X-Request-ID and structured access logs.
app.add_middleware(RequestAuditMiddleware)


# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next) -> Response:
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "0"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    return response


# ---------------------------------------------------------------------------
# Request body size limiter — reject payloads over 10 MB to prevent DoS
# ---------------------------------------------------------------------------
_MAX_BODY_BYTES = 10 * 1024 * 1024  # 10 MB


@app.middleware("http")
async def limit_request_body(request: Request, call_next) -> Response:
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > _MAX_BODY_BYTES:
        return JSONResponse(status_code=413, content={"error": "Request body too large (max 10 MB)"})
    return await call_next(request)


# ---------------------------------------------------------------------------
# Middleware for metrics
# ---------------------------------------------------------------------------
@app.middleware("http")
async def metrics_middleware(request: Request, call_next) -> Response:
    import time

    method = request.method
    path = request.url.path
    start = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start
    REQUEST_COUNT.labels(method=method, endpoint=path, status=response.status_code).inc()
    REQUEST_LATENCY.labels(method=method, endpoint=path).observe(duration)
    return response


# ---------------------------------------------------------------------------
# Health & Metrics
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "chat-service-py",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/metrics")
async def metrics(request: Request):
    """Prometheus metrics — restricted to internal scrapers via X-Internal-Api-Key."""
    key = settings.OPRAI_INTERNAL_API_KEY
    if key and request.headers.get("X-Internal-Api-Key") != key:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


# ---------------------------------------------------------------------------
# Cache Management
# ---------------------------------------------------------------------------
@app.get("/cache/health")
async def cache_health(_wallet: str = Depends(require_auth)):
    """Check Redis cache health. Requires authentication to prevent info leakage."""
    cache = await get_cache_service()
    health = await cache.health_check()
    return {"cache": health}


@app.get("/cache/stats")
async def cache_stats(_wallet: str = Depends(require_auth)):
    """Get cache statistics. Requires authentication to prevent info leakage."""
    cache = await get_cache_service()
    stats = await cache.get_stats()
    return {"stats": stats}


@app.post("/cache/invalidate")
async def invalidate_cache(
    type: str = Query(
        ...,
        description="Cache type to invalidate: yields, prices, portfolio, token, all",
    ),
    key: str | None = Query(None, description="Specific key to invalidate"),
    _internal: None = Depends(require_internal),
):
    """Invalidate cache entries"""
    cache = await get_cache_service()

    if key:
        # Invalidate specific key
        await cache.delete(type, key)
        return {"success": True, "message": f"Invalidated {type}:{key}"}

    # Invalidate by type
    if type == "yields":
        count = await cache.invalidate_yields()
    elif type == "prices":
        count = await cache.invalidate_prices()
    elif type == "portfolio":
        count = await cache.invalidate_portfolio(key or "")
    elif type == "token":
        count = await cache.invalidate_token(key or "")
    elif type == "all":
        count = await cache.delete_pattern("*")
    else:
        raise HTTPException(status_code=400, detail="Invalid cache type")

    return {"success": True, "invalidated": count}


# ---------------------------------------------------------------------------
# Yield / APR Routes
# ---------------------------------------------------------------------------
@app.get("/yields")
async def get_yields(
    category: str = Query("liquid_staking", description="Category: liquid_staking, lending"),
    limit: int = Query(10, ge=1, le=50),
    _wallet: str = Depends(require_auth),
):
    """Get current yields from DeFi protocols."""
    try:
        results = await get_yield_comparison(category)
        # Sort by APY descending and limit
        sorted_results = sorted(
            [r for r in results if r.get("apy") is not None],
            key=lambda x: x["apy"],
            reverse=True,
        )[:limit]
        return {
            "success": True,
            "category": category,
            "count": len(sorted_results),
            "yields": sorted_results,
        }
    except Exception as e:
        logger.error("Failed to fetch yields", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/yields/all")
async def get_all_yields(_wallet: str = Depends(require_auth)):
    """Get yields from all categories."""
    categories = ["liquid_staking", "lending"]
    results = {}
    for cat in categories:
        try:
            yields = await get_yield_comparison(cat)
            results[cat] = sorted(
                [r for r in yields if r.get("apy") is not None],
                key=lambda x: x["apy"],
                reverse=True,
            )
        except Exception as e:
            logger.error("Failed to fetch yields for category", exc_info=True)
            results[cat] = []
    return {"success": True, "results": results}


# ---------------------------------------------------------------------------
# Portfolio Optimizer Routes
# ---------------------------------------------------------------------------
class PortfolioPosition(BaseModel):
    """A single portfolio position."""
    protocol: str
    token: str
    value_usd: float
    apy: float | None = None
    cost_basis: float | None = None


class PortfolioAnalyzeRequest(BaseModel):
    """Request to analyze a portfolio."""
    positions: List[PortfolioPosition]
    risk_tolerance: str = Field(default="moderate", description="conservative, moderate, or aggressive")
    prices_24h: Dict[str, float] | None = Field(default=None, description="Token prices for 24h change")


class PortfolioOptimizeRequest(BaseModel):
    """Request to optimize a portfolio."""
    positions: List[PortfolioPosition]
    category: str = Field(default="liquid_staking", description="liquid_staking, lending")
    risk_tolerance: str = Field(default="moderate")


@app.post("/portfolio/analyze")
async def analyze_portfolio_endpoint(
    body: PortfolioAnalyzeRequest,
    _wallet: str = Depends(require_auth),
):
    """Analyze a portfolio and provide optimization recommendations."""
    try:
        # Convert to dict format for the service
        positions = [p.model_dump() for p in body.positions]

        # Get yields for weighted APY calculation
        yields = await get_yield_comparison("liquid_staking")
        yields.extend(await get_yield_comparison("lending"))

        result = analyze_portfolio(
            positions=positions,
            yields=yields,
            risk_tolerance=body.risk_tolerance,
            prices_24h=body.prices_24h,
        )
        return {"success": True, "analysis": result}
    except Exception as e:
        logger.error("Failed to analyze portfolio", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/portfolio/optimize")
async def optimize_portfolio_endpoint(
    body: PortfolioOptimizeRequest,
    _wallet: str = Depends(require_auth),
):
    """Get portfolio optimization recommendations with yield data."""
    try:
        positions = [p.model_dump() for p in body.positions]
        result = await optimize_portfolio(
            positions=positions,
            category=body.category,
            risk_tolerance=body.risk_tolerance,
        )
        return {"success": True, "optimization": result}
    except Exception as e:
        logger.error("Failed to optimize portfolio", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Protocol Comparison Routes
# ---------------------------------------------------------------------------
@app.get("/protocols/compare")
async def compare_protocols_endpoint(
    category: str | None = Query(None, description="Filter by: liquid_staking, lending, dex"),
    sort_by: str = Query("apy", description="Sort by: apy, tvl, risk, risk_adjusted"),
    limit: int = Query(10, ge=1, le=50),
    _wallet: str = Depends(require_auth),
):
    """Compare DeFi protocols by APY, TVL, risk, or risk-adjusted returns."""
    try:
        result = await get_protocol_comparison(
            category=category,
            sort_by=sort_by,
            limit=limit,
        )
        return {"success": True, **result}
    except Exception as e:
        logger.error("Failed to compare protocols", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Risk Assessment Routes
# ---------------------------------------------------------------------------
class RiskPosition(BaseModel):
    """A position for risk assessment."""
    id: str | None = None
    protocol: str
    type: str  # stake, lend, lp, borrow, etc.
    value_usd: float
    collateral_usd: float | None = None
    debt_usd: float | None = None
    initial_price: float | None = None
    current_price: float | None = None
    is_stable_pair: bool | None = None


class PortfolioRiskRequest(BaseModel):
    """Request for portfolio risk analysis."""
    positions: List[RiskPosition]
    prices_24h: Dict[str, float] | None = None


@app.post("/risk/analyze")
async def analyze_portfolio_risk_endpoint(
    body: PortfolioRiskRequest,
    _wallet: str = Depends(require_auth),
):
    """Analyze risk for entire portfolio including liquidation and impermanent loss."""
    try:
        positions = [p.model_dump() for p in body.positions]
        result = analyze_portfolio_risk(positions, body.prices_24h)
        return {"success": True, "analysis": result}
    except Exception as e:
        logger.error("Failed to analyze portfolio risk", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/risk/position")
async def assess_position_risk_endpoint(
    position: RiskPosition,
    _wallet: str = Depends(require_auth),
):
    """Assess risk for a single position."""
    try:
        pos_dict = position.model_dump()
        result = assess_position_risk(pos_dict, None)
        return {"success": True, "risk": result}
    except Exception as e:
        logger.error("Failed to assess position risk", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Token Security Routes
# ---------------------------------------------------------------------------
class TokenHolderInput(BaseModel):
    """A token holder"""
    address: str
    amount: float = 0
    pct: float = 0


class TokenSecurityRequest(BaseModel):
    """Request for token security analysis"""
    token_address: str
    holders: List[TokenHolderInput] | None = None
    liquidity_usd: float | None = None
    market_cap_usd: float | None = None
    mint_authority: str | None = None
    freeze_authority: str | None = None
    supply: float = 0
    decimals: int = 9
    metadata: Dict[str, Any] | None = None
    pair_address: str | None = None


@app.post("/token/security")
async def analyze_token_security_endpoint(
    body: TokenSecurityRequest,
    _wallet: str = Depends(require_auth),
):
    """
    Analyze token security - rug pull detection, holder analysis, mint authority.
    """
    try:
        holders = None
        if body.holders:
            holders = [h.model_dump() for h in body.holders]

        result = await analyze_token_security(
            token_address=body.token_address,
            holders=holders,
            liquidity_usd=body.liquidity_usd,
            market_cap_usd=body.market_cap_usd,
            mint_authority=body.mint_authority,
            freeze_authority=body.freeze_authority,
            supply=body.supply,
            decimals=body.decimals,
            metadata=body.metadata,
            pair_address=body.pair_address,
        )
        return {"success": True, "security": result}
    except Exception as e:
        logger.error("Failed to analyze token security", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Trending Tokens Routes
# ---------------------------------------------------------------------------
@app.get("/tokens/trending")
async def get_trending_tokens(
    timeframe: str = Query("24h", description="Timeframe: 1h, 6h, 24h"),
    category: str = Query("all", description="Category: hot, gainers, losers, all"),
    limit: int = Query(10, ge=1, le=50),
    min_liquidity: float = Query(10000, ge=0, description="Minimum liquidity in USD"),
    _wallet: str = Depends(require_auth),
):
    """
    Get trending tokens from DexScreener.
    - hot: Highest volume tokens
    - gainers: Top price gainers
    - losers: Top price losers
    - all: Summary with all categories
    """
    try:
        if category == "hot":
            tokens = await get_hot_tokens(limit=limit, min_liquidity=min_liquidity)
            return {"success": True, "category": "hot", "tokens": tokens}
        elif category == "gainers":
            timeframe_map = {"1h": "1h", "6h": "6h", "24h": "24h"}
            tf = timeframe_map.get(timeframe, "24h")
            tokens = await get_top_gainers(tf, limit=limit, min_liquidity=min_liquidity)
            return {"success": True, "category": "gainers", "timeframe": tf, "tokens": tokens}
        elif category == "losers":
            timeframe_map = {"1h": "1h", "6h": "6h", "24h": "24h"}
            tf = timeframe_map.get(timeframe, "24h")
            tokens = await get_top_losers(tf, limit=limit, min_liquidity=min_liquidity)
            return {"success": True, "category": "losers", "timeframe": tf, "tokens": tokens}
        else:
            # Return summary
            summary = await get_trending_summary()
            return {"success": True, "category": "all", **summary}
    except Exception as e:
        logger.error("Failed to fetch trending tokens", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/tokens/search")
async def search_tokens_endpoint(
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(10, ge=1, le=50),
    _wallet: str = Depends(require_auth),
):
    """Search tokens by symbol or name."""
    try:
        tokens = await search_tokens(q, limit=limit)
        return {"success": True, "query": q, "tokens": tokens}
    except Exception as e:
        logger.error("Failed to search tokens", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/tokens/{token_address}")
async def get_token_endpoint(
    token_address: str,
    _wallet: str = Depends(require_auth),
):
    """Get detailed token info by address."""
    try:
        token = await get_token_by_address(token_address)
        if not token:
            raise HTTPException(status_code=404, detail="Token not found")
        return {"success": True, "token": token}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to fetch token", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Streaming Routes (SSE - Server-Sent Events)
# ---------------------------------------------------------------------------
@app.get("/stream/prices")
async def stream_prices(
    tokens: str = Query(..., description="Comma-separated token addresses"),
    interval: int = Query(1, ge=1, le=60, description="Update interval in seconds"),
    _wallet: str = Depends(require_auth),
):
    """
    Stream price updates via Server-Sent Events (SSE).

    Example: /stream/prices?tokens=SOL,USDC,BTC&interval=5
    """
    token_list = [t.strip() for t in tokens.split(",") if t.strip()]

    async def event_generator():
        from app.services.streaming import PriceStreamService, get_stream_service

        stream_service = await get_stream_service()
        price_service = PriceStreamService(stream_service.stream_manager)

        try:
            async for price_update in price_service.subscribe_prices(
                token_addresses=token_list,
                interval_ms=interval * 1000,
            ):
                yield f"data: {json.dumps(price_update)}\n\n"

        except asyncio.CancelledError:
            logger.debug("Price stream closed by client")
        except Exception:
            logger.error("Price stream error", exc_info=True)
            yield f"data: {json.dumps({'error': 'stream_error', 'message': 'An error occurred'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/stream/positions")
async def stream_positions(
    protocols: str = Query("", description="Comma-separated protocol filters"),
    wallet: str = Depends(require_auth),
):
    """
    Stream portfolio position updates via SSE.

    Wallet is derived from the authenticated JWT — clients cannot request
    position updates for wallets they do not own.

    Example: /stream/positions?protocols=jupiter,raydium
    """
    protocol_list = [p.strip() for p in protocols.split(",") if p.strip()] if protocols else None

    async def event_generator():
        from app.services.streaming import PositionStreamService, get_stream_service

        stream_service = await get_stream_service()
        position_service = PositionStreamService(stream_service.stream_manager)

        try:
            async for position_update in position_service.subscribe_positions(
                wallet_address=wallet,
                protocols=protocol_list,
            ):
                yield f"data: {json.dumps(position_update)}\n\n"

        except asyncio.CancelledError:
            logger.debug("Position stream closed by client")
        except Exception:
            logger.error("Position stream error", exc_info=True)
            yield f"data: {json.dumps({'error': 'stream_error', 'message': 'An error occurred'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/stream/notifications")
async def stream_notifications(
    wallet: str = Depends(require_auth),
):
    """
    Stream notifications/alerts via SSE.

    Example: /stream/notifications?wallet=ABC...
    """

    async def event_generator():
        from app.services.streaming import NotificationStreamService, get_stream_service

        stream_service = await get_stream_service()
        notification_service = NotificationStreamService(stream_service.stream_manager)

        try:
            async for notification in notification_service.subscribe_notifications(
                wallet_address=wallet,
            ):
                yield f"data: {json.dumps(notification)}\n\n"

        except asyncio.CancelledError:
            logger.debug("Notification stream closed by client")
        except Exception:
            logger.error("Notification stream error", exc_info=True)
            yield f"data: {json.dumps({'error': 'stream_error', 'message': 'An error occurred'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/stream/stats")
async def stream_stats(_wallet: str = Depends(require_auth)):
    """Get streaming service statistics"""
    try:
        stream_service = await get_stream_service()
        stats = await stream_service.get_stats()
        return {"success": True, "stats": stats}
    except Exception as e:
        logger.error("Failed to fetch stream stats", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Analytics Routes
# ---------------------------------------------------------------------------
class PortfolioPositionInput(BaseModel):
    """A portfolio position for analytics"""
    protocol: str
    category: str = "unknown"
    token: str
    value_usd: float
    apy: float | None = None
    cost_basis: float | None = None


class AnalyticsRequest(BaseModel):
    """Request for analytics calculation"""
    positions: List[PortfolioPositionInput]
    timeframe_days: int = 30


@app.post("/analytics/portfolio")
async def analytics_portfolio(
    body: AnalyticsRequest,
    _wallet: str = Depends(require_auth),
):
    """
    Get comprehensive portfolio analytics.

    Returns:
        - Portfolio summary (total value, yield, positions)
        - Allocations by category
        - Concentration risk
    """
    try:
        from app.services.analytics import get_analytics

        analytics = get_analytics()
        positions = [p.model_dump() for p in body.positions]

        metrics = await analytics.calculate_portfolio_metrics(positions)

        return {"success": True, "analytics": metrics}
    except Exception as e:
        logger.error("Failed to calculate portfolio analytics", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/analytics/pnl")
async def analytics_pnl(
    body: AnalyticsRequest,
    _wallet: str = Depends(require_auth),
):
    """
    Get P&L analytics.

    Returns:
        - Unrealized P&L
        - Realized P&L (if historical data available)
        - P&L by token
    """
    try:
        from app.services.analytics import get_analytics

        analytics = get_analytics()
        positions = [p.model_dump() for p in body.positions]

        pnl = await analytics.calculate_pnl(positions)

        return {"success": True, "pnl": pnl}
    except Exception as e:
        logger.error("Failed to calculate P&L", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/analytics/protocols")
async def analytics_protocols(
    body: AnalyticsRequest,
    _wallet: str = Depends(require_auth),
):
    """
    Get protocol-level attribution.

    Returns:
        - Performance by protocol
        - Category breakdown
        - Risk levels
    """
    try:
        from app.services.analytics import get_analytics

        analytics = get_analytics()
        positions = [p.model_dump() for p in body.positions]

        attribution = await analytics.get_protocol_attribution(positions)

        return {
            "success": True,
            "attribution": [
                {
                    "protocol": a.protocol,
                    "category": a.category,
                    "value_usd": a.value_usd,
                    "percentage": a.percentage,
                    "pnl_usd": a.pnl_usd,
                    "apy": a.apy,
                    "risk_level": a.risk_level,
                }
                for a in attribution
            ]
        }
    except Exception as e:
        logger.error("Failed to calculate protocol attribution", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/analytics/performance")
async def analytics_performance(
    body: AnalyticsRequest,
    _wallet: str = Depends(require_auth),
):
    """
    Get performance metrics.

    Returns:
        - Weighted APY
        - Period yield
        - Sharpe ratio (approximation)
        - Max drawdown (approximation)
    """
    try:
        from app.services.analytics import get_analytics

        analytics = get_analytics()
        positions = [p.model_dump() for p in body.positions]

        performance = await analytics.get_performance_metrics(
            positions,
            timeframe_days=body.timeframe_days
        )

        return {"success": True, "performance": performance}
    except Exception as e:
        logger.error("Failed to calculate performance", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/analytics/risk")
async def analytics_risk(
    body: AnalyticsRequest,
    _wallet: str = Depends(require_auth),
):
    """
    Get risk metrics.

    Returns:
        - Diversification score
        - Concentration risk
        - Category risk
        - Overall risk score
    """
    try:
        from app.services.analytics import get_analytics

        analytics = get_analytics()
        positions = [p.model_dump() for p in body.positions]

        risk = await analytics.get_risk_metrics(positions)

        return {"success": True, "risk": risk}
    except Exception as e:
        logger.error("Failed to calculate risk", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/analytics/complete")
async def analytics_complete(
    body: AnalyticsRequest,
    _wallet: str = Depends(require_auth),
):
    """
    Get complete analytics in a single request.

    Returns all analytics:
    - Portfolio summary
    - P&L
    - Protocol attribution
    - Performance
    - Risk metrics
    """
    try:
        from app.services.analytics import get_analytics

        analytics = get_analytics()
        positions = [p.model_dump() for p in body.positions]

        # Run all analytics in parallel
        portfolio = await analytics.calculate_portfolio_metrics(positions)
        pnl = await analytics.calculate_pnl(positions)
        attribution = await analytics.get_protocol_attribution(positions)
        performance = await analytics.get_performance_metrics(positions, body.timeframe_days)
        risk = await analytics.get_risk_metrics(positions)

        return {
            "success": True,
            "portfolio": portfolio,
            "pnl": pnl,
            "attribution": [
                {
                    "protocol": a.protocol,
                    "category": a.category,
                    "value_usd": a.value_usd,
                    "percentage": a.percentage,
                    "pnl_usd": a.pnl_usd,
                    "apy": a.apy,
                    "risk_level": a.risk_level,
                }
                for a in attribution
            ],
            "performance": performance,
            "risk": risk,
        }
    except Exception as e:
        logger.error("Failed to calculate complete analytics", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


import asyncio


# ---------------------------------------------------------------------------
# Session Routes
# ---------------------------------------------------------------------------
@app.get("/sessions")
async def list_sessions(
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
    limit: int = Query(20, ge=1, le=50),
    cursor: str | None = Query(None),
):
    sessions, has_more, next_cursor = await session_svc.list_sessions(
        db, wallet, limit=limit, cursor=cursor
    )
    return {
        "sessions": [
            {
                "id": s["id"],
                "title": s["title"],
                "pinned": s["pinned"],
                "createdAt": s["createdAt"],
                "updatedAt": s["updatedAt"],
            }
            for s in sessions
        ],
        "hasMore": has_more,
        "nextCursor": next_cursor,
    }


@app.post("/sessions")
async def create_session(
    body: CreateSessionRequest,
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    title = body.title.strip() or "New chat"
    session = await session_svc.create_session(db, wallet, wallet, title)
    _audit(db, AuditEvent(
        event_type=AuditEventType.ACTION_EXECUTED,
        severity=AuditEventSeverity.INFO,
        entity_type=AuditEntityType.SESSION,
        entity_id=session["id"],
        wallet_address=wallet,
        session_id=session["id"],
        event_data={"action": "session_created", "title": title},
    ))
    return {
        "session": {
            "id": session["id"],
            "title": session["title"],
            "createdAt": session["createdAt"],
            "updatedAt": session["updatedAt"],
        }
    }


@app.get("/sessions/{session_id}")
async def get_session_route(
    session_id: str,
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    session = await session_svc.get_session(db, wallet, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Not found")
    return {"session": session}


@app.patch("/sessions/{session_id}")
async def update_session_route(
    session_id: str,
    body: UpdateSessionRequest,
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    title = body.title.strip()
    updated = await session_svc.update_title(db, wallet, session_id, title)
    if not updated:
        raise HTTPException(status_code=404, detail="Session not found")
    session = await session_svc.get_session(db, wallet, session_id)
    return {"session": session}


@app.patch("/sessions/{session_id}/pin")
async def pin_session_route(
    session_id: str,
    body: PinSessionRequest,
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    updated = await session_svc.set_pinned(db, wallet, session_id, body.pinned)
    if not updated:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True}


@app.delete("/sessions/{session_id}")
async def delete_session_route(
    session_id: str,
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    deleted = await session_svc.delete_session(db, wallet, session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    _audit(db, AuditEvent(
        event_type=AuditEventType.ACTION_EXECUTED,
        severity=AuditEventSeverity.INFO,
        entity_type=AuditEntityType.SESSION,
        entity_id=session_id,
        wallet_address=wallet,
        session_id=session_id,
        event_data={"action": "session_deleted"},
    ))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Message Routes
# ---------------------------------------------------------------------------
@app.get("/sessions/{session_id}/messages")
async def get_messages(
    session_id: str,
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
    limit: int | None = Query(None, ge=1, le=200),
    offset: int | None = Query(None, ge=0),
):
    # Verify session ownership
    session = await session_svc.get_session(db, wallet, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Not found")

    messages = await message_svc.get_messages(db, wallet, session_id, limit=limit, offset=offset)
    return {"messages": messages}


class PatchMessageMetaRequest(BaseModel):
    action_results: dict | None = None
    query_snapshots: dict | None = None


@app.patch("/sessions/{session_id}/messages/{message_id}/metadata")
async def patch_message_metadata(
    session_id: str,
    message_id: str,
    body: PatchMessageMetaRequest,
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    """Merge patch dict into message metadata (action_results / query_snapshots)."""
    patch: dict = {}
    if body.action_results is not None:
        patch["action_results"] = body.action_results
    if body.query_snapshots is not None:
        patch["query_snapshots"] = body.query_snapshots
    if not patch:
        return {"ok": True}
    ok = await message_svc.update_message_metadata(db, session_id, message_id, wallet, patch)
    if not ok:
        raise HTTPException(status_code=404, detail="Message not found")
    await db.commit()
    return {"ok": True}


@app.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    body: SendMessageRequest,
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    """Send a user message and get a non-streaming LLM reply."""
    content = body.content.strip()
    is_local = session_id.startswith("local:")

    # Auto-create server session for local sessions
    actual_session_id = session_id
    response_extra: dict[str, Any] = {}

    if is_local:
        new_session = await session_svc.create_session(db, wallet, wallet, "New chat")
        actual_session_id = new_session["id"]
        response_extra["sessionId"] = actual_session_id
    else:
        session = await session_svc.get_session(db, wallet, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Not found")

    # Save user message
    await message_svc.create_message(db, actual_session_id, wallet, "user", content)

    # Increment message_count for user message
    new_count = await message_svc._increment_message_count(db, actual_session_id)
    await maybe_create_summary(db, actual_session_id, wallet, new_count)

    # Call LLM (non-streaming) with backend-managed context
    try:
        model_messages = await build_llm_context(db, actual_session_id, wallet)
        llm = LLMService()
        text = await llm.acomplete(model_messages)

        # Save assistant message
        await message_svc.create_message(db, actual_session_id, wallet, "assistant", text or "")
        await message_svc._increment_message_count(db, actual_session_id)

        # Generate title for first message in new session
        if is_local or new_count == 1:
            try:
                title = await generate_title(content)
                await session_svc.update_title(db, wallet, actual_session_id, title)
                response_extra["title"] = title
            except Exception:
                logger.warning("Title generation failed", exc_info=True)

        _audit(db, AuditEvent(
            event_type=AuditEventType.ACTION_EXECUTED,
            severity=AuditEventSeverity.INFO,
            entity_type=AuditEntityType.SESSION,
            entity_id=actual_session_id,
            wallet_address=wallet,
            session_id=actual_session_id,
            event_data={"action": "message_sent", "content_length": len(content)},
        ))
        return {
            "message": text,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            **response_extra,
        }
    except Exception as exc:
        logger.error("LLM error: %s", exc, exc_info=True)
        await message_svc.create_message(
            db, actual_session_id, wallet, "system",
            "I can't respond right now. Please try again later.",
        )
        _audit(db, AuditEvent(
            event_type=AuditEventType.SYSTEM_ERROR,
            severity=AuditEventSeverity.ERROR,
            entity_type=AuditEntityType.SESSION,
            entity_id=actual_session_id,
            wallet_address=wallet,
            session_id=actual_session_id,
            event_data={"action": "message_sent", "error": str(exc)},
        ))
        return {
            "message": "",
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "degraded": True,
            **response_extra,
        }


# ---------------------------------------------------------------------------
# SSE Streaming endpoint
# ---------------------------------------------------------------------------
@app.post("/messages/stream")
async def stream_message(
    body: StreamMessageRequest,
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    """Stream an LLM response as Server-Sent Events."""
    content = body.content.strip()
    session_id = body.session_id
    is_local = session_id.startswith("local:")

    # Convert attachments to dict format for the service
    attachments = [att.model_dump() for att in body.attachments]

    actual_session_id = session_id
    is_first_message = False
    if is_local:
        new_session = await session_svc.create_session(db, wallet, wallet, "New chat")
        actual_session_id = new_session["id"]
        is_first_message = True
        await db.commit()
    else:
        session = await session_svc.get_session(db, wallet, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Not found")
        is_first_message = session.get("messageCount", 0) == 0

    _audit(db, AuditEvent(
        event_type=AuditEventType.ACTION_EXECUTED,
        severity=AuditEventSeverity.INFO,
        entity_type=AuditEntityType.SESSION,
        entity_id=actual_session_id,
        wallet_address=wallet,
        session_id=actual_session_id,
        event_data={"action": "message_sent_stream", "content_length": len(content)},
    ))

    async def event_generator() -> AsyncGenerator[str, None]:
        # If session was aliased, send the new session ID first
        if is_local:
            yield f"data: {json.dumps({'sessionId': actual_session_id})}\n\n"

        # StreamingResponse runs after the handler returns, so the Depends(get_session)
        # session is already committed/closed by then. Open a dedicated session here.
        async with async_session_factory() as stream_db:
            try:
                async for chunk in message_svc.stream_chat_response(
                    stream_db, actual_session_id, wallet, content,
                    is_first_message=is_first_message,
                    attachments=attachments,
                ):
                    yield chunk
                await stream_db.commit()
            except Exception:
                await stream_db.rollback()
                raise

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# User Preferences Routes
# ---------------------------------------------------------------------------

class PreferencesUpdateRequest(BaseModel):
    """Request to update user preferences"""
    theme: str | None = None
    language: str | None = None
    display_name: str | None = None
    timezone: str | None = None
    notifications: dict | None = None
    privacy: dict | None = None
    trading: dict | None = None


@app.get("/preferences")
async def get_preferences(
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    """
    Get user preferences.

    Returns:
        - Theme, language, timezone settings
        - Notification preferences
        - Privacy settings
        - Trading preferences
    """
    try:
        from app.services.user_preferences import get_preferences_service

        service = get_preferences_service(db)
        preferences = await service.get_preferences(wallet)

        if not preferences:
            # Return defaults
            from app.services.user_preferences import get_default_preferences
            from app.services.session import get_or_create_user_id

            user_id = await get_or_create_user_id(db, wallet)
            preferences = get_default_preferences(user_id, wallet)

        return {"success": True, "preferences": preferences.to_dict()}
    except Exception as e:
        logger.error("Failed to fetch preferences", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/preferences")
async def update_preferences(
    body: PreferencesUpdateRequest,
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    """
    Update user preferences.

    Supports partial updates - only provided fields will be updated.
    """
    try:
        from app.services.user_preferences import get_preferences_service, get_default_preferences
        from app.services.session import get_or_create_user_id

        service = get_preferences_service(db)

        # Check if preferences exist
        existing = await service.get_preferences(wallet, use_cache=False)

        if not existing:
            # Create new preferences
            user_id = await get_or_create_user_id(db, wallet)
            existing = get_default_preferences(user_id, wallet)

        # Apply updates
        updates = body.model_dump(exclude_none=True)
        if updates:
            preferences = await service.update_preferences(wallet, updates)
        else:
            preferences = existing

        _audit(db, AuditEvent(
            event_type=AuditEventType.PREFERENCES_UPDATED,
            severity=AuditEventSeverity.INFO,
            entity_type=AuditEntityType.PREFERENCES,
            entity_id=wallet,
            wallet_address=wallet,
            event_data={"updated_fields": list(updates.keys())},
        ))
        return {"success": True, "preferences": preferences.to_dict()}
    except Exception as e:
        logger.error("Failed to update preferences", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.delete("/preferences")
async def delete_preferences(
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    """
    Reset user preferences to defaults.
    """
    try:
        from app.services.user_preferences import get_preferences_service, get_default_preferences
        from app.services.session import get_or_create_user_id

        service = get_preferences_service(db)

        # Get user ID before deleting
        user_id_result = await db.execute(
            sa_text("SELECT id FROM auth_schema.users WHERE wallet_address = :wallet"),
            {"wallet": wallet}
        )
        row = user_id_result.fetchone()
        user_id = row[0] if row else str(uuid.uuid4())

        # Create default preferences
        defaults = get_default_preferences(user_id, wallet)
        await service.save_preferences(defaults)

        return {"success": True, "message": "Preferences reset to defaults"}
    except Exception as e:
        logger.error("Failed to reset preferences", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/preferences/channels")
async def get_notification_channels(
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    """
    Get enabled notification channels for the user.
    """
    try:
        from app.services.user_preferences import get_preferences_service

        service = get_preferences_service(db)
        channels = await service.get_notification_channels(wallet)

        return {"success": True, "channels": channels}
    except Exception as e:
        logger.error("Failed to fetch notification channels", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/preferences/test-notification")
async def test_notification(
    channel: str = Query(..., description="Channel to test (email, telegram, push, webhook)"),
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    """
    Send a test notification to verify channel configuration.
    """
    try:
        from app.services.user_preferences import get_preferences_service

        service = get_preferences_service(db)
        preferences = await service.get_preferences(wallet)

        if not preferences:
            raise HTTPException(status_code=404, detail="Preferences not found")

        # Check if the channel is enabled
        channels = await service.get_notification_channels(wallet)
        if channel not in channels:
            raise HTTPException(
                status_code=400,
                detail=f"Channel '{channel}' is not enabled"
            )

        # Send test notification (placeholder - would integrate with notification service)
        logger.info("Test notification requested", extra={"wallet": wallet, "channel": channel})

        return {
            "success": True,
            "message": f"Test notification sent via {channel}"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to send test notification", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Audit Trail Routes
# ---------------------------------------------------------------------------

class AuditQueryRequest(BaseModel):
    """Query parameters for audit events"""
    user_id: str | None = None
    wallet_address: str | None = None
    event_type: str | None = None
    severity: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    request_path: str | None = None
    limit: int = 100
    offset: int = 0


@app.get("/audit/events")
async def get_audit_events(
    wallet: str = Depends(require_auth),
    event_type: str | None = Query(None, description="Filter by event type"),
    severity: str | None = Query(None, description="Filter by severity"),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
):
    """
    Get audit events for the authenticated user.
    """
    try:
        from app.services.audit_trail import get_audit_service, AuditQuery, AuditEventType, AuditEventSeverity

        service = get_audit_service(db)

        query = AuditQuery(
            wallet_address=wallet,
            event_type=AuditEventType(event_type) if event_type else None,
            severity=AuditEventSeverity(severity) if severity else None,
            limit=limit,
            offset=offset,
        )

        events = await service.query_events(query)

        return {
            "success": True,
            "events": [e.to_dict() for e in events],
            "count": len(events),
        }
    except Exception as e:
        logger.error("Failed to fetch audit events", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/audit/activity")
async def get_user_activity(
    wallet: str = Depends(require_auth),
    limit: int = Query(20, le=100),
):
    """
    Get recent user activity.
    """
    try:
        from app.services.audit_trail import get_audit_service

        service = get_audit_service(db)
        events = await service.get_user_activity(wallet, limit)

        return {
            "success": True,
            "activity": [e.to_dict() for e in events],
            "count": len(events),
        }
    except Exception as e:
        logger.error("Failed to fetch user activity", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/audit/transactions")
async def get_transaction_history(
    wallet: str = Depends(require_auth),
    start_date: str | None = Query(None, description="Start date (ISO format)"),
    end_date: str | None = Query(None, description="End date (ISO format)"),
):
    """
    Get transaction history from audit trail.
    """
    try:
        from app.services.audit_trail import get_audit_service

        service = get_audit_service(db)

        start = datetime.fromisoformat(start_date) if start_date else None
        end = datetime.fromisoformat(end_date) if end_date else None

        events = await service.get_transaction_history(wallet, start, end)

        return {
            "success": True,
            "transactions": [e.to_dict() for e in events],
            "count": len(events),
        }
    except Exception as e:
        logger.error("Failed to fetch transaction history", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/audit/security")
async def get_security_events(
    wallet: str = Depends(require_auth),
    hours: int = Query(24, le=168, description="Hours to look back"),
):
    """
    Get recent security events.
    """
    try:
        from app.services.audit_trail import get_audit_service

        service = get_audit_service(db)
        events = await service.get_security_events(wallet, hours)

        return {
            "success": True,
            "security_events": [e.to_dict() for e in events],
            "count": len(events),
        }
    except Exception as e:
        logger.error("Failed to fetch security events", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/audit/statistics")
async def get_audit_statistics(
    wallet: str = Depends(require_auth),
    days: int = Query(30, le=365),
):
    """
    Get audit statistics for the user.
    """
    try:
        from app.services.audit_trail import get_audit_service

        service = get_audit_service(db)
        stats = await service.get_statistics(wallet, days)

        return {
            "success": True,
            "statistics": stats,
        }
    except Exception as e:
        logger.error("Failed to fetch audit statistics", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/audit/export")
async def export_audit_events(
    body: AuditQueryRequest,
    wallet: str = Depends(require_auth),
    format: str = Query("json", regex="^(json|csv)$"),
):
    """
    Export audit events in specified format.
    """
    try:
        from app.services.audit_trail import get_audit_service, AuditQuery, AuditEventType, AuditEventSeverity

        service = get_audit_service(db)

        query = AuditQuery(
            user_id=body.user_id,
            wallet_address=wallet,  # always use authenticated wallet — ignore any body.wallet_address override
            event_type=AuditEventType(body.event_type) if body.event_type else None,
            severity=AuditEventSeverity(body.severity) if body.severity else None,
            entity_type=body.entity_type,
            entity_id=body.entity_id,
            start_date=datetime.fromisoformat(body.start_date) if body.start_date else None,
            end_date=datetime.fromisoformat(body.end_date) if body.end_date else None,
            request_path=body.request_path,
            limit=body.limit,
            offset=body.offset,
        )

        exported = await service.export_events(query, format)

        return {
            "success": True,
            "format": format,
            "data": exported,
        }
    except Exception as e:
        logger.error("Failed to export audit events", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# MEV Protection Routes
# ---------------------------------------------------------------------------

class PriorityFeeRequest(BaseModel):
    """Request for priority fee estimation"""
    priority: str = "medium"  # low, medium, high, urgent
    use_jito: bool = True


class ProtectionRequest(BaseModel):
    """Request for MEV protection"""
    level: str = "medium"  # none, low, medium, high, maximum
    strategy: str = "jito_tip"  # standard, jito_tip, jito_bundle
    max_slippage_bps: int = 300
    check_sandwich: bool = True
    check_price_impact: bool = True
    estimated_price_impact_bps: int = 0


class FeeEstimateRequest(BaseModel):
    """Request for fee estimation"""
    priority: str = "medium"
    use_jito: bool = True
    compute_units: int = 200000


@app.get("/mev/priority-fee")
async def get_priority_fee(
    priority: str = Query("medium", regex="^(low|medium|high|urgent)$"),
    use_jito: bool = Query(True),
    _wallet: str = Depends(require_auth),
):
    """
    Get priority fee estimate in micro-lamports.
    """
    try:
        from app.services.mev_protection import (
            get_mev_service,
            TransactionPriority,
        )

        service = get_mev_service()
        priority_enum = TransactionPriority(priority)
        fee = await service.get_priority_fee_estimate(priority_enum, use_jito)

        return {
            "success": True,
            "priority_fee_micro_lamports": fee,
            "priority": priority,
            "use_jito": use_jito,
        }
    except Exception as e:
        logger.error("Failed to get priority fee", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/mev/jito-tip")
async def get_jito_tip(
    priority: str = Query("medium", regex="^(low|medium|high|urgent)$"),
    _wallet: str = Depends(require_auth),
):
    """
    Get Jito tip estimate.
    """
    try:
        from app.services.mev_protection import (
            get_mev_service,
            TransactionPriority,
        )

        service = get_mev_service()
        tip = await service.get_jito_tip_estimate(TransactionPriority(priority))

        return {
            "success": True,
            "jito_tip_micro_lamports": tip,
            "priority": priority,
        }
    except Exception as e:
        logger.error("Failed to get Jito tip", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/mev/estimate-fees")
async def estimate_fees(
    body: FeeEstimateRequest,
    _wallet: str = Depends(require_auth),
):
    """
    Estimate total fees for a transaction.
    """
    try:
        from app.services.mev_protection import (
            get_mev_service,
            PriorityFeeConfig,
            TransactionPriority,
        )

        service = get_mev_service()
        config = PriorityFeeConfig(
            priority=TransactionPriority(body.priority),
            use_jito=body.use_jito,
        )

        fees = await service.estimate_fees(config)

        return {
            "success": True,
            "fees": fees.to_dict(),
        }
    except Exception as e:
        logger.error("Failed to estimate fees", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/mev/protection")
async def apply_mev_protection(
    body: ProtectionRequest,
    _wallet: str = Depends(require_auth),
):
    """
    Apply MEV protection to a transaction.
    """
    try:
        from app.services.mev_protection import (
            get_mev_service,
            MEVProtectionConfig,
            ProtectionLevel,
            ProtectionStrategy,
        )

        service = get_mev_service()
        config = MEVProtectionConfig(
            level=ProtectionLevel(body.level),
            strategy=ProtectionStrategy(body.strategy),
            max_slippage_bps=body.max_slippage_bps,
            check_sandwich=body.check_sandwich,
            check_price_impact=body.check_price_impact,
        )

        result = await service.apply_protection(config, body.estimated_price_impact_bps)

        return {
            "success": True,
            "protection": result.to_dict(),
        }
    except Exception as e:
        logger.error("Failed to apply MEV protection", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/mev/config/default")
async def get_default_mev_config(
    risk_tolerance: str = Query("moderate", regex="^(conservative|moderate|aggressive)$"),
    _wallet: str = Depends(require_auth),
):
    """
    Get default MEV protection config based on user risk tolerance.
    """
    try:
        from app.services.mev_protection import get_default_protection_config

        config = get_default_protection_config(risk_tolerance)

        return {
            "success": True,
            "config": config.to_dict(),
            "risk_tolerance": risk_tolerance,
        }
    except Exception as e:
        logger.error("Failed to get default config", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/mev/bundle-params")
async def get_bundle_params(
    transaction_count: int = Query(1, ge=1, le=5),
    priority: str = Query("medium", regex="^(low|medium|high|urgent)$"),
    _wallet: str = Depends(require_auth),
):
    """
    Get Jito bundle parameters.
    """
    try:
        from app.services.mev_protection import (
            get_mev_service,
            TransactionPriority,
        )

        service = get_mev_service()
        tip = await service.get_jito_tip_estimate(TransactionPriority(priority))

        # Return bundle params template
        params = {
            "tip_micro_lamports": tip,
            "timeout_ms": 10000,
            "transactions": [f"<base64_transaction_{i+1}>" for i in range(transaction_count)],
        }

        return {
            "success": True,
            "bundle_params": params,
            "note": "Replace placeholder transactions with actual base64-encoded transactions",
        }
    except Exception as e:
        logger.error("Failed to get bundle params", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Address Validation Routes
# ---------------------------------------------------------------------------

class AddressValidationRequest(BaseModel):
    """Request for address validation"""
    address: str
    check_token: bool = True
    check_security: bool = True


class BatchValidationRequest(BaseModel):
    """Request for batch address validation"""
    addresses: List[str]
    check_token: bool = True
    check_security: bool = True


@app.post("/address/validate")
async def validate_address(
    body: AddressValidationRequest,
    _wallet: str = Depends(require_auth),
):
    """
    Validate a Solana address.

    Checks:
    - Address format validation
    - Address type detection (wallet, token, program)
    - Token details (if applicable)
    - Security analysis
    """
    try:
        from app.services.address_validation import get_address_service

        service = get_address_service()
        result = await service.validate_address(
            body.address,
            check_token=body.check_token,
            check_security=body.check_security,
        )

        return {
            "success": True,
            "validation": result.to_dict(),
        }
    except Exception as e:
        logger.error("Failed to validate address", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/address/validate/batch")
async def batch_validate_addresses(
    body: BatchValidationRequest,
    _wallet: str = Depends(require_auth),
):
    """
    Validate multiple addresses in batch.
    """
    try:
        from app.services.address_validation import get_address_service

        service = get_address_service()
        results = await service.batch_validate(
            body.addresses,
            check_token=body.check_token,
            check_security=body.check_security,
        )

        return {
            "success": True,
            "validations": [r.to_dict() for r in results],
            "count": len(results),
        }
    except Exception as e:
        logger.error("Failed to batch validate addresses", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/address/security/{address}")
async def analyze_address_security(
    address: str,
    _wallet: str = Depends(require_auth),
):
    """
    Analyze security of an address.

    Returns:
    - Risk assessment
    - Known malicious checks
    - Token security analysis
    - Recommendations
    """
    try:
        from app.services.address_validation import get_address_service

        service = get_address_service()
        analysis = await service.analyze_security(address)

        return {
            "success": True,
            "security": analysis.to_dict(),
        }
    except Exception as e:
        logger.error("Failed to analyze address security", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/address/type/{address}")
async def detect_address_type(
    address: str,
    _wallet: str = Depends(require_auth),
):
    """
    Detect the type of a Solana address.
    """
    try:
        from app.services.address_validation import get_address_service, AddressType

        service = get_address_service()
        validation = service.validate_address_format(address)

        return {
            "success": True,
            "address": address,
            "type": validation.address_type.value,
            "is_valid": validation.is_valid_format,
            "is_wallet": validation.is_wallet_address,
            "is_token": validation.is_token_address,
            "is_program": validation.is_program_address,
        }
    except Exception as e:
        logger.error("Failed to detect address type", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Whale Tracking Routes
# ---------------------------------------------------------------------------

class AddWhaleRequest(BaseModel):
    """Request to add a whale to track"""
    address: str
    name: str
    whale_type: str = "unknown"  # exchange, fund, market_maker, defi_protocol, large_holder
    tags: List[str] = []


class AddSmartMoneyRequest(BaseModel):
    """Request to add smart money to track"""
    address: str
    name: str
    trading_style: str = "unknown"  # swing, scalp, trend, value


class CreateAlertRuleRequest(BaseModel):
    """Request to create an alert rule"""
    name: str
    condition: Dict[str, Any]
    priority: str = "medium"  # low, medium, high, urgent
    channels: List[str] = ["in_app"]


@app.get("/whale/tracked")
async def get_tracked_whales(
    whale_type: str | None = Query(None, description="Filter by whale type"),
    active_only: bool = Query(True),
    _wallet: str = Depends(require_auth),
):
    """
    Get list of tracked whale wallets.
    """
    try:
        from app.services.whale_tracking import get_whale_service, WhaleType

        service = get_whale_service()
        wtype = WhaleType(whale_type) if whale_type else None
        whales = await service.get_whales(wtype, active_only)

        return {
            "success": True,
            "whales": [w.to_dict() for w in whales],
            "count": len(whales),
        }
    except Exception as e:
        logger.error("Failed to get tracked whales", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/whale/track")
async def add_whale(
    body: AddWhaleRequest,
    wallet: str = Depends(require_auth),
):
    """
    Add a new whale to track.
    """
    try:
        from app.services.whale_tracking import get_whale_service, WhaleType

        service = get_whale_service()
        whale = await service.add_whale(
            address=body.address,
            name=body.name,
            whale_type=WhaleType(body.whale_type),
            tags=body.tags,
        )

        return {
            "success": True,
            "whale": whale.to_dict(),
        }
    except Exception as e:
        logger.error("Failed to add whale", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.delete("/whale/untrack/{address}")
async def remove_whale(
    address: str,
    wallet: str = Depends(require_auth),
):
    """
    Remove a whale from tracking.
    """
    try:
        from app.services.whale_tracking import get_whale_service

        service = get_whale_service()
        success = await service.remove_whale(address)

        return {
            "success": success,
            "message": "Whale removed" if success else "Whale not found",
        }
    except Exception as e:
        logger.error("Failed to remove whale", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/whale/activity/{address}")
async def get_whale_activity(
    address: str,
    limit: int = Query(20, le=100),
    _wallet: str = Depends(require_auth),
):
    """
    Get recent activity for a whale address.
    """
    try:
        from app.services.whale_tracking import get_whale_service

        service = get_whale_service()
        activity = await service.get_whale_activity(address, limit)

        return {
            "success": True,
            "whale_address": address,
            "activity": activity,
            "count": len(activity),
        }
    except Exception as e:
        logger.error("Failed to get whale activity", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/whale/smart-money")
async def get_smart_money(
    style: str | None = Query(None, description="Filter by trading style"),
    _wallet: str = Depends(require_auth),
):
    """
    Get list of smart money wallets.
    """
    try:
        from app.services.whale_tracking import get_whale_service

        service = get_whale_service()
        smart_money = await service.get_smart_money(style)

        return {
            "success": True,
            "wallets": [w.to_dict() for w in smart_money],
            "count": len(smart_money),
        }
    except Exception as e:
        logger.error("Failed to get smart money", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/whale/smart-money/add")
async def add_smart_money(
    body: AddSmartMoneyRequest,
    wallet: str = Depends(require_auth),
):
    """
    Add a smart money wallet to track.
    """
    try:
        from app.services.whale_tracking import get_whale_service

        service = get_whale_service()
        smart_money = await service.add_smart_money(
            address=body.address,
            name=body.name,
            trading_style=body.trading_style,
        )

        return {
            "success": True,
            "wallet": smart_money.to_dict(),
        }
    except Exception as e:
        logger.error("Failed to add smart money", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/whale/anomalies")
async def get_volume_anomalies(
    min_ratio: float = Query(3.0, ge=1.0, description="Minimum anomaly ratio"),
    limit: int = Query(10, le=50),
    _wallet: str = Depends(require_auth),
):
    """
    Get tokens with volume anomalies.
    """
    try:
        from app.services.whale_tracking import get_whale_service

        service = get_whale_service()
        anomalies = await service.get_volume_anomalies(min_ratio, limit)

        return {
            "success": True,
            "anomalies": [a.to_dict() for a in anomalies],
            "count": len(anomalies),
        }
    except Exception as e:
        logger.error("Failed to get anomalies", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/whale/alerts")
async def get_whale_alerts(
    wallet: str = Depends(require_auth),
    limit: int = Query(20, le=100),
    unread_only: bool = Query(False),
):
    """
    Get whale alerts for the user.
    """
    try:
        from app.services.whale_tracking import get_whale_service

        service = get_whale_service()
        alerts = await service.get_alerts(wallet, limit, unread_only)

        return {
            "success": True,
            "alerts": [a.to_dict() for a in alerts],
            "count": len(alerts),
        }
    except Exception as e:
        logger.error("Failed to get alerts", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/whale/rules")
async def create_alert_rule(
    body: CreateAlertRuleRequest,
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    """
    Create a custom alert rule.
    """
    try:
        from app.services.whale_tracking import get_whale_service, AlertPriority
        from app.services.session import get_or_create_user_id

        service = get_whale_service()
        user_id = await get_or_create_user_id(db, wallet)

        rule = await service.create_alert_rule(
            user_id=user_id,
            name=body.name,
            condition=body.condition,
            priority=AlertPriority(body.priority),
            channels=body.channels,
        )

        return {
            "success": True,
            "rule": rule.to_dict(),
        }
    except Exception as e:
        logger.error("Failed to create alert rule", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/whale/rules")
async def get_alert_rules(
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    """
    Get alert rules for the user.
    """
    try:
        from app.services.whale_tracking import get_whale_service
        from app.services.session import get_or_create_user_id

        service = get_whale_service()
        user_id = await get_or_create_user_id(db, wallet)

        rules = await service.get_alert_rules(user_id)

        return {
            "success": True,
            "rules": [r.to_dict() for r in rules],
            "count": len(rules),
        }
    except Exception as e:
        logger.error("Failed to get alert rules", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.delete("/whale/rules/{rule_id}")
async def delete_alert_rule(
    rule_id: str,
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    """
    Delete an alert rule.
    """
    try:
        from app.services.whale_tracking import get_whale_service
        from app.services.session import get_or_create_user_id

        service = get_whale_service()
        user_id = await get_or_create_user_id(db, wallet)

        success = await service.delete_alert_rule(rule_id, user_id)

        return {
            "success": success,
            "message": "Rule deleted" if success else "Rule not found",
        }
    except Exception as e:
        logger.error("Failed to delete alert rule", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/whale/statistics")
async def get_whale_statistics(_wallet: str = Depends(require_auth)):
    """
    Get whale tracking statistics.
    """
    try:
        from app.services.whale_tracking import get_whale_service

        service = get_whale_service()
        stats = await service.get_statistics()

        return {
            "success": True,
            "statistics": stats,
        }
    except Exception as e:
        logger.error("Failed to get statistics", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Tax Report Routes
# ---------------------------------------------------------------------------

class TaxReportRequest(BaseModel):
    """Request for tax report generation"""
    year: int
    cost_basis_method: str = "fifo"  # fifo, lifo, hifo, average
    include_staking: bool = True
    include_farming: bool = True
    include_nfts: bool = True
    include_airdrops: bool = True
    short_term_days: int = 365
    short_term_rate: float = 35.0
    long_term_rate: float = 15.0


@app.post("/tax/report")
async def generate_tax_report(
    body: TaxReportRequest,
    wallet: str = Depends(require_auth),
):
    """
    Generate a tax report for the given year.
    """
    try:
        from app.services.tax_report import (
            get_tax_service,
            TaxReportConfig,
            CostBasisMethod,
        )

        service = get_tax_service()

        config = TaxReportConfig(
            wallet_address=wallet,
            year=body.year,
            cost_basis_method=CostBasisMethod(body.cost_basis_method),
            include_staking=body.include_staking,
            include_farming=body.include_farming,
            include_nfts=body.include_nfts,
            include_airdrops=body.include_airdrops,
            short_term_days=body.short_term_days,
            short_term_rate=body.short_term_rate,
            long_term_rate=body.long_term_rate,
        )

        summary = await service.generate_tax_report(config)

        return {
            "success": True,
            "report": summary.to_dict(),
        }
    except Exception as e:
        logger.error("Failed to generate tax report", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/tax/export")
async def export_tax_report(
    body: TaxReportRequest,
    wallet: str = Depends(require_auth),
    format: str = Query("json", regex="^(json|csv)$"),
):
    """
    Export tax report in specified format.
    """
    try:
        from app.services.tax_report import (
            get_tax_service,
            TaxReportConfig,
            CostBasisMethod,
        )

        service = get_tax_service()

        config = TaxReportConfig(
            wallet_address=wallet,
            year=body.year,
            cost_basis_method=CostBasisMethod(body.cost_basis_method),
        )

        exported = await service.export_report(config, format)

        return {
            "success": True,
            "format": format,
            "year": body.year,
            "data": exported,
        }
    except Exception as e:
        logger.error("Failed to export tax report", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/tax/events/{year}")
async def get_taxable_events(
    year: int,
    wallet: str = Depends(require_auth),
    token: str | None = Query(None),
):
    """
    Get detailed taxable events for a year.
    """
    try:
        from app.services.tax_report import get_tax_service

        service = get_tax_service()
        events = await service.get_taxable_events(wallet, year, token)

        return {
            "success": True,
            "year": year,
            "events": [e.to_dict() for e in events],
            "count": len(events),
        }
    except Exception as e:
        logger.error("Failed to get taxable events", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/tax/years")
async def get_available_years(
    wallet: str = Depends(require_auth),
):
    """
    Get available tax years for the wallet.
    """
    # In production, this would query the database
    current_year = datetime.now(timezone.utc).year
    years = list(range(2020, current_year + 1))

    return {
        "success": True,
        "years": years,
    }


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def main() -> None:
    """Run the service with uvicorn."""
    import uvicorn

    # Logging is already configured at module import time; this is a no-op guard.
    logger.info("Starting chat-service-py", port=settings.PORT)
    uvicorn.run(
        "app.main:app",
        host=settings.BIND_HOST,
        port=settings.PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
