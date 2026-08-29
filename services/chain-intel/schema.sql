-- ═══════════════════════════════════════════════════════════════════════════
-- OPRAI On-Chain Intelligence — ClickHouse schema (Robinhood Chain, id 4663)
-- Layered: raw facts → derived (computed) → reference. Idempotent.
-- Runs against DB `rh`. See blueprint: claude.ai/code/artifact/e30e370b-...
-- ═══════════════════════════════════════════════════════════════════════════

-- ── Layer 1: RAW FACTS (populated by the bulk ETL, rh_etl.py) ────────────────
-- (already created by the running ETL; repeated here as source-of-truth DDL)

CREATE TABLE IF NOT EXISTS rh.blocks (
  number UInt64, hash String, parent_hash String, timestamp DateTime,
  miner String, gas_used UInt64, gas_limit UInt64, base_fee UInt64,
  tx_count UInt32, size UInt32
) ENGINE=MergeTree ORDER BY number PARTITION BY intDiv(number, 2000000);

CREATE TABLE IF NOT EXISTS rh.transactions (
  hash String, block_number UInt64, tx_index UInt32, timestamp DateTime,
  from_addr String, to_addr String, value UInt256, gas UInt64, gas_price UInt64,
  method_id String, input String, status UInt8, nonce UInt64,
  INDEX idx_from from_addr TYPE bloom_filter GRANULARITY 4,
  INDEX idx_to to_addr TYPE bloom_filter GRANULARITY 4
) ENGINE=MergeTree ORDER BY (block_number, tx_index) PARTITION BY intDiv(block_number, 2000000);

CREATE TABLE IF NOT EXISTS rh.logs (
  block_number UInt64, tx_hash String, tx_index UInt32, log_index UInt32, timestamp DateTime,
  address String, topic0 String, topic1 String, topic2 String, topic3 String, data String,
  INDEX idx_addr address TYPE bloom_filter GRANULARITY 4,
  INDEX idx_t0 topic0 TYPE bloom_filter GRANULARITY 4
) ENGINE=MergeTree ORDER BY (block_number, log_index) PARTITION BY intDiv(block_number, 2000000);

CREATE TABLE IF NOT EXISTS rh.token_transfers (
  block_number UInt64, timestamp DateTime, tx_hash String, log_index UInt32,
  token String, from_addr String, to_addr String, value UInt256, token_id UInt256, kind LowCardinality(String),
  INDEX idx_from from_addr TYPE bloom_filter GRANULARITY 4,
  INDEX idx_to to_addr TYPE bloom_filter GRANULARITY 4
) ENGINE=MergeTree ORDER BY (token, block_number) PARTITION BY intDiv(block_number, 2000000);

-- Contract creations (deployer identity → dev/rug analysis). contract_addr comes
-- from receipt.contractAddress — captured by an ETL pass (rh_contracts_etl.py),
-- since ClickHouse has no keccak/rlp to derive it from (from,nonce) in-DB.
CREATE TABLE IF NOT EXISTS rh.contracts (
  address String, deployer String, creation_tx String, creation_block UInt64, timestamp DateTime,
  is_token UInt8 DEFAULT 0, token_type LowCardinality(String) DEFAULT '',
  symbol String DEFAULT '', name String DEFAULT '', decimals UInt8 DEFAULT 0,
  INDEX idx_deployer deployer TYPE bloom_filter GRANULARITY 4
) ENGINE=ReplacingMergeTree ORDER BY address;

-- ── Layer 1.5: ADDRESS-CENTRIC (the wallet-query workhorse) ──────────────────
-- Every transfer/swap fanned out to per-address rows, ordered by address so
-- "everything wallet X did" is one seek. Built by an MV from token_transfers
-- (+ dex_swaps once available). direction: 'in' | 'out'.
CREATE TABLE IF NOT EXISTS rh.address_activity (
  address String, block_number UInt64, timestamp DateTime, kind LowCardinality(String),
  token String, counterparty String, value UInt256, usd Float64 DEFAULT 0,
  direction LowCardinality(String), tx_hash String
) ENGINE=MergeTree ORDER BY (address, block_number) PARTITION BY intDiv(block_number, 2000000);

-- ── Layer 1: DEX (decoded from logs — real buy/sell + price source) ──────────
CREATE TABLE IF NOT EXISTS rh.dex_pools (
  pool String, token0 String, token1 String, dex LowCardinality(String), created_block UInt64
) ENGINE=ReplacingMergeTree ORDER BY pool;

