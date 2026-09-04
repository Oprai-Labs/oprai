"""The analytics layer — clean, query-friendly fact tables at the right grain, so any
question is an aggregation over millions of rows (milliseconds), never a scan of the
billions in logs / token_transfers. Every venue's semantics are resolved ONCE here
(swapper vs pool deltas, decimals, quote side, the real actor behind a relayer).

  trades         one row per swap leg that touches a non-quote token, keyed by the
                 ACTOR (tx sender, or the ERC-4337 account behind a bundler):
                 side buy/sell, human amounts, USD, price, venue, pool, hook.
                 ORDER BY (actor, ts) + projection by (token, ts).
  launches       one row per launched token: dev, launchpad (+source), launch time,
                 curve/hook, graduated (+when).
  token_supply   minted − burned per token, kept live by a materialized view on
                 token_transfers.
  token_decimals decimals from the node for every traded token.
  token_hour / wallet_hour      aggregate states per hour (windows: 24h, 7d …).
  token_state / wallet_state    aggregate states over all time (ATH, first/last,
                                volume, traders …); finalized by the *_v views.
The materialized views keep token_hour/wallet_hour/token_state/wallet_state current
on every insert into trades, so freshness = how often dex_swaps + trades are appended
(the incremental run takes seconds; run it every few minutes).

  python3 -m api.build_analytics --init            # tables + views (idempotent)
  python3 -m api.build_analytics --decimals        # resolve decimals for new tokens
  python3 -m api.build_analytics --backfill        # trades from dex_swaps, all history
  python3 -m api.build_analytics --launches        # rebuild launches
  python3 -m api.build_analytics --incremental     # new dex_swaps → trades (+ views)
"""
from __future__ import annotations

import asyncio
import sys
import time

import httpx

from . import ch, node
from .ch import CH_URL, CH_USER, CH_PASS, CH_DB
from .reports import _HOOK_LAUNCHPAD, _LAUNCHPAD_ROUTERS

ZERO = "0x0000000000000000000000000000000000000000"
DEAD = "0x000000000000000000000000000000000000dead"
WETH = "0x0bd7d308f8e1639fab988df18a8011f41eacad73"
USDG = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
USDE = "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34"
QUOTES = (ZERO, WETH, USDG, USDE)
T_USEROP = "0x49628fd1471006c1482da88028e9ce4dbb080b815c9b0344d39e5a8e6ec1419f"
PONS_HOOK = "0xe5e702641ea86f4ae6cc3cdaed2b886f976be044"


async def w(sql: str, timeout: float = 3600) -> None:
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(CH_URL, params={"user": CH_USER, "password": CH_PASS, "database": CH_DB,
                                          "max_execution_time": int(timeout), "max_memory_usage": 16_000_000_000},
                         content=sql.encode())
        if r.status_code != 200:
            raise RuntimeError(r.text[:500])


QIN = "','".join(QUOTES)


