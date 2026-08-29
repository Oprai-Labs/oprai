-- ═══════════════════════════════════════════════════════════════════════════
-- Derived-table transforms — run AFTER the bulk ETL completes (full data).
-- Order matters (each depends on the previous). Idempotent-ish: TRUNCATE +
-- rebuild the derived table, since they are pure functions of the raw facts.
-- Hex-offset parsing marked [TUNE] — validate against real rows on first run.
-- ═══════════════════════════════════════════════════════════════════════════

-- helper: 32-byte topic/word → 0x + last 20 bytes (address)
-- word is a 66-char '0x…' string → address = '0x' + chars 27..66
-- substring(word, 27, 40) grabs the last 40 hex chars.

-- ── 1) dex_pools ← PairCreated (V2) / PoolCreated (V3) ───────────────────────
-- V2 PairCreated: topics=[sig, token0, token1], data=[pair(32B)][len(32B)]
-- V3 PoolCreated: topics=[sig, token0, token1, fee], data=[tickSpacing(32B)][pool(32B)]
INSERT INTO rh.dex_pools
SELECT concat('0x', lower(substring(data, 27, 40))) AS pool,       -- [TUNE] V2: pair is word 0
       concat('0x', lower(substring(topic1, 27, 40))) AS token0,
       concat('0x', lower(substring(topic2, 27, 40))) AS token1,
       'uniswap_v2' AS dex, block_number AS created_block
FROM rh.logs
WHERE topic0 = '0x7a53080ba414158be7ec69b987b5fb7d07dee101fe85488f0853ae16239d0bde'
UNION ALL
SELECT concat('0x', lower(substring(data, 91, 40))) AS pool,       -- [TUNE] V3: pool is word 1
       concat('0x', lower(substring(topic1, 27, 40))),
       concat('0x', lower(substring(topic2, 27, 40))),
       'uniswap_v3', block_number
FROM rh.logs
WHERE topic0 = '0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118';

-- ── 2) dex_swaps ← Swap events + dex_pools ──────────────────────────────────
-- V2 Swap: topics=[sig, sender, to], data=[a0In][a1In][a0Out][a1Out] (4 words)
--   in = whichever amountIn>0; out = whichever amountOut>0.
-- V3 Swap: topics=[sig, sender, recipient], data=[amount0][amount1][...]
--   signed int256; the negative side is the output.
INSERT INTO rh.dex_swaps
WITH v2 AS (
  SELECT l.block_number, l.timestamp, l.tx_hash, l.log_index, l.address AS pool,
    concat('0x', lower(substring(l.topic1, 27, 40))) AS sender,
    concat('0x', lower(substring(l.topic2, 27, 40))) AS recipient,
    reinterpretAsUInt256(reverse(unhex(substring(l.data, 3,   64)))) AS a0in,   -- [TUNE]
    reinterpretAsUInt256(reverse(unhex(substring(l.data, 67,  64)))) AS a1in,
    reinterpretAsUInt256(reverse(unhex(substring(l.data, 131, 64)))) AS a0out,
    reinterpretAsUInt256(reverse(unhex(substring(l.data, 195, 64)))) AS a1out,
    p.token0, p.token1
  FROM rh.logs l INNER JOIN rh.dex_pools p ON l.address = p.pool
  WHERE l.topic0 = '0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822'
)
SELECT block_number, timestamp, tx_hash, log_index, pool, sender, recipient,
  if(a0in > 0, token0, token1) AS token_in,
  if(a0in > 0, token1, token0) AS token_out,
  if(a0in > 0, a0in, a1in)   AS amount_in,
  if(a0in > 0, a1out, a0out) AS amount_out,
  'uniswap_v2' AS dex
FROM v2;
-- NOTE: V3 swap decode (signed amounts) added in a follow-up; V2 covers the
-- bulk of memecoin price discovery. USDG-paired swaps drive token_prices.

-- ── 3) token_prices ← USDG-paired swaps (USDG = $1 anchor) ───────────────────
-- price = USDG amount / token amount, per swap, sampled to a time series.
INSERT INTO rh.token_prices
SELECT
  if(token_in = '0x5fc5360d0400a0fd4f2af552add042d716f1d168', token_out, token_in) AS token,
  timestamp,
  if(token_in = '0x5fc5360d0400a0fd4f2af552add042d716f1d168',
     toFloat64(amount_in)  / nullIf(toFloat64(amount_out), 0),
     toFloat64(amount_out) / nullIf(toFloat64(amount_in),  0)) AS price_usd
FROM rh.dex_swaps
WHERE (token_in  = '0x5fc5360d0400a0fd4f2af552add042d716f1d168'
    OR token_out = '0x5fc5360d0400a0fd4f2af552add042d716f1d168')
  AND price_usd > 0;
