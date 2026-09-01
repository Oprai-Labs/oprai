"""Auth-on-behalf: obtain a real gateway JWT for a user's custodial wallet.

Flow (no gateway changes needed): nonce -> build the SIWS/SIWE message ->
sign it with the signer (the bot never sees the key) -> verify -> capture the
JWT from the Set-Cookie header. Tokens are cached per (telegram_id, chain)
until shortly before expiry.
"""

from __future__ import annotations

import datetime as dt
import re
import time

from app.config import settings
from app.gateway_client import GatewayError, gateway
from app.services import wallet as wallet_svc
from app.signer_client import SignerError, signer

# (telegram_id, chain) -> (jwt, epoch_expiry)
_cache: dict[tuple[int, str], tuple[str, float]] = {}
_REFRESH_SKEW = 60  # refresh a minute before expiry

_COOKIE_RE = re.compile(r"oprai-auth-token=([^;]+)")

_CHAIN_LABEL = {"solana": "Solana", "evm": "Ethereum"}
# The verify endpoint treats anything not in {ethereum,evm,eip155} as Solana.
_VERIFY_CHAIN = {"solana": "solana", "evm": "ethereum"}


class AuthError(RuntimeError):
    pass


def _build_message(chain: str, address: str, nonce: str) -> str:
    domain = settings.OPRAI_TG_APP_DOMAIN or "localhost"
    label = _CHAIN_LABEL.get(chain, "Solana")
    issued = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # First line is the domain claim the auth-service prefix-checks (prod); the
    # "Nonce: <nonce>" line is the challenge binding it always checks.
    return (
        f"{domain} wants you to sign in with your {label} account:\n"
        f"{address}\n\n"
        f"Sign in to OPRAI.\n\n"
        f"URI: https://{domain}\n"
        f"Version: 1\n"
        f"Chain ID: {chain}\n"
        f"Nonce: {nonce}\n"
        f"Issued At: {issued}"
    )


async def authenticate(telegram_id: int, chain: str) -> str:
    """Run the full SIWS/SIWE flow and return a fresh JWT for the wallet."""
    w = await wallet_svc.get_or_create_wallet(telegram_id, chain)
    address, enc_key_ref = w["address"], w["enc_key_ref"]

    # 1) nonce
    r = await gateway.post("/auth/nonce", {})
    if r.status_code != 200:
        raise AuthError(f"nonce failed ({r.status_code})")
    body = r.json()
    nonce, nonce_id = body["nonce"], body["nonceId"]

    # 2) build + sign message (key stays in the signer)
    message = _build_message(chain, address, nonce)
    try:
        signed = await signer.sign(chain, enc_key_ref, message)
    except SignerError as e:
        raise AuthError(f"signer failed: {e}") from e
    signature = signed["signature"]

    # 3) verify -> JWT arrives only as a Set-Cookie
    v = await gateway.post(
        "/auth/verify",
        {
            "walletAddress": address,
            "signature": signature,
            "nonceId": nonce_id,
            "chain": _VERIFY_CHAIN.get(chain, "solana"),
            "message": message,
        },
    )
    if v.status_code != 200:
        raise AuthError(f"verify failed ({v.status_code}): {v.text[:200]}")

    jwt = _extract_jwt(v)
    if not jwt:
        raise AuthError("verify succeeded but no auth cookie returned")

    expiry = _expiry_epoch(v)
    _cache[(telegram_id, chain)] = (jwt, expiry)
    return jwt


def _extract_jwt(resp) -> str | None:
    # httpx exposes parsed cookies; fall back to raw header parsing.
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
        # tolerate trailing Z
        return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except Exception:
        return time.time() + 3600


async def get_jwt(telegram_id: int, chain: str = "solana") -> str:
    """Return a cached JWT if still valid, else authenticate fresh."""
    cached = _cache.get((telegram_id, chain))
    if cached and cached[1] - _REFRESH_SKEW > time.time():
        return cached[0]
    try:
        return await authenticate(telegram_id, chain)
    except (GatewayError, SignerError) as e:
        raise AuthError(str(e)) from e
