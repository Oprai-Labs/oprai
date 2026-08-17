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

import httpx
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from typing import Any, Dict, List, Optional

# Configure structured logging before any logger is created.
from app.logging_config import configure_logging
configure_logging(level=os.environ.get("LOG_LEVEL", "INFO"), fmt=os.environ.get("LOG_FORMAT", "json"))

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
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
from sqlalchemy import select, text as sa_text, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.connection import get_session, init_db, close_db, async_session_factory
from app.middleware.request_audit import RequestAuditMiddleware
from app.services.cache import get_cache_service, close_cache_service
from app.grpc_server import start_grpc_server
from app.middleware.auth import require_auth, require_internal
from app.services import session as session_svc
from app.services import message as message_svc
from app.services import share as share_svc
from app.services import issue_report as issue_svc
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
    protocols: list[str] | None = Field(default=None)

    model_config = {"populate_by_name": True}


class EditMessageRequest(BaseModel):
    """Edit-and-resend payload. The frontend sends `messageId` of the user
    message being edited, the new `content`, and optionally a fresh set of
    `protocols`. The backend supersedes that message and every message after
    it, then streams a brand-new assistant turn over SSE."""
    session_id: str = Field(..., alias="sessionId")
    message_id: str = Field(..., alias="messageId")
    content: str = Field(..., min_length=1, max_length=2000)
    protocols: list[str] | None = Field(default=None)

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

    # Warm the RAG index. Fires-and-forgets so a slow Qdrant cold-start
    # doesn't block service readiness, but the warmup query is in flight
    # by the time the first user request arrives — the typical case is
    # the index is hot in RAM and the user pays no first-query penalty.
    if settings.KNOWLEDGE_RAG_ENABLED:
        from app.rag import get_rag_service
        asyncio.create_task(get_rag_service().warmup())

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
# Product-analytics events (funnel / feature adoption / activation)
# ---------------------------------------------------------------------------
_EVENT_TYPES = {"app_open", "funnel", "feature_used", "error_shown", "page_view", "action"}


class EventIn(BaseModel):
    event_type: str = Field(..., max_length=48)
    event_name: str = Field(..., max_length=80)
    session_id: Optional[str] = None
    properties: Optional[dict] = None


@app.post("/events")
async def ingest_event(
    evt: EventIn,
    x_user_wallet: str = Header(..., alias="X-User-Wallet"),
):
    """Record one product-analytics event (meta only — never message content/PII).

    Wallet comes from the gateway-injected header, so events are always attributed
    to the authenticated user and a client cannot spoof another wallet. Writes on
    its own session; analytics must never surface as a user-facing error.
    """
    if evt.event_type not in _EVENT_TYPES:
        raise HTTPException(status_code=422, detail="invalid event_type")

    props = evt.properties if isinstance(evt.properties, dict) else {}
    blob = json.dumps(props)
    if len(blob) > 4000:  # cap: events are meta, not payloads
        blob = json.dumps({"_truncated": True})

    sid: Optional[str] = None
    if evt.session_id:
        try:
            sid = str(uuid.UUID(str(evt.session_id)))
        except Exception:
            sid = None

    try:
        async with async_session_factory() as s:
            await s.execute(
                sa_text(
                    """INSERT INTO analytics_schema.events
                         (wallet, session_id, event_type, event_name, properties)
                       VALUES (:w, :sid, :t, :n, CAST(:p AS jsonb))"""
                ),
                {"w": x_user_wallet, "sid": sid, "t": evt.event_type,
                 "n": evt.event_name[:80], "p": blob},
            )
            await s.commit()
    except Exception:
        logger.debug("event ingest failed", exc_info=True)

    return {"ok": True}


# ---------------------------------------------------------------------------
# Rewards: tier + points + referral (FAZ-3). Read-only aggregates from the
# admin_schema views (which run as their owner, so no raw cross-schema access);
# referral attribution is an explicit "redeem code" action, never a signup hook.
# ---------------------------------------------------------------------------
def _gen_referral_code() -> str:
    import secrets
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no ambiguous chars
    return "".join(secrets.choice(alphabet) for _ in range(8))


@app.get("/me/rewards")
async def get_rewards(x_user_wallet: str = Header(..., alias="X-User-Wallet")):
    """The caller's tier, points and referral code (generated on first view)."""
    w = x_user_wallet
    async with async_session_factory() as s:
        tier_row = (await s.execute(sa_text(
            "SELECT tier, volume_usd FROM admin_schema.v_user_tier WHERE wallet=:w"), {"w": w})).first()
        pts_row = (await s.execute(sa_text(
            "SELECT own_points, referral_points, total_points FROM admin_schema.v_user_points WHERE wallet=:w"), {"w": w})).first()

        code_row = (await s.execute(sa_text(
            "SELECT code FROM analytics_schema.referral_codes WHERE wallet=:w"), {"w": w})).first()
        code = code_row[0] if code_row else None
        if code is None:
            for _ in range(5):
                cand = _gen_referral_code()
                row = (await s.execute(sa_text(
                    "INSERT INTO analytics_schema.referral_codes (wallet, code) VALUES (:w, :c) "
                    "ON CONFLICT (wallet) DO NOTHING RETURNING code"), {"w": w, "c": cand})).first()
                if row:
                    code = row[0]; break
                existing = (await s.execute(sa_text(
                    "SELECT code FROM analytics_schema.referral_codes WHERE wallet=:w"), {"w": w})).first()
                if existing:
                    code = existing[0]; break
            await s.commit()
        ref_count = (await s.execute(sa_text(
            "SELECT count(*) FROM analytics_schema.referrals WHERE referrer_wallet=:w"), {"w": w})).scalar() or 0

        cb_row = (await s.execute(sa_text(
            "SELECT cashback_earned_usd, cashback_claimed_usd, cashback_claimable_usd "
            "FROM admin_schema.v_user_cashback WHERE wallet=:w"), {"w": w})).first()

    return {
        "tier": int(tier_row[0]) if tier_row and tier_row[0] else 1,
        "volumeUsd": float(tier_row[1]) if tier_row and tier_row[1] is not None else 0.0,
        "points": {
            "own": int(pts_row[0]) if pts_row else 0,
            "referral": int(pts_row[1]) if pts_row else 0,
            "total": int(pts_row[2]) if pts_row else 0,
        },
        "cashback": {
            "earnedUsd": float(cb_row[0]) if cb_row and cb_row[0] is not None else 0.0,
            "claimedUsd": float(cb_row[1]) if cb_row and cb_row[1] is not None else 0.0,
            "claimableUsd": float(cb_row[2]) if cb_row and cb_row[2] is not None else 0.0,
        },
        "referralCode": code,
        "referralCount": int(ref_count),
    }


class RedeemBody(BaseModel):
    code: str


@app.post("/referral/redeem")
async def redeem_referral(body: RedeemBody, x_user_wallet: str = Header(..., alias="X-User-Wallet")):
    """Link the caller to a referrer via their code. One referrer per referee; no self-referral."""
    referee = x_user_wallet
    code = (body.code or "").strip().upper()
    if not code:
        raise HTTPException(status_code=422, detail="code required")
    async with async_session_factory() as s:
        ref = (await s.execute(sa_text(
            "SELECT wallet FROM analytics_schema.referral_codes WHERE code=:c"), {"c": code})).first()
        if not ref:
            raise HTTPException(status_code=404, detail="invalid code")
        referrer = ref[0]
        if referrer == referee:
            raise HTTPException(status_code=400, detail="cannot refer yourself")
        created = (await s.execute(sa_text(
            "INSERT INTO analytics_schema.referrals (referee_wallet, referrer_wallet, code) "
            "VALUES (:re, :rr, :c) ON CONFLICT (referee_wallet) DO NOTHING RETURNING referee_wallet"),
            {"re": referee, "rr": referrer, "c": code})).first() is not None
        await s.commit()
    return {"ok": True, "linked": created}


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
# Portfolio Analytics — per-token cost basis & all-time PnL
# ---------------------------------------------------------------------------
# These routes feed the "PnL (all time)" column on the portfolio page.
# `read_costbasis` is the hot path — every page load hits it. `refresh` is
# the cold path — frontend fires it after the initial render (debounced
# 5 minutes per wallet) so subsequent loads see fresh data without
# blocking first paint.

from app.services.portfolio_analytics import (  # noqa: E402
    sync_wallet_costbasis,
    read_costbasis,
)


# Per-wallet debounce so back-to-back refresh calls don't fan out
# duplicate Helius pages. Lives in-memory because the work is cheap to
# re-run if a replica restarts (worst case: one extra sync), and a
# distributed lock would be overkill for a non-critical background job.
_refresh_debounce: dict[str, float] = {}
_REFRESH_DEBOUNCE_SECONDS = 300.0


