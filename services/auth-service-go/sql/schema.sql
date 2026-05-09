-- Auth Service Schema DDL
-- Idempotent: all statements use IF NOT EXISTS / CREATE OR REPLACE.
-- Run via: make migrate  (or psql -f this file)

CREATE SCHEMA IF NOT EXISTS auth_schema;

-- ── Users table ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS auth_schema.users (
    id                       UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    wallet_address           VARCHAR(255) NOT NULL UNIQUE,
    chain                    VARCHAR(50)  NOT NULL DEFAULT 'solana',
    display_name             VARCHAR(255),
    risk_tolerance           VARCHAR(50),
    preferred_protocols      TEXT[],
    auto_suggestions_allowed BOOLEAN      NOT NULL DEFAULT true,
    role                     VARCHAR(50)  NOT NULL DEFAULT 'user',
    status                   VARCHAR(50)  NOT NULL DEFAULT 'active',
    last_active_at           TIMESTAMPTZ,

    -- Soft delete (consistent with other schemas)
    is_deleted               BOOLEAN      NOT NULL DEFAULT false,
    deleted_at               TIMESTAMPTZ,

    -- Status audit columns (set by admin service)
    status_reason            TEXT,
    status_changed_at        TIMESTAMPTZ,
    status_changed_by        VARCHAR(255),

    created_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT valid_role   CHECK (role   IN ('user', 'admin', 'superadmin')),
    CONSTRAINT valid_status CHECK (status IN ('active', 'suspended', 'banned', 'pending'))
);

CREATE INDEX IF NOT EXISTS idx_users_wallet_address
    ON auth_schema.users (wallet_address)
    WHERE is_deleted = false;

CREATE INDEX IF NOT EXISTS idx_users_status
    ON auth_schema.users (status)
    WHERE is_deleted = false;

-- updated_at auto-refresh
CREATE OR REPLACE FUNCTION auth_schema.update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_users_updated_at ON auth_schema.users;
CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON auth_schema.users
    FOR EACH ROW
    EXECUTE FUNCTION auth_schema.update_updated_at();

-- Soft-delete: fill deleted_at automatically
CREATE OR REPLACE FUNCTION auth_schema.handle_soft_delete()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.is_deleted = true AND OLD.is_deleted = false THEN
        NEW.deleted_at = NOW();
    ELSIF NEW.is_deleted = false THEN
        NEW.deleted_at = NULL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_users_soft_delete ON auth_schema.users;
CREATE TRIGGER trg_users_soft_delete
    BEFORE UPDATE ON auth_schema.users
    FOR EACH ROW
    EXECUTE FUNCTION auth_schema.handle_soft_delete();

-- ── Login logs (partitioned by month for scalability) ─────────────────────────
CREATE TABLE IF NOT EXISTS auth_schema.login_logs (
    id             UUID        NOT NULL DEFAULT gen_random_uuid(),
    user_id        UUID        NOT NULL,
    wallet_address VARCHAR(255) NOT NULL,
    ip_address     VARCHAR(45) NOT NULL,
    user_agent     TEXT        NOT NULL DEFAULT 'unknown',
    country        VARCHAR(10),
    success        BOOLEAN     NOT NULL DEFAULT true,
    failure_reason TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
) PARTITION BY RANGE (created_at);

-- Default partition catches anything not matched by monthly partitions
CREATE TABLE IF NOT EXISTS auth_schema.login_logs_default
    PARTITION OF auth_schema.login_logs DEFAULT;

-- Bootstrap current + next 11 months (12 total) automatically
DO $$
DECLARE
    m DATE;
    tbl TEXT;
BEGIN
    FOR i IN 0..11 LOOP
        m   := DATE_TRUNC('month', NOW() + (i || ' months')::INTERVAL);
        tbl := 'login_logs_' || TO_CHAR(m, 'YYYY_MM');
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS auth_schema.%I
             PARTITION OF auth_schema.login_logs
             FOR VALUES FROM (%L) TO (%L)',
            tbl, m, m + INTERVAL '1 month'
        );
    END LOOP;
