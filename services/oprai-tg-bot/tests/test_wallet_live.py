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


# ── a wallet's number belongs to the wallet ─────────────────────────────────
@pytest.mark.asyncio
async def test_the_numbering_does_not_move_when_you_switch_wallets():
    """The list was sorted active-first, so switching renumbered them: W1
    became W2 and back. The number is how someone tells two addresses apart —
    a label that moves is worse than none."""
    await init_pool()
    tg = random.randint(10**10, 10**11)
    await upsert_tg_user(tg, "stable_numbers")
    try:
        first = await wallet_svc.get_or_create_wallet(tg)
        second = await wallet_svc.new_wallet(tg)

        def order():
            return None

        rows = await wallet_svc.list_wallets(tg)
        assert rows[0]["address"] == first["address"], "the first wallet isn't W1"

        await wallet_svc.activate(tg, first["address"])
        rows = await wallet_svc.list_wallets(tg)
        assert rows[0]["address"] == first["address"], "switching renumbered them"
        assert rows[1]["address"] == second["address"]

        await wallet_svc.activate(tg, second["address"])
        rows = await wallet_svc.list_wallets(tg)
        assert rows[0]["address"] == first["address"], "switching back renumbered them"
        # Which one is in use is a separate fact from what it is called.
        assert rows[1]["archived_at"] is None and rows[0]["archived_at"] is not None
    finally:
        await pool().execute("DELETE FROM tg_wallets WHERE telegram_id = $1", tg)
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg)
        await close_pool()


@pytest.mark.asyncio
async def test_a_wallet_can_be_named_and_unnamed():
    await init_pool()
    tg = random.randint(10**10, 10**11)
    await upsert_tg_user(tg, "naming")
    try:
        wallet = await wallet_svc.get_or_create_wallet(tg)
        assert await wallet_svc.rename(tg, wallet["address"], "Trading")
        assert (await wallet_svc.list_wallets(tg))[0]["label"] == "Trading"

        # Clearing it falls back to the number rather than leaving a blank.
        assert await wallet_svc.rename(tg, wallet["address"], None)
        assert (await wallet_svc.list_wallets(tg))[0]["label"] is None

        # Someone else's wallet is not theirs to rename.
        assert not await wallet_svc.rename(
            tg, "0x0000000000000000000000000000000000000001", "Nice try"
        )
    finally:
        await pool().execute("DELETE FROM tg_wallets WHERE telegram_id = $1", tg)
        await pool().execute("DELETE FROM tg_users WHERE telegram_id = $1", tg)
        await close_pool()


# ── when our own node is busy ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_rpc_falls_back_when_our_node_is_not_answering(monkeypatch):
    """Our node is a real full node: it prunes and re-syncs and traverses its
    trie database for an hour at a time, serving nothing while it does. That
    hour used to blind the bot completely — balances, deposits, copy trades —
    for a reason that had nothing to do with the chain."""
    import httpx

    from app.services import evm

    seen: list[str] = []

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def post(self, url, json=None):
            seen.append(url)
            if "rh-nitro" in url:
                raise httpx.ConnectError("All connection attempts failed")
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1,
                                             "result": "0x340a04b"})

    # Production shape: our own node first, a public one behind it.
    monkeypatch.setattr(evm.settings, "OPRAI_TG_RPC_OVERRIDE",
                        "http://rh-nitro:8547")
    monkeypatch.setattr(evm.settings, "OPRAI_TG_RPC_FALLBACK",
                        "https://rpc.mainnet.chain.robinhood.com")
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    result = await evm.rpc("eth_blockNumber", [])

    assert result == "0x340a04b", "the fallback did not answer"
    assert len(seen) >= 2, f"no fallback was attempted: {seen}"
    assert seen[0] != seen[-1], "the same endpoint was tried twice"


@pytest.mark.asyncio
async def test_an_unreachable_rpc_never_reports_an_empty_reason():
    """The log read 'rpc unreachable:' and stopped at the colon — several
    httpx errors stringify to nothing, so it said less than silence."""
    import httpx

    from app.services.evm import _why

    for e in (httpx.ConnectError(""), httpx.ReadTimeout(""), httpx.HTTPError("")):
        text = _why(e)
        assert text.strip(), "an empty reason survived"
        assert not text.endswith(":"), text