@app.get("/portfolio/pnl/{wallet}")
async def portfolio_pnl(
    wallet: str,
    auth_wallet: str = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
):
    """Per-mint cost basis for the connected wallet.

    Returns one row per mint with `totalBoughtAmount`, `totalBoughtUsd`,
    `totalSoldAmount`, `totalSoldUsd`. The frontend joins this against the
    current balance + current price to compute realized + unrealized PnL.

    The endpoint enforces caller == subject — a user can only read their
    own cost basis. This is a privacy concern more than a security one
    (PnL leakage), but cheap to enforce.
    """
    if wallet != auth_wallet:
        raise HTTPException(status_code=403, detail="Can only read own cost basis")
    rows = await read_costbasis(session, wallet)
    return {"wallet": wallet, "positions": rows}


@app.post("/portfolio/refresh/{wallet}")
async def portfolio_refresh(
    wallet: str,
    auth_wallet: str = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
):
    """Trigger an incremental cost-basis sync from Helius.

    Debounced 5 minutes per wallet — repeated calls inside the window
    return the cached "already running / recently ran" status without
    re-fanning out Helius pages. The frontend can safely fire-and-forget
    this on every portfolio load.
    """
    if wallet != auth_wallet:
        raise HTTPException(status_code=403, detail="Can only refresh own cost basis")
    now = asyncio.get_event_loop().time()
    last = _refresh_debounce.get(wallet)
    if last is not None and (now - last) < _REFRESH_DEBOUNCE_SECONDS:
        return {"status": "debounced", "retry_after_seconds": int(_REFRESH_DEBOUNCE_SECONDS - (now - last))}
    _refresh_debounce[wallet] = now
    summary = await sync_wallet_costbasis(session, wallet)
    return {"status": "ok", "summary": summary}


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
# Magic Eden NFT Routes
# ---------------------------------------------------------------------------


def magic_eden_error(e: httpx.HTTPStatusError) -> HTTPException:
    """Turn Magic Eden's refusal into a sentence, and keep the body in the log.

    Every route here used to answer with ``Magic Eden API error: <first 200
    bytes of their response>``. That is two problems in one string: it blames
    an API for what is usually an ordinary outcome (nothing listed, a stale
    token), and it pastes a raw JSON body onto a card. Neither tells the user
    anything they can do.

    The status is the only part that is reliably meaningful across forty
    endpoints, so the message is built from it. The body still exists — in the
    log, where whoever is debugging can read all of it rather than 200 bytes.
    """
    status = e.response.status_code
    logger.warning(
        "Magic Eden refused",
        status=status,
        url=str(e.request.url),
        body=e.response.text[:500],
    )
    if status == 404:
        detail = "Magic Eden has no record of that — it may not be listed, or it may have just moved."
    elif status == 429:
        detail = "Magic Eden is limiting how fast we can ask right now. Try again in a few seconds."
    elif status in (400, 422):
        detail = "Magic Eden turned down those details. Check the item and the amount, then try again."
    elif status in (401, 403):
        detail = "Magic Eden would not allow that request."
    else:
        detail = "Magic Eden is not responding right now. Please try again in a moment."
    return HTTPException(status_code=status, detail=detail)


@app.get("/nft/magic-eden/instructions/withdraw")
async def me_withdraw(
    buyer: str = Query(..., description="Buyer wallet address"),
    amount: float = Query(..., description="Amount in SOL to withdraw from escrow"),
    auctionHouseAddress: str | None = Query(None, description="Auction house address"),
    priorityFee: int | None = Query(None, description="Priority fee in microlamports"),
    _wallet: str = Depends(require_auth),
):
    """Build an unsigned escrow withdraw transaction via Magic Eden."""
    from app.clients.magic_eden import get_withdraw_instruction
    try:
        tx = await get_withdraw_instruction(buyer, amount, auctionHouseAddress, priorityFee)
        return {"success": True, "transaction": tx}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME withdraw failed buyer=%s amount=%s", buyer, amount, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to build Magic Eden withdraw transaction")


@app.get("/nft/magic-eden/instructions/deposit")
async def me_deposit(
    buyer: str = Query(..., description="Buyer wallet address"),
    amount: float = Query(..., description="Amount in SOL to deposit into escrow"),
    auctionHouseAddress: str | None = Query(None, description="Auction house address"),
    priorityFee: int | None = Query(None, description="Priority fee in microlamports"),
    _wallet: str = Depends(require_auth),
):
    """Build an unsigned escrow deposit transaction via Magic Eden."""
    from app.clients.magic_eden import get_deposit_instruction
    try:
        tx = await get_deposit_instruction(buyer, amount, auctionHouseAddress, priorityFee)
        return {"success": True, "transaction": tx}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME deposit failed buyer=%s amount=%s", buyer, amount, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to build Magic Eden deposit transaction")


@app.get("/nft/magic-eden/instructions/sell_cancel")
async def me_sell_cancel(
    seller: str = Query(..., description="Seller wallet address"),
    tokenMint: str = Query(..., description="Token mint address"),
    tokenAccount: str = Query(..., description="Token account holding the NFT"),
    price: float = Query(..., description="Listed price in SOL (must match original listing)"),
    auctionHouseAddress: str | None = Query(None, description="Auction house address"),
    sellerReferral: str | None = Query(None, description="Seller referral wallet"),
    expiry: int | None = Query(None, description="Expiry timestamp in seconds (0 = no expiry)"),
    priorityFee: int | None = Query(None, description="Priority fee in microlamports"),
    _wallet: str = Depends(require_auth),
):
    """Build an unsigned cancel-listing transaction via Magic Eden."""
    from app.clients.magic_eden import get_sell_cancel_instruction
    try:
        tx = await get_sell_cancel_instruction(
            seller, tokenMint, tokenAccount, price,
            auctionHouseAddress, sellerReferral, expiry, priorityFee,
        )
        return {"success": True, "transaction": tx}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME sell_cancel failed seller=%s mint=%s", seller, tokenMint, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to build Magic Eden sell-cancel transaction")


@app.get("/nft/magic-eden/instructions/sell_now")
async def me_sell_now(
    buyer: str = Query(..., description="Buyer wallet address"),
    seller: str = Query(..., description="Seller wallet address"),
    tokenMint: str = Query(..., description="Token mint address"),
    tokenATA: str = Query(..., description="Associate token account"),
    price: float = Query(..., description="Current price in SOL"),
    newPrice: float = Query(..., description="New price in SOL"),
    sellerExpiry: int = Query(..., description="Seller expiry timestamp in seconds (0 = no expiry)"),
    auctionHouseAddress: str | None = Query(None, description="Auction house address"),
    buyerReferral: str | None = Query(None, description="Buyer referral wallet"),
    sellerReferral: str | None = Query(None, description="Seller referral wallet"),
    buyerExpiry: int | None = Query(None, description="Buyer expiry timestamp (0 = no expiry)"),
    priorityFee: int | None = Query(None, description="Priority fee in microlamports"),
    _wallet: str = Depends(require_auth),
):
    """Build an unsigned sell-now (accept offer) transaction via Magic Eden."""
    from app.clients.magic_eden import get_sell_now_instruction
    try:
        tx = await get_sell_now_instruction(
            buyer, seller, tokenMint, tokenATA, price, newPrice, sellerExpiry,
            auctionHouseAddress, buyerReferral, sellerReferral, buyerExpiry, priorityFee,
        )
        return {"success": True, "transaction": tx}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME sell_now failed seller=%s mint=%s", seller, tokenMint, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to build Magic Eden sell-now transaction")


@app.get("/nft/magic-eden/instructions/sell_change_price")
async def me_sell_change_price(
    seller: str = Query(..., description="Seller wallet address"),
    tokenMint: str = Query(..., description="Token mint address"),
    tokenAccount: str = Query(..., description="Token account holding the NFT"),
    price: float = Query(..., description="Current (old) listing price in SOL"),
    newPrice: float = Query(..., description="New listing price in SOL"),
    expiry: int = Query(..., description="Expiry timestamp in seconds (0 = no expiry)"),
    auctionHouseAddress: str | None = Query(None, description="Auction house address"),
    sellerReferral: str | None = Query(None, description="Seller referral wallet"),
    priorityFee: int | None = Query(None, description="Priority fee in microlamports"),
    _wallet: str = Depends(require_auth),
):
    """Build an unsigned sell-change-price transaction via Magic Eden."""
    from app.clients.magic_eden import get_sell_change_price_instruction
    try:
        tx = await get_sell_change_price_instruction(
            seller, tokenMint, tokenAccount, price, newPrice, expiry,
            auctionHouseAddress, sellerReferral, priorityFee,
        )
        return {"success": True, "transaction": tx}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME sell_change_price failed seller=%s mint=%s", seller, tokenMint, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to build Magic Eden sell-change-price transaction")