END $$;

CREATE INDEX IF NOT EXISTS idx_login_logs_wallet_created
    ON auth_schema.login_logs (wallet_address, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_login_logs_ip_address
    ON auth_schema.login_logs (ip_address, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_login_logs_user_id
    ON auth_schema.login_logs (user_id, created_at DESC);

-- ── User preferences ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS auth_schema.user_preferences (
    id             UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID         NOT NULL REFERENCES auth_schema.users(id) ON DELETE CASCADE,
    wallet_address VARCHAR(255) NOT NULL,
    theme          VARCHAR(20)  NOT NULL DEFAULT 'dark',
    language       VARCHAR(10)  NOT NULL DEFAULT 'en',
    display_name   VARCHAR(255),
    timezone       VARCHAR(50)  NOT NULL DEFAULT 'UTC',
    notifications  JSONB        NOT NULL DEFAULT '{}',
    privacy        JSONB        NOT NULL DEFAULT '{}',
    trading        JSONB        NOT NULL DEFAULT '{}',
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),

    UNIQUE (user_id)
);

CREATE INDEX IF NOT EXISTS idx_user_preferences_wallet
    ON auth_schema.user_preferences (wallet_address);

DROP TRIGGER IF EXISTS trg_prefs_updated_at ON auth_schema.user_preferences;
CREATE TRIGGER trg_prefs_updated_at
    BEFORE UPDATE ON auth_schema.user_preferences
    FOR EACH ROW
    EXECUTE FUNCTION auth_schema.update_updated_at();

-- ── Audit trail (immutable — INSERT only) ─────────────────────────────────────
-- Partitioned by month to keep query performance on growing data.
CREATE TABLE IF NOT EXISTS auth_schema.audit_trail (
    id                    UUID         NOT NULL DEFAULT gen_random_uuid(),
    event_type            VARCHAR(50)  NOT NULL,
    severity              VARCHAR(20)  NOT NULL DEFAULT 'info',
    entity_type           VARCHAR(50),
    entity_id             VARCHAR(255),
    user_id               UUID,
    wallet_address        VARCHAR(255),
    session_id            VARCHAR(255),
    event_data            JSONB        NOT NULL DEFAULT '{}',
    ip_address            VARCHAR(45),
    user_agent            TEXT,
    request_method        VARCHAR(10),
    request_path          VARCHAR(500),
    request_id            VARCHAR(255),
    response_status       INTEGER,
    response_time_ms      DECIMAL(10,2),
    transaction_signature VARCHAR(255),
    transaction_amount    DECIMAL(20,8),
    transaction_token     VARCHAR(50),
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT valid_severity CHECK (severity IN ('debug', 'info', 'warning', 'error', 'critical'))
) PARTITION BY RANGE (created_at);

-- Default partition
CREATE TABLE IF NOT EXISTS auth_schema.audit_trail_default
    PARTITION OF auth_schema.audit_trail DEFAULT;

-- Bootstrap current + next 11 months (12 total)
DO $$
DECLARE
    m   DATE;
    tbl TEXT;
BEGIN
    FOR i IN 0..11 LOOP
        m   := DATE_TRUNC('month', NOW() + (i || ' months')::INTERVAL);
        tbl := 'audit_trail_' || TO_CHAR(m, 'YYYY_MM');
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS auth_schema.%I
             PARTITION OF auth_schema.audit_trail
             FOR VALUES FROM (%L) TO (%L)',
            tbl, m, m + INTERVAL '1 month'
        );
    END LOOP;
END $$;

