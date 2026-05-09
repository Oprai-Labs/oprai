-- Per-service database roles with minimal privileges.
-- Run ONCE as superuser after schema migrations.
-- Each service gets its own role restricted to its own schema only.
--
-- Usage (passwords passed via psql --variable flags — never hardcode):
--   psql "$DATABASE_URL" \
--     --variable="auth_pass=$AUTH_DB_PASS" \
--     --variable="chat_pass=$CHAT_DB_PASS" \
--     --variable="solana_pass=$SOLANA_DB_PASS" \
--     --variable="memory_pass=$MEMORY_DB_PASS" \
--     --variable="admin_pass=$ADMIN_DB_PASS" \
--     -f scripts/db/init_roles.sql
--
-- Or use: make init-roles  (sets variables from .env automatically)
--
-- Then configure per-service DATABASE_URL env vars:
--   auth-service:   postgresql://auth_app:<AUTH_DB_PASS>@postgres:5432/oprai
--   chat-service:   postgresql://chat_app:<CHAT_DB_PASS>@postgres:5432/oprai
--   solana-service: postgresql://solana_app:<SOLANA_DB_PASS>@postgres:5432/oprai
--   memory-service: postgresql://memory_app:<MEMORY_DB_PASS>@postgres:5432/oprai
--   admin-service:  postgresql://admin_app:<ADMIN_DB_PASS>@postgres:5432/oprai

-- ── Create roles with passwords from psql variables ───────────────────────────
DO $$
BEGIN
    -- auth_app
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'auth_app') THEN
        EXECUTE format('CREATE ROLE auth_app WITH LOGIN PASSWORD %L', :'auth_pass');
    ELSE
        EXECUTE format('ALTER ROLE auth_app WITH PASSWORD %L', :'auth_pass');
    END IF;
    -- chat_app
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'chat_app') THEN
        EXECUTE format('CREATE ROLE chat_app WITH LOGIN PASSWORD %L', :'chat_pass');
    ELSE
        EXECUTE format('ALTER ROLE chat_app WITH PASSWORD %L', :'chat_pass');
    END IF;
    -- solana_app
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'solana_app') THEN
        EXECUTE format('CREATE ROLE solana_app WITH LOGIN PASSWORD %L', :'solana_pass');
    ELSE
        EXECUTE format('ALTER ROLE solana_app WITH PASSWORD %L', :'solana_pass');
    END IF;
    -- memory_app
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'memory_app') THEN
        EXECUTE format('CREATE ROLE memory_app WITH LOGIN PASSWORD %L', :'memory_pass');
    ELSE
        EXECUTE format('ALTER ROLE memory_app WITH PASSWORD %L', :'memory_pass');
    END IF;
    -- admin_app (read across all schemas + write to admin_schema)
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'admin_app') THEN
        EXECUTE format('CREATE ROLE admin_app WITH LOGIN PASSWORD %L', :'admin_pass');
    ELSE
        EXECUTE format('ALTER ROLE admin_app WITH PASSWORD %L', :'admin_pass');
    END IF;
END $$;

-- ── auth_app: owns auth_schema ────────────────────────────────────────────────
GRANT USAGE ON SCHEMA auth_schema TO auth_app;
GRANT SELECT, INSERT, UPDATE ON auth_schema.users TO auth_app;
GRANT SELECT, INSERT ON auth_schema.login_logs TO auth_app;
GRANT SELECT, INSERT, UPDATE ON auth_schema.user_preferences TO auth_app;
GRANT INSERT, SELECT ON auth_schema.audit_trail TO auth_app;   -- INSERT only (immutable)
-- Sequences
GRANT USAGE ON ALL SEQUENCES IN SCHEMA auth_schema TO auth_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA auth_schema
    GRANT USAGE ON SEQUENCES TO auth_app;

-- ── chat_app: owns chat_schema ────────────────────────────────────────────────
GRANT USAGE ON SCHEMA chat_schema TO chat_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA chat_schema TO chat_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA chat_schema TO chat_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA chat_schema
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO chat_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA chat_schema
    GRANT USAGE ON SEQUENCES TO chat_app;

-- ── solana_app: owns solana_schema + read auth_schema.users for wallet lookups ─
GRANT USAGE ON SCHEMA solana_schema TO solana_app;
GRANT SELECT, INSERT, UPDATE ON solana_schema.transactions TO solana_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA solana_schema TO solana_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA solana_schema
    GRANT SELECT, INSERT, UPDATE ON TABLES TO solana_app;

GRANT USAGE ON SCHEMA auth_schema TO solana_app;
GRANT SELECT ON auth_schema.users TO solana_app;  -- read-only, for user_id → wallet lookups

-- ── memory_app: owns memory_schema ───────────────────────────────────────────
GRANT USAGE ON SCHEMA memory_schema TO memory_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA memory_schema TO memory_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA memory_schema TO memory_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA memory_schema
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO memory_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA memory_schema
    GRANT USAGE ON SEQUENCES TO memory_app;

-- ── admin_app: read-only across all schemas + full admin_schema ───────────────
-- auth_schema (read-only + controlled updates)
GRANT USAGE ON SCHEMA auth_schema TO admin_app;
GRANT SELECT ON ALL TABLES IN SCHEMA auth_schema TO admin_app;
GRANT UPDATE (status, status_reason, status_changed_at, status_changed_by, role, updated_at)
    ON auth_schema.users TO admin_app;

-- chat_schema (read-only + soft-delete sessions)
GRANT USAGE ON SCHEMA chat_schema TO admin_app;
GRANT SELECT ON ALL TABLES IN SCHEMA chat_schema TO admin_app;
GRANT UPDATE (is_deleted, deleted_at, updated_at) ON chat_schema.chat_sessions TO admin_app;

-- solana_schema (read-only)
GRANT USAGE ON SCHEMA solana_schema TO admin_app;
GRANT SELECT ON ALL TABLES IN SCHEMA solana_schema TO admin_app;

-- memory_schema (read-only)
GRANT USAGE ON SCHEMA memory_schema TO admin_app;
GRANT SELECT ON ALL TABLES IN SCHEMA memory_schema TO admin_app;

-- admin_schema (full control)
GRANT USAGE ON SCHEMA admin_schema TO admin_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA admin_schema TO admin_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA admin_schema TO admin_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA admin_schema
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO admin_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA admin_schema
    GRANT USAGE ON SEQUENCES TO admin_app;

-- ── Revoke public schema access ───────────────────────────────────────────────
REVOKE ALL ON SCHEMA public FROM auth_app, chat_app, solana_app, memory_app, admin_app;
