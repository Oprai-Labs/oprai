"""Backfill / refresh `dex_swaps` from Swap logs (Uniswap V4 / V3 / V2), decoded in
ClickHouse itself and joined to dex_pools for the token pair. Adds a USD leg: the
quote side (USDG / USDe / WETH / native ETH at the day's WETH price) — so per-venue
volume and per-wallet traded value are real numbers, not estimates.

Semantics differ per venue and are handled here once:
  V3  amount0/1 are POOL deltas (positive = pool received)     → token_in = positive side
  V4  amount0/1 are SWAPPER deltas (negative = swapper paid)   → token_in = negative side
  V2  amount0In/1In/0Out/1Out                                  → in/out explicit
Runs in 500k-block chunks from the table's own high-water mark; safe to re-run.

  python3 -m api.build_dex_swaps [--from=N] [--to=N] [--chunk=500000]"""
from __future__ import annotations

import asyncio
import sys
import time

import httpx

from . import ch
from .ch import CH_URL, CH_USER, CH_PASS, CH_DB

V4 = "0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f"
V3 = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
V2 = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
USDG = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
USDE = "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34"
WETH = "0x0bd7d308f8e1639fab988df18a8011f41eacad73"
ZERO = "0x0000000000000000000000000000000000000000"


async def w(sql: str, timeout: float = 1800) -> None:
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(CH_URL, params={"user": CH_USER, "password": CH_PASS, "database": CH_DB,
                                          "max_execution_time": int(timeout), "max_memory_usage": 12_000_000_000},
                         content=sql.encode())
        if r.status_code != 200:
            raise RuntimeError(r.text[:400])


def word(i: int) -> str:            # i-th 32-byte data word as Int256
    return f"reinterpretAsInt256(reverse(unhex(substring(l.data, {3 + 64 * i}, 64))))"


def uword(i: int) -> str:
    return f"toUInt256({word(i)})"


USD = """
    multiIf(token_in  = '{usdg}', toFloat64(amount_in)  / 1e6,
            token_out = '{usdg}', toFloat64(amount_out) / 1e6,
            token_in  = '{usde}', toFloat64(amount_in)  / 1e18,
            token_out = '{usde}', toFloat64(amount_out) / 1e18,
            token_in  IN ('{weth}', '{zero}'), toFloat64(amount_in)  / 1e18 * weth_usd,
            token_out IN ('{weth}', '{zero}'), toFloat64(amount_out) / 1e18 * weth_usd,
            0.0)""".format(usdg=USDG, usde=USDE, weth=WETH, zero=ZERO)


def chunk_sql(lo: int, hi: int) -> str:
    v3 = f"""
        SELECT l.block_number, l.timestamp, l.tx_hash, l.log_index, l.address AS pool,
               concat('0x', substring(l.topic1, 27, 40)) AS sender, concat('0x', substring(l.topic2, 27, 40)) AS recipient,
               {word(0)} AS a0, {word(1)} AS a1, 'uniswap-v3' AS dex
        FROM rh.logs l WHERE l.topic0 = '{V3}' AND l.block_number BETWEEN {lo} AND {hi}"""
    v4 = f"""
        SELECT l.block_number, l.timestamp, l.tx_hash, l.log_index, l.topic1 AS pool,
               concat('0x', substring(l.topic2, 27, 40)) AS sender, concat('0x', substring(l.topic2, 27, 40)) AS recipient,
               -{word(0)} AS a0, -{word(1)} AS a1, 'uniswap-v4' AS dex
        FROM rh.logs l WHERE l.topic0 = '{V4}' AND l.block_number BETWEEN {lo} AND {hi}"""
    v2 = f"""
        SELECT l.block_number, l.timestamp, l.tx_hash, l.log_index, l.address AS pool,
               concat('0x', substring(l.topic1, 27, 40)) AS sender, concat('0x', substring(l.topic2, 27, 40)) AS recipient,
               {word(0)} - {word(2)} AS a0, {word(1)} - {word(3)} AS a1, 'uniswap-v2' AS dex
        FROM rh.logs l WHERE l.topic0 = '{V2}' AND l.block_number BETWEEN {lo} AND {hi}"""
    # after normalisation: a0/a1 are POOL deltas for every venue (positive = pool received)
    return f"""
    INSERT INTO rh.dex_swaps (block_number, timestamp, tx_hash, log_index, pool, sender, recipient,
                              token_in, token_out, amount_in, amount_out, dex, usd)
    SELECT block_number, timestamp, tx_hash, log_index, pool, sender, recipient,
           token_in, token_out, amount_in, amount_out, dex, {USD} AS usd
    FROM (
        SELECT s.block_number AS block_number, s.timestamp AS timestamp, s.tx_hash AS tx_hash,
               s.log_index AS log_index, s.pool AS pool, s.sender AS sender, s.recipient AS recipient,
               s.dex AS dex, ifNull(wp.weth_usd, 0.0) AS weth_usd,
               if(s.a0 > 0, p.token0, p.token1) AS token_in,
               if(s.a0 > 0, p.token1, p.token0) AS token_out,
               toUInt256(if(s.a0 > 0, s.a0, s.a1)) AS amount_in,
               toUInt256(if(s.a0 > 0, -s.a1, -s.a0)) AS amount_out
        FROM ({v3} UNION ALL {v4} UNION ALL {v2}) AS s
        INNER JOIN rh.dex_pools p ON p.pool = s.pool
        LEFT JOIN rh.weth_price wp ON wp.day = toDate(s.timestamp)
        WHERE (s.a0 > 0 AND s.a1 <= 0) OR (s.a1 > 0 AND s.a0 <= 0)
    )"""


async def main() -> None:
    args = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a)
    chunk = int(args.get("--chunk", 500_000))
    await w("ALTER TABLE rh.dex_swaps ADD COLUMN IF NOT EXISTS usd Float64 DEFAULT 0")
    await w("ALTER TABLE rh.dex_swaps ADD INDEX IF NOT EXISTS idx_tin token_in TYPE bloom_filter GRANULARITY 4")
    await w("ALTER TABLE rh.dex_swaps ADD INDEX IF NOT EXISTS idx_tout token_out TYPE bloom_filter GRANULARITY 4")
    await w("ALTER TABLE rh.dex_swaps ADD INDEX IF NOT EXISTS idx_tx tx_hash TYPE bloom_filter GRANULARITY 4")
    hi_logs = int(await ch.scalar("SELECT max(block_number) FROM rh.logs"))
    start = int(args.get("--from", 0) or (int(await ch.scalar("SELECT max(block_number) FROM rh.dex_swaps") or 0) + 1))
    end = int(args.get("--to", hi_logs))
    print(f"dex_swaps: {start}..{end} in {chunk}-block chunks", flush=True)
    lo = start
    while lo <= end:
        hi = min(lo + chunk - 1, end)
        t = time.time()
        await w(chunk_sql(lo, hi))
        n = await ch.scalar(f"SELECT count() FROM rh.dex_swaps WHERE block_number BETWEEN {lo} AND {hi}", timeout=120)
        print(f"  {lo}..{hi}: {n} swaps ({time.time() - t:.0f}s)", flush=True)
        lo = hi + 1
    tot = await ch.scalar("SELECT count() FROM rh.dex_swaps")
    vol = await ch.scalar("SELECT round(sum(usd)) FROM rh.dex_swaps")
    print(f"done: {tot} swaps, ${vol} priced volume", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
