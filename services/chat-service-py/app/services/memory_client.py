"""HTTP client for the Memory Service.

Searches past memories and stores new ones to provide long-term context
for chat conversations. Communicates via the internal REST API at
MEMORY_SERVICE_URL.
"""

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 5.0  # seconds — memory lookups should be fast


async def search_memories(
    wallet: str,
    query: str,
    top_k: int = 5,
    threshold: float = 0.7,
    types: str | None = None,
) -> list[dict[str, Any]]:
    """Search the memory service for relevant past memories.

    Returns a list of memory results sorted by relevance score.
    On failure, returns an empty list (non-blocking).
    """
    url = f"{settings.MEMORY_SERVICE_URL}/memory/search"
    params: dict[str, Any] = {
        "query": query,
        "topK": top_k,
        "threshold": threshold,
    }
    if types:
        params["types"] = types

    headers = {
        "X-User-Wallet": wallet,
        "X-Internal-Api-Key": settings.OPRAI_INTERNAL_API_KEY,
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("results", [])
            logger.warning(
                "Memory search returned %d: %s", resp.status_code, resp.text[:200]
            )
    except httpx.ConnectError:
        logger.debug("Memory service not reachable at %s", settings.MEMORY_SERVICE_URL)
    except Exception:
        logger.warning("Memory search failed", exc_info=True)

    return []


async def store_memory(
    wallet: str,
    memory_type: str,
    summary: str,
    extra: dict[str, Any] | None = None,
) -> str | None:
    """Store a memory point in the memory service.

    Returns the point ID on success, None on failure (non-blocking).
    """
    url = f"{settings.MEMORY_SERVICE_URL}/memory"
    payload: dict[str, Any] = {
        "user_id": wallet,
        "type": memory_type,
        "summary": summary,
    }
    if extra:
        payload.update(extra)

    headers = {
        "X-User-Wallet": wallet,
        "X-Internal-Api-Key": settings.OPRAI_INTERNAL_API_KEY,
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                url, json={"payload": payload}, headers=headers
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                return data.get("id")
            logger.warning(
                "Memory store returned %d: %s", resp.status_code, resp.text[:200]
            )
    except httpx.ConnectError:
        logger.debug("Memory service not reachable at %s", settings.MEMORY_SERVICE_URL)
    except Exception:
        logger.warning("Memory store failed", exc_info=True)

    return None


async def get_consent(wallet: str) -> dict[str, bool] | None:
    """Fetch user consent flags from the memory service.

    Returns consent dict or None if unavailable.
    """
    url = f"{settings.MEMORY_SERVICE_URL}/consent/{wallet}"
    headers = {
        "X-User-Wallet": wallet,
        "X-Internal-Api-Key": settings.OPRAI_INTERNAL_API_KEY,
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                return resp.json()
    except httpx.ConnectError:
        logger.debug("Memory service not reachable at %s", settings.MEMORY_SERVICE_URL)
    except Exception:
        logger.warning("Consent fetch failed", exc_info=True)

    return None
