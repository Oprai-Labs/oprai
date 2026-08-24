-- Robinhood Chain (id 4663): allow its economics rows. Migration 006 widened
-- transactions.valid_chain to 6 EVM chains; add 'robinhood' to the set. The
-- rollup + per-chain rewards views need no change — they key on whatever chain
-- string is booked. Idempotent (drop-and-re-add, guarded).

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
            ('solana','ethereum','base','bsc','polygon','arbitrum','optimism','robinhood',
             'avalanche','unichain','blast','celo','zora'));
END $$;
