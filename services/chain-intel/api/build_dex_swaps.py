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
PONS_BUY = "0xec36bf571f136799e8dc0b0b8bea4b04d8bd3d43de838aab0d5fc21d4cbfc455"    # (buyer, recipient) ethIn, tokensOut, fee, tax
PONS_SELL = "0x8113d738abdcb6b38357e9d53a54a7157861a09031b453651f0fe7fe151f59df"   # (seller, recipient) tokensIn, ethOut, fee, tax
PONS_LAUNCH = "0x908408e307fc569b417f6cbec5d5a06f44a0a505ac0479b47d421a4b2fd6a1e6"  # emitted BY the new curve at launch
V4_INIT = "0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438"
V4_PM = "0x8366a39cc670b4001a1121b8f6a443a643e40951"
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
            token_in  IN ('{weth}', '{zero}'), toFloat64(amount_in)  / 1e18 * dictGetOrDefault('rh.weth_price_dict', 'weth_usd', toDate(timestamp), 0.0),
            token_out IN ('{weth}', '{zero}'), toFloat64(amount_out) / 1e18 * dictGetOrDefault('rh.weth_price_dict', 'weth_usd', toDate(timestamp), 0.0),
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
               s.dex AS dex,
               if(s.a0 > 0, p.token0, p.token1) AS token_in,
               if(s.a0 > 0, p.token1, p.token0) AS token_out,
               toUInt256(if(s.a0 > 0, s.a0, s.a1)) AS amount_in,
               toUInt256(if(s.a0 > 0, -s.a1, -s.a0)) AS amount_out
        FROM ({v3} UNION ALL {v4} UNION ALL {v2}) AS s
        INNER JOIN rh.dex_pools p ON p.pool = s.pool
        WHERE (s.a0 > 0 AND s.a1 <= 0) OR (s.a1 > 0 AND s.a0 <= 0)
    )"""


def pons_sql(lo: int, hi: int) -> str:
    """Pons V2 bonding-curve trades → dex_swaps rows (dex = 'pons-curve'). The curve
    contract is the pool; buyer/seller is topic1 (the caller). The quote leg is
    priced by what the curve is quoted in: ETH (day's WETH price), USDG/USDe, or
    another token (that token's daily price)."""
    return f"""
    INSERT INTO rh.dex_swaps (block_number, timestamp, tx_hash, log_index, pool, sender, recipient,
                              token_in, token_out, amount_in, amount_out, dex, usd)
    SELECT block_number, timestamp, tx_hash, log_index, pool, sender, recipient,
           token_in, token_out, amount_in, amount_out, dex,
           multiIf(quote IN ('{ZERO}', '{WETH}'), qamt / 1e18 * dictGetOrDefault('rh.weth_price_dict', 'weth_usd', toDate(timestamp), 0.0),
                   quote = '{USDG}', qamt / 1e6,
                   quote = '{USDE}', qamt / 1e18,
                   qamt / 1e18 * ifNull(tp.px, 0.0)) AS usd
    FROM (
        SELECT l.block_number AS block_number, l.timestamp AS timestamp, l.tx_hash AS tx_hash, l.log_index AS log_index,
               l.address AS pool, concat('0x', substring(l.topic1, 27, 40)) AS sender,
               concat('0x', substring(l.topic2, 27, 40)) AS recipient,
               if(l.topic0 = '{PONS_BUY}', c.quote, c.token) AS token_in,
               if(l.topic0 = '{PONS_BUY}', c.token, c.quote) AS token_out,
               {uword(0)} AS amount_in, {uword(1)} AS amount_out, 'pons-curve' AS dex, c.quote AS quote,
               if(l.topic0 = '{PONS_BUY}', toFloat64({uword(0)}), toFloat64({uword(1)})) AS qamt
        FROM rh.logs l INNER JOIN rh.pons_curves c ON c.curve = l.address
        WHERE l.topic0 IN ('{PONS_BUY}', '{PONS_SELL}') AND l.block_number BETWEEN {lo} AND {hi}
    ) AS s LEFT JOIN rh.token_price_daily tp ON tp.token = s.quote AND tp.day = toDate(s.timestamp)"""


async def build_weth_price(days: int | None = None) -> None:
    """ETH/USD per day from the chain's OWN WETH↔USDG (and ETH↔USDG) swaps: the daily
    median of usdg/weth over every such swap. Gaps (no swaps) are filled forward, and
    today is always present, so nothing quoted in ETH stays unpriced. Exposed to the
    ETL and mutations as the dictionary rh.weth_price_dict."""
    rows = await ch.q(f"""
        SELECT toDate(timestamp) AS day,
               medianExact(if(token_in = '{USDG}', toFloat64(amount_in) / 1e6 / (toFloat64(amount_out) / 1e18),
                                                    toFloat64(amount_out) / 1e6 / (toFloat64(amount_in) / 1e18))) AS px,
               count() AS n
        FROM rh.dex_swaps
        WHERE dex IN ('uniswap-v3', 'uniswap-v4')
          AND ((token_in = '{USDG}' AND token_out IN ('{WETH}', '{ZERO}')) OR (token_out = '{USDG}' AND token_in IN ('{WETH}', '{ZERO}')))
          AND amount_in > 0 AND amount_out > 0 {f"AND timestamp > now() - INTERVAL {days} DAY" if days else ""}
        GROUP BY day HAVING n >= 5 ORDER BY day""", timeout=1800)
    if not rows:
        return
    import datetime as dt
    by_day = {dt.date.fromisoformat(r["day"]): float(r["px"]) for r in rows}
    d, last = min(by_day), None
    today = dt.date.today()
    filled = []
    while d <= today:
        px = by_day.get(d)
        if px and 100 < px < 100000:          # sanity: ETH has never been outside this
            last = px
        if last:
            filled.append((d.isoformat(), last))
        d += dt.timedelta(days=1)
    await w("CREATE TABLE IF NOT EXISTS rh.weth_price (day Date, weth_usd Float64) ENGINE = ReplacingMergeTree ORDER BY day")
    await w("INSERT INTO rh.weth_price (day, weth_usd) VALUES " + ",".join(f"('{d}',{px})" for d, px in filled))
    await w("OPTIMIZE TABLE rh.weth_price FINAL")
    await w(f"""CREATE DICTIONARY IF NOT EXISTS rh.weth_price_dict (day Date, weth_usd Float64)
               PRIMARY KEY day SOURCE(CLICKHOUSE(TABLE 'weth_price' DB 'rh' USER '{CH_USER}' PASSWORD '{CH_PASS}'))
               LAYOUT(FLAT()) LIFETIME(MIN 300 MAX 900)""")
    await w("SYSTEM RELOAD DICTIONARY rh.weth_price_dict")
    print(f"  weth_price: {len(filled)} days ({filled[0][0]}..{filled[-1][0]}), today={filled[-1][1]:.2f}", flush=True)


async def reprice_eth_legs() -> None:
    """Rows written while a day had no WETH price (usd = 0) get their USD now that the
    dictionary covers every day — a mutation, run after build_weth_price."""
    q = f"""ALTER TABLE rh.dex_swaps UPDATE usd = multiIf(
            token_in  IN ('{WETH}', '{ZERO}'), toFloat64(amount_in)  / 1e18 * dictGetOrDefault('rh.weth_price_dict', 'weth_usd', toDate(timestamp), 0.0),
            token_out IN ('{WETH}', '{ZERO}'), toFloat64(amount_out) / 1e18 * dictGetOrDefault('rh.weth_price_dict', 'weth_usd', toDate(timestamp), 0.0),
            usd)
        WHERE usd = 0 AND (token_in IN ('{WETH}', '{ZERO}') OR token_out IN ('{WETH}', '{ZERO}'))
          AND token_in NOT IN ('{USDG}', '{USDE}') AND token_out NOT IN ('{USDG}', '{USDE}')"""
    await w(q)
    print("  reprice mutation submitted", flush=True)


async def build_pons_curves(full: bool = False) -> None:
    """curve → (token, quote) map. Incremental by default: only curves launched after
    the newest one we know (the launch event is emitted BY the new curve, the token is
    minted to it in the same tx, the QUOTE is the ERC20 it pays out that is not its own
    token — ZERO when it only pays native ETH). `full` rebuilds everything."""
    await w("""CREATE TABLE IF NOT EXISTS rh.pons_curves (curve String, token String, quote String DEFAULT '', created_block UInt64)
               ENGINE = ReplacingMergeTree ORDER BY curve""")
    await w("ALTER TABLE rh.pons_curves ADD COLUMN IF NOT EXISTS quote String DEFAULT ''")
    since = 0 if full else int(await ch.scalar("SELECT max(created_block) FROM rh.pons_curves") or 0)
    if full:
        await w("TRUNCATE TABLE rh.pons_curves")
    await w(f"""INSERT INTO rh.pons_curves (curve, token, quote, created_block)
        SELECT t.to_addr AS curve, t.token, '{ZERO}', min(t.block_number)
        FROM rh.token_transfers t
        WHERE t.kind = 'erc20' AND t.from_addr = '{ZERO}' AND t.block_number > {since} AND t.to_addr IN (
            SELECT DISTINCT address FROM rh.logs WHERE topic0 = '{PONS_LAUNCH}' AND block_number > {since})
        GROUP BY curve, t.token""", timeout=3600)
    # quote token for curves that have paid something out but are still marked ETH: the
    # ERC20 they pay OUT most that is not their own token (bounded to new curves)
    await w(f"""INSERT INTO rh.pons_curves (curve, token, quote, created_block)
        SELECT c.curve, c.token, q.quote, c.created_block FROM rh.pons_curves c
        INNER JOIN (
            SELECT curve, argMax(token, n) AS quote FROM (
                SELECT t.from_addr AS curve, t.token AS token, count() AS n
                FROM rh.token_transfers t INNER JOIN rh.pons_curves c2 ON c2.curve = t.from_addr
                WHERE t.kind = 'erc20' AND t.token != c2.token AND c2.created_block > {since}
                GROUP BY curve, token) GROUP BY curve) q ON q.curve = c.curve
        WHERE c.created_block > {since}""", timeout=3600)
    await w("OPTIMIZE TABLE rh.pons_curves FINAL")
    if full:
        await w("""CREATE TABLE IF NOT EXISTS rh.token_price_daily (token String, day Date, px Float64)
                   ENGINE = ReplacingMergeTree ORDER BY (token, day)""")
        await w("TRUNCATE TABLE rh.token_price_daily")
        await w("INSERT INTO rh.token_price_daily SELECT token, toDate(timestamp), argMax(price_usd, timestamp) FROM rh.token_prices GROUP BY token, toDate(timestamp)", timeout=3600)


async def build_v4_pool_keys() -> None:
    """Every V4 pool's full PoolKey (incl. tickSpacing, which dex_pools lacks) from the
    PoolManager Initialize log — what a route builder needs to swap into a pool."""
    await w("""CREATE TABLE IF NOT EXISTS rh.v4_pool_keys (
        pool String, token0 String, token1 String, fee UInt32, tick_spacing Int32, hooks String,
        created_block UInt64, sqrt_price_x96 UInt256, tick Int32)
        ENGINE = ReplacingMergeTree ORDER BY pool""")
    start = int(await ch.scalar("SELECT max(created_block) FROM rh.v4_pool_keys") or 0) + 1
    await w(f"""INSERT INTO rh.v4_pool_keys
        SELECT topic1 AS pool, concat('0x', substring(topic2, 27, 40)) AS token0,
               concat('0x', substring(topic3, 27, 40)) AS token1,
               toUInt32(reinterpretAsUInt256(reverse(unhex(substring(data, 3, 64))))) AS fee,
               toInt32(reinterpretAsInt256(reverse(unhex(substring(data, 67, 64))))) AS tick_spacing,
               concat('0x', substring(data, 155, 40)) AS hooks, block_number AS created_block,
               reinterpretAsUInt256(reverse(unhex(substring(data, 195, 64)))) AS sqrt_price_x96,
               toInt32(reinterpretAsInt256(reverse(unhex(substring(data, 259, 64))))) AS tick
        FROM rh.logs WHERE address = '{V4_PM}' AND topic0 = '{V4_INIT}' AND block_number >= {start}""", timeout=3600)


async def main() -> None:
    args = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a)
    chunk = int(args.get("--chunk", 500_000))
    if "--weth-only" in sys.argv:
        await build_weth_price()
        await reprice_eth_legs()
        return
    if "--pons-only" in sys.argv or "--keys-only" in sys.argv:
        if "--pons-only" in sys.argv:
            await build_pons_curves(full=True)
            n = await ch.scalar("SELECT count() FROM rh.pons_curves")
            hi = int(await ch.scalar("SELECT max(block_number) FROM rh.logs"))
            lo = int(args.get("--from", 1))
            await w("ALTER TABLE rh.dex_swaps DELETE WHERE dex = 'pons-curve'")
            print(f"pons curves: {n} quotes: {await ch.q('SELECT quote, count() n FROM rh.pons_curves GROUP BY quote ORDER BY n DESC LIMIT 5')}; curve trades {lo}..{hi}", flush=True)
            while lo <= hi:
                h2 = min(lo + 2_000_000 - 1, hi)
                await w(pons_sql(lo, h2))
                lo = h2 + 1
            print("pons rows:", await ch.scalar("SELECT count() FROM rh.dex_swaps WHERE dex='pons-curve'"), flush=True)
        if "--keys-only" in sys.argv:
            await build_v4_pool_keys()
            print("v4_pool_keys:", await ch.scalar("SELECT count() FROM rh.v4_pool_keys"), flush=True)
        return
    await w("ALTER TABLE rh.dex_swaps ADD COLUMN IF NOT EXISTS usd Float64 DEFAULT 0")
    await w("ALTER TABLE rh.dex_swaps ADD INDEX IF NOT EXISTS idx_tin token_in TYPE bloom_filter GRANULARITY 4")
    await w("ALTER TABLE rh.dex_swaps ADD INDEX IF NOT EXISTS idx_tout token_out TYPE bloom_filter GRANULARITY 4")
    await w("ALTER TABLE rh.dex_swaps ADD INDEX IF NOT EXISTS idx_tx tx_hash TYPE bloom_filter GRANULARITY 4")
    hi_logs = int(await ch.scalar("SELECT max(block_number) FROM rh.logs"))
    start = int(args.get("--from", 0) or (int(await ch.scalar("SELECT max(block_number) FROM rh.dex_swaps") or 0) + 1))
    end = int(args.get("--to", hi_logs))
    print(f"dex_swaps: {start}..{end} in {chunk}-block chunks", flush=True)
    await build_weth_price(days=3)
    await build_pons_curves(full="--full-curves" in sys.argv)
    await build_v4_pool_keys()
    lo = start
    while lo <= end:
        hi = min(lo + chunk - 1, end)
        t = time.time()
        await w(chunk_sql(lo, hi))
        await w(pons_sql(lo, hi))
        n = await ch.scalar(f"SELECT count() FROM rh.dex_swaps WHERE block_number BETWEEN {lo} AND {hi}", timeout=120)
        print(f"  {lo}..{hi}: {n} swaps ({time.time() - t:.0f}s)", flush=True)
        lo = hi + 1
    tot = await ch.scalar("SELECT count() FROM rh.dex_swaps")
    vol = await ch.scalar("SELECT round(sum(usd)) FROM rh.dex_swaps")
    print(f"done: {tot} swaps, ${vol} priced volume", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