@app.get("/nft/magic-eden/instructions/sell")
async def me_sell(
    seller: str = Query(..., description="Seller wallet address"),
    tokenMint: str = Query(..., description="Token mint address"),
    tokenAccount: str = Query(..., description="Token account holding the NFT"),
    price: float = Query(..., description="Listing price in SOL"),
    auctionHouseAddress: str | None = Query(None, description="Auction house address"),
    sellerReferral: str | None = Query(None, description="Seller referral wallet"),
    expiry: int | None = Query(None, description="Expiry timestamp in seconds (0 = no expiry)"),
    priorityFee: int | None = Query(None, description="Priority fee in microlamports"),
    _wallet: str = Depends(require_auth),
):
    """Build an unsigned sell/list NFT transaction via Magic Eden."""
    from app.clients.magic_eden import get_sell_instruction
    try:
        tx = await get_sell_instruction(
            seller, tokenMint, tokenAccount, price,
            auctionHouseAddress, sellerReferral, expiry, priorityFee,
        )
        return {"success": True, "transaction": tx}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME sell failed seller=%s mint=%s", seller, tokenMint, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to build Magic Eden sell transaction")


@app.get("/nft/magic-eden/instructions/buy_change_price")
async def me_buy_change_price(
    buyer: str = Query(..., description="Buyer wallet address"),
    tokenMint: str = Query(..., description="Token mint address"),
    price: float = Query(..., description="Current (old) offer price in SOL"),
    newPrice: float = Query(..., description="New offer price in SOL"),
    auctionHouseAddress: str | None = Query(None, description="Auction house address"),
    buyerReferral: str | None = Query(None, description="Buyer referral wallet"),
    expiry: int | None = Query(None, description="Expiry timestamp in seconds (0 = no expiry)"),
    priorityFee: int | None = Query(None, description="Priority fee in microlamports"),
    _wallet: str = Depends(require_auth),
):
    """Build an unsigned change-offer-price transaction via Magic Eden."""
    from app.clients.magic_eden import get_buy_change_price_instruction
    try:
        tx = await get_buy_change_price_instruction(
            buyer, tokenMint, price, newPrice, auctionHouseAddress, buyerReferral, expiry, priorityFee,
        )
        return {"success": True, "transaction": tx}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME buy_change_price failed buyer=%s mint=%s", buyer, tokenMint, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to build Magic Eden change-price transaction")


@app.get("/nft/magic-eden/instructions/buy_cancel")
async def me_buy_cancel(
    buyer: str = Query(..., description="Buyer wallet address"),
    tokenMint: str = Query(..., description="Token mint address"),
    price: float = Query(..., description="Price in SOL (must match original offer)"),
    auctionHouseAddress: str | None = Query(None, description="Auction house address"),
    buyerReferral: str | None = Query(None, description="Buyer referral wallet"),
    expiry: int | None = Query(None, description="Expiry timestamp in seconds (0 = no expiry)"),
    priorityFee: int | None = Query(None, description="Priority fee in microlamports"),
    _wallet: str = Depends(require_auth),
):
    """Build an unsigned cancel-buy-offer transaction via Magic Eden."""
    from app.clients.magic_eden import get_buy_cancel_instruction
    try:
        tx = await get_buy_cancel_instruction(
            buyer, tokenMint, price, auctionHouseAddress, buyerReferral, expiry, priorityFee,
        )
        return {"success": True, "transaction": tx}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME buy_cancel failed buyer=%s mint=%s", buyer, tokenMint, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to build Magic Eden cancel-buy transaction")


@app.get("/nft/magic-eden/instructions/buy_now")
async def me_buy_now(
    buyer: str = Query(..., description="Buyer wallet address"),
    seller: str = Query(..., description="Seller wallet address"),
    tokenMint: str = Query(..., description="Token mint address"),
    tokenATA: str = Query(..., description="Seller's Associated Token Account"),
    price: float = Query(..., description="Price in SOL"),
    sellerExpiry: int = Query(..., description="Seller expiry timestamp in seconds (0 = no expiry)"),
    auctionHouseAddress: str | None = Query(None, description="Auction house address"),
    buyerReferral: str | None = Query(None, description="Buyer referral wallet"),
    sellerReferral: str | None = Query(None, description="Seller referral wallet"),
    buyerExpiry: int | None = Query(None, description="Buyer expiry timestamp (0 = no expiry)"),
    buyerCreatorRoyaltyPercent: int | None = Query(None, ge=0, le=100, description="Creator royalty percent (0-100)"),
    priorityFee: int | None = Query(None, description="Priority fee in microlamports"),
    splPrice: str | None = Query(None, description="SPL token price information (JSON string)"),
    _wallet: str = Depends(require_auth),
):
    """Build an unsigned buy-now NFT transaction via Magic Eden."""
    from app.clients.magic_eden import get_buy_now_instruction
    try:
        tx = await get_buy_now_instruction(
            buyer, seller, tokenMint, tokenATA, price, sellerExpiry,
            auctionHouseAddress, buyerReferral, sellerReferral,
            buyerExpiry, buyerCreatorRoyaltyPercent, priorityFee, splPrice,
        )
        return {"success": True, "transaction": tx}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME buy_now failed buyer=%s mint=%s", buyer, tokenMint, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to build Magic Eden buy-now transaction")


@app.get("/nft/magic-eden/instructions/buy_now_transfer_nft")
async def me_buy_now_transfer_nft(
    buyer: str = Query(..., description="Buyer wallet address"),
    seller: str = Query(..., description="Seller wallet address"),
    tokenMint: str = Query(..., description="Token mint address"),
    tokenATA: str = Query(..., description="Associated Token Account of the token"),
    price: float = Query(..., description="Price in SOL"),
    destinationATA: str = Query(..., description="ATA to send the bought NFT to"),
    destinationOwner: str = Query(..., description="Owner of the destination ATA"),
    createATA: bool = Query(..., description="Whether to include create ATA instructions"),
    sellerExpiry: int = Query(..., description="Seller expiry timestamp in seconds (0 = no expiry)"),
    auctionHouseAddress: str | None = Query(None, description="Auction house address"),
    buyerReferral: str | None = Query(None, description="Buyer referral wallet"),
    sellerReferral: str | None = Query(None, description="Seller referral wallet"),
    buyerExpiry: int | None = Query(None, description="Buyer expiry timestamp (0 = no expiry)"),
    buyerCreatorRoyaltyPercent: int | None = Query(None, ge=0, le=100, description="Creator royalty percent (0-100)"),
    priorityFee: int | None = Query(None, description="Priority fee in microlamports"),
    _wallet: str = Depends(require_auth),
):
    """Build an unsigned buy-and-transfer NFT transaction via Magic Eden."""
    from app.clients.magic_eden import get_buy_now_transfer_nft_instruction
    try:
        tx = await get_buy_now_transfer_nft_instruction(
            buyer, seller, tokenMint, tokenATA, price,
            destinationATA, destinationOwner, createATA, sellerExpiry,
            auctionHouseAddress, buyerReferral, sellerReferral,
            buyerExpiry, buyerCreatorRoyaltyPercent, priorityFee,
        )
        return {"success": True, "transaction": tx}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME buy_now_transfer failed buyer=%s mint=%s", buyer, tokenMint, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to build Magic Eden buy-transfer transaction")


@app.get("/nft/magic-eden/instructions/buy")
async def me_buy_instruction(
    buyer: str = Query(..., description="Buyer wallet address"),
    tokenMint: str = Query(..., description="Token mint address"),
    price: float = Query(..., description="Price in SOL"),
    auctionHouseAddress: str | None = Query(None, description="Auction house address"),
    buyerReferral: str | None = Query(None, description="Buyer referral wallet"),
    buyerCreatorRoyaltyPercent: int | None = Query(None, ge=0, le=100, description="Creator royalty percent (0-100)"),
    expiry: int | None = Query(None, description="Expiry timestamp in seconds (0 = 7 days)"),
    priorityFee: int | None = Query(None, description="Priority fee in microlamports"),
    _wallet: str = Depends(require_auth),
):
    """Build an unsigned NFT buy transaction via Magic Eden."""
    from app.clients.magic_eden import get_buy_instruction
    try:
        tx = await get_buy_instruction(
            buyer, tokenMint, price, auctionHouseAddress, buyerReferral,
            buyerCreatorRoyaltyPercent, expiry, priorityFee,
        )
        return {"success": True, "transaction": tx}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME buy instruction failed buyer=%s mint=%s", buyer, tokenMint, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to build Magic Eden buy transaction")


@app.get("/nft/magic-eden/collections/{symbol}/leaderboard")
async def me_collection_leaderboard(
    symbol: str,
    limit: int = Query(100, ge=1, le=100, description="Number of top holders to return (max 100)"),
    _wallet: str = Depends(require_auth),
):
    """Get holder leaderboard for a collection from Magic Eden."""
    from app.clients.magic_eden import get_collection_leaderboard
    try:
        data = await get_collection_leaderboard(symbol, limit)
        return {"success": True, **data}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME leaderboard failed symbol=%s", symbol, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch Magic Eden leaderboard")


