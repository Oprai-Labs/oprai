-- EVM economics: per-chain rollup rows + account-pooled tier volume.
--
-- Two changes make EVM (Relay) swaps feed the multichain rewards correctly:
--
-- 1. The wallet rollup was keyed by user_wallet ALONE. One EVM address trades on
--    many chains (Base, Arbitrum, …), and per-chain cashback (v_account_cashback
--    _by_chain groups by chain) needs one rollup row PER (wallet, chain). Re-key
--    the primary key to (user_wallet, chain). Existing rows are all chain='solana',
--    unique per wallet, so the composite key holds without data loss.
--
-- 2. `account_id` (the OPRAI account that owns the wallet) is recorded on both the
--    ledger and the rollup so the cashback TIER can be computed from the account's
--    POOLED volume across all its wallets/chains — the user's "volume is common to
--    all chains" rule — from within solana_schema (no cross-schema read).
--
-- Additive + idempotent (batch_execute runs this on every boot).

ALTER TABLE solana_schema.tx_economics
    ADD COLUMN IF NOT EXISTS account_id VARCHAR(64);

ALTER TABLE solana_schema.wallet_economics_rollup
    ADD COLUMN IF NOT EXISTS account_id VARCHAR(64);

-- Re-key the rollup to (user_wallet, chain). Drop the old single-column PK first;
-- its default name is <table>_pkey. Guard both steps so re-runs are no-ops.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'wallet_economics_rollup_pkey'
          AND conrelid = 'solana_schema.wallet_economics_rollup'::regclass
          AND array_length(conkey, 1) = 1
    ) THEN
        ALTER TABLE solana_schema.wallet_economics_rollup
            DROP CONSTRAINT wallet_economics_rollup_pkey;
        ALTER TABLE solana_schema.wallet_economics_rollup
            ADD CONSTRAINT wallet_economics_rollup_pkey PRIMARY KEY (user_wallet, chain);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_wallet_econ_account
    ON solana_schema.wallet_economics_rollup (account_id);

CREATE INDEX IF NOT EXISTS idx_tx_econ_account
    ON solana_schema.tx_economics (account_id);

CREATE INDEX IF NOT EXISTS idx_tx_econ_chain
    ON solana_schema.tx_economics (chain);

-- The transactions table's chain CHECK only allowed solana/ethereum/polygon —
-- EVM swaps land on Base, BNB, Arbitrum, Optimism too. Widen it to the full set
-- Relay routes so an EVM swap can be logged. Drop-and-re-add, guarded.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'valid_chain'
          AND conrelid = 'solana_schema.transactions'::regclass
    ) THEN
        ALTER TABLE solana_schema.transactions DROP CONSTRAINT valid_chain;
    END IF;
    ALTER TABLE solana_schema.transactions
        ADD CONSTRAINT valid_chain CHECK (chain IN
            ('solana','ethereum','base','bsc','polygon','arbitrum','optimism'));
END $$;