# ── DDL ────────────────────────────────────────────────────────────────────
async def init() -> None:
    await w("""CREATE TABLE IF NOT EXISTS rh.token_decimals (token String, decimals UInt8, resolved UInt8)
               ENGINE = ReplacingMergeTree ORDER BY token""")
    await w(f"""CREATE DICTIONARY IF NOT EXISTS rh.token_decimals_dict (token String, decimals UInt8)
               PRIMARY KEY token SOURCE(CLICKHOUSE(TABLE 'token_decimals' DB 'rh' USER '{CH_USER}' PASSWORD '{CH_PASS}'))
               LAYOUT(COMPLEX_KEY_HASHED()) LIFETIME(MIN 600 MAX 1200)""")

    await w("""CREATE TABLE IF NOT EXISTS rh.token_supply (token String, minted Float64, burned Float64)
               ENGINE = SummingMergeTree ORDER BY token""")
    await w(f"""CREATE MATERIALIZED VIEW IF NOT EXISTS rh.token_supply_mv TO rh.token_supply AS
               SELECT token, sumIf(toFloat64(value), from_addr = '{ZERO}') AS minted,
                      sumIf(toFloat64(value), to_addr IN ('{ZERO}', '{DEAD}')) AS burned
               FROM rh.token_transfers
               WHERE kind = 'erc20' AND (from_addr = '{ZERO}' OR to_addr IN ('{ZERO}', '{DEAD}'))
               GROUP BY token""")

    await w("""CREATE TABLE IF NOT EXISTS rh.trades (
        ts DateTime, block UInt64, tx_hash String, log_index UInt32,
        actor String, relayed UInt8,
        side LowCardinality(String), token String, token_amount Float64,
        quote String, quote_amount Float64, usd Float64, price_usd Float64,
        venue LowCardinality(String), pool String, hook String,
        PROJECTION p_token (SELECT * ORDER BY token, ts),
        INDEX idx_tx tx_hash TYPE bloom_filter GRANULARITY 4
    ) ENGINE = MergeTree PARTITION BY toYYYYMM(ts) ORDER BY (actor, ts, log_index)""")

    await w("""CREATE TABLE IF NOT EXISTS rh.launches (
        token String, dev String, dev_source LowCardinality(String),
        launchpad LowCardinality(String), launchpad_source LowCardinality(String),
        launch_ts DateTime, launch_block UInt64, curve String, hook String, first_pool String,
        graduated UInt8, graduated_ts DateTime
    ) ENGINE = ReplacingMergeTree ORDER BY token""")

    # hourly buckets (windows) and all-time states — kept live by MVs on trades
    await w("""CREATE TABLE IF NOT EXISTS rh.token_hour (
        token String, hour DateTime,
        trades AggregateFunction(count), buys AggregateFunction(countIf, UInt8), sells AggregateFunction(countIf, UInt8),
        vol_usd AggregateFunction(sum, Float64), buy_usd AggregateFunction(sumIf, Float64, UInt8), sell_usd AggregateFunction(sumIf, Float64, UInt8),
        traders AggregateFunction(uniq, String), buyers AggregateFunction(uniqIf, String, UInt8),
        hi AggregateFunction(max, Float64), lo AggregateFunction(min, Float64),
        open AggregateFunction(argMin, Float64, DateTime), close AggregateFunction(argMax, Float64, DateTime),
        tok_amt AggregateFunction(sum, Float64)
    ) ENGINE = AggregatingMergeTree ORDER BY (token, hour)""")
    await w("ALTER TABLE rh.token_hour ADD COLUMN IF NOT EXISTS tok_amt AggregateFunction(sum, Float64)")
    await w("DROP VIEW IF EXISTS rh.token_hour_mv")
    await w("""CREATE MATERIALIZED VIEW IF NOT EXISTS rh.token_hour_mv TO rh.token_hour AS
        SELECT token, toStartOfHour(ts) AS hour,
               countState() AS trades, countIfState(side = 'buy') AS buys, countIfState(side = 'sell') AS sells,
               sumState(usd) AS vol_usd, sumIfState(usd, side = 'buy') AS buy_usd, sumIfState(usd, side = 'sell') AS sell_usd,
               uniqState(actor) AS traders, uniqIfState(actor, side = 'buy') AS buyers,
               maxState(price_usd) AS hi, minState(if(price_usd > 0, price_usd, inf)) AS lo,
               argMinState(price_usd, ts) AS open, argMaxState(price_usd, ts) AS close,
               sumState(token_amount) AS tok_amt
        FROM rh.trades WHERE usd >= 1 GROUP BY token, hour""")

    await w("""CREATE TABLE IF NOT EXISTS rh.wallet_hour (
        actor String, hour DateTime,
        trades AggregateFunction(count), buys AggregateFunction(countIf, UInt8), sells AggregateFunction(countIf, UInt8),
        vol_usd AggregateFunction(sum, Float64), buy_usd AggregateFunction(sumIf, Float64, UInt8), sell_usd AggregateFunction(sumIf, Float64, UInt8),
        tokens AggregateFunction(uniq, String)
    ) ENGINE = AggregatingMergeTree ORDER BY (actor, hour)""")
    await w("""CREATE MATERIALIZED VIEW IF NOT EXISTS rh.wallet_hour_mv TO rh.wallet_hour AS
        SELECT actor, toStartOfHour(ts) AS hour,
               countState() AS trades, countIfState(side = 'buy') AS buys, countIfState(side = 'sell') AS sells,
               sumState(usd) AS vol_usd, sumIfState(usd, side = 'buy') AS buy_usd, sumIfState(usd, side = 'sell') AS sell_usd,
               uniqState(token) AS tokens
        FROM rh.trades GROUP BY actor, hour""")

    await w("""CREATE TABLE IF NOT EXISTS rh.token_state (
        token String,
        trades AggregateFunction(count), buys AggregateFunction(countIf, UInt8), sells AggregateFunction(countIf, UInt8),
        vol_usd AggregateFunction(sum, Float64), buy_usd AggregateFunction(sumIf, Float64, UInt8), sell_usd AggregateFunction(sumIf, Float64, UInt8),
        traders AggregateFunction(uniq, String), buyers AggregateFunction(uniqIf, String, UInt8),
        first_ts AggregateFunction(min, DateTime), last_ts AggregateFunction(max, DateTime),
        ath AggregateFunction(max, Float64), ath_ts AggregateFunction(argMax, DateTime, Float64),
        first_price AggregateFunction(argMin, Float64, DateTime), last_price AggregateFunction(argMax, Float64, DateTime),
        venues AggregateFunction(groupUniqArray, String)
    ) ENGINE = AggregatingMergeTree ORDER BY token""")
    await w("""CREATE MATERIALIZED VIEW IF NOT EXISTS rh.token_state_mv TO rh.token_state AS
        SELECT token,
               countState() AS trades, countIfState(side = 'buy') AS buys, countIfState(side = 'sell') AS sells,
               sumState(usd) AS vol_usd, sumIfState(usd, side = 'buy') AS buy_usd, sumIfState(usd, side = 'sell') AS sell_usd,
               uniqState(actor) AS traders, uniqIfState(actor, side = 'buy') AS buyers,
               minState(ts) AS first_ts, maxState(ts) AS last_ts,
               maxState(if(usd >= 5, price_usd, 0)) AS ath, argMaxState(ts, if(usd >= 5, price_usd, 0)) AS ath_ts,
               argMinState(price_usd, ts) AS first_price, argMaxState(price_usd, ts) AS last_price,
               groupUniqArrayState(toString(venue)) AS venues
        FROM rh.trades WHERE price_usd > 0 GROUP BY token""")

    await w("""CREATE TABLE IF NOT EXISTS rh.wallet_state (
        actor String,
        trades AggregateFunction(count), buys AggregateFunction(countIf, UInt8), sells AggregateFunction(countIf, UInt8),
        vol_usd AggregateFunction(sum, Float64), buy_usd AggregateFunction(sumIf, Float64, UInt8), sell_usd AggregateFunction(sumIf, Float64, UInt8),
        tokens AggregateFunction(uniq, String), first_ts AggregateFunction(min, DateTime), last_ts AggregateFunction(max, DateTime),
        venues AggregateFunction(groupUniqArray, String), relayed AggregateFunction(sum, UInt8)
    ) ENGINE = AggregatingMergeTree ORDER BY actor""")
    await w("""CREATE MATERIALIZED VIEW IF NOT EXISTS rh.wallet_state_mv TO rh.wallet_state AS
        SELECT actor,
               countState() AS trades, countIfState(side = 'buy') AS buys, countIfState(side = 'sell') AS sells,
               sumState(usd) AS vol_usd, sumIfState(usd, side = 'buy') AS buy_usd, sumIfState(usd, side = 'sell') AS sell_usd,
               uniqState(token) AS tokens, minState(ts) AS first_ts, maxState(ts) AS last_ts,
               groupUniqArrayState(toString(venue)) AS venues, sumState(relayed) AS relayed
        FROM rh.trades GROUP BY actor""")

    # finalized, joined views — what the query layer exposes
    await w(f"""CREATE OR REPLACE VIEW rh.token_state_v AS
        SELECT s.token AS token,
               countMerge(s.trades) AS trades, countIfMerge(s.buys) AS buys, countIfMerge(s.sells) AS sells,
               sumMerge(s.vol_usd) AS vol_usd, sumIfMerge(s.buy_usd) AS buy_usd, sumIfMerge(s.sell_usd) AS sell_usd,
               uniqMerge(s.traders) AS traders, uniqIfMerge(s.buyers) AS buyers,
               minMerge(s.first_ts) AS first_trade_ts, maxMerge(s.last_ts) AS last_trade_ts,
               maxMerge(s.ath) AS ath_price_usd, argMaxMerge(s.ath_ts) AS ath_ts,
               argMinMerge(s.first_price) AS first_price_usd, argMaxMerge(s.last_price) AS price_usd,
               groupUniqArrayMerge(s.venues) AS venues,
               (sp.minted - sp.burned) / pow(10, dictGetOrDefault('rh.token_decimals_dict', 'decimals', tuple(s.token), 18)) AS supply,
               argMaxMerge(s.last_price) * (sp.minted - sp.burned) / pow(10, dictGetOrDefault('rh.token_decimals_dict', 'decimals', tuple(s.token), 18)) AS mcap_usd,
               maxMerge(s.ath) * (sp.minted - sp.burned) / pow(10, dictGetOrDefault('rh.token_decimals_dict', 'decimals', tuple(s.token), 18)) AS ath_mcap_usd,
               if(maxMerge(s.ath) > 0, 1 - argMaxMerge(s.last_price) / maxMerge(s.ath), 0) AS drawdown,
               l.dev AS dev, l.launchpad AS launchpad, l.launch_ts AS launch_ts, l.graduated AS graduated, l.graduated_ts AS graduated_ts,
               tm.holders AS holders
        FROM rh.token_state s
        LEFT JOIN rh.token_supply sp ON sp.token = s.token
        LEFT JOIN rh.launches l ON l.token = s.token
        LEFT JOIN rh.token_metrics tm ON tm.token = s.token
        GROUP BY s.token, sp.minted, sp.burned, l.dev, l.launchpad, l.launch_ts, l.graduated, l.graduated_ts, tm.holders""")
    await w("""CREATE OR REPLACE VIEW rh.wallet_state_v AS
        SELECT actor AS wallet, countMerge(trades) AS trades, countIfMerge(buys) AS buys, countIfMerge(sells) AS sells,
               sumMerge(vol_usd) AS vol_usd, sumIfMerge(buy_usd) AS buy_usd, sumIfMerge(sell_usd) AS sell_usd,
               uniqMerge(tokens) AS tokens, minMerge(first_ts) AS first_trade_ts, maxMerge(last_ts) AS last_trade_ts,
               groupUniqArrayMerge(venues) AS venues, sumMerge(relayed) AS relayed_trades
        FROM rh.wallet_state GROUP BY actor""")
    await w("""CREATE OR REPLACE VIEW rh.token_hour_v AS
        SELECT token, hour, countMerge(trades) AS trades, countIfMerge(buys) AS buys, countIfMerge(sells) AS sells,
               sumMerge(vol_usd) AS vol_usd, sumIfMerge(buy_usd) AS buy_usd, sumIfMerge(sell_usd) AS sell_usd,
               uniqMerge(traders) AS traders, uniqIfMerge(buyers) AS buyers,
               maxMerge(hi) AS high, minMerge(lo) AS low, argMinMerge(open) AS open, argMaxMerge(close) AS close,
               vol_usd / nullIf(sumMerge(tok_amt), 0) AS vwap   -- vol_usd = the merged alias above
        FROM rh.token_hour GROUP BY token, hour""")
    await w("""CREATE OR REPLACE VIEW rh.wallet_hour_v AS
        SELECT actor AS wallet, hour, countMerge(trades) AS trades, countIfMerge(buys) AS buys, countIfMerge(sells) AS sells,
               sumMerge(vol_usd) AS vol_usd, sumIfMerge(buy_usd) AS buy_usd, sumIfMerge(sell_usd) AS sell_usd, uniqMerge(tokens) AS tokens
        FROM rh.wallet_hour GROUP BY actor, hour""")
    await w("""CREATE TABLE IF NOT EXISTS rh.token_stats (
        token String, symbol String, name String,
        trades UInt64, buys UInt64, sells UInt64, vol_usd Float64, buy_usd Float64, sell_usd Float64,
        traders UInt64, buyers UInt64, first_trade_ts DateTime, last_trade_ts DateTime,
        price_usd Float64, first_price_usd Float64, ath_price_usd Float64, ath_ts DateTime,
        supply Float64, mcap_usd Float64, ath_mcap_usd Float64, first_mcap_usd Float64, drawdown Float64, ath_multiple Float64,
        vol_24h_usd Float64, trades_24h UInt64, buyers_24h UInt64, vol_7d_usd Float64,
        holders UInt64, dev String, launchpad LowCardinality(String), launch_ts DateTime, graduated UInt8, graduated_ts DateTime,
        venues Array(String), updated_at DateTime,
        INDEX idx_dev dev TYPE bloom_filter GRANULARITY 2
    ) ENGINE = ReplacingMergeTree(updated_at) ORDER BY token""")
    print("  init ok", flush=True)