-- Indexes on partition parent (propagate to all children)
CREATE INDEX IF NOT EXISTS idx_audit_user_id
    ON auth_schema.audit_trail (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_wallet
    ON auth_schema.audit_trail (wallet_address, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_event_type
    ON auth_schema.audit_trail (event_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_created_at
    ON auth_schema.audit_trail (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_entity
    ON auth_schema.audit_trail (entity_type, entity_id);

CREATE INDEX IF NOT EXISTS idx_audit_severity
    ON auth_schema.audit_trail (severity, created_at DESC)
    WHERE severity IN ('warning', 'error', 'critical');

-- ── Immutability: audit_trail cannot be modified or deleted ───────────────────
CREATE OR REPLACE FUNCTION auth_schema.audit_trail_immutable()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_trail is immutable — rows cannot be modified or deleted (event_type: %)',
        COALESCE(OLD.event_type, '?');
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_immutable ON auth_schema.audit_trail;
CREATE TRIGGER trg_audit_immutable
    BEFORE UPDATE OR DELETE ON auth_schema.audit_trail
    FOR EACH ROW
    EXECUTE FUNCTION auth_schema.audit_trail_immutable();

-- ── Partition maintenance function ───────────────────────────────────────────
-- Call monthly (e.g. from a pg_cron job) to keep 12 months of partitions ahead.
-- Example: SELECT auth_schema.create_auth_partitions(12);
CREATE OR REPLACE FUNCTION auth_schema.create_auth_partitions(months_ahead INT DEFAULT 12)
RETURNS VOID AS $$
DECLARE
    m   DATE;
    tbl TEXT;
BEGIN
    FOR i IN 0..(months_ahead - 1) LOOP
        m := DATE_TRUNC('month', NOW() + (i || ' months')::INTERVAL);

        -- login_logs
        tbl := 'login_logs_' || TO_CHAR(m, 'YYYY_MM');
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS auth_schema.%I
             PARTITION OF auth_schema.login_logs
             FOR VALUES FROM (%L) TO (%L)',
            tbl, m, m + INTERVAL '1 month'
        );

        -- audit_trail
        tbl := 'audit_trail_' || TO_CHAR(m, 'YYYY_MM');
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS auth_schema.%I
             PARTITION OF auth_schema.audit_trail
             FOR VALUES FROM (%L) TO (%L)',
            tbl, m, m + INTERVAL '1 month'
        );
    END LOOP;
END;
$$ LANGUAGE plpgsql;

-- ── Additional indexes ────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_users_last_active_at
    ON auth_schema.users (last_active_at DESC)
    WHERE is_deleted = false AND last_active_at IS NOT NULL;

-- ── Spending limits (server-side enforcement) ────────────────────────────────
-- maxPerTxUsd = 0 means unlimited; maxPerDayUsd = 0 means unlimited.
CREATE TABLE IF NOT EXISTS auth_schema.spending_limits (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID        NOT NULL REFERENCES auth_schema.users(id) ON DELETE CASCADE,
    wallet_address   VARCHAR(255) NOT NULL,
    max_per_tx_usd   DECIMAL(18,2) NOT NULL DEFAULT 0,
    max_per_day_usd  DECIMAL(18,2) NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),

    UNIQUE (user_id)
);

CREATE INDEX IF NOT EXISTS idx_spending_limits_wallet
    ON auth_schema.spending_limits (wallet_address);

DROP TRIGGER IF EXISTS trg_spending_limits_updated_at ON auth_schema.spending_limits;
CREATE TRIGGER trg_spending_limits_updated_at
    BEFORE UPDATE ON auth_schema.spending_limits
    FOR EACH ROW
    EXECUTE FUNCTION auth_schema.update_updated_at();

-- ── Daily spending tracker (server-side counter for the daily cap) ───────────
-- One row per (wallet, UTC date). The atomic INCREMENT in
-- /internal/spending/commit makes this race-safe across concurrent requests.
CREATE TABLE IF NOT EXISTS auth_schema.spending_daily (
    wallet_address VARCHAR(255)  NOT NULL,
    date_key       DATE          NOT NULL,
    total_usd      DECIMAL(18,2) NOT NULL DEFAULT 0,
    updated_at     TIMESTAMPTZ   NOT NULL DEFAULT now(),

    PRIMARY KEY (wallet_address, date_key)
);

CREATE INDEX IF NOT EXISTS idx_spending_daily_date
    ON auth_schema.spending_daily (date_key);

-- ── Statistics refresh ────────────────────────────────────────────────────────
ANALYZE auth_schema.users;
