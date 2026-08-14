-- Per-transaction economics ledger: OPRAI commission (fee) + trade volume/notional.
-- One row per transaction, written 'pending' at record time (POST /transactions) with
-- the SERVER-recomputed fee (never client-reported), finalized at confirm/fail.
-- Feeds wallet + daily rollups for tiers, points, revenue and volume analytics.

CREATE TABLE IF NOT EXISTS solana_schema.tx_economics (
    id                UUID          NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    transaction_id    UUID          NOT NULL REFERENCES solana_schema.transactions(id) ON DELETE CASCADE,
    tx_signature      TEXT,                                   -- filled at confirm
    user_wallet       VARCHAR(64)   NOT NULL,
    protocol          VARCHAR(48),
    action            VARCHAR(48)   NOT NULL,
    -- volume side
    input_mint        VARCHAR(64),
    output_mint       VARCHAR(64),
    input_amount      NUMERIC(40,0),                          -- atomic (base units)
    output_amount     NUMERIC(40,0),
    notional_usd      NUMERIC(20,6),                          -- trade size in USD (best-effort snapshot)
    -- fee side (server-authoritative)
    fee_bps           INTEGER       NOT NULL DEFAULT 0,       -- OPRAI commission, recomputed server-side
    fee_mint          VARCHAR(64),
    fee_amount_token  NUMERIC(40,0),                          -- best-effort (execution-dependent)
    fee_usd           NUMERIC(20,6),                          -- notional_usd * fee_bps / 10000
    -- lifecycle
    outcome           VARCHAR(16)   NOT NULL DEFAULT 'pending'
                          CHECK (outcome IN ('pending','confirmed','failed','cancelled')),
    usd_price_source  VARCHAR(24),                            -- jupiter|est_usd|none
    created_at        TIMESTAMPTZ   NOT NULL DEFAULT now(),
    confirmed_at      TIMESTAMPTZ,
    UNIQUE (transaction_id)
);

CREATE INDEX IF NOT EXISTS idx_tx_econ_wallet     ON solana_schema.tx_economics (user_wallet, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tx_econ_outcome    ON solana_schema.tx_economics (outcome, confirmed_at DESC);
CREATE INDEX IF NOT EXISTS idx_tx_econ_protocol   ON solana_schema.tx_economics (protocol, confirmed_at DESC);
CREATE INDEX IF NOT EXISTS idx_tx_econ_confirmed  ON solana_schema.tx_economics (confirmed_at DESC) WHERE outcome = 'confirmed';

-- Per-wallet cumulative rollup (only confirmed tx). Updated incrementally at confirm.
-- Powers tiers / points / "top wallets by volume|fee" in O(1).
CREATE TABLE IF NOT EXISTS solana_schema.wallet_economics_rollup (
    user_wallet            VARCHAR(64)  NOT NULL PRIMARY KEY,
    lifetime_notional_usd  NUMERIC(24,6) NOT NULL DEFAULT 0,
    lifetime_fee_usd       NUMERIC(24,6) NOT NULL DEFAULT 0,
    confirmed_tx_count     BIGINT        NOT NULL DEFAULT 0,
    first_tx_at            TIMESTAMPTZ,
    last_tx_at             TIMESTAMPTZ,
    updated_at            TIMESTAMPTZ    NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_wallet_econ_volume ON solana_schema.wallet_economics_rollup (lifetime_notional_usd DESC);
CREATE INDEX IF NOT EXISTS idx_wallet_econ_fee    ON solana_schema.wallet_economics_rollup (lifetime_fee_usd DESC);

-- Per-day, per-protocol rollup (only confirmed tx). Powers revenue/volume trend charts.
CREATE TABLE IF NOT EXISTS solana_schema.daily_economics_rollup (
    stat_date    DATE          NOT NULL,
    protocol     VARCHAR(48)   NOT NULL DEFAULT 'unknown',
    volume_usd   NUMERIC(24,6) NOT NULL DEFAULT 0,
    fee_usd      NUMERIC(24,6) NOT NULL DEFAULT 0,
    tx_count     BIGINT        NOT NULL DEFAULT 0,
    updated_at   TIMESTAMPTZ   NOT NULL DEFAULT now(),
    PRIMARY KEY (stat_date, protocol)
);

CREATE INDEX IF NOT EXISTS idx_daily_econ_date ON solana_schema.daily_economics_rollup (stat_date DESC);