# ── decimals ───────────────────────────────────────────────────────────────
async def decimals() -> None:
    toks = [r["t"] for r in await ch.q(f"""
        SELECT DISTINCT t FROM (
            SELECT token_in AS t FROM rh.dex_swaps UNION ALL SELECT token_out AS t FROM rh.dex_swaps)
        WHERE t NOT IN ('{QIN}') AND t NOT IN (SELECT token FROM rh.token_decimals)""", timeout=1800)]
    print(f"  decimals to resolve: {len(toks)}", flush=True)
    known = [(USDG, 6), (USDE, 18), (WETH, 18), (ZERO, 18)]
    rows = [f"('{t}',{d},1)" for t, d in known]
    for i in range(0, len(toks), 400):
        chunk = toks[i:i + 400]
        batch = [{"jsonrpc": "2.0", "id": j, "method": "eth_call",
                  "params": [{"to": t, "data": "0x313ce567"}, "latest"]} for j, t in enumerate(chunk)]
        try:
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.post(node.NODE, json=batch)
                res = {x["id"]: x.get("result") for x in r.json()}
        except Exception:
            res = {}
        for j, t in enumerate(chunk):
            v = res.get(j)
            if v and v not in ("0x", "") and len(v) >= 66:
                d = int(v[2:66], 16)
                rows.append(f"('{t}',{d if 0 < d <= 36 else 18},{1 if 0 < d <= 36 else 0})")
            else:
                rows.append(f"('{t}',18,0)")
        if len(rows) >= 5000:
            await w("INSERT INTO rh.token_decimals (token, decimals, resolved) VALUES " + ",".join(rows))
            rows = []
        if i % 40000 == 0:
            print(f"    {i}/{len(toks)}", flush=True)
    if rows:
        await w("INSERT INTO rh.token_decimals (token, decimals, resolved) VALUES " + ",".join(rows))
    await w("OPTIMIZE TABLE rh.token_decimals FINAL")
    await w("SYSTEM RELOAD DICTIONARY rh.token_decimals_dict")
    print("  decimals:", await ch.scalar("SELECT count() FROM rh.token_decimals"), flush=True)


