-- Tier from FEE-PAYING volume only.
--
-- Cashback is a share of the commission a user paid, and their tier sets that
-- share — so the tier must be driven by volume that actually paid commission
-- (Jupiter fee-swaps, pump.fun, Relay), NOT fee-free activity (Orca/Raydium
-- direct, LP, stake, transfer, stable↔stable). Otherwise someone tiers up on
-- free volume and draws max cashback funded by nothing, and it's game-able.
--
-- `lifetime_notional_usd` stays as TOTAL volume (analytics). This adds a parallel
-- `lifetime_fee_notional_usd` — only trades with fee_usd>0 — which the tier reads.
-- Backfilled from the confirmed ledger so existing users keep the right tier.
-- Additive + idempotent.

ALTER TABLE solana_schema.wallet_economics_rollup
    ADD COLUMN IF NOT EXISTS lifetime_fee_notional_usd NUMERIC(24,6) NOT NULL DEFAULT 0;

-- Backfill per (wallet, chain) from confirmed, fee-paying rows.
UPDATE solana_schema.wallet_economics_rollup r
SET lifetime_fee_notional_usd = COALESCE((
        SELECT sum(t.notional_usd)
        FROM solana_schema.tx_economics t
        WHERE t.user_wallet = r.user_wallet
          AND COALESCE(t.chain, 'solana') = r.chain
          AND t.outcome = 'confirmed'
          AND COALESCE(t.fee_usd, 0) > 0
    ), 0)
WHERE r.lifetime_fee_notional_usd = 0;

CREATE INDEX IF NOT EXISTS idx_wallet_econ_fee_volume
    ON solana_schema.wallet_economics_rollup (lifetime_fee_notional_usd DESC);