-- Non-USDG tokens: chain through ETH↔USDG in a follow-up pass.

-- ── 4) token_balances ← token_transfers (SummingMergeTree) ──────────────────
INSERT INTO rh.token_balances
SELECT token, addr AS address, delta AS balance
FROM rh.token_transfers
ARRAY JOIN [to_addr, from_addr] AS addr,
           [toInt256(value), -toInt256(value)] AS delta
WHERE kind = 'erc20' AND addr NOT IN ('0x', '0x0000000000000000000000000000000000000000');

-- ── 5) wallet_token_positions ← transfers priced at swap-time USD (cost basis) ─
-- Approx avg-cost engine: value each in/out transfer at the token's price at
-- that timestamp (asof join to token_prices). Realized PnL = usd_out − cost.
INSERT INTO rh.wallet_token_positions
WITH priced AS (
  SELECT tt.to_addr AS wallet, tt.token, 'in' AS side, tt.timestamp AS ts,
         toFloat64(tt.value) AS qty, toFloat64(tt.value) * p.price_usd AS usd
  FROM rh.token_transfers tt
  ASOF LEFT JOIN rh.token_prices p ON tt.token = p.token AND tt.timestamp >= p.timestamp
  WHERE tt.kind = 'erc20'
  UNION ALL
  SELECT tt.from_addr AS wallet, tt.token, 'out', tt.timestamp,
         toFloat64(tt.value), toFloat64(tt.value) * p.price_usd
  FROM rh.token_transfers tt
  ASOF LEFT JOIN rh.token_prices p ON tt.token = p.token AND tt.timestamp >= p.timestamp
  WHERE tt.kind = 'erc20'
)
SELECT wallet, token,
  sumIf(qty, side='in')  AS qty_in,  sumIf(usd, side='in')  AS usd_in,
  sumIf(qty, side='out') AS qty_out, sumIf(usd, side='out') AS usd_out,
  usd_in / nullIf(qty_in, 0) AS avg_cost,
  usd_out - (avg_cost * qty_out) AS realized_pnl,
  qty_in - qty_out AS holding,
  minIf(ts, side='in') AS first_buy_ts, max(ts) AS last_ts
FROM priced
WHERE wallet NOT IN ('0x', '0x0000000000000000000000000000000000000000')
GROUP BY wallet, token;

-- ── 6) wallet_metrics + smart_wallets (smart = top realized-PnL percentile) ──
INSERT INTO rh.wallet_metrics
SELECT wallet,
  sum(realized_pnl) AS realized_pnl, 0 AS unrealized_pnl,
  sum(realized_pnl) / nullIf(sum(usd_in), 0) AS roi,
  countIf(realized_pnl > 0) / nullIf(countIf(qty_out > 0), 0) AS win_rate,
  count() AS n_tokens, 0 AS trade_count,
  avg(dateDiff('hour', first_buy_ts, last_ts)) AS avg_hold_h,
  0 AS active_days, min(first_buy_ts) AS first_seen, max(last_ts) AS last_seen,
  '' AS archetype, 0 AS smart_score
FROM rh.wallet_token_positions GROUP BY wallet;

-- smart_wallets: realized PnL in the top 1% AND traded >= 5 tokens AND win-rate>=0.5
INSERT INTO rh.smart_wallets
SELECT wallet, realized_pnl AS smart_score, rowNumberInAllBlocks() AS rank,
       realized_pnl, win_rate, n_tokens
FROM (
  SELECT * FROM rh.wallet_metrics
  WHERE n_tokens >= 5 AND win_rate >= 0.5
    AND realized_pnl >= (SELECT quantile(0.99)(realized_pnl) FROM rh.wallet_metrics WHERE realized_pnl > 0)
  ORDER BY realized_pnl DESC
);

-- ── 7) smart_money_inflows: what the smart set is buying (last 24h / 7d) ─────
INSERT INTO rh.smart_money_inflows
SELECT tt.token, '24h' AS window,
  sum(toFloat64(tt.value) * p.price_usd) AS net_inflow_usd,
  uniqExact(tt.to_addr) AS distinct_smart_buyers, max(tt.timestamp) AS last_buy_ts
FROM rh.token_transfers tt
INNER JOIN rh.smart_wallets sw ON tt.to_addr = sw.wallet
ASOF LEFT JOIN rh.token_prices p ON tt.token = p.token AND tt.timestamp >= p.timestamp
WHERE tt.kind='erc20' AND tt.timestamp >= now() - INTERVAL 24 HOUR
GROUP BY tt.token;