async def symbols() -> None:
    """symbol()/name() from the node for traded tokens not yet resolved (contracts.symbol is empty)."""
    await w("""CREATE TABLE IF NOT EXISTS rh.token_symbols (token String, symbol String, name String)
               ENGINE = ReplacingMergeTree ORDER BY token""")
    toks = [r["token"] for r in await ch.q("SELECT DISTINCT token FROM rh.token_state WHERE token NOT IN (SELECT token FROM rh.token_symbols)", timeout=600)]
    print(f"  symbols to resolve: {len(toks)}", flush=True)
    rows = []
    for i in range(0, len(toks), 300):
        chunk = toks[i:i + 300]
        batch = []
        for j, t in enumerate(chunk):
            batch.append({"jsonrpc": "2.0", "id": 2 * j, "method": "eth_call", "params": [{"to": t, "data": "0x95d89b41"}, "latest"]})
            batch.append({"jsonrpc": "2.0", "id": 2 * j + 1, "method": "eth_call", "params": [{"to": t, "data": "0x06fdde03"}, "latest"]})
        try:
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.post(node.NODE, json=batch)
                res = {x["id"]: x.get("result") for x in r.json()}
        except Exception:
            res = {}
        for j, t in enumerate(chunk):
            sy = node._decode_str(res.get(2 * j) or "0x") or ""
            nm = node._decode_str(res.get(2 * j + 1) or "0x") or ""
            esc = lambda x: x.replace("\\", "\\\\").replace("'", "\\'")[:64]
            rows.append(f"('{t}','{esc(sy)}','{esc(nm)}')")
        if len(rows) >= 3000:
            await w("INSERT INTO rh.token_symbols (token, symbol, name) VALUES " + ",".join(rows))
            rows = []
        if i % 30000 == 0:
            print(f"    {i}/{len(toks)}", flush=True)
    if rows:
        await w("INSERT INTO rh.token_symbols (token, symbol, name) VALUES " + ",".join(rows))
    await w("OPTIMIZE TABLE rh.token_symbols FINAL")
    print("  token_symbols:", await ch.scalar("SELECT count() FROM rh.token_symbols"), flush=True)


