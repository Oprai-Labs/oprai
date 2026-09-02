"""HTTP client for the chain-intel real-time signal endpoints (the alpha feed).

The heavy lifting — detecting smart-money buys, tracked-wallet activity and fresh
launches off the live Robinhood-Chain index — lives in chain-intel (`/signals/*`).
The bot only POLLS these (keyed by `since_block` so it's idempotent and gap-free)
and turns the results into Telegram alerts. Internal service, X-Internal-Api-Key
gated, reached over the docker network (rh-chain-intel-api:3160)."""
from __future__ import annotations

import httpx

try:  # importable standalone (tests) even without the bot's settings machinery
    from app.config import settings
except Exception:  # pragma: no cover
    settings = None


class SignalsError(RuntimeError):
    pass


class SignalsClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 timeout: float = 12.0) -> None:
        self._base = (base_url or getattr(settings, "CHAIN_INTEL_URL", "")
                      or "http://rh-chain-intel-api:3160").rstrip("/")
        self._key = (api_key if api_key is not None
                     else getattr(settings, "OPRAI_INTERNAL_API_KEY", ""))
        self._timeout = timeout

    def _headers(self) -> dict:
        return {"X-Internal-Api-Key": self._key} if self._key else {}

    async def _get(self, path: str, params: dict | None = None) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                r = await c.get(f"{self._base}{path}", headers=self._headers(),
                                params=params or {})
        except httpx.HTTPError as e:
            raise SignalsError(f"chain-intel unreachable: {e}") from e
        if r.status_code != 200:
            raise SignalsError(f"chain-intel {path} → {r.status_code}: {r.text[:200]}")
        return r.json()

    async def tip(self) -> int:
        """Current index cursor ceiling — seed a subscription's since_block here."""
        return int((await self._get("/signals/tip")).get("tip") or 0)

    async def smart_buys(self, since_block: int, min_smart: int = 2,
                         limit: int = 30) -> dict:
        """Discovery feed: tokens smart wallets bought since `since_block`."""
        return await self._get("/signals/smart-buys", {
            "since_block": since_block, "min_smart": min_smart, "limit": limit})

    async def new_launches(self, since_block: int, with_smart_only: bool = False,
                           limit: int = 40) -> dict:
        return await self._get("/signals/new-launches", {
            "since_block": since_block,
            "with_smart_only": str(with_smart_only).lower(), "limit": limit})

    async def wallet_recent_buys(self, wallet: str, since_block: int,
                                 limit: int = 20) -> dict:
        """A tracked wallet's buys since `since_block`."""
        return await self._get(f"/wallet/{wallet}/recent-buys", {
            "since_block": since_block, "limit": limit})

    async def token_report(self, token: str) -> dict:
        """Full token X-ray (for the Analyze button) — {subject,status,kpis,facts,…}."""
        return await self._get(f"/token/{token}")
