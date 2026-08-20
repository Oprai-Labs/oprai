"""FastAPI application entrypoint for the OPRAI memory service.

Starts both the HTTP server (FastAPI/Uvicorn) and the gRPC server
within the same process using the async lifespan protocol.
"""

import logging
import os
import time
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any

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
from starlette.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.connection import get_session, init_db, close_db
from app.grpc_server import start_grpc_server
from app.middleware.auth import require_auth
from app.middleware.request_audit import RequestAuditMiddleware
from app.services.audit_service import log_memory_op
from app.services.consent import get_consent, update_consent, is_type_allowed
from app.services.embeddings import EmbeddingService
from app.services.summary import SummaryService
from app.services.vector import VectorService

import structlog
logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------
REQUEST_COUNT = Counter(
    "memory_service_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)
REQUEST_LATENCY = Histogram(
    "memory_service_request_duration_seconds",
    "Request latency in seconds",
    ["method", "endpoint"],
)

# ---------------------------------------------------------------------------
# Pydantic request/response schemas
# ---------------------------------------------------------------------------


class StoreMemoryRequest(BaseModel):
    payload: dict[str, Any] = Field(
        ...,
        description="Memory payload with at least user_id, type, and summary",
    )


class SearchMemoryParams(BaseModel):
    query: str = Field(..., min_length=1, max_length=5000)
    types: str | None = Field(None, description="Comma-separated memory types to filter")
    top_k: int = Field(default=5, alias="topK", ge=1, le=50)
    threshold: float = Field(default=0.75, ge=0.0, le=1.0)

    model_config = {"populate_by_name": True}


class UpdateConsentRequest(BaseModel):
    position: bool | None = None
    contract: bool | None = None
    strategy: bool | None = None
    preference: bool | None = None
    decision: bool | None = None


class SummarizeRequest(BaseModel):
    conversation_id: str = Field(..., alias="conversationId")
    chunk: str = Field(..., min_length=1)
    token_count: int = Field(default=0, alias="tokenCount", ge=0)

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Singleton services (created at startup, torn down at shutdown)
# ---------------------------------------------------------------------------
_vector_service: VectorService | None = None
_embedding_service: EmbeddingService | None = None
_summary_service: SummaryService | None = None
_grpc_server = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: init DB + Qdrant, start gRPC, then teardown."""
    global _vector_service, _embedding_service, _summary_service, _grpc_server

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

    # Database
    await init_db()
    logger.info("Database initialized")

    # Vector store
    _vector_service = VectorService()
    try:
        await _vector_service.ensure_collection()
        logger.info("Qdrant collection ensured")
    except Exception:
        logger.warning("Could not ensure Qdrant collection (Qdrant may be unavailable)", exc_info=True)

    # Embeddings (optional -- degrades gracefully without API key)
    try:
        _embedding_service = EmbeddingService()
        logger.info("Embedding service ready")
    except RuntimeError:
        logger.warning("Embedding service not available (missing API key)")
        _embedding_service = None

    # Summarization
    _summary_service = SummaryService()

    # gRPC
    _grpc_server = await start_grpc_server()
    logger.info("gRPC server started on port %d", settings.GRPC_PORT)

    yield

    # Shutdown
    if _grpc_server is not None:
        await _grpc_server.stop(grace=5)
        logger.info("gRPC server stopped")
    if _vector_service is not None:
        await _vector_service.close()
    await close_db()
    logger.info("All connections closed")


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="OPRAI Memory Service",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    # Internal service — only the gateway and trusted origins should call this.
    # allow_origins=["*"] combined with allow_credentials=True is forbidden by
    # the CORS spec (browsers reject it). Use explicit origins instead.
    allow_origins=settings.CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-User-Wallet", "X-Internal-Api-Key"],
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
async def security_headers_middleware(request: Request, call_next):
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
async def limit_request_body(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > _MAX_BODY_BYTES:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=413, content={"error": "Request body too large (max 10 MB)"})
    return await call_next(request)


# ---------------------------------------------------------------------------
# Middleware for metrics
# ---------------------------------------------------------------------------
@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
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
        "service": "memory-service-py",
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
# Memory Routes
# ---------------------------------------------------------------------------
@app.post("/memory")
async def store_memory(
    body: StoreMemoryRequest,
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    """Add or update a memory point in Qdrant."""
    if _embedding_service is None or _vector_service is None:
        raise HTTPException(
            status_code=503,
            detail="Memory service is not fully configured (missing API key or Qdrant)",
        )

    payload = body.payload
    summary = payload.get("summary", "")
    memory_type = payload.get("type", "meta")

    if not summary:
        raise HTTPException(status_code=400, detail="payload.summary is required")

    # Check consent
    consent = await get_consent(db, wallet)
    if not is_type_allowed(consent, memory_type):
        raise HTTPException(
            status_code=403,
            detail=f"User consent required for memory type '{memory_type}'",
        )

    # Compliance check
    _validate_payload_compliance(summary, memory_type)

    # Generate embedding
    vector = await _embedding_service.embed(summary)

    # Ensure user_id is set
    payload.setdefault("user_id", wallet)
    payload.setdefault("timestamp", datetime.now(timezone.utc).isoformat())

    # Store in Qdrant
    t_start = time.perf_counter()
    try:
        point_id = await _vector_service.store(payload, vector)
        duration_ms = int((time.perf_counter() - t_start) * 1000)
        await log_memory_op(
            db,
            operation="insert",
            collection=settings.COLLECTION_NAME,
            user_id=wallet,
            wallet_address=wallet,
            vector_count=1,
            bytes_stored=len(summary.encode()),
            memory_type=memory_type,
            metadata={"point_id": point_id},
            duration_ms=duration_ms,
            success=True,
        )
    except Exception as exc:
        duration_ms = int((time.perf_counter() - t_start) * 1000)
        await log_memory_op(
            db,
            operation="insert",
            collection=settings.COLLECTION_NAME,
            user_id=wallet,
            wallet_address=wallet,
            duration_ms=duration_ms,
            success=False,
            error_message=str(exc),
        )
        raise

    return {"point": {"id": point_id, "payload": payload}, "merged": False}


@app.get("/memory/search")
async def search_memory(
    wallet: str = Depends(require_auth),
    query: str = Query(default="", min_length=0),
    types: str | None = Query(default=None),
    topK: int = Query(default=5, ge=1, le=50),
    threshold: float = Query(default=0.75, ge=0.0, le=1.0),
    db: AsyncSession = Depends(get_session),
):
    """Semantic search for memory points."""
    if _embedding_service is None or _vector_service is None:
        raise HTTPException(
            status_code=503,
            detail="Memory service is not fully configured",
        )

    if not query:
        return {"results": []}

    query_vector = await _embedding_service.embed(query)

    filters: dict[str, Any] = {"user_id": wallet}
    if types:
        filters["types"] = [t.strip() for t in types.split(",") if t.strip()]

    t_start = time.perf_counter()
    try:
        results = await _vector_service.search(
            query_vector, top_k=topK, threshold=threshold, filters=filters,
        )
        duration_ms = int((time.perf_counter() - t_start) * 1000)
        await log_memory_op(
            db,
            operation="search",
            collection=settings.COLLECTION_NAME,
            user_id=wallet,
            wallet_address=wallet,
            vector_count=len(results),
            metadata={"query_length": len(query), "top_k": topK, "results_returned": len(results)},
            duration_ms=duration_ms,
            success=True,
        )
    except Exception as exc:
        duration_ms = int((time.perf_counter() - t_start) * 1000)
        await log_memory_op(
            db,
            operation="search",
            collection=settings.COLLECTION_NAME,
            user_id=wallet,
            wallet_address=wallet,
            duration_ms=duration_ms,
            success=False,
            error_message=str(exc),
        )
        raise

    return {"results": results}


@app.delete("/memory/{point_id}")
async def delete_memory(
    point_id: str,
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    """Delete a memory point by ID. Only the owning wallet may delete."""
    if _vector_service is None:
        raise HTTPException(status_code=503, detail="Vector service not available")

    point = await _vector_service.get_by_id(point_id)
    if point is None:
        raise HTTPException(status_code=404, detail="Point not found")

    if point["payload"].get("user_id") != wallet:
        raise HTTPException(status_code=403, detail="Forbidden")

    t_start = time.perf_counter()
    deleted = await _vector_service.delete(point_id)
    duration_ms = int((time.perf_counter() - t_start) * 1000)

    await log_memory_op(
        db,
        operation="delete",
        collection=settings.COLLECTION_NAME,
        user_id=wallet,
        wallet_address=wallet,
        vector_count=1,
        metadata={"point_id": point_id},
        duration_ms=duration_ms,
        success=deleted,
        error_message=None if deleted else "Delete operation returned False",
    )

    if not deleted:
        raise HTTPException(status_code=500, detail="Delete failed")

    return {"ok": True, "id": point_id}


@app.delete("/memories")
async def delete_all_memories(
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
    target_wallet: str | None = Query(None, alias="wallet"),
):
    """Delete every memory point owned by the authenticated wallet.

    Used by chat-service's `/user/memories` proxy (Settings → Privacy →
    "Delete saved memories"). The `wallet` query param is accepted for
    parity with the proxy but is constrained to the authenticated wallet
    — clients cannot wipe someone else's memories.
    """
    if _vector_service is None:
        raise HTTPException(status_code=503, detail="Vector service not available")

    # Scope check: only the authenticated wallet can wipe its own memories.
    # The proxy passes the wallet as a query param for compatibility, but
    # the auth dependency is the source of truth.
    if target_wallet and target_wallet != wallet:
        raise HTTPException(status_code=403, detail="Forbidden")

    t_start = time.perf_counter()
    count = await _vector_service.delete_by_wallet(wallet)
    duration_ms = int((time.perf_counter() - t_start) * 1000)

    await log_memory_op(
        db,
        operation="delete_all",
        collection=settings.COLLECTION_NAME,
        user_id=wallet,
        wallet_address=wallet,
        vector_count=count,
        metadata={},
        duration_ms=duration_ms,
        success=True,
        error_message=None,
    )

    return {"ok": True, "deleted": count}


# ---------------------------------------------------------------------------
# Consent Routes
# ---------------------------------------------------------------------------
@app.get("/consent/{user_id}")
async def get_consent_route(
    user_id: str,
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    """Get consent status for a user."""
    resolved_id = wallet if user_id == "me" else user_id

    # Only allow users to see their own consent
    if resolved_id != wallet:
        raise HTTPException(status_code=403, detail="Forbidden")

    consent = await get_consent(db, resolved_id)
    return {"userId": resolved_id, "consent": consent}


@app.put("/consent/{user_id}")
async def update_consent_route(
    user_id: str,
    body: UpdateConsentRequest,
    wallet: str = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    """Update consent flags for a user."""
    resolved_id = wallet if user_id == "me" else user_id

    if resolved_id != wallet:
        raise HTTPException(status_code=403, detail="Forbidden")

    fields = body.model_dump(exclude_none=True)
    consent = await update_consent(db, resolved_id, fields)
    return {"userId": resolved_id, "consent": consent}


# ---------------------------------------------------------------------------
# Summarization Route
# ---------------------------------------------------------------------------
@app.post("/summarize")
async def summarize_route(
    body: SummarizeRequest,
    wallet: str = Depends(require_auth),
):
    """Summarize a conversation chunk."""
    if _summary_service is None:
        raise HTTPException(status_code=503, detail="Summary service not available")

    summary = await _summary_service.summarize(
        body.conversation_id, body.chunk, body.token_count,
    )
    return {"summary": summary}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
DENYLIST = [
    # Secrets / credentials
    "private key", "mnemonic", "seed phrase", "signed transaction",
    "identity_number", "credit_card", "health_record",
]
# Note: this list used to also carry jailbreak phrases in four languages, to
# stop a user planting a delayed injection in their own memory store. It came
# out with the equivalent filter in chat-service: a phrase list catches the
# wordings someone thought to write down and nothing else, so it bought
# confidence rather than safety. What is left here is the part a substring
# match can actually do — keeping secrets and PII out of the store.


def _validate_payload_compliance(summary: str, memory_type: str) -> None:
    """Check that the summary does not contain prohibited content."""
    import unicodedata
    normalized = unicodedata.normalize("NFKC", summary).lower()
    for term in DENYLIST:
        if term in normalized:
            raise HTTPException(
                status_code=400,
                detail="Payload contains prohibited content",
            )
    if memory_type != "meta" and len(summary) < 30:
        raise HTTPException(
            status_code=400,
            detail="Summary is too short (minimum 30 characters for non-meta types)",
        )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def main() -> None:
    """Run the service with uvicorn."""
    import uvicorn

    # Logging is already configured at module import time; this is a no-op guard.
    logger.info("Starting memory-service-py", port=settings.PORT)
    uvicorn.run(
        "app.main:app",
        host=settings.BIND_HOST,
        port=settings.PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
