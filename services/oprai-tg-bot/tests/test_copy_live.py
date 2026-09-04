"""Copy-trading: the risk layer, and the parts that were never connected.

Auto-execution spends someone's money with nobody watching, so the tests are
about refusing: a cap that can't be checked, a size below the floor, a base
asset that isn't really a buy. The engine's `decide()` is pure, so every guard
is checkable without touching a chain.
"""

from __future__ import annotations

import random
import urllib.error
import urllib.request

import pytest

from app.config import settings
from app.db import close_pool, init_pool, pool, upsert_tg_user
from app.services import copy_executor
from app.services.copy_engine import CopyConfig, CopyEngine, decide


def _reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


LIVE = _reachable(f"{settings.OPRAI_TG_SIGNER_URL.rstrip('/')}/health") and _reachable(
    f"{settings.GATEWAY_URL.rstrip('/')}/health"
)

CFG = CopyConfig(enabled=True, mode="fixed", amount_eth=0.02,
                 max_per_trade_eth=0.05, min_per_trade_eth=0.001,
                 daily_cap_usd=100.0)


class _Store:
    """Minimal stand-in: one copier, no spend, records what happened."""

    def __init__(self, spent: float = 0.0):
        self.spent = spent
        self.fills: list[tuple] = []

    async def copiers_of(self, leader):
        return [{"telegram_id": 1, "config": CFG}]

    async def spent_today_usd(self, uid):
        return self.spent

    async def record_fill(self, uid, leader, token, eth, usd, tx):
        self.fills.append((uid, token, eth, usd))


async def _engine(store, price, executed: list, notes: list):
    async def execute(uid, token, eth):
        executed.append((uid, token, eth))
        return "0xhash"

    async def notify(uid, text):
        notes.append(text)

    async def provider(uid):
        return price

    return CopyEngine(store, execute, notify, price_provider=provider)


# ── the pure guards ─────────────────────────────────────────────────────────
def test_the_money_side_of_a_trade_is_not_a_buy():
    """A leader moving into USDG or WETH is funding, not conviction. Copying
    it spends the user's ETH to hold the same dollars they started with."""
    usdg = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
    assert not decide(usdg, 1.0, CFG, 0.0, 3000.0).execute


def test_a_copy_is_capped_per_trade():
    """Proportional sizing follows a leader who may be far larger. The price
    here is deliberately low so the DAILY cap can't bind — this test is about
    the per-trade ceiling on its own."""
    big = CopyConfig(**{**CFG.__dict__, "mode": "proportional", "amount_eth": 1.0})
    d = decide("0xtoken", 10.0, big, 0.0, eth_price_usd=100.0)
    assert d.execute and d.amount_eth == CFG.max_per_trade_eth


def test_the_daily_cap_shrinks_the_last_trade_rather_than_breaking_it():
    d = decide("0xtoken", 1.0, CFG, spent_today_usd=70.0, eth_price_usd=3000.0)
    assert d.execute
    assert d.amount_eth * 3000.0 <= 30.0 + 1e-9, "the daily cap was exceeded"


def test_nothing_happens_once_the_cap_is_reached():
    assert not decide("0xtoken", 1.0, CFG, 100.0, 3000.0).execute


# ── the price behind the cap ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_cap_that_cannot_be_priced_stops_the_trade():
    """The daily limit is in dollars. Without a real ETH price it isn't a
    limit at all, so the copy is skipped and the user is told why — spending
    against a number we couldn't read is how a cap quietly stops working."""
    store, executed, notes = _Store(), [], []
    engine = await _engine(store, None, executed, notes)
    await engine.on_buy("0xleader", "0xtoken", 1.0, "0xtx")
    assert executed == [], "traded without being able to check the cap"
    assert notes and "limit" in notes[0].lower()


