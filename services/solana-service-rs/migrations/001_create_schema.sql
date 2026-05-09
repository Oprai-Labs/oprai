-- Solana Service Schema Migration
-- Run once on fresh database setup.
-- Idempotent: all statements use IF NOT EXISTS.

CREATE SCHEMA IF NOT EXISTS solana_schema;

-- ── Transactions table ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS solana_schema.transactions (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID         NOT NULL,
    user_wallet       VARCHAR(255) NOT NULL DEFAULT '',
    -- Cross-schema soft-references (no FK constraints, schema isolation)
    chat_session_id   UUID,
    chat_message_id   UUID,
    tx_hash           VARCHAR(255),
    chain             VARCHAR(50)  NOT NULL DEFAULT 'solana',
    status            VARCHAR(50)  NOT NULL DEFAULT 'pending',
    action            VARCHAR(100) NOT NULL,
    protocol          VARCHAR(100),
    parameters        JSONB        NOT NULL DEFAULT '{}',
    estimated_fee     VARCHAR(50),
    actual_fee        VARCHAR(50),
    error_message     TEXT,
    initiated_at      TIMESTAMPTZ,
    submitted_at      TIMESTAMPTZ,
    confirmed_at      TIMESTAMPTZ,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT valid_chain  CHECK (chain IN ('solana', 'ethereum', 'polygon')),
    CONSTRAINT valid_status CHECK (status IN ('pending', 'building', 'submitted', 'confirmed', 'completed', 'error', 'timeout', 'cancelled'))
);

-- ── Indexes ───────────────────────────────────────────────────────────────────
-- Primary lookup: by user
CREATE INDEX IF NOT EXISTS idx_solana_tx_user_id
    ON solana_schema.transactions (user_id, created_at DESC);

-- Status filtering (admin dashboard, monitoring)
CREATE INDEX IF NOT EXISTS idx_solana_tx_status
    ON solana_schema.transactions (status, created_at DESC);

-- On-chain signature lookup
CREATE INDEX IF NOT EXISTS idx_solana_tx_hash
    ON solana_schema.transactions (tx_hash)
    WHERE tx_hash IS NOT NULL;

-- Partial index: pending/submitted only (small, hot set)
CREATE INDEX IF NOT EXISTS idx_solana_tx_pending
    ON solana_schema.transactions (user_id, created_at)
    WHERE status IN ('pending', 'building', 'submitted');

-- Action + protocol analytics
CREATE INDEX IF NOT EXISTS idx_solana_tx_action
    ON solana_schema.transactions (action, protocol, created_at DESC);

-- Wallet-based direct lookup (bypasses user_id join)
CREATE INDEX IF NOT EXISTS idx_solana_tx_user_wallet
    ON solana_schema.transactions (user_wallet, created_at DESC)
    WHERE user_wallet <> '';

-- Cross-schema session linkage
CREATE INDEX IF NOT EXISTS idx_solana_tx_chat_session
    ON solana_schema.transactions (chat_session_id)
    WHERE chat_session_id IS NOT NULL;

-- ── updated_at auto-refresh trigger ──────────────────────────────────────────
CREATE OR REPLACE FUNCTION solana_schema.update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_solana_tx_updated_at ON solana_schema.transactions;
CREATE TRIGGER trg_solana_tx_updated_at
    BEFORE UPDATE ON solana_schema.transactions
    FOR EACH ROW
    EXECUTE FUNCTION solana_schema.update_updated_at();

-- ── Table comment ─────────────────────────────────────────────────────────────
COMMENT ON TABLE solana_schema.transactions IS
    'On-chain transaction records. References chat_schema by UUID value only — no FK constraints to keep schema boundaries independent.';

ANALYZE solana_schema.transactions;
