"""HTTP client for the isolated Rust signer (oprai-tg-signer).

The bot never sees a private key: it asks the signer to create/import a key
(getting back an address + an opaque Vault-ciphertext handle) and to sign
messages (passing the handle back). If the signer is down or Vault is
unreachable, calls raise — the bot must fail closed, never fake a wallet.
"""

from __future__ import annotations

import httpx

from app.config import settings


class SignerError(RuntimeError):
    pass


class SignerClient:
    def __init__(self, base_url: str | None = None, timeout: float = 10.0) -> None:
        self._base = (base_url or settings.OPRAI_TG_SIGNER_URL).rstrip("/")
        self._timeout = timeout

    async def _post(self, path: str, payload: dict) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                r = await c.post(f"{self._base}{path}", json=payload)
        except httpx.HTTPError as e:
            raise SignerError(f"signer unreachable: {e}") from e
        if r.status_code != 200:
            detail = _err(r)
            raise SignerError(f"signer {path} failed ({r.status_code}): {detail}")
        return r.json()

    async def create_wallet(self, chain: str) -> dict:
        """-> {address, enc_key_ref}"""
        return await self._post("/wallet/create", {"chain": chain})

    async def import_wallet(self, chain: str, secret: str) -> dict:
        """-> {address, enc_key_ref}"""
        return await self._post(
            "/wallet/import", {"chain": chain, "secret": secret}
        )

    async def sign(self, chain: str, enc_key_ref: str, message: str) -> dict:
        """-> {address, signature}"""
        return await self._post(
            "/sign",
            {"chain": chain, "enc_key_ref": enc_key_ref, "message": message},
        )

    async def health(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                r = await c.get(f"{self._base}/health")
            return r.json()
        except httpx.HTTPError as e:
            raise SignerError(f"signer unreachable: {e}") from e


def _err(r: httpx.Response) -> str:
    try:
        return r.json().get("error", r.text)
    except Exception:
        return r.text


signer = SignerClient()
