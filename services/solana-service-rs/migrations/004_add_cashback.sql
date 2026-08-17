-- Cashback ledger.
--
-- The reward model: a user pays the full commission and earns a tier percentage
-- of it back as cashback, credited when the trade confirms and claimable later.
-- Per-tx cashback lives on tx_economics; the lifetime accrued + claimed totals
-- live on the wallet rollup (the source of truth the rewards endpoint reads).
--
-- Idempotent: safe to re-run on every boot (batch_execute).

ALTER TABLE solana_schema.tx_economics
    ADD COLUMN IF NOT EXISTS cashback_usd numeric;

ALTER TABLE solana_schema.wallet_economics_rollup
    ADD COLUMN IF NOT EXISTS lifetime_cashback_usd numeric NOT NULL DEFAULT 0;

ALTER TABLE solana_schema.wallet_economics_rollup
    ADD COLUMN IF NOT EXISTS claimed_cashback_usd numeric NOT NULL DEFAULT 0;
