-- Admin Service Schema DDL
-- Idempotent: all statements use IF NOT EXISTS / CREATE OR REPLACE.
--
-- IMPORTANT: Default admin credentials are NOT seeded here.
-- Run scripts/db/seed_admin.sh on first deploy to create the initial
-- admin user with a password set via ADMIN_INITIAL_PASSWORD env var.

CREATE SCHEMA IF NOT EXISTS admin_schema;

-- ── Admin users ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS admin_schema.admin_users (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    username      VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role          VARCHAR(50)  NOT NULL DEFAULT 'superadmin',
    -- Force password change on next login (set true for new accounts)
    must_change_password BOOLEAN NOT NULL DEFAULT true,
    last_login_at TIMESTAMPTZ,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT valid_admin_role CHECK (role IN ('superadmin', 'admin', 'viewer'))
);

CREATE OR REPLACE FUNCTION admin_schema.update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_admin_users_updated_at ON admin_schema.admin_users;
CREATE TRIGGER trg_admin_users_updated_at
    BEFORE UPDATE ON admin_schema.admin_users
    FOR EACH ROW
    EXECUTE FUNCTION admin_schema.update_updated_at();

-- ── Admin audit log (append-only) ────────────────────────────────────────────
-- admin_id is nullable: allows SET NULL if admin is deleted, but still
-- preserves the log entry via admin_username.
CREATE TABLE IF NOT EXISTS admin_schema.admin_audit_log (
    id             UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    admin_id       UUID         REFERENCES admin_schema.admin_users(id) ON DELETE SET NULL,
    admin_username VARCHAR(255) NOT NULL,
    action         VARCHAR(255) NOT NULL,
    target_type    VARCHAR(255),
    target_id      VARCHAR(255),
    details        JSONB,
    ip_address     VARCHAR(45),
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_admin_audit_admin_created
    ON admin_schema.admin_audit_log (admin_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_admin_audit_target
    ON admin_schema.admin_audit_log (target_type, target_id);

CREATE INDEX IF NOT EXISTS idx_admin_audit_action
    ON admin_schema.admin_audit_log (action, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_admin_audit_created
    ON admin_schema.admin_audit_log (created_at DESC);

-- Immutability: audit log entries cannot be modified or deleted
CREATE OR REPLACE FUNCTION admin_schema.audit_log_immutable()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'admin_audit_log is immutable — rows cannot be modified or deleted';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_admin_audit_immutable ON admin_schema.admin_audit_log;
CREATE TRIGGER trg_admin_audit_immutable
    BEFORE UPDATE OR DELETE ON admin_schema.admin_audit_log
    FOR EACH ROW
    EXECUTE FUNCTION admin_schema.audit_log_immutable();

-- ── Table comments ────────────────────────────────────────────────────────────
COMMENT ON TABLE admin_schema.admin_users IS
    'Admin panel users. Credentials seeded via scripts/db/seed_admin.sh — never hardcoded in schema.';

COMMENT ON TABLE admin_schema.admin_audit_log IS
    'Immutable audit log of all admin actions. Rows cannot be modified or deleted.';

-- ── Admin sessions (track active admin logins) ───────────────────────────────
CREATE TABLE IF NOT EXISTS admin_schema.admin_sessions (
    id             UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    admin_id       UUID         NOT NULL REFERENCES admin_schema.admin_users(id) ON DELETE CASCADE,
    admin_username VARCHAR(255) NOT NULL,
    ip_address     VARCHAR(45)  NOT NULL,
    user_agent     TEXT,
    login_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    logout_at      TIMESTAMPTZ,
    last_active_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    is_active      BOOLEAN      NOT NULL DEFAULT true,
    token_hash     VARCHAR(255)
);

CREATE INDEX IF NOT EXISTS idx_admin_sessions_admin_id ON admin_schema.admin_sessions(admin_id, login_at DESC);
CREATE INDEX IF NOT EXISTS idx_admin_sessions_active ON admin_schema.admin_sessions(is_active, last_active_at DESC) WHERE is_active = true;
CREATE INDEX IF NOT EXISTS idx_admin_sessions_ip ON admin_schema.admin_sessions(ip_address, login_at DESC);

COMMENT ON TABLE admin_schema.admin_sessions IS 'Tracks admin login sessions for security audit and active session management.';

-- ── IP login logs (cross-schema view for multi-account detection) ─────────────
-- Populated by the auth service login_logs via a materialized view or
-- direct insert from auth-service-go on every login event.
-- Admin service reads this for fraud detection.
CREATE TABLE IF NOT EXISTS admin_schema.ip_logs (
    id             UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    wallet_address VARCHAR(255) NOT NULL,
    ip_address     VARCHAR(45)  NOT NULL,
    user_agent     TEXT,
    country        VARCHAR(10),
    success        BOOLEAN      NOT NULL DEFAULT true,
    failure_reason VARCHAR(255),
    logged_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ip_logs_ip_logged ON admin_schema.ip_logs(ip_address, logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_ip_logs_wallet ON admin_schema.ip_logs(wallet_address, logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_ip_logs_logged ON admin_schema.ip_logs(logged_at DESC);

COMMENT ON TABLE admin_schema.ip_logs IS 'IP-based login tracking for multi-account and fraud detection. Populated from auth_schema.login_logs.';

-- ── Materialized view: wallets per IP (refreshed periodically) ────────────────
CREATE MATERIALIZED VIEW IF NOT EXISTS admin_schema.ip_wallet_summary AS
SELECT
    ip_address,
    COUNT(DISTINCT wallet_address) AS wallet_count,
    COUNT(*)                       AS login_count,
    COUNT(*) FILTER (WHERE success = false) AS fail_count,
    MIN(logged_at)                 AS first_seen,
    MAX(logged_at)                 AS last_seen,
    ARRAY_AGG(DISTINCT wallet_address ORDER BY wallet_address) FILTER (WHERE wallet_address IS NOT NULL) AS wallets
FROM admin_schema.ip_logs
GROUP BY ip_address;

CREATE UNIQUE INDEX IF NOT EXISTS idx_ip_wallet_summary_ip ON admin_schema.ip_wallet_summary(ip_address);

COMMENT ON MATERIALIZED VIEW admin_schema.ip_wallet_summary IS 'Aggregated IP login summary for multi-account detection. Refresh with: REFRESH MATERIALIZED VIEW CONCURRENTLY admin_schema.ip_wallet_summary;';

-- ── Failed login attempts (brute-force tracking) ──────────────────────────────
CREATE TABLE IF NOT EXISTS admin_schema.failed_login_attempts (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    username      VARCHAR(255),
    ip_address    VARCHAR(45)  NOT NULL,
    failure_reason VARCHAR(255),
    user_agent    TEXT,
    attempted_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_failed_logins_ip_time ON admin_schema.failed_login_attempts(ip_address, attempted_at DESC);
CREATE INDEX IF NOT EXISTS idx_failed_logins_username ON admin_schema.failed_login_attempts(username, attempted_at DESC) WHERE username IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_failed_logins_time ON admin_schema.failed_login_attempts(attempted_at DESC);

COMMENT ON TABLE admin_schema.failed_login_attempts IS 'Records failed admin login attempts for security monitoring and brute-force analysis.';

-- ── Analytics: daily aggregate snapshots ─────────────────────────────────────
-- Populated by a scheduled job / cron that runs nightly.
-- Allows fast O(1) dashboard queries without full table scans.
CREATE TABLE IF NOT EXISTS admin_schema.daily_stats (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    stat_date         DATE        NOT NULL UNIQUE,
    new_users         INT         NOT NULL DEFAULT 0,
    active_users      INT         NOT NULL DEFAULT 0,
    new_sessions      INT         NOT NULL DEFAULT 0,
    total_messages    INT         NOT NULL DEFAULT 0,
    new_transactions  INT         NOT NULL DEFAULT 0,
    confirmed_tx      INT         NOT NULL DEFAULT 0,
    failed_tx         INT         NOT NULL DEFAULT 0,
    total_fees_lamport BIGINT     NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_daily_stats_date ON admin_schema.daily_stats(stat_date DESC);

COMMENT ON TABLE admin_schema.daily_stats IS 'Nightly aggregated statistics for fast dashboard queries. Populated by a cron job.';

-- ── Memory operations audit (cross-schema, read from memory_schema) ───────────
-- Admin service reads memory_schema.memory_operations directly (read-only role).
-- This view provides a convenient cross-schema summary.
CREATE OR REPLACE VIEW admin_schema.memory_ops_summary AS
SELECT
    mo.user_id,
    mo.operation,
    mo.collection,
    COUNT(*)         AS op_count,
    SUM(mo.vector_count) AS total_vectors,
    MAX(mo.created_at)   AS last_op_at
FROM memory_schema.memory_operations mo
GROUP BY mo.user_id, mo.operation, mo.collection;

COMMENT ON VIEW admin_schema.memory_ops_summary IS 'Cross-schema view of memory service operations per user. Requires memory_schema read access.';