@app.get("/nft/magic-eden/collections/{symbol}/holder_stats")
async def me_collection_holder_stats(
    symbol: str,
    _wallet: str = Depends(require_auth),
):
    """Get holder statistics for a collection from Magic Eden."""
    from app.clients.magic_eden import get_collection_holder_stats
    try:
        stats = await get_collection_holder_stats(symbol)
        return {"success": True, **stats}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME holder stats failed symbol=%s", symbol, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch Magic Eden holder stats")


@app.post("/nft/magic-eden/collections/batch/listings")
async def me_collections_batch_listings(
    body: dict = Body(..., examples=[{"symbols": ["degods", "magicticket"]}]),
    offset: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int = Query(20, ge=1, le=100, description="Number of items to return (max 100)"),
    min_price: float | None = Query(None, description="Filter listings below this price (SOL)"),
    max_price: float | None = Query(None, description="Filter listings above this price (SOL)"),
    sort: str = Query("listPrice", description="Sort field: listPrice or updatedAt"),
    sort_direction: str = Query("asc", description="Sort direction: asc or desc"),
    listingAggMode: bool = Query(False, description="Aggregate listings from all marketplaces"),
    _wallet: str = Depends(require_auth),
):
    """Get active listings for multiple collections in batch from Magic Eden."""
    from app.clients.magic_eden import get_collections_batch_listings
    symbols = body.get("symbols", [])
    if not symbols:
        raise HTTPException(status_code=422, detail="'symbols' array is required in request body")
    try:
        listings = await get_collections_batch_listings(
            symbols, offset, limit, min_price, max_price, sort, sort_direction, listingAggMode
        )
        return {"success": True, "symbols": symbols, "listings": listings, "count": len(listings)}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME batch listings failed symbols=%s", symbols, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch Magic Eden batch listings")


@app.get("/nft/magic-eden/collections/{symbol}/listings")
async def me_collection_listings(
    symbol: str,
    offset: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int = Query(20, ge=1, le=100, description="Number of items to return (max 100)"),
    min_price: float | None = Query(None, description="Filter listings below this price (SOL)"),
    max_price: float | None = Query(None, description="Filter listings above this price (SOL)"),
    sort: str = Query("listPrice", description="Sort field: listPrice or updatedAt"),
    sort_direction: str = Query("asc", description="Sort direction: asc or desc"),
    listingAggMode: bool = Query(False, description="Aggregate listings from all marketplaces"),
    _wallet: str = Depends(require_auth),
):
    """Get active listings for a collection from Magic Eden."""
    from app.clients.magic_eden import get_collection_listings
    try:
        listings = await get_collection_listings(
            symbol, offset, limit, min_price, max_price, sort, sort_direction, listingAggMode
        )
        return {"success": True, "symbol": symbol, "listings": listings, "count": len(listings)}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME collection listings failed symbol=%s", symbol, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch Magic Eden collection listings")


@app.get("/nft/magic-eden/collections")
async def me_collections(
    offset: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int = Query(200, ge=1, le=500, description="Number of items to return (max 500)"),
    _wallet: str = Depends(require_auth),
):
    """Get a paginated list of NFT collections from Magic Eden."""
    from app.clients.magic_eden import get_collections
    try:
        collections, paging = await get_collections(offset, limit)
        return {"success": True, "collections": collections, "count": len(collections), "paging": paging}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME collections list failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch Magic Eden collections")


@app.get("/nft/magic-eden/collections/{collection_symbol}/attributes")
async def me_collection_attributes(
    collection_symbol: str,
    _wallet: str = Depends(require_auth),
):
    """Get trait attributes and floor prices for a collection from Magic Eden."""
    from app.clients.magic_eden import get_collection_attributes
    try:
        data = await get_collection_attributes(collection_symbol)
        return {"success": True, **data}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME collection attributes failed symbol=%s", collection_symbol, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch Magic Eden collection attributes")


@app.get("/nft/magic-eden/collections/{symbol}/stats")
async def me_collection_stats(
    symbol: str,
    timeWindow: str | None = Query(None, description="Time window: 24h, 7d, 30d, all"),
    listingAggMode: bool = Query(False, description="Aggregate listings from all marketplaces"),
    _wallet: str = Depends(require_auth),
):
    """Get stats for a collection from Magic Eden."""
    from app.clients.magic_eden import get_collection_stats
    try:
        stats = await get_collection_stats(symbol, timeWindow, listingAggMode)
        return {"success": True, "stats": stats}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME collection stats failed symbol=%s", symbol, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch Magic Eden collection stats")


@app.get("/nft/magic-eden/collections/{symbol}/activities")
async def me_collection_activities(
    symbol: str,
    offset: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Number of items to return (max 1000)"),
    _wallet: str = Depends(require_auth),
):
    """Get trading activities for a collection from Magic Eden."""
    from app.clients.magic_eden import get_collection_activities
    try:
        activities = await get_collection_activities(symbol, offset, limit)
        return {"success": True, "symbol": symbol, "activities": activities, "count": len(activities)}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME collection activities failed symbol=%s", symbol, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch Magic Eden collection activities")


@app.get("/nft/magic-eden/wallets/{wallet_address}/escrow_balance")
async def me_wallet_escrow_balance(
    wallet_address: str,
    _wallet: str = Depends(require_auth),
):
    """Get Magic Eden escrow balance for a wallet."""
    from app.clients.magic_eden import get_wallet_escrow_balance
    try:
        data = await get_wallet_escrow_balance(wallet_address)
        return {"success": True, "walletAddress": wallet_address, **data}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME escrow balance failed wallet=%s", wallet_address, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch Magic Eden escrow balance")


@app.get("/nft/magic-eden/wallets/{wallet_address}/offers_received")
async def me_wallet_offers_received(
    wallet_address: str,
    min_price: float | None = Query(None, description="Filter offers below this price (SOL)"),
    max_price: float | None = Query(None, description="Filter offers above this price (SOL)"),
    offset: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int = Query(100, ge=1, le=500, description="Number of items to return (max 500)"),
    sort: str = Query("updatedAt", description="Sort field: updatedAt or bidAmount"),
    sort_direction: str = Query("desc", description="Sort direction: asc or desc"),
    _wallet: str = Depends(require_auth),
):
    """Get offers received on NFTs owned by a wallet from Magic Eden."""
    from app.clients.magic_eden import get_wallet_offers_received
    try:
        offers = await get_wallet_offers_received(
            wallet_address, min_price, max_price, offset, limit, sort, sort_direction
        )
        return {"success": True, "walletAddress": wallet_address, "offers": offers, "count": len(offers)}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME wallet offers_received failed wallet=%s", wallet_address, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch Magic Eden offers received")


@app.get("/nft/magic-eden/wallets/{wallet_address}/offers_made")
async def me_wallet_offers_made(
    wallet_address: str,
    min_price: float | None = Query(None, description="Filter offers below this price (SOL)"),
    max_price: float | None = Query(None, description="Filter offers above this price (SOL)"),
    offset: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int = Query(100, ge=1, le=500, description="Number of items to return (max 500)"),
    sort: str = Query("bidAmount", description="Sort field: updatedAt or bidAmount"),
    sort_direction: str = Query("desc", description="Sort direction: asc or desc"),
    _wallet: str = Depends(require_auth),
):
    """Get offers made by a wallet from Magic Eden."""
    from app.clients.magic_eden import get_wallet_offers_made
    try:
        offers = await get_wallet_offers_made(
            wallet_address, min_price, max_price, offset, limit, sort, sort_direction
        )
        return {"success": True, "walletAddress": wallet_address, "offers": offers, "count": len(offers)}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME wallet offers_made failed wallet=%s", wallet_address, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch Magic Eden offers made")


@app.get("/nft/magic-eden/wallets/owner/activities")
async def me_owner_activities(
    owner: str = Query(..., description="Wallet address (required)"),
    createdAt: str | None = Query(None, description="Filter activities created after this time"),
    _wallet: str = Depends(require_auth),
):
    """Get trading activities for a wallet by owner query param from Magic Eden."""
    from app.clients.magic_eden import get_owner_activities
    try:
        activities = await get_owner_activities(owner, createdAt)
        return {"success": True, "owner": owner, "activities": activities, "count": len(activities)}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME owner activities failed owner=%s", owner, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch Magic Eden owner activities")


@app.get("/nft/magic-eden/wallets/{wallet_address}/activities")
async def me_wallet_activities(
    wallet_address: str,
    offset: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int = Query(100, ge=1, le=500, description="Number of items to return (max 500)"),
    _wallet: str = Depends(require_auth),
):
    """Get trading activities for a wallet from Magic Eden."""
    from app.clients.magic_eden import get_wallet_activities
    try:
        activities = await get_wallet_activities(wallet_address, offset, limit)
        return {"success": True, "walletAddress": wallet_address, "activities": activities, "count": len(activities)}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME wallet activities failed wallet=%s", wallet_address, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch Magic Eden wallet activities")


