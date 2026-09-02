"""Build `dex_pools` — the venue map: which DEX a token trades on, its pair, fee
tier and (V4) hook. Without it the index can say what moved but never WHERE, so
"which DEX / which factory / which platform does this wallet use" is unanswerable.

Robinhood Chain is Uniswap-dominated and mostly **V4**, which is a SINGLETON: pools
are `poolId` (bytes32), not addresses, and only the PoolManager's `Initialize` event
carries the pair — so V4 pools are read from logs (no node calls at all). V3/V2 pools
ARE addresses, so we take the distinct addresses that emitted a Swap and resolve
token0/token1/fee from the node in batches.

Run: docker exec -w /app rh-chain-intel-api python3 -m api.build_dex_pools [--full]
Idempotent: rebuilds the table from scratch (it's small — thousands of rows)."""
from __future__ import annotations

import asyncio
import sys

import httpx

from . import ch, node
from .ch import CH_URL, CH_USER, CH_PASS, CH_DB

# Uniswap V4 singleton on Robinhood Chain + its Initialize event.
V4_POOL_MANAGER = "0x8366a39cc670b4001a1121b8f6a443a643e40951"
SIG_V4_INIT = "0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438"
SIG_V4_SWAP = "0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f"
SIG_V3_SWAP = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
SIG_V2_SWAP = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
SEL_TOKEN0, SEL_TOKEN1, SEL_FEE = "0x0dfe1681", "0xd21220a7", "0xddca3f43"
BLOCK_STEP = 2_000_000


async def ch_write(sql: str) -> None:
    async with httpx.AsyncClient(timeout=180) as c:
        r = await c.post(CH_URL, params={"user": CH_USER, "password": CH_PASS,
                                         "database": CH_DB}, content=sql.encode())
        r.raise_for_status()


def _word(hx: str, i: int) -> str:
    hx = hx[2:] if hx.startswith("0x") else hx
    return hx[i * 64:(i + 1) * 64]


def _addr_from_word(word: str) -> str:
    return "0x" + word[-40:].lower()


async def ensure_columns() -> None:
    """dex_pools ships as (pool, token0, token1, dex, created_block) — fee tier and
    the V4 hook are what identify the venue/launchpad, so add them if missing."""
    for col, typ in (("fee", "UInt32"), ("hooks", "String")):
        try:
            await ch_write(f"ALTER TABLE rh.dex_pools ADD COLUMN IF NOT EXISTS {col} {typ}")
        except Exception as e:
            print(f"  ALTER {col}: {str(e)[:90]}")


async def collect_v4(tip: int) -> list[tuple]:
    """V4 pools straight from Initialize logs: id=topic1, currency0/1=topic2/3,
    data = [fee, tickSpacing, hooks, sqrtPriceX96, tick]."""
    out, lo = [], 0
    while lo <= tip:
        hi = min(lo + BLOCK_STEP - 1, tip)
        rows = await ch.q(
            f"SELECT topic1, topic2, topic3, data, min(block_number) AS blk "
            f"FROM rh.logs WHERE address='{V4_POOL_MANAGER}' AND topic0='{SIG_V4_INIT}' "
            f"AND block_number BETWEEN {lo} AND {hi} "
            f"GROUP BY topic1, topic2, topic3, data", timeout=180)
        for r in rows:
            d = r["data"] or ""
            if len(d) < 2 + 64 * 3:
                continue
            out.append((
                r["topic1"].lower(),                    # poolId
                _addr_from_word(r["topic2"]),           # currency0 (0x0 = native ETH)
                _addr_from_word(r["topic3"]),           # currency1
                "uniswap-v4",
                int(r["blk"]),
                int(_word(d, 0), 16),                   # fee (hundredths of a bip)
                _addr_from_word(_word(d, 2)),           # hooks
            ))
        lo = hi + 1
    return out


