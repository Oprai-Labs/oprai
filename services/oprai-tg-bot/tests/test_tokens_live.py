"""Token registry + ERC-20 transfer path (Robinhood Chain).

Needs Postgres with a synced tg_token_registry and a reachable Robinhood RPC.
"""

from __future__ import annotations

import random
import urllib.error
import urllib.request

import pytest

from app.config import settings
from app.db import close_pool, init_pool, pool, upsert_tg_user
from app.services import evm
from app.services import tokens as tok
from app.services import wallet as wallet_svc


def _reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


LIVE = _reachable(f"{settings.OPRAI_TG_SIGNER_URL.rstrip('/')}/health")
pytestmark = pytest.mark.skipif(not LIVE, reason="signer/chain not reachable")


@pytest.fixture
async def db():
    await init_pool()
    yield
    await close_pool()


@pytest.mark.asyncio
async def test_registry_resolves_symbol_name_and_address(db):
    if await tok.registry_size() == 0:
        pytest.skip("registry not seeded — run sync_registry() first")

    for query in ("NVDA", "$nvda", "nvidia"):
        m = await tok.resolve(query)
        assert m and m[0]["symbol"] == "NVDA", query
        nvda = m[0]

    # the same token by raw address
    by_addr = await tok.resolve(nvda["address"])
    assert by_addr and by_addr[0]["address"].lower() == nvda["address"].lower()

    # a name fragment
    tesla = await tok.resolve("tesla")
    assert tesla and tesla[0]["symbol"] == "TSLA"

    # nonsense resolves to nothing (the handler then refuses)
    assert await tok.resolve("definitelynotatokenxyz") == []


@pytest.mark.asyncio
async def test_decimals_come_from_chain_not_assumed(db):
    """USDG is 6 decimals; assuming 18 would send 10^12x the intended amount."""
    if await tok.registry_size() == 0:
        pytest.skip("registry not seeded")
    usdg = await tok.resolve("USDG")
    assert usdg and usdg[0]["decimals"] == 6

    weth = await tok.resolve("WETH")
    assert weth and weth[0]["decimals"] == 18


@pytest.mark.asyncio
async def test_erc20_transfer_is_built_against_the_token_contract(db):
    """An ERC-20 send targets the TOKEN with calldata and zero native value."""
    if await tok.registry_size() == 0:
        pytest.skip("registry not seeded")
    tg_id = random.randint(10_000_000_000, 99_999_999_999)
    await upsert_tg_user(tg_id, "pytest-tok")
    try:
        addr = await wallet_svc.wallet_address(tg_id)
        nvda = (await tok.resolve("NVDA"))[0]

        # a fresh wallet holds none of it — the handler refuses before building
        assert await tok.token_balance(nvda["address"], addr) == 0

        recipient = "0x000000000000000000000000000000000000dEaD"
        units = 5 * 10 ** nvda["decimals"]
        data = evm.encode_erc20_transfer(recipient, units)
        assert data.startswith("0xa9059cbb")
        assert int(data[74:], 16) == units

        # gas estimation on a token transfer the sender can't afford must fail
        # loudly rather than silently falling back to the 21k native figure.
        with pytest.raises(evm.EvmError):
            await evm.estimate_gas(addr, nvda["address"], 0, data)
    finally:
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg_id)


# ── finding a token ─────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_token_can_be_found_however_someone_names_it():
    """Symbol, case, a dollar sign, part of the name, or the address — people
    do all of these, and any one failing is a swap that can't be started."""
    await init_pool()
    try:
        for query, expected in (
            ("NVDA", "NVDA"), ("nvda", "NVDA"), ("$NVDA", "NVDA"),
            ("nvidia", "NVDA"), ("tesla", "TSLA"),
            ("USDe", "USDe"), ("syrupUSDG", "syrupUSDG"),
            ("0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34", "USDe"),
        ):
            found = await tok.resolve(query)
            assert found, f"{query!r} found nothing"
            assert found[0]["symbol"] == expected, f"{query!r} -> {found[0]['symbol']}"
    finally:
        await close_pool()


@pytest.mark.asyncio
async def test_an_address_we_have_never_seen_is_read_from_the_chain():
    """The registry is seeded from lists, so anything newly launched is
    unknown to it. The chain always knows."""
    await init_pool()
    try:
        # Morpho's singleton is a contract with no symbol(), so it must NOT
        # come back as a token — an unreadable address is not a token.
        assert not await tok.resolve("0x9D53d5E3bd5E8d4Cbfa6DB1ca238AEA02E651010")
        # Nothing plausible either.
        assert not await tok.resolve("zzzzz")
    finally:
        await close_pool()


@pytest.mark.asyncio
async def test_every_collateral_a_market_accepts_is_searchable_by_name():
    """"Borrow against syrupUSDG" is how someone says it — by name, not by
    address. Resolvable-by-address alone is not findable."""
    import random

    from app.db import upsert_tg_user
    from app.services import auth as auth_svc
    from app.services import morpho

    await init_pool()
    tg = random.randint(10**10, 10**11)
    await upsert_tg_user(tg, "collateral_search")
    try:
        jwt = await auth_svc.get_jwt(tg)
        for market in await morpho.markets(jwt):
            symbol = market["collateralSymbol"]
            assert await tok.resolve(symbol), f"{symbol} is not searchable by name"
    finally:
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg)
        await close_pool()
