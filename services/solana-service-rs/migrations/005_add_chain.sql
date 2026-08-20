-- Multichain rewards: tag economics rows with the chain the fee was earned on.
-- Existing rows are Solana (the only chain trading exists on today). When EVM
-- trading (Relay/Uniswap swaps with commission) lands, those fees record with
-- their chain ('ethereum', 'base', …) and the per-chain rewards views populate
-- automatically. Additive + idempotent.

ALTER TABLE solana_schema.tx_economics
    ADD COLUMN IF NOT EXISTS chain VARCHAR(50) NOT NULL DEFAULT 'solana';

ALTER TABLE solana_schema.wallet_economics_rollup
    ADD COLUMN IF NOT EXISTS chain VARCHAR(50) NOT NULL DEFAULT 'solana';

CREATE INDEX IF NOT EXISTS idx_wallet_econ_chain
    ON solana_schema.wallet_economics_rollup (chain);