@pytest.mark.asyncio
async def test_the_live_price_is_what_the_cap_and_the_record_use():
    """A fill recorded at a stale price makes tomorrow's cap wrong too."""
    store, executed, notes = _Store(), [], []
    engine = await _engine(store, 4000.0, executed, notes)
    await engine.on_buy("0xleader", "0xtoken", 1.0, "0xtx")
    assert executed and executed[0][2] == CFG.amount_eth
    assert store.fills[0][3] == pytest.approx(CFG.amount_eth * 4000.0)


@pytest.mark.asyncio
async def test_a_failed_buy_is_reported_and_records_nothing():
    store, notes = _Store(), []

    async def failing(uid, token, eth):
        raise copy_executor.CopyExecutionError("not enough ETH")

    async def notify(uid, text):
        notes.append(text)

    async def provider(uid):
        return 3000.0

    engine = CopyEngine(store, failing, notify, price_provider=provider)
    await engine.on_buy("0xleader", "0xtoken", 1.0, "0xtx")
    assert store.fills == [], "a failed buy was recorded as a fill"
    assert notes and "failed" in notes[0].lower()


# ── live ────────────────────────────────────────────────────────────────────
@pytest.mark.skipif(not LIVE, reason="gateway/signer not running")
@pytest.mark.asyncio
async def test_eth_is_priced_from_the_chains_own_dex():
    """The price enforcing a dollar cap should be the price the trade would
    actually get, not a constant written down months ago."""
    await init_pool()
    tg = random.randint(10**10, 10**11)
    await upsert_tg_user(tg, "copy_price")
    try:
        price = await copy_executor.eth_price_usd(tg)
        assert price is not None
        # A sanity band, not a prediction: this only has to catch a decimals
        # mistake, which would be wrong by a factor of a million.
        assert 100 < price < 100_000, f"implausible ETH price {price}"
    finally:
        await pool().execute("DELETE FROM tg_wallets WHERE telegram_id = $1", tg)
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg)
        await close_pool()


@pytest.mark.skipif(not LIVE, reason="gateway/signer not running")
@pytest.mark.asyncio
async def test_an_unfunded_copy_is_refused_before_signing():
    await init_pool()
    tg = random.randint(10**10, 10**11)
    await upsert_tg_user(tg, "copy_broke")
    try:
        with pytest.raises(copy_executor.CopyExecutionError) as err:
            await copy_executor.buy(tg, "usdg", 0.01)
        assert "enough" in str(err.value).lower()
    finally:
        await pool().execute("DELETE FROM tg_wallets WHERE telegram_id = $1", tg)
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg)
        await close_pool()


# ── when the node it watches goes quiet ─────────────────────────────────────
@pytest.mark.asyncio
async def test_the_watcher_moves_to_the_fallback_and_stops_shouting(monkeypatch):
    """Our node stops serving RPC for an hour while it prunes. At a 400ms poll
    that produced ~9,000 identical warnings an hour and zero copy trades — the
    loudest possible way to be silently broken."""
    import asyncio as _asyncio

    import httpx

    from app.services import copy_watcher

    tried: list[str] = []
    warnings: list[dict] = []

    async def fake_rpc(c, node, method, params):
        tried.append(node)
        if "rh-nitro" in node:
            raise httpx.ConnectError("All connection attempts failed")
        if method == "eth_blockNumber":
            raise SystemExit  # one clean pass, then stop the loop
        return None

    monkeypatch.setattr(copy_watcher, "_rpc", fake_rpc)
    monkeypatch.setattr(copy_watcher.log, "warning",
                        lambda ev, **kw: warnings.append({"event": ev, **kw}))

    async def run():
        await copy_watcher.watch_buys(
            "http://rh-nitro:8547", lambda: set(), lambda *a: None,
            poll_ms=1, fallback="https://rpc.mainnet.chain.robinhood.com",
        )

    with pytest.raises(SystemExit):
        await run()

    assert any("rh-nitro" in n for n in tried), "the primary was never tried"
    assert any("robinhood.com" in n for n in tried), "it never moved to the fallback"
    assert len(warnings) <= 2, f"the failing node was logged {len(warnings)} times"