@pytest.mark.asyncio
async def test_the_batch_path_falls_back_too(monkeypatch):
    """The deposit watcher reads every wallet's balance through rpc_batch, not
    rpc — so a fallback that only covered single calls left the one job that
    runs every eight seconds still blind."""
    import httpx

    from app.services import evm

    seen: list[str] = []

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def post(self, url, json=None):
            seen.append(url)
            if "rh-nitro" in url:
                raise httpx.ConnectError("All connection attempts failed")
            return httpx.Response(200, json=[{"id": 0, "result": "0x1"}])

    monkeypatch.setattr(evm.settings, "OPRAI_TG_RPC_OVERRIDE", "http://rh-nitro:8547")
    monkeypatch.setattr(evm.settings, "OPRAI_TG_RPC_FALLBACK",
                        "https://rpc.mainnet.chain.robinhood.com")
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    out = await evm.rpc_batch([("eth_getBalance", ["0xabc", "latest"])])
    assert out == ["0x1"], f"the batch fallback did not answer: {out}"
    assert any("rh-nitro" in u for u in seen), "our own node was never tried"
    assert any("robinhood.com" in u for u in seen), "no fallback was attempted"


@pytest.mark.asyncio
async def test_a_long_outage_is_two_log_lines_not_thousands(monkeypatch):
    """The fallback works, and that is exactly why it must be quiet about it.
    Logging every call wrote two lines a second for as long as the node was
    pruning — thousands of identical lines burying the one thing worth
    reading. Log the transitions: we left the primary, and we came back."""
    import httpx

    from app.services import evm

    lines: list[str] = []
    monkeypatch.setattr(evm.log, "warning", lambda ev, **kw: lines.append(ev))
    monkeypatch.setattr(evm.log, "info", lambda ev, **kw: lines.append(ev))
    monkeypatch.setattr(evm, "_on_fallback", False)
    monkeypatch.setattr(evm.settings, "OPRAI_TG_RPC_OVERRIDE", "http://rh-nitro:8547")
    monkeypatch.setattr(evm.settings, "OPRAI_TG_RPC_FALLBACK",
                        "https://rpc.mainnet.chain.robinhood.com")

    down = {"primary": True}

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def post(self, url, json=None):
            if "rh-nitro" in url and down["primary"]:
                raise httpx.ConnectError("All connection attempts failed")
            return httpx.Response(200, json={"result": "0x1"})

    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    for _ in range(50):
        await evm.rpc("eth_blockNumber", [])
    assert lines == ["rpc_using_fallback"], f"50 calls wrote {len(lines)} lines"

    down["primary"] = False
    await evm.rpc("eth_blockNumber", [])
    assert lines == ["rpc_using_fallback", "rpc_primary_recovered"], (
        "coming back to our own node went unrecorded"
    )


@pytest.mark.asyncio
async def test_a_node_that_answers_but_is_behind_is_not_used(monkeypatch):
    """Our node came back from pruning 390,000 blocks behind and started
    serving again. Every balance read was then stale, and as it caught up each
    wallet's balance would rise again and be announced as money arriving.
    Answering is not the same as being right."""
    import httpx

    from app.services import evm

    used: list[str] = []

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def post(self, url, json=None):
            if json.get("method") == "eth_blockNumber":
                head = 54_486_198 if "rh-nitro" in url else 54_876_457
                return httpx.Response(200, json={"result": hex(head)})
            used.append(url)
            return httpx.Response(200, json={"result": "0x1"})

    monkeypatch.setattr(evm.settings, "OPRAI_TG_RPC_OVERRIDE", "http://rh-nitro:8547")
    monkeypatch.setattr(evm.settings, "OPRAI_TG_RPC_FALLBACK",
                        "https://rpc.mainnet.chain.robinhood.com")
    monkeypatch.setattr(evm, "_lag_checked_at", 0.0)
    monkeypatch.setattr(evm, "_primary_stale", False)
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    await evm.rpc("eth_getBalance", ["0xabc", "latest"])
    assert used and "robinhood.com" in used[0], (
        f"a node 390k blocks behind was still asked for a balance: {used}"
    )


@pytest.mark.asyncio
async def test_a_node_at_the_head_is_preferred(monkeypatch):
    """The whole point of running our own node. Once it catches up it must be
    used again, without anyone touching a config."""
    import httpx

    from app.services import evm

    used: list[str] = []

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def post(self, url, json=None):
            if json.get("method") == "eth_blockNumber":
                head = 54_876_450 if "rh-nitro" in url else 54_876_457
                return httpx.Response(200, json={"result": hex(head)})
            used.append(url)
            return httpx.Response(200, json={"result": "0x1"})

    monkeypatch.setattr(evm.settings, "OPRAI_TG_RPC_OVERRIDE", "http://rh-nitro:8547")
    monkeypatch.setattr(evm.settings, "OPRAI_TG_RPC_FALLBACK",
                        "https://rpc.mainnet.chain.robinhood.com")
    monkeypatch.setattr(evm, "_lag_checked_at", 0.0)
    monkeypatch.setattr(evm, "_primary_stale", True)
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    await evm.rpc("eth_getBalance", ["0xabc", "latest"])
    assert used and "rh-nitro" in used[0], (
        f"a healthy node seven blocks behind was skipped: {used}"
    )
