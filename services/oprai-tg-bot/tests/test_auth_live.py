"""Live integration test for auth-on-behalf (0.4).

Requires signer + gateway + auth-service + Postgres all running. Verifies the
bot can obtain a real JWT for a custodial wallet (SIWS and SIWE) and that the
gateway recognises the session as that exact wallet.

Run: cd services/oprai-tg-bot && .venv/bin/pytest tests/test_auth_live.py -v
"""

from __future__ import annotations

import random
import urllib.error
import urllib.request

import pytest

from app.config import settings
from app.db import close_pool, init_pool, pool, upsert_tg_user
from app.gateway_client import gateway
from app.services import auth as auth_svc
from app.services import wallet as wallet_svc


def _reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


if not _reachable(f"{settings.OPRAI_TG_SIGNER_URL.rstrip('/')}/health") or not _reachable(
    f"{settings.GATEWAY_URL.rstrip('/')}/health"
):
    pytest.skip(
        "signer or gateway not reachable — live auth test skipped",
        allow_module_level=True,
    )


@pytest.fixture
async def db():
    await init_pool()
    yield
    await close_pool()


@pytest.mark.parametrize("chain", ["solana", "evm"])
@pytest.mark.asyncio
async def test_auth_on_behalf(db, chain):
    tg_id = random.randint(10_000_000_000, 99_999_999_999)
    await upsert_tg_user(tg_id, "pytest-auth")
    try:
        addr = (await wallet_svc.get_or_create_wallet(tg_id, chain))["address"]

        jwt = await auth_svc.get_jwt(tg_id, chain)
        assert jwt and jwt.count(".") == 2, "expected a JWT"

        # the gateway must recognise the session as OUR wallet
        r = await gateway.get("/auth/session", jwt=jwt)
        assert r.status_code == 200
        session = r.json()
        assert session.get("authenticated") is True
        assert session.get("wallet", "").lower() == addr.lower()

        # cached on second call (same token object)
        jwt2 = await auth_svc.get_jwt(tg_id, chain)
        assert jwt2 == jwt
    finally:
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg_id)