# ── trades ─────────────────────────────────────────────────────────────────
def trades_sql(lo: int, hi: int) -> str:
    dec = "dictGetOrDefault('rh.token_decimals_dict', 'decimals', tuple({t}), 18)"
    return f"""
    INSERT INTO rh.trades (ts, block, tx_hash, log_index, actor, relayed, side, token, token_amount,
                           quote, quote_amount, usd, price_usd, venue, pool, hook)
    WITH
      tx AS (SELECT block_number, hash, from_addr FROM rh.transactions WHERE block_number BETWEEN {lo} AND {hi}),
      uo AS (SELECT block_number, tx_hash, any(concat('0x', substring(topic2, 27, 40))) AS acct
             FROM rh.logs WHERE topic0 = '{T_USEROP}' AND block_number BETWEEN {lo} AND {hi} GROUP BY block_number, tx_hash),
      hk AS (SELECT pool, hooks FROM rh.v4_pool_keys)
    SELECT s.timestamp, s.block_number, s.tx_hash, s.log_index,
           if(uo.acct != '', uo.acct, tx.from_addr) AS actor, if(uo.acct != '', 1, 0) AS relayed,
           side, token, token_amount, quote, quote_amount, s.usd,
           if(token_amount > 0 AND s.usd > 0, s.usd / token_amount, 0) AS price_usd,
           s.dex, s.pool, ifNull(hk.hooks, '') AS hook
    FROM (
        SELECT *, 'buy' AS side, token_out AS token,
               toFloat64(amount_out) / pow(10, {dec.format(t='token_out')}) AS token_amount,
               token_in AS quote, toFloat64(amount_in) / pow(10, {dec.format(t='token_in')}) AS quote_amount
        FROM rh.dex_swaps WHERE block_number BETWEEN {lo} AND {hi} AND token_out NOT IN ('{QIN}')
        UNION ALL
        SELECT *, 'sell' AS side, token_in AS token,
               toFloat64(amount_in) / pow(10, {dec.format(t='token_in')}) AS token_amount,
               token_out AS quote, toFloat64(amount_out) / pow(10, {dec.format(t='token_out')}) AS quote_amount
        FROM rh.dex_swaps WHERE block_number BETWEEN {lo} AND {hi} AND token_in NOT IN ('{QIN}')
    ) AS s
    INNER JOIN tx ON tx.block_number = s.block_number AND tx.hash = s.tx_hash
    LEFT JOIN uo ON uo.block_number = s.block_number AND uo.tx_hash = s.tx_hash
    LEFT JOIN hk ON hk.pool = s.pool"""