@app.get("/nft/magic-eden/wallets/{wallet_address}")
async def me_wallet(
    wallet_address: str,
    _wallet: str = Depends(require_auth),
):
    """Get Magic Eden profile for a wallet."""
    from app.clients.magic_eden import get_wallet
    try:
        profile = await get_wallet(wallet_address)
        return {"success": True, "wallet": profile}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME wallet profile failed wallet=%s", wallet_address, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch Magic Eden wallet profile")


@app.get("/nft/magic-eden/wallets/{wallet_address}/tokens")
async def me_wallet_tokens(
    wallet_address: str,
    collection_symbol: str | None = Query(None, description="Filter by collection symbol"),
    mcc_address: str | None = Query(None, description="Filter by Metaplex Certified Collection address"),
    offset: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int = Query(100, ge=1, le=500, description="Number of items to return (max 500)"),
    min_price: float | None = Query(None, description="Filter listings below this price (SOL)"),
    max_price: float | None = Query(None, description="Filter listings above this price (SOL)"),
    listStatus: str | None = Query(None, description="listed, unlisted, or both"),
    sort: str = Query("updatedAt", description="Sort field: updatedAt or listPrice"),
    sort_direction: str = Query("desc", description="Sort direction: asc or desc"),
    _wallet: str = Depends(require_auth),
):
    """Get NFT tokens owned by a wallet from Magic Eden."""
    from app.clients.magic_eden import get_wallet_tokens
    try:
        tokens = await get_wallet_tokens(
            wallet_address, collection_symbol, mcc_address,
            offset, limit, min_price, max_price, listStatus, sort, sort_direction,
        )
        return {"success": True, "walletAddress": wallet_address, "tokens": tokens, "count": len(tokens)}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME wallet tokens failed wallet=%s", wallet_address, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch Magic Eden wallet tokens")


@app.get("/nft/magic-eden/tokens/{token_mint}")
async def me_token(
    token_mint: str,
    _wallet: str = Depends(require_auth),
):
    """Get metadata for a specific NFT token from Magic Eden."""
    from app.clients.magic_eden import get_token
    try:
        token = await get_token(token_mint)
        return {"success": True, "token": token}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME token metadata failed token_mint=%s", token_mint, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch Magic Eden token")


@app.get("/nft/magic-eden/tokens/{token_mint}/listings")
async def me_token_listings(
    token_mint: str,
    listingAggMode: bool = Query(False, description="True to return aggregated marketplace listings (Tensor, ME, etc.), False for Magic Eden only"),
    _wallet: str = Depends(require_auth),
):
    """Get active listings for a specific NFT token on Magic Eden."""
    from app.clients.magic_eden import get_token_listings
    try:
        listings = await get_token_listings(token_mint, listingAggMode)
        return {"success": True, "tokenMint": token_mint, "listings": listings, "count": len(listings)}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME token listings failed token_mint=%s", token_mint, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch Magic Eden listings")


@app.get("/nft/magic-eden/tokens/{token_mint}/offers_received")
async def me_token_offers_received(
    token_mint: str,
    min_price: float | None = Query(None, description="Filter offers below this price (SOL)"),
    max_price: float | None = Query(None, description="Filter offers above this price (SOL)"),
    offset: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int = Query(100, ge=1, le=500, description="Number of items to return (max 500)"),
    sort: str = Query("updatedAt", description="Sort field: updatedAt or bidAmount"),
    sort_direction: str = Query("desc", description="Sort direction: asc or desc"),
    _wallet: str = Depends(require_auth),
):
    """Get offers received on a specific NFT token from Magic Eden."""
    from app.clients.magic_eden import get_token_offers_received
    try:
        offers = await get_token_offers_received(
            token_mint, min_price, max_price, offset, limit, sort, sort_direction
        )
        return {"success": True, "tokenMint": token_mint, "offers": offers, "count": len(offers)}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME token offers failed token_mint=%s", token_mint, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch Magic Eden offers")


@app.get("/nft/magic-eden/tokens/{token_mint}/activities")
async def me_token_activities(
    token_mint: str,
    offset: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int = Query(100, ge=1, le=500, description="Number of items to return (max 500)"),
    _wallet: str = Depends(require_auth),
):
    """Get trading activities for a specific NFT token from Magic Eden."""
    from app.clients.magic_eden import get_token_activities
    try:
        activities = await get_token_activities(token_mint, offset, limit)
        return {"success": True, "tokenMint": token_mint, "activities": activities, "count": len(activities)}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME token activities failed token_mint=%s", token_mint, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch Magic Eden activities")


@app.get("/nft/magic-eden/instructions/mmm/sol-fulfill-sell")
async def me_mmm_sol_fulfill_sell(
    pool: str = Query(..., description="Public key of pool to interact with"),
    asset_amount: float = Query(..., alias="assetAmount", description="Amount of asset to transact"),
    max_payment_amount: float = Query(..., alias="maxPaymentAmount", description="Maximum payment amount to be paid by the buyer, in SOL"),
    buyside_creator_royalty_bp: int = Query(..., alias="buysideCreatorRoyaltyBp", description="Creator royalty in basis points"),
    buyer: str = Query(..., description="Public key of buyer of asset"),
    asset_mint: str = Query(..., alias="assetMint", description="Public key of mint account of asset"),
    allowlist_aux_account: str | None = Query(None, alias="allowlistAuxAccount", description="Allowlist aux account for token authentication"),
    priority_fee: int | None = Query(None, alias="priorityFee", description="Priority fee in microlamports"),
    _wallet: str = Depends(require_auth),
):
    """Build an unsigned transaction for a buyer to fulfill an MMM pool sell order."""
    from app.clients.magic_eden import get_mmm_sol_fulfill_sell_instruction
    try:
        result = await get_mmm_sol_fulfill_sell_instruction(
            pool, asset_amount, max_payment_amount, buyside_creator_royalty_bp,
            buyer, asset_mint, allowlist_aux_account, priority_fee,
        )
        return {"success": True, "pool": pool, "data": result}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME MMM sol-fulfill-sell failed pool=%s buyer=%s", pool, buyer, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to build Magic Eden MMM sol-fulfill-sell transaction")


@app.get("/nft/magic-eden/instructions/mmm/sol-fulfill-buy")
async def me_mmm_sol_fulfill_buy(
    pool: str = Query(..., description="Public key of pool to interact with"),
    asset_amount: float = Query(..., alias="assetAmount", description="Amount of asset to transact"),
    min_payment_amount: float = Query(..., alias="minPaymentAmount", description="Minimum payment amount acceptable by the seller, in SOL"),
    seller: str = Query(..., description="Public key of seller of asset"),
    asset_mint: str = Query(..., alias="assetMint", description="Public key of mint account of asset"),
    asset_token_account: str = Query(..., alias="assetTokenAccount", description="Public key of token account of asset"),
    allowlist_aux_account: str | None = Query(None, alias="allowlistAuxAccount", description="Token authentication auxiliary account for allowlist verification"),
    skip_delist: bool | None = Query(None, alias="skipDelist", description="Whether to skip attempting to delist a detected listed NFT"),
    priority_fee: int | None = Query(None, alias="priorityFee", description="Priority fee in microlamports"),
    _wallet: str = Depends(require_auth),
):
    """Build an unsigned transaction for a seller to fulfill an MMM pool buy order."""
    from app.clients.magic_eden import get_mmm_sol_fulfill_buy_instruction
    try:
        result = await get_mmm_sol_fulfill_buy_instruction(
            pool, asset_amount, min_payment_amount, seller, asset_mint,
            asset_token_account, allowlist_aux_account, skip_delist, priority_fee,
        )
        return {"success": True, "pool": pool, "data": result}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME MMM sol-fulfill-buy failed pool=%s seller=%s", pool, seller, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to build Magic Eden MMM sol-fulfill-buy transaction")


@app.get("/nft/magic-eden/instructions/mmm/sol-close-pool")
async def me_mmm_sol_close_pool(
    pool: str = Query(..., description="Public key of pool to close"),
    priority_fee: int | None = Query(None, alias="priorityFee", description="Priority fee in microlamports"),
    _wallet: str = Depends(require_auth),
):
    """Build an unsigned transaction to close an MMM pool and reclaim funds."""
    from app.clients.magic_eden import get_mmm_sol_close_pool_instruction
    try:
        result = await get_mmm_sol_close_pool_instruction(pool, priority_fee)
        return {"success": True, "pool": pool, "data": result}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME MMM sol-close-pool failed pool=%s", pool, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to build Magic Eden MMM sol-close-pool transaction")


