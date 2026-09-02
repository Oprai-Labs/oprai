"""Bridging into Robinhood Chain (Relay, cross-chain).

The custodial wallet is one key, so the same address exists on every chain —
these tests check the chain registry, that each RPC actually answers, and that
a real cross-chain quote prices.
"""

from __future__ import annotations

import random
import urllib.error
import urllib.request

import pytest

from app.config import settings
from app.db import close_pool, init_pool, pool, upsert_tg_user
from app.services import auth as auth_svc
from app.services import chains
from app.services import evm
from app.services import relay
from app.services import wallet as wallet_svc


def _reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


LIVE = _reachable(f"{settings.OPRAI_TG_SIGNER_URL.rstrip('/')}/health") and _reachable(
    f"{settings.GATEWAY_URL.rstrip('/')}/health"
)


# ── unit: the chain registry ────────────────────────────────────────────────
def test_chains_resolve_by_name_alias_and_id():
    assert chains.resolve("base").id == 8453
    assert chains.resolve("arb").id == 42161
    assert chains.resolve("eth").id == 1
    assert chains.resolve("8453").id == 8453
    assert chains.resolve("Robinhood Chain").id == chains.ROBINHOOD
    assert chains.resolve("nonsense") is None


def test_source_chains_exclude_home():
    """You bridge FROM elsewhere INTO Robinhood, never from Robinhood to itself."""
    assert all(c.id != chains.ROBINHOOD for c in chains.source_chains())
    assert len(chains.source_chains()) == len(chains.CHAINS) - 1


def test_rpc_url_is_env_overridable():
    import os
    original = os.environ.get("BASE_RPC")
    os.environ["BASE_RPC"] = "https://example.invalid"
    try:
        assert chains.resolve("base").rpc == "https://example.invalid"
    finally:
        if original is None:
            del os.environ["BASE_RPC"]
        else:
            os.environ["BASE_RPC"] = original


# ── live ────────────────────────────────────────────────────────────────────
@pytest.fixture
async def db():
    await init_pool()
    yield
    await close_pool()


@pytest.mark.skipif(not LIVE, reason="signer/gateway not reachable")
@pytest.mark.asyncio
async def test_every_chain_rpc_answers(db):
    """A chain we offer to bridge from must actually be readable."""
    tg_id = random.randint(10_000_000_000, 99_999_999_999)
    await upsert_tg_user(tg_id, "pytest-bridge")
    try:
        addr = await wallet_svc.wallet_address(tg_id)
        for c in chains.CHAINS:
            balance = evm.to_int(
                await evm.rpc("eth_getBalance", [addr, "latest"], c.id)
            )
            assert balance >= 0, c.name
    finally:
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg_id)


@pytest.mark.skipif(not LIVE, reason="signer/gateway not reachable")
@pytest.mark.asyncio
async def test_live_cross_chain_quote_base_to_robinhood(db):
    tg_id = random.randint(10_000_000_000, 99_999_999_999)
    await upsert_tg_user(tg_id, "pytest-bridge2")
    try:
        addr = await wallet_svc.wallet_address(tg_id)
        jwt = await auth_svc.get_jwt(tg_id)
        base = chains.resolve("base")
        q = await relay.quote(jwt, relay.build_params(
            origin_currency=relay.NATIVE, destination_currency=relay.NATIVE,
            amount="0.05", origin_chain_id=base.id,
            destination_chain_id=chains.ROBINHOOD, sender=addr, recipient=addr,
        ))
        s = relay.summarize(q)
        assert float(s["out"]["amount"]) > 0
        # a bridge keeps most of the value — a wildly different figure means we
        # priced the wrong pair
        assert 0.9 < float(s["out"]["amount"]) / 0.05 <= 1.0
    finally:
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg_id)