async def backfill(chunk: int = 1_000_000) -> None:
    hi = int(await ch.scalar("SELECT max(block_number) FROM rh.dex_swaps"))
    lo = int(await ch.scalar("SELECT max(block) FROM rh.trades") or 0) + 1
    print(f"  trades {lo}..{hi}", flush=True)
    while lo <= hi:
        h = min(lo + chunk - 1, hi)
        t = time.time()
        await w(trades_sql(lo, h))
        print(f"    {lo}..{h} ({time.time() - t:.0f}s)", flush=True)
        lo = h + 1
    print("  trades:", await ch.scalar("SELECT count() FROM rh.trades"), flush=True)


# ── launches ───────────────────────────────────────────────────────────────
async def launches() -> None:
    hooks_case = " ".join(f"WHEN '{h}' THEN '{n.replace(chr(39), '')}'" for h, n in _HOOK_LAUNCHPAD.items())
    routers_case = " ".join(f"WHEN '{a}' THEN '{n.replace(chr(39), '')}'" for a, n in _LAUNCHPAD_ROUTERS.items())
    hooks_in = "','".join(_HOOK_LAUNCHPAD)
    routers_in = "','".join(_LAUNCHPAD_ROUTERS)
    await w("TRUNCATE TABLE rh.launches")
    await w(f"""INSERT INTO rh.launches
    WITH
      pons AS (SELECT token, curve, created_block FROM rh.pons_curves),
      hooked AS (SELECT token, argMin(pool, created_block) AS pool, argMin(hooks, created_block) AS hooks, min(created_block) AS blk
                 FROM (SELECT token0 AS token, pool, hooks, created_block FROM rh.v4_pool_keys WHERE hooks IN ('{hooks_in}') AND token0 NOT IN ('{QIN}')
                       UNION ALL
                       SELECT token1 AS token, pool, hooks, created_block FROM rh.v4_pool_keys WHERE hooks IN ('{hooks_in}') AND token1 NOT IN ('{QIN}'))
                 GROUP BY token),
      anypool AS (SELECT token, min(created_block) AS blk, argMin(pool, created_block) AS pool
                  FROM (SELECT token0 AS token, pool, created_block FROM rh.dex_pools WHERE token0 NOT IN ('{QIN}')
                        UNION ALL SELECT token1 AS token, pool, created_block FROM rh.dex_pools WHERE token1 NOT IN ('{QIN}'))
                  GROUP BY token),
      openpool AS (SELECT token, min(created_block) AS blk
                   FROM (SELECT token0 AS token, created_block FROM rh.dex_pools WHERE (dex != 'uniswap-v4' OR hooks NOT IN ('{hooks_in}')) AND token0 NOT IN ('{QIN}')
                         UNION ALL SELECT token1 AS token, created_block FROM rh.dex_pools WHERE (dex != 'uniswap-v4' OR hooks NOT IN ('{hooks_in}')) AND token1 NOT IN ('{QIN}'))
                   GROUP BY token),
      creators AS (SELECT token, argMax(dev, block) AS dev FROM rh.launch_creators GROUP BY token),
      ctr AS (SELECT address, deployer, creation_block, creation_tx FROM rh.contracts FINAL WHERE is_token = 1),
      -- creation-tx callee: hit transactions through its primary key (block) + hash, never a hash join over 600M rows
      ctxt AS (SELECT t.hash AS creation_tx, t.to_addr AS callee FROM rh.transactions t
               WHERE t.block_number IN (SELECT creation_block FROM ctr) AND t.hash IN (SELECT creation_tx FROM ctr)),
      ctx AS (SELECT c.address AS token, x.callee AS callee FROM ctr c INNER JOIN ctxt x ON x.creation_tx = c.creation_tx)
    SELECT
      u.token,
      multiIf(cr.dev != '', cr.dev, ct.deployer != '', ct.deployer, '') AS dev,
      multiIf(cr.dev != '', 'launch event', ct.deployer != '', 'contract deployer', '') AS dev_source,
      multiIf(p.curve != '', 'Pons', h.hooks != '', CASE h.hooks {hooks_case} ELSE 'hook' END,
              ctx.callee IN ('{routers_in}'), CASE ctx.callee {routers_case} ELSE '' END, ap.pool != '', 'direct pool', 'unknown') AS launchpad,
      multiIf(p.curve != '', 'factory', h.hooks != '', 'uniswap-v4 hook', ctx.callee IN ('{routers_in}'), 'creation router', ap.pool != '', 'first pool', '') AS launchpad_source,
      b.timestamp AS launch_ts,
      multiIf(p.curve != '', p.created_block, h.blk > 0, h.blk, ct.creation_block > 0, ct.creation_block, ap.blk) AS launch_block,
      p.curve AS curve, h.hooks AS hook, if(h.pool != '', h.pool, ap.pool) AS first_pool,
      multiIf(p.curve != '', op.blk > p.created_block, h.hooks != '', op.blk > h.blk, 0) AS graduated,
      if(multiIf(p.curve != '', op.blk > p.created_block, h.hooks != '', op.blk > h.blk, 0), gb.timestamp, toDateTime(0)) AS graduated_ts
    FROM (SELECT token FROM pons UNION DISTINCT SELECT token FROM hooked UNION DISTINCT SELECT token FROM anypool UNION DISTINCT SELECT address AS token FROM ctr) u
    LEFT JOIN pons p ON p.token = u.token
    LEFT JOIN hooked h ON h.token = u.token
    LEFT JOIN anypool ap ON ap.token = u.token
    LEFT JOIN openpool op ON op.token = u.token
    LEFT JOIN creators cr ON cr.token = u.token
    LEFT JOIN ctr ct ON ct.address = u.token
    LEFT JOIN ctx ON ctx.token = u.token
    LEFT JOIN rh.blocks b ON b.number = multiIf(p.curve != '', p.created_block, h.blk > 0, h.blk, ct.creation_block > 0, ct.creation_block, ap.blk)
    LEFT JOIN rh.blocks gb ON gb.number = op.blk
    WHERE u.token != ''""", timeout=3600)
    print("  launches:", await ch.q("SELECT launchpad, count() n, countIf(graduated) grad FROM rh.launches GROUP BY launchpad ORDER BY n DESC LIMIT 12"), flush=True)


