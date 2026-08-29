# chain-intel — Robinhood Chain on-chain intelligence

OPRAI's self-hosted on-chain analytics engine for **Robinhood Chain (id 4663)** —
the "conversational Nansen". Blueprint: https://claude.ai/code/artifact/e30e370b-ab99-441b-a301-bc2e6f4936c2

## Architecture (two layers)
1. **Data engine (deterministic)** — self-hosted Nitro node → ClickHouse index →
   derived analytics tables. Produces structured *analysis objects*.
2. **Narrative (LLM)** — OPRAI's LLM turns the analysis object into long, rich,
   chart-backed reports. **The LLM never invents numbers** — every figure comes
   from the index.

## Infra (on the prod box, isolated compose projects; NOT the `infra` stack)
- **Nitro full node** — `rhnode` project, RPC `127.0.0.1:8547`, data `/data/nitro`,
  config `/data/rh-config`, syncing to tip. Archive snapshot (701GB) downloading
  to `/cold` for Phase-3 traces.
- **ClickHouse** — `rhindex` project, DB `rh`, `127.0.0.1:8123`. Data `/data/clickhouse`
  (hot NVMe); cold-tier to `/cold` HDD later via native TTL-move.
- **Bulk ETL** — `/data/rh-index/rh_etl.py` (8 workers, ~3K blk/s). Populates the
  raw facts (blocks/transactions/logs/token_transfers).

## Schema (`schema.sql`) — layered
- **Raw facts**: blocks, transactions, logs, token_transfers, contracts, dex_pools,
  dex_swaps, internal_txns (traces, Phase 3).
- **Address-centric**: address_activity (per-wallet fan-out — the wallet-query workhorse).
- **Derived**: token_prices, token_balances, wallet_token_positions (P&L ledger),
  wallet_metrics, token_metrics, token_holders, smart_wallets, smart_money_inflows.
- **Reference**: event_signatures, method_signatures, seed_wallets (KOL/RH), wallet_labels.

Design: denormalized per access pattern (token-ordered AND address-ordered),
bloom-filter skip indexes on from/to/address/token, block-range partitions for
hot/cold tiering, UInt256/Int256 for token amounts.

## Transforms (`transforms.sql`) — run AFTER the ETL completes
Pure functions of the raw facts (rebuildable). Order: dex_pools → dex_swaps →
token_prices → token_balances → wallet_token_positions → wallet_metrics →
smart_wallets → smart_money_inflows. Hex-offset parsing marked `[TUNE]` — validate
against real rows on first run.

## Validated on partial data (2026-08-29, ~11M blocks in)
- **~50M swaps** (UniswapV3 43.4M + V2 6.96M), ~700k pools → price pipeline viable.
- **USDG** (`0x5fc5360d…`) 19.9M transfers → strong $1 price anchor.
- **67% of logs** decode against the known-event table.
- Token report (holder/whale/sniper/bundle) proven live on a 611k-holder memecoin.

## Build order (what's next)
1. **ETL finishes** (~hours) → run `transforms.sql` on full data (+ tune `[TUNE]`).
2. **contracts ETL** (`rh_contracts_etl.py`) — capture receipt.contractAddress →
   deployer (dev/rug analysis). ClickHouse has no keccak to derive it in-DB.
3. **Intelligence API** — small FastAPI service exposing analysis objects
   (`wallet_report`, `token_report`, `smart_money`, `early_catchers`, `screen`).
4. **OPRAI integration** — new `query_onchain` tools (coordinate: chat-service is
   being edited in a parallel session — do this after / together).
5. **Frontend** — `analysis-report` card (charts + narrative + tables).

## NOT to break
The live OPRAI `infra` stack on the same box. Node + index are separate compose
projects; watch /data capacity and CPU/RAM contention.
