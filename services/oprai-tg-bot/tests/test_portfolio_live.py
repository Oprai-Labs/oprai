"""Live test for read-only balances (0.5b).

Needs the signer (to create the wallet) + Postgres + a reachable SOLANA_RPC.
A fresh custodial wallet has 0 SOL, so we assert the call returns a numeric
balance (the RPC path works), not a specific amount.

Run: cd services/oprai-tg-bot && .venv/bin/pytest tests/test_portfolio_live.py -v
"""

from __future__ import annotations

import random
import urllib.error
import urllib.request

import pytest

from app.config import settings
from app.db import close_pool, init_pool, pool, upsert_tg_user
from app.services import portfolio as pf


def _reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


if not _reachable(f"{settings.OPRAI_TG_SIGNER_URL.rstrip('/')}/health"):
    pytest.skip("signer not reachable — live portfolio test skipped", allow_module_level=True)


@pytest.fixture
async def db():
    await init_pool()
    yield
    await close_pool()


@pytest.mark.asyncio
async def test_solana_native_balance(db):
    tg_id = random.randint(10_000_000_000, 99_999_999_999)
    await upsert_tg_user(tg_id, "pytest-pf")
    try:
        bal = await pf.solana_balance(tg_id)
        assert bal["address"]
        assert isinstance(bal["lamports"], int) and bal["lamports"] >= 0
        assert isinstance(bal["sol"], float) and bal["sol"] >= 0.0
        # lamports/SOL consistency
        assert abs(bal["sol"] * pf.LAMPORTS_PER_SOL - bal["lamports"]) < 1
    finally:
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg_id)