def stats_sql(where_tokens: str) -> str:
    """token_stats rows for the tokens selected by `where_tokens` (SQL over `trades`
    grouped by token). Prices are hourly VWAP (vol_usd / token_amount) so one dust
    trade cannot set an ATH; ATH/first/last come from hours with ≥ $20 volume."""
    dec = "dictGetOrDefault('rh.token_decimals_dict', 'decimals', tuple(h.token), 18)"
    return f"""
    INSERT INTO rh.token_stats
    WITH
      sel AS ({where_tokens}),
      hrs AS (SELECT token, hour, sumMerge(vol_usd) AS v, sumMerge(vol_usd) / nullIf(sumMerge(tok_amt), 0) AS vwap,
                     countMerge(trades) AS n, uniqIfMerge(buyers) AS b
              FROM rh.token_hour WHERE token IN (SELECT token FROM sel) GROUP BY token, hour),
      px AS (SELECT token, maxIf(vwap, v >= 20) AS ath, argMaxIf(hour, vwap, v >= 20) AS ath_hour,
                    argMinIf(vwap, hour, v >= 20) AS first_px, argMaxIf(vwap, hour, v >= 5) AS last_px,
                    sumIf(v, hour >= now() - INTERVAL 24 HOUR) AS v24, sumIf(n, hour >= now() - INTERVAL 24 HOUR) AS n24,
                    sumIf(v, hour >= now() - INTERVAL 7 DAY) AS v7
             FROM hrs GROUP BY token),
      b24 AS (SELECT token, uniq(actor) AS buyers24 FROM rh.trades WHERE token IN (SELECT token FROM sel) AND side = 'buy' AND ts >= now() - INTERVAL 24 HOUR GROUP BY token),
      st AS (SELECT token, countMerge(trades) AS trades, countIfMerge(buys) AS buys, countIfMerge(sells) AS sells,
                    sumMerge(vol_usd) AS vol_usd, sumIfMerge(buy_usd) AS buy_usd, sumIfMerge(sell_usd) AS sell_usd,
                    uniqMerge(traders) AS traders, uniqIfMerge(buyers) AS buyers, minMerge(first_ts) AS first_ts, maxMerge(last_ts) AS last_ts,
                    argMaxMerge(last_price) AS last_trade_px, groupUniqArrayMerge(venues) AS venues
             FROM rh.token_state WHERE token IN (SELECT token FROM sel) GROUP BY token)
    SELECT h.token, ifNull(c.symbol, ''), ifNull(c.name, ''),
           st.trades, st.buys, st.sells, st.vol_usd, st.buy_usd, st.sell_usd, st.traders, st.buyers, st.first_ts, st.last_ts,
           if(px.last_px > 0, px.last_px, st.last_trade_px) AS price_usd, px.first_px, px.ath, px.ath_hour,
           (sp.minted - sp.burned) / pow(10, {dec}) AS supply,
           if(px.last_px > 0, px.last_px, st.last_trade_px) * (sp.minted - sp.burned) / pow(10, {dec}) AS mcap_usd,
           px.ath * (sp.minted - sp.burned) / pow(10, {dec}) AS ath_mcap_usd,
           px.first_px * (sp.minted - sp.burned) / pow(10, {dec}) AS first_mcap_usd,
           if(px.ath > 0, 1 - if(px.last_px > 0, px.last_px, st.last_trade_px) / px.ath, 0) AS drawdown,
           if(px.first_px > 0, px.ath / px.first_px, 0) AS ath_multiple,
           px.v24, px.n24, ifNull(b24.buyers24, 0), px.v7,
           ifNull(tm.holders, 0), ifNull(l.dev, ''), ifNull(l.launchpad, ''), ifNull(l.launch_ts, toDateTime(0)), ifNull(l.graduated, 0), ifNull(l.graduated_ts, toDateTime(0)),
           st.venues, now()
    FROM (SELECT token FROM sel) h
    INNER JOIN st ON st.token = h.token
    LEFT JOIN px ON px.token = h.token
    LEFT JOIN b24 ON b24.token = h.token
    LEFT JOIN rh.token_supply sp ON sp.token = h.token
    LEFT JOIN rh.launches l ON l.token = h.token
    LEFT JOIN rh.token_metrics tm ON tm.token = h.token
    LEFT JOIN rh.token_symbols c ON c.token = h.token"""


