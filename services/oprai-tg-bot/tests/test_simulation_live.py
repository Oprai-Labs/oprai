"""Do the transactions we build actually execute?

Every other test checks that a card is *built* — the right fields, the right
amounts, the right order. None of them can tell whether the chain would accept
it. A transaction that decodes perfectly and reverts on submit is the worst
outcome we ship: the user tapped Confirm, paid gas, and got nothing.

So these run the built calldata on the real chain with `eth_call`, as our own
wallet, with a pretend balance supplied through state overrides. Nothing is
signed and nothing is written — but the contract executes for real, so a revert
here is a card that would have failed in someone's hands.

This is also how the hand-written Seaport encoder is checked end to end:
decoding it back proves we wrote what we meant, and only running it proves
Seaport agrees.
"""

from __future__ import annotations

import random
import urllib.error
import urllib.request

import pytest

from app.config import settings
from app.db import close_pool, init_pool, pool, upsert_tg_user
from app.services import auth as auth_svc
from app.services import evm, opensea, sushi
from app.services import wallet as wallet_svc

# Enough to cover any quote below; the wallet is empty in reality.
PRETEND_BALANCE = hex(100 * 10**18)


def _reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


LIVE = _reachable(f"{settings.OPRAI_TG_SIGNER_URL.rstrip('/')}/health") and _reachable(
    f"{settings.GATEWAY_URL.rstrip('/')}/health"
)


async def _node_supports_overrides() -> bool:
    """Not every node accepts eth_call's third parameter. Without it these
    tests would silently check nothing, so they skip loudly instead."""
    dead = "0x000000000000000000000000000000000000dEaD"
    try:
        await evm.rpc(
            "eth_call",
            [{"from": dead, "to": dead, "value": "0x1"}, "latest",
             {dead: {"balance": PRETEND_BALANCE}}],
        )
        return True
    except evm.EvmError:
        return False


async def _executes(address: str, tx: dict) -> tuple[bool, str]:
    call = {"from": address, "to": tx["to"], "data": tx["data"]}
    value = evm.to_int(tx.get("value"))
    if value:
        call["value"] = hex(value)
    try:
        await evm.rpc(
            "eth_call", [call, "latest", {address: {"balance": PRETEND_BALANCE}}]
        )
        return True, ""
    except evm.EvmError as e:
        return False, str(e)[:200]


@pytest.mark.skipif(not LIVE, reason="gateway/signer not running")
@pytest.mark.asyncio
async def test_a_built_swap_executes_on_chain():
    await init_pool()
    tg = random.randint(10**10, 10**11)
    await upsert_tg_user(tg, "sim_swap")
    try:
        if not await _node_supports_overrides():
            pytest.skip("node does not support eth_call state overrides")
        address = await wallet_svc.wallet_address(tg)
        jwt = await auth_svc.get_jwt(tg)

        built = await sushi.swap(
            jwt, wallet=address, token_in="eth", token_out="usdg", amount=0.05
        )
        # Native input needs no allowance, so the swap is the only step and it
        # can be run on its own.
        assert sushi.transaction_count(built) == 1
        ok, err = await _executes(address, built["transactions"][0])
        assert ok, f"the swap we would have signed reverts: {err}"
    finally:
        await pool().execute("DELETE FROM tg_wallets WHERE telegram_id = $1", tg)
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg)
        await close_pool()


@pytest.mark.skipif(not LIVE, reason="gateway/signer not running")
@pytest.mark.asyncio
async def test_a_built_nft_purchase_executes_on_chain():
    """The Seaport calldata is hand-encoded — three nested dynamic tuples with
    no ABI library. Decoding it back proves we wrote what we meant; only
    running it proves Seaport agrees."""
    await init_pool()
    tg = random.randint(10**10, 10**11)
    await upsert_tg_user(tg, "sim_nft")
    try:
        if not await _node_supports_overrides():
            pytest.skip("node does not support eth_call state overrides")
        address = await wallet_svc.wallet_address(tg)
        jwt = await auth_svc.get_jwt(tg)

        checked = 0
        for collection in await opensea.trending(jwt, limit=4):
            for row in await opensea.listings(jwt, collection["slug"], limit=3):
                try:
                    built = await opensea.build_buy(
                        jwt, address, row["orderHash"], row.get("protocolAddress")
                    )
                except opensea.OpenSeaError:
                    continue  # not buyable in-app; the UI never offers it
                # An ERC-20-priced listing needs its approval mined first, so
                # only a single-step (native) purchase can be run standalone.
                if len(built["transactions"]) != 1:
                    continue
                ok, err = await _executes(address, built["transactions"][0])
                assert ok, f"buying #{row.get('tokenId')} reverts: {err}"
                checked += 1
                if checked >= 2:
                    return
        if checked == 0:
            pytest.skip("no single-step listing available to simulate right now")
    finally:
        await pool().execute("DELETE FROM tg_wallets WHERE telegram_id = $1", tg)
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg)
        await close_pool()
