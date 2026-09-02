"""Live integration test for the custodial wallet flow (Robinhood Chain).

Requires a running signer (Vault-connected) + Postgres with tg_schema. Verifies
signer create -> tg_wallets row -> idempotent re-create -> the stored ciphertext
signs and round-trips to the same address.

Run: cd services/oprai-tg-bot && .venv/bin/pytest tests/test_wallet_live.py -v
"""

from __future__ import annotations

import random
import urllib.error
import urllib.request

import pytest

from app.config import settings
from app.db import close_pool, init_pool, pool, upsert_tg_user
from app.services import wallet as wallet_svc
from app.signer_client import signer


def _signer_reachable() -> bool:
    try:
        with urllib.request.urlopen(
            f"{settings.OPRAI_TG_SIGNER_URL.rstrip('/')}/health", timeout=2
        ) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


if not _signer_reachable():
    pytest.skip("signer not reachable — live integration test skipped", allow_module_level=True)


@pytest.fixture
async def db():
    await init_pool()
    yield
    await close_pool()


@pytest.mark.asyncio
async def test_signer_healthy():
    h = await signer.health()
    assert h.get("status") == "ok"
    assert h.get("vault") == "connected", f"Vault not connected: {h}"


@pytest.mark.asyncio
async def test_create_persist_idempotent_and_sign(db):
    tg_id = random.randint(10_000_000_000, 99_999_999_999)
    await upsert_tg_user(tg_id, "pytest")
    try:
        row = await wallet_svc.get_or_create_wallet(tg_id)
        assert row["chain"] == "evm"
        assert row["address"].startswith("0x") and len(row["address"]) == 42
        assert row["enc_key_ref"].startswith("vault:v1:")

        # idempotent: re-create returns the SAME address (no new key)
        again = await wallet_svc.get_or_create_wallet(tg_id)
        assert again["address"] == row["address"]

        # the stored ciphertext signs and round-trips to the same address
        signed = await signer.sign("evm", row["enc_key_ref"], "oprai wallet test")
        assert signed["address"] == row["address"]
    finally:
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg_id)