CREATE TABLE IF NOT EXISTS rh.dex_swaps (
  block_number UInt64, timestamp DateTime, tx_hash String, log_index UInt32,
  pool String, sender String, recipient String,
  token_in String, token_out String, amount_in UInt256, amount_out UInt256,
  dex LowCardinality(String),
  INDEX idx_pool pool TYPE bloom_filter GRANULARITY 4,
  INDEX idx_sender sender TYPE bloom_filter GRANULARITY 4
) ENGINE=MergeTree ORDER BY (block_number, log_index) PARTITION BY intDiv(block_number, 2000000);

-- Internal transactions (traces) — Phase 3, from the archive node.
CREATE TABLE IF NOT EXISTS rh.internal_txns (
  block_number UInt64, tx_hash String, trace_index UInt32, timestamp DateTime,
  from_addr String, to_addr String, value UInt256, call_type LowCardinality(String), depth UInt16,
  INDEX idx_from from_addr TYPE bloom_filter GRANULARITY 4,
  INDEX idx_to to_addr TYPE bloom_filter GRANULARITY 4
) ENGINE=MergeTree ORDER BY (block_number, tx_hash, trace_index) PARTITION BY intDiv(block_number, 2000000);

-- ── Layer 2: DERIVED (computed via transforms/*.sql after the ETL) ───────────
CREATE TABLE IF NOT EXISTS rh.token_prices (
  token String, timestamp DateTime, price_usd Float64
) ENGINE=MergeTree ORDER BY (token, timestamp) PARTITION BY intDiv(toUInt64(timestamp), 2592000);

CREATE TABLE IF NOT EXISTS rh.token_balances (
  token String, address String, balance Int256
) ENGINE=SummingMergeTree ORDER BY (token, address);

CREATE TABLE IF NOT EXISTS rh.wallet_token_positions (
  wallet String, token String, qty_in Float64, usd_in Float64,
  qty_out Float64, usd_out Float64, avg_cost Float64, realized_pnl Float64,
  holding Float64, first_buy_ts DateTime, last_ts DateTime
) ENGINE=ReplacingMergeTree(last_ts) ORDER BY (wallet, token);

CREATE TABLE IF NOT EXISTS rh.wallet_metrics (
  wallet String, realized_pnl Float64, unrealized_pnl Float64, roi Float64,
  win_rate Float64, n_tokens UInt32, trade_count UInt32, avg_hold_h Float64,
  active_days UInt32, first_seen DateTime, last_seen DateTime,
  archetype LowCardinality(String) DEFAULT '', smart_score Float64 DEFAULT 0
) ENGINE=ReplacingMergeTree ORDER BY wallet;

CREATE TABLE IF NOT EXISTS rh.token_metrics (
  token String, holders UInt64, transfers UInt64, volume_usd Float64,
  first_block UInt64, deployer String DEFAULT '', sniper_count UInt32 DEFAULT 0,
  bundle_count UInt32 DEFAULT 0, smart_holders UInt32 DEFAULT 0, top10_pct Float64 DEFAULT 0
) ENGINE=ReplacingMergeTree ORDER BY token;

CREATE TABLE IF NOT EXISTS rh.token_holders (
  token String, address String, balance Float64, pct Float64, rank UInt32
) ENGINE=ReplacingMergeTree ORDER BY (token, address);

CREATE TABLE IF NOT EXISTS rh.smart_wallets (
  wallet String, smart_score Float64, rank UInt32, realized_pnl Float64, win_rate Float64, n_tokens UInt32
) ENGINE=ReplacingMergeTree ORDER BY wallet;

CREATE TABLE IF NOT EXISTS rh.smart_money_inflows (
  token String, window LowCardinality(String), net_inflow_usd Float64,
  distinct_smart_buyers UInt32, last_buy_ts DateTime
) ENGINE=ReplacingMergeTree ORDER BY (token, window);

-- ── Layer 3: REFERENCE / ENRICHMENT ─────────────────────────────────────────
-- event_signatures / method_signatures already seeded.
CREATE TABLE IF NOT EXISTS rh.seed_wallets (
  address String, label String, source LowCardinality(String)
) ENGINE=ReplacingMergeTree ORDER BY address;

CREATE TABLE IF NOT EXISTS rh.wallet_labels (
  address String, label LowCardinality(String)
) ENGINE=ReplacingMergeTree ORDER BY (address, label);
