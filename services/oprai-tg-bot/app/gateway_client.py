"""HTTP client for the API gateway.

The bot talks to the gateway exactly like any other client: it presents a JWT
(obtained on-behalf via SIWS/SIWE — see services/auth.py) as a Bearer token,
and includes the CSRF header the gateway requires on state-changing calls.
"""

from __future__ import annotations

import httpx

from app.config import settings


class GatewayError(RuntimeError):
    pass


# The gateway rejects state-changing requests without this CSRF header.
_CSRF = {"X-Requested-With": "XMLHttpRequest"}


class GatewayClient:
    def __init__(self, base_url: str | None = None, timeout: float = 15.0) -> None:
        self._base = (base_url or settings.GATEWAY_URL).rstrip("/")
        self._timeout = timeout

    def _headers(self, jwt: str | None) -> dict:
        h = dict(_CSRF)
        if jwt:
            h["Authorization"] = f"Bearer {jwt}"
        return h

    async def post(self, path: str, json: dict, jwt: str | None = None) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                return await c.post(
                    f"{self._base}{path}", json=json, headers=self._headers(jwt)
                )
        except httpx.HTTPError as e:
            raise GatewayError(f"gateway unreachable: {e}") from e

    async def get(self, path: str, jwt: str | None = None, params: dict | None = None) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                return await c.get(
                    f"{self._base}{path}", headers=self._headers(jwt), params=params
                )
        except httpx.HTTPError as e:
            raise GatewayError(f"gateway unreachable: {e}") from e


gateway = GatewayClient()