@app.get("/nft/magic-eden/instructions/mmm/sol-withdraw-buy")
async def me_mmm_sol_withdraw_buy(
    pool: str = Query(..., description="Public key of pool to withdraw from"),
    sol_amount: float = Query(..., alias="solAmount", description="Amount of SOL to withdraw"),
    priority_fee: int | None = Query(None, alias="priorityFee", description="Priority fee in microlamports"),
    _wallet: str = Depends(require_auth),
):
    """Build an unsigned transaction to withdraw SOL from an MMM pool's buy-side."""
    from app.clients.magic_eden import get_mmm_sol_withdraw_buy_instruction
    try:
        result = await get_mmm_sol_withdraw_buy_instruction(pool, sol_amount, priority_fee)
        return {"success": True, "pool": pool, "data": result}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME MMM sol-withdraw-buy failed pool=%s", pool, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to build Magic Eden MMM sol-withdraw-buy transaction")


@app.get("/nft/magic-eden/instructions/mmm/sol-deposit-buy")
async def me_mmm_sol_deposit_buy(
    pool: str = Query(..., description="Public key of pool to deposit into"),
    sol_amount: float = Query(..., alias="solAmount", description="Amount of SOL to deposit"),
    priority_fee: int | None = Query(None, alias="priorityFee", description="Priority fee in microlamports"),
    _wallet: str = Depends(require_auth),
):
    """Build an unsigned transaction to deposit SOL into an MMM pool's buy-side."""
    from app.clients.magic_eden import get_mmm_sol_deposit_buy_instruction
    try:
        result = await get_mmm_sol_deposit_buy_instruction(pool, sol_amount, priority_fee)
        return {"success": True, "pool": pool, "data": result}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME MMM sol-deposit-buy failed pool=%s", pool, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to build Magic Eden MMM sol-deposit-buy transaction")


@app.get("/nft/magic-eden/instructions/mmm/update-pool")
async def me_mmm_update_pool(
    pool: str = Query(..., description="Public key of the pool to modify"),
    spot_price: float = Query(..., alias="spotPrice", description="Pool spot price in SOL"),
    curve_type: str = Query(..., alias="curveType", description="Curve type: 'linear' or 'exp'"),
    curve_delta: float = Query(..., alias="curveDelta", description="Curve delta used to change price after a fill"),
    reinvest_buy: bool = Query(..., alias="reinvestBuy", description="Must remain unchanged from pool creation"),
    reinvest_sell: bool = Query(..., alias="reinvestSell", description="Must remain unchanged from pool creation"),
    expiry: int = Query(..., description="Expiry timestamp in seconds (0 = no expiry)"),
    lp_fee_bp: int = Query(..., alias="lpFeeBp", description="LP fee in basis points"),
    buyside_creator_royalty_bp: int = Query(..., alias="buysideCreatorRoyaltyBp", description="Creator royalty in basis points"),
    priority_fee: int | None = Query(None, alias="priorityFee", description="Priority fee in microlamports"),
    shared_escrow_count: int | None = Query(None, alias="sharedEscrowCount", description="Number of pools sharing the escrow account"),
    _wallet: str = Depends(require_auth),
):
    """Build an unsigned transaction to update an existing Magic Eden MMM pool."""
    from app.clients.magic_eden import get_mmm_update_pool_instruction
    try:
        result = await get_mmm_update_pool_instruction(
            pool, spot_price, curve_type, curve_delta, reinvest_buy, reinvest_sell,
            expiry, lp_fee_bp, buyside_creator_royalty_bp, priority_fee, shared_escrow_count,
        )
        return {"success": True, "pool": pool, "data": result}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME MMM update-pool failed pool=%s", pool, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to build Magic Eden MMM update-pool transaction")


@app.get("/nft/magic-eden/instructions/mmm/create-pool")
async def me_mmm_create_pool(
    spot_price: float = Query(..., alias="spotPrice", description="Pool initial spot price in SOL"),
    curve_type: str = Query(..., alias="curveType", description="Curve type: 'linear', 'exp', or 'xyk'"),
    curve_delta: float = Query(..., alias="curveDelta", description="Curve delta used to change price after a fill"),
    reinvest_buy: bool = Query(..., alias="reinvestBuy", description="Whether to reinvest bought asset or transfer to owner"),
    reinvest_sell: bool = Query(..., alias="reinvestSell", description="Whether to reinvest payment from sale or transfer to owner"),
    lp_fee_bp: int = Query(..., alias="lpFeeBp", description="Liquidity provider fee in basis points"),
    buyside_creator_royalty_bp: int = Query(..., alias="buysideCreatorRoyaltyBp", description="Creator royalty the pool should pay in basis points"),
    payment_mint: str = Query(..., alias="paymentMint", description="Mint address of payment (default for SOL)"),
    collection_symbol: str = Query(..., alias="collectionSymbol", description="Collection symbol for which the pool will be valid"),
    owner: str = Query(..., description="Owner of the pool"),
    expiry: int | None = Query(None, description="Expiry timestamp in seconds (0 = no expiry)"),
    sol_deposit: float | None = Query(None, alias="solDeposit", description="Optional SOL amount to deposit with pool creation"),
    priority_fee: int | None = Query(None, alias="priorityFee", description="Priority fee in microlamports"),
    shared_escrow_count: int | None = Query(None, alias="sharedEscrowCount", description="Number of pools that will share the escrow account"),
    _wallet: str = Depends(require_auth),
):
    """Build an unsigned transaction to create a new Magic Eden MMM pool."""
    from app.clients.magic_eden import get_mmm_create_pool_instruction
    try:
        result = await get_mmm_create_pool_instruction(
            spot_price, curve_type, curve_delta, reinvest_buy, reinvest_sell,
            lp_fee_bp, buyside_creator_royalty_bp, payment_mint, collection_symbol,
            owner, expiry, sol_deposit, priority_fee, shared_escrow_count,
        )
        return {"success": True, "data": result}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME MMM create-pool failed collection=%s owner=%s", collection_symbol, owner, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to build Magic Eden MMM create-pool transaction")


@app.get("/nft/magic-eden/mmm/token/{mint_address}/pools")
async def me_mmm_token_pools(
    mint_address: str,
    limit: int = Query(1, ge=1, le=5, description="Total best offers to return (max 5). Best offer comes first."),
    _wallet: str = Depends(require_auth),
):
    """Get best MMM buy pools for a specific NFT mint from Magic Eden."""
    from app.clients.magic_eden import get_mmm_token_pools
    try:
        result = await get_mmm_token_pools(mint_address, limit)
        return {"success": True, "mintAddress": mint_address, "data": result}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME MMM token pools failed mint=%s", mint_address, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch Magic Eden MMM token pools")


@app.get("/nft/magic-eden/mmm/pools")
async def me_mmm_pools(
    collection_symbol: str | None = Query(None, description="Collection symbol (at least this or owner required)"),
    pools: list[str] | None = Query(None, description="Pool keys to filter by"),
    owner: str | None = Query(None, description="Pool owner public key (at least this or collectionSymbol required)"),
    offset: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int = Query(100, ge=1, le=500, description="Number of items to return (max 500)"),
    field: str | None = Query(None, description="Sort field: 0=NONE, 1=ADDRESS, 2=SPOT_PRICE, 5=BUYSIDE_ADJUSTED_PRICE"),
    direction: str | None = Query(None, description="Sort direction: 0=NONE, 1=DESC, 2=INC"),
    _wallet: str = Depends(require_auth),
):
    """Get Magic Eden MMM (market maker) pools for a collection or owner."""
    if not collection_symbol and not owner:
        raise HTTPException(status_code=422, detail="At least one of 'collectionSymbol' or 'owner' is required")
    from app.clients.magic_eden import get_mmm_pools
    try:
        result = await get_mmm_pools(collection_symbol, pools, owner, offset, limit, field, direction)
        return {"success": True, "data": result}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME MMM pools failed collection=%s owner=%s", collection_symbol, owner, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch Magic Eden MMM pools")


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
                # The sidebar groups by activity, not by row-modification
                # time — see services.session._serialize.
                "lastMessageAt": s["lastMessageAt"],
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


# ---------------------------------------------------------------------------
# Share Routes — publish one chat behind an opaque, revocable link
#
# The three owner routes below are wallet-scoped; `share_svc` returns None
# rather than raising when the wallet does not own the session, so a caller
# probing someone else's session id gets the same 404 as one probing an id
# that does not exist. The public resolver lives further down, next to the
# other unauthenticated endpoints.
# ---------------------------------------------------------------------------

