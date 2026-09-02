"""Auth-on-behalf: obtain a real gateway JWT for the user's Robinhood wallet.

Robinhood Chain is EVM, so auth is SIWE (EIP-4361, secp256k1). Flow (no gateway
changes): nonce -> build the domain-bound SIWE message -> sign it in the signer
(the bot never sees the key) -> verify -> capture the JWT from the Set-Cookie.
Tokens are cached per telegram_id until shortly before expiry.
"""

from __future__ import annotations

import datetime as dt
import re
import time

from app.config import settings
from app.gateway_client import GatewayError, gateway
from app.services import wallet as wallet_svc
from app.signer_client import SignerError, signer

# telegram_id -> (jwt, epoch_expiry)
_cache: dict[int, tuple[str, float]] = {}
_REFRESH_SKEW = 60  # refresh a minute before expiry
_COOKIE_RE = re.compile(r"oprai-auth-token=([^;]+)")

ROBINHOOD_CHAIN_ID = 4663
CHAIN = "evm"  # signer scheme; verify endpoint treats evm/ethereum as EVM


class AuthError(RuntimeError):
    pass


def _build_message(address: str, nonce: str) -> str:
    domain = settings.OPRAI_TG_APP_DOMAIN or "localhost"
    issued = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # First line is the domain claim the auth-service prefix-checks (prod); the
    # "Nonce: <nonce>" line is the challenge binding it always checks.
    return (
        f"{domain} wants you to sign in with your Ethereum account:\n"
        f"{address}\n\n"
        f"Sign in to OPRAI on Robinhood Chain.\n\n"
        f"URI: https://{domain}\n"
        f"Version: 1\n"
        f"Chain ID: {ROBINHOOD_CHAIN_ID}\n"
        f"Nonce: {nonce}\n"
        f"Issued At: {issued}"
    )


async def authenticate(telegram_id: int) -> str:
    """Run the SIWE flow and return a fresh JWT for the Robinhood wallet."""
    w = await wallet_svc.get_or_create_wallet(telegram_id)
    address, enc_key_ref = w["address"], w["enc_key_ref"]

    r = await gateway.post("/auth/nonce", {})
    if r.status_code != 200:
        raise AuthError(f"nonce failed ({r.status_code})")
    body = r.json()
    nonce, nonce_id = body["nonce"], body["nonceId"]

    message = _build_message(address, nonce)
    try:
        signed = await signer.sign(CHAIN, enc_key_ref, message)
    except SignerError as e:
        raise AuthError(f"signer failed: {e}") from e

    v = await gateway.post(
        "/auth/verify",
        {
            "walletAddress": address,
            "signature": signed["signature"],
            "nonceId": nonce_id,
            "chain": "ethereum",
            "message": message,
        },
    )
    if v.status_code != 200:
        raise AuthError(f"verify failed ({v.status_code}): {v.text[:200]}")

    jwt = _extract_jwt(v)
    if not jwt:
        raise AuthError("verify succeeded but no auth cookie returned")

    _cache[telegram_id] = (jwt, _expiry_epoch(v))
    return jwt


def _extract_jwt(resp) -> str | None:
    tok = resp.cookies.get("oprai-auth-token")
    if tok:
        return tok
    for h in resp.headers.get_list("set-cookie"):
        m = _COOKIE_RE.search(h)
        if m:
            return m.group(1)
    return None


def _expiry_epoch(resp) -> float:
    try:
        iso = resp.json().get("expiresAt", "")
        return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except Exception:
        return time.time() + 3600


async def get_jwt(telegram_id: int) -> str:
    """Return a cached JWT if still valid, else authenticate fresh."""
    cached = _cache.get(telegram_id)
    if cached and cached[1] - _REFRESH_SKEW > time.time():
        return cached[0]
    try:
        return await authenticate(telegram_id)
    except (GatewayError, SignerError) as e:
        raise AuthError(str(e)) from e
