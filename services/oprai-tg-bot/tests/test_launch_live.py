"""Launching a token on Robinhood Chain (Pons).

The live test builds a real launch transaction — factory, fee and calldata come
from the chain — without signing or broadcasting anything.
"""

from __future__ import annotations

import random
import urllib.error
import urllib.request

import pytest

from app.config import settings
from app.db import close_pool, init_pool, pool, upsert_tg_user
from app.services import auth as auth_svc
from app.services import evm, launch
from app.services import wallet as wallet_svc

PONS_V2_FACTORY = "0x7eD598BcEf8bd9Edd8C97A195C6d13f40801EC7e"


def _reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


LIVE = _reachable(f"{settings.OPRAI_TG_SIGNER_URL.rstrip('/')}/health") and _reachable(
    f"{settings.GATEWAY_URL.rstrip('/')}/health"
)


def test_mint_is_recognised_as_a_transfer_from_zero():
    """That is how we learn the new token's address from the receipt."""
    assert launch.ZERO_TOPIC == "0x" + "0" * 64
    assert launch.TRANSFER_TOPIC.startswith("0xddf252ad")


@pytest.fixture
async def db():
    await init_pool()
    yield
    await close_pool()


@pytest.mark.skipif(not LIVE, reason="signer/gateway not reachable")
@pytest.mark.asyncio
async def test_live_launch_builds_a_real_transaction(db):
    tg_id = random.randint(10_000_000_000, 99_999_999_999)
    await upsert_tg_user(tg_id, "pytest-launch")
    try:
        addr = await wallet_svc.wallet_address(tg_id)
        jwt = await auth_svc.get_jwt(tg_id)

        res = await launch.pons_launch(
            jwt, name="OPRAI Test Token", symbol="OPRTEST", wallet=addr
        )
        txs = res["transactions"]
        assert len(txs) == 1, "a Pons launch is a single transaction"
        tx = txs[0]
        assert tx["to"].lower() == PONS_V2_FACTORY.lower()
        assert tx["chainId"] == evm.CHAIN_ID
        assert int(tx["value"]) == int(res["launchFeeWei"]) > 0
        assert len(tx["data"]) > 200, "calldata should carry the token params"
    finally:
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg_id)


@pytest.mark.skipif(not LIVE, reason="signer/gateway not reachable")
@pytest.mark.asyncio
async def test_unaffordable_launch_says_so_in_plain_words(db):
    """A node won't estimate gas for a transaction the sender can't afford, and
    that refusal must not reach the user as raw RPC text."""
    tg_id = random.randint(10_000_000_000, 99_999_999_999)
    await upsert_tg_user(tg_id, "pytest-launch-poor")
    try:
        addr = await wallet_svc.wallet_address(tg_id)  # empty wallet
        jwt = await auth_svc.get_jwt(tg_id)
        res = await launch.pons_launch(
            jwt, name="OPRAI Test Token", symbol="OPRTEST", wallet=addr
        )
        with pytest.raises(evm.EvmError) as excinfo:
            await evm.build_tx_from_provider(addr, res["transactions"][0])
        message = str(excinfo.value)
        assert "insufficient funds for gas" not in message.lower()
        assert "eth" in message.lower()
    finally:
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg_id)
