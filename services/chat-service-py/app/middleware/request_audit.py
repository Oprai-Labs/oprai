"""Request-level audit middleware.

Generates a request_id for every request, binds it to structlog contextvars so
all log lines within the request carry it, and emits an audit DB row for API
errors (4xx/5xx) and slow responses (>1s).
"""

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger(__name__)


class RequestAuditMiddleware(BaseHTTPMiddleware):
    """Middleware that adds X-Request-ID and emits structured access logs."""

    SKIP_PATHS = {"/health", "/metrics"}

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip noisy low-value endpoints.
        if request.url.path in self.SKIP_PATHS:
            return await call_next(request)

        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        wallet = request.headers.get("X-User-Wallet", "")

        # Bind context so all structlog calls within this request include these fields.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            wallet=wallet,
        )

        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000

        response.headers["X-Request-ID"] = request_id

        status = response.status_code
        log_kwargs = dict(
            status=status,
            duration_ms=round(duration_ms, 2),
        )

        if status >= 500:
            logger.error("request completed", **log_kwargs)
        elif status >= 400:
            logger.warning("request completed", **log_kwargs)
        elif duration_ms > 1000:
            logger.warning("slow request", **log_kwargs)
        else:
            logger.info("request completed", **log_kwargs)

        return response
