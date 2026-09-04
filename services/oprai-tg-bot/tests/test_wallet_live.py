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


# ── lifecycle ───────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_new_wallet_never_strands_the_old_one():
    """A wallet row used to be REPLACED — by /wallet import, and by anything
    else that made a new one. That discarded the only copy of the old key, so
    whatever the old address still held became unreachable at that moment."""
    await init_pool()
    tg = random.randint(10**10, 10**11)
    await upsert_tg_user(tg, "lifecycle")
    try:
        first = await wallet_svc.get_or_create_wallet(tg)
        second = await wallet_svc.new_wallet(tg)
        assert first["address"] != second["address"]

        rows = await wallet_svc.list_wallets(tg)
        assert len(rows) == 2, "the old wallet was destroyed, not archived"
        active = [r for r in rows if r["archived_at"] is None]
        assert len(active) == 1 and active[0]["address"] == second["address"]

        # The point of keeping it: the old key is still recoverable.
        old = await wallet_svc.export_secret(tg, first["address"])
        assert old["address"].lower() == first["address"].lower()
        assert old["secret"].startswith("0x") and len(old["secret"]) == 66
    finally:
        await pool().execute("DELETE FROM tg_wallets WHERE telegram_id = $1", tg)
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg)
        await close_pool()


@pytest.mark.asyncio
async def test_an_exported_key_restores_the_same_wallet():
    """An export that decodes to a different address sends someone to an empty
    wallet with no way back."""
    await init_pool()
    tg = random.randint(10**10, 10**11)
    await upsert_tg_user(tg, "export_rt")
    try:
        original = await wallet_svc.get_or_create_wallet(tg)
        exported = await wallet_svc.export_secret(tg)
        assert exported["address"].lower() == original["address"].lower()

        restored = await wallet_svc.import_wallet(tg, exported["secret"])
        assert restored["address"].lower() == original["address"].lower()
    finally:
        await pool().execute("DELETE FROM tg_wallets WHERE telegram_id = $1", tg)
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg)
        await close_pool()


@pytest.mark.asyncio
async def test_importing_archives_rather_than_overwrites():
    """The original bug: importing your own key silently discarded the wallet
    the bot had made for you, along with anything in it."""
    await init_pool()
    tg = random.randint(10**10, 10**11)
    await upsert_tg_user(tg, "import_archive")
    try:
        made_for_them = await wallet_svc.get_or_create_wallet(tg)
        spare = await wallet_svc.export_secret(tg)  # a key we can re-import

        await wallet_svc.new_wallet(tg)             # now theirs is archived
        await wallet_svc.import_wallet(tg, spare["secret"])

        rows = await wallet_svc.list_wallets(tg)
        addresses = {r["address"].lower() for r in rows}
        assert made_for_them["address"].lower() in addresses, "the first wallet vanished"
        assert len([r for r in rows if r["archived_at"] is None]) == 1
    finally:
        await pool().execute("DELETE FROM tg_wallets WHERE telegram_id = $1", tg)
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg)
        await close_pool()