async def stats(full: bool = False, since_block: int | None = None) -> None:
    """Materialize token_stats: all tokens (full) or only tokens traded since a block."""
    if full:
        sel = "SELECT DISTINCT token FROM rh.token_state"
    else:
        sel = f"SELECT DISTINCT token FROM rh.trades WHERE block > {since_block}"
    t = time.time()
    if full:
        # in slices of the token key space to keep memory bounded
        for i in range(16):
            lo, hi = format(i, "x"), format(i + 1, "x")
            cond = f"SELECT DISTINCT token FROM rh.token_state WHERE substring(token, 3, 1) >= '{lo}' AND substring(token, 3, 1) < '{hi}'" if i < 15 else \
                   "SELECT DISTINCT token FROM rh.token_state WHERE substring(token, 3, 1) >= 'f'"
            await w(stats_sql(cond), timeout=3600)
            print(f"    stats slice {i + 1}/16 ({time.time() - t:.0f}s)", flush=True)
    else:
        await w(stats_sql(sel), timeout=600)
    await w("OPTIMIZE TABLE rh.token_stats FINAL")
    print(f"  token_stats: {await ch.scalar('SELECT count() FROM rh.token_stats')} rows ({time.time() - t:.0f}s)", flush=True)


async def rebuild_hours() -> None:
    """token_hour from trades (after the MV changed): full recompute."""
    await w("TRUNCATE TABLE rh.token_hour")
    await w("""INSERT INTO rh.token_hour
        SELECT token, toStartOfHour(ts) AS hour,
               countState() AS trades, countIfState(side = 'buy') AS buys, countIfState(side = 'sell') AS sells,
               sumState(usd) AS vol_usd, sumIfState(usd, side = 'buy') AS buy_usd, sumIfState(usd, side = 'sell') AS sell_usd,
               uniqState(actor) AS traders, uniqIfState(actor, side = 'buy') AS buyers,
               maxState(price_usd) AS hi, minState(if(price_usd > 0, price_usd, inf)) AS lo,
               argMinState(price_usd, ts) AS open, argMaxState(price_usd, ts) AS close,
               sumState(token_amount) AS tok_amt
        FROM rh.trades WHERE usd >= 1 GROUP BY token, hour""", timeout=7200)
    print("  token_hour rebuilt:", await ch.scalar("SELECT count() FROM rh.token_hour"), flush=True)


async def backfill_supply() -> None:
    """Full recompute of minted/burned from history (the MV only sees inserts made
    after it was created). Truncate first so the two never add up twice."""
    await w("TRUNCATE TABLE rh.token_supply")
    await w(f"""INSERT INTO rh.token_supply
        SELECT token, sumIf(toFloat64(value), from_addr = '{ZERO}') AS minted,
               sumIf(toFloat64(value), to_addr IN ('{ZERO}', '{DEAD}')) AS burned
        FROM rh.token_transfers WHERE kind = 'erc20' AND (from_addr = '{ZERO}' OR to_addr IN ('{ZERO}', '{DEAD}'))
        GROUP BY token""", timeout=7200)
    print("  token_supply:", await ch.scalar("SELECT count() FROM rh.token_supply"), flush=True)


async def main() -> None:
    args = sys.argv[1:]
    if "--init" in args or not args:
        await init()
    if "--decimals" in args:
        await decimals()
    if "--reset" in args:
        for t in ("trades", "token_state", "wallet_state", "token_hour", "wallet_hour"):
            await w(f"TRUNCATE TABLE rh.{t}")
        print("  reset trades + aggregate states", flush=True)
    if "--supply" in args:
        await backfill_supply()
    if "--backfill" in args:
        await backfill()
    if "--symbols" in args:
        await symbols()
    if "--launches" in args:
        await launches()
    if "--hours" in args:
        await rebuild_hours()
    if "--stats" in args:
        await stats(full=True)
    if "--incremental" in args:
        since = int(await ch.scalar("SELECT max(block) FROM rh.trades") or 0)
        await decimals()
        await backfill(chunk=2_000_000)
        await symbols()
        await stats(full=False, since_block=since)


if __name__ == "__main__":
    asyncio.run(main())