async def collect_addr_pools(sig: str, dex: str, tip: int, since: int = 0) -> list[tuple]:
    """V3/V2: distinct addresses that emitted a Swap, then token0/token1/fee from
    the node in batches (the log itself never carries the pair). `since` bounds the
    scan — a pool that hasn't traded in the window is not a venue anyone is asking
    about, and scanning 1.8B logs for every historical pool costs far more than it
    is worth."""
    pools: dict[str, int] = {}
    lo = since
    while lo <= tip:
        hi = min(lo + BLOCK_STEP - 1, tip)
        rows = await ch.q(
            f"SELECT address, min(block_number) AS blk FROM rh.logs "
            f"WHERE topic0='{sig}' AND block_number BETWEEN {lo} AND {hi} "
            f"GROUP BY address", timeout=180)
        for r in rows:
            a = r["address"].lower()
            pools[a] = min(pools.get(a, 1 << 62), int(r["blk"]))
        lo = hi + 1
    addrs = list(pools)
    out = []
    for i in range(0, len(addrs), 200):
        chunk = addrs[i:i + 200]
        t0 = await _batch_call(chunk, SEL_TOKEN0)
        t1 = await _batch_call(chunk, SEL_TOKEN1)
        fee = await _batch_call(chunk, SEL_FEE)
        for a in chunk:
            if not t0.get(a) or not t1.get(a):
                continue
            try:
                f = int(fee.get(a) or "0x0", 16)
            except ValueError:
                f = 0
            out.append((a, _addr_from_word(t0[a]), _addr_from_word(t1[a]),
                        dex, pools[a], f, "0x" + "0" * 40))
    return out


async def _batch_call(addrs: list[str], selector: str) -> dict[str, str]:
    batch = [{"jsonrpc": "2.0", "id": i, "method": "eth_call",
              "params": [{"to": a, "data": selector}, "latest"]}
             for i, a in enumerate(addrs)]
    try:
        async with httpx.AsyncClient(timeout=40) as c:
            res = (await c.post(node.NODE, json=batch)).json() or []
    except Exception:
        return {}
    by_id = {x.get("id"): x.get("result") for x in res if isinstance(x, dict)}
    return {a: by_id[i] for i, a in enumerate(addrs)
            if by_id.get(i) and by_id[i] not in ("0x", "")}


async def insert_rows(rows: list[tuple]) -> None:
    for i in range(0, len(rows), 2000):
        vals = ",".join(
            "('{}','{}','{}','{}',{},{},'{}')".format(*r) for r in rows[i:i + 2000])
        await ch_write("INSERT INTO rh.dex_pools "
                       "(pool, token0, token1, dex, created_block, fee, hooks) VALUES " + vals)


async def main() -> None:
    """Steps are separate so each finishes inside a sane window and partial progress
    persists: `--v4` (all history, from logs), `--v3v2` (bounded scan + node calls).
    `--truncate` clears first; no step flag runs everything."""
    args = sys.argv[1:]
    do_v4 = "--v4" in args or not any(a.startswith("--") and a != "--truncate" for a in args)
    do_v3v2 = "--v3v2" in args or not any(a.startswith("--") and a != "--truncate" for a in args)
    since_blocks = 5_000_000
    for a in args:
        if a.startswith("--since="):
            since_blocks = int(a.split("=", 1)[1])

    tip = int(await ch.scalar("SELECT max(block_number) FROM rh.logs") or 0)
    print(f"tip={tip}", flush=True)
    await ensure_columns()
    if "--truncate" in args:
        await ch_write("TRUNCATE TABLE rh.dex_pools")
        print("truncated", flush=True)

    if do_v4:
        v4 = await collect_v4(tip)
        await insert_rows(v4)
        print(f"  uniswap-v4: {len(v4)} pools written", flush=True)
    if do_v3v2:
        since = max(0, tip - since_blocks)
        for sig, dex in ((SIG_V3_SWAP, "uniswap-v3"), (SIG_V2_SWAP, "uniswap-v2")):
            rows = await collect_addr_pools(sig, dex, tip, since)
            await insert_rows(rows)
            print(f"  {dex}: {len(rows)} pools written (since block {since})", flush=True)

    n = await ch.scalar("SELECT count() FROM rh.dex_pools")
    print(f"dex_pools = {n} rows", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