@app.get("/sessions/{session_id}/share")
async def get_session_share(
    session_id: str,
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    """Current share for a session, or {"share": null} when not shared."""
    return {"share": await share_svc.get_share(db, wallet, session_id)}


@app.post("/sessions/{session_id}/share")
async def create_session_share(
    session_id: str,
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    """Publish the session, or move an existing link's snapshot up to now.

    Idempotent by design: sharing an already-shared chat refreshes the
    cutoff and returns the SAME token, because the URL may already be in
    someone else's hands and "update the link" must not break it.
    """
    share = await share_svc.create_or_refresh(db, wallet, session_id)
    if share is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _audit(db, AuditEvent(
        event_type=AuditEventType.ACTION_EXECUTED,
        severity=AuditEventSeverity.INFO,
        entity_type=AuditEntityType.SESSION,
        entity_id=session_id,
        wallet_address=wallet,
        session_id=session_id,
        event_data={"action": "session_shared", "sharedUpTo": share["sharedUpTo"]},
    ))
    return {"share": share}


@app.delete("/sessions/{session_id}/share")
async def revoke_session_share(
    session_id: str,
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    """Revoke the link. The token stops resolving immediately and for good."""
    removed = await share_svc.revoke(db, wallet, session_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Share not found")
    _audit(db, AuditEvent(
        event_type=AuditEventType.ACTION_EXECUTED,
        severity=AuditEventSeverity.INFO,
        entity_type=AuditEntityType.SESSION,
        entity_id=session_id,
        wallet_address=wallet,
        session_id=session_id,
        event_data={"action": "session_share_revoked"},
    ))
    return {"ok": True}


@app.get("/shares")
async def list_session_shares(
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    """Every link this wallet has published — backs the Shared Links page."""
    return {"shares": await share_svc.list_shares(db, wallet)}


@app.get("/public/shares/{token}")
async def get_public_share(
    token: str,
    _internal: None = Depends(require_internal),
    db: AsyncSession = Depends(get_session),
):
    """Resolve a share token to its published conversation. No wallet needed.

    `require_internal` — not `require_auth` — is the whole point: the gateway
    still proves it is the gateway, but no user identity is demanded, so a
    visitor with the link reads the chat without connecting a wallet. The
    token is the only credential, and it grants reading and nothing else.
    """
    shared = await share_svc.resolve_public(db, token)
    if shared is None:
        raise HTTPException(status_code=404, detail="Shared chat not found")
    return shared


# ---------------------------------------------------------------------------
# Issue Reports — Help → Report Issue
# ---------------------------------------------------------------------------

class IssueReportRequest(BaseModel):
    """Body for POST /issues."""

    category: str
    subject: str
    description: str
    # Route, app build, user agent — captured by the client so a report can be
    # acted on without a follow-up round trip asking "where were you?".
    context: dict | None = None


@app.post("/issues")
async def create_issue_report(
    body: IssueReportRequest,
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    """File a report against OPRAI. Wallet-scoped, throttled per wallet."""
    description = (body.description or "").strip()
    if len(description) < 10:
        # The one field the report is worthless without. Rejected here as well
        # as in the form, because the form is not the only way to reach this.
        raise HTTPException(status_code=400, detail="Please describe the issue in a little more detail.")

    recent = await issue_svc.count_recent(db, wallet)
    if recent >= 5:
        raise HTTPException(
            status_code=429,
            detail="You've sent several reports just now — please wait a few minutes before sending another.",
        )

    report = await issue_svc.create_report(
        db,
        wallet=wallet,
        category=body.category,
        subject=body.subject,
        description=description,
        context=body.context,
    )
    _audit(db, AuditEvent(
        event_type=AuditEventType.ACTION_EXECUTED,
        severity=AuditEventSeverity.INFO,
        entity_type=AuditEntityType.SESSION,
        entity_id=report["id"],
        wallet_address=wallet,
        session_id=None,
        event_data={"action": "issue_reported", "category": report["category"]},
    ))
    return {"report": report}


@app.get("/issues/mine")
async def list_own_issue_reports(
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    """What this wallet has reported, and where each one stands."""
    return {"reports": await issue_svc.list_own_reports(db, wallet)}


# ---------------------------------------------------------------------------
# Settings — Account / Usage / Privacy
#
# Order matters: `/sessions/all` MUST be declared before the
# `/sessions/{session_id}` catch-all DELETE below, otherwise FastAPI
# matches "all" as a session_id and the bulk-delete endpoint is unreachable.
# ---------------------------------------------------------------------------

@app.delete("/sessions/all")
async def delete_all_sessions_route(
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    """Soft-delete every session belonging to the authenticated wallet.

    Used by Settings → Privacy → "Delete chat history". Rows are kept
    in the DB with `is_deleted=True` for audit / recovery; visible UI
    history is wiped immediately.
    """
    count = await session_svc.delete_all_sessions(db, wallet)
    _audit(db, AuditEvent(
        event_type=AuditEventType.ACTION_EXECUTED,
        severity=AuditEventSeverity.WARNING,
        entity_type=AuditEntityType.SESSION,
        entity_id="all",
        wallet_address=wallet,
        event_data={"action": "all_sessions_deleted", "count": count},
    ))
    return {"ok": True, "deleted": count}


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


@app.get("/usage")
async def get_usage(wallet: str = Depends(require_auth)):
    """Per-wallet usage snapshot for the Settings → Usage card.

    Returns daily / weekly / monthly token + message counters along with
    each timeframe's reset boundary and the per-chat cap values. The
    frontend renders three progress cards and the composer banner reads
    `chat.tokenCap` / `chat.messageCap` to label the per-chat lock.
    """
    from app.services.cost_cap import get_usage_snapshot
    return await get_usage_snapshot(wallet)


@app.delete("/user/memories")
async def delete_all_memories_route(
    wallet: str = Depends(require_auth),
):
    """Proxy to memory-service to delete every memory for this wallet.

    Used by Settings → Privacy → "Delete saved memories". The memory
    service owns the Qdrant collection; we authenticate via wallet auth
    here and forward with the internal-service header pair so the
    memory-service's `require_auth` accepts the call.
    """
    memory_base = os.environ.get("MEMORY_SERVICE_URL", "http://localhost:3040").rstrip("/")
    headers = {
        "X-User-Wallet": wallet,
        "X-Internal-Api-Key": settings.OPRAI_INTERNAL_API_KEY,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.delete(
                f"{memory_base}/memories",
                params={"wallet": wallet},
                headers=headers,
            )
            r.raise_for_status()
            return r.json() if r.headers.get("content-type", "").startswith("application/json") else {"ok": True}
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"memory-service unreachable: {exc}")


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
    # Verify session ownership AND surface the per-chat lock state so the
    # frontend can render the locked composer on reload (the lock is a
    # durable DB flag — must not depend on transient SSE error state).
    session = await session_svc.get_session(db, wallet, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Not found")

    messages = await message_svc.get_messages(db, wallet, session_id, limit=limit, offset=offset)
    return {
        "messages": messages,
        "session": {
            "id":           session.get("id"),
            "messageCount": session.get("messageCount", 0),
            "totalTokens":  session.get("totalTokens", 0),
            "isLocked":     session.get("isLocked", False),
            "lockedReason": session.get("lockedReason"),
        },
    }


class PatchMessageMetaRequest(BaseModel):
    action_results: dict | None = None
    query_snapshots: dict | None = None


class FeedbackRequest(BaseModel):
    """Body for POST /messages/{message_id}/feedback."""

    rating: int  # +1 = thumbs up, -1 = thumbs down
    note: str | None = None


@app.post("/messages/{message_id}/feedback")
async def post_message_feedback(
    message_id: str,
    body: FeedbackRequest,
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    """Record a thumbs-up / thumbs-down on an assistant message.

    A second click from the same wallet on the same message updates the
    existing row instead of inserting a duplicate (UNIQUE constraint).
    Negative ratings power the admin review queue and the alert when
    overall thumbs-down rate exceeds X% of recent messages.
    """
    if body.rating not in (-1, 1):
        raise HTTPException(status_code=400, detail="rating must be -1 or 1")

    note = (body.note or "").strip()
    if len(note) > 4000:
        note = note[:4000]

    try:
        msg_uuid = uuid.UUID(message_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="invalid message_id")

    # Verify message exists & belongs to a session this wallet can write to.
    # Defence in depth — middleware already attached `wallet`, but we don't
    # let one wallet drown another wallet's message in negative feedback.
    from app.models.message import ChatMessage as _ChatMessageORM

    msg_q = await db.execute(
        select(_ChatMessageORM).where(_ChatMessageORM.id == msg_uuid)
    )
    msg = msg_q.scalar_one_or_none()
    if msg is None:
        raise HTTPException(status_code=404, detail="message not found")
    if msg.wallet_address != wallet:
        raise HTTPException(status_code=403, detail="not your conversation")

    # Upsert: one row per (message, wallet).
    await db.execute(
        sa_text(
            f"""
            INSERT INTO {settings.DB_SCHEMA}.message_feedback
                (id, message_id, wallet, rating, note)
            VALUES (gen_random_uuid(), :mid, :wallet, :rating, :note)
            ON CONFLICT (message_id, wallet) DO UPDATE
              SET rating = EXCLUDED.rating,
                  note   = EXCLUDED.note
            """
        ),
        {"mid": msg_uuid, "wallet": wallet, "rating": body.rating, "note": note or None},
    )
    await db.commit()
    return {"ok": True}


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
                    protocols=body.protocols,
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


@app.post("/messages/edit")
async def edit_message_stream(
    body: EditMessageRequest,
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    """Edit a previously-sent user message and stream a fresh response.

    Industry-standard "branch-on-edit" semantics implemented as a soft-delete:
    every message at or after the edited target gets `metadata.superseded_at`
    stamped, so it is filtered out of both the chat UI feed and the LLM
    history. The edited content is persisted as a brand-new user message and
    the assistant response streams normally — the SSE format matches
    `/messages/stream` so the client can reuse the same parser.
    """
    from app.models.message import ChatMessage as _ChatMessageORM
    import uuid as _uuid
    from datetime import datetime as _dt, timezone as _tz

    content = body.content.strip()
    session_id = body.session_id

    # Local sessions never reached the DB — reject up front rather than
    # invent a session here. Editing only makes sense in a persisted thread.
    if session_id.startswith("local:"):
        raise HTTPException(status_code=400, detail="Cannot edit messages in an unpersisted session")

    session = await session_svc.get_session(db, wallet, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Look up the target message and verify ownership + role.
    try:
        target_uuid = _uuid.UUID(body.message_id)
        session_uuid = _uuid.UUID(session_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid id format")

    target_stmt = (
        select(_ChatMessageORM)
        .where(
            _ChatMessageORM.id == target_uuid,
            _ChatMessageORM.session_id == session_uuid,
            _ChatMessageORM.wallet_address == wallet,
        )
    )
    target = (await db.execute(target_stmt)).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Message not found")
    if target.role != "user":
        raise HTTPException(status_code=400, detail="Only user messages can be edited")
    if (target.metadata_ or {}).get("superseded_at"):
        raise HTTPException(status_code=400, detail="Message has already been superseded")

    # Soft-delete: stamp superseded_at on the target AND every message
    # ordered at-or-after it. We pull then update each row so JSONB merging
    # is explicit and we don't depend on an `||` operator the dialect may
    # not expose through the ORM.
    affected_stmt = (
        select(_ChatMessageORM)
        .where(
            _ChatMessageORM.session_id == session_uuid,
            _ChatMessageORM.wallet_address == wallet,
            _ChatMessageORM.created_at >= target.created_at,
        )
    )
    affected = (await db.execute(affected_stmt)).scalars().all()
    now_iso = _dt.now(_tz.utc).isoformat()
    for m in affected:
        meta = dict(m.metadata_ or {})
        if meta.get("superseded_at"):
            continue  # already gone — skip
        meta["superseded_at"] = now_iso
        meta["superseded_by_edit_of"] = str(target.id)
        m.metadata_ = meta
        # SQLAlchemy with JSONB needs the attribute marked dirty for the
        # in-place dict mutation to be flushed. Re-assign the whole field.

    await db.commit()

    _audit(db, AuditEvent(
        event_type=AuditEventType.ACTION_EXECUTED,
        severity=AuditEventSeverity.INFO,
        entity_type=AuditEntityType.SESSION,
        entity_id=session_id,
        wallet_address=wallet,
        session_id=session_id,
        event_data={
            "action": "message_edited",
            "edited_message_id": body.message_id,
            "superseded_count": len(affected),
            "new_content_length": len(content),
        },
    ))

    async def event_generator() -> AsyncGenerator[str, None]:
        # Tell the client which messages to drop locally, so the UI matches
        # the DB before the new assistant turn streams in.
        yield f"data: {json.dumps({'edit': {'editedMessageId': body.message_id, 'supersededCount': len(affected)}})}\n\n"

        # TEMP DEBUG — record edit-stream lifecycle so we can diagnose
        # the "first try fails, retry works" intermittent. Remove once stable.
        try:
            with open("/tmp/oprai-debug.log", "a") as _df:
                _df.write(
                    f"\n[EDIT] stream-open session={session_id[:8]} "
                    f"target={body.message_id[:8]} content={content[:80]!r}\n"
                )
        except Exception:
            pass

        async with async_session_factory() as stream_db:
            try:
                async for chunk in message_svc.stream_chat_response(
                    stream_db, session_id, wallet, content,
                    is_first_message=False,
                    attachments=[],
                    protocols=body.protocols,
                    # Stamp the new user message so the bubble keeps its
                    # "(edited)" indicator after page reload, and so audit
                    # trails can distinguish original vs edited turns.
                    extra_user_metadata={
                        "edited_at": now_iso,
                        "edit_replaces": body.message_id,
                    },
                ):
                    yield chunk
                await stream_db.commit()
                try:
                    with open("/tmp/oprai-debug.log", "a") as _df:
                        _df.write(f"[EDIT] stream-done session={session_id[:8]}\n")
                except Exception:
                    pass
            except Exception as exc:
                # Capture the exception details into our debug log so we can see
                # WHY the first attempt fails. The bare `raise` re-raises into
                # FastAPI/uvicorn which turns it into an HTTP-level error the
                # frontend renders as "Sorry, something went wrong".
                import traceback as _tb
                try:
                    with open("/tmp/oprai-debug.log", "a") as _df:
                        _df.write(
                            f"[EDIT-ERR] session={session_id[:8]} "
                            f"err={type(exc).__name__}: {str(exc)[:400]}\n"
                            f"{_tb.format_exc()[:2000]}\n"
                        )
                except Exception:
                    pass
                # Surface the failure to the client as an in-stream error event
                # rather than letting the connection drop. The frontend's SSE
                # parser already handles `{error, errorType}` gracefully.
                err_label = type(exc).__name__
                yield f"data: {json.dumps({'error': f'Edit failed ({err_label}). Please try again.', 'errorType': 'edit_stream'})}\n\n"
                yield "data: [DONE]\n\n"
                await stream_db.rollback()
                return

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
    format: str = Query("json", pattern="^(json|csv)$"),
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
    priority: str = Query("medium", pattern="^(low|medium|high|urgent)$"),
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
    priority: str = Query("medium", pattern="^(low|medium|high|urgent)$"),
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
    risk_tolerance: str = Query("moderate", pattern="^(conservative|moderate|aggressive)$"),
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
    priority: str = Query("medium", pattern="^(low|medium|high|urgent)$"),
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
    format: str = Query("json", pattern="^(json|csv)$"),
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


@app.get("/nft/magic-eden/instructions/magic-ticket/burns")
async def me_magic_ticket_burns(
    wallet_address: str = Query(..., alias="walletAddress", description="Wallet address that owns the tickets"),
    mint_addresses: str = Query(..., alias="mintAddresses", description="Comma-separated list of mint addresses to burn"),
    _wallet: str = Depends(require_auth),
):
    """Build burn transactions for given Magic Ticket mint addresses."""
    from app.clients.magic_eden import get_magic_ticket_burn_instructions
    mints = [m.strip() for m in mint_addresses.split(",") if m.strip()]
    try:
        result = await get_magic_ticket_burn_instructions(wallet_address, mints)
        return {"success": True, "walletAddress": wallet_address, "mintCount": len(mints), "data": result}
    except httpx.HTTPStatusError as e:
        raise magic_eden_error(e)
    except Exception:
        logger.error("ME magic ticket burns failed wallet=%s", wallet_address, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to build Magic Eden magic ticket burn transactions")


@app.get("/api/v1/memories")
async def get_memories(
    x_user_wallet: str = Header(..., alias="X-User-Wallet"),
    db: AsyncSession = Depends(get_session),
):
    """Return this wallet's durable memory facts (confidence >= 0.4, newest first, max 12)."""
    rows = await db.execute(
        sa_text(
            f"SELECT fact_type, fact_value, confidence, updated_at "
            f"FROM {settings.DB_SCHEMA}.user_facts "
            "WHERE wallet = :w AND confidence >= 0.4 "
            "ORDER BY updated_at DESC LIMIT 12"
        ),
        {"w": x_user_wallet},
    )
    items = rows.fetchall()
    return {
        "memories": [
            {
                "fact_type": ft,
                "fact_value": fv,
                "confidence": float(conf),
                "updated_at": str(ua),
            }
            for ft, fv, conf, ua in items
        ]
    }


@app.delete("/api/v1/memories/{fact_type}")
async def delete_memory(
    fact_type: str,
    x_user_wallet: str = Header(..., alias="X-User-Wallet"),
    db: AsyncSession = Depends(get_session),
):
    """Delete a specific memory fact for this wallet."""
    await db.execute(
        sa_text(
            f"DELETE FROM {settings.DB_SCHEMA}.user_facts "
            "WHERE wallet = :w AND fact_type = :ft"
        ),
        {"w": x_user_wallet, "ft": fact_type},
    )
    await db.commit()
    return {"deleted": fact_type}


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
