"""Relay swap path: quote (live) + step execution semantics (unit).

The unit tests here encode the trap the web client falls into: Relay returns a
LIST of steps and an ERC-20 input puts an approval first, so a client that signs
only the first item approves the token and never trades it.
"""

from __future__ import annotations

import random
import urllib.error
import urllib.request

import pytest

from app.config import settings
from app.db import close_pool, init_pool, pool, upsert_tg_user
from app.services import auth as auth_svc
from app.services import relay
from app.services import tokens as tok
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


# ── unit: step handling ─────────────────────────────────────────────────────
APPROVE_THEN_SWAP = [
    {"type": "approve", "items": [{"data": {"to": "0xToken", "data": "0x095ea7b3",
                                            "value": "0", "gas": "60000"}}]},
    {"type": "deposit", "items": [{"data": {"to": "0xRouter", "data": "0xdeadbeef",
                                            "value": "10000000000000000",
                                            "gas": "250000",
                                            "maxFeePerGas": "1500000000",
                                            "maxPriorityFeePerGas": "100000"}}]},
]


def test_every_step_counts_not_just_the_first():
    """An ERC-20 swap is approve + trade — both must be executed."""
    assert relay.count_transactions(APPROVE_THEN_SWAP) == 2


def test_signature_only_steps_are_skipped():
    steps = [{"type": "sign", "items": [{"data": {}}]}] + APPROVE_THEN_SWAP
    assert relay.count_transactions(steps) == 2


def test_provider_amounts_parse_as_decimal_or_hex():
    """Providers mix decimal strings, 0x-hex and ints in the same fields."""
    from app.services import evm
    assert evm.to_int("10000000000000000") == 10**16
    assert evm.to_int("0x5208") == 21000
    assert evm.to_int(21000) == 21000
    assert evm.to_int("") == 0 and evm.to_int(None) == 0


def test_native_sentinel_is_the_zero_address():
    """0xEeee… is NOT special-cased upstream and would fail the lookup."""
    assert relay.NATIVE == "0x0000000000000000000000000000000000000000"


# ── live: a real quote through the gateway ──────────────────────────────────
@pytest.fixture
async def db():
    await init_pool()
    yield
    await close_pool()


@pytest.mark.skipif(not LIVE, reason="signer/gateway not reachable")
@pytest.mark.asyncio
async def test_live_quote_eth_to_usdg(db):
    tg_id = random.randint(10_000_000_000, 99_999_999_999)
    await upsert_tg_user(tg_id, "pytest-relay")
    try:
        addr = await wallet_svc.wallet_address(tg_id)
        jwt = await auth_svc.get_jwt(tg_id)
        usdg = (await tok.resolve("USDG"))[0]["address"]
        params = relay.build_params(
            origin_currency=relay.NATIVE, destination_currency=usdg,
            amount="0.01", sender=addr, recipient=addr,
        )
        q = await relay.quote(jwt, params)
        s = relay.summarize(q)
        assert s["in"]["symbol"] == "ETH"
        assert s["out"]["symbol"] == "USDG"
        assert float(s["out"]["amount"]) > 0
    finally:
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg_id)
