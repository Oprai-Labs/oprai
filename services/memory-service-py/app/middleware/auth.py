"""FastAPI authentication dependency.

Validates requests using the gateway-injected headers:
  - X-User-Wallet: the authenticated wallet address
  - X-Internal-Api-Key: shared secret between gateway and services

This mirrors the Express @oprai/auth-middleware behaviour.
"""

import logging
import secrets

from fastapi import Header, HTTPException, Request

from app.config import settings

logger = logging.getLogger(__name__)


async def require_auth(
    request: Request,
    x_user_wallet: str | None = Header(None, alias="X-User-Wallet"),
    x_internal_api_key: str | None = Header(None, alias="X-Internal-Api-Key"),
) -> str:
    """Return the authenticated wallet address, or raise 401/403.

    This is used as a FastAPI dependency via ``Depends(require_auth)``.
    The return value is the verified wallet address string.
    """
    if not x_user_wallet:
        logger.warning(
            "Authentication rejected: missing wallet header",
            extra={"path": request.url.path, "method": request.method},
        )
        raise HTTPException(status_code=401, detail="Unauthorized: missing wallet header")

    # Use constant-time comparison to prevent timing-based API key enumeration.
    if not x_internal_api_key or not secrets.compare_digest(
        x_internal_api_key, settings.OPRAI_INTERNAL_API_KEY
    ):
        logger.warning(
            "Authentication rejected: invalid internal API key",
            extra={"path": request.url.path, "method": request.method},
        )
        raise HTTPException(status_code=403, detail="Forbidden: invalid internal API key")

    return x_user_wallet
